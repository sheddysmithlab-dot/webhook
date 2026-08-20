"""Per-Card identity: WhatsApp number = user; CARD-00X = listing."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AiConversation, AiListingDraft, AiMedia

CARD_RE = re.compile(r"\bCARD[- ]?0*(\d+)\b", re.I)
MIN_PHOTOS = 2
MAX_PHOTOS = 5
CLEANUP_MINUTES = 10


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
    if draft.card_id and CARD_RE.search(draft.card_id):
        return draft.card_id
    seq = next_card_seq(db, draft.mobile)
    draft.card_id = format_card_id(seq)
    return draft.card_id


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


def switch_active_card(db: Session, conv: AiConversation, card_id: str) -> AiListingDraft | None:
    draft = (
        db.query(AiListingDraft)
        .filter(AiListingDraft.mobile == conv.mobile, AiListingDraft.card_id == card_id)
        .order_by(AiListingDraft.id.desc())
        .first()
    )
    if not draft:
        return None
    conv.draft_id = draft.id
    return draft


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
    return MIN_PHOTOS <= n <= MAX_PHOTOS or n >= MIN_PHOTOS


def photos_status(db: Session, draft_id: int | None) -> dict:
    n = card_photo_count(db, draft_id)
    return {
        "count": n,
        "min": MIN_PHOTOS,
        "max": MAX_PHOTOS,
        "need_more": n < MIN_PHOTOS,
        "at_max": n >= MAX_PHOTOS,
        "ready": n >= MIN_PHOTOS,
    }


def schedule_card_cleanup(draft: AiListingDraft, minutes: int = CLEANUP_MINUTES) -> None:
    draft.cleanup_at = _now() + timedelta(minutes=minutes)


def clear_card_chat_data(db: Session, conv: AiConversation, draft: AiListingDraft) -> None:
    """Wipe per-card chat payload fields for a finished card — keep other cards."""
    from .schema import empty_payload
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    # If this draft is active, reset listing fields so next card starts clean
    if conv.draft_id == draft.id:
        keep = {
            "whatsapp_number": payload.get("whatsapp_number"),
            "customer_name": payload.get("customer_name"),
            "wa_name": payload.get("wa_name"),
            "profile_id": payload.get("profile_id"),
            "profile_status": payload.get("profile_status"),
            "otp_verified": payload.get("otp_verified"),
            "account_onboarded": payload.get("account_onboarded"),
            "account_role": payload.get("account_role"),
            "account_step": "done" if payload.get("account_onboarded") else "",
            "ai_introduced": payload.get("ai_introduced"),
            "language": payload.get("language"),
            "verification_status": payload.get("verification_status"),
        }
        fresh = empty_payload()
        fresh.update({k: v for k, v in keep.items() if v not in (None, "")})
        fresh["active_card_id"] = None
        _write_payload(conv, fresh)
        conv.draft_id = None
        conv.state = "NEW_CHAT"
    draft.status = "CLEARED"
    draft.cleanup_at = None
    # Detach media from cleared draft (files can remain on disk)
    db.query(AiMedia).filter(AiMedia.draft_id == draft.id).update(
        {"draft_id": None}, synchronize_session=False
    )


def process_due_card_cleanups(db: Session, limit: int = 50) -> int:
    now = _now()
    rows = (
        db.query(AiListingDraft)
        .filter(
            AiListingDraft.cleanup_at.isnot(None),
            AiListingDraft.cleanup_at <= now,
            AiListingDraft.status.in_(["POSTED", "APPROVED", "LIVE", "REJECTED"]),
        )
        .order_by(AiListingDraft.cleanup_at.asc())
        .limit(limit)
        .all()
    )
    done = 0
    for draft in rows:
        conv = db.get(AiConversation, draft.conversation_id)
        if conv:
            clear_card_chat_data(db, conv, draft)
        else:
            draft.status = "CLEARED"
            draft.cleanup_at = None
        done += 1
    if done:
        db.commit()
    return done
