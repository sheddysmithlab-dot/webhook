"""Office operator (postdesk) mode for the public WhatsApp agent.

An allowlisted office number (decided only by the InfraDealer backend) can
select or create a customer account and post listings onto it:

    customer 9876543210          select an existing customer
    create customer 9876543210   create a new customer (agent asks the name, no OTP)
    customer change / clear      drop the selected customer
    customer                     show the selected customer
    detail                       wallet, listing counts and recent listings of the selected customer
    hi / menu                    office menu

A bare mobile or a short sentence around one ("is number se listing daal do
9111554173") selects that customer too.

Office mode is fail-closed: it stays off unless the Meta App Secret is set, so
every inbound message was signature-verified and the sender number is genuine.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AiConversation, AiOfficeSession
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.office_mode")

TARGET_TTL = timedelta(hours=12)
OPERATOR_REVERIFY = timedelta(hours=1)
_DENY_TTL_S = 600
_denied: dict[str, float] = {}

_CREATE = re.compile(r"^\s*(?:create|new|add|naya|nayi|banao)\s+(?:customer|account|grahak)\b(.*)$", re.I | re.S)
_CUSTOMER = re.compile(r"^\s*(?:customer|grahak)\b(.*)$", re.I | re.S)
_CHANGE = re.compile(r"^\s*(?:change|badlo|switch)\s*$", re.I)
_CLEAR = re.compile(r"^\s*(?:clear|remove|hatao|reset)\s*$", re.I)
_STATUS = re.compile(r"^\s*(?:status|\?|kaun|kon|who)?\s*$", re.I)
_CANCEL = re.compile(r"^\s*(?:cancel|stop|rehne\s*do|mat\s*karo|nahi|no)\s*[.!]*\s*$", re.I)
_NAME_OK = re.compile(r"^[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F .'-]{1,79}$")
_PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?\s*91[\s\-]*|0)?[6-9](?:[\s\-]*\d){9}(?!\d)")
_ACCOUNT_WORD = re.compile(
    r"\b(?:account|acount|accont|accout|a/c|khata|customer|costumer|grahak|user\s*id|profile)\b", re.I
)
_SELECT_INTENT = re.compile(
    r"\b(?:number|nummber|numbr|nmbr|no|mobile|mob|phone|listing|listings|post|posting|daal\w*|dal|dalo|"
    r"detail\w*|select|chuno|wale|wala|wali|se|par|pe|ka|ki|ke|check|dekh\w*|dikha\w*|bata\w*|"
    r"kholo|open|access|login|use)\b",
    re.I,
)
_LISTING_SIGNAL = re.compile(
    r"\b(?:19[89]\d|20[0-3]\d)\b|₹|\b(?:lakh|lakhs|lac|lacs|price|rate|rs|km|kms|hours?|hrs|model|seller|"
    r"contact|owner|malik|bechni|bechna|bikau|sale|sell|condition|tyre|engine)\b",
    re.I,
)
_DETAIL = re.compile(
    r"\b(?:detail\w*|detial\w*|deatil\w*|detal\w*|info\w*|jaa?nkari|summary|balance|tokens?|wallet|khata)\b"
    r"|\blistings?\b.{0,30}\b(?:dikha\w*|dekh\w*|bata\w*|list|kitni|kitne|show|view|check)\b"
    r"|\b(?:dikha\w*|dekh\w*|bata\w*|show|view|check|kitni|kitne)\b.{0,30}\blistings?\b",
    re.I,
)
_DETAIL_SKIP = re.compile(
    r"\b(?:vehicle|gaa?di|machine|photo\w*|pic\w*|image\w*|video|bhej\w*|send|sending)\b", re.I
)
_SWITCH_WORD = re.compile(
    r"\b(?:change|chang|badal\w*|badlo|switch|dusr\w*|doosr\w*|dusra|another|other|alag)\b"
    r"|\b(?:naya|nayi|new)\s+(?:account|acount|customer|grahak|number|mobile)\b",
    re.I,
)
_SAME_WORD = re.compile(r"\b(?:same|isi|issi|wahi|wohi|yahi|usi|ussi|current|purane?)\b", re.I)
_NUMBER_WORD = re.compile(r"\b(?:number|nummber|numbr|nmbr|mobile|no)\b", re.I)
_LIVE_STATUSES = {"POSTED", "APPROVED", "LIVE", "PUBLISHED"}
_GREETING = re.compile(
    r"^\s*(?:hi+|hii+|hel+o+|helo|hey+|namaste|namaskar|ram\s*ram|menu|help|start|options?|sir)\s*[.!?]*\s*$",
    re.I,
)
# After a live URL: operator can update that listing or start another on the same customer.
_UPDATE_PREV = re.compile(
    r"\b(update|edit|badlo|badal\w*|purani|pichhli|pichli|previous|wahi|same)\b"
    r"|\b(listing|post|card|ad)\s*(update|edit|badlo)"
    r"|\b(update|edit|badlo)\s*(listing|post|card|ad|karo|kr\s*do)?\b",
    re.I,
)
_NEW_LISTING = re.compile(
    r"\b(new|naya|nayi|alag|dusri|doosri)\b.{0,20}\b(listing|post|card|ad|gaadi|gadi)\b"
    r"|\b(listing|post|card|ad)\b.{0,16}\b(new|naya|nayi|alag)\b"
    r"|\b(nayi|naya|new)\s*(wali|wale)?\s*$",
    re.I,
)
_SHORT_WORDS = 14
_OFFICE_LAST_KEYS = (
    "office_ready_for_next",
    "awaiting_office_next_action",
    "office_last_live_listing_id",
    "office_last_live_url",
    "office_last_live_draft_id",
    "office_last_live_card_id",
    "office_last_live_title",
    "office_pending_text",
    "office_held_media_ids",
    "office_send_choice_buttons",
    "office_button_set",
    "office_selected_next",
    "office_confirm_nonce",
    "office_executed_nonce",
)
# WhatsApp interactive ids. Flow: list/reply pick → Confirm/Cancel buttons.
OFFICE_BTN_NEW_SAME = "office_new_same"
OFFICE_BTN_UPDATE = "office_update"
OFFICE_BTN_NEW_ACCOUNT = "office_new_account"
OFFICE_BTN_CONFIRM = "office_confirm"
OFFICE_BTN_CANCEL = "office_cancel"
OFFICE_BTN_CHANGE = OFFICE_BTN_CANCEL  # legacy alias
# Reply-button fallback (title max 20). Prefer list rows when sending pick UI.
OFFICE_CHOICE_BUTTONS = (
    {"id": OFFICE_BTN_NEW_SAME, "title": "Nayi Listing"},
    {"id": OFFICE_BTN_UPDATE, "title": "Update listing"},
    {"id": OFFICE_BTN_NEW_ACCOUNT, "title": "Naya account"},
)
OFFICE_LIST_ROWS = (
    {
        "id": OFFICE_BTN_NEW_SAME,
        "title": "Nayi Listing",
        "description": "Same account se another listing",
    },
    {
        "id": OFFICE_BTN_UPDATE,
        "title": "Update Previous",
        "description": "Existing listing edit/update",
    },
    {
        "id": OFFICE_BTN_NEW_ACCOUNT,
        "title": "New Account",
        "description": "New customer + new listing",
    },
)
OFFICE_CONFIRM_BUTTONS = (
    {"id": OFFICE_BTN_CONFIRM, "title": "Confirm"},
    {"id": OFFICE_BTN_CANCEL, "title": "Cancel"},
)
_OFFICE_CHOICE_SHORT = {
    "NEW": "Nayi Listing",
    "UPDATE": "Update Previous Listing",
    "NEW_ACCOUNT": "New Account + New Listing",
}
_OFFICE_CHOICE_LABELS = {
    "NEW": "🟢 Nayi Listing — Same account se another listing post karni hai.",
    "UPDATE": "🟡 Update Previous Listing — Pehle se posted listing edit/update karni hai.",
    "NEW_ACCOUNT": "🔵 New Account + New Listing — Naye customer account se listing post karni hai.",
}
_CHOICE_UPDATE = re.compile(
    r"^\s*(?:1|update|edit|badlo|badal\w*|purani|pichhli|pichli|previous|update\s*purani|"
    r"update\s*listing|update\s*previous|office_update)\s*[.!]*\s*$",
    re.I,
)
_CHOICE_NEW = re.compile(
    r"^\s*(?:2|new|naya|nayi|alag|nayi\s*listing|office_new_same)\s*"
    r"(?:wali|wale|listing|post|card|ad)?\s*[.!]*\s*$",
    re.I,
)
_CHOICE_NEW_ACCOUNT = re.compile(
    r"^\s*(?:3|naya\s*account|new\s*account|dusra\s*(?:account|customer|number|mobile)|"
    r"office_new_account)\s*[.!]*\s*$",
    re.I,
)
_CHOICE_CONFIRM = re.compile(
    r"^\s*(?:confirm|haan|han|yes|ok|okay|office_confirm)\s*[.!]*\s*$",
    re.I,
)
_CHOICE_CANCEL = re.compile(
    r"^\s*(?:cancel|nahi|no|stop|rehne\s*do|office_cancel|change|badlo|office_change)\s*[.!]*\s*$",
    re.I,
)
_CHOICE_CHANGE = _CHOICE_CANCEL  # legacy alias
_VEHICLE_WORD = re.compile(
    r"\b(?:jcb|tata|ashok|leyland|eicher|mahindra|tipper|tractor|excavator|trailer|trolley|"
    r"tyre|tayar|photo|pic|image|price|rate|model|year|km|hours?|hrs|bechni|bechna|bikau|"
    r"gaadi|gadi|truck|dumper)\b",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digits10(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[-10:]


def _phone_from(rest: str) -> str:
    compact = re.sub(r"[\s\-:+().]", "", rest or "")
    if re.fullmatch(r"(?:91|0)?[6-9]\d{9}", compact):
        return compact[-10:]
    return ""


def _customer_phone_in_text(text: str, own: str) -> str:
    """The single customer mobile written anywhere in a sentence ("91115 54173"), else ""."""
    found = {_digits10(m.group(0)) for m in _PHONE_IN_TEXT.finditer(text or "")}
    found.discard(own)
    return found.pop() if len(found) == 1 else ""


def _office_disabled_notice(conv: AiConversation, msg: str) -> str | None:
    """Tell the office number why customer commands do nothing while office mode is off."""
    if str(_payload(conv).get("account_type") or "").lower() != "office":
        return None
    own = _digits10(conv.mobile)
    looks_office = bool(_CREATE.match(msg) or _CUSTOMER.match(msg)) or bool(
        _ACCOUNT_WORD.search(msg) and _customer_phone_in_text(msg, own)
    )
    if not looks_office:
        return None
    return (
        "⚠️ Office mode abhi band hai, isliye customer account select/create nahi ho sakta.\n"
        "Wajah: WhatsApp webhook par Meta App Secret set nahi hai.\n"
        "Admin webhook.infradealer.com → Webhook → App secret set kare, uske baad dobara bhejein."
    )


def signature_enforced(db: Session) -> bool:
    """Meta signature is only checked when the App Secret is set (else fail-open)."""
    from ..services import get_or_create_settings

    try:
        return bool((get_or_create_settings(db).app_secret or "").strip())
    except Exception:
        log.exception("office_mode: meta settings unavailable")
        return False


def _client(db: Session):
    from ..infradealer.service import get_integration_service

    return get_integration_service(db)._client()


def _call(db: Session, method: str, *args) -> dict | None:
    client = _client(db)
    if not client:
        return None
    try:
        return getattr(client, method)(*args)
    except Exception:
        log.exception("office_mode: %s failed", method)
        return None


def _denied_recently(mobile: str) -> bool:
    ts = _denied.get(mobile)
    return bool(ts and time.time() - ts < _DENY_TTL_S)


def _get_session(db: Session, mobile: str) -> AiOfficeSession | None:
    return db.query(AiOfficeSession).filter(AiOfficeSession.operator_mobile == mobile).first()


def _drop_session(db: Session, mobile: str) -> None:
    row = _get_session(db, mobile)
    if row:
        db.delete(row)
        db.flush()
    _denied[mobile] = time.time()


def _ensure_session(db: Session, mobile: str) -> AiOfficeSession:
    row = _get_session(db, mobile)
    if row:
        return row
    now = _now()
    row = AiOfficeSession(operator_mobile=mobile, created_at=now, updated_at=now)
    db.add(row)
    db.flush()
    return row


def _touch(row: AiOfficeSession) -> None:
    row.updated_at = _now()


def _clear_target(row: AiOfficeSession) -> None:
    row.target_phone = ""
    row.target_user_id = ""
    row.target_name = ""
    row.target_username = ""
    _touch(row)


def _set_target(row: AiOfficeSession, customer: dict, name: str = "") -> None:
    row.target_phone = _digits10(customer.get("phone"))
    row.target_user_id = str(customer.get("user_id") or "")[:32]
    row.target_name = str(name or customer.get("name") or customer.get("username") or "")[:120]
    row.target_username = str(customer.get("username") or "")[:64]
    row.step = ""
    row.pending_phone = ""
    _touch(row)


def _target_expired(row: AiOfficeSession) -> bool:
    stamp = row.updated_at or row.created_at
    return bool(stamp and _now() - stamp > TARGET_TTL)


def active_target(row: AiOfficeSession | None) -> dict | None:
    if not row or not row.target_user_id:
        return None
    if _target_expired(row):
        return None
    return {
        "user_id": row.target_user_id,
        "phone": row.target_phone,
        "name": row.target_name or row.target_username,
        "username": row.target_username,
    }


def _forbidden(res: dict | None) -> bool:
    return bool(res) and int(res.get("http_status") or 0) == 403


def operator_session(db: Session, conv: AiConversation) -> AiOfficeSession | None:
    """Backend-verified office operator session for this sender, else None."""
    if not signature_enforced(db):
        return None
    mobile = _digits10(conv.mobile)
    if not mobile or _denied_recently(mobile):
        return None
    row = _get_session(db, mobile)
    stale = row is not None and (row.updated_at or row.created_at) and _now() - (row.updated_at or row.created_at) > OPERATOR_REVERIFY
    if row is not None and not stale:
        return row
    if row is None and not _reported_office(db, conv):
        return None
    res = _call(db, "office_customer_lookup", mobile, mobile)
    if _forbidden(res):
        _drop_session(db, mobile)
        return None
    if not res or not res.get("ok"):
        return row
    row = _ensure_session(db, mobile)
    if row.target_user_id and _target_expired(row):
        _clear_target(row)
    else:
        _touch(row)
    return row


def _customer_card(target: dict) -> str:
    lines = [
        f"Name: {target.get('name') or '-'}",
        f"Mobile: {target.get('phone') or '-'}",
        f"User ID: {target.get('username') or '-'}",
    ]
    return "\n".join(lines)


_STATUS_LABEL = {
    "approved": "Live",
    "pending": "Pending",
    "rejected": "Rejected",
    "expired": "Expired",
}


def _inr(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    if amount >= 1e7:
        return f"₹{amount / 1e7:.2f}".rstrip("0").rstrip(".") + " Cr"
    if amount >= 1e5:
        return f"₹{amount / 1e5:.2f}".rstrip("0").rstrip(".") + " lakh"
    return f"₹{amount:,.0f}"


def _int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _account_details(target: dict, customer: dict) -> str:
    """Selected customer card plus wallet / listings when the backend sent them."""
    lines = [_customer_card(target)]
    if not customer:
        return lines[0]
    status = str(customer.get("status") or "").strip()
    if status:
        lines.append(f"Status: {status}")
    if "tokens" in customer:
        lines.append(f"Wallet: {_int(customer.get('tokens'))} tokens")
    if "listings_total" in customer:
        lines.append(
            f"Listings: {_int(customer.get('listings_total'))} total"
            f" | Live {_int(customer.get('listings_live'))}"
            f" | Pending {_int(customer.get('listings_pending'))}"
            f" | Rejected {_int(customer.get('listings_rejected'))}"
        )
    recent = customer.get("recent_listings")
    if isinstance(recent, list) and recent:
        lines.append("")
        lines.append("Recent listings:")
        for i, item in enumerate(recent[:5], 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Listing").strip()[:60]
            bits = [title]
            price = _inr(item.get("price"))
            if price:
                bits.append(price)
            st = str(item.get("status") or "").lower()
            bits.append(_STATUS_LABEL.get(st, st.title() or "-"))
            lines.append(f"{i}. " + " — ".join(bits))
            if item.get("url") and st == "approved":
                lines.append(f"   {item['url']}")
    elif "listings_total" in customer:
        lines.append("Abhi is account par koi listing nahi hai.")
    return "\n".join(lines)


def _office_menu(target: dict | None) -> str:
    head = "🏢 InfraDealer Office Mode (bina OTP / password)\n"
    if target:
        head += f"📌 Selected: {target.get('name') or '-'}, {target.get('phone') or '-'}\n"
    else:
        head += "📌 Abhi koi customer select nahi hai.\n"
    return (
        head + "\n"
        "• Customer select: 98XXXXXXXX (ya: customer 98XXXXXXXX)\n"
        "• Naya account: create customer 98XXXXXXXX\n"
        "• Account detail / listings: detail\n"
        "• Listing post: customer select karke vehicle details + photos bhejein\n"
        "• Customer hatana: customer change"
    )


def _select_hint() -> str:
    return "Customer select karein: customer 98XXXXXXXX\nNaya account: create customer 98XXXXXXXX"


def _server_error() -> str:
    return "⚠️ InfraDealer server se jawab nahi mila. Thodi der baad dobara bhejein."


def _cmd_select(db: Session, mobile: str, phone: str, res: dict | None = None) -> str | None:
    if res is None:
        res = _call(db, "office_customer_lookup", mobile, phone)
    if _forbidden(res):
        _drop_session(db, mobile)
        return None
    if not res or not res.get("ok"):
        return _server_error()
    row = _ensure_session(db, mobile)
    body = res.get("body") or {}
    if not body.get("found"):
        return _create_customer(db, row, mobile, phone)
    customer = body.get("customer") or {}
    _set_target(row, customer)
    return (
        "✅ Customer selected\n"
        f"{_account_details(active_target(row) or {}, customer)}\n\n"
        "Ab bheji gayi listings isi account par post hongi.\n"
        "Badalne ke liye: customer change"
    )


def _cmd_details(db: Session, row: AiOfficeSession, mobile: str) -> str | None:
    target = active_target(row)
    if not target:
        return "Abhi koi customer select nahi hai. Kis customer ki detail chahiye?\n" + _select_hint()
    res = _call(db, "office_customer_lookup", mobile, target["phone"])
    if _forbidden(res):
        _drop_session(db, mobile)
        return None
    if not res or not res.get("ok"):
        return _server_error()
    body = res.get("body") or {}
    if not body.get("found"):
        _clear_target(row)
        return f"❌ Is mobile par ab koi account nahi hai: {target['phone']}\n" + _select_hint()
    customer = body.get("customer") or {}
    _set_target(row, customer)
    return "📋 Customer account\n" + _account_details(active_target(row) or target, customer)


def _cmd_create_start(db: Session, mobile: str, phone: str, name: str = "") -> str | None:
    res = _call(db, "office_customer_lookup", mobile, phone)
    if _forbidden(res):
        _drop_session(db, mobile)
        return None
    if not res or not res.get("ok"):
        return _server_error()
    row = _ensure_session(db, mobile)
    body = res.get("body") or {}
    if body.get("found"):
        _set_target(row, body.get("customer") or {})
        return (
            "ℹ️ Is mobile par account pehle se hai — wahi select kar diya.\n"
            f"{_customer_card(active_target(row) or {})}\n\n"
            "Ab bheji gayi listings isi account par post hongi."
        )
    return _create_customer(db, row, mobile, phone, name)


def _cmd_create_finish(db: Session, row: AiOfficeSession, mobile: str, text: str) -> str | None:
    msg = (text or "").strip()
    if _CANCEL.match(msg):
        row.step = ""
        row.pending_phone = ""
        _touch(row)
        return "Account creation cancel kar diya."
    name = re.sub(r"\s+", " ", msg)
    if not _NAME_OK.match(name):
        return "Sirf customer ka naam bhejein (jaise: Ramesh Kumar).\nRokne ke liye: cancel"
    return _create_customer(db, row, mobile, row.pending_phone, name)


def _create_customer(db: Session, row: AiOfficeSession, mobile: str, phone: str, name: str = "") -> str | None:
    """Create the customer account straight from the office line — no OTP, no password, no questions."""
    name = re.sub(r"\s+", " ", name or "").strip()
    if not _NAME_OK.match(name):
        name = f"Customer {phone[-4:]}"
    row.step = ""
    row.pending_phone = ""
    _touch(row)
    res = _call(db, "office_customer_create", mobile, phone, name)
    if _forbidden(res):
        _drop_session(db, mobile)
        return "⚠️ Ye number office operator ke liye authorized nahi hai."
    if not res:
        return _server_error()
    body = res.get("body") or {}
    code = str(body.get("code") or "").upper()
    if code == "ACCOUNT_EXISTS" and body.get("customer"):
        _set_target(row, body["customer"])
        return (
            "ℹ️ Is mobile par account pehle se hai — wahi select kar diya.\n"
            f"{_customer_card(active_target(row) or {})}"
        )
    if not res.get("ok"):
        row.step = ""
        row.pending_phone = ""
        _touch(row)
        reason = str(body.get("message") or "Account nahi ban paya.")
        return f"❌ {reason}"
    customer = body.get("customer") or {}
    _set_target(row, customer, name=name)
    return (
        "✅ New account created\n"
        f"Mobile: {row.target_phone}\n"
        f"Name: {name}\n"
        f"Username: {row.target_username}\n"
        "Created by: Office / Postdesk\n\n"
        "Koi OTP nahi bheja gaya. Customer 'Forgot Password' se apna password set kar sakta hai.\n"
        "Ab bheji gayi listings isi account par post hongi."
    )


def _short(msg: str) -> bool:
    return len(_PHONE_IN_TEXT.sub(" ", msg).split()) <= _SHORT_WORDS


def _wants_customer(msg: str) -> bool:
    rest = _PHONE_IN_TEXT.sub(" ", msg)
    if not re.sub(r"[\s,.:;!?\-+()]", "", rest):
        return True
    if _LISTING_SIGNAL.search(rest):
        return bool(_ACCOUNT_WORD.search(rest)) and _short(msg)
    if _ACCOUNT_WORD.search(rest):
        return True
    return bool(_SELECT_INTENT.search(rest)) and _short(msg)


def _natural_select(db: Session, conv: AiConversation, mobile: str, msg: str) -> str | None:
    """"91115 54173 wale account se post karna hai" → select (or offer to create) that customer.

    A vehicle description that merely contains a seller contact is left alone.
    """
    phone = _customer_phone_in_text(msg, mobile)
    if not phone or not _wants_customer(msg):
        return None
    if _known_operator(db, conv) is None:
        return None
    res = _call(db, "office_customer_lookup", mobile, phone)
    if _forbidden(res):
        _drop_session(db, mobile)
        return None
    if not res or not res.get("ok"):
        return _server_error()
    if (res.get("body") or {}).get("found"):
        return _cmd_select(db, mobile, phone, res)
    return _create_customer(db, _ensure_session(db, mobile), mobile, phone)


def _target_id(db: Session, mobile: str) -> str:
    target = active_target(_get_session(db, mobile)) if mobile else None
    return target["user_id"] if target else ""


def _last_draft_id(db: Session, mobile: str) -> int:
    from sqlalchemy import func

    from ..models import AiListingDraft

    return int(db.query(func.max(AiListingDraft.id)).filter(AiListingDraft.mobile == mobile).scalar() or 0)


def _on_target_change(db: Session, conv: AiConversation, mobile: str, before: str, after: str) -> str:
    """New customer → the listing being built belongs to nobody else; start a fresh card."""
    from .confirm import card_submitted, start_new_listing
    from .schema import empty_payload
    from .tools import _PREVIOUS_LISTING_KEYS

    payload = _payload(conv)
    payload["office_draft_floor"] = _last_draft_id(db, mobile)
    if not conv.draft_id:
        defaults = empty_payload()
        for key in _PREVIOUS_LISTING_KEYS:
            payload[key] = defaults.get(key)
    _write_payload(conv, payload)
    if not conv.draft_id:
        return ""
    if not (before or card_submitted(db, conv) or payload.get("listing_edit_mode")):
        return ""
    has_data = bool(payload.get("brand") or payload.get("model") or payload.get("awaiting_confirm"))
    start_new_listing(db, conv, {}, [])
    if before and has_data and after:
        return "\n\n🆕 Pichle customer ka adhoora card band kar diya. Is customer ki listing details + photos bhejein."
    return ""


def last_listing_allowed(db: Session, conv: AiConversation, draft) -> bool:
    """Office line may only reopen a card made for the currently selected customer."""
    mobile = _digits10(conv.mobile)
    if _get_session(db, mobile) is None and not _reported_office(db, conv):
        return True
    row = _get_session(db, mobile)
    if not active_target(row):
        return False
    floor = _payload(conv).get("office_draft_floor")
    if floor in (None, ""):
        return False
    try:
        if int(draft.id) <= int(floor):
            return False
    except (TypeError, ValueError):
        return False
    from .confirm import _SUBMITTED

    confirmed = str(getattr(draft, "confirmed_json", "") or "").strip()
    return (draft.status or "").upper() in _SUBMITTED or confirmed not in ("", "{}", "null")


def _listing_title_bits(payload: dict, draft=None) -> str:
    bits = [
        str(payload.get("brand") or "").strip(),
        str(payload.get("model") or "").strip(),
        str(payload.get("year") or "").strip(),
    ]
    title = " ".join(b for b in bits if b)
    if title:
        return title[:60]
    if draft is not None:
        return str(getattr(draft, "title", "") or getattr(draft, "card_id", "") or "")[:60]
    return str(payload.get("office_last_live_title") or "previous listing")[:60]


# Office push often keeps draft=READY_FOR_REVIEW / payload=PENDING_REVIEW until the
# approve webhook — but the tip already has a listing id (+ often a live URL).
_CAPTURE_STATUSES = _LIVE_STATUSES | {
    "PENDING_REVIEW",
    "READY_FOR_REVIEW",
    "UNDER_REVIEW",
    "ADMIN_ACKNOWLEDGED",
    "SUBMITTED",
}


def _capture_office_last_live(db: Session, conv: AiConversation) -> bool:
    """Remember the live listing so the next photo/detail can update it or start a new card."""
    payload = _payload(conv)
    if payload.get("office_last_live_listing_id") or payload.get("office_last_live_draft_id"):
        payload["office_ready_for_next"] = True
        _write_payload(conv, payload)
        return True
    from ..models import AiListingDraft

    draft = db.get(AiListingDraft, conv.draft_id) if conv.draft_id else None
    status_draft = (draft.status or "").upper() if draft else ""
    status_pl = str(payload.get("listing_status") or "").upper()
    lid = str(payload.get("infradealer_listing_id") or payload.get("posted_product_id") or "").strip()
    if not lid:
        return False
    status_ok = status_draft in _CAPTURE_STATUSES or status_pl in _CAPTURE_STATUSES
    if not status_ok and not str(payload.get("listing_url") or "").strip():
        return False
    payload["office_last_live_listing_id"] = lid
    payload["office_last_live_url"] = str(payload.get("listing_url") or "").strip()
    if draft is not None:
        payload["office_last_live_draft_id"] = int(draft.id)
        payload["office_last_live_card_id"] = str(draft.card_id or payload.get("active_card_id") or "")
    elif conv.draft_id:
        payload["office_last_live_draft_id"] = int(conv.draft_id)
        payload["office_last_live_card_id"] = str(payload.get("active_card_id") or "")
    else:
        payload["office_last_live_card_id"] = str(payload.get("active_card_id") or "")
    payload["office_last_live_title"] = _listing_title_bits(payload, draft)
    payload["office_ready_for_next"] = True
    _write_payload(conv, payload)
    return True


def _restore_office_keys(payload: dict, saved: dict) -> None:
    for key in _OFFICE_LAST_KEYS:
        val = saved.get(key)
        if val not in (None, "", [], {}):
            payload[key] = val


def _clear_office_next_flags(payload: dict, *, keep_last: bool = False) -> None:
    payload["awaiting_office_next_action"] = False
    payload["office_ready_for_next"] = False
    payload["office_pending_text"] = ""
    payload["office_held_media_ids"] = []
    payload["office_send_choice_buttons"] = False
    payload["office_button_set"] = None
    payload["office_selected_next"] = None
    payload["office_confirm_nonce"] = None
    payload.pop("office_replay_text", None)
    if keep_last:
        return
    for key in (
        "office_last_live_listing_id",
        "office_last_live_url",
        "office_last_live_draft_id",
        "office_last_live_card_id",
        "office_last_live_title",
    ):
        payload[key] = None


def _close_posted_card(db: Session, conv: AiConversation, *, keep_awaiting: bool = False) -> None:
    """A live card is finished: the next message starts clean instead of re-confirming it."""
    payload = _payload(conv)
    if payload.get("listing_edit_mode"):
        return
    if payload.get("awaiting_office_next_action") and not keep_awaiting:
        # Choice pending — do not wipe the ask/context mid-flight.
        return
    if not conv.draft_id or _known_operator(db, conv) is None:
        return
    from ..models import AiListingDraft
    from .cards import clear_card_chat_data

    draft = db.get(AiListingDraft, conv.draft_id)
    if draft is None or (draft.status or "").upper() not in _LIVE_STATUSES:
        return
    _capture_office_last_live(db, conv)
    payload = _payload(conv)
    floor = payload.get("office_draft_floor")
    saved = {k: payload.get(k) for k in _OFFICE_LAST_KEYS}
    if keep_awaiting:
        saved["awaiting_office_next_action"] = True
        saved["office_ready_for_next"] = True
    clear_card_chat_data(db, conv, draft)
    payload = _payload(conv)
    _restore_office_keys(payload, saved)
    payload["office_ready_for_next"] = True
    # Do NOT keep live listing_id/url on the active chat — else "New" becomes a
    # place edit and sync_posted_product patches the previous listing.
    payload["infradealer_listing_id"] = None
    payload["posted_product_id"] = None
    payload["listing_url"] = None
    payload["listing_status"] = None
    payload["summary_json"] = None
    payload["confirmed_json"] = None
    if floor not in (None, ""):
        payload["office_draft_floor"] = floor
    _write_payload(conv, payload)


def should_hold_media_after_live(db: Session, conv: AiConversation) -> bool:
    """Office: after a live URL, hold photos until UPDATE vs NEW is chosen."""
    try:
        if _known_operator(db, conv) is None:
            return False
    except Exception:
        return False
    payload = _payload(conv)
    if payload.get("listing_edit_mode"):
        return False
    if payload.get("awaiting_office_next_action") or payload.get("office_ready_for_next"):
        return True
    if not conv.draft_id:
        return False
    from ..models import AiListingDraft

    draft = db.get(AiListingDraft, conv.draft_id)
    return bool(draft and (draft.status or "").upper() in _LIVE_STATUSES)


def _looks_like_listing_followup(text: str, media_note: str = "") -> bool:
    note = media_note or ""
    if re.search(r"\b(?:image|photo|video)\b|id=\d+", note, re.I):
        return True
    msg = (text or "").strip()
    if not msg or _GREETING.match(msg):
        return False
    if (
        _CHOICE_UPDATE.match(msg)
        or _CHOICE_NEW.match(msg)
        or _CHOICE_NEW_ACCOUNT.match(msg)
        or _CHOICE_CONFIRM.match(msg)
        or _CHOICE_CANCEL.match(msg)
    ):
        return False
    if msg.upper() in {"NEW", "UPDATE", "NEW_ACCOUNT", "CONFIRM", "CANCEL", "CHANGE"}:
        return False
    if _NEW_LISTING.search(msg) and not _LISTING_SIGNAL.search(msg) and not _VEHICLE_WORD.search(msg):
        return False
    if _DETAIL.search(msg) and _short(msg) and not _DETAIL_SKIP.search(msg):
        return False
    if _LISTING_SIGNAL.search(msg) or _VEHICLE_WORD.search(msg):
        return True
    if len(msg) > 40 and not _PHONE_IN_TEXT.search(msg):
        return True
    return False


def _office_next_ask(payload: dict) -> str:
    title = str(payload.get("office_last_live_title") or "previous listing").strip() or "previous listing"
    url = str(payload.get("office_last_live_url") or "").strip()
    link = f"\n🔗 {url}" if url else ""
    return (
        f"*Aage kya karna hai?* Ek option select karein:{link}\n\n"
        f"1. 🟢 *Nayi Listing* — Same account se another listing.\n"
        f"2. 🟡 *Update Previous Listing* — Existing listing ({title}) edit/update.\n"
        f"3. 🔵 *New Account + New Listing* — Naye customer se post.\n\n"
        "Neeche *Select option* dabake ek choice choose karein.\n"
        "Uske baad Confirm maanga jayega."
    )


def _office_confirm_ask(db: Session, conv: AiConversation, payload: dict | None = None) -> str:
    pl = payload if isinstance(payload, dict) else _payload(conv)
    choice = str(pl.get("office_selected_next") or "").upper()
    short = _OFFICE_CHOICE_SHORT.get(choice) or choice or "-"
    who = _selected_who(db, conv)
    return (
        f"You selected: *{short}*\n"
        f"Selected Account: *{who}*\n\n"
        "Are you sure you want to continue?\n"
        "Neeche *Confirm* ya *Cancel* dabayein."
    )


def _set_office_button_set(payload: dict, kind: str) -> None:
    """kind: pick | confirm"""
    payload["office_button_set"] = kind
    payload["office_send_choice_buttons"] = True  # runner still checks this


def arm_office_next_choice(db: Session, conv: AiConversation) -> None:
    """After a live URL: remember the listing and ask via interactive list next send.

    Always arms the pick UI for office operators — even when draft status is still
    READY_FOR_REVIEW / PENDING_REVIEW (capture is best-effort).
    """
    if _known_operator(db, conv) is None:
        return
    captured = _capture_office_last_live(db, conv)
    payload = _payload(conv)
    if not captured:
        lid = str(
            payload.get("infradealer_listing_id") or payload.get("posted_product_id") or ""
        ).strip()
        if lid:
            payload["office_last_live_listing_id"] = lid
            payload["office_last_live_url"] = str(payload.get("listing_url") or "").strip()
            if conv.draft_id and not payload.get("office_last_live_draft_id"):
                payload["office_last_live_draft_id"] = int(conv.draft_id)
            if not payload.get("office_last_live_title"):
                payload["office_last_live_title"] = _listing_title_bits(payload, None)
        else:
            log.warning(
                "office_mode: arm next-choice without listing id conv=%s",
                getattr(conv, "id", None),
            )
    payload["awaiting_office_next_action"] = True
    payload["office_ready_for_next"] = True
    payload["office_selected_next"] = None
    payload["office_confirm_nonce"] = None
    _set_office_button_set(payload, "pick")
    _write_payload(conv, payload)
    log.info(
        "office_mode: armed pick UI conv=%s lid=%s",
        getattr(conv, "id", None),
        payload.get("office_last_live_listing_id") or "-",
    )


def office_choice_buttons_pending(conv: AiConversation) -> bool:
    pl = _payload(conv)
    return bool(pl.get("office_send_choice_buttons") or pl.get("office_button_set"))


def take_office_interactive_ui(conv: AiConversation) -> dict:
    """Consume one-shot UI flag. Returns {mode: list|buttons, rows/buttons, ...}."""
    payload = _payload(conv)
    kind = str(payload.get("office_button_set") or "").strip().lower()
    if not payload.get("office_send_choice_buttons") and not kind:
        return {}
    payload["office_send_choice_buttons"] = False
    payload["office_button_set"] = None
    _write_payload(conv, payload)
    if kind == "confirm":
        return {"mode": "buttons", "buttons": [dict(b) for b in OFFICE_CONFIRM_BUTTONS]}
    return {
        "mode": "list",
        "button_label": "Select option",
        "section_title": "Next action",
        "rows": [dict(r) for r in OFFICE_LIST_ROWS],
        "fallback_buttons": [dict(b) for b in OFFICE_CHOICE_BUTTONS],
    }


def take_office_choice_buttons(conv: AiConversation) -> list[dict]:
    """Backward-compatible: confirm buttons, or pick reply-button fallback."""
    ui = take_office_interactive_ui(conv)
    if not ui:
        return []
    if ui.get("mode") == "buttons":
        return list(ui.get("buttons") or [])
    return list(ui.get("fallback_buttons") or OFFICE_CHOICE_BUTTONS)


def normalize_office_choice_text(text: str) -> str:
    """Map WhatsApp button/list id/title to a stable choice token."""
    msg = (text or "").strip()
    low = msg.lower()
    if low in {OFFICE_BTN_CONFIRM, "confirm"}:
        return "CONFIRM"
    if low in {OFFICE_BTN_CANCEL, OFFICE_BTN_CHANGE, "cancel", "change"}:
        return "CANCEL"
    if low in {OFFICE_BTN_NEW_SAME, "nayi listing", "nayi listing same"}:
        return "NEW"
    if low in {
        OFFICE_BTN_UPDATE,
        "update purani",
        "update listing",
        "update previous",
        "update previous listing",
        "update",
    }:
        return "UPDATE"
    if low in {OFFICE_BTN_NEW_ACCOUNT, "naya account", "new account", "new account + new listing"}:
        return "NEW_ACCOUNT"
    return msg


def _stage_office_selection(db: Session, conv: AiConversation, choice: str) -> str:
    """Store the selection; wait for Confirm before running the action."""
    import time

    payload = _payload(conv)
    key = str(choice or "").upper()
    lid = str(payload.get("office_last_live_listing_id") or "").strip()
    payload["office_selected_next"] = key
    payload["awaiting_office_next_action"] = True
    payload["office_ready_for_next"] = True
    payload["office_confirm_nonce"] = f"{key}:{lid}:{int(time.time())}:{conv.id}"
    _set_office_button_set(payload, "confirm")
    _write_payload(conv, payload)
    return _office_confirm_ask(db, conv, payload)


def _cancel_office_selection(db: Session, conv: AiConversation) -> str:
    payload = _payload(conv)
    _clear_office_next_flags(payload, keep_last=True)
    _write_payload(conv, payload)
    return (
        "❌ Cancelled. Koi action nahi hui.\n"
        "Listing waise hi live hai. Nayi photo/detail bhejoge to options phir milenge."
    )


def _office_selection_expired(payload: dict) -> bool:
    return not (
        payload.get("office_last_live_listing_id")
        or payload.get("office_last_live_draft_id")
    )


def _validate_office_execute(db: Session, conv: AiConversation, choice: str) -> str | None:
    """Return an error message if the pending action must not run."""
    payload = _payload(conv)
    key = str(choice or "").upper()
    if _office_selection_expired(payload) and key in {"UPDATE", "NEW"}:
        if key == "UPDATE":
            return (
                "⚠️ Session expire / listing context missing.\n"
                "Update nahi ho sakta. Nayi listing ke liye phir se post karke options use karein."
            )
    if key == "UPDATE":
        lid = str(payload.get("office_last_live_listing_id") or "").strip()
        draft_id = payload.get("office_last_live_draft_id")
        if not lid and draft_id in (None, ""):
            return "⚠️ Purani listing ID nahi mili. Update cancel."
        if draft_id not in (None, ""):
            from ..models import AiListingDraft

            draft = db.get(AiListingDraft, int(draft_id))
            if draft is None or (draft.mobile or "") != (conv.mobile or ""):
                return "⚠️ Listing is operator session se match nahi karti. Update cancel."
    if key in {"NEW", "UPDATE"} and _known_operator(db, conv) is not None:
        if not active_target(_known_operator(db, conv)):
            return (
                "⚠️ Customer select nahi hai.\n"
                "Pehle customer number bhejein: customer 98XXXXXXXX"
            )
    return None


def _execute_office_selection(db: Session, conv: AiConversation, choice: str, pending: str = "") -> str | None:
    payload = _payload(conv)
    key = str(choice or "").upper()
    nonce = str(payload.get("office_confirm_nonce") or "").strip()
    if nonce and str(payload.get("office_executed_nonce") or "") == nonce:
        return "✅ Ye action pehle hi complete ho chuki hai. Duplicate skip."

    err = _validate_office_execute(db, conv, key)
    if err:
        _clear_office_next_flags(payload, keep_last=True)
        _write_payload(conv, payload)
        return err

    if key == "NEW_ACCOUNT":
        out = _office_start_new_account(db, conv)
    elif key == "NEW":
        out = _office_start_new_after_live(db, conv, pending)
    elif key == "UPDATE":
        out = _office_resume_update_after_live(db, conv, pending)
    else:
        _set_office_button_set(payload, "pick")
        _write_payload(conv, payload)
        return _office_next_ask(payload)

    if nonce:
        pl = _payload(conv)
        pl["office_executed_nonce"] = nonce
        pl["office_confirm_nonce"] = None
        _write_payload(conv, pl)
    return out


def _bind_held_office_media(db: Session, conv: AiConversation) -> None:
    from .cards import MAX_PHOTOS
    from ..models import AiMedia

    if not conv.draft_id:
        return
    payload = _payload(conv)
    held = [int(x) for x in (payload.get("office_held_media_ids") or []) if str(x).isdigit() or isinstance(x, int)]
    rows = []
    if held:
        rows = (
            db.query(AiMedia)
            .filter(AiMedia.id.in_(held[-MAX_PHOTOS:]), AiMedia.conversation_id == conv.id)
            .order_by(AiMedia.id.asc())
            .all()
        )
    if not rows:
        rows = (
            db.query(AiMedia)
            .filter(
                AiMedia.conversation_id == conv.id,
                AiMedia.draft_id.is_(None),
                AiMedia.kind.in_(["image", "photo", "video"]),
            )
            .order_by(AiMedia.id.desc())
            .limit(MAX_PHOTOS)
            .all()
        )
        rows = list(reversed(rows))
    ids = []
    for row in rows:
        row.draft_id = conv.draft_id
        ids.append(row.id)
    if ids:
        payload["media_ids"] = list(dict.fromkeys([*(payload.get("media_ids") or []), *ids]))[:MAX_PHOTOS]
        payload["office_held_media_ids"] = []
        _write_payload(conv, payload)


def _resume_office_last_for_update(db: Session, conv: AiConversation):
    from ..models import AiListingDraft
    from .session_memory import resume_last_listing_for_update

    payload = _payload(conv)
    lid = str(payload.get("office_last_live_listing_id") or "").strip()
    url = str(payload.get("office_last_live_url") or "").strip()
    title = str(payload.get("office_last_live_title") or "").strip()
    draft_id = payload.get("office_last_live_draft_id")
    card_id = str(payload.get("office_last_live_card_id") or "").strip()
    draft = db.get(AiListingDraft, int(draft_id)) if draft_id not in (None, "") else None
    if draft is None:
        draft = resume_last_listing_for_update(db, conv)
    else:
        try:
            if conv.draft_id and conv.draft_id != draft.id:
                from .cards import persist_active_card_session

                persist_active_card_session(db, conv)
        except Exception:
            pass
        from .cards import hydrate_card_session
        import json

        conv.draft_id = draft.id
        hydrate_card_session(db, conv, draft)
        payload = _payload(conv)
        confirmed = {}
        try:
            confirmed = json.loads(draft.confirmed_json or "{}")
            if not isinstance(confirmed, dict):
                confirmed = {}
        except Exception:
            confirmed = {}
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
    payload = _payload(conv)
    if lid:
        payload["infradealer_listing_id"] = lid
        payload["posted_product_id"] = lid
        payload["office_last_live_listing_id"] = lid
    if url:
        payload["listing_url"] = url
        payload["office_last_live_url"] = url
    if title:
        payload["office_last_live_title"] = title
    if draft is not None:
        payload["office_last_live_draft_id"] = draft.id
        payload["office_last_live_card_id"] = draft.card_id or card_id
    payload["listing_status"] = payload.get("listing_status") or "LIVE"
    payload["listing_edit_mode"] = True
    _write_payload(conv, payload)
    return draft


def _selected_who(db: Session, conv: AiConversation) -> str:
    row = _known_operator(db, conv)
    target = active_target(row) if row is not None else None
    return _post_to_label(target or {}) if target else "selected customer"


def _office_start_new_after_live(db: Session, conv: AiConversation, pending_text: str = "") -> str | None:
    from .confirm import start_new_listing

    who = _selected_who(db, conv)
    start_new_listing(db, conv, {"intent": "SELL"}, [])
    payload = _payload(conv)
    _clear_office_next_flags(payload)
    if pending_text:
        payload["office_replay_text"] = pending_text
        _write_payload(conv, payload)
        _bind_held_office_media(db, conv)
        return None
    _write_payload(conv, payload)
    _bind_held_office_media(db, conv)
    return (
        f"✅ Same account (*{who}*) pe nayi listing shuru.\n"
        "Is gaadi ki photos + details bhejein."
    )


def _office_resume_update_after_live(db: Session, conv: AiConversation, pending_text: str = "") -> str | None:
    draft = _resume_office_last_for_update(db, conv)
    payload = _payload(conv)
    held = list(payload.get("office_held_media_ids") or [])
    pending = (pending_text or "").strip()
    title = str(payload.get("office_last_live_title") or "purani listing").strip()
    _clear_office_next_flags(payload, keep_last=True)
    payload["office_held_media_ids"] = held
    payload["listing_edit_mode"] = True
    if pending:
        payload["office_replay_text"] = pending
    _write_payload(conv, payload)
    _bind_held_office_media(db, conv)
    if draft is None and not pending:
        return "Purani listing nahi mili. *Nayi listing* button dabayein."
    if pending:
        return None
    url = str(payload.get("listing_url") or payload.get("office_last_live_url") or "").strip()
    return (
        f"✅ Purani listing edit mode: *{title}*"
        + (f"\n🔗 {url}" if url else "")
        + "\nJo detail/photo change karni hai woh bhejein."
    )


def _office_start_new_account(db: Session, conv: AiConversation) -> str:
    from .confirm import start_new_listing

    row = _known_operator(db, conv)
    if row is not None:
        _clear_target(row)
        row.step = ""
        row.pending_phone = ""
        _touch(row)
    try:
        start_new_listing(db, conv, {}, [])
    except Exception:
        log.exception("office_mode: start_new_listing on new-account choice failed")
    payload = _payload(conv)
    _clear_office_next_flags(payload)
    _write_payload(conv, payload)
    return (
        "✅ Theek hai — naya account + nayi listing.\n"
        "Customer ka mobile number bhejein:\n"
        "customer 98XXXXXXXX"
    )


def _resolve_office_next_action(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str | None:
    payload = _payload(conv)
    raw = (text or "").strip()
    msg = normalize_office_choice_text(raw)
    pending = str(payload.get("office_pending_text") or "").strip()

    # Different customer number → drop the ask and let select flow run.
    if _customer_phone_in_text(raw, _digits10(conv.mobile)):
        _clear_office_next_flags(payload)
        _write_payload(conv, payload)
        return None

    def _choice_new(m: str) -> bool:
        if m.upper() == "NEW" or _CHOICE_NEW.match(m):
            return True
        if not _NEW_LISTING.search(m) or _UPDATE_PREV.search(m):
            return False
        return not (_LISTING_SIGNAL.search(m) or _VEHICLE_WORD.search(m)) and len(m.split()) <= 10

    def _choice_update(m: str) -> bool:
        if m.upper() == "UPDATE" or _CHOICE_UPDATE.match(m):
            return True
        if not _UPDATE_PREV.search(m) or _NEW_LISTING.search(m):
            return False
        return not (_LISTING_SIGNAL.search(m) or _VEHICLE_WORD.search(m)) and len(m.split()) <= 10

    def _choice_new_account(m: str) -> bool:
        return m.upper() == "NEW_ACCOUNT" or bool(_CHOICE_NEW_ACCOUNT.match(m))

    # Confirm / Cancel (second step — clickable reply buttons).
    if msg.upper() == "CONFIRM" or _CHOICE_CONFIRM.match(msg):
        selected = str(payload.get("office_selected_next") or "").upper()
        if not selected:
            _set_office_button_set(payload, "pick")
            _write_payload(conv, payload)
            return "Pehle ek option select karein, phir *Confirm* dabayein.\n\n" + _office_next_ask(payload)
        return _execute_office_selection(db, conv, selected, pending)

    if msg.upper() == "CANCEL" or _CHOICE_CANCEL.match(msg):
        return _cancel_office_selection(db, conv)

    # First step: selecting an option only stages it — Confirm runs the action.
    if _choice_new_account(msg):
        return _stage_office_selection(db, conv, "NEW_ACCOUNT")
    if _choice_new(msg):
        return _stage_office_selection(db, conv, "NEW")
    if _choice_update(msg):
        return _stage_office_selection(db, conv, "UPDATE")

    # More photos while waiting — keep buffering and re-ask pick/confirm.
    if _looks_like_listing_followup(raw, media_note):
        if raw and not (
            _CHOICE_UPDATE.match(raw)
            or _CHOICE_NEW.match(raw)
            or _CHOICE_NEW_ACCOUNT.match(raw)
            or _CHOICE_CONFIRM.match(raw)
            or _CHOICE_CANCEL.match(raw)
        ):
            payload["office_pending_text"] = ((pending + "\n" if pending else "") + raw).strip()[:2000]
            _write_payload(conv, payload)
        if payload.get("office_selected_next"):
            _set_office_button_set(payload, "confirm")
            _write_payload(conv, payload)
            return _office_confirm_ask(db, conv, payload) + "\n\n(Pehle Confirm dabayein.)"
        _set_office_button_set(payload, "pick")
        _write_payload(conv, payload)
        return _office_next_ask(payload)

    if not raw and media_note:
        if payload.get("office_selected_next"):
            _set_office_button_set(payload, "confirm")
            _write_payload(conv, payload)
            return _office_confirm_ask(db, conv, payload)
        _set_office_button_set(payload, "pick")
        _write_payload(conv, payload)
        return _office_next_ask(payload)

    # Free-typed text while awaiting — nudge back to interactive buttons (no action).
    if payload.get("office_selected_next"):
        _set_office_button_set(payload, "confirm")
        _write_payload(conv, payload)
        return (
            "Please neeche ke *Confirm* / *Cancel* buttons use karein.\n\n"
            + _office_confirm_ask(db, conv, payload)
        )
    _set_office_button_set(payload, "pick")
    _write_payload(conv, payload)
    return (
        "Please neeche *Select option* se ek choice choose karein "
        "(type mat karein).\n\n"
        + _office_next_ask(payload)
    )


def _maybe_offer_update_or_new(
    db: Session, conv: AiConversation, text: str, media_note: str = ""
) -> str | None:
    if _known_operator(db, conv) is None:
        return None
    payload = _payload(conv)
    if payload.get("listing_edit_mode"):
        return None
    if payload.get("awaiting_office_next_action"):
        return _resolve_office_next_action(db, conv, text, media_note)

    raw = (text or "").strip()
    msg = normalize_office_choice_text(raw)
    # Explicit NEW / UPDATE / new-account with a remembered live card → stage, then Confirm.
    if payload.get("office_ready_for_next") and (
        payload.get("office_last_live_listing_id") or payload.get("office_last_live_draft_id")
    ):
        if msg.upper() == "CONFIRM" or _CHOICE_CONFIRM.match(msg):
            selected = str(payload.get("office_selected_next") or "").upper()
            if selected:
                return _execute_office_selection(db, conv, selected, "")
        if msg.upper() == "CANCEL" or _CHOICE_CANCEL.match(msg):
            return _cancel_office_selection(db, conv)
        if msg.upper() == "NEW_ACCOUNT" or _CHOICE_NEW_ACCOUNT.match(msg):
            payload["awaiting_office_next_action"] = True
            _write_payload(conv, payload)
            return _stage_office_selection(db, conv, "NEW_ACCOUNT")
        if msg.upper() == "NEW" or _CHOICE_NEW.match(msg) or (
            _NEW_LISTING.search(msg) and not _LISTING_SIGNAL.search(msg) and not media_note
        ):
            payload["awaiting_office_next_action"] = True
            _write_payload(conv, payload)
            return _stage_office_selection(db, conv, "NEW")
        if msg.upper() == "UPDATE" or _CHOICE_UPDATE.match(msg) or (
            _UPDATE_PREV.search(msg) and not _NEW_LISTING.search(msg) and not _LISTING_SIGNAL.search(msg) and not media_note
        ):
            payload["awaiting_office_next_action"] = True
            _write_payload(conv, payload)
            return _stage_office_selection(db, conv, "UPDATE")

    if not _looks_like_listing_followup(text, media_note):
        return None
    # New customer number in the same message → select flow, not update/new ask.
    if _customer_phone_in_text(raw, _digits10(conv.mobile)) and not _SAME_WORD.search(raw):
        return None
    if not _capture_office_last_live(db, conv):
        return None

    payload = _payload(conv)
    payload["awaiting_office_next_action"] = True
    payload["office_ready_for_next"] = True
    payload["office_selected_next"] = None
    _set_office_button_set(payload, "pick")
    if raw:
        payload["office_pending_text"] = raw[:2000]
    _write_payload(conv, payload)
    try:
        _close_posted_card(db, conv, keep_awaiting=True)
    except Exception:
        log.exception("office_mode: soft-close before update/new ask failed")
    payload = _payload(conv)
    payload["awaiting_office_next_action"] = True
    payload["office_ready_for_next"] = True
    if raw and not payload.get("office_pending_text"):
        payload["office_pending_text"] = raw[:2000]
    _set_office_button_set(payload, "pick")
    _write_payload(conv, payload)
    return _office_next_ask(payload)


def _wants_account_switch(msg: str) -> bool:
    """"account change karke dusre number se dalna hai" — no number given yet."""
    if _PHONE_IN_TEXT.search(msg) or _LISTING_SIGNAL.search(msg) or _SAME_WORD.search(msg):
        return False
    if len(msg.split()) > 25:
        return False
    return bool(_SWITCH_WORD.search(msg) and (_ACCOUNT_WORD.search(msg) or _NUMBER_WORD.search(msg)))


def handle_office_turn(
    db: Session, conv: AiConversation, text: str, lang: str = "", media_note: str = ""
) -> str | None:
    """Reply for office commands / guards, or None to continue the normal flow."""
    mobile = _digits10(conv.mobile)
    text = normalize_office_choice_text(text)
    try:
        offer = _maybe_offer_update_or_new(db, conv, text, media_note)
        if offer is not None:
            return offer
        # Choice resolved with buffered details → skip auto-close so edit/new card stays.
        if not _payload(conv).get("office_replay_text"):
            _close_posted_card(db, conv)
        # Bare "New"/"UPDATE" right after live URL (close just armed office_ready).
        pl = _payload(conv)
        if pl.get("office_ready_for_next") and (
            pl.get("office_last_live_listing_id") or pl.get("office_last_live_draft_id")
        ):
            offer2 = _maybe_offer_update_or_new(db, conv, text, media_note)
            if offer2 is not None:
                return offer2
            if pl.get("office_replay_text"):
                return None
    except Exception:
        log.exception("office_mode: closing posted card / update-new ask failed")
    before = _target_id(db, mobile)
    reply = _handle_office_turn(db, conv, text, lang)
    if reply and mobile:
        after = _target_id(db, mobile)
        if after != before:
            try:
                reply += _on_target_change(db, conv, mobile, before, after)
            except Exception:
                log.exception("office_mode: card reset on customer change failed")
    return reply


def _handle_office_turn(db: Session, conv: AiConversation, text: str, lang: str = "") -> str | None:
    msg = (text or "").strip()
    if not msg:
        return None
    if not signature_enforced(db):
        return _office_disabled_notice(conv, msg)
    mobile = _digits10(conv.mobile)
    if not mobile or _denied_recently(mobile):
        return None

    m_create = _CREATE.match(msg)
    if m_create:
        rest = m_create.group(1)
        found = _PHONE_IN_TEXT.search(rest)
        phone = _phone_from(found.group(0)) if found else ""
        if phone:
            name = _PHONE_IN_TEXT.sub(" ", rest).strip(" ,.:-")
            return _cmd_create_start(db, mobile, phone, name)
        return None

    m_cust = _CUSTOMER.match(msg)
    if m_cust:
        rest = m_cust.group(1)
        phone = _phone_from(rest)
        if phone:
            return _cmd_select(db, mobile, phone)
        if _CHANGE.match(rest) or _CLEAR.match(rest) or _STATUS.match(rest):
            row = operator_session(db, conv)
            if not row:
                return None
            if _STATUS.match(rest):
                target = active_target(row)
                if not target:
                    return "Abhi koi customer select nahi hai.\n" + _select_hint()
                return "📌 Selected customer\n" + _customer_card(target)
            _clear_target(row)
            row.step = ""
            row.pending_phone = ""
            if _CHANGE.match(rest):
                return "Customer hata diya. Naya customer select karein:\ncustomer 98XXXXXXXX"
            return "Customer selection clear. Listing post karne se pehle customer select karein.\n" + _select_hint()
        return None

    row = _get_session(db, mobile)
    if row is not None and row.step == "await_name":
        row = operator_session(db, conv)
        if row is not None and row.step == "await_name":
            if _CANCEL.match(msg) or _NAME_OK.match(re.sub(r"\s+", " ", msg)):
                return _cmd_create_finish(db, row, mobile, msg)
            row.step = ""
            row.pending_phone = ""
            _touch(row)

    natural = _natural_select(db, conv, mobile, msg)
    if natural:
        return natural

    if _wants_account_switch(msg):
        row = _known_operator(db, conv)
        if row is not None:
            _clear_target(row)
            row.step = ""
            row.pending_phone = ""
            _touch(row)
            return (
                "Theek hai, customer hata diya.\n"
                "Jis customer ke account par listing daalni hai uska mobile number bhejein:\n"
                "customer 98XXXXXXXX"
            )

    if _GREETING.match(msg):
        row = _known_operator(db, conv)
        if row is not None:
            return _office_menu(active_target(row))
        return None

    if _DETAIL.search(msg) and _short(msg) and not (_LISTING_SIGNAL.search(msg) or _DETAIL_SKIP.search(msg)):
        from .vision import card_has_photos, wants_card_details

        if wants_card_details(msg) and card_has_photos(db, conv):
            return None
        row = _known_operator(db, conv)
        if row is not None:
            return _cmd_details(db, row, mobile)
        return None

    if _payload(conv).get("awaiting_confirm"):
        from .confirm import _POST_CONFIRM, is_yes

        if is_yes(msg) or _POST_CONFIRM.search(msg):
            row = _known_operator(db, conv)
            if row is not None and not active_target(row):
                return "⚠️ Listing kis customer ke account par post karni hai?\n" + _select_hint()
    return None


def _known_operator(db: Session, conv: AiConversation) -> AiOfficeSession | None:
    """Existing session, or verify once when the backend reported account_type=office."""
    if not signature_enforced(db):
        return None
    mobile = _digits10(conv.mobile)
    if not mobile or _denied_recently(mobile):
        return None
    row = _get_session(db, mobile)
    if row is not None:
        return row
    if not _reported_office(db, conv):
        return None
    return operator_session(db, conv)


def _reported_office(db: Session, conv: AiConversation) -> bool:
    """Backend said account_type=office (conversation payload or cached account state)."""
    if str(_payload(conv).get("account_type") or "").lower() == "office":
        return True
    try:
        from .account_filter import read_account_details

        remote = read_account_details(db, conv.mobile).remote or {}
        return str(remote.get("account_type") or "").lower() == "office"
    except Exception:
        log.exception("office_mode: account state read failed")
        return False


def is_office_session(db: Session, conv: AiConversation) -> bool:
    """True when this sender is a verified office operator (skip same-number rule)."""
    try:
        return _known_operator(db, conv) is not None
    except Exception:
        log.exception("office_mode: session check failed")
        return False


_COOLDOWN_LINE = re.compile(r"^.*(?:\b10\s*(?:minute|minutes|min)\b|10\s*मिनट).*$\n?", re.I | re.M)


def _office_post_result(payload: dict, who: str) -> str:
    """Only claim "posted" when the website actually returned a listing for this card."""
    status = str(payload.get("listing_status") or "").upper()
    lid = str(payload.get("infradealer_listing_id") or "")
    if status == "REJECTED":
        reason = payload.get("rejection_reason") or "-"
        return (
            f"❌ Listing post nahi hui — website ne reject ki hai.\nKaran: {reason}\n\n"
            "Theek karke (photos / details) dobara bhejein, phir YES likhein."
        )
    if not lid or status not in {"POSTED", "PENDING_REVIEW", "LIVE", "APPROVED"}:
        return "⏳ Listing website ko bheji ja rahi hai — confirm hote hi link yahin bhej denge."
    link = payload.get("listing_url") or ""
    if not link:
        from .data_push import public_listing_url

        link = public_listing_url(lid)
    return (
        f"✅ Listing {who} ke account par post ho gayi (live)."
        + (f"\n🔗 {link}" if link else "")
        + "\n\n*Aage kya karna hai?* Ek option select karein:\n"
        "1. 🟢 *Nayi Listing* — Same account se another listing\n"
        "2. 🟡 *Update Previous Listing* — Existing listing edit/update\n"
        "3. 🔵 *New Account + New Listing* — Naye customer se post\n\n"
        "Neeche *Select option* se choose karein, phir *Confirm*."
        + f"\n\nSelected Account: {who}"
    )


def _post_to_label(target: dict) -> str:
    """Username / phone for Post to — never a bare generic 'customer'."""
    username = str(target.get("username") or "").strip()
    name = str(target.get("name") or "").strip()
    phone = str(target.get("phone") or "").strip()
    generic = {"", "-", "customer", "user", "grahak"}
    if username and username.lower() not in generic:
        return username
    if name and name.lower() not in generic and not re.fullmatch(r"customer\s*\d*", name, re.I):
        return name
    if phone:
        return phone
    return username or name or phone or "-"


def decorate_office_reply(db: Session, conv: AiConversation, reply: str) -> str:
    """Show which customer account a listing will post to at the confirm step."""
    if not reply:
        return reply
    try:
        operator = _known_operator(db, conv) if _COOLDOWN_LINE.search(reply) else None
        if operator is not None:
            # Office listings go live immediately — never say "team will review".
            # Office listings are approved on creation; a failed push never reaches this text.
            target = active_target(operator) or {}
            who = _post_to_label(target)
            reply = _office_post_result(_payload(conv), who)
            if "post ho gayi (live)" in reply:
                try:
                    arm_office_next_choice(db, conv)
                except Exception:
                    log.exception("office_mode: arm next choice after live failed")
    except Exception:
        log.exception("office_mode: cooldown strip failed")
    if "Post to:" in reply:
        return reply
    try:
        if not _payload(conv).get("awaiting_confirm"):
            return reply
        row = _known_operator(db, conv)
        if row is None:
            return reply
        target = active_target(row)
        if target:
            who = _post_to_label(target)
            return (
                f"{reply}\n\n📌 Post to: {who}, {target['phone'] or '-'}\n"
                "Confirm? YES/NO"
            )
        return f"{reply}\n\n⚠️ Customer select nahi hai.\n{_select_hint()}"
    except Exception:
        log.exception("office_mode: decorate failed")
        return reply


def office_listing_context(db: Session, conv: AiConversation, account_type: str = "") -> dict | None:
    """Office fields for listing.push, or None for normal customers.

    office_mode=False only ever downgrades: the backend still decides from its
    own allowlist whether the sender is an office operator.
    """
    mobile = _digits10(conv.mobile if conv else "")
    if not mobile:
        return None
    row = _get_session(db, mobile)
    if row is None and str(account_type or "").lower() not in {"office", "staff", "admin"}:
        return None
    if not signature_enforced(db):
        return {"office_mode": False, "target": None}
    return {"office_mode": True, "target": active_target(row)}
