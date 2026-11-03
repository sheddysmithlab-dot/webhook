"""Reusable WhatsApp interactive choices (list + reply buttons) for common asks.

Office post-desk flow stays in office_mode.py. This module covers customer-facing
choice prompts: intent, confirm, menu, account, category, skip, session recovery.
"""

from __future__ import annotations

import logging
import re

from ..models import AiConversation
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.wa_choice")

# ── Stable inbound ids ──────────────────────────────────────────────────────
WA_BTN_SELL = "wa_intent_sell"
WA_BTN_BUY = "wa_intent_buy"
WA_BTN_YES = "wa_confirm_yes"
WA_BTN_NO = "wa_confirm_no"
WA_BTN_SKIP = "wa_skip"
WA_BTN_CONTINUE = "wa_continue"
WA_BTN_MENU_SELL = "wa_menu_sell"
WA_BTN_MENU_BUY = "wa_menu_buy"
WA_BTN_MENU_STATUS = "wa_menu_status"
WA_BTN_MENU_ACCOUNT = "wa_menu_account"
WA_BTN_MENU_PASSWORD = "wa_menu_password"
WA_BTN_BROKER = "wa_role_broker"
WA_BTN_USER = "wa_role_user"
WA_BTN_USERNAME_OK = "wa_username_ok"
WA_BTN_USERNAME_CHANGE = "wa_username_change"
WA_BTN_SESSION_NEW = "wa_session_new"
WA_BTN_SESSION_UPDATE = "wa_session_update"
WA_BTN_AFTER_NEW = "wa_after_new_yes"
WA_BTN_AFTER_NO = "wa_after_new_no"

# Category list ids → plain category name for extract/normalize
_CATEGORY_CHOICES = (
    "Truck",
    "Tipper",
    "Dumper",
    "Excavator",
    "JCB",
    "Loader",
    "Crane",
    "Bus",
    "Tractor",
    "Other",
)

WA_ID_TO_TEXT = {
    WA_BTN_SELL: "sell",
    WA_BTN_BUY: "buy",
    WA_BTN_YES: "Haan",
    WA_BTN_NO: "Nahi",
    WA_BTN_SKIP: "skip",
    WA_BTN_CONTINUE: "continue",
    WA_BTN_MENU_SELL: "1",
    WA_BTN_MENU_BUY: "2",
    WA_BTN_MENU_STATUS: "3",
    WA_BTN_MENU_ACCOUNT: "4",
    WA_BTN_MENU_PASSWORD: "password help",
    WA_BTN_BROKER: "broker",
    WA_BTN_USER: "user",
    WA_BTN_USERNAME_OK: "Haan",
    WA_BTN_USERNAME_CHANGE: "change username?",
    WA_BTN_SESSION_NEW: "nayi listing",
    WA_BTN_SESSION_UPDATE: "last listing update",
    WA_BTN_AFTER_NEW: "nayi listing",
    WA_BTN_AFTER_NO: "Nahi",
}
for _cat in _CATEGORY_CHOICES:
    WA_ID_TO_TEXT[f"wa_cat_{_cat.lower().replace(' ', '_')}"] = _cat

INTENT_BUTTONS = (
    {"id": WA_BTN_SELL, "title": "Sell / Bechna"},
    {"id": WA_BTN_BUY, "title": "Buy / Kharidna"},
)
CONFIRM_BUTTONS = (
    {"id": WA_BTN_YES, "title": "Haan"},
    {"id": WA_BTN_NO, "title": "Nahi"},
)
# Confirm card with optional location tip (max 3 reply buttons).
PLACE_CONFIRM_BUTTONS = (
    {"id": WA_BTN_YES, "title": "Haan"},
    {"id": WA_BTN_SKIP, "title": "Skip location"},
    {"id": WA_BTN_NO, "title": "Nahi"},
)
SKIP_CONTINUE_BUTTONS = (
    {"id": WA_BTN_SKIP, "title": "Skip"},
    {"id": WA_BTN_CONTINUE, "title": "Continue"},
)
ROLE_BUTTONS = (
    {"id": WA_BTN_BROKER, "title": "Broker"},
    {"id": WA_BTN_USER, "title": "User"},
)
USERNAME_BUTTONS = (
    {"id": WA_BTN_USERNAME_OK, "title": "Haan"},
    {"id": WA_BTN_USERNAME_CHANGE, "title": "Change"},
)
SESSION_BUTTONS = (
    {"id": WA_BTN_SESSION_NEW, "title": "Nayi listing"},
    {"id": WA_BTN_SESSION_UPDATE, "title": "Last update"},
)
AFTER_SUBMIT_BUTTONS = (
    {"id": WA_BTN_AFTER_NEW, "title": "Haan"},
    {"id": WA_BTN_AFTER_NO, "title": "Nahi"},
)
MENU_LIST_ROWS = (
    {"id": WA_BTN_MENU_SELL, "title": "Sell / Bechna", "description": "Gaadi ya machine bechna"},
    {"id": WA_BTN_MENU_BUY, "title": "Buy / Kharidna", "description": "Gaadi ya machine kharidna"},
    {"id": WA_BTN_MENU_STATUS, "title": "Listing status", "description": "Apni listing / link dekhna"},
    {"id": WA_BTN_MENU_ACCOUNT, "title": "Account setup", "description": "Account / profile madad"},
)
NEXT_HELP_LIST_ROWS = (
    {"id": WA_BTN_MENU_SELL, "title": "Sell / Bechna", "description": "Vehicle bechna"},
    {"id": WA_BTN_MENU_BUY, "title": "Buy / Kharidna", "description": "Vehicle kharidna"},
    {"id": WA_BTN_MENU_STATUS, "title": "Listing", "description": "Listing status / link"},
    {"id": WA_BTN_MENU_PASSWORD, "title": "Password help", "description": "Password / login madad"},
)
CATEGORY_LIST_ROWS = tuple(
    {
        "id": f"wa_cat_{c.lower().replace(' ', '_')}",
        "title": c[:24],
        "description": f"{c} category",
    }
    for c in _CATEGORY_CHOICES
)

