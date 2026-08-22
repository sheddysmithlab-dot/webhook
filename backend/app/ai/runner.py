import json
import logging
import re
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiConversation, AiEvent, AiMedia, BlockedNumber, Chat
from ..redis_cache import (
    get_active_card,
    is_latest_wamid,
    mobile_lock,
    set_active_card,
    set_latest_wamid,
    set_processing,
)
from ..services import (
    download_whatsapp_media,
    get_or_create_settings,
    send_whatsapp_text,
    store_chat,
    utcnow,
)
from .schema import loads
from .tools import _draft_for, _payload, _write_payload
from ..identity import usable_person_name

log = logging.getLogger("infradealer.ai")


def _ai_respond(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str:
    """Hot path: four-agent orchestrator (account_filter -> chat_memory <-> filter/push)."""
    # ai_simple_chat mode used the legacy simple_chat.py module (now removed); always use orchestrator.
    from .orchestrator import handle_message

    return handle_message(db, conv, text, media_note)


def _is_latest_inbound(db: Session, conversation_id: str, wamid: str, mobile: str = "") -> bool:
    """True if this wamid is still the newest inbound (Redis first, then DB)."""
    if not wamid:
        return True
    if mobile and not is_latest_wamid(mobile, wamid):
        return False
    newest = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id, Chat.direction == "inbound")
        .order_by(Chat.id.desc())
        .first()
    )
    if not newest or not newest.wamid:
        return True
    return newest.wamid == wamid


def parse_meta_message(msg: dict) -> dict:
    typ = msg.get("type") or "text"
    text = ((msg.get("text") or {}).get("body")) or ""
    media = None
    if typ == "image":
        blob = msg.get("image") or {}
        media = {"kind": "image", "id": blob.get("id"), "mime": blob.get("mime_type"), "caption": blob.get("caption") or ""}
        text = media["caption"] or text or "[photo]"
    elif typ == "video":
        blob = msg.get("video") or {}
        media = {"kind": "video", "id": blob.get("id"), "mime": blob.get("mime_type"), "caption": blob.get("caption") or ""}
        text = media["caption"] or text or "[video]"
    elif typ == "document":
        blob = msg.get("document") or {}
        media = {"kind": "document", "id": blob.get("id"), "mime": blob.get("mime_type"), "caption": blob.get("caption") or blob.get("filename") or ""}
        text = media["caption"] or text or "[document]"
    elif typ == "audio":
        blob = msg.get("audio") or {}
        media = {"kind": "audio", "id": blob.get("id"), "mime": blob.get("mime_type"), "caption": ""}
        text = text or "[voice note]"
    elif typ == "button":
        text = ((msg.get("button") or {}).get("text")) or text
    elif typ == "interactive":
        inter = msg.get("interactive") or {}
        text = ((inter.get("button_reply") or {}).get("title")) or ((inter.get("list_reply") or {}).get("title")) or text
    elif typ in {"reaction", "sticker", "ephemeral", "unsupported", "system"}:
        return {"type": typ, "text": "", "media": None}
    # Meta sometimes prefixes button/quick replies as "Reply Name"
    text = re.sub(r"(?i)^\s*reply\s+", "", (text or "").strip())
    return {"type": typ, "text": text, "media": media}


def already_processed(db: Session, wamid: str) -> bool:
    if not wamid:
        return False
    return db.query(AiEvent).filter(AiEvent.wamid == wamid, AiEvent.event_type == "inbound").first() is not None


def get_or_create_conversation(db: Session, mobile: str, name: str = "") -> AiConversation:
    row = db.query(AiConversation).filter(AiConversation.mobile == mobile).first()
    label = usable_person_name(name)
    if row:
        if label:
            payload = _payload(row)
            if payload.get("wa_name") != label:
                payload["wa_name"] = label
                _write_payload(row, payload)
        return row
    row = AiConversation(
        mobile=mobile,
        conversation_id=f"CONV_{mobile}",
        state="NEW",
        customer_name="",
        payload_json="{}",
    )
    db.add(row)
    db.flush()
    payload = _payload(row)
    payload["whatsapp_number"] = mobile
    if label:
        payload["wa_name"] = label
    _write_payload(row, payload)
    return row


