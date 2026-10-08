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
_GREETING = re.compile(
    r"^\s*(?:hi+|hii+|hel+o+|helo|hey+|namaste|namaskar|ram\s*ram|menu|help|start|options?|sir)\s*[.!?]*\s*$",
    re.I,
)
_SHORT_WORDS = 14


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
        row.step = ""
        row.pending_phone = ""
        _touch(row)
        return (
            f"❌ Is mobile par koi account nahi hai: {phone}\n"
            f"Naya account banane ke liye bhejein: create customer {phone}"
        )
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


def _cmd_create_start(db: Session, mobile: str, phone: str) -> str | None:
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
    row.step = "await_name"
    row.pending_phone = phone
    _touch(row)
    return f"Naye customer ({phone}) ka naam bhejein.\nRokne ke liye: cancel"


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
    phone = row.pending_phone
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
    row = _ensure_session(db, mobile)
    row.step = "await_name"
    row.pending_phone = phone
    _touch(row)
    return (
        f"❌ Is mobile par koi account nahi hai: {phone}\n"
        "Naya account banane ke liye customer ka naam bhejein (bina OTP).\n"
        "Rokne ke liye: cancel"
    )


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

    payload = _payload(conv)
    payload["office_draft_floor"] = _last_draft_id(db, mobile)
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


def handle_office_turn(db: Session, conv: AiConversation, text: str, lang: str = "") -> str | None:
    """Reply for office commands / guards, or None to continue the normal flow."""
    mobile = _digits10(conv.mobile)
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
        phone = _phone_from(m_create.group(1))
        if phone:
            return _cmd_create_start(db, mobile, phone)
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
            return _cmd_create_finish(db, row, mobile, msg)

    natural = _natural_select(db, conv, mobile, msg)
    if natural:
        return natural

    if _GREETING.match(msg):
        row = _known_operator(db, conv)
        if row is not None:
            return _office_menu(active_target(row))
        return None

    if _DETAIL.search(msg) and _short(msg) and not (_LISTING_SIGNAL.search(msg) or _DETAIL_SKIP.search(msg)):
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


def decorate_office_reply(db: Session, conv: AiConversation, reply: str) -> str:
    """Show which customer account a listing will post to at the confirm step."""
    if not reply or "Post to:" in reply:
        return reply
    try:
        if not _payload(conv).get("awaiting_confirm"):
            return reply
        row = _known_operator(db, conv)
        if row is None:
            return reply
        target = active_target(row)
        if target:
            return (
                f"{reply}\n\n📌 Post to: {target['name'] or '-'}, {target['phone'] or '-'}\n"
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
