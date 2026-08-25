"""One-time InfraDealer account: OTP, password, broker vs user."""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models import AiConversation, InfraDealerAccountState, User
from ..services import hash_user_password
from .confirm import is_no, is_yes
from .extract import extract_role
from .i18n import t
from .tools import _payload, _write_payload, execute_tool

log = logging.getLogger("infradealer.ai.account")

SITE = "http://www.infradealer.com"
_PASS_SKIP = re.compile(r"\b(skip|baad me|nahi|password nahi)\b", re.I)
_HAS_ACCOUNT = re.compile(
    r"account.{0,40}(bana|banaya|hai|he|already|exist|बना)|"
    r"(pahle|pehle|already|pehle se|pahle se|पहले).{0,30}account|"
    r"\b(check (to|tow|karo|kar)|account check)\b|"
    r"मेरा.{0,20}(अकाउंट|account)|पहले से.{0,20}(अकाउंट|account)|अकाउंट.{0,16}बना",
    re.I,
)
_NEW_CHAT = re.compile(
    r"new chat|naya chat|nayi chat|start karo|chat start|reset chat|naya listing|"
    r"नई चैट|नया चैट|चैट स्टार्ट",
    re.I,
)
# Only wipe chat memory when user clearly means conversation — NOT website listing delete.
_CLEAR_CHAT = re.compile(
    r"\b("
    r"delete(\s+all)?(\s+previous)?\s+(conversation|chat|history|baat)"
    r"|clear(\s+all)?(\s+previous)?\s+(conversation|chat|history|baat)"
    r"|reset(\s+chat|\s+conversation)"
    r"|previous\s+(conversation|chat|baat|history)\s*(delete|clear|hata|mita)?"
    r"|(purani|pichhli|pehle\s+wali)\s+(baat|chat|conversation|history)\s*(delete|clear|hata|mita)?"
    r"|(conversation|chat|history)\s*(delete|clear|hata|mita)"
    r"|delete\s+conversation|clear\s+chat|chat\s+saaf|"
    r"डिलीट\s*(चैट|बात)|हटा\s*दो\s*(चैट|बात)|मिटा\s*दो\s*(चैट|बात)|पुरानी\s*(बात|चैट)\s*(हटा|मिटा|डिलीट)"
    r")\b",
    re.I,
)
_DELETE_LISTING = re.compile(
    r"(listing|post|ad|ads|website|site|infra|card).{0,48}(delete|hata|remove|mita|nikal)|"
    r"(delete|hata|remove|mita|nikal).{0,48}(listing|post|ad|website|site|card)|"
    r"(website|site)\s*se\s*(delete|hata|mita|nikal)|"
    r"listing\s*(hata|mita|delete)\s*(do|dena|karo|kar)?",
    re.I,
)
_ASK_LINK = re.compile(
    r"\b(link|url|permalink)\b|"
    r"(direct\s+link|live\s+link|listing\s+link|open\s+ho\s*ja)|"
    r"link\s*(do|bhejo|chahiye|dedo|de\s*do|bhej\s*do)",
    re.I,
)
_ASK_LAST_POST = re.compile(
    r"(last|pichhli|pichli|pehle|previous|akhir).{0,24}(post|listing|dali|bechi|card)|"
    r"(kya|kyaa)\s+(post|dali|becha)|"
    r"mene\s+(last\s+)?post|"
    r"meri\s+last\s+listing|"
    r"listing\s*(status|kahan)|"
    r"jo\s+(maine|mene)\s+(dali|post)",
    re.I,
)
_LISTING_HINT = re.compile(
    r"\b(bech|sell|buy|kharid|chahiye|tata|jcb|eicher|truck|tipper|price|rate|kimat|"
    r"lakh|model|year|photo|gadi|gaadi|listing|card-\d+)\b|"
    r"[6-9]\d{9}",
    re.I,
)


def says_has_account(text: str) -> bool:
    return bool(_HAS_ACCOUNT.search(text or ""))


def wants_new_chat(text: str) -> bool:
    return bool(_NEW_CHAT.search(text or ""))


def wants_clear_conversation(text: str) -> bool:
    """User wants AI chat/card memory wiped — not website listing delete."""
    msg = text or ""
    if wants_delete_listing(msg):
        return False
    return bool(_CLEAR_CHAT.search(msg))


def wants_delete_listing(text: str) -> bool:
    """User wants posted listing removed from InfraDealer website — not chat clear."""
    return bool(_DELETE_LISTING.search(text or ""))


def wants_listing_link(text: str) -> bool:
    return bool(_ASK_LINK.search(text or ""))


