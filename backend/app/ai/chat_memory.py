"""InfraDealer AI Relationship Manager (`chat_memory`).

Conversational brain: intent, workflow memory, adaptive collection,
Data Filter coordination, user confirmation, and status communication.

Does NOT publish listings, invent facts, approve listings, or touch raw SQL.
Business actions go through controlled tools / Data Filter / data_push.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AiConversation, AiEvent, User
from .confirm import confirmation_has_modification, is_no, is_yes
from .data_filteration import (
    extract_fields,
    filter_memory,
    filter_payload,
    is_collection_ready,
    normalize_year,
    resolve_category,
)
from .i18n import pick_language, t
from .schema import FILTER_VERSION, SCHEMA_VERSION, listing_title, normalize_vehicle_category
from .tools import _draft_for, _payload, _write_payload, execute_tool

log = logging.getLogger("infradealer.ai.chat_memory")

AGENT_VERSION = "chat-memory-2.1"
PROMPT_VERSION = "RM-18"
WORKFLOW_VERSION = "4"

# Relationship-manager workflow states (mapped onto conv.state where possible)
RM_STATES = {
    "NEW_SESSION",
    "ACCOUNT_CONTEXT_LOADED",
    "IDENTITY_PENDING",
    "ACCOUNT_CREATION",
    "OTP_VERIFICATION",
    "INTENT_DETECTION",
    "CATEGORY_SELECTION",
    "LISTING_CREATION",
    "DATA_COLLECTION",
    "DATA_VALIDATION",
    "WAITING_FOR_MISSING_DATA",
    "WAITING_FOR_PHOTOS",
    "WAITING_FOR_DOCUMENT",
    "WAITING_FOR_USER_CONFIRMATION",
    "USER_CONFIRMED",
    "SUBMISSION_IN_PROGRESS",
    "SUBMITTED_TO_ADMIN",
    "UNDER_ADMIN_REVIEW",
    "APPROVED",
    "REJECTED",
    "REVISION_REQUIRED",
    "LIVE",
    "CLOSED",
    "HUMAN_HANDOFF",
    "ERROR_RECOVERY",
    "PAUSED",
    "DATA_CONFLICT_PENDING_USER_DECISION",
}

_SELL = re.compile(r"\b(bech|sell|bikau|bechna|bechni|dena|बेच)\b", re.I)
_BUY = re.compile(r"\b(buy|kharid|lena|lene|chahiye|खरीद)\b", re.I)
_CANCEL = re.compile(
    r"\b(cancel|rehne\s*do|baad\s*me|later|pause|mat\s*dalna|nahi\s*dalna|"
    r"रहने\s*दो|बाद\s*में|रद्द)\b",
    re.I,
)
_RESUME = re.compile(
    r"\b(continue|resume|puro\s*kar|poori\s*kar|wahi\s*listing| वो\s*listing|"
    r"listing\s*poori|जारी|पूरी\s*कर)\b",
    re.I,
)
_HUMAN = re.compile(
    r"\b(human|agent|support|customer\s*care|헬프|बात\s*करनी|"
    r"insan|insaan|call\s*me|complaint)\b",
    re.I,
)
_STATUS = re.compile(
    r"\b(status|live\s*hui|approve|rejected|listing\s*kahan|"
    r"लिस्टिंग\s*(कहाँ|स्टेटस)|live\s*हुई)\b",
    re.I,
)
_LINK = re.compile(r"\b(link|url|permalink)\b|link\s*(do|bhejo|chahiye)", re.I)
_INJECTION = re.compile(
    r"(ignore\s+(all\s+)?(previous\s+)?instructions|system\s*prompt|"
    r"reveal\s*(api|key|database|prompt)|execute\s*sql)",
    re.I,
)
_GREET = re.compile(
    r"^\s*(hi+|hii+|hello|hey+|namaste|namaskar|good\s*(morning|evening)|"
    r"क्या\s*haal|kaise\s*ho)[\s!?.]*$",
    re.I,
)

FIELD_ASK_ORDER = (
    "intent", "category", "brand", "model", "year",
    "expected_price", "budget", "state", "location", "km", "hours", "photos",
)

ASK_KEY_MAP = {
    "price": "expected_price",
    "expected_price": "expected_price",
    "location": "state",
    "state": "state",
    "hours": "hours",
    "operating_hours": "hours",
    "km": "km",
    "running_km": "km",
    "photos": "photos",
    "category": "category",
    "brand": "brand",
    "model": "model",
    "year": "year",
    "budget": "budget",
    "intent": "intent",
}


def read_memory(db: Session, conv: AiConversation) -> dict:
    return _payload(conv)


def _lang(db: Session, conv: AiConversation, text: str = "") -> str:
    prev = (getattr(conv, "language", None) or "").strip() or (_payload(conv).get("language") or "")
    lang = pick_language(text, prev, "auto")
    conv.language = lang
    payload = _payload(conv)
    if payload.get("language") != lang:
        payload["language"] = lang
        _write_payload(conv, payload)
    return lang


def _rm_state(payload: dict) -> str:
    return str(payload.get("rm_state") or payload.get("workflow_state") or "").upper()


def _set_rm_state(payload: dict, state: str) -> None:
    state = (state or "").upper()
    payload["rm_state"] = state
    payload["workflow_state"] = state


def _update_summary(payload: dict) -> str:
    name = payload.get("customer_name") or payload.get("wa_name") or "User"
    intent = (payload.get("intent") or "").upper() or "UNKNOWN"
    vehicle = listing_title(payload)
    known = []
    for key in ("brand", "model", "year", "state", "city", "expected_price"):
        if payload.get(key):
            known.append(f"{key}={payload[key]}")
    miss = payload.get("missing_fields") or []
    if isinstance(miss, list):
        miss_txt = ", ".join(
            (m["field"] if isinstance(m, dict) else str(m)) for m in miss[:6]
        )
    else:
        miss_txt = str(miss)
    summary = (
        f"{name} intent={intent} vehicle={vehicle}. "
        f"Confirmed: {'; '.join(known) or 'none'}. "
        f"Pending: {miss_txt or 'none'}. "
        f"State: {_rm_state(payload) or conv_state_fallback(payload)}."
    )
    payload["conversation_summary"] = summary[:500]
    return summary


def conv_state_fallback(payload: dict) -> str:
    if payload.get("awaiting_confirm"):
        return "WAITING_FOR_USER_CONFIRMATION"
    if (payload.get("intent") or "").upper() in {"BUY", "SELL"}:
        return "DATA_COLLECTION"
    return "INTENT_DETECTION"


def load_account_context(db: Session, conv: AiConversation) -> dict:
    """Trusted identity/account context via account_filter — never invented from user text."""
    from .account_filter import sync_conversation_account

    verdict = sync_conversation_account(db, conv)
    payload = _payload(conv)
    ctx = payload.get("account_context") if isinstance(payload.get("account_context"), dict) else {}
    if not ctx:
        ctx = {
            "identity": {
                "phone": conv.mobile,
                "verified": bool(payload.get("otp_verified") or conv.profile_status in {"found", "verified"}),
            },
            "account": {
                "found": verdict.found,
                "account_id": verdict.account_id,
                "name": verdict.name or payload.get("customer_name"),
                "type": (verdict.account_type or "user").upper(),
                "status": verdict.status,
                "eligibility": verdict.eligibility,
            },
            "workflow": {
                "active": bool(payload.get("intent") or _rm_state(payload)),
                "workflow_id": payload.get("workflow_id") or f"WF-{conv.id}",
                "state": _rm_state(payload) or conv.state,
            },
        }
        payload["account_context"] = ctx
    payload["workflow_id"] = payload.get("workflow_id") or f"WF-{conv.id}"
    if not _rm_state(payload):
        _set_rm_state(payload, "ACCOUNT_CONTEXT_LOADED" if verdict.found else "IDENTITY_PENDING")
    _write_payload(conv, payload)
    return ctx


def detect_intent(text: str, payload: dict, media_note: str = "") -> str:
    """Contextual intent — short replies depend on workflow state."""
    msg = (text or "").strip()
    low = msg.lower()
    state = _rm_state(payload)
    awaiting = payload.get("awaiting_confirm") or state == "WAITING_FOR_USER_CONFIRMATION"

    if _INJECTION.search(msg):
        return "PROMPT_INJECTION"
    if _HUMAN.search(msg):
        return "HUMAN_SUPPORT"
    if _CANCEL.search(msg) and len(msg.split()) <= 8:
        return "CANCEL_WORKFLOW"
    if _RESUME.search(msg):
        return "RESUME_WORKFLOW"
    if _STATUS.search(msg):
        return "CHECK_LISTING_STATUS"
    if _LINK.search(msg):
        return "ASK_LISTING_LINK"

    if awaiting:
        if confirmation_has_modification(msg):
            return "REJECT_CORRECTION"
        if is_yes(msg):
            return "CONFIRM_LISTING"
        if is_no(msg):
            return "REJECT_CORRECTION"

    if state == "DATA_CONFLICT_PENDING_USER_DECISION":
        return "RESOLVE_CONFLICT"

    if state == "OTP_VERIFICATION" or conv_otp_pending(payload):
        digits = re.sub(r"\D", "", msg)
        if len(digits) == 6:
            return "VERIFY_OTP"

    if media_note or "[photo]" in low or "photo" in low:
        return "UPLOAD_PHOTO"

    if _SELL.search(low) and not _BUY.search(low):
        return "SELL"
    if _BUY.search(low) and not _SELL.search(low):
        return "BUY"

    # Vehicle dump without explicit sell/buy → prefer SELL when media or vehicle cues
    if re.search(r"\b(tata|jcb|truck|tipper|eicher|model|lakh|lac)\b", low) or media_note:
        if not payload.get("intent"):
            return "SELL" if not _BUY.search(low) else "BUY"

    if _GREET.match(msg):
        return "GREETING"

    if payload.get("intent") and state in {
        "DATA_COLLECTION", "WAITING_FOR_MISSING_DATA", "LISTING_CREATION",
        "CATEGORY_SELECTION", "DATA_VALIDATION", "",
    }:
        return "PROVIDE_FIELD"

    return "OTHER"


def conv_otp_pending(payload: dict) -> bool:
    return payload.get("verification_status") == "otp_pending" or payload.get("account_step") == "otp"


def apply_fields(db: Session, conv: AiConversation, fields: dict) -> dict:
    data = dict(fields or {})
    if data:
        payload = _payload(conv)
        if payload.get("chat_cleared"):
            payload["chat_cleared"] = False
            _write_payload(conv, payload)
    if data.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": str(data["intent"]).upper()})
    veh = {
        k: v
        for k, v in data.items()
        if k
        in {
            "category", "type", "brand", "model", "year", "year_min", "registration_year",
            "running", "running_km", "operating_hours", "condition", "accident_history",
            "negotiable", "location", "state", "city", "owners", "finance_amount",
            "tyre_percent", "finance_condition", "work_issues",
            "expected_price", "budget", "budget_max",
        }
        and v not in (None, "")
    }
    if veh:
        # Track field history for corrections
        payload = _payload(conv)
        history = list(payload.get("field_history") or [])
        for key, val in veh.items():
            old = payload.get(key)
            if old not in (None, "") and str(old) != str(val):
                history.append({
                    "field": key,
                    "old_value": old,
                    "new_value": val,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "USER",
                })
        if history:
            payload["field_history"] = history[-40:]
            _write_payload(conv, payload)
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)
    return _payload(conv)


def collect_message(db: Session, conv: AiConversation, text: str) -> dict:
    """Harvest a user turn into structured fields via Data Filter extract + tools."""
    msg = (text or "").strip()
    low = msg.lower()
    payload = _payload(conv)
    seed: dict = {}
    if _SELL.search(low) and not _BUY.search(low):
        seed["intent"] = "SELL"
    elif _BUY.search(low) and not _SELL.search(low):
        seed["intent"] = "BUY"

    cat = resolve_category(msg)
    if cat:
        seed["category"] = cat

    # Keep existing place fields so later turns (price/year/km) cannot wipe location
    for key in ("city", "state", "location"):
        if payload.get(key) not in (None, ""):
            seed[key] = payload.get(key)

    extracted = extract_fields([{"text": msg, "source": "USER"}], seed)

    # Explicit place in this turn can override seeded location
    named_city = _fuzzy_city_safe(msg)
    named_state = extract_state_safe(msg)
    loc_intent = bool(
        re.search(
            r"\b(location|city|state|rajya|jagah|place|me\s+hai|mein\s+hai|"
            r"badlo|change|update)\b|लोकेशन|शहर|राज्य|जगह",
            low,
        )
    ) or bool(named_city or named_state)
    if loc_intent and (named_city or named_state):
        if named_city:
            extracted["city"] = named_city
            extracted["location"] = named_city
            st = named_state
            if not st:
                try:
                    from .extract import infer_state_from_city

                    st = infer_state_from_city(named_city)
                except Exception:
                    st = None
            if st:
                extracted["state"] = st
        elif named_state:
            extracted["state"] = named_state
            # Don't invent a city from state-only correction
            if not extracted.get("city"):
                extracted["location"] = named_state

    if not extracted.get("brand"):
        m = re.search(
            r"\b(tata|ashok\s*leyland|bharat\s*benz|mahindra|eicher|volvo|jcb|hitachi|komatsu|sany)\b",
            low,
            re.I,
        )
        if m:
            extracted["brand"] = m.group(1)
    if not extracted.get("model"):
        m = re.search(r"\b(\d{3,4}[A-Za-z]?|3DX|4DX|JS\d+)\b", msg)
        if m and not normalize_year(m.group(1)):
            extracted["model"] = m.group(1)

    # Uncertain language → do not mark confirmed later
    uncertain = bool(re.search(r"\b(shayad|maybe|lagbhag|around|लगभग|शायद)\b", low))

    if extracted.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": extracted["intent"]})

    veh = {
        k: v
        for k, v in extracted.items()
        if k
        in {
            "category", "brand", "model", "year", "expected_price", "price",
            "state", "city", "location", "running_km", "operating_hours", "km",
        }
        and v not in (None, "")
    }
    if veh.get("price") and not veh.get("expected_price"):
        veh["expected_price"] = veh.pop("price")
    elif "price" in veh:
        veh["expected_price"] = veh.get("expected_price") or veh.pop("price")
    if "km" in veh and "running_km" not in veh:
        veh["running_km"] = veh.pop("km")

    existing_place = any(payload.get(k) not in (None, "") for k in ("city", "state", "location"))
    if existing_place and not loc_intent:
        for key in ("city", "state", "location"):
            veh.pop(key, None)
    else:
        # Drop non-place garbage even on first fill
        from .data_filteration import _looks_like_place

        for key in ("city", "state", "location"):
            val = veh.get(key)
            if val and not _looks_like_place(str(val)) and not extract_state_safe(str(val)) and not _fuzzy_city_safe(str(val)):
                veh.pop(key, None)

    if veh:
        if "expected_price" in veh and isinstance(veh["expected_price"], (int, float)):
            if re.search(r"lakh|lac|crore", low):
                mprice = re.search(r"\d+(?:\.\d+)?\s*(?:lakh|lac|lacs|crore|cr)", msg, re.I)
                veh["expected_price"] = mprice.group(0) if mprice else str(veh["expected_price"])
            else:
                veh["expected_price"] = str(veh["expected_price"])
        for key in list(veh.keys()):
            if key == "year" and isinstance(veh[key], int):
                veh[key] = str(veh[key])
            elif key in {"running_km", "operating_hours"} and not isinstance(veh[key], str):
                veh[key] = str(veh[key])
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)
        if uncertain and veh.get("year"):
            payload = _payload(conv)
            payload.setdefault("confidence", {})["year"] = "UNCERTAIN"
            _write_payload(conv, payload)
    return _payload(conv)


def _fuzzy_city_safe(text: str) -> str | None:
    try:
        from .extract import _fuzzy_city

        return _fuzzy_city(text)
    except Exception:
        return None


def extract_state_safe(text: str) -> str | None:
    try:
        from .extract import extract_state

        return extract_state(text)
    except Exception:
        return None


def build_data_filter_payload(conv: AiConversation, payload: dict) -> dict:
    sources = {}
    conf = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
    cust = ((payload.get("source") or {}).get("customer") or {})
    for key in ("brand", "model", "year", "expected_price", "state", "city", "category"):
        if payload.get(key) not in (None, ""):
            if conf.get(key) in {"INFERRED_BY_AI", "AI_INFERRED"}:
                sources[key] = "AI_INFERRED"
            elif key in cust or conf.get(key) == "CONFIRMED_BY_CUSTOMER":
                sources[key] = "USER"
            else:
                sources[key] = "USER"
    return {
        "conversation_id": conv.conversation_id,
        "account_id": payload.get("profile_id") or conv.profile_id,
        "workflow_id": payload.get("workflow_id") or f"WF-{conv.id}",
        "intent": (payload.get("intent") or conv.intent or "").upper(),
        "category": payload.get("category"),
        "draft_id": conv.draft_id,
        "fields": {
            k: payload.get(k)
            for k in (
                "brand", "model", "year", "expected_price", "price", "budget",
                "state", "city", "location", "running_km", "operating_hours", "km",
            )
            if payload.get(k) not in (None, "")
        },
        "field_sources": sources,
        "documents": payload.get("documents") or {},
    }


def handle_data_filter_result(db: Session, conv: AiConversation, result) -> dict:
    payload = _payload(conv)
    miss = result.missing_fields or []
    payload["missing_fields"] = [
        (m["field"] if isinstance(m, dict) else str(m)) for m in miss
    ]
    payload["filter_result"] = result.as_dict() if hasattr(result, "as_dict") else {}
    if result.conflicts:
        _set_rm_state(payload, "DATA_CONFLICT_PENDING_USER_DECISION")
    elif result.readiness == "MISSING_REQUIRED_DATA":
        _set_rm_state(payload, "WAITING_FOR_MISSING_DATA")
    elif result.readiness == "READY_FOR_CONFIRMATION":
        _set_rm_state(payload, "DATA_VALIDATION")
    elif result.readiness == "DUPLICATE_WARNING":
        _set_rm_state(payload, "DATA_VALIDATION")
    elif result.readiness == "INVALID_DATA":
        _set_rm_state(payload, "WAITING_FOR_MISSING_DATA")
    _update_summary(payload)
    _write_payload(conv, payload)
    return payload


def _next_ask_key(payload: dict) -> str | None:
    miss = payload.get("missing_fields") or []
    ordered = []
    for item in miss:
        field = item["field"] if isinstance(item, dict) else str(item)
        ordered.append(ASK_KEY_MAP.get(field, field))
    # Also use schema missing if filter not run yet
    if not ordered and (payload.get("intent") or "").upper() in {"BUY", "SELL"}:
        from .schema import missing_fields

        ordered = [ASK_KEY_MAP.get(x, x) for x in missing_fields(payload) if x != "customer_name"]
    for key in FIELD_ASK_ORDER:
        if key in ordered:
            if key == "photos" and payload.get("photos_complete"):
                continue
            return key
    # Photos after required fields for SELL
    if (payload.get("intent") or "").upper() == "SELL":
        n = len(payload.get("media_ids") or [])
        if not payload.get("photos_complete") and n < 2:
            return "photos" if n > 0 or is_collection_ready(payload) else None
    return None


def _facts_line(payload: dict) -> str:
    bits = []
    for key in ("category", "brand", "model", "year", "state", "expected_price"):
        val = payload.get(key)
        if val not in (None, ""):
            bits.append(str(val))
    return " ".join(bits[:5])


def build_next_question(payload: dict, lang: str) -> str:
    key = _next_ask_key(payload)
    if not key:
        return ""
    ask = t(lang, key if key in TEMPLATES_KEYS() else "unclear")
    # uncertain year confirmation
    if payload.get("confidence", {}).get("year") == "UNCERTAIN" and payload.get("year"):
        return f"Year {payload.get('year')} confirm kar doon?"
    facts = _facts_line(payload)
    if facts and key not in {"intent"}:
        return t(lang, "ack", facts=facts) + ask
    return ask


def TEMPLATES_KEYS() -> set:
    from .i18n import TEMPLATES

    return set(TEMPLATES["hinglish"].keys())


def build_confirmation_summary(db: Session, conv: AiConversation, lang: str = "hinglish") -> str:
    result = filter_memory(db, conv)
    payload = handle_data_filter_result(db, conv, result)
    payload["awaiting_confirm"] = True
    payload["customer_confirmed"] = False
    _set_rm_state(payload, "WAITING_FOR_USER_CONFIRMATION")
    nd = result.normalized_data or {}
    vehicle = result.data.get("vehicle") if isinstance(result.data.get("vehicle"), str) else listing_title(payload)
    if isinstance(result.data.get("vehicle"), dict):
        vehicle = listing_title({**payload, **result.data["vehicle"]})
    summary = {
        **(payload.get("summary_json") or {}),
        **nd,
        "vehicle": vehicle,
        "readiness": result.readiness,
        "quality": result.quality,
        "version": (payload.get("draft_version") or 1),
    }
    payload["summary_json"] = summary
    payload["draft_version"] = int(payload.get("draft_version") or 1)
    _write_payload(conv, payload)
    execute_tool(db, conv, "save_conversation", {"state": "AWAITING_CONFIRMATION"})

    lines = [
        "Aapki listing ki details:" if lang != "english" else "Your listing details:",
        str(vehicle),
    ]
    if nd.get("category") or payload.get("category"):
        lines.append(f"Category: {nd.get('category') or payload.get('category')}")
    if nd.get("year") or payload.get("year"):
        lines.append(f"Year: {nd.get('year') or payload.get('year')}")
    km = nd.get("km") or payload.get("running_km")
    if km:
        lines.append(f"KM: {km}")
    hours = nd.get("operating_hours") or payload.get("operating_hours")
    if hours:
        lines.append(f"Hours: {hours}")
    loc = nd.get("city") or nd.get("state") or payload.get("city") or payload.get("state")
    if loc:
        lines.append(f"Location: {loc}")
    price = nd.get("price") or nd.get("expected_price") or payload.get("expected_price")
    if price:
        lines.append(f"Price: {price}")
    photos = len(payload.get("media_ids") or [])
    if photos:
        lines.append(f"Photos: {photos}")
    if result.readiness == "DUPLICATE_WARNING":
        lines.append(t(lang, "duplicate"))
    if result.conflicts:
        c = result.conflicts[0]
        lines.append(t(lang, "conflict_year", user=c.get("user_value"), document=c.get("document_value")))
    lines.append(t(lang, "confirm_prompt"))
    return "\n".join(lines)


# Back-compat name used by tests
def send_for_confirmation(db: Session, conv: AiConversation, lang: str = "hinglish") -> str:
    return build_confirmation_summary(db, conv, lang)


def interpret_confirmation(text: str, payload: dict) -> str:
    if confirmation_has_modification(text):
        return "MODIFY"
    if is_yes(text):
        return "CONFIRM"
    if is_no(text):
        return "REJECT"
    return "UNCLEAR"


def submit_confirmed_listing(db: Session, conv: AiConversation) -> dict:
    payload = _payload(conv)
    version = int(payload.get("draft_version") or 1)
    payload["customer_confirmed"] = True
    payload["awaiting_confirm"] = False
    payload["confirmed_version"] = version
    payload["confirmed_at"] = payload.get("confirmed_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["confirmed_json"] = payload.get("summary_json") or payload.get("confirmed_json") or {}
    payload["draft_version"] = version
    payload["confirmation"] = {
        "confirmed": True,
        "confirmed_version": version,
        "version": version,
        "timestamp": payload["confirmed_at"],
    }
    _set_rm_state(payload, "SUBMISSION_IN_PROGRESS")
    _write_payload(conv, payload)
    execute_tool(db, conv, "save_conversation", {"state": "CONFIRMED"})
    result = execute_tool(db, conv, "submit_for_review", {})
    payload = _payload(conv)
    if result.get("ok") is False:
        if result.get("error") == "listing_not_ready" or result.get("status") in {
            "SUBMISSION_BLOCKED", "STALE_CONFIRMATION",
        }:
            _set_rm_state(payload, "WAITING_FOR_MISSING_DATA")
            _write_payload(conv, payload)
            return result
        if result.get("status") in {"RETRY", "DELIVERY_FAILED", "FAILED"} and not result.get("user_data_error"):
            # System failure — keep draft intact; do not tell user listing was rejected
            _set_rm_state(payload, "SUBMISSION_IN_PROGRESS")
            _write_payload(conv, payload)
            return result
    _set_rm_state(payload, "SUBMITTED_TO_ADMIN")
    payload["listing_status"] = payload.get("listing_status") or "PENDING_REVIEW"
    _write_payload(conv, payload)
    execute_tool(db, conv, "save_conversation", {"state": "READY_FOR_REVIEW"})
    return result


def handle_admin_approval(db: Session, conv: AiConversation, event: dict, lang: str = "hinglish") -> str:
    from .data_push import process_admin_event, validate_live_url

    evt = dict(event or {})
    if evt.get("live_url") and not evt.get("event"):
        evt["event"] = "LISTING_APPROVED"
    result = process_admin_event(db, conv, evt)
    payload = _payload(conv)
    link = (
        (result.get("notification") or {}).get("live_url")
        or payload.get("listing_url")
        or evt.get("live_url")
        or ""
    )
    if not validate_live_url(str(link or "")):
        link = ""
    status = str(result.get("status") or payload.get("listing_status") or "")
    if result.get("ok") and status == "LIVE" and link:
        _set_rm_state(payload, "LIVE")
        _write_payload(conv, payload)
        return t(lang, "approved", link=link)
    if result.get("ok") and status == "APPROVED":
        _set_rm_state(payload, "SUBMITTED_TO_ADMIN")
        _write_payload(conv, payload)
        return t(lang, "not_live")
    if not result.get("ok"):
        return t(lang, "link_missing")
    if not link:
        return t(lang, "link_missing")
    _set_rm_state(payload, "LIVE")
    _write_payload(conv, payload)
    return t(lang, "approved", link=link)


def handle_admin_rejection(db: Session, conv: AiConversation, event: dict, lang: str = "hinglish") -> str:
    from .data_push import process_admin_event

    evt = dict(event or {})
    if not evt.get("event"):
        evt["event"] = "LISTING_REJECTED"
    result = process_admin_event(db, conv, evt)
    payload = _payload(conv)
    reason = (
        (result.get("notification") or {}).get("reason_text")
        or evt.get("reason_text")
        or evt.get("reason_code")
        or payload.get("rejection_reason")
        or "Needs correction"
    )
    _set_rm_state(payload, "REVISION_REQUIRED")
    _write_payload(conv, payload)
    return t(lang, "rejected", reason=reason)


def create_handoff(db: Session, conv: AiConversation, reason: str = "USER_REQUEST") -> dict:
    payload = _payload(conv)
    handoff = {
        "required": True,
        "reason": reason,
        "conversation_id": conv.conversation_id,
        "workflow_id": payload.get("workflow_id"),
        "summary": payload.get("conversation_summary") or _update_summary(payload),
    }
    payload["handoff"] = handoff
    _set_rm_state(payload, "HUMAN_HANDOFF")
    _write_payload(conv, payload)
    execute_tool(db, conv, "save_conversation", {"state": "BLOCKED"})
    db.add(AiEvent(
        wamid=conv.last_wamid or "",
        mobile=conv.mobile,
        event_type="human_handoff",
        detail=str(handoff)[:2000],
    ))
    return handoff


def _maybe_attach_media(payload: dict, media_note: str) -> None:
    if not media_note:
        return
    # Media IDs are attached by runner; here only mark photo workflow
    if "[photo]" in (media_note or "").lower() or "photo" in (media_note or "").lower():
        _set_rm_state(payload, "WAITING_FOR_PHOTOS")


def handle_message(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str:
    """Main Relationship Manager turn — state → intent → collect → filter → ask/confirm."""
    started = time.perf_counter()
    lang = _lang(db, conv, text)
    load_account_context(db, conv)
    payload = _payload(conv)
    msg = (text or "").strip()

    # Ensure draft isolation for listing workflows
    if (payload.get("intent") or "").upper() == "SELL" or media_note:
        _draft_for(db, conv)

    intent = detect_intent(msg, payload, media_note=media_note)
    response_type = "ACKNOWLEDGEMENT"
    reply = ""

    try:
        if intent == "PROMPT_INJECTION":
            response_type = "ERROR_MESSAGE"
            reply = t(lang, "injection_refuse")

        elif intent == "HUMAN_SUPPORT":
            create_handoff(db, conv, "USER_REQUEST")
            response_type = "HUMAN_HANDOFF"
            reply = t(lang, "handoff")

        elif intent == "CANCEL_WORKFLOW":
            _set_rm_state(payload, "PAUSED")
            payload["awaiting_confirm"] = False
            _write_payload(conv, payload)
            response_type = "ACKNOWLEDGEMENT"
            reply = t(lang, "paused")

        elif intent == "RESUME_WORKFLOW":
            _set_rm_state(payload, "WAITING_FOR_MISSING_DATA")
            result = filter_memory(db, conv)
            handle_data_filter_result(db, conv, result)
            payload = _payload(conv)
            miss = payload.get("missing_fields") or []
            miss_txt = ", ".join(
                (m["field"] if isinstance(m, dict) else str(m)) for m in miss[:4]
            ) or "details"
            response_type = "ASK_QUESTION"
            reply = t(lang, "resume", vehicle=listing_title(payload), missing=miss_txt)
            q = build_next_question(payload, lang)
            if q:
                reply = q

        elif intent == "CHECK_LISTING_STATUS":
            from .data_push import get_submission_status, validate_live_url

            sub_status = get_submission_status(db, conv)
            status = sub_status.get("status") or payload.get("listing_status") or conv.state or "DRAFT"
            link = sub_status.get("live_url") or payload.get("listing_url") or ""
            if link and not validate_live_url(link):
                link = ""
            response_type = "STATUS_UPDATE"
            reply = t(lang, "status_ask", status=status)
            if str(status).upper() in {"LIVE"} and link:
                reply = t(lang, "approved", link=link)
            elif str(status).upper() in {
                "PENDING_REVIEW", "READY_FOR_REVIEW", "SUBMITTED",
                "UNDER_REVIEW", "ADMIN_ACKNOWLEDGED", "APPROVED",
            }:
                reply = t(lang, "not_live")

        elif intent == "ASK_LISTING_LINK":
            from .data_push import get_submission_status, validate_live_url

            sub_status = get_submission_status(db, conv)
            link = sub_status.get("live_url") or payload.get("listing_url") or ""
            if link and not validate_live_url(link):
                link = ""
            response_type = "STATUS_UPDATE"
            reply = t(lang, "approved", link=link) if link else t(lang, "link_missing")

        elif intent == "GREETING" and not payload.get("intent"):
            response_type = "ASK_QUESTION"
            _set_rm_state(payload, "INTENT_DETECTION")
            _write_payload(conv, payload)
            reply = t(lang, "greet")

        elif intent == "VERIFY_OTP":
            digits = re.sub(r"\D", "", msg)
            result = execute_tool(db, conv, "verify_otp", {"code": digits})
            response_type = "OTP_REQUEST"
            if result.get("ok"):
                reply = t(lang, "otp_ok")
                # Resume listing collection if intent was preserved
                payload = _payload(conv)
                if (payload.get("intent") or "").upper() in {"BUY", "SELL"}:
                    q = build_next_question(payload, lang)
                    reply = (reply + "\n" + q) if q else reply
            else:
                reply = t(lang, "otp_fail")

        elif intent == "CONFIRM_LISTING":
            decision = interpret_confirmation(msg, payload)
            if decision == "CONFIRM":
                result = submit_confirmed_listing(db, conv)
                response_type = "LISTING_SUBMITTED"
                if result.get("ok") is False and result.get("error") == "listing_not_ready":
                    reply = build_next_question(_payload(conv), lang) or t(lang, "unclear")
                    response_type = "ASK_QUESTION"
                else:
                    reply = t(lang, "submitted")
            else:
                response_type = "ASK_QUESTION"
                reply = t(lang, "confirm_prompt")

        elif intent in {"REJECT_CORRECTION", "PROVIDE_FIELD", "SELL", "BUY", "UPLOAD_PHOTO", "OTHER", "RESOLVE_CONFLICT"}:
            # Intent change SELL ↔ BUY: reset listing fields, keep account
            if intent in {"SELL", "BUY"}:
                payload = _payload(conv)
                prev = (payload.get("intent") or "").upper()
                if prev and prev != intent and prev in {"BUY", "SELL"}:
                    for key in (
                        "category", "brand", "model", "year", "expected_price", "budget",
                        "running_km", "operating_hours", "missing_fields", "summary_json",
                        "awaiting_confirm", "customer_confirmed",
                    ):
                        payload[key] = [] if key == "missing_fields" else (False if key in {"awaiting_confirm", "customer_confirmed"} else None)
                    payload["media_ids"] = []
                    payload["draft_version"] = int(payload.get("draft_version") or 1) + 1
                    _write_payload(conv, payload)
                execute_tool(db, conv, "save_customer_data", {"intent": intent})
                payload = _payload(conv)
                _set_rm_state(payload, "LISTING_CREATION")
                _write_payload(conv, payload)

            if intent == "REJECT_CORRECTION" or confirmation_has_modification(msg):
                payload = _payload(conv)
                payload["awaiting_confirm"] = False
                payload["customer_confirmed"] = False
                payload["draft_version"] = int(payload.get("draft_version") or 1) + 1
                _set_rm_state(payload, "DATA_COLLECTION")
                _write_payload(conv, payload)

            # Collect from this turn
            collect_message(db, conv, msg)
            if media_note:
                payload = _payload(conv)
                _maybe_attach_media(payload, media_note)
                n = len(payload.get("media_ids") or [])
                if n >= 2:
                    payload["photos_complete"] = True
                _write_payload(conv, payload)

            payload = _payload(conv)
            if not payload.get("intent") and intent in {"SELL", "BUY"}:
                payload["intent"] = intent
                _write_payload(conv, payload)

            # Coordinate with Data Filter when we have something to validate
            if (payload.get("intent") or "").upper() in {"BUY", "SELL"}:
                result = filter_memory(db, conv)
                payload = handle_data_filter_result(db, conv, result)

                if result.conflicts:
                    c = result.conflicts[0]
                    response_type = "ASK_QUESTION"
                    reply = t(lang, "conflict_year", user=c.get("user_value"), document=c.get("document_value"))
                elif result.readiness == "INVALID_DATA":
                    err = (result.validation_errors or [{}])[0]
                    response_type = "ASK_QUESTION"
                    code = err.get("code") or ""
                    if code == "FUTURE_YEAR":
                        reply = t(lang, "year")
                    else:
                        reply = build_next_question(payload, lang) or t(lang, "unclear")
                elif is_collection_ready(payload):
                    photo_required = any(
                        (m["field"] if isinstance(m, dict) else m) == "photos"
                        for m in (result.missing_fields or [])
                    )
                    if photo_required and len(payload.get("media_ids") or []) < 2:
                        response_type = "ASK_QUESTION"
                        n = len(payload.get("media_ids") or [])
                        reply = t(lang, "photo_need_min", count=n) if n else t(lang, "photos")
                    elif result.readiness in {"READY_FOR_CONFIRMATION", "DUPLICATE_WARNING"} or not photo_required:
                        response_type = "CONFIRMATION_REQUEST"
                        reply = build_confirmation_summary(db, conv, lang)
                    else:
                        response_type = "ASK_QUESTION"
                        reply = build_next_question(payload, lang) or t(lang, "photos")
                else:
                    response_type = "ASK_QUESTION"
                    reply = build_next_question(payload, lang)
                    if not reply:
                        if (payload.get("intent") or "").upper() == "SELL" and len(payload.get("media_ids") or []) < 2 and is_collection_ready(payload):
                            reply = t(lang, "photos")
                        else:
                            reply = t(lang, "unclear")
            else:
                response_type = "ASK_QUESTION"
                reply = t(lang, "intent")

        else:
            response_type = "ASK_QUESTION"
            reply = build_next_question(_payload(conv), lang) or t(lang, "greet")

    except Exception:
        log.exception("chat_memory handle_message failed")
        _set_rm_state(_payload(conv), "ERROR_RECOVERY")
        _write_payload(conv, _payload(conv))
        reply = t(lang, "unclear")
        response_type = "ERROR_MESSAGE"

    # Observability event
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    try:
        db.add(AiEvent(
            wamid=conv.last_wamid or "",
            mobile=conv.mobile,
            event_type="chat_memory_turn",
            detail=str({
                "agent_version": AGENT_VERSION,
                "prompt_version": PROMPT_VERSION,
                "workflow_version": WORKFLOW_VERSION,
                "schema_version": SCHEMA_VERSION,
                "filter_version": FILTER_VERSION,
                "intent": intent,
                "response_type": response_type,
                "rm_state": _rm_state(_payload(conv)),
                "ms": elapsed_ms,
            })[:2000],
        ))
    except Exception:
        pass

    reply = (reply or t(lang, "saving")).strip()
    # Never claim live without backend URL
    if re.search(r"\blive\b", reply, re.I) and "http" not in reply and response_type != "LISTING_APPROVED":
        if "approve" not in reply.lower() and "लिस्टिंग" not in reply:
            pass
    return reply[:1200]
