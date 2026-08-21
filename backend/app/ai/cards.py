"""Per-Card identity: WhatsApp number = user; CARD-00X = listing."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AiConversation, AiListingDraft, AiMedia

CARD_RE = re.compile(r"\bCARD[- ]?0*(\d+)\b", re.I)
MIN_PHOTOS = 2
MAX_PHOTOS = 5
CLEANUP_MINUTES = 10

# Listing fields that must stay isolated per Card ID (not account/identity).
CARD_SESSION_KEYS = (
    "intent",
    "category",
    "type",
    "brand",
    "model",
    "year",
    "year_min",
    "registration_year",
    "running",
    "running_km",
    "operating_hours",
    "condition",
    "accident_history",
    "negotiable",
    "location",
    "state",
    "city",
    "owners",
    "finance_amount",
    "tyre_percent",
    "finance_condition",
    "work_issues",
    "optional_asked",
    "optional_done",
    "expected_price",
    "budget",
    "budget_max",
    "description",
    "media_ids",
    "photos_complete",
    "photos_prompted",
    "skipped_asks",
    "missing_fields",
    "awaiting_confirm",
    "awaiting_vehicle_choice",
    "pending_vehicle",
    "pending_media_ids",
    "customer_confirmed",
    "summary_json",
    "confirmed_json",
    "listing_status",
    "data_status",
    "active_card_id",
    "listing_url",
    "infradealer_listing_id",
    "rejection_reason",
    "listing_review_notified",
    "confidence",
    "source",
)

_AMBIGUOUS_REF = re.compile(
    r"\b(isme|is\s*me|ispe|is\s*pe|usme|us\s*me|isko|usko|uspe|us\s*pe|"
    r"yeh|ye\s+card|is\s+card|us\s+card|same\s+card)\b",
    re.I,
)
_FIELD_EDIT = re.compile(
    r"\b(price|rate|budget|model|year|location|state|city|brand|running|"
    r"owners|change|badlo|update|correct|sahi|galat)\b",
    re.I,
)

_ACTIVE_STATUSES = {
    "COLLECTING",
    "PENDING_REVIEW",
    "CONFIRMED",
    "READY_FOR_REVIEW",
    "NEEDS_INFO",
    "AWAITING_CONFIRMATION",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_card_id(seq: int) -> str:
    return f"CARD-{int(seq):03d}"


def next_card_seq(db: Session, mobile: str) -> int:
    rows = (
        db.query(AiListingDraft.card_id)
        .filter(AiListingDraft.mobile == mobile, AiListingDraft.card_id.isnot(None))
        .all()
    )
    best = 0
    for (cid,) in rows:
        m = CARD_RE.search(str(cid or ""))
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def ensure_card_id(db: Session, draft: AiListingDraft) -> str:
    """Assign CARD-00X uniquely per mobile; race-safe via unique constraint + retry."""
    if draft.card_id and CARD_RE.search(draft.card_id):
        return draft.card_id
    phone = draft.mobile or ""
    for attempt in range(12):
        seq = next_card_seq(db, phone) + attempt
        candidate = format_card_id(seq)
        clash = (
            db.query(AiListingDraft.id)
            .filter(
                AiListingDraft.mobile == phone,
                AiListingDraft.card_id == candidate,
                AiListingDraft.id != (draft.id or -1),
            )
            .first()
        )
        if clash:
            continue
        draft.card_id = candidate
        try:
            with db.begin_nested():
                db.flush()
            return draft.card_id
        except IntegrityError:
            draft.card_id = None
            continue
    # Last-resort unique suffix (still CARD-style for display)
    draft.card_id = format_card_id(next_card_seq(db, phone) + int(_now().timestamp()) % 1000)
    db.flush()
    return draft.card_id


def backfill_missing_card_ids(db: Session, limit: int = 5000) -> int:
    rows = (
        db.query(AiListingDraft)
        .filter(AiListingDraft.card_id.is_(None))
        .order_by(AiListingDraft.mobile.asc(), AiListingDraft.id.asc())
        .limit(limit)
        .all()
    )
    n = 0
    for draft in rows:
        ensure_card_id(db, draft)
        n += 1
    if n:
        db.commit()
    return n


def parse_card_mention(text: str) -> str | None:
    m = CARD_RE.search(text or "")
    if not m:
        return None
    return format_card_id(int(m.group(1)))


def active_drafts(db: Session, mobile: str) -> list[AiListingDraft]:
    return (
        db.query(AiListingDraft)
        .filter(
            AiListingDraft.mobile == mobile,
            AiListingDraft.status.notin_(["CLEARED", "DELETED"]),
        )
        .order_by(AiListingDraft.id.asc())
        .all()
    )


def collecting_drafts(db: Session, mobile: str) -> list[AiListingDraft]:
    rows = active_drafts(db, mobile)
    out = []
    for d in rows:
        st = (d.status or "").upper()
        if st in _ACTIVE_STATUSES or st in {"", "PENDING"}:
            out.append(d)
    return out


def persist_active_card_session(db: Session, conv: AiConversation) -> None:
    """Save live conversation payload listing fields onto the active draft."""
    from .tools import _payload

    if not conv.draft_id:
        return
    draft = db.get(AiListingDraft, conv.draft_id)
    if not draft or (draft.status or "").upper() in {"CLEARED", "DELETED"}:
        return
    ensure_card_id(db, draft)
    payload = _payload(conv)
    session = {k: payload.get(k) for k in CARD_SESSION_KEYS if k in payload}
    session["active_card_id"] = draft.card_id
    draft.inferred_json = json.dumps(session, ensure_ascii=False)
    if payload.get("confirmed_json"):
        draft.confirmed_json = json.dumps(payload.get("confirmed_json"), ensure_ascii=False)
    if payload.get("customer_confirmed") and payload.get("confirmed_json"):
        draft.customer_json = json.dumps(payload.get("confirmed_json"), ensure_ascii=False)
    media_ids = [int(x) for x in (payload.get("media_ids") or []) if str(x).isdigit() or isinstance(x, int)]
    if media_ids:
        db.query(AiMedia).filter(AiMedia.id.in_(media_ids[:MAX_PHOTOS])).update(
            {"draft_id": draft.id}, synchronize_session=False
        )


def hydrate_card_session(db: Session, conv: AiConversation, draft: AiListingDraft) -> None:
    """Load a draft's saved listing session into the live conversation payload."""
    from .schema import empty_payload
    from .tools import _payload, _write_payload

    ensure_card_id(db, draft)
    live = _payload(conv)
    keep = {
        "whatsapp_number": live.get("whatsapp_number") or conv.mobile,
        "customer_name": live.get("customer_name") or conv.customer_name,
        "wa_name": live.get("wa_name"),
        "profile_id": live.get("profile_id") or conv.profile_id,
        "profile_status": live.get("profile_status") or conv.profile_status,
        "otp_verified": live.get("otp_verified"),
        "account_onboarded": live.get("account_onboarded"),
        "account_role": live.get("account_role"),
        "account_step": live.get("account_step"),
        "account_password_set": live.get("account_password_set"),
        "account_type": live.get("account_type"),
        "account_label": live.get("account_label"),
        "account_can_post": live.get("account_can_post"),
        "account_reason": live.get("account_reason"),
        "account_buy_link": live.get("account_buy_link"),
        "account_eligibility": live.get("account_eligibility"),
        "ai_introduced": live.get("ai_introduced"),
        "language": live.get("language") or conv.language,
        "verification_status": live.get("verification_status"),
        "contact_phone": live.get("contact_phone"),
    }
    try:
        saved = json.loads(draft.inferred_json or "{}")
    except json.JSONDecodeError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    fresh = empty_payload()
    for k, v in keep.items():
        if v not in (None, ""):
            fresh[k] = v
    for k in CARD_SESSION_KEYS:
        if k in saved:
            fresh[k] = saved[k]
    # Media always from DB for this draft (source of truth)
    media_rows = (
        db.query(AiMedia.id)
        .filter(AiMedia.draft_id == draft.id, AiMedia.kind.in_(["image", "photo"]))
        .order_by(AiMedia.id.asc())
        .limit(MAX_PHOTOS)
        .all()
    )
    fresh["media_ids"] = [r[0] for r in media_rows] or list(saved.get("media_ids") or [])[:MAX_PHOTOS]
    fresh["active_card_id"] = draft.card_id
    fresh["awaiting_card_choice"] = False
    _write_payload(conv, fresh)
    if draft.intent:
        conv.intent = draft.intent
    st = (draft.status or "").upper()
    if st == "CONFIRMED":
        conv.state = "CONFIRMED"
    elif saved.get("awaiting_confirm"):
        conv.state = "AWAITING_CONFIRMATION"
    elif (fresh.get("intent") or "").upper() == "BUY":
        conv.state = "BUY_DATA_COLLECTION"
    else:
        conv.state = "SELL_DATA_COLLECTION"


