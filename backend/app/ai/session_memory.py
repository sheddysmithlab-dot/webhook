"""Chat session memory: idle reset after user's last message + last-listing resume.

Idle rule (important):
- Clock starts from the user's LAST inbound WhatsApp message only.
- Reset happens on the NEXT user message if ≥10 minutes passed since that last message.
- This is NOT a recurring "every 10 minutes" timer while chatting.
- Bot replies / background jobs must NOT refresh the idle clock.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AiConversation, AiListingDraft
from .cards import CLEANUP_MINUTES, hydrate_card_session, persist_active_card_session
from .schema import empty_payload
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.session_memory")

# Minutes of silence after the user's last message before live chat topic is forgotten.
MEMORY_IDLE_MINUTES = CLEANUP_MINUTES  # 10

_UPDATE_LAST = re.compile(
    r"("
    r"(last|pichhli|pichli|pehle|previous|akhir|purani|wahi).{0,40}"
    r"(listing|post|card|ad|gaadi|gadi|wali)"
    r"|"
    r"(listing|post|card|ad).{0,40}"
    r"(change|update|badlo|sahi|galat|edit|correct|fix|price|rate|year|model|photo)"
    r"|"
    r"(price|rate|year|model|photo|km|location).{0,24}"
    r"(badlo|change|update|kar\s*do|sahi\s*kar)"
    r".{0,30}(listing|post|card|wahi|last|pichhli)?"
    r"|"
    r"\b(update\s+(my\s+)?listing|edit\s+(my\s+)?listing|listing\s+update)\b"
    r"|"
    r"(usme|isme|us\s*me|is\s*me).{0,20}(change|badlo|update|price|rate)"
    r")",
    re.I,
)

_IDENTITY_KEEP = (
    "whatsapp_number", "customer_name", "wa_name", "wa_id", "profile_id",
    "profile_status", "otp_verified", "account_onboarded", "account_role",
    "account_type", "account_label", "account_can_post", "account_reason",
    "account_buy_link", "account_eligibility", "account_context",
    "ai_introduced", "language", "verification_status", "infradealer_user_id",
    "listing_url", "infradealer_listing_id", "listing_status", "push_stage",
    "rejection_reason", "summary_json", "confirmed_json", "submission",
    "next_listing_not_before", "last_listing_submitted_at",
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_iso(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def wants_update_last_listing(text: str) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    return bool(_UPDATE_LAST.search(msg))


def touch_user_message(conv: AiConversation, payload: dict | None = None) -> None:
    """Record USER inbound message time — only clock that drives idle reset."""
    pl = payload if isinstance(payload, dict) else _payload(conv)
    now = _now().isoformat()
    pl["last_user_message_at"] = now
    pl["last_activity_at"] = now  # alias for older payloads
    pl["memory_idle_minutes"] = MEMORY_IDLE_MINUTES
    _write_payload(conv, pl)


touch_activity = touch_user_message


def last_user_message_at(conv: AiConversation, payload: dict | None = None) -> datetime | None:
    """Timestamp of the user's previous inbound message (not bot / not DB updated_at)."""
    pl = payload if isinstance(payload, dict) else _payload(conv)
    return (
        _parse_iso(str(pl.get("last_user_message_at") or ""))
        or _parse_iso(str(pl.get("last_activity_at") or ""))
    )


def idle_minutes_since_user(conv: AiConversation, payload: dict | None = None) -> float:
    """Minutes since user's last message. 0 if never messaged / unknown."""
    last = last_user_message_at(conv, payload)
    if last is None:
        return 0.0
    return max((_now() - last).total_seconds() / 60.0, 0.0)


idle_minutes = idle_minutes_since_user


def is_memory_idle(conv: AiConversation, payload: dict | None = None, minutes: int = MEMORY_IDLE_MINUTES) -> bool:
    """True only when user has been silent ≥ `minutes` since their last message."""
    last = last_user_message_at(conv, payload)
    if last is None:
        return False  # no prior user message → don't wipe
    return idle_minutes_since_user(conv, payload) >= float(minutes)


