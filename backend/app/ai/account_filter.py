"""Account filter — identify Free / Office / Token / Broker and listing eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import AiConversation, InfraDealerAccountState, User

SITE = "https://www.infradealer.com"
WALLET = "https://www.infradealer.com/wallet"
BROKER = "https://www.infradealer.com/business-partners"


@dataclass
class AccountVerdict:
    account_type: str  # free | office | token | broker | unknown | missing
    can_post: bool
    reason: str
    buy_link: str = ""
    label: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def classify_user(user: User | None, state: InfraDealerAccountState | None = None) -> str:
    if not user and not state:
        return "missing"
    role = (getattr(user, "role", None) or "").strip().lower() if user else ""
    meta = {}
    if state and getattr(state, "meta_json", None):
        import json

        try:
            meta = json.loads(state.meta_json or "{}")
        except Exception:
            meta = {}
    hint = str(meta.get("account_type") or meta.get("plan") or "").lower()
    if role in {"office", "admin", "staff"} or hint == "office":
        return "office"
    if role in {"broker", "partner"} or hint == "broker":
        return "broker"
    if role in {"token", "verified", "premium"} or hint in {"token", "verified_tokens"}:
        return "token"
    if user or (state and (state.account_status or "").upper() in {"ACCOUNT_CREATED", "ACCOUNT_FOUND", "VERIFIED"}):
        return "free"
    return "missing"


def broker_subscription_active(user: User | None, state: InfraDealerAccountState | None) -> bool:
    """True when broker has an active ~1 year subscription flag in meta/state."""
    if not state:
        return False
    import json

    try:
        meta = json.loads(state.meta_json or "{}")
    except Exception:
        meta = {}
    if meta.get("broker_subscription_active") is True:
        return True
    exp = meta.get("broker_subscription_expires") or meta.get("subscription_expires_at")
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
    import json

    if state:
        try:
            meta = json.loads(state.meta_json or "{}")
        except Exception:
            meta = {}
        credits = meta.get("verified_tokens") or meta.get("token_credits") or meta.get("credits")
        try:
            if credits is not None and int(credits) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def find_account(db: Session, mobile: str) -> tuple[User | None, InfraDealerAccountState | None]:
    phone = "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]
    user = db.query(User).filter(User.mobile == phone).first() if phone else None
    state = (
        db.query(InfraDealerAccountState).filter(InfraDealerAccountState.mobile == phone).first()
        if phone
        else None
    )
    return user, state


def verify_account(db: Session, mobile: str) -> AccountVerdict:
    """chatmsg → process → account find → verify account."""
    user, state = find_account(db, mobile)
    kind = classify_user(user, state)

    if kind == "missing":
        return AccountVerdict(
            account_type="missing",
            can_post=False,
            reason="no_account",
            buy_link=SITE,
            label="No Account",
        )

    if kind == "office":
        return AccountVerdict(
            account_type="office",
            can_post=True,
            reason="office_free",
            label="Office Account",
        )

    if kind == "broker":
        if broker_subscription_active(user, state):
            return AccountVerdict(
                account_type="broker",
                can_post=True,
                reason="broker_subscription",
                label="Broker Account",
            )
        return AccountVerdict(
            account_type="broker",
            can_post=False,
            reason="broker_needs_credit",
            buy_link=BROKER,
            label="Broker Account",
        )

    if kind == "token":
        if token_credits_available(user, state):
            return AccountVerdict(
                account_type="token",
                can_post=True,
                reason="token_ok",
                label="Token Based (Verified)",
            )
        return AccountVerdict(
            account_type="token",
            can_post=False,
            reason="token_needed",
            buy_link=WALLET,
            label="Token Based (Verified)",
        )

    # free listing
    return AccountVerdict(
        account_type="free",
        can_post=True,
        reason="free_listing",
        label="Free Listing",
    )


def apply_verdict_to_payload(payload: dict, verdict: AccountVerdict) -> dict:
    payload["account_type"] = verdict.account_type
    payload["account_label"] = verdict.label
    payload["account_can_post"] = verdict.can_post
    payload["account_reason"] = verdict.reason
    payload["account_buy_link"] = verdict.buy_link
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


def sync_conversation_account(db: Session, conv: AiConversation) -> AccountVerdict:
    from .tools import _payload, _write_payload

    verdict = verify_account(db, conv.mobile)
    payload = _payload(conv)
    apply_verdict_to_payload(payload, verdict)
    _write_payload(conv, payload)
    return verdict