def switch_active_card(db: Session, conv: AiConversation, card_id: str) -> AiListingDraft | None:
    draft = (
        db.query(AiListingDraft)
        .filter(AiListingDraft.mobile == conv.mobile, AiListingDraft.card_id == card_id)
        .order_by(AiListingDraft.id.desc())
        .first()
    )
    if not draft or (draft.status or "").upper() in {"CLEARED", "DELETED"}:
        return None
    if conv.draft_id and conv.draft_id != draft.id:
        persist_active_card_session(db, conv)
    conv.draft_id = draft.id
    hydrate_card_session(db, conv, draft)
    try:
        from ..redis_cache import set_active_card

        if draft.card_id:
            set_active_card(conv.mobile, draft.card_id)
    except Exception:
        pass
    return draft


def needs_card_clarification(db: Session, conv: AiConversation, text: str) -> bool:
    """True when multiple cards are open and the user refers ambiguously."""
    if parse_card_mention(text or ""):
        return False
    open_cards = collecting_drafts(db, conv.mobile)
    if len(open_cards) < 2:
        return False
    msg = (text or "").strip()
    if not msg:
        return False
    if _AMBIGUOUS_REF.search(msg):
        return True
    # Field edit with no card id while another card also exists
    if _FIELD_EDIT.search(msg) and not conv.draft_id:
        return True
    return False