def wants_last_post(text: str) -> bool:
    return bool(_ASK_LAST_POST.search(text or ""))


# Explicit: create / open InfraDealer account from WhatsApp (OTP flow).
_CREATE_ACCOUNT = re.compile(
    r"("
    r"(naya|new|nayi)\s+(account|akount|accnt|अकाउंट|अकाउन्ट|खाता).{0,24}"
    r"(bana|ban\s*do|banao|banwa|create|kar)"
    r"|(account|akount|accnt|अकाउंट|अकाउन्ट|खाता).{0,28}"
    r"(bana\s*do|banao|banwa\s*do|banwao|create|signup|sign\s*up|open\s*kar)"
    r"|(create|make|start|open).{0,20}(new\s+)?(account|akount)"
    r"|account\s*(chahiye|banana|banwana|bana\s*do)"
    r"|signup|sign\s*up|register\s*(karo|kardo|kar\s*do)?"
    r")",
    re.I,
)
# Short follow-ups right after "account not found" (e.g. "Naya bana do")
_CREATE_ACCOUNT_SHORT = re.compile(
    r"^(naya|new|nayi)?\s*(bana|ban|banwa)\s*do\.?$"
    r"|^(create|banao|banwao)\s*(do|karo)?\.?$"
    r"|^(haan|han|yes|ji)\s*,?\s*(bana|ban|create).{0,12}$",
    re.I,
)


def _wa_unmatched_payload(payload: dict | None) -> bool:
    pl = payload or {}
    if pl.get("wa_account_matched") is True:
        return False
    if pl.get("wa_account_matched") is False:
        return True
    reason = str(pl.get("account_reason") or "").upper()
    if reason in {"ACCOUNT_NOT_FOUND", "NOT_FOUND"}:
        return True
    atype = str(pl.get("account_type") or "").upper()
    if atype in {"MISSING", "ACCOUNT_TYPE_UNKNOWN", ""} and not pl.get("account_onboarded"):
        if not pl.get("profile_id") and not pl.get("infradealer_user_id"):
            return True
    return False


def wants_create_account(text: str, payload: dict | None = None) -> bool:
    """User wants WhatsApp OTP account creation — not website-only redirect."""
    msg = (text or "").strip()
    if not msg:
        return False
    if wants_delete_listing(msg):
        return False
    if _CREATE_ACCOUNT.search(msg):
        return True
    # After mismatch warning, short "naya bana do" means create account here
    if _wa_unmatched_payload(payload) and _CREATE_ACCOUNT_SHORT.search(msg):
        return True
    return False


def account_gate_fact(lang: str, payload: dict | None = None) -> str:
    """Canonical corrected mismatch / eligibility fact (backend truth)."""
    pl = payload or {}
    if _wa_unmatched_payload(pl):
        return t(lang, "account_wa_not_matched")
    reason = str(pl.get("account_reason") or "").upper()
    if reason in {"TOKEN_NO_CREDITS", "TOKEN_INSUFFICIENT"}:
        link = str(pl.get("account_buy_link") or "https://infradealer.com/wallet")
        return t(lang, "tokens_buy", link=link)
    return t(lang, "account_not_eligible")


def needs_account_gate(payload: dict | None = None) -> bool:
    """True when listing must wait — AI should only explain/adapt the account fact."""
    pl = payload or {}
    if pl.get("account_onboarded"):
        return False
    if account_busy(pl):
        return False  # mid OTP/password/role — other hard gates own the turn
    if _wa_unmatched_payload(pl):
        return True
    if pl.get("account_gate") == "ELIGIBILITY_BLOCKED":
        return True
    if str(pl.get("account_eligibility") or "").upper() == "NOT_ELIGIBLE":
        return True
    return False


_ADAPT_FACT_SYSTEM = (
    "You are InfraDealer WhatsApp AI. Backend gives you BACKEND_FACT (correct truth) "
    "and the customer's latest message.\n"
    "Your job: rewrite BACKEND_FACT into 1-2 short WhatsApp lines that fit THIS message.\n"
    "Rules:\n"
    "- Keep the same meaning as BACKEND_FACT. Never invent account IDs, OTP digits, "
    "credits, passwords, or live links.\n"
    "- Do NOT paste BACKEND_FACT word-for-word. Rephrase naturally (Sir/Ma'am, aap, ji).\n"
    "- First address what they just said (bechna/kharidna/account/help), then the fact.\n"
    "- If number is unmatched, invite WhatsApp signup: type account banao or reply Haan "
    "to create account here (OTP by SMS on this mobile).\n"
    "- One short WhatsApp reply only. No markdown headings."
)


