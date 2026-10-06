"""First-contact welcome for new leads (e.g. Click-to-WhatsApp ad: "Can I get more info on this?").

Ad clicks, and unregistered numbers that only greet / ask for info, get the InfraDealer welcome
menu first. Account / OTP talk starts later, once they actually want to list.
The next reply "1" / "2" / "3" is mapped to buy / sell / business partner.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import AiConversation, Chat
from .tools import _write_payload

WELCOME_TEXT = (
    "👋 Hello! Welcome to InfraDealer 🚜\n"
    "Thanks for contacting us!\n"
    "We help buyers and sellers connect for Trucks, Dumpers, Tippers, JCB, Excavators, Cranes "
    "& other Heavy Equipment.\n"
    "Please tell us what you're looking for:\n"
    "1️⃣ Buy equipment\n"
    "2️⃣ Sell equipment\n"
    "3️⃣ Become a Dealer/Business Partner\n"
    "You can also send the machine type + preferred location + budget, and we'll help you "
    "find the right option."
)

# The ad's prefilled message is the welcome text itself — the customer already sees the menu.
WELCOME_ECHO_REPLY = (
    "🙏 Thanks for reaching out to InfraDealer!\n"
    "Just reply with a number:\n"
    "1️⃣ Buy equipment\n"
    "2️⃣ Sell equipment\n"
    "3️⃣ Become a Dealer/Business Partner\n"
    "Or send the machine type + preferred location + budget, and we'll help you find the right option."
)

PARTNER_TEXT = (
    "🤝 *Become a Dealer / Business Partner*\n"
    "• Register here: https://infradealer.com/partner/signup\n"
    "• After KYC approval your business card goes live for buyers across India\n"
    "Any question? Just reply here."
)

# Rewritten into plain intent text so the normal agents pick up the flow.
MENU_CHOICES = {
    "1": "Mujhe equipment kharidna hai",
    "2": "Mujhe equipment bechna hai",
}

# Meta's default Click-to-WhatsApp prefilled texts.
_AD_PREFILL = re.compile(
    r"(can i get more (info|information|details)|i('m| am) interested in this|"
    r"i want to know more|tell me more about this)",
    re.I,
)
_GREETING_ONLY = re.compile(
    r"(hi+|hii+|hello+|helo|hlo|hey+|namaste|namaskar|ram ram|jai shree ram|"
    r"good (morning|afternoon|evening)|sir|ji|sir ji)( (sir|ji|bhai|sir ji))?",
    re.I,
)
_INFO_ASK = re.compile(
    r"\b(more\s+info|info|information|details?|jaa?nkari|interested|know\s+more|"
    r"tell\s+me\s+more|batao|bataiye|btao)\b",
    re.I,
)
# Anything concrete belongs to the normal flow (listing, account, wallet, ...).
_SPECIFIC = re.compile(
    r"\b(bech|sell|kharid|buy|price|rate|lakh|model|account|otp|listing|card-|delete|"
    r"photo|wallet|token|password|update|login)\w*",
    re.I,
)
_MENU_PICK = re.compile(r"(?:option\s*)?([123])")


def _core(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text or "")).strip()


def is_ad_prefill(text: str) -> bool:
    msg = (text or "").strip()
    return bool(msg) and len(msg) <= 160 and bool(_AD_PREFILL.search(msg))


def is_welcome_echo(text: str) -> bool:
    core = _core(text).lower()
    return "welcome to infradealer" in core and ("buy equipment" in core or "sell equipment" in core)


def is_lead_inquiry(text: str) -> bool:
    """Greeting-only or generic 'more info' message with nothing concrete in it."""
    msg = (text or "").strip()
    if not msg or len(msg) > 160:
        return False
    if _SPECIFIC.search(msg):
        return False
    core = _core(msg)
    if core and _GREETING_ONLY.fullmatch(core):
        return True
    return bool(_INFO_ASK.search(msg))


def _has_listing_context(conv: AiConversation, payload: dict) -> bool:
    if conv.draft_id:
        return True
    return any(
        payload.get(k)
        for k in ("intent", "category", "brand", "model", "awaiting_confirm", "active_card_id")
    )


def _original_inbound_text(db: Session, conv: AiConversation) -> str:
    """Raw WhatsApp text of this turn — the corrector may have rewritten `text` before us."""
    wamid = (getattr(conv, "last_wamid", "") or "").strip()
    if not wamid:
        return ""
    try:
        row = (
            db.query(Chat)
            .filter(Chat.wamid == wamid, Chat.direction == "inbound")
            .order_by(Chat.id.desc())
            .first()
        )
        return (row.body or "") if row else ""
    except Exception:
        return ""


def lead_welcome_reply(
    db: Session, conv: AiConversation, payload: dict, text: str, media_note: str = ""
) -> str:
    """Welcome menu for an ad click / new lead's first inquiry this session; '' otherwise."""
    from .account import _wa_unmatched_payload, account_busy

    pl = payload or {}
    if media_note or account_busy(pl):
        return ""
    raw = _original_inbound_text(db, conv)
    if is_welcome_echo(text) or is_welcome_echo(raw):
        pl["lead_welcome_sent"] = True
        pl["lead_menu_pending"] = True
        pl["ai_introduced"] = True
        pl["lead_source"] = "ad"
        _write_payload(conv, pl)
        return WELCOME_ECHO_REPLY
    if pl.get("lead_welcome_sent") or _has_listing_context(conv, pl):
        return ""
    from_ad = is_ad_prefill(text) or is_ad_prefill(raw)
    new_lead = _wa_unmatched_payload(pl) and not pl.get("account_onboarded") and is_lead_inquiry(text)
    if not (from_ad or new_lead):
        return ""

    pl["lead_welcome_sent"] = True
    pl["lead_menu_pending"] = True
    pl["ai_introduced"] = True
    if from_ad:
        pl["lead_source"] = "ad"
    _write_payload(conv, pl)
    return WELCOME_TEXT


def resolve_menu_choice(conv: AiConversation, payload: dict, text: str) -> tuple[str, str]:
    """Map the reply right after the welcome menu.

    Returns (text_for_agents, direct_reply). direct_reply set → send it and stop.
    """
    pl = payload or {}
    if not pl.get("lead_menu_pending"):
        return text, ""
    pl["lead_menu_pending"] = False
    _write_payload(conv, pl)

    match = _MENU_PICK.fullmatch(_core(text).lower())
    if not match:
        return text, ""
    choice = match.group(1)
    if choice == "3":
        return text, PARTNER_TEXT
    return MENU_CHOICES[choice], ""
