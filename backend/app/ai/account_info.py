"""Human-friendly WhatsApp replies for account / wallet / listing questions.

Never expose backend jargon (FREE_USER, ELIGIBLE, onboarded, backend DB).
ChatGPT-style: heading + bullet points + bold key numbers.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models import AiConversation
from .account_filter import connect_webhook_account, read_account_details
from .format_reply import bold, fmt_section
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.account_info")

_ACCOUNT_DETAIL = re.compile(
    r"("
    r"\b(account|akount|अकाउंट)\b.{0,40}\b(detail|details|info|jaankari|jankari|batao|bata|summary)\b"
    r"|\b(meri|mera|mujhe|mere)\b.{0,24}\b(account|akount)\b"
    r"|account\s*(detail|details|info)"
    r"|(database|db)\s*(mein|me)\s*(check|dekh|dekho)"
    r"|database\s*mein\s*check"
    r")",
    re.I,
)
_TOKEN_INFO = re.compile(
    r"("
    r"\b(token|tokens|wallet|credit|credits)\b"
    r"|wallet.{0,20}(kitne|kitna|balance|mein|me)"
    r"|(kitne|kitna).{0,20}(token|tokens|credit)"
    r")",
    re.I,
)
_LISTING_INFO = re.compile(
    r"("
    r"\b(meri|mere|mujhe)\b.{0,30}\b(listing|listings|post|ads?)\b"
    r"|\b(listing|listings)\b.{0,30}\b(kitni|kitti|kitne|kitna|status|available|hai|hain|kahan|kitii)\b"
    r"|listing\s*(status|check|batao|detail|kitni|kitti)"
    r"|(kitni|kitti|kitne|kitna)\s+(listing|listings)"
    r")",
    re.I,
)


def wants_account_snapshot(text: str) -> bool:
    msg = text or ""
    return bool(_ACCOUNT_DETAIL.search(msg) or _TOKEN_INFO.search(msg) or _LISTING_INFO.search(msg))


def _human_plan(account_type: str) -> str:
    at = (account_type or "").lower()
    if at in {"broker"}:
        return "broker / partner"
    if at in {"office", "staff", "admin"}:
        return "office"
    if at in {"token"}:
        return "token wallet"
    return "free plan"


def _refresh_snapshot(db: Session, conv: AiConversation) -> dict:
    try:
        connect_webhook_account(db, conv, refresh=True)
        db.flush()
    except Exception:
        log.exception("account snapshot refresh failed for %s", conv.mobile)

    details = read_account_details(db, conv.mobile)
    remote = details.remote if isinstance(details.remote, dict) else {}
    payload = _payload(conv)

    tokens = remote.get("credits")
    if tokens is None:
        tokens = remote.get("tokens")
    try:
        tokens_i = int(tokens) if tokens is not None else None
    except (TypeError, ValueError):
        tokens_i = None

    atype = str(remote.get("account_type") or payload.get("account_type") or "free").lower()
    name = (
        remote.get("name")
        or payload.get("customer_name")
        or conv.customer_name
        or "Sir/Ma'am"
    )
    buy = str(remote.get("buy_link") or payload.get("account_buy_link") or "https://infradealer.com/wallet")

    live = remote.get("listings_live")
    pending = remote.get("listings_pending")
    total = remote.get("listings_total")
    try:
        live_i = int(live) if live is not None else None
        pending_i = int(pending) if pending is not None else None
        total_i = int(total) if total is not None else None
    except (TypeError, ValueError):
        live_i = pending_i = total_i = None

    if live_i is None and pending_i is None:
        st = str(payload.get("listing_status") or "").upper()
        local_pending = 1 if st in {"PENDING_REVIEW", "UNDER_REVIEW", "READY_FOR_REVIEW", "SUBMITTED", "DRAFT"} else 0
        local_live = 1 if st in {"LIVE", "POSTED", "APPROVED", "PUBLISHED"} else 0
        pending_i = local_pending
        live_i = local_live
        total_i = local_pending + local_live

    if tokens_i is not None:
        payload["wallet_tokens"] = tokens_i
    payload["account_plan_label"] = _human_plan(atype)
    if buy:
        payload["account_buy_link"] = buy
    _write_payload(conv, payload)

    return {
        "name": str(name)[:80],
        "plan": _human_plan(atype),
        "tokens": tokens_i,
        "buy_link": buy,
        "listings_live": live_i if live_i is not None else 0,
        "listings_pending": pending_i if pending_i is not None else 0,
        "listings_total": total_i if total_i is not None else 0,
        "onboarded": bool(payload.get("account_onboarded") or details.webhook_connected),
        "found": bool(details.infradealer_user_id or payload.get("account_onboarded")),
    }


def _token_bullets(snap: dict) -> list[str]:
    tok = snap.get("tokens")
    if tok is None:
        return [f"Wallet balance sync nahi hua — check: {snap['buy_link']}"]
    if tok <= 0:
        return [
            f"Wallet: {bold('0 tokens')}",
            f"Tokens chahiye to: {snap['buy_link']}",
        ]
    return [f"Wallet: {bold(f'{tok} tokens')}"]


def _listing_bullets(snap: dict) -> list[str]:
    live = int(snap.get("listings_live") or 0)
    pending = int(snap.get("listings_pending") or 0)
    total = int(snap.get("listings_total") or (live + pending))
    if live == 0 and pending == 0:
        return [
            f"Live / pending listings: {bold('0')}",
            "Nayi listing ke liye vehicle detail bhej dijiye",
        ]
    return [
        f"Total: {bold(total)}",
        f"Live website pe: {bold(live)}",
        f"Admin review me: {bold(pending)}",
    ]


def handle_account_info(db: Session, conv: AiConversation, text: str, lang: str) -> str | None:
    """Deterministic ChatGPT-style reply — heading + bullets, only asked points."""
    msg = (text or "").strip()
    if not wants_account_snapshot(msg):
        return None

    snap = _refresh_snapshot(db, conv)
    want_tokens = bool(_TOKEN_INFO.search(msg))
    want_listings = bool(_LISTING_INFO.search(msg))
    want_account = bool(_ACCOUNT_DETAIL.search(msg))
    if re.search(r"\b(database|db)\b", msg, re.I):
        want_tokens = True
        want_account = True

    if not snap.get("found") and not snap.get("onboarded"):
        return fmt_section(
            "Account nahi mila",
            [
                "Is WhatsApp number pe clear InfraDealer account nahi dikha",
                f"{bold('account banao')} likh kar yahi se bana sakte hain",
                "Ya registered number se message kijiye",
            ],
        )

    # Tokens only
    if want_tokens and not want_account and not want_listings:
        return fmt_section("Wallet / Tokens", _token_bullets(snap))

    # Listings only
    if want_listings and not want_account and not want_tokens:
        return fmt_section("Aapki Listings", _listing_bullets(snap))

    # Tokens + listings (no full account dump)
    if want_tokens and want_listings and not want_account:
        bullets = []
        bullets.extend(_token_bullets(snap))
        bullets.extend(_listing_bullets(snap))
        return fmt_section("Wallet + Listings", bullets)

    # Full account detail
    bullets = [
        f"Status: {bold('active')}",
        f"Plan: {bold(snap['plan'])}",
    ]
    if want_tokens or want_account:
        bullets.extend(_token_bullets(snap))
    if want_listings or want_account:
        bullets.extend(_listing_bullets(snap))

    return fmt_section(
        "Account Summary",
        bullets,
        footer="Next: vehicle bechna / kharidna, listing, ya password help bataiye.",
    )