def attach_media(db: Session, conv: AiConversation, wamid: str, media: dict | None) -> str:
    if not media or not media.get("id"):
        return ""
    from .cards import MAX_PHOTOS, card_photo_count

    kind = (media.get("kind") or "image").lower()
    dup = db.query(AiMedia).filter(AiMedia.meta_media_id == media["id"]).first()
    if dup:
        if wamid:
            chat = db.query(Chat).filter(Chat.wamid == wamid, Chat.direction == "inbound").first()
            if chat and not chat.media_id:
                chat.media_id = dup.id
        return f"{dup.kind} id={dup.id} duplicate_skipped"

    payload = _payload(conv)
    awaiting_choice = conv.state == "AWAITING_VEHICLE_CHOICE" or payload.get("awaiting_vehicle_choice")
    target_draft_id = conv.draft_id
    if not awaiting_choice and kind in {"image", "photo"}:
        if not target_draft_id:
            draft = _draft_for(db, conv)
            target_draft_id = draft.id
            conv.draft_id = draft.id
        if card_photo_count(db, target_draft_id) >= MAX_PHOTOS:
            return "photo_rejected_max"

    meta = get_or_create_settings(db)
    path, mime = download_whatsapp_media(
        meta,
        media["id"],
        settings.ai_media_dir,
        f"{conv.mobile}_{media['id']}",
    )
    row = AiMedia(
        conversation_id=conv.id,
        draft_id=target_draft_id if not awaiting_choice else None,
        wamid=wamid or "",
        meta_media_id=media.get("id") or "",
        kind=media.get("kind") or "image",
        mime=mime or media.get("mime") or "",
        caption=(media.get("caption") or "")[:1000],
        local_path=path,
    )
    db.add(row)
    db.flush()
    if wamid:
        chat = db.query(Chat).filter(Chat.wamid == wamid, Chat.direction == "inbound").first()
        if chat:
            chat.media_id = row.id
    payload = _payload(conv)
    if awaiting_choice:
        pending = list(payload.get("pending_media_ids") or [])
        pending.append(row.id)
        payload["pending_media_ids"] = pending
        row.draft_id = None
    else:
        ids = list(payload.get("media_ids") or [])
        if kind in {"image", "photo"} and len([i for i in ids if i]) >= MAX_PHOTOS:
            db.delete(row)
            db.flush()
            return "photo_rejected_max"
        ids.append(row.id)
        payload["media_ids"] = ids[:MAX_PHOTOS]
        if not row.draft_id:
            if conv.draft_id:
                row.draft_id = conv.draft_id
            else:
                draft = _draft_for(db, conv)
                row.draft_id = draft.id
    _write_payload(conv, payload)
    return f"{row.kind} id={row.id} caption={row.caption!r} saved={bool(path)}" + ("" if path else " DOWNLOAD_FAILED")


