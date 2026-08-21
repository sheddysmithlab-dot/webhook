"""Collect WhatsApp sell/buy vehicle data into conversation memory.

Flow: account_filter → chat_memory → data_filteration → data_push

chat_memory stores what the user said; when enough fields exist it hands off
to data_filteration for cleaning + confirmation text back to the user.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..identity import looks_like_price
from ..models import AiConversation
from .extract import extract_from_text
from .i18n import t
from .schema import missing_fields, normalize_vehicle_category
from .tools import _payload, _write_payload, execute_tool

_SKIP_OPT = re.compile(r"\b(skip|baad me|nahi pata|koi nahi|bas yahi)\b", re.I)


def read_memory(db: Session, conv: AiConversation) -> dict[str, Any]:
    """Current collected sell/buy memory for this WhatsApp chat."""
    return _payload(conv)


def write_memory(db: Session, conv: AiConversation, payload: dict) -> dict:
    _write_payload(conv, payload)
    return payload


def _soft_sell_intent(fields: dict, text: str, media_note: str) -> dict:
    if fields.get("intent"):
        return fields
    if not (fields.get("brand") or fields.get("model") or media_note):
        return fields
    low = (text or "").lower()
    if re.search(r"\b(bech|sell|bikau|dena)\b", low) or media_note:
        if not re.search(r"\b(kharid|buy|chahiye|lena)\b", low):
            fields = dict(fields)
            fields["intent"] = "SELL"
    return fields


def apply_fields(db: Session, conv: AiConversation, fields: dict) -> dict:
    """Persist extracted intent / vehicle / contact into chat memory."""
    if not fields:
        return read_memory(db, conv)
    if fields.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": fields["intent"]})
    veh = {
        k: v
        for k, v in fields.items()
        if k not in {"intent", "contact_phone"} and v not in (None, "")
    }
    if veh:
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)
    if fields.get("contact_phone"):
        pl = read_memory(db, conv)
        pl["contact_phone"] = fields["contact_phone"]
        write_memory(db, conv, pl)
    return read_memory(db, conv)


def map_short_answer(db: Session, conv: AiConversation, text: str) -> dict:
    """Map one-word answers to the last asked field (year / price / category / state)."""
    msg = (text or "").strip()
    if not msg:
        return read_memory(db, conv)
    last = (conv.error_message or "")
    ask_key = last.split(":", 1)[1].strip() if last.lower().startswith("ask:") else ""
    pl = read_memory(db, conv)

    if ask_key == "year" and not pl.get("year"):
        y = re.search(r"\b((?:19|20)\d{2}|\d{2})\b", msg)
        if y:
            tok = y.group(1)
            if len(tok) == 2:
                n = int(tok)
                tok = f"20{tok}" if n <= 30 else (f"19{tok}" if n >= 90 else tok)
            execute_tool(db, conv, "save_vehicle_data", {"year": tok, "source": "customer"})

    if ask_key in {"expected_price", "budget"} and looks_like_price(msg):
        key = "budget" if ask_key == "budget" else "expected_price"
        execute_tool(db, conv, "save_vehicle_data", {key: msg[:80], "source": "customer"})

    if ask_key == "category" and not normalize_vehicle_category(pl.get("category") or ""):
        cat = normalize_vehicle_category(msg)
        if cat:
            execute_tool(db, conv, "save_vehicle_data", {"category": cat, "type": cat, "source": "customer"})

    if ask_key in {"state", "location"} and len(msg.split()) <= 12:
        from .extract import extract_state, infer_state_from_city, _fuzzy_city

        st = extract_state(msg.lower())
        city = _fuzzy_city(msg.split()[0]) if msg.split() else None
        data: dict[str, Any] = {"source": "customer"}
        if st:
            data["state"] = st
        if city:
            data["city"] = city.title()
            data["location"] = data["city"]
            data.setdefault("state", infer_state_from_city(city) or "")
        elif not st:
            data["state"] = msg[:80]
        execute_tool(db, conv, "save_vehicle_data", {k: v for k, v in data.items() if v})

    return read_memory(db, conv)


def collect_message(
    db: Session,
    conv: AiConversation,
    text: str,
    media_note: str = "",
) -> dict[str, Any]:
    """Main collect step: parse user text → save into chat memory → return fields this turn."""
    fields = extract_from_text(text or "")
    fields = _soft_sell_intent(fields, text or "", media_note or "")
    apply_fields(db, conv, fields)
    map_short_answer(db, conv, text or "")
    return fields


def ask_missing(lang: str, payload: dict) -> str | None:
    """Next missing sell/buy question for WhatsApp."""
    miss = [m for m in missing_fields(payload) if m not in {"customer_name", "photos"}]
    if not miss:
        return None
    key = miss[0]
    q = t(
        lang,
        key
        if key
        in {
            "intent",
            "category",
            "brand",
            "model",
            "year",
            "expected_price",
            "budget",
            "state",
            "location",
        }
        else "more_detail",
    )
    bits = [str(payload.get(k)) for k in ("brand", "model", "year", "state", "expected_price") if payload.get(k)]
    if bits and key != "intent":
        return t(lang, "ack", facts=" ".join(bits[:4])) + q
    return q


def mark_optional_done(db: Session, conv: AiConversation, text: str, fields: dict | None = None) -> dict:
    """After optional_bundle ask, accept skip / any reply as done."""
    pl = read_memory(db, conv)
    if not pl.get("optional_asked") or pl.get("optional_done"):
        return pl
    msg = (text or "").strip()
    if fields or _SKIP_OPT.search(msg) or msg:
        pl["optional_done"] = True
        write_memory(db, conv, pl)
    return read_memory(db, conv)


def mark_optional_asked(db: Session, conv: AiConversation) -> dict:
    pl = read_memory(db, conv)
    pl["optional_asked"] = True
    conv.error_message = "ask:optional_bundle"
    write_memory(db, conv, pl)
    return pl


def pass_to_filteration(db: Session, conv: AiConversation):
    """Hand collected memory to data_filteration."""
    from .data_filteration import filter_memory

    return filter_memory(db, conv)


def send_for_confirmation(db: Session, conv: AiConversation, lang: str) -> str:
    """Filter collected data and send confirmation card back through chat memory."""
    from .data_filteration import prepare_confirmation

    _data, text = prepare_confirmation(db, conv, lang)
    return text