def card_clarification_prompt(db: Session, conv: AiConversation, lang: str) -> str:
    from .i18n import t
    from .tools import _payload, _write_payload

    cards = collecting_drafts(db, conv.mobile)
    labels = []
    for d in cards:
        ensure_card_id(db, d)
        tip = ""
        try:
            saved = json.loads(d.inferred_json or "{}")
            tip = " ".join(str(x) for x in [saved.get("brand"), saved.get("model")] if x).strip()
        except Exception:
            tip = (d.title or "")[:40]
        labels.append(f"{d.card_id}" + (f" ({tip})" if tip else ""))
    payload = _payload(conv)
    payload["awaiting_card_choice"] = True
    _write_payload(conv, payload)
    return t(lang, "card_clarify", cards=", ".join(labels))


def card_photo_count(db: Session, draft_id: int | None) -> int:
    if not draft_id:
        return 0
    return (
        db.query(AiMedia)
        .filter(AiMedia.draft_id == draft_id, AiMedia.kind.in_(["image", "photo"]))
        .count()
    )


def photos_ready(db: Session, draft_id: int | None) -> bool:
    n = card_photo_count(db, draft_id)
    return MIN_PHOTOS <= n <= MAX_PHOTOS


def photos_status(db: Session, draft_id: int | None) -> dict:
    n = card_photo_count(db, draft_id)
    return {
        "count": n,
        "min": MIN_PHOTOS,
        "max": MAX_PHOTOS,
        "need_more": n < MIN_PHOTOS,
        "at_max": n >= MAX_PHOTOS,
        "ready": MIN_PHOTOS <= n <= MAX_PHOTOS,
    }


CLEANUP_MINUTES = 10
CLEANUP_WARN_BEFORE_MINUTES = 1
_CLEANUP_STATUSES = {"POSTED", "APPROVED", "LIVE", "REJECTED", "PUBLISHED"}