# ── Reply detectors (most-specific first in ensure_*) ───────────────────────
_CONFIRM_ASK_HINT = re.compile(
    r"details\s+sahi|confirm_ask|haan\s+likh|reply\s+yes|हाँ\s*लिख|are these details|"
    r"haan\s*/\s*nahi\s*dabayein|tap\s*\*?yes\*?",
    re.I,
)
_PLACE_TIP_HINT = re.compile(
    r"राज्य/शहर|state/city|kis\s+state|which\s+state|location|Skip\s+location|buyers?\s+ko\s+dhoondhne",
    re.I,
)
_INTENT_ASK_HINT = re.compile(
    r"bechna\s+chahte|bechna\s+hai\s+ya|sell\s+or\s+buy|sell\s+karna|बेचना|खरीदना|"
    r"wechna|vecvu|wikayche|sell\s*/\s*buy",
    re.I,
)
_MENU_ASK_HINT = re.compile(
    r"kya\s+aap\s+karna\s+chahte|what would you like to do|number reply|नंबर\s*रिप्लाई|"
    r"select\s+option\s+se\s+choose",
    re.I,
)
_ACCOUNT_NEXT_HINT = re.compile(
    r"aur\s+kuch\s+chahiye|need\s+anything\s+else|password\s+help|bechna/kharidna",
    re.I,
)
_OPTIONAL_HINT = re.compile(
    r"kuch\s+aur\s+details|any\s+more\s+details|optional_bundle|"
    r"skip\s*/\s*continue|\*Skip\*.*\*Continue\*",
    re.I,
)
_YEAR_CONFIRM_HINT = re.compile(
    r"year\s+\d{4}\s+confirm|confirm\s+kar\s+doon|shall\s+i\s+confirm\s+year|"
    r"ईयर\s+\d{4}\s+कन्फर्म",
    re.I,
)
_CATEGORY_HINT = re.compile(
    r"kaunsi\s+category|which\s+category|Truck\s*/\s*Tipper\s*/\s*JCB|कौन\s*सी\s*कैटेगरी",
    re.I,
)
_ACCOUNT_OFFER_HINT = re.compile(
    r"account\s+bana\s+du|account\s+nahi\s+mila|shall\s+i\s+create\s+your\s+account|"
    r"account\s+banao|अकाउंट\s+बना\s+दूँ|अकाउंट\s+नहीं\s+मिला",
    re.I,
)
_ROLE_HINT = re.compile(
    r"broker\s+hain\s+ya\s+user|broker\s+or\s+a?\s*user|ब्रोकर\s+हैं\s+या\s+यूजर",
    re.I,
)
_USERNAME_HINT = re.compile(
    r"username\s+suggest|suggested\s+username|username\s+theek\s+hai|"
    r"यूज़रनेम\s+सुझा|is this okay\?\s*reply\s+yes",
    re.I,
)
_CHAT_CLEARED_HINT = re.compile(
    r"chat\s+clear|nayi\s+listing\s+shuru|start\s+a\s+new\s+listing\s+or\s+update|"
    r"last\s+listing\s+update|चैट\s+क्लियर",
    re.I,
)
_AFTER_SUBMIT_HINT = re.compile(
    r"nayi\s+listing\s+chahiye|need\s+a\s+new\s+one|नई\s+लिस्टिंग\s+चाहिए|"
    r"listing\s+submit\s+ho\s+chuki\s+hai\.\s*nayi",
    re.I,
)


def resolve_wa_choice_text(bid: str, title: str = "") -> str:
    """Map interactive id/title to text the rest of the engine already understands."""
    key = str(bid or "").strip()
    if key in WA_ID_TO_TEXT:
        return WA_ID_TO_TEXT[key]
    return str(title or key or "").strip()


def _set_ui(payload: dict, ui: dict) -> None:
    payload["wa_ui_pending"] = True
    payload["wa_ui"] = ui