def adapt_account_gate_reply(
    db: Session,
    conv: AiConversation,
    user_text: str,
    lang: str,
    payload: dict | None = None,
) -> str:
    """Correct fact → AI agent → reply adapted to user's message (fallback = fact)."""
    from ..services import resolve_ai_config
    from .i18n import language_instruction

    pl = payload if isinstance(payload, dict) else _payload(conv)
    fact = account_gate_fact(lang, pl)
    cfg = resolve_ai_config(db)
    if not cfg.get("enabled") or not cfg.get("api_key"):
        return fact

    user_block = (
        "BACKEND_FACT (correct — keep this truth):\n"
        + fact
        + "\n\nCUSTOMER_MESSAGE_START\n"
        + (user_text or "")[:600]
        + "\nCUSTOMER_MESSAGE_END\n"
        + "Rewrite BACKEND_FACT for this customer message. Do not paste it verbatim."
    )
    messages = [
        {"role": "system", "content": _ADAPT_FACT_SYSTEM + "\n" + language_instruction(lang)},
        {"role": "user", "content": user_block},
    ]
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en",
    }
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": 180,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    try:
        import httpx

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=body)
            data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        choice = (data.get("choices") or [{}])[0]
        content = str((choice.get("message") or {}).get("content") or "").strip()
        content = re.sub(r"\s+", " ", content).strip()
        if not content or len(content) < 12:
            return fact
        if re.search(r"(system prompt|api[_ ]?key|otp\s*\d{4,}|password\s*:)", content, re.I):
            return fact
        if len(content) > 420:
            content = content[:420].rsplit(" ", 1)[0] + "…"
        return content
    except Exception:
        log.exception("adapt_account_gate_reply failed — using canonical fact")
        return fact


def account_busy(payload: dict) -> bool:
    step = (payload.get("account_step") or "").strip()
    if not step or step == "done":
        return False
    if step.startswith("pw_"):
        return True
    return not payload.get("account_onboarded")


def should_intercept_account(payload: dict, text: str) -> bool:
    """Only steal the turn for account when the user is clearly answering account questions.

    Prevents: user asks price/vehicle but bot replies about account / OTP.
    """
    if not account_busy(payload):
        return False
    step = (payload.get("account_step") or "").strip()
    msg = (text or "").strip()
    if not msg:
        return False
    # Full WhatsApp registration / password-reset — always stay on the form
    if step in {
        "offer_create",
        "reg_name",
        "reg_username",
        "reg_email",
        "reg_password",
        "pw_otp",
        "pw_new",
    }:
        return True
    # Listing / card work always wins over a stuck account prompt
    if _LISTING_HINT.search(msg) and step in {"ask_exists", "password", "role"}:
        return False
    if step == "ask_exists":
        return (
            is_yes(msg)
            or is_no(msg)
            or says_has_account(msg)
            or bool(re.search(r"\b(nahi|nahin|new|create|bana nahi|account)\b", msg, re.I))
        )
    if step == "otp":
        digits = re.sub(r"\D", "", msg)
        return len(digits) == 6 or says_has_account(msg)
    if step == "password":
        if _PASS_SKIP.search(msg):
            return True
        if _LISTING_HINT.search(msg):
            return False
        return 4 <= len(msg) <= 64 and len(msg.split()) <= 4
    if step == "role":
        return extract_role(msg) is not None
    return False


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", re.I)
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{3,40}$")
_PASSWORD_RESET = re.compile(
    r"("
    r"\b(password|passwd|pasword|पासवर्ड)\b.{0,30}\b(badlo|badal|change|chenge|chang|reset|update|bhool|forgot|naya|new)\b"
    r"|\b(change|chenge|chang|reset|forgot|naya|new|badlo|badal)\b.{0,30}\b(password|passwd|pasword|पासवर्ड)\b"
    r"|\bpassword\s*(badlo|badal|change|chenge|reset)\b"
    r"|mera\s+account.{0,50}password"
    r"|account.{0,50}password.{0,30}(change|chenge|badlo|badal|reset)"
    r"|password\s*(chenge|chang|change|badlo)"
    r")",
    re.I,
)
_OTP_RESEND = re.compile(
    r"("
    r"\b(resend|re-send|dobara|phir\s*se)\b.{0,20}\b(otp|code)\b"
    r"|\b(otp|code)\b.{0,20}\b(resend|re-send|dobara|bhejo|bhej\s*do|send)\b"
    r"|(nahi|nahin|nhi|not).{0,16}(aya|aaya|aayi|ayi|mila|received|aaya\s*otp|otp)"
    r"|otp.{0,12}(nahi|nahin|nhi|not).{0,12}(aya|aaya|mila|received)"
    r")",
    re.I,
)
_OTP_CANCEL = re.compile(
    r"\b(cancel|band\s*karo|chhodo|mat\s*karo|skip|baad\s*me)\b",
    re.I,
)


