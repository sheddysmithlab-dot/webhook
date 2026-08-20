import json
import re

SELL_ASK = ["category", "brand", "model", "year", "expected_price", "state"]
BUY_ASK = ["category", "brand", "budget", "state"]
HUMAN_ONLY_STATUS = {"POSTED", "APPROVED", "LIVE", "published", "PUBLISHED"}
ALLOWED_STATES = {
    "NEW", "NEW_CHAT", "INTENT_PENDING", "INTENT_DETECTED",
    "BUY_DATA_COLLECTION", "SELL_DATA_COLLECTION", "DATA_COLLECTING",
    "PROFILE_CHECKING", "PROFILE_FOUND", "PROFILE_NOT_FOUND",
    "OTP_PENDING", "OTP_VERIFIED", "PROFILE_VERIFIED", "PROFILE_CREATED",
    "DATA_INCOMPLETE", "DATA_COMPLETE", "AWAITING_CONFIRMATION", "AWAITING_VEHICLE_CHOICE", "CONFIRMED",
    "READY_FOR_REVIEW", "COMPLETED", "ERROR", "BLOCKED",
}
INTENTS = {"BUY", "SELL", "GENERAL_ENQUIRY", "SUPPORT", "EXISTING_LISTING_QUERY", "PROFILE_QUERY", "UNKNOWN"}
VEHICLE_CATEGORIES = (
    "Truck", "Dumper", "Tipper", "Crane", "Poclain", "Loader",
    "Backhoe Loader", "JCB", "Excavator", "Grader", "Crusher", "Other",
)
_CAT_ALIASES = {
    "truck": "Truck", "truk": "Truck", "lorry": "Truck", "lory": "Truck",
    "ट्रक": "Truck",
    "dumper": "Dumper", "dump": "Dumper", "डम्पर": "Dumper", "डंपर": "Dumper",
    "tipper": "Tipper", "tiper": "Tipper", "tipar": "Tipper", "टिपर": "Tipper", "टिपर": "Tipper",
    "crane": "Crane", "crain": "Crane", "krane": "Crane", "क्रेन": "Crane",
    "poclain": "Poclain", "poclin": "Poclain", "pockland": "Poclain", "pocklain": "Poclain",
    "pokland": "Poclain", "pockalnd": "Poclain", "pocland": "Poclain", "poklain": "Poclain",
    "पोक्लेन": "Poclain", "पोकलैन": "Poclain",
    "loader": "Loader", "loder": "Loader", "लोडर": "Loader",
    "backhoe": "Backhoe Loader", "backhoe loader": "Backhoe Loader", "back hoe": "Backhoe Loader",
    "bokehloader": "Backhoe Loader", "bokeh loader": "Backhoe Loader", "bokhoe": "Backhoe Loader",
    "backholoader": "Backhoe Loader", "backhoeloader": "Backhoe Loader", "बैकहो": "Backhoe Loader",
    "jcb": "JCB", "जेसीबी": "JCB",
    "excavator": "Excavator", "excavatoer": "Excavator", "excavater": "Excavator",
    "एक्स्केवेटर": "Excavator", "एक्सावेटर": "Excavator",
    "grader": "Grader", "grder": "Grader", "ग्रेडर": "Grader",
    "crusher": "Crusher", "crucher": "Crusher", "crushar": "Crusher", "क्रशर": "Crusher",
    "other": "Other", "anya": "Other", "अन्य": "Other",
}


