"""WhatsApp webhook → InfraDealer account filter.

Collects WhatsApp user identity from inbound webhook chats, connects that
phone to InfraDealer `account.check`, reads account details, and confirms
listing eligibility (Free / Office / Token / Broker).

Flow: account_filter → chat_memory → data_filteration → data_push
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..identity import usable_person_name, wa_profile_name
from ..models import AiConversation, Chat, Contact, InfraDealerAccountState, InfraDealerRequest, User

log = logging.getLogger("infradealer.ai.account_filter")

SITE = "https://www.infradealer.com"
WALLET = "https://www.infradealer.com/wallet"
BROKER = "https://www.infradealer.com/business-partners"

_FOUND_STATUSES = {
    "ACCOUNT_CREATED",
    "ACCOUNT_EXISTS",
    "ACCOUNT_FOUND",
    "VERIFIED",
    "OTP_VERIFIED",
}
_SKIP_REFRESH_STATUSES = _FOUND_STATUSES | {"NOT_FOUND", "OTP_PENDING", "OTP_VERIFIED"}
_CONFIRMED_REMOTE = {"verified", "active", "live", "ok", "account_created", "account_found"}


@dataclass
class WhatsAppUser:
    mobile: str = ""
    wa_id: str = ""
    wa_name: str = ""
    customer_name: str = ""
    conversation_id: str = ""
    source: str = "whatsapp_webhook"

    def as_dict(self) -> dict[str, str]:
        return {
            "mobile": self.mobile,
            "wa_id": self.wa_id,
            "wa_name": self.wa_name,
            "customer_name": self.customer_name,
            "conversation_id": self.conversation_id,
            "source": self.source,
        }


@dataclass
class AccountDetails:
    mobile: str = ""
    local_user_id: int | None = None
    local_name: str = ""
    local_role: str = ""
    account_ready: bool = False
    profile_status: str = "UNKNOWN"
    account_status: str = "NOT_REQUESTED"
    infradealer_user_id: str = ""
    registration_id: str = ""
    remote: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    webhook_connected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mobile": self.mobile,
            "local_user_id": self.local_user_id,
            "local_name": self.local_name,
            "local_role": self.local_role,
            "account_ready": self.account_ready,
            "profile_status": self.profile_status,
            "account_status": self.account_status,
            "infradealer_user_id": self.infradealer_user_id,
            "registration_id": self.registration_id,
            "remote": self.remote,
            "webhook_connected": self.webhook_connected,
        }


@dataclass
class AccountVerdict:
    account_type: str  # free | office | token | broker | unknown | missing
    can_post: bool
    reason: str
    buy_link: str = ""
    label: str = ""
    details: AccountDetails | None = None
    wa_user: WhatsAppUser | None = None

    @property
    def eligibility(self) -> str:
        return "ELIGIBLE" if self.can_post else "NOT_ELIGIBLE"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def phone_digits(mobile: str | None) -> str:
    return "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]


def _load_meta(state: InfraDealerAccountState | None) -> dict[str, Any]:
    if not state or not getattr(state, "meta_json", None):
        return {}
    try:
        data = json.loads(state.meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remote_account(meta: dict[str, Any]) -> dict[str, Any]:
    acct = meta.get("account")
    return acct if isinstance(acct, dict) else {}


def _meta_hint(meta: dict[str, Any], *keys: str) -> str:
    remote = _remote_account(meta)
    for key in keys:
        val = meta.get(key)
        if val in (None, ""):
            val = remote.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def classify_user(user: User | None, state: InfraDealerAccountState | None = None) -> str:
    if not user and not state:
        return "missing"
    role = (getattr(user, "role", None) or "").strip().lower() if user else ""
    meta = _load_meta(state)
    hint = _meta_hint(meta, "account_type", "plan", "type", "role").lower()
    remote_role = str(_remote_account(meta).get("role") or "").strip().lower()
    role = role or remote_role
    if role in {"office", "admin", "staff"} or hint == "office":
        return "office"
    if role in {"broker", "partner"} or hint == "broker":
        return "broker"
    if role in {"token", "verified", "premium"} or hint in {"token", "verified_tokens"}:
        return "token"
    status = (state.account_status or "").upper() if state else ""
    if user or status in _FOUND_STATUSES:
        return "free"
    return "missing"


def broker_subscription_active(user: User | None, state: InfraDealerAccountState | None) -> bool:
    """True when broker has an active ~1 year subscription flag in meta/state."""
    if not state:
        return False
    meta = _load_meta(state)
    remote = _remote_account(meta)
    if meta.get("broker_subscription_active") is True or remote.get("broker_subscription_active") is True:
        return True
    exp = (
        meta.get("broker_subscription_expires")
        or meta.get("subscription_expires_at")
        or remote.get("broker_subscription_expires")
        or remote.get("subscription_expires_at")
    )
    if not exp:
        return False
    try:
        if isinstance(exp, (int, float)):
            return datetime.utcfromtimestamp(exp) > _now()
        dt = datetime.fromisoformat(str(exp).replace("Z", ""))
        return dt > _now()
    except Exception:
        return False


def token_credits_available(user: User | None, state: InfraDealerAccountState | None) -> bool:
    if not state:
        return False
    meta = _load_meta(state)
    remote = _remote_account(meta)
    credits = (
        meta.get("verified_tokens")
        or meta.get("token_credits")
        or meta.get("credits")
        or remote.get("verified_tokens")
        or remote.get("token_credits")
        or remote.get("credits")
    )
    try:
        return credits is not None and int(credits) > 0
    except (TypeError, ValueError):
        return False


def find_account(db: Session, mobile: str) -> tuple[User | None, InfraDealerAccountState | None]:
    phone = phone_digits(mobile)
    user = db.query(User).filter(User.mobile == phone).first() if phone else None
    state = (
        db.query(InfraDealerAccountState).filter(InfraDealerAccountState.mobile == phone).first()
        if phone
        else None
    )
    return user, state


def collect_whatsapp_user(
    db: Session,
    conv: AiConversation | None = None,
    mobile: str = "",
    *,
    persist: bool = False,
) -> WhatsAppUser:
    """Read WhatsApp identity collected by the inbound webhook (chat + contact + payload)."""
    from .tools import _payload, _write_payload

    phone = phone_digits((conv.mobile if conv else "") or mobile)
    payload = _payload(conv) if conv else {}
    wa = WhatsAppUser(
        mobile=phone,
        conversation_id=(conv.conversation_id if conv else "") or "",
        wa_id=str(payload.get("wa_id") or payload.get("whatsapp_number") or phone),
        wa_name=usable_person_name(payload.get("wa_name")) or "",
        customer_name=usable_person_name(conv.customer_name if conv else "")
        or usable_person_name(payload.get("customer_name"))
        or "",
    )

    contact = db.query(Contact).filter(Contact.mobile == phone).first() if phone else None
    if contact:
        if not wa.wa_name:
            wa.wa_name = usable_person_name(contact.name) or ""
        if contact.wa_id:
            wa.wa_id = contact.wa_id

    if conv and conv.conversation_id:
        chat = (
            db.query(Chat)
            .filter(
                Chat.conversation_id == conv.conversation_id,
                Chat.direction == "inbound",
                Chat.from_name != "",
            )
            .order_by(Chat.id.desc())
            .first()
        )
        if chat:
            wa.wa_name = wa.wa_name or usable_person_name(chat.from_name) or ""
            if chat.from_mobile:
                wa.wa_id = wa.wa_id or phone_digits(chat.from_mobile) or chat.from_mobile
        if not wa.wa_name:
            wa.wa_name = wa_profile_name(db, conv.conversation_id)

    wa.customer_name = wa.customer_name or wa.wa_name
    wa.wa_id = wa.wa_id or phone

    if persist and conv:
        changed = False
        if wa.wa_name and payload.get("wa_name") != wa.wa_name:
            payload["wa_name"] = wa.wa_name
            changed = True
        if phone and payload.get("whatsapp_number") != phone:
            payload["whatsapp_number"] = phone
            changed = True
        if wa.wa_id and payload.get("wa_id") != wa.wa_id:
            payload["wa_id"] = wa.wa_id
            changed = True
        if wa.customer_name and payload.get("customer_name") != wa.customer_name:
            payload["customer_name"] = wa.customer_name
            changed = True
        if changed:
            _write_payload(conv, payload)
        if wa.customer_name and not usable_person_name(conv.customer_name):
            conv.customer_name = wa.customer_name[:120]
    return wa


def read_account_details(db: Session, mobile: str) -> AccountDetails:
    """Detailed local + webhook-cached InfraDealer account snapshot."""
    user, state = find_account(db, mobile)
    meta = _load_meta(state)
    remote = _remote_account(meta)
    status = (state.account_status or "NOT_REQUESTED") if state else "NOT_REQUESTED"
    return AccountDetails(
        mobile=phone_digits(mobile),
        local_user_id=user.id if user else None,
        local_name=(user.name if user else "") or "",
        local_role=(user.role if user else "") or "",
        account_ready=bool(user.account_ready) if user else False,
        profile_status=(state.profile_status if state else "UNKNOWN") or "UNKNOWN",
        account_status=status,
        infradealer_user_id=(state.infradealer_user_id if state else "") or str(remote.get("user_id") or ""),
        registration_id=(state.registration_id if state else "") or "",
        remote=remote,
        meta=meta,
        webhook_connected=bool(
            meta.get("webhook_verified")
            or remote.get("user_id")
            or (state and (state.infradealer_user_id or status in _FOUND_STATUSES))
        ),
    )


def apply_remote_account(state: InfraDealerAccountState | None, body: dict | None) -> dict[str, Any]:
    """Persist InfraDealer account.check / create / otp response onto local state."""
    if not state:
        return {}
    body = body if isinstance(body, dict) else {}
    meta = _load_meta(state)
    acct = body.get("account") if isinstance(body.get("account"), dict) else {}
    if acct:
        meta["account"] = acct
        state.infradealer_user_id = str(acct.get("user_id") or state.infradealer_user_id or "")
        for src, dest in (
            ("account_type", "account_type"),
            ("type", "account_type"),
            ("plan", "plan"),
            ("role", "role"),
            ("credits", "credits"),
            ("verified_tokens", "verified_tokens"),
            ("token_credits", "token_credits"),
            ("broker_subscription_active", "broker_subscription_active"),
            ("broker_subscription_expires", "broker_subscription_expires"),
            ("subscription_expires_at", "subscription_expires_at"),
            ("name", "account_name"),
            ("status", "account_status_remote"),
            ("phone", "account_phone"),
        ):
            val = acct.get(src)
            if val not in (None, ""):
                meta[dest] = val
    found = bool(
        body.get("account_found")
        or acct.get("user_id")
        or str(body.get("code") or "").upper() in {"ACCOUNT_EXISTS", "ACCOUNT_FOUND", "ACCOUNT_CREATED"}
    )
    meta["account_found"] = found
    meta["webhook_verified"] = True
    meta["webhook_code"] = str(body.get("code") or body.get("error_code") or "")
    meta["checked_at"] = _now().isoformat()
    remote_status = str(acct.get("status") or "").strip().lower()
    if found:
        state.profile_status = "VERIFIED" if remote_status in _CONFIRMED_REMOTE else "FOUND"
        if (state.account_status or "").upper() not in {"OTP_PENDING", "ACCOUNT_CREATED"}:
            state.account_status = "ACCOUNT_EXISTS"
        if remote_status in _CONFIRMED_REMOTE:
            state.account_status = "ACCOUNT_CREATED" if remote_status in {"account_created", "verified"} else state.account_status
    elif body.get("account_found") is False or str(body.get("code") or "").upper() in {"ACCOUNT_NOT_FOUND", "ACCOUNT_REQUIRED"}:
        state.profile_status = "NOT_FOUND"
        if (state.account_status or "").upper() not in {"OTP_PENDING", "ACCOUNT_CREATED"}:
            state.account_status = "NOT_FOUND"
        meta["account_found"] = False
    state.meta_json = json.dumps(meta, ensure_ascii=False)
    return meta


def connect_webhook_account(
    db: Session,
    conv: AiConversation,
    *,
    refresh: bool = False,
) -> InfraDealerAccountState | None:
    """Connect WhatsApp user phone to InfraDealer account.check and store the result."""
    collect_whatsapp_user(db, conv, persist=True)
    phone = phone_digits(conv.mobile)
    if not phone:
        return None
    _, state = find_account(db, phone)
    status = (state.account_status or "").upper() if state else ""
    if state and not refresh and status in _SKIP_REFRESH_STATUSES:
        return state
    try:
        from ..infradealer.service import InfraDealerIntegrationService

        svc = InfraDealerIntegrationService(db)
        if not svc.is_configured():
            return state
        item = svc.check_account(conv)
        if not item:
            return find_account(db, phone)[1] or state
        svc.process_outbox_item(item)
        db.flush()
        req = (
            db.query(InfraDealerRequest)
            .filter(InfraDealerRequest.request_id == item.request_id)
            .order_by(InfraDealerRequest.id.desc())
            .first()
        )
        body: dict[str, Any] = {}
        if req and req.response_json:
            try:
                parsed = json.loads(req.response_json or "{}")
                body = parsed if isinstance(parsed, dict) else {}
            except Exception:
                body = {}
        state = find_account(db, phone)[1]
        if body:
            apply_remote_account(state, body)
        return find_account(db, phone)[1] or state
    except Exception:
        log.exception("InfraDealer webhook account check failed for %s", phone)
        return find_account(db, phone)[1] or state


def verify_account(db: Session, mobile: str) -> AccountVerdict:
    """chatmsg → process → account find → verify account."""
    user, state = find_account(db, mobile)
    details = read_account_details(db, mobile)
    kind = classify_user(user, state)

    if kind == "missing":
        return AccountVerdict(
            account_type="missing",
            can_post=False,
            reason="no_account",
            buy_link=SITE,
            label="No Account",
            details=details,
        )

    if kind == "office":
        return AccountVerdict(
            account_type="office",
            can_post=True,
            reason="office_free",
            label="Office Account",
            details=details,
        )

    if kind == "broker":
        if broker_subscription_active(user, state):
            return AccountVerdict(
                account_type="broker",
                can_post=True,
                reason="broker_subscription",
                label="Broker Account",
                details=details,
            )
        return AccountVerdict(
            account_type="broker",
            can_post=False,
            reason="broker_needs_credit",
            buy_link=BROKER,
            label="Broker Account",
            details=details,
        )

    if kind == "token":
        if token_credits_available(user, state):
            return AccountVerdict(
                account_type="token",
                can_post=True,
                reason="token_ok",
                label="Token Based (Verified)",
                details=details,
            )
        return AccountVerdict(
            account_type="token",
            can_post=False,
            reason="token_needed",
            buy_link=WALLET,
            label="Token Based (Verified)",
            details=details,
        )

    return AccountVerdict(
        account_type="free",
        can_post=True,
        reason="free_listing",
        label="Free Listing",
        details=details,
    )


def apply_verdict_to_payload(payload: dict, verdict: AccountVerdict) -> dict:
    payload["account_type"] = verdict.account_type
    payload["account_label"] = verdict.label
    payload["account_can_post"] = verdict.can_post
    payload["account_reason"] = verdict.reason
    payload["account_buy_link"] = verdict.buy_link
    payload["account_eligibility"] = verdict.eligibility
    if verdict.details:
        payload["account_status"] = verdict.details.account_status
        payload["account_profile_status"] = verdict.details.profile_status
        if verdict.details.infradealer_user_id:
            payload["infradealer_user_id"] = verdict.details.infradealer_user_id
        payload["account_webhook_connected"] = verdict.details.webhook_connected
    if verdict.wa_user:
        if verdict.wa_user.wa_name:
            payload["wa_name"] = verdict.wa_user.wa_name
        if verdict.wa_user.mobile:
            payload["whatsapp_number"] = verdict.wa_user.mobile
        if verdict.wa_user.wa_id:
            payload["wa_id"] = verdict.wa_user.wa_id
        if verdict.wa_user.customer_name:
            payload.setdefault("customer_name", verdict.wa_user.customer_name)
    return payload


def eligibility_message(lang: str, verdict: AccountVerdict) -> str | None:
    """Short WA line when posting is blocked; None if can post."""
    from .i18n import t

    if verdict.can_post:
        return None
    if verdict.reason == "no_account":
        return t(lang, "account_missing")
    if verdict.reason == "token_needed":
        return t(lang, "account_need_tokens", link=verdict.buy_link or WALLET)
    if verdict.reason == "broker_needs_credit":
        return t(lang, "account_broker_credit", link=verdict.buy_link or BROKER)
    return t(lang, "account_blocked")


def confirm_account(
    db: Session,
    conv: AiConversation,
    *,
    refresh: bool = False,
) -> AccountVerdict:
    """Collect WA user → optionally live-verify via webhook → classify eligibility."""
    wa_user = collect_whatsapp_user(db, conv, persist=True)
    if refresh:
        connect_webhook_account(db, conv, refresh=True)
    verdict = verify_account(db, conv.mobile)
    verdict.wa_user = wa_user
    verdict.details = verdict.details or read_account_details(db, conv.mobile)
    return verdict


def sync_conversation_account(
    db: Session,
    conv: AiConversation,
    *,
    refresh: bool = False,
) -> AccountVerdict:
    from .tools import _payload, _write_payload

    verdict = confirm_account(db, conv, refresh=refresh)
    payload = _payload(conv)
    apply_verdict_to_payload(payload, verdict)
    _write_payload(conv, payload)
    return verdict
