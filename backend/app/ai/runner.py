import json
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiConversation, AiEvent, AiMedia, BlockedNumber, Chat
from ..services import (
    download_whatsapp_media,
    get_or_create_settings,
    send_whatsapp_text,
    store_chat,
    utcnow,
)
from .engine import respond
from .schema import loads
from .tools import _draft_for, _payload, _write_payload
from ..identity import usable_person_name

log = logging.getLogger("infradealer.ai")

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
    return {"type": typ, "text": (text or "").strip(), "media": media}


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
    dup = db.query(AiMedia).filter(AiMedia.meta_media_id == media["id"]).first()
    if dup:
        if wamid:
            chat = db.query(Chat).filter(Chat.wamid == wamid, Chat.direction == "inbound").first()
            if chat and not chat.media_id:
                chat.media_id = dup.id
        return f"{dup.kind} id={dup.id} duplicate_skipped"
    meta = get_or_create_settings(db)
    path, mime = download_whatsapp_media(
        meta,
        media["id"],
        settings.ai_media_dir,
        f"{conv.mobile}_{media['id']}",
    )
    row = AiMedia(
        conversation_id=conv.id,
        draft_id=conv.draft_id,
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
    awaiting_choice = conv.state == "AWAITING_VEHICLE_CHOICE" or payload.get("awaiting_vehicle_choice")
    if awaiting_choice:
        pending = list(payload.get("pending_media_ids") or [])
        pending.append(row.id)
        payload["pending_media_ids"] = pending
        row.draft_id = None
    else:
        ids = list(payload.get("media_ids") or [])
        ids.append(row.id)
        payload["media_ids"] = ids
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
    if db.query(BlockedNumber).filter(BlockedNumber.mobile == mobile).first():
        return None
    if already_processed(db, wamid):
        return None
    conv = get_or_create_conversation(db, mobile, name)
    conv.last_wamid = wamid or conv.last_wamid
    conv.updated_at = utcnow()
    db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="inbound", detail=(text or "")[:1000]))
    media_note = attach_media(db, conv, wamid, media)
    try:
        reply = respond(db, conv, text, media_note)
    except Exception as exc:
        log.exception("ai respond failed %s: %s", mobile, exc)
        db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="error", detail=str(exc)[:1000]))
        reply = "Namaste Sir, thodi technical dikkat hai. Kripya ek pal — aap gadi bechna chahte hain ya lena?"
    db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="ai_reply", detail=(reply or "")[:1000]))
    if send and reply:
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
            log.warning("ai whatsapp send failed %s: %s", mobile, exc)
            db.add(AiEvent(wamid=wamid or "", mobile=mobile, event_type="error", detail=str(exc)[:1000]))
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