def process_inbound(
    db: Session,
    *,
    mobile: str,
    text: str,
    wamid: str = "",
    name: str = "",
    media: dict | None = None,
    send: bool = True,
) -> str | None:
    t0 = time.perf_counter()
    t_lock = t_ai = t_wa = 0.0
    path = "none"
    stale = False

    if db.query(BlockedNumber).filter(BlockedNumber.mobile == mobile).first():
        return None
    if already_processed(db, wamid):
        return None

    # Mark newest message early (webhook may also set this)
    if wamid:
        set_latest_wamid(mobile, wamid)

    with mobile_lock(mobile) as held:
        t_lock = (time.perf_counter() - t0) * 1000
        if not held:
            log.info("ai.lock_busy mobile=***%s", mobile[-4:] if mobile else "")
            return None
        if already_processed(db, wamid):
            return None

        set_processing(mobile, "ai")
        conv = get_or_create_conversation(db, mobile, name)
        conv.last_wamid = wamid or conv.last_wamid
        conv.updated_at = utcnow()
        # Any user reply cancels pending post-approve/reject 10-min chat clear
        try:
            from .cards import cancel_card_cleanup

            cancel_card_cleanup(db, conv, mobile)
        except Exception:
            pass
        # Do not store full message body in logs/events beyond truncate — already capped
        db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="inbound", detail=(text or "")[:200]))

        simple = getattr(settings, "ai_simple_chat", False)
        if simple:
            # Do not create drafts / Card photo pipelines in plain chat mode
            media_note = ""
            if media and media.get("kind"):
                kind = (media.get("kind") or "image").lower()
                media_note = kind
                if kind == "image":
                    text = text or "[photo]"
                elif kind == "audio":
                    text = text or "[voice note]"
                elif kind == "video":
                    text = text or "[video]"
                elif kind == "document":
                    text = text or "[document]"
        else:
            media_note = attach_media(db, conv, wamid, media)

        t_ai0 = time.perf_counter()
        try:
            # AI Understanding: correct typos + classify routing
            from .corrector import correct_user_message, classify_message, reset_free_chat_count
            from .free_chat import free_chat_reply, free_chat_enabled
            from .i18n import pick_language, t

            lang = pick_language(text, getattr(conv, "language", "") or "", "auto")
            conv.language = lang

            # Step 1: Correct typos
            try:
                corrected = correct_user_message(db, conv, text, media_note)
                if corrected != text:
                    log.info("corrector: '%s' → '%s' mobile=***%s", text[:80], corrected[:80], conv.mobile[-4:] if conv.mobile else "")
                    text = corrected
            except Exception:
                log.exception("corrector failed, using original text")

            # Step 2: Classify and route
            pl = _payload(conv)
            verdict = classify_message(text, pl, media_note)

            if verdict["route"] == "confirmed":
                # Confirmed → agent handles it
                pl = reset_free_chat_count(pl)
                _write_payload(conv, pl)
                reply = _ai_respond(db, conv, text, media_note)
                path = "orchestrator"
            elif verdict["route"] == "unconfirmed":
                # Unconfirmed → free chat (count 0, 1)
                pl["free_chat_count"] = int(verdict.get("free_count", 0)) + 1
                _write_payload(conv, pl)
                if free_chat_enabled(db):
                    reply = free_chat_reply(db, conv, text, lang, media_note)
                else:
                    reply = t(lang, "greet")
                path = "free_chat"
            else:
                # 3rd unconfirmed → show InfraDealer options
                pl["free_chat_count"] = 0
                _write_payload(conv, pl)
                reply = t(lang, "infradealer_options")
                path = "options"

            if not simple:
                pl = _payload(conv)
                if pl.get("active_card_id"):
                    set_active_card(mobile, pl["active_card_id"])
        except Exception as exc:
            log.exception("ai respond failed mobile=***%s", mobile[-4:] if mobile else "")
            db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="error", detail=str(exc)[:300]))
            reply = t(lang, "ai_error_retry")
            path = "error"

        # LLM connectivity failure — reply is None or empty
        if not reply or not reply.strip():
            log.warning("ai empty reply mobile=***%s", mobile[-4:] if mobile else "")
            db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="error", detail="empty_reply"))
            reply = t(lang, "ai_error_retry")
            path = "error"
        t_ai = (time.perf_counter() - t_ai0) * 1000

        db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="ai_reply", detail=(reply or "")[:200]))

        if send and reply and not _is_latest_inbound(db, conv.conversation_id, wamid, mobile=mobile):
            stale = True
            log.info("skip stale AI reply mobile=***%s", mobile[-4:] if mobile else "")
            db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="ai_stale_skip", detail="stale"))
            set_processing(mobile, "stale")
            total = (time.perf_counter() - t0) * 1000
            log.info(
                "ai.timing lock=%.0fms ai=%.0fms wa=0ms total=%.0fms path=%s stale=1",
                t_lock, t_ai, total, path,
            )
            return reply

        if send and reply:
            set_processing(mobile, "wa")
            t_wa0 = time.perf_counter()
            meta = get_or_create_settings(db)
            try:
                result = send_whatsapp_text(meta, mobile, reply)
                store_chat(
                    db,
                    wamid=result.get("wamid") or f"ai.{conv.id}.{int(utcnow().timestamp())}",
                    conversation_id=conv.conversation_id,
                    from_mobile=meta.phone_number_id or "infradealer",
                    from_name="InfraDealer AI",
                    to_mobile=mobile,
                    direction="outbound",
                    body=reply,
                    status="sent",
                    unread=False,
                )
            except Exception as exc:
                log.warning("ai whatsapp send failed mobile=***%s: %s", mobile[-4:] if mobile else "", exc)
                db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="error", detail=str(exc)[:300]))
            t_wa = (time.perf_counter() - t_wa0) * 1000

        set_processing(mobile, "done")
        total = (time.perf_counter() - t0) * 1000
        log.info(
            "ai.timing lock=%.0fms ai=%.0fms wa=%.0fms total=%.0fms path=%s stale=%s",
            t_lock, t_ai, t_wa, total, path, int(stale),
        )
        return reply


def conversation_public(conv: AiConversation, media: list[AiMedia], draft) -> dict:
    return {
        "id": conv.id,
        "mobile": conv.mobile,
        "conversation_id": conv.conversation_id,
        "state": conv.state,
        "intent": conv.intent,
        "profile_id": conv.profile_id,
        "profile_status": conv.profile_status,
        "customer_name": conv.customer_name,
        "draft_id": conv.draft_id,
        "payload": loads(conv.payload_json),
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "draft": None if not draft else {
            "id": draft.id,
            "card_id": getattr(draft, "card_id", None),
            "status": draft.status,
            "title": draft.title,
            "intent": draft.intent,
            "posted_product_id": draft.posted_product_id,
            "customer": json.loads(draft.customer_json or "{}"),
            "inferred": json.loads(draft.inferred_json or "{}"),
            "confirmed": json.loads(draft.confirmed_json or "{}") if getattr(draft, "confirmed_json", None) else {},
        },
        "confirmed": json.loads(draft.confirmed_json or "{}") if draft and getattr(draft, "confirmed_json", None) else {},
        "media": [
            {
                "id": m.id,
                "kind": m.kind,
                "caption": m.caption,
                "mime": m.mime,
                "has_file": bool(m.local_path),
                "url": f"/api/admin/ai/media/{m.id}" if m.local_path else "",
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in media
        ],
    }