def wants_password_reset(text: str) -> bool:
    return bool(_PASSWORD_RESET.search(text or ""))


def wants_otp_resend(text: str) -> bool:
    return bool(_OTP_RESEND.search(text or ""))


def clear_stale_listing_otp(conv: AiConversation, payload: dict | None = None) -> dict:
    """Onboarded users must not get stuck in listing OTP_PENDING loops."""
    pl = payload if isinstance(payload, dict) else _payload(conv)
    step = str(pl.get("account_step") or "")
    if pl.get("account_onboarded") and step not in {"otp", "pw_otp", "pw_new"}:
        if pl.get("verification_status") == "otp_pending" or conv.state == "OTP_PENDING":
            pl["verification_status"] = "verified" if pl.get("otp_verified") else "unverified"
            if conv.state == "OTP_PENDING":
                conv.state = "NEW"
            _write_payload(conv, pl)
    return pl


def suggest_username(name: str, mobile: str = "") -> str:
    base = re.sub(r"[^a-z0-9._]", "", (name or "").lower().replace(" ", ""))
    if len(base) >= 3:
        return base[:40]
    tail = "".join(ch for ch in (mobile or "") if ch.isdigit())[-4:] or "user"
    return (base + tail)[:40] or f"user{tail}"


def _valid_name(msg: str) -> bool:
    clean = re.sub(r"\s+", " ", (msg or "").strip())
    if len(clean) < 2 or len(clean) > 120:
        return False
    if re.fullmatch(r"\d+", clean):
        return False
    return bool(re.search(r"[a-zA-Z\u0900-\u097F]", clean))


def _valid_email(msg: str) -> bool:
    return bool(_EMAIL_RE.match((msg or "").strip()))


def _valid_username(msg: str) -> bool:
    return bool(_USERNAME_RE.match((msg or "").strip()))


def _valid_password(msg: str) -> bool:
    return 6 <= len((msg or "").strip()) <= 64


def begin_registration_offer(db: Session, conv: AiConversation, lang: str) -> str:
    """When WA number has no account — ask to create via WhatsApp (not dead-end)."""
    payload = _payload(conv)
    if payload.get("account_onboarded"):
        return t(lang, "confirm_ok")
    payload["account_step"] = "offer_create"
    payload["account_has_infra"] = False
    conv.error_message = "ask:account_reg_offer"
    _write_payload(conv, payload)
    return t(lang, "account_reg_offer")


def gate_or_registration_offer(
    db: Session,
    conv: AiConversation,
    user_text: str,
    lang: str,
    payload: dict | None = None,
) -> str:
    """Unmatched WA → registration offer; eligibility block → adapted fact."""
    pl = payload if isinstance(payload, dict) else _payload(conv)
    if _wa_unmatched_payload(pl) and not account_busy(pl):
        return begin_registration_offer(db, conv, lang)
    return adapt_account_gate_reply(db, conv, user_text, lang, pl)


def _website_line(lang: str) -> str:
    return t(lang, "account_created", site=SITE)


def _infra(db: Session):
    try:
        from ..infradealer.service import InfraDealerIntegrationService

        svc = InfraDealerIntegrationService(db)
        if svc.is_configured():
            return svc
    except Exception:
        log.exception("InfraDealer service unavailable")
    return None


def _state(db: Session, mobile: str) -> InfraDealerAccountState | None:
    return db.query(InfraDealerAccountState).filter(InfraDealerAccountState.mobile == (mobile or "")[-10:]).first()


def _infra_exists(st: InfraDealerAccountState | None) -> bool:
    return bool(st and st.account_status in {"ACCOUNT_EXISTS", "ACCOUNT_CREATED", "ACCOUNT_FOUND"})


def _finish_existing(db: Session, conv: AiConversation, lang: str, extra: str = "") -> str:
    payload = _payload(conv)
    user = _ensure_user(db, conv, payload)
    user.account_ready = True
    user.role = user.role or "user"
    return _finish(db, conv, user, lang, extra)


