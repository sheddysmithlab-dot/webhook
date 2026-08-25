"""InfraDealer account_filter — Identity & Account Resolution Engine.

Answers: WHO is this user? Resolves trusted channel identity, account type/status,
and eligibility. Does NOT chat, invent listing data, or approve listings.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..identity import usable_person_name
from ..models import (
    AiConversation,
    AiEvent,
    AiListingDraft,
    BlockedNumber,
    Chat,
    Contact,
    InfraDealerAccountState,
    User,
)
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.account_filter")

AGENT_VERSION = "account-filter-2.0"

# Stale threshold for cached InfraDealer account state before forcing a webhook refresh.
REMOTE_REFRESH_TTL_SECONDS = 300


def normalize_phone(raw: str | None) -> str:
    """Canonical last-10 Indian mobile digits (channel identity storage)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def to_e164_in(mobile10: str) -> str | None:
    m = normalize_phone(mobile10)
    if len(m) != 10 or m[0] not in "6789":
        return None
    return f"+91{m}"


@dataclass
class WhatsAppUser:
    mobile: str = ""
    wa_id: str = ""
    wa_name: str = ""
    source: str = "whatsapp_webhook"
    verified: bool = True


@dataclass
class AccountDetails:
    mobile: str = ""
    infradealer_user_id: str = ""
    webhook_connected: bool = False
    account_status: str = ""
    remote: dict = field(default_factory=dict)
    local_user_id: int | None = None
    local_role: str = ""


@dataclass
class AccountVerdict:
    mobile: str = ""
    account_type: str = "missing"  # missing|office|free|token|broker|...
    can_post: bool = False
    eligibility: str = "NOT_ELIGIBLE"  # ELIGIBLE|NOT_ELIGIBLE
    found: bool = False
    account_id: Any = None
    name: str = ""
    status: str = "NOT_FOUND"
    wa_user: WhatsAppUser | None = None
    details: AccountDetails | None = None
    reason: str = ""
    buy_link: str = "https://infradealer.com"
    conflict: bool = False  # local vs remote account_type mismatch
    onboarded: bool = False  # local User.account_ready flag

    def as_dict(self) -> dict:
        return {
            "mobile": self.mobile,
            "account_type": self.account_type,
            "can_post": self.can_post,
            "eligibility": self.eligibility,
            "found": self.found,
            "account_id": self.account_id,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "buy_link": self.buy_link,
            "conflict": self.conflict,
            "onboarded": self.onboarded,
            "agent_version": AGENT_VERSION,
        }


