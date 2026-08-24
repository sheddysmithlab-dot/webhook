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
    r"meri\s+(last\s+)?listing|"
    r"listing\s*(status|kahan|kya)|"
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


def account_busy(payload: dict) -> bool:
    step = payload.get("account_step") or ""
    return bool(step) and step != "done" and not payload.get("account_onboarded")


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
    payload.pop("account_context", None)  # force refresh on next turn
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
        from .i18n import t
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
    name = (conv.customer_name or payload.get("customer_name") or payload.get("wa_name") or "Seller")[:120]
    user = User(name=name, mobile=conv.mobile, source="whatsapp_ai_otp", role="user")
    db.add(user)
    db.flush()
    conv.profile_id = user.id
    return user


def start_account(db: Session, conv: AiConversation, lang: str, prefix: str = "") -> str:
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
        if st and st.account_status == "NOT_FOUND":
            payload["account_has_infra"] = False
            payload["account_step"] = "otp"
            _write_payload(conv, payload)
            name = (conv.customer_name or payload.get("customer_name") or payload.get("wa_name") or "Seller")[:120]
            try:
                with db.begin_nested():
                    created = svc.create_account(conv, name)
                    if created:
                        svc.process_outbox_item(created)
                        db.flush()
            except Exception:
                log.exception("InfraDealer account create failed for %s", conv.mobile)
            st = _state(db, conv.mobile)
            head = prefix or t(lang, "confirm_ok")
            if st and st.account_status == "OTP_PENDING" and st.registration_id:
                conv.error_message = "ask:otp"
                return head + "\n\n" + t(lang, "account_otp")
            return _local_otp_then(db, conv, payload, lang, prefix, infra_create=False)
    found = execute_tool(db, conv, "find_profile_by_mobile", {})
    user = db.query(User).filter(User.mobile == conv.mobile).first() if found.get("found") else None
    if user and user.account_ready and user.role:
        return _finish(db, conv, user, lang, prefix or t(lang, "confirm_ok"))
    payload = _payload(conv)
    payload["account_step"] = "ask_exists"
    conv.error_message = "ask:account_exists"
    _write_payload(conv, payload)
    head = prefix or t(lang, "confirm_ok")
    return head + "\n\n" + t(lang, "account_ask")


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


def handle_account(db: Session, conv: AiConversation, text: str, lang: str) -> str | None:
    payload = _payload(conv)
    if payload.get("account_onboarded"):
        return None
    step = payload.get("account_step") or ""
    if not step:
        return None
    msg = (text or "").strip()
    low = msg.lower()

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
            payload["account_step"] = "otp"
            _write_payload(conv, payload)
            otp = execute_tool(db, conv, "send_otp", {})
            if not otp.get("ok"):
                return t(lang, "otp_send_fail")
            return t(lang, "account_otp")
        if is_no(msg) or re.search(r"\b(nahi|nahin|new|bana nahi|create)\b", low):
            payload["account_has_infra"] = False
            payload["account_step"] = "otp"
            _write_payload(conv, payload)
            svc = _infra(db)
            if svc:
                name = (conv.customer_name or payload.get("customer_name") or payload.get("wa_name") or "Seller")[:120]
                item = svc.create_account(conv, name)
                if item:
                    svc.process_outbox_item(item)
                    db.flush()
                st = _state(db, conv.mobile)
                if st and st.account_status == "OTP_PENDING" and st.registration_id:
                    conv.error_message = "ask:otp"
                    return t(lang, "account_otp")
            otp = execute_tool(db, conv, "send_otp", {})
            if not otp.get("ok"):
                return t(lang, "otp_send_fail")
            return t(lang, "account_otp")
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
                    payload = _payload(conv)
                    payload["account_step"] = "password"
                    conv.error_message = "ask:account_password"
                    _write_payload(conv, payload)
                    return t(lang, "account_password")
                if st and item and (item.business_status == "OTP_EXPIRED" or item.last_error == "OTP_EXPIRED"):
                    nxt = svc.request_otp(conv)
                    if nxt:
                        svc.process_outbox_item(nxt)
                    return t(lang, "otp_ask")
                return t(lang, "otp_mismatch")
            result = execute_tool(db, conv, "verify_otp", {"code": digits})
            if not result.get("ok"):
                return t(lang, "otp_mismatch")
            payload = _payload(conv)
            payload["account_step"] = "password"
            conv.error_message = "ask:account_password"
            _write_payload(conv, payload)
            return t(lang, "account_password")
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