def clear_wa_ui(payload: dict) -> None:
    payload["wa_ui_pending"] = False
    payload["wa_ui"] = None


def _arm_buttons(conv: AiConversation, buttons: tuple | list) -> None:
    payload = _payload(conv)
    _set_ui(payload, {"mode": "buttons", "buttons": [dict(b) for b in buttons]})
    _write_payload(conv, payload)


def _arm_list(
    conv: AiConversation,
    rows: tuple | list,
    *,
    button_label: str = "Select option",
    section_title: str = "Options",
    fallback_buttons: tuple | list | None = None,
) -> None:
    payload = _payload(conv)
    _set_ui(
        payload,
        {
            "mode": "list",
            "button_label": button_label,
            "section_title": section_title,
            "rows": [dict(r) for r in rows],
            "fallback_buttons": [dict(b) for b in (fallback_buttons or INTENT_BUTTONS)],
        },
    )
    _write_payload(conv, payload)


def arm_intent_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, INTENT_BUTTONS)


def arm_confirm_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, CONFIRM_BUTTONS)


def arm_place_confirm_choice(conv: AiConversation) -> None:
    """Confirm card + optional location tip → Haan / Skip location / Nahi."""
    _arm_buttons(conv, PLACE_CONFIRM_BUTTONS)


def arm_skip_continue_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, SKIP_CONTINUE_BUTTONS)


def arm_role_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, ROLE_BUTTONS)


def arm_username_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, USERNAME_BUTTONS)


def arm_session_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, SESSION_BUTTONS)


def arm_after_submit_choice(conv: AiConversation) -> None:
    _arm_buttons(conv, AFTER_SUBMIT_BUTTONS)


def arm_menu_choice(conv: AiConversation) -> None:
    _arm_list(conv, MENU_LIST_ROWS, section_title="Options")


def arm_account_next_choice(conv: AiConversation) -> None:
    _arm_list(conv, NEXT_HELP_LIST_ROWS, section_title="Next help")


def arm_category_choice(conv: AiConversation) -> None:
    _arm_list(
        conv,
        CATEGORY_LIST_ROWS,
        section_title="Category",
        fallback_buttons=INTENT_BUTTONS[:2],
    )


def take_wa_interactive_ui(conv: AiConversation) -> dict:
    """Consume one-shot general interactive UI (not office)."""
    payload = _payload(conv)
    if not payload.get("wa_ui_pending"):
        return {}
    ui = payload.get("wa_ui") if isinstance(payload.get("wa_ui"), dict) else {}
    clear_wa_ui(payload)
    _write_payload(conv, payload)
    if not ui:
        return {}
    mode = str(ui.get("mode") or "").strip().lower()
    if mode == "list":
        return {
            "mode": "list",
            "button_label": str(ui.get("button_label") or "Select option")[:20],
            "section_title": str(ui.get("section_title") or "Options")[:24],
            "rows": list(ui.get("rows") or []),
            "fallback_buttons": list(ui.get("fallback_buttons") or INTENT_BUTTONS),
        }
    buttons = list(ui.get("buttons") or [])
    if buttons:
        return {"mode": "buttons", "buttons": buttons}
    return {}


def ensure_interactive_for_reply(conv: AiConversation, reply: str) -> None:
    """Arm interactive chrome for common choice asks right before WhatsApp send.

    Skips when office UI is already armed (post-live pick / confirm).
    Order: most-specific detectors first.
    """
    if not reply or not str(reply).strip():
        return
    payload = _payload(conv)
    if payload.get("office_send_choice_buttons") or payload.get("office_button_set"):
        return
    if payload.get("wa_ui_pending"):
        return

    text = str(reply)

    # Account / session / collection (before generic confirm/intent)
    if _USERNAME_HINT.search(text):
        arm_username_choice(conv)
        return
    if _ROLE_HINT.search(text):
        arm_role_choice(conv)
        return
    if _ACCOUNT_OFFER_HINT.search(text):
        arm_confirm_choice(conv)  # Haan / Nahi create account
        return
    if _CHAT_CLEARED_HINT.search(text):
        arm_session_choice(conv)
        return
    if _AFTER_SUBMIT_HINT.search(text):
        arm_after_submit_choice(conv)
        return
    if _YEAR_CONFIRM_HINT.search(text):
        arm_confirm_choice(conv)
        return
    if _CATEGORY_HINT.search(text):
        arm_category_choice(conv)
        return
    if _OPTIONAL_HINT.search(text):
        arm_skip_continue_choice(conv)
        return
    if _ACCOUNT_NEXT_HINT.search(text):
        arm_account_next_choice(conv)
        return

    # Confirm card: with location tip → Haan / Skip / Nahi
    if _CONFIRM_ASK_HINT.search(text):
        if _PLACE_TIP_HINT.search(text):
            arm_place_confirm_choice(conv)
        else:
            arm_confirm_choice(conv)
        return

    if _MENU_ASK_HINT.search(text):
        arm_menu_choice(conv)
        return

    if _INTENT_ASK_HINT.search(text):
        arm_intent_choice(conv)
