"""Reusable WhatsApp interactive choices (list + reply buttons) for common asks.

Office post-desk flow stays in office_mode.py. This module covers:
- Sell vs Buy
- Listing confirm (Haan / Nahi)
- Main options menu
"""

from __future__ import annotations

import logging
import re

from ..models import AiConversation
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.wa_choice")

# Stable inbound ids → existing text handlers keep working.
WA_BTN_SELL = "wa_intent_sell"
WA_BTN_BUY = "wa_intent_buy"
WA_BTN_YES = "wa_confirm_yes"
WA_BTN_NO = "wa_confirm_no"
WA_BTN_MENU_SELL = "wa_menu_sell"
WA_BTN_MENU_BUY = "wa_menu_buy"
WA_BTN_MENU_STATUS = "wa_menu_status"
WA_BTN_MENU_ACCOUNT = "wa_menu_account"

WA_ID_TO_TEXT = {
    WA_BTN_SELL: "sell",
    WA_BTN_BUY: "buy",
    WA_BTN_YES: "Haan",
    WA_BTN_NO: "Nahi",
    WA_BTN_MENU_SELL: "1",
    WA_BTN_MENU_BUY: "2",
    WA_BTN_MENU_STATUS: "3",
    WA_BTN_MENU_ACCOUNT: "4",
}

INTENT_BUTTONS = (
    {"id": WA_BTN_SELL, "title": "Sell / Bechna"},
    {"id": WA_BTN_BUY, "title": "Buy / Kharidna"},
)
CONFIRM_BUTTONS = (
    {"id": WA_BTN_YES, "title": "Haan"},
    {"id": WA_BTN_NO, "title": "Nahi"},
)
MENU_LIST_ROWS = (
    {"id": WA_BTN_MENU_SELL, "title": "Sell / Bechna", "description": "Gaadi ya machine bechna"},
    {"id": WA_BTN_MENU_BUY, "title": "Buy / Kharidna", "description": "Gaadi ya machine kharidna"},
    {"id": WA_BTN_MENU_STATUS, "title": "Listing status", "description": "Apni listing / link dekhna"},
    {"id": WA_BTN_MENU_ACCOUNT, "title": "Account setup", "description": "Account / profile madad"},
)

_CONFIRM_ASK_HINT = re.compile(
    r"details\s+sahi|confirm_ask|haan\s+likh|reply\s+yes|हाँ\s*लिख|are these details",
    re.I,
)
_INTENT_ASK_HINT = re.compile(
    r"bechna\s+chahte|sell\s+or\s+buy|sell\s+karna|बेचना|खरीदना|wechna|vecvu|wikayche",
    re.I,
)
_MENU_ASK_HINT = re.compile(
    r"kya\s+aap\s+karna\s+chahte|what would you like to do|number reply|नंबर\s*रिप्लाई",
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


def arm_intent_choice(conv: AiConversation) -> None:
    payload = _payload(conv)
    _set_ui(
        payload,
        {
            "mode": "buttons",
            "buttons": [dict(b) for b in INTENT_BUTTONS],
        },
    )
    _write_payload(conv, payload)


def arm_confirm_choice(conv: AiConversation) -> None:
    payload = _payload(conv)
    _set_ui(
        payload,
        {
            "mode": "buttons",
            "buttons": [dict(b) for b in CONFIRM_BUTTONS],
        },
    )
    _write_payload(conv, payload)


def arm_menu_choice(conv: AiConversation) -> None:
    payload = _payload(conv)
    _set_ui(
        payload,
        {
            "mode": "list",
            "button_label": "Select option",
            "section_title": "Options",
            "rows": [dict(r) for r in MENU_LIST_ROWS],
            "fallback_buttons": [dict(b) for b in INTENT_BUTTONS],
        },
    )
    _write_payload(conv, payload)


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
    """
    if not reply or not str(reply).strip():
        return
    payload = _payload(conv)
    if payload.get("office_send_choice_buttons") or payload.get("office_button_set"):
        return
    if payload.get("wa_ui_pending"):
        return

    # Listing confirm: Haan / Nahi — only when this reply is actually asking.
    # (awaiting_confirm alone is too broad; mid-flow tips must stay text.)
    if _CONFIRM_ASK_HINT.search(reply):
        arm_confirm_choice(conv)
        return

    # Main numbered menu → list (radio-style Select option)
    if _MENU_ASK_HINT.search(reply):
        arm_menu_choice(conv)
        return

    # Sell vs Buy when intent not set yet
    intent = str(payload.get("intent") or "").strip().upper()
    if not intent and _INTENT_ASK_HINT.search(reply):
        arm_intent_choice(conv)