def normalize_vehicle_category(raw: str | None) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not text:
        return ""
    if text in {c.lower() for c in VEHICLE_CATEGORIES}:
        for c in VEHICLE_CATEGORIES:
            if c.lower() == text:
                return c
    if text in _CAT_ALIASES:
        return _CAT_ALIASES[text]
    for cue, label in sorted(_CAT_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", text):
            return label
    return ""


def empty_payload() -> dict:
    return {
        "intent": None,
        "language": None,
        "customer_name": None,
        "whatsapp_number": None,
        "profile_status": "none",
        "profile_id": None,
        "otp_verified": False,
        "category": None,
        "type": None,
        "brand": None,
        "model": None,
        "year": None,
        "year_min": None,
        "registration_year": None,
        "running": None,
        "running_km": None,
        "operating_hours": None,
        "condition": None,
        "accident_history": None,
        "negotiable": None,
        "location": None,
        "state": None,
        "city": None,
        "owners": None,
        "finance_amount": None,
        "tyre_percent": None,
        "finance_condition": None,
        "work_issues": None,
        "optional_asked": False,
        "optional_done": False,
        "account_step": "",
        "account_has_infra": None,
        "account_role": None,
        "account_password_set": False,
        "account_onboarded": False,
        "expected_price": None,
        "budget": None,
        "budget_max": None,
        "description": None,
        "contact_phone": None,
        "wa_name": None,
        "media_ids": [],
        "photos_complete": False,
        "photos_prompted": False,
        "active_card_id": None,
        "account_type": None,
        "account_label": None,
        "account_can_post": None,
        "account_reason": None,
        "account_buy_link": None,
        "skipped_asks": [],
        "missing_fields": [],
        "awaiting_confirm": False,
        "awaiting_vehicle_choice": False,
        "ai_introduced": False,
        "pending_vehicle": {},
        "pending_media_ids": [],
        "customer_confirmed": False,
        "summary_json": {},
        "confirmed_json": {},
        "verification_status": "unverified",
        "listing_status": None,
        "data_status": "INCOMPLETE",
        "confidence": {},
        "source": {"customer": {}, "inferred": {}, "backend": {}},
    }


def loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    base = empty_payload()
    if isinstance(data, dict):
        for key, val in data.items():
            if key in base:
                base[key] = val
        src = data.get("source") if isinstance(data.get("source"), dict) else {}
        base["source"] = {
            "customer": src.get("customer") if isinstance(src.get("customer"), dict) else {},
            "inferred": src.get("inferred") if isinstance(src.get("inferred"), dict) else {},
            "backend": src.get("backend") if isinstance(src.get("backend"), dict) else {},
        }
        conf = data.get("confidence") if isinstance(data.get("confidence"), dict) else {}
        base["confidence"] = conf
    return base


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _blank(val) -> bool:
    return val is None or str(val).strip() in {"", "null", "unknown", "None"}


def missing_fields(payload: dict) -> list[str]:
    intent = (payload.get("intent") or "").upper()
    if intent not in {"BUY", "SELL"}:
        payload["missing_fields"] = ["intent"]
        return payload["missing_fields"]
    keys = SELL_ASK if intent == "SELL" else BUY_ASK
    skipped = {str(x) for x in (payload.get("skipped_asks") or []) if x}
    miss = []
    for key in keys:
        if key in skipped:
            continue
        if key == "category":
            if not normalize_vehicle_category(payload.get("category") or payload.get("type") or ""):
                miss.append("category")
            continue
        if key == "budget":
            if _blank(payload.get("budget")) and _blank(payload.get("budget_max")):
                miss.append("budget")
            continue
        if key == "state":
            if _blank(payload.get("state")) and _blank(payload.get("location")):
                miss.append("state")
            continue
        if _blank(payload.get(key)):
            miss.append(key)
    payload["missing_fields"] = miss
    return miss


def review_gaps(payload: dict) -> list[str]:
    """Critical gaps that block listing JSON."""
    intent = (payload.get("intent") or "").upper()
    gaps = []
    if intent not in {"BUY", "SELL"}:
        return ["intent"]
    if intent == "SELL":
        if not normalize_vehicle_category(payload.get("category") or payload.get("type") or ""):
            gaps.append("category")
        if _blank(payload.get("brand")):
            gaps.append("brand")
        if _blank(payload.get("model")):
            gaps.append("model")
        if _blank(payload.get("year")):
            gaps.append("year")
        if _blank(payload.get("expected_price")):
            gaps.append("expected_price")
        if _blank(payload.get("state")) and _blank(payload.get("location")):
            gaps.append("state")
    else:
        if not normalize_vehicle_category(payload.get("category") or payload.get("type") or ""):
            gaps.append("category")
        if _blank(payload.get("budget")) and _blank(payload.get("budget_max")):
            gaps.append("budget")
        if _blank(payload.get("state")) and _blank(payload.get("location")):
            gaps.append("state")
    if not payload.get("customer_confirmed"):
        gaps.append("customer_confirmed")
    return gaps


def listing_title(payload: dict) -> str:
    brand = str(payload.get("brand") or "").strip()
    model = str(payload.get("model") or "").strip()
    category = normalize_vehicle_category(payload.get("category") or payload.get("type") or "") or str(payload.get("category") or "").strip()
    parts = []
    if brand:
        parts.append(brand)
    if model and model.lower() not in " ".join(parts).lower():
        parts.append(model)
    blob = " ".join(parts).lower()
    if category and category.lower() not in blob:
        parts.append(category)
    title = " ".join(parts)
    return (title or "InfraDealer enquiry")[:200]


def collection_state(intent: str) -> str:
    intent = (intent or "").upper()
    if intent == "BUY":
        return "BUY_DATA_COLLECTION"
    if intent == "SELL":
        return "SELL_DATA_COLLECTION"
    return "INTENT_PENDING"