def _maybe_push_listing(db: Session, conv: AiConversation) -> None:
    payload = _payload(conv)
    if not payload.get("customer_confirmed"):
        return
    if str(payload.get("listing_status") or "").upper() in {"POSTED", "PENDING_REVIEW", "PUSHED_TO_INFRADEALER"}:
        return
    if payload.get("infradealer_listing_id"):
        return
    svc = _infra(db)
    if svc:
        from ..models import AiListingDraft

        draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first() if conv.draft_id else None
        if draft and svc.listing_already_pushed(conv, draft, payload):
            return
    execute_tool(db, conv, "submit_for_review", {})


def _finish(db: Session, conv: AiConversation, user: User, lang: str, extra: str = "") -> str:
    payload = _payload(conv)
    payload["account_onboarded"] = True
    payload["account_step"] = "done"
    payload["account_role"] = user.role or payload.get("account_role") or "user"
    payload["account_password_set"] = bool(user.password_hash)
    payload["account_eligibility"] = "ELIGIBLE"
    payload["wa_account_matched"] = True
    payload.pop("account_context", None)  # force refresh on next turn
    payload.pop("reg_password", None)
    payload.pop("pw_reset_otp", None)
    conv.profile_id = user.id
    conv.profile_status = "verified"
    _write_payload(conv, payload)
    _maybe_push_listing(db, conv)
    _send_website_button(db, conv, lang)
    bits = [x for x in (extra, _website_line(lang)) if x]
    return "\n\n".join(bits)


def _send_website_button(db: Session, conv: AiConversation, lang: str) -> None:
    """Send interactive button with website link after account onboarding."""
    try:
        from ..services import get_or_create_settings, send_whatsapp_button, store_chat
        import time as _time

        meta = get_or_create_settings(db)
        body_text = t(lang, "account_created", site=SITE)
        buttons = [{"title": t(lang, "btn_visit_website"), "url": SITE}]
        result = send_whatsapp_button(meta, conv.mobile, body_text, buttons)
        store_chat(
            db,
            wamid=result.get("wamid") or f"btn.{conv.id}.{int(_time.time())}",
            conversation_id=conv.conversation_id or f"CONV_{conv.mobile}",
            from_mobile=meta.phone_number_id or "infradealer",
            from_name="InfraDealer",
            to_mobile=conv.mobile,
            direction="outbound",
            body=body_text,
            status="sent",
            unread=False,
        )
    except Exception:
        log.exception("website button send failed for %s", conv.mobile)


def _ensure_user(db: Session, conv: AiConversation, payload: dict) -> User:
    user = db.query(User).filter(User.mobile == conv.mobile).first()
    if user:
        return user
    name = (
        payload.get("reg_name")
        or conv.customer_name
        or payload.get("customer_name")
        or payload.get("wa_name")
        or "Seller"
    )[:120]
    user = User(name=name, mobile=conv.mobile, source="whatsapp_ai_otp", role="user")
    db.add(user)
    db.flush()
    conv.profile_id = user.id
    return user


def _begin_name_step(db: Session, conv: AiConversation, lang: str, prefix: str = "") -> str:
    payload = _payload(conv)
    payload["account_step"] = "reg_name"
    payload["account_has_infra"] = False
    conv.error_message = "ask:account_reg_name"
    _write_payload(conv, payload)
    head = prefix.strip() if prefix else ""
    body = t(lang, "account_reg_ask_name")
    return f"{head}\n\n{body}".strip() if head else body


def start_password_reset(db: Session, conv: AiConversation, lang: str, *, resend: bool = False) -> str:
    """Existing account: SMS OTP → new password via WhatsApp (deterministic, no LLM)."""
    payload = _payload(conv)
    svc = _infra(db)
    if not svc:
        payload["account_step"] = ""
        _write_payload(conv, payload)
        return t(lang, "otp_send_fail")
    try:
        item = svc.password_reset_request(conv)
        if item:
            svc.process_outbox_item(item)
            db.flush()
            code = (item.business_status or item.last_error or "").upper()
            if item.status == "DONE" or code in {"OTP_SENT", "SUCCESS"}:
                payload["account_step"] = "pw_otp"
                payload.pop("pw_reset_otp", None)
                conv.error_message = "ask:pw_otp"
                _write_payload(conv, payload)
                if resend:
                    return t(lang, "account_pw_reset_resent")
                return t(lang, "account_pw_reset_start")
            if code == "ACCOUNT_NOT_FOUND":
                payload["account_step"] = ""
                _write_payload(conv, payload)
                return begin_registration_offer(db, conv, lang)
            log.warning(
                "password reset OTP request failed mobile=***%s status=%s code=%s",
                (conv.mobile or "")[-4:],
                item.status,
                code,
            )
    except Exception:
        log.exception("password reset OTP request failed for %s", conv.mobile)
    # Keep user in pw_otp if they already had an OTP pending; otherwise clear
    if payload.get("account_step") != "pw_otp":
        payload["account_step"] = ""
        _write_payload(conv, payload)
    return t(lang, "otp_send_fail")