def reset_idle_chat_memory(db: Session, conv: AiConversation) -> dict:
    """Forget live chat topic after user-idle — keep account + listing DB memory."""
    payload = _payload(conv)
    try:
        if conv.draft_id:
            persist_active_card_session(db, conv)
    except Exception:
        log.exception("persist before idle reset failed")

    keep = {k: payload.get(k) for k in _IDENTITY_KEEP if payload.get(k) not in (None, "")}
    fresh = empty_payload()
    fresh.update(keep)
    fresh["chat_cleared"] = True
    fresh["memory_reset_at"] = _now().isoformat()
    fresh["memory_reset_reason"] = "USER_IDLE_AFTER_LAST_MSG"
    fresh["rm_state"] = "NEW_SESSION"
    fresh["master_workflow_state"] = "NEW"
    fresh["active_card_id"] = None
    fresh["awaiting_confirm"] = False
    fresh["customer_confirmed"] = False
    fresh["intent"] = None
    # Caller touches after reset for THIS inbound message
    fresh.pop("last_user_message_at", None)
    fresh.pop("last_activity_at", None)
    _write_payload(conv, fresh)
    conv.draft_id = None
    conv.intent = ""
    conv.state = "NEW_CHAT"
    conv.error_message = ""
    log.info("user-idle memory reset mobile=***%s", (conv.mobile or "")[-4:])
    return fresh


def find_last_listing_draft(db: Session, mobile: str) -> AiListingDraft | None:
    phone = "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]
    if not phone:
        return None
    return (
        db.query(AiListingDraft)
        .filter(AiListingDraft.mobile == phone)
        .filter(AiListingDraft.status.notin_(["CLEARED", "DELETED"]))
        .order_by(AiListingDraft.updated_at.desc(), AiListingDraft.id.desc())
        .first()
    )


def resume_last_listing_for_update(db: Session, conv: AiConversation) -> AiListingDraft | None:
    draft = find_last_listing_draft(db, conv.mobile)
    if not draft:
        return None
    try:
        if conv.draft_id and conv.draft_id != draft.id:
            persist_active_card_session(db, conv)
    except Exception:
        pass
    conv.draft_id = draft.id
    hydrate_card_session(db, conv, draft)
    payload = _payload(conv)
    confirmed = {}
    try:
        confirmed = json.loads(draft.confirmed_json or "{}")
        if not isinstance(confirmed, dict):
            confirmed = {}
    except json.JSONDecodeError:
        confirmed = {}
    if confirmed:
        for key, val in confirmed.items():
            if val not in (None, "", [], {}) and not payload.get(key):
                payload[key] = val
    payload["intent"] = payload.get("intent") or draft.intent or "SELL"
    payload["active_card_id"] = draft.card_id
    payload["awaiting_confirm"] = False
    payload["customer_confirmed"] = False
    payload["chat_cleared"] = False
    payload["rm_state"] = "DATA_COLLECTION"
    payload["workflow_state"] = "CORRECTION_REQUIRED"
    payload["master_workflow_state"] = "CORRECTION_REQUIRED"
    payload["listing_edit_mode"] = True
    payload["editing_draft_id"] = draft.id
    payload["draft_version"] = int(payload.get("draft_version") or 1) + 1
    payload["confirmed_version"] = None
    _write_payload(conv, payload)
    conv.intent = "SELL"
    conv.state = "SELL_DATA_COLLECTION"
    return draft


def prepare_turn(db: Session, conv: AiConversation, text: str) -> dict[str, Any]:
    """
    Decide turn mode BEFORE agents run.

    Idle check uses the previous inbound timestamp only; then this message
    updates last_user_message_at (chatting never resets mid-conversation).
    """
    payload = _payload(conv)
    update = wants_update_last_listing(text)
    idle = is_memory_idle(conv, payload)

    if update:
        draft = resume_last_listing_for_update(db, conv)
        touch_user_message(conv)
        if draft:
            return {"mode": "engine_update", "draft": draft, "reset": False, "card_id": draft.card_id}
        return {"mode": "continue", "draft": None, "reset": False, "missing_last_listing": True}

    if idle and not payload.get("chat_cleared"):
        had_topic = bool(
            payload.get("intent")
            or payload.get("brand")
            or payload.get("awaiting_confirm")
            or payload.get("rm_state") not in {"", None, "NEW_SESSION", "INTENT_DETECTION"}
            or conv.draft_id
        )
        if had_topic:
            reset_idle_chat_memory(db, conv)
            touch_user_message(conv)
            return {"mode": "new_chat", "draft": None, "reset": True}

    touch_user_message(conv)
    return {"mode": "continue", "draft": None, "reset": False}