def _meta(state: InfraDealerAccountState | None) -> dict:
    if not state:
        return {}
    try:
        data = json.loads(state.meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _role_to_type(role: str | None) -> str:
    r = (role or "").strip().lower()
    if r in {"office", "admin", "staff"}:
        return "office"
    if r == "broker":
        return "broker"
    if r == "token":
        return "token"
    if r in {"user", "free", "seller", "buyer", ""}:
        return "free" if r else "missing"
    return r or "missing"


def verify_account(db: Session, mobile: str) -> AccountVerdict:
    """Deterministic posting eligibility from local User + InfraDealerAccountState."""
    phone = normalize_phone(mobile)
    verdict = AccountVerdict(mobile=phone)
    if len(phone) != 10:
        verdict.reason = "INVALID_PHONE"
        return verdict

    if db.query(BlockedNumber).filter(BlockedNumber.mobile == phone).first():
        verdict.status = "BLOCKED"
        verdict.reason = "BLOCKED"
        verdict.account_type = "blocked"
        return verdict

    user = db.query(User).filter(User.mobile == phone).first()
    state = (
        db.query(InfraDealerAccountState)
        .filter(InfraDealerAccountState.mobile == phone)
        .first()
    )
    meta = _meta(state)
    remote_type = str(meta.get("account_type") or meta.get("type") or "").lower()

    if not user and not state:
        verdict.account_type = "missing"
        verdict.reason = "ACCOUNT_NOT_FOUND"
        return verdict

    local_type = "missing"
    if user:
        verdict.found = True
        verdict.account_id = user.id
        verdict.name = user.name or ""
        verdict.onboarded = bool(user.account_ready)
        verdict.status = "ACTIVE" if user.account_ready else "FOUND"
        local_type = _role_to_type(user.role)
    else:
        verdict.found = bool(state and (state.infradealer_user_id or state.account_status))
        verdict.account_id = state.infradealer_user_id if state else None
        verdict.name = str(meta.get("name") or "")
        verdict.onboarded = bool(state and state.account_status in {"ACCOUNT_FOUND", "ACCOUNT_EXISTS", "VERIFIED"})
        verdict.status = (state.account_status if state else "NOT_FOUND") or "NOT_FOUND"
        local_type = _role_to_type(remote_type) if remote_type else "missing"

    # Conflict detection: local vs remote account_type disagree (excluding unknowns)
    if (
        remote_type
        and remote_type not in {"missing", "user"}
        and local_type not in {"missing"}
        and remote_type != local_type
        and remote_type != "user"
    ):
        verdict.conflict = True

    # Prefer remote account_type when present (webhook truth) unless conflict unresolved
    if remote_type in {"token", "broker", "office", "free", "user"}:
        atype = "free" if remote_type == "user" else remote_type
    else:
        atype = local_type

    verdict.account_type = atype if atype != "missing" or user else "missing"

    if verdict.account_type == "office":
        verdict.can_post = True
        verdict.eligibility = "ELIGIBLE"
        verdict.reason = "OFFICE"
    elif verdict.account_type == "token":
        credits = meta.get("credits")
        try:
            credits_i = int(credits) if credits is not None else 0
        except (TypeError, ValueError):
            credits_i = 0
        verdict.can_post = credits_i > 0
        verdict.eligibility = "ELIGIBLE" if verdict.can_post else "NOT_ELIGIBLE"
        verdict.reason = "TOKEN_CREDITS" if verdict.can_post else "TOKEN_NO_CREDITS"
        if meta.get("buy_link"):
            verdict.buy_link = str(meta["buy_link"])
        elif not verdict.can_post:
            verdict.buy_link = "https://infradealer.com/wallet"
    elif verdict.account_type == "free":
        # Free/onboarded users may still need wallet tokens at push (trial cost can be 0).
        verdict.can_post = bool(verdict.onboarded)
        verdict.eligibility = "ELIGIBLE" if verdict.can_post else "NOT_ELIGIBLE"
        verdict.reason = "FREE_USER" if verdict.can_post else "FREE_NOT_ONBOARDED"
        if meta.get("buy_link"):
            verdict.buy_link = str(meta["buy_link"])
    elif verdict.account_type == "broker":
        active = bool(
            meta.get("broker_subscription_active")
            or meta.get("subscription_active")
            or meta.get("active")
        )
        verdict.can_post = active
        verdict.eligibility = "ELIGIBLE" if active else "NOT_ELIGIBLE"
        verdict.reason = "BROKER_ACTIVE" if active else "BROKER_INACTIVE"
    else:
        verdict.can_post = False
        verdict.eligibility = "NOT_ELIGIBLE"
        verdict.reason = "ACCOUNT_MISSING"

    verdict.details = read_account_details(db, phone)
    return verdict


def collect_whatsapp_user(
    db: Session,
    conv: AiConversation,
    *,
    persist: bool = False,
    inbound_name: str = "",
) -> WhatsAppUser:
    """Trusted channel identity — never replace with a number mentioned in chat text."""
    phone = normalize_phone(conv.mobile)
    wa = WhatsAppUser(mobile=phone, verified=True, source="whatsapp_webhook")

    contact = db.query(Contact).filter(Contact.mobile == phone).first()
    if contact:
        wa.wa_id = contact.wa_id or ""
        wa.wa_name = usable_person_name(contact.name) or ""

    if not wa.wa_name:
        chat = (
            db.query(Chat)
            .filter(Chat.conversation_id == conv.conversation_id, Chat.direction == "inbound")
            .order_by(Chat.id.desc())
            .first()
        )
        if chat and chat.from_name:
            wa.wa_name = usable_person_name(chat.from_name) or ""
            if chat.from_mobile:
                wa.wa_id = wa.wa_id or re.sub(r"\D", "", chat.from_mobile)

    if inbound_name:
        wa.wa_name = usable_person_name(inbound_name) or wa.wa_name

    if persist:
        payload = _payload(conv)
        if wa.wa_name:
            payload["wa_name"] = wa.wa_name
            if not usable_person_name(conv.customer_name):
                conv.customer_name = wa.wa_name
                payload["customer_name"] = wa.wa_name
        payload["whatsapp_number"] = phone
        _write_payload(conv, payload)
    return wa


def apply_remote_account(state: InfraDealerAccountState, response: dict | None) -> InfraDealerAccountState:
    """Merge Admin/webhook account resolution into account state (no invented fields)."""
    response = response if isinstance(response, dict) else {}
    account = response.get("account") if isinstance(response.get("account"), dict) else {}
    found = bool(response.get("account_found") or response.get("found") or account)
    code = str(response.get("code") or response.get("business_code") or "")

    meta = _meta(state)
    if account:
        if account.get("user_id"):
            state.infradealer_user_id = str(account["user_id"])[:64]
        if account.get("name"):
            meta["name"] = account["name"]
        if account.get("account_type") or account.get("type"):
            meta["account_type"] = str(account.get("account_type") or account.get("type")).lower()
        if "credits" in account:
            meta["credits"] = account.get("credits")
        elif "tokens" in account:
            meta["credits"] = account.get("tokens")
        if "tokens" in account:
            meta["tokens"] = account.get("tokens")
        for k in ("listings_total", "listings_live", "listings_pending"):
            if k in account:
                meta[k] = account.get(k)
        if account.get("buy_link") or account.get("buy_url"):
            meta["buy_link"] = account.get("buy_link") or account.get("buy_url")
        if "broker_subscription_active" in account:
            meta["broker_subscription_active"] = account.get("broker_subscription_active")
        if account.get("status"):
            meta["remote_status"] = account["status"]
        if account.get("phone"):
            meta["remote_phone"] = account["phone"]

    if found or code in {"ACCOUNT_FOUND", "ACCOUNT_EXISTS"}:
        state.account_status = "ACCOUNT_FOUND"
    elif code in {"ACCOUNT_NOT_FOUND", "NOT_FOUND"} or response.get("account_found") is False:
        state.account_status = "NOT_FOUND"
    elif code:
        state.account_status = code[:24]

    meta["last_code"] = code
    state.meta_json = json.dumps(meta, ensure_ascii=False)
    return state


def read_account_details(db: Session, mobile: str) -> AccountDetails:
    phone = normalize_phone(mobile)
    details = AccountDetails(mobile=phone)
    state = (
        db.query(InfraDealerAccountState)
        .filter(InfraDealerAccountState.mobile == phone)
        .first()
    )
    user = db.query(User).filter(User.mobile == phone).first()
    if user:
        details.local_user_id = user.id
        details.local_role = user.role or ""
    if state:
        details.infradealer_user_id = state.infradealer_user_id or ""
        details.account_status = state.account_status or ""
        details.remote = _meta(state)
        details.webhook_connected = bool(
            state.infradealer_user_id
            or state.account_status in {"ACCOUNT_FOUND", "ACCOUNT_EXISTS", "VERIFIED"}
        )
    return details


def connect_webhook_account(
    db: Session,
    conv: AiConversation,
    *,
    refresh: bool = False,
) -> InfraDealerAccountState | None:
    """Trigger InfraDealer account check and sync state (best-effort)."""
    phone = normalize_phone(conv.mobile)
    if not phone:
        return None
    from ..infradealer.service import get_integration_service, account_mobile

    svc = get_integration_service(db)
    state = svc.get_or_create_account_state(account_mobile(phone), conversation_id=conv.id)
    if not refresh and state and state.account_status in {"ACCOUNT_FOUND", "NOT_FOUND", "OTP_PENDING"}:
        return state
    try:
        item = svc.check_account(conv)
        if item:
            svc.process_outbox_item(item)
            db.flush()
    except Exception:
        log.exception("connect_webhook_account failed mobile=***%s", phone[-4:])
    return (
        db.query(InfraDealerAccountState)
        .filter(InfraDealerAccountState.mobile == phone)
        .first()
    )


def _remote_state_age_seconds(state: InfraDealerAccountState | None) -> float:
    """Seconds since the InfraDealer account state was last updated."""
    if not state or not state.updated_at:
        return float("inf")
    from datetime import datetime, timezone

    updated = state.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds()


def refresh_account_if_stale(
    db: Session,
    conv: AiConversation,
    *,
    force: bool = False,
) -> InfraDealerAccountState | None:
    """Best-effort remote refresh when cached InfraDealer state is older than TTL."""
    phone = normalize_phone(conv.mobile)
    if not phone:
        return None
    state = (
        db.query(InfraDealerAccountState)
        .filter(InfraDealerAccountState.mobile == phone)
        .first()
    )
    if force or _remote_state_age_seconds(state) >= REMOTE_REFRESH_TTL_SECONDS:
        return connect_webhook_account(db, conv, refresh=True)
    return state


def sync_conversation_account(db: Session, conv: AiConversation) -> AccountVerdict:
    """Resolve identity + eligibility onto conversation payload (shared state)."""
    wa = collect_whatsapp_user(db, conv, persist=True)
    verdict = verify_account(db, conv.mobile)
    verdict.wa_user = wa

    # If eligibility failed on a token/broker account whose remote state may be stale,
    # force a best-effort refresh and re-evaluate before persisting a hard NOT_ELIGIBLE.
    if (
        verdict.eligibility == "NOT_ELIGIBLE"
        and verdict.account_type in {"token", "broker"}
        and verdict.reason in {"TOKEN_NO_CREDITS", "BROKER_INACTIVE"}
    ):
        try:
            refresh_account_if_stale(db, conv, force=True)
            verdict = verify_account(db, conv.mobile)
            verdict.wa_user = wa
        except Exception:
            log.exception("stale refresh failed mobile=***%s", normalize_phone(conv.mobile)[-4:])

    details = read_account_details(db, conv.mobile)
    verdict.details = details

    risk_level = "HIGH" if verdict.status == "BLOCKED" else ("MEDIUM" if verdict.conflict else "LOW")
    payload = _payload(conv)
    payload["account_type"] = verdict.account_type.upper()
    payload["account_label"] = verdict.account_type
    payload["account_can_post"] = verdict.can_post
    payload["account_eligibility"] = verdict.eligibility
    payload["account_reason"] = verdict.reason
    payload["account_conflict"] = verdict.conflict
    payload["account_onboarded"] = verdict.onboarded
    payload["account_buy_link"] = verdict.buy_link
    if details.infradealer_user_id:
        payload["infradealer_user_id"] = details.infradealer_user_id
    # Keep remote InfraDealer id in payload only — NEVER write it to
    # conv.profile_id (FK → local webhook users.id). That mismatch caused
    # ForeignKeyViolation and silent WhatsApp reply failures.
    if verdict.account_id:
        payload["infradealer_user_id"] = str(verdict.account_id)
        payload["remote_account_id"] = str(verdict.account_id)
        # Do not set payload["profile_id"] / conv.profile_id from remote id
    elif not verdict.found:
        payload.pop("remote_account_id", None)

    local_user = (
        db.query(User).filter(User.mobile == phone).first()
        if (phone := normalize_phone(conv.mobile))
        else None
    )
    if local_user:
        payload["profile_id"] = local_user.id
        conv.profile_id = local_user.id
        conv.profile_status = "found" if verdict.found else (conv.profile_status or "local")
    else:
        # Never keep a remote InfraDealer id on conv.profile_id (FK → local users)
        if conv.profile_id is not None:
            exists = db.query(User.id).filter(User.id == conv.profile_id).first()
            if not exists:
                conv.profile_id = None
        payload.pop("profile_id", None)
        if not verdict.found:
            conv.profile_status = "missing"
        elif verdict.found:
            conv.profile_status = "found"

    if not verdict.found:
        payload["wa_account_matched"] = False
    if verdict.found:
        payload["wa_account_matched"] = True
    if verdict.name and not usable_person_name(payload.get("customer_name")):
        payload["customer_name"] = verdict.name

    if not payload.get("workflow_id"):
        payload["workflow_id"] = f"WF-{conv.id}"
    payload["account_context"] = {
        "identity": {
            "phone": to_e164_in(conv.mobile) or conv.mobile,
            "source": "whatsapp_webhook",
            "verified": True,
            "wa_name": wa.wa_name,
        },
        "account": {
            "found": verdict.found,
            "account_id": verdict.account_id,
            "name": verdict.name or wa.wa_name,
            "type": (verdict.account_type or "ACCOUNT_TYPE_UNKNOWN").upper(),
            "status": verdict.status,
            "eligibility": verdict.eligibility,
            "can_post": verdict.can_post,
            "onboarded": verdict.onboarded,
        },
        "workflow": {
            "workflow_id": payload["workflow_id"],
            "state": payload.get("rm_state") or conv.state,
            "conversation_id": conv.conversation_id,
            "draft_id": conv.draft_id,
        },
        "security": {"conflict": verdict.conflict, "risk_level": risk_level},
    }
    _write_payload(conv, payload)
    return verdict


def resolve_identity(
    db: Session,
    conv: AiConversation,
    *,
    request_id: str = "",
    event_id: str = "",
    verdict: AccountVerdict | None = None,
) -> dict:
    """Full account_filter output contract for orchestrator / chat_memory.

    Pass ``verdict`` from ``sync_conversation_account`` to avoid re-querying the DB
    when the two are called back-to-back (orchestrator hot path).
    """
    rid = request_id or f"REQ-{uuid.uuid4().hex[:12]}"
    eid = event_id or f"EVT-{uuid.uuid4().hex[:10]}"
    if verdict is None:
        verdict = sync_conversation_account(db, conv)
    wa = verdict.wa_user or collect_whatsapp_user(db, conv, persist=True)
    verdict.wa_user = wa
    details = verdict.details or read_account_details(db, conv.mobile)

    drafts = (
        db.query(AiListingDraft)
        .filter(AiListingDraft.conversation_id == conv.id)
        .all()
    )
    pending = sum(1 for d in drafts if (d.status or "").upper() in {"DRAFT", "READY_FOR_REVIEW", "PENDING"})
    live = sum(1 for d in drafts if (d.status or "").upper() in {"LIVE", "PUBLISHED"})

    blocked = verdict.status == "BLOCKED"
    found = verdict.found
    next_state = "EXISTING_ACCOUNT" if found else "NEW_ACCOUNT_REQUIRED"
    if blocked:
        next_state = "BLOCKED_ACCOUNT"

    phone_e164 = to_e164_in(conv.mobile)
    out = {
        "success": True,
        "request_id": rid,
        "event_id": eid,
        "source_agent": "account_filter",
        "agent_version": AGENT_VERSION,
        "identity": {
            "phone": phone_e164 or conv.mobile,
            "mobile10": normalize_phone(conv.mobile),
            "source": "whatsapp_webhook",
            "verified": True,
            "valid": bool(phone_e164),
            "wa_name": wa.wa_name,
        },
        "account": {
            "found": found,
            "account_id": verdict.account_id,
            "name": verdict.name or wa.wa_name,
            "type": (verdict.account_type or "ACCOUNT_TYPE_UNKNOWN").upper(),
            "status": verdict.status,
            "eligibility": verdict.eligibility,
            "can_post": verdict.can_post,
            "onboarded": verdict.onboarded,
            "infradealer_user_id": details.infradealer_user_id,
        },
        "relationship": {
            "active_listing_count": live,
            "pending_listing_count": pending,
        },
        "workflow": {
            "active": bool(conv.intent or conv.state not in {"", "NEW"}),
            "type": None,
            "workflow_id": _payload(conv).get("workflow_id") or f"WF-{conv.id}",
            "state": conv.state,
            "conversation_id": conv.conversation_id,
            "draft_id": conv.draft_id,
        },
        "security": {
            "conflict": verdict.conflict,
            "risk_level": "HIGH" if blocked else ("MEDIUM" if verdict.conflict else "LOW"),
            "blocked": blocked,
        },
        "next_action": {
            "next_agent": "chat_memory",
            "state": next_state,
        },
        "account_context_compact": None,
        "verdict": verdict.as_dict(),
    }
    out["account_context_compact"] = {
        "identity": out["identity"],
        "account": out["account"],
        "relationship": out["relationship"],
        "workflow": out["workflow"],
        "security": out["security"],
    }

    event_type = "IDENTITY_RESOLVED" if found else ("ACCOUNT_BLOCKED" if blocked else "ACCOUNT_NOT_FOUND")
    db.add(AiEvent(
        wamid=conv.last_wamid or "",
        mobile=conv.mobile,
        event_type=event_type,
        detail=json.dumps({
            "request_id": rid,
            "event_id": eid,
            "eligibility": verdict.eligibility,
            "account_type": verdict.account_type,
            "conflict": verdict.conflict,
            "blocked": blocked,
        }, ensure_ascii=False)[:2000],
    ))
    return out