def _draft_meta(draft: AiListingDraft) -> dict:
    try:
        data = json.loads(draft.inferred_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_draft_meta(draft: AiListingDraft, meta: dict) -> None:
    draft.inferred_json = json.dumps(meta, ensure_ascii=False)


def schedule_card_cleanup(draft: AiListingDraft, minutes: int = CLEANUP_MINUTES) -> None:
    """After admin approve/reject: clear chat in `minutes` if user stays silent.

    At (minutes - 1) send a 1-minute warning WhatsApp message.
    Listing memory (draft confirmed_json / listing ids) is kept.
    """
    mins = max(int(minutes or CLEANUP_MINUTES), 1)
    draft.cleanup_at = _now() + timedelta(minutes=mins)
    meta = _draft_meta(draft)
    meta["cleanup_warn_sent"] = False
    meta["cleanup_scheduled_at"] = _now().isoformat()
    meta["cleanup_minutes"] = mins
    _write_draft_meta(draft, meta)
    try:
        from ..redis_cache import mark_card_cleanup

        mark_card_cleanup(draft.mobile or "", draft.card_id or "", int(mins * 60))
    except Exception:
        pass


def cancel_card_cleanup(db: Session, conv: AiConversation | None = None, mobile: str = "") -> int:
    """User replied — cancel pending post-decision chat clear for their drafts."""
    phone = (mobile or (conv.mobile if conv else "") or "").strip()[-10:]
    if not phone:
        return 0
    rows = (
        db.query(AiListingDraft)
        .filter(
            AiListingDraft.mobile == phone,
            AiListingDraft.cleanup_at.isnot(None),
            AiListingDraft.status.in_(list(_CLEANUP_STATUSES)),
        )
        .all()
    )
    cancelled = 0
    for draft in rows:
        draft.cleanup_at = None
        meta = _draft_meta(draft)
        meta.pop("cleanup_warn_sent", None)
        meta.pop("cleanup_scheduled_at", None)
        meta.pop("cleanup_minutes", None)
        _write_draft_meta(draft, meta)
        try:
            from ..redis_cache import clear_card_cleanup_marker

            clear_card_cleanup_marker(draft.mobile or "", draft.card_id or "")
        except Exception:
            pass
        cancelled += 1
    return cancelled


def clear_card_chat_data(db: Session, conv: AiConversation, draft: AiListingDraft) -> None:
    """Wipe live AI chat collection for a finished card — keep listing memory."""
    from .schema import empty_payload
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    if conv.draft_id == draft.id or (payload.get("active_card_id") and payload.get("active_card_id") == draft.card_id):
        keep = {
            "whatsapp_number": payload.get("whatsapp_number"),
            "customer_name": payload.get("customer_name"),
            "wa_name": payload.get("wa_name"),
            "wa_id": payload.get("wa_id"),
            "profile_id": payload.get("profile_id"),
            "profile_status": payload.get("profile_status"),
            "otp_verified": payload.get("otp_verified"),
            "account_onboarded": payload.get("account_onboarded"),
            "account_role": payload.get("account_role"),
            "account_step": "done" if payload.get("account_onboarded") else "",
            "ai_introduced": True,
            "language": payload.get("language"),
            "verification_status": payload.get("verification_status"),
            "account_type": payload.get("account_type"),
            "account_label": payload.get("account_label"),
            "account_can_post": payload.get("account_can_post"),
            "account_reason": payload.get("account_reason"),
            "account_buy_link": payload.get("account_buy_link"),
            "account_eligibility": payload.get("account_eligibility"),
            # Listing memory stays for link / last-post after chat clear
            "listing_url": payload.get("listing_url"),
            "infradealer_listing_id": payload.get("infradealer_listing_id"),
            "listing_status": payload.get("listing_status") or draft.status,
            "push_stage": payload.get("push_stage"),
            "summary_json": payload.get("summary_json"),
            "confirmed_json": payload.get("confirmed_json"),
            "rejection_reason": payload.get("rejection_reason"),
            "chat_cleared": True,
        }
        fresh = empty_payload()
        fresh.update({k: v for k, v in keep.items() if v not in (None, "")})
        fresh["active_card_id"] = None
        fresh["chat_cleared"] = True
        _write_payload(conv, fresh)
        conv.draft_id = None
        conv.state = "NEW_CHAT"
        conv.intent = ""
        conv.error_message = ""
    # Keep draft listing memory (title / confirmed_json / customer_json). Mark chat cleared only.
    st = (draft.status or "").upper()
    if st in _CLEANUP_STATUSES:
        meta = _draft_meta(draft)
        meta["chat_cleared_at"] = _now().isoformat()
        meta["cleanup_warn_sent"] = True
        _write_draft_meta(draft, meta)
    else:
        draft.status = "CLEARED"
    draft.cleanup_at = None
    try:
        from ..redis_cache import clear_card_cleanup_marker

        clear_card_cleanup_marker(draft.mobile or "", draft.card_id or "")
    except Exception:
        pass


def _notify_cleanup_warning(db: Session, conv: AiConversation, card_id: str) -> None:
    from .i18n import t
    from ..services import get_or_create_settings, send_whatsapp_text, store_chat

    mobile = (conv.mobile or "").strip()
    if not mobile:
        return
    lang = (conv.language or "").strip() or "hinglish"
    text = t(lang, "card_cleanup_warn", card=card_id or "Card")
    try:
        meta = get_or_create_settings(db)
        result = send_whatsapp_text(meta, mobile, text)
        store_chat(
            db,
            wamid=result.get("wamid") or f"ai.cleanup.warn.{conv.id}.{int(_now().timestamp())}",
            conversation_id=conv.conversation_id or f"CONV_{mobile}",
            from_mobile=meta.phone_number_id or "infradealer",
            from_name="InfraDealer AI",
            to_mobile=mobile,
            direction="outbound",
            body=text,
            status="sent",
            unread=False,
        )
    except Exception:
        pass


def _notify_conversation_deleted(db: Session, conv: AiConversation, card_id: str) -> None:
    """WhatsApp ping right after card conversation is wiped."""
    from .i18n import t
    from ..services import get_or_create_settings, send_whatsapp_text, store_chat

    mobile = (conv.mobile or "").strip()
    if not mobile:
        return
    lang = (conv.language or "").strip() or "hinglish"
    text = t(lang, "card_conversation_deleted", card=card_id or "Card")
    try:
        meta = get_or_create_settings(db)
        result = send_whatsapp_text(meta, mobile, text)
        store_chat(
            db,
            wamid=result.get("wamid") or f"ai.cleanup.{conv.id}.{int(_now().timestamp())}",
            conversation_id=conv.conversation_id or f"CONV_{mobile}",
            from_mobile=meta.phone_number_id or "infradealer",
            from_name="InfraDealer AI",
            to_mobile=mobile,
            direction="outbound",
            body=text,
            status="sent",
            unread=False,
        )
    except Exception:
        # Cleanup must not fail if WhatsApp send fails
        pass


def process_due_cleanup_warnings(db: Session, limit: int = 50) -> int:
    """At minute 9 (1 min before clear): warn user that conversation will be deleted."""
    now = _now()
    rows = (
        db.query(AiListingDraft)
        .filter(
            AiListingDraft.cleanup_at.isnot(None),
            AiListingDraft.cleanup_at > now,
            AiListingDraft.cleanup_at <= now + timedelta(minutes=CLEANUP_WARN_BEFORE_MINUTES),
            AiListingDraft.status.in_(list(_CLEANUP_STATUSES)),
        )
        .order_by(AiListingDraft.cleanup_at.asc())
        .limit(limit)
        .all()
    )
    done = 0
    for draft in rows:
        try:
            meta = _draft_meta(draft)
            if meta.get("cleanup_warn_sent"):
                continue
            conv = db.get(AiConversation, draft.conversation_id)
            if not conv:
                meta["cleanup_warn_sent"] = True
                _write_draft_meta(draft, meta)
                done += 1
                continue
            card = draft.card_id or ensure_card_id(db, draft)
            _notify_cleanup_warning(db, conv, card)
            meta["cleanup_warn_sent"] = True
            meta["cleanup_warn_at"] = now.isoformat()
            _write_draft_meta(draft, meta)
            done += 1
        except Exception:
            continue
    if done:
        db.commit()
    return done


def process_due_card_cleanups(db: Session, limit: int = 50) -> int:
    now = _now()
    rows = (
        db.query(AiListingDraft)
        .filter(
            AiListingDraft.cleanup_at.isnot(None),
            AiListingDraft.cleanup_at <= now,
            AiListingDraft.status.in_(list(_CLEANUP_STATUSES)),
        )
        .order_by(AiListingDraft.cleanup_at.asc())
        .limit(limit)
        .all()
    )
    done = 0
    for draft in rows:
        try:
            card = draft.card_id or ensure_card_id(db, draft)
            conv = db.get(AiConversation, draft.conversation_id)
            if conv:
                clear_card_chat_data(db, conv, draft)
                _notify_conversation_deleted(db, conv, card)
            else:
                draft.cleanup_at = None
                meta = _draft_meta(draft)
                meta["chat_cleared_at"] = now.isoformat()
                _write_draft_meta(draft, meta)
            done += 1
        except Exception:
            # Retry-safe: leave cleanup_at so worker can try again; never crash the loop
            continue
    if done:
        db.commit()
    return done
