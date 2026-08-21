"""Filter chat_memory collected sell/buy data → full listing info for confirmation.

Flow: account_filter → chat_memory → data_filteration → data_push
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..identity import looks_like_price, seller_fields, unique_photo_ids
from ..models import AiConversation, AiListingDraft
from .i18n import t
from .schema import _blank, missing_fields, normalize_vehicle_category


# Keys we keep when filtering raw chat memory into a clean listing card.
_VEHICLE_KEYS = (
    "intent",
    "category",
    "type",
    "brand",
    "model",
    "year",
    "expected_price",
    "budget",
    "budget_max",
    "state",
    "city",
    "location",
    "running",
    "running_km",
    "operating_hours",
    "owners",
    "finance_amount",
    "finance_condition",
    "tyre_percent",
    "work_issues",
    "condition",
    "description",
    "active_card_id",
    "media_ids",
    "photos_complete",
    "optional_asked",
    "optional_done",
    "customer_name",
    "wa_name",
    "whatsapp_number",
    "contact_phone",
)


@dataclass
class FilteredListing:
    ready: bool
    missing: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    intent: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "missing": list(self.missing),
            "intent": self.intent,
            "data": dict(self.data),
        }


def _clean_str(val: Any, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(val or "").strip())
    if text.lower() in {"null", "none", "unknown", "-"}:
        return ""
    return text[:limit]


def is_collection_ready(payload: dict) -> bool:
    """True when required sell/buy fields are present for confirmation."""
    intent = (payload.get("intent") or "").upper()
    if intent == "SELL":
        price = looks_like_price(str(payload.get("expected_price") or ""))
        state_ok = not _blank(payload.get("state")) or not _blank(payload.get("location"))
        return bool(
            normalize_vehicle_category(payload.get("category") or payload.get("type") or "")
            and not _blank(payload.get("brand"))
            and not _blank(payload.get("model"))
            and not _blank(payload.get("year"))
            and price
            and state_ok
        )
    if intent == "BUY":
        want = not _blank(payload.get("brand")) or bool(
            normalize_vehicle_category(payload.get("category") or payload.get("type") or "")
        )
        cat_ok = bool(normalize_vehicle_category(payload.get("category") or payload.get("type") or ""))
        budget = looks_like_price(str(payload.get("budget") or payload.get("budget_max") or ""))
        state_ok = not _blank(payload.get("state")) or not _blank(payload.get("location"))
        return bool(cat_ok and want and budget and state_ok)
    return False


def filter_collected(payload: dict) -> dict[str, Any]:
    """Strip noise from chat memory; keep only usable listing fields."""
    out: dict[str, Any] = {}
    for key in _VEHICLE_KEYS:
        if key not in payload:
            continue
        val = payload.get(key)
        if val in (None, "", [], {}, False):
            continue
        if key in {"category", "type"}:
            canon = normalize_vehicle_category(str(val))
            if canon:
                out[key] = canon
            continue
        if key == "expected_price" and not looks_like_price(str(val)):
            continue
        if key in {"budget", "budget_max"} and not looks_like_price(str(val)):
            continue
        if isinstance(val, str):
            cleaned = _clean_str(val)
            if cleaned:
                out[key] = cleaned
        else:
            out[key] = val

    intent = (out.get("intent") or payload.get("intent") or "").upper()
    if intent in {"BUY", "SELL"}:
        out["intent"] = intent
    cat = normalize_vehicle_category(out.get("category") or out.get("type") or "")
    if cat:
        out["category"] = cat
        out["type"] = cat
    return out


def build_full_info(db: Session, conv: AiConversation, payload: dict | None = None) -> dict[str, Any]:
    """Full confirmation card: filtered vehicle + seller + photos."""
    from .tools import _payload

    raw = payload if isinstance(payload, dict) else _payload(conv)
    filtered = filter_collected(raw)
    name, phone = seller_fields(db, conv, None, raw)
    photos = unique_photo_ids(db, conv, raw)
    vehicle = " ".join(str(x) for x in [filtered.get("brand"), filtered.get("model")] if x).strip()
    rate = filtered.get("expected_price") or filtered.get("budget") or filtered.get("budget_max") or ""
    loc = filtered.get("state") or filtered.get("location") or ""
    if filtered.get("state") and filtered.get("city") and filtered.get("city") != filtered.get("state"):
        loc = f"{filtered.get('state')} / {filtered.get('city')}"

    data = {
        "card": filtered.get("active_card_id") or raw.get("active_card_id") or "",
        "vehicle": vehicle,
        "category": filtered.get("category") or "",
        "year": filtered.get("year") or "",
        "rate": rate,
        "location": loc,
        "state": filtered.get("state") or "",
        "city": filtered.get("city") or filtered.get("location") or "",
        "running": filtered.get("running")
        or filtered.get("running_km")
        or filtered.get("operating_hours")
        or "",
        "owners": filtered.get("owners") or "",
        "finance_amount": filtered.get("finance_amount") or "",
        "finance_condition": filtered.get("finance_condition") or "",
        "tyre_percent": filtered.get("tyre_percent") or "",
        "work_issues": filtered.get("work_issues") or "",
        "condition": filtered.get("condition") or "",
        "description": filtered.get("description") or "",
        "phone": phone or conv.mobile,
        "name": name,
        "whatsapp": conv.mobile,
        "intent": (filtered.get("intent") or conv.intent or "").upper(),
        "photos": photos,
        "photo_count": len(photos),
        "filtered": filtered,
    }
    if not data["card"] and conv.draft_id:
        draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()
        if draft and draft.card_id:
            data["card"] = draft.card_id
    return {
        k: v
        for k, v in data.items()
        if v not in (None, "", [], 0) or k in {
            "card", "vehicle", "category", "year", "rate", "location", "phone", "photos", "filtered",
        }
    }


def filter_memory(db: Session, conv: AiConversation, payload: dict | None = None) -> FilteredListing:
    """chat_memory → data_filteration entry: readiness + full info."""
    from .tools import _payload

    raw = payload if isinstance(payload, dict) else _payload(conv)
    filtered = filter_collected(raw)
    miss = [m for m in missing_fields({**raw, **filtered}) if m not in {"customer_name", "photos"}]
    ready = is_collection_ready({**raw, **filtered})
    full = build_full_info(db, conv, {**raw, **filtered}) if ready else {
        "intent": (filtered.get("intent") or raw.get("intent") or "").upper(),
        "filtered": filtered,
        "missing": miss,
    }
    return FilteredListing(
        ready=ready,
        missing=miss,
        data=full,
        raw=raw,
        intent=(filtered.get("intent") or raw.get("intent") or "").upper(),
    )


def summary_text(data: dict, lang: str) -> str:
    """WhatsApp confirmation message from filtered full info."""
    lines = []
    if data.get("card"):
        lines.append(f"Card : {data['card']}")
    lines.extend(
        [
            f"Vehicle : {data.get('vehicle') or '—'}",
            f"Category : {data.get('category') or '—'}",
            f"Year : {data.get('year') or '—'}",
            f"Rate : {data.get('rate') or '—'}",
            f"Location : {data.get('location') or '—'}",
        ]
    )
    if data.get("running"):
        lines.append(f"Running : {data['running']}")
    if data.get("owners"):
        lines.append(f"Owners : {data['owners']}")
    if data.get("finance_amount"):
        lines.append(f"Finance : {data['finance_amount']}")
    if data.get("finance_condition"):
        lines.append(f"Finance condition : {data['finance_condition']}")
    if data.get("tyre_percent"):
        lines.append(f"Tyre : {data['tyre_percent']}")
    if data.get("work_issues"):
        lines.append(f"Work/issues : {data['work_issues']}")
    if data.get("condition"):
        lines.append(f"Condition : {data['condition']}")
    if data.get("phone"):
        lines.append(f"Phone : {data['phone']}")
    if data.get("name"):
        lines.append(f"Name : {data['name']}")
    n = data.get("photo_count") or len(data.get("photos") or [])
    if n:
        lines.append(f"Photos : {n}")
    return "\n".join(lines) + "\n\n" + t(lang, "confirm_ask")


def prepare_confirmation(db: Session, conv: AiConversation, lang: str) -> tuple[dict, str]:
    """Filter memory → store confirmation snapshot on conversation → return WA text.

    Writes back into chat_memory (payload) so user can Haan/Yes confirm.
    """
    from .tools import _payload, _write_payload

    result = filter_memory(db, conv)
    data = result.data if result.ready else build_full_info(db, conv, result.raw)
    payload = _payload(conv)
    filtered = filter_collected(payload)
    payload["awaiting_confirm"] = True
    payload["customer_confirmed"] = False
    payload["summary_json"] = {k: v for k, v in data.items() if k != "filtered"}
    payload["filtered_listing"] = filtered
    payload["data_status"] = "AWAITING_CONFIRMATION"
    conv.state = "AWAITING_CONFIRMATION"
    conv.error_message = "ask:confirm"
    _write_payload(conv, payload)
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first() if conv.draft_id else None
    if draft and draft.status == "CONFIRMED":
        draft.status = "COLLECTING"
        draft.confirmed_json = "{}"
    return data, summary_text(data, lang)