def start_account(db: Session, conv: AiConversation, lang: str, prefix: str = "") -> str:
    """Start WhatsApp registration (name → username → email → password → SMS OTP)."""
    from .account_filter import collect_whatsapp_user, connect_webhook_account

    payload = _payload(conv)
    if payload.get("account_onboarded"):
        return prefix or t(lang, "confirm_ok")
    collect_whatsapp_user(db, conv, persist=True)
    svc = _infra(db)
    if svc:
        try:
            with db.begin_nested():
                connect_webhook_account(db, conv, refresh=True)
                db.flush()
        except Exception:
            log.exception("InfraDealer account check failed for %s", conv.mobile)
        st = _state(db, conv.mobile)
        if _infra_exists(st):
            return _finish_existing(db, conv, lang, prefix or t(lang, "account_already"))

    found = execute_tool(db, conv, "find_profile_by_mobile", {})
    user = db.query(User).filter(User.mobile == conv.mobile).first() if found.get("found") else None
    if user and user.account_ready and user.role:
        return _finish(db, conv, user, lang, prefix or t(lang, "confirm_ok"))

    return _begin_name_step(db, conv, lang, prefix)


def _submit_registration_and_otp(db: Session, conv: AiConversation, payload: dict, lang: str) -> str:
    name = str(payload.get("reg_name") or conv.customer_name or "Seller")[:120]
    username = str(payload.get("reg_username") or suggest_username(name, conv.mobile))[:40]
    email = str(payload.get("reg_email") or "").strip().lower()
    password = str(payload.get("reg_password") or "").strip()
    svc = _infra(db)
    created = None
    if svc:
        try:
            with db.begin_nested():
                created = svc.create_account(
                    conv,
                    name,
                    username=username,
                    email=email,
                    password=password,
                )
                if created:
                    svc.process_outbox_item(created)
                    db.flush()
        except Exception:
            log.exception("InfraDealer account create failed for %s", conv.mobile)
            created = None
        st = _state(db, conv.mobile)
        if st and st.account_status == "OTP_PENDING" and st.registration_id:
            payload["account_step"] = "otp"
            conv.error_message = "ask:otp"
            _write_payload(conv, payload)
            return t(lang, "account_otp")
        biz = ""
        if created:
            biz = (created.business_status or created.last_error or "").upper()
        if biz in {"EMAIL_EXISTS", "USERNAME_EXISTS", "INVALID_EMAIL", "INVALID_USERNAME", "INVALID_PASSWORD"}:
            if "EMAIL" in biz:
                payload["account_step"] = "reg_email"
                _write_payload(conv, payload)
                return t(lang, "account_reg_invalid_email")
            if "USERNAME" in biz:
                payload["account_step"] = "reg_username"
                _write_payload(conv, payload)
                return t(lang, "account_reg_invalid_username")
            if "PASSWORD" in biz:
                payload["account_step"] = "reg_password"
                _write_payload(conv, payload)
                return t(lang, "account_reg_invalid_password")
    return _local_otp_then(db, conv, payload, lang, "", infra_create=False)


def _local_otp_then(db, conv, payload, lang, prefix, infra_create=False) -> str:
    payload["account_step"] = "otp"
    _write_payload(conv, payload)
    # Prefer InfraDealer otp.request (backend DLT SMS) when registration exists.
    svc = _infra(db)
    st = _state(db, conv.mobile)
    if svc and st and st.registration_id:
        try:
            item = svc.request_otp(conv)
            if item:
                svc.process_outbox_item(item)
                db.flush()
                head = prefix or t(lang, "confirm_ok")
                return head + "\n\n" + t(lang, "account_otp")
        except Exception:
            log.exception("InfraDealer OTP request failed; falling back to local DLT SMS")
    otp = execute_tool(db, conv, "send_otp", {})
    if not otp.get("ok"):
        return t(lang, "otp_send_fail")
    head = prefix or t(lang, "confirm_ok")
    return head + "\n\n" + t(lang, "account_otp")


def _complete_after_otp(db: Session, conv: AiConversation, lang: str) -> str:
    payload = _payload(conv)
    user = _ensure_user(db, conv, payload)
    name = str(payload.get("reg_name") or "").strip()
    if name:
        user.name = name[:120]
        conv.customer_name = name[:120]
    pwd = str(payload.get("reg_password") or "").strip()
    if pwd:
        user.password_hash = hash_user_password(pwd)
        payload["account_password_set"] = True
    user.role = user.role or "user"
    user.account_ready = True
    user.source = user.source or "whatsapp_ai_otp"
    payload["account_role"] = user.role
    return _finish(db, conv, user, lang, t(lang, "account_reg_done"))


def handle_account(db: Session, conv: AiConversation, text: str, lang: str) -> str | None:
    payload = _payload(conv)
    step = (payload.get("account_step") or "").strip()
    if payload.get("account_onboarded") and not step.startswith("pw_"):
        return None
    if not step:
        return None
    msg = (text or "").strip()
    low = msg.lower()

    if step == "offer_create":
        if is_yes(msg) or wants_create_account(msg, payload) or _CREATE_ACCOUNT_SHORT.search(msg):
            return _begin_name_step(db, conv, lang)
        if is_no(msg):
            payload["account_step"] = ""
            _write_payload(conv, payload)
            return t(lang, "account_reg_offer_no")
        return t(lang, "account_reg_offer")

    if step == "reg_name":
        if not _valid_name(msg):
            return t(lang, "account_reg_invalid_name")
        name = re.sub(r"\s+", " ", msg).strip()[:120]
        payload["reg_name"] = name
        conv.customer_name = name
        suggested = suggest_username(name, conv.mobile)
        payload["reg_username_suggest"] = suggested
        payload["account_step"] = "reg_username"
        conv.error_message = "ask:account_reg_username"
        _write_payload(conv, payload)
        return t(lang, "account_reg_ask_username", username=suggested)

    if step == "reg_username":
        if is_yes(msg):
            username = str(payload.get("reg_username_suggest") or suggest_username(
                str(payload.get("reg_name") or ""), conv.mobile
            ))
        else:
            username = msg.strip().lstrip("@")
        if not _valid_username(username):
            return t(lang, "account_reg_invalid_username")
        payload["reg_username"] = username
        payload["account_step"] = "reg_email"
        conv.error_message = "ask:account_reg_email"
        _write_payload(conv, payload)
        return t(lang, "account_reg_ask_email")

    if step == "reg_email":
        email = msg.strip().lower()
        if not _valid_email(email):
            return t(lang, "account_reg_invalid_email")
        payload["reg_email"] = email
        payload["account_step"] = "reg_password"
        conv.error_message = "ask:account_reg_password"
        _write_payload(conv, payload)
        return t(lang, "account_reg_ask_password")

    if step == "reg_password":
        if not _valid_password(msg):
            return t(lang, "account_reg_invalid_password")
        payload["reg_password"] = msg.strip()
        _write_payload(conv, payload)
        return _submit_registration_and_otp(db, conv, payload, lang)

    if step == "pw_otp":
        if _OTP_CANCEL.search(msg) and not re.search(r"\d{6}", msg):
            payload["account_step"] = ""
            payload.pop("pw_reset_otp", None)
            conv.error_message = ""
            _write_payload(conv, payload)
            return t(lang, "account_pw_reset_cancelled")
        if wants_otp_resend(msg) or wants_password_reset(msg):
            return start_password_reset(db, conv, lang, resend=True)
        digits = re.sub(r"\D", "", msg)
        if len(digits) != 6:
            return t(lang, "account_pw_reset_need_otp")
        payload["pw_reset_otp"] = digits
        payload["account_step"] = "pw_new"
        conv.error_message = "ask:pw_new"
        _write_payload(conv, payload)
        return t(lang, "account_pw_reset_ask_new")

    if step == "pw_new":
        if not _valid_password(msg):
            return t(lang, "account_reg_invalid_password")
        otp = str(payload.get("pw_reset_otp") or "")
        if len(otp) != 6:
            # OTP was lost from session — ask again without auto-resending (avoids rate-limit noise)
            payload["account_step"] = "pw_otp"
            payload.pop("pw_reset_otp", None)
            conv.error_message = "ask:pw_otp"
            _write_payload(conv, payload)
            return t(lang, "account_pw_reset_otp_lost")
        svc = _infra(db)
        if not svc:
            return t(lang, "account_pw_reset_fail")
        try:
            item = svc.password_reset_confirm(conv, otp, msg.strip())
            if item:
                svc.process_outbox_item(item)
                db.flush()
            code = ((item.business_status if item else "") or (item.last_error if item else "") or "").upper()
            ok = bool(
                item
                and item.status == "DONE"
                and (code in {"PASSWORD_UPDATED", "SUCCESS", ""} or not code)
            )
            if not ok and item and item.status == "DONE":
                # Some gateways return success with empty business_status
                ok = not code or code in {"PASSWORD_UPDATED", "SUCCESS", "OTP_SENT"}
            if ok:
                user = _ensure_user(db, conv, payload)
                user.password_hash = hash_user_password(msg.strip())
                payload["account_step"] = "done" if payload.get("account_onboarded") else ""
                payload.pop("pw_reset_otp", None)
                _write_payload(conv, payload)
                return t(lang, "account_pw_reset_done")
            if code in {"OTP_INVALID", "OTP_EXPIRED", "OTP_ATTEMPTS_EXCEEDED"}:
                payload["account_step"] = "pw_otp"
                payload.pop("pw_reset_otp", None)
                _write_payload(conv, payload)
                return t(lang, "otp_mismatch")
            log.warning(
                "password reset confirm failed mobile=***%s status=%s code=%s err=%s",
                (conv.mobile or "")[-4:],
                getattr(item, "status", None),
                code,
                getattr(item, "last_error", None),
            )
        except Exception:
            log.exception("password reset confirm failed for %s", conv.mobile)
        return t(lang, "account_pw_reset_fail")

    if step == "ask_exists":
        if is_yes(msg) or says_has_account(msg) or re.search(r"\b(bana hua|banaya|already|account hai|haan account)\b", low):
            payload["account_has_infra"] = True
            from .account_filter import connect_webhook_account

            try:
                connect_webhook_account(db, conv, refresh=True)
                db.flush()
            except Exception:
                log.exception("account re-check failed for %s", conv.mobile)
            if _infra_exists(_state(db, conv.mobile)):
                return _finish_existing(db, conv, lang, t(lang, "account_already"))
            found = execute_tool(db, conv, "find_profile_by_mobile", {})
            user = db.query(User).filter(User.mobile == conv.mobile).first() if found.get("found") else None
            if user:
                if not user.role:
                    payload["account_step"] = "role"
                    conv.error_message = "ask:account_role"
                    _write_payload(conv, payload)
                    return t(lang, "account_role")
                user.account_ready = True
                return _finish(db, conv, user, lang, t(lang, "account_already"))
            return _begin_name_step(db, conv, lang)
        if is_no(msg) or re.search(r"\b(nahi|nahin|new|bana nahi|create)\b", low):
            return _begin_name_step(db, conv, lang)
        return t(lang, "account_ask")

    if step == "otp":
        if says_has_account(msg):
            from .account_filter import connect_webhook_account

            try:
                connect_webhook_account(db, conv, refresh=True)
                db.flush()
            except Exception:
                log.exception("account re-check failed for %s", conv.mobile)
            if _infra_exists(_state(db, conv.mobile)):
                return _finish_existing(db, conv, lang, t(lang, "account_already"))
            return t(lang, "account_otp")
        digits = re.sub(r"\D", "", msg)
        if len(digits) == 6:
            svc = _infra(db)
            st = _state(db, conv.mobile)
            if svc and st and st.registration_id:
                item = svc.verify_otp_external(conv, digits)
                if item:
                    svc.process_outbox_item(item)
                    db.flush()
                st = _state(db, conv.mobile)
                if st and st.account_status == "ACCOUNT_CREATED":
                    return _complete_after_otp(db, conv, lang)
                if st and item and (item.business_status == "OTP_EXPIRED" or item.last_error == "OTP_EXPIRED"):
                    nxt = svc.request_otp(conv)
                    if nxt:
                        svc.process_outbox_item(nxt)
                    return t(lang, "otp_ask")
                return t(lang, "otp_mismatch")
            result = execute_tool(db, conv, "verify_otp", {"code": digits})
            if not result.get("ok"):
                return t(lang, "otp_mismatch")
            return _complete_after_otp(db, conv, lang)
        return t(lang, "otp_ask")

    if step == "password":
        if _PASS_SKIP.search(msg) or len(msg) < 4:
            if len(msg) < 4 and not _PASS_SKIP.search(msg):
                return t(lang, "account_password")
        else:
            user = _ensure_user(db, conv, payload)
            user.password_hash = hash_user_password(msg)
            payload["account_password_set"] = True
        payload["account_step"] = "role"
        conv.error_message = "ask:account_role"
        _write_payload(conv, payload)
        return t(lang, "account_role")

    if step == "role":
        role = extract_role(msg)
        if not role:
            return t(lang, "account_role")
        user = _ensure_user(db, conv, payload)
        user.role = role
        user.account_ready = True
        user.source = user.source or "whatsapp_ai_otp"
        payload["account_role"] = role
        return _finish(db, conv, user, lang)

    return None
