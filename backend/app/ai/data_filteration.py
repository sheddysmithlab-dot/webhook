"""InfraDealer Listing Intelligence / Data Filter agent.

Quality-control gate between conversation payload and listing submission.
Implements the Data_filter pipeline: extract → normalize → validate →
duplicate/media checks → confidence → canonical listing → readiness result.

Does NOT chat with users, publish listings, or invent missing values.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .schema import (
    FILTER_VERSION,
    SCHEMA_VERSION,
    category_schema,
    listing_title,
    missing_fields,
    normalize_vehicle_category,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from ..models import AiConversation

log = logging.getLogger("infradealer.ai.data_filter")

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def _parse_listing_price(raw: str) -> float:
    """Local price parser (avoids importing identity → services → missing i18n)."""
    text = (raw or "").strip()
    if not text:
        return 0.0
    low = text.lower()
    if _YEAR_RE.match(text):
        return 0.0
    looks = bool(re.search(r"lakh|lac|\bl\b|₹|rs\.?|crore|\bcr\b", low))
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return 0.0
    others = [n for n in nums if not _YEAR_RE.match(n)]
    if not others:
        return 0.0
    if not looks and all(re.fullmatch(r"\d{3,4}", n) for n in others):
        return 0.0
    try:
        price = float(others[0])
    except (TypeError, ValueError):
        return 0.0
    if price <= 0:
        return 0.0
    if "lakh" in low or "lac" in low or re.search(r"\b\d+(?:\.\d+)?\s*l\b", low):
        price *= 100000
    elif "crore" in low or re.search(r"\bcr\b", low):
        price *= 10000000
    if 1900 <= price <= 2035:
        return 0.0
    return price

FIELD_STATUSES = {
    "MISSING",
    "EXTRACTED",
    "NORMALIZED",
    "CONFIRMED",
    "UNCERTAIN",
    "CONFLICT",
    "INVALID",
    "INFERRED",
}

READINESS = {
    "READY_FOR_CONFIRMATION",
    "MISSING_REQUIRED_DATA",
    "INVALID_DATA",
    "CONFLICT_REQUIRES_USER",
    "DUPLICATE_WARNING",
    "PHOTO_ISSUE",
    "DOCUMENT_CONFLICT",
    "SYSTEM_ERROR",
}

SOURCE_RANK = {
    "VERIFIED_BACKEND": 1,
    "OFFICIAL_DOCUMENT": 2,
    "RC_DOCUMENT": 2,
    "USER_CONFIRMED": 3,
    "CONFIRMED_BY_CUSTOMER": 3,
    "USER": 4,
    "customer": 4,
    "STRUCTURED_LISTING": 5,
    "PHOTO_OCR": 6,
    "AI_INFERRED": 7,
    "INFERRED_BY_AI": 7,
    "inferred": 7,
}

_BRAND_ALIASES = {
    "tata": "Tata",
    "tata motors": "Tata",
    "टाटा": "Tata",
    "ashok leyland": "Ashok Leyland",
    "ashokleyland": "Ashok Leyland",
    "leyland": "Ashok Leyland",
    "bharat benz": "BharatBenz",
    "bharatbenz": "BharatBenz",
    "mahindra": "Mahindra",
    "eicher": "Eicher",
    "volvo": "Volvo",
    "jcb": "JCB",
    "जेसीबी": "JCB",
    "hitachi": "Hitachi",
    "komatsu": "Komatsu",
    "caterpillar": "Caterpillar",
    "cat": "Caterpillar",
    "sany": "SANY",
    "liugong": "LiuGong",
    "case": "CASE",
}

_CITY_STATE = {
    "indore": ("Indore", "Madhya Pradesh"),
    "indor": ("Indore", "Madhya Pradesh"),
    "bhopal": ("Bhopal", "Madhya Pradesh"),
    "jabalpur": ("Jabalpur", "Madhya Pradesh"),
    "gwalior": ("Gwalior", "Madhya Pradesh"),
    "ujjain": ("Ujjain", "Madhya Pradesh"),
    "mumbai": ("Mumbai", "Maharashtra"),
    "pune": ("Pune", "Maharashtra"),
    "nagpur": ("Nagpur", "Maharashtra"),
    "delhi": ("Delhi", "Delhi"),
    "jaipur": ("Jaipur", "Rajasthan"),
    "ahmedabad": ("Ahmedabad", "Gujarat"),
    "surat": ("Surat", "Gujarat"),
    "hyderabad": ("Hyderabad", "Telangana"),
    "chennai": ("Chennai", "Tamil Nadu"),
    "bangalore": ("Bengaluru", "Karnataka"),
    "bengaluru": ("Bengaluru", "Karnataka"),
    "kolkata": ("Kolkata", "West Bengal"),
    "lucknow": ("Lucknow", "Uttar Pradesh"),
    "kanpur": ("Kanpur", "Uttar Pradesh"),
}

HOUR_CATEGORIES = {
    "JCB", "Excavator", "Poclain", "Loader", "Crane", "Crusher",
    "Grader", "Backhoe Loader",
}

_PRICE_OUTLIER_MAX = 50_00_00_000  # 50 crore
_YEAR_MIN = 1970


@dataclass
class FilterResult:
    success: bool = True
    ready: bool = False
    readiness: str = "MISSING_REQUIRED_DATA"
    data: dict = field(default_factory=dict)
    normalized_data: dict = field(default_factory=dict)
    field_status: dict = field(default_factory=dict)
    missing_fields: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    validation_errors: list = field(default_factory=list)
    duplicate_check: dict = field(default_factory=lambda: {"possible_duplicate": False})
    quality: dict = field(default_factory=dict)
    confirmation: dict = field(default_factory=dict)
    photos: dict = field(default_factory=dict)
    request_id: str = ""
    draft: dict = field(default_factory=dict)
    classification: dict = field(default_factory=dict)
    filter_version: str = FILTER_VERSION
    schema_version: str = SCHEMA_VERSION
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "ready": self.ready,
            "readiness": self.readiness,
            "request_id": self.request_id,
            "draft": self.draft,
            "classification": self.classification,
            "normalized_data": self.normalized_data,
            "field_status": self.field_status,
            "missing_fields": self.missing_fields,
            "conflicts": self.conflicts,
            "validation_errors": self.validation_errors,
            "duplicate_check": self.duplicate_check,
            "quality": self.quality,
            "confirmation": self.confirmation,
            "photos": self.photos,
            "listing": self.data,
            "filter_version": self.filter_version,
            "schema_version": self.schema_version,
            "error": self.error,
        }


def _blank(val: Any) -> bool:
    return val is None or str(val).strip() in {"", "null", "unknown", "None", "—", "-"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def resolve_category(raw: str | None, messages: list | None = None) -> str:
    hit = normalize_vehicle_category(raw)
    if hit:
        return hit
    for msg in messages or []:
        text = msg.get("text") if isinstance(msg, dict) else str(msg or "")
        hit = normalize_vehicle_category(text)
        if hit:
            return hit
    return ""


def load_category_schema(category: str) -> dict:
    return category_schema(category)


def normalize_currency(raw: Any) -> dict | None:
    if _blank(raw):
        return None
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return {"value": int(raw), "currency": "INR", "unit": "INR"}
    text = str(raw).strip()
    amount = _parse_listing_price(text)
    if amount <= 0:
        # bare number without lakh/crore markers
        nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
        for n in nums:
            if re.fullmatch(r"(?:19|20)\d{2}", n):
                continue
            try:
                amount = float(n)
            except ValueError:
                continue
            break
    if amount <= 0:
        return None
    return {"value": int(round(amount)), "currency": "INR", "unit": "INR"}


def normalize_units(raw: Any, *, prefer_hours: bool = False) -> dict | None:
    if _blank(raw):
        return None
    if isinstance(raw, (int, float)) and float(raw) >= 0:
        unit = "h" if prefer_hours else "km"
        return {"value": int(raw), "unit": unit, "original": str(raw)}
    text = str(raw).strip()
    low = text.lower()
    mult = 1
    if re.search(r"हजार|haz[a]?ar|\bk\b", low):
        mult = 1000
    elif re.search(r"lakh|lac", low):
        mult = 100_000
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return None
    try:
        value = float(nums[0]) * mult
    except ValueError:
        return None
    if "hour" in low or "hrs" in low or "घंट" in low:
        unit = "h"
    elif prefer_hours and "km" not in low:
        unit = "h"
    else:
        unit = "km"
    return {"value": int(round(value)), "unit": unit, "original": text}


def normalize_year(raw: Any) -> dict | None:
    if _blank(raw):
        return None
    text = str(raw).strip()
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    if m:
        year = int(m.group(1))
        status = "NORMALIZED"
        if year > _current_year():
            status = "INVALID"
        return {"value": year, "status": status, "original": text}
    # Don't treat price amounts ("18 lakh") as two-digit model years
    if re.search(r"lakh|lac|crore|\bcr\b|₹|rs\.?", text, re.I):
        return None
    m2 = re.search(r"\b(\d{2})\s*(?:model|ka|की|का)?\b", text, re.I)
    if m2 and not re.search(r"(?:19|20)\d{2}", text):
        yy = int(m2.group(1))
        # Ambiguous two-digit year → prefer 20xx if plausible, else uncertain
        year = 2000 + yy if yy <= (_current_year() % 100) + 1 else 1900 + yy
        if _YEAR_MIN <= year <= _current_year():
            return {"value": year, "status": "UNCERTAIN", "original": text, "confidence": 0.7}
        return {"value": year, "status": "INVALID", "original": text}
    if text.isdigit() and len(text) == 4:
        year = int(text)
        status = "INVALID" if year > _current_year() else "NORMALIZED"
        return {"value": year, "status": status, "original": text}
    return None


def normalize_brand_model(brand: Any = None, model: Any = None) -> dict:
    out: dict[str, Any] = {}
    if not _blank(brand):
        b = re.sub(r"\s+", " ", str(brand).strip())
        key = b.lower()
        out["brand"] = _BRAND_ALIASES.get(key, b[:80])
    if not _blank(model):
        out["model"] = re.sub(r"\s+", " ", str(model).strip())[:80]
    return out


_STATE_ALIASES = {
    "madhya pradesh": "Madhya Pradesh",
    "m.p.": "Madhya Pradesh",
    "m.p": "Madhya Pradesh",
    "mp": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "mh": "Maharashtra",
    "rajasthan": "Rajasthan",
    "rj": "Rajasthan",
    "gujarat": "Gujarat",
    "gj": "Gujarat",
    "delhi": "Delhi",
    "ncr": "Delhi",
    "uttar pradesh": "Uttar Pradesh",
    "u.p.": "Uttar Pradesh",
    "u.p": "Uttar Pradesh",
    "up": "Uttar Pradesh",
    "karnataka": "Karnataka",
    "telangana": "Telangana",
    "tamil nadu": "Tamil Nadu",
    "west bengal": "West Bengal",
    "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh",
    "haryana": "Haryana",
    "punjab": "Punjab",
    "मध्य प्रदेश": "Madhya Pradesh",
    "मप्र": "Madhya Pradesh",
    "महाराष्ट्र": "Maharashtra",
    "राजस्थान": "Rajasthan",
    "गुजरात": "Gujarat",
    "दिल्ली": "Delhi",
    "उत्तर प्रदेश": "Uttar Pradesh",
}


def _looks_like_place(text: str) -> bool:
    """Reject prices, years, vehicle dumps, yes/no, and long sentences as locations."""
    raw = (text or "").strip()
    if not raw or len(raw) > 48:
        return False
    low = raw.lower()
    if re.fullmatch(
        r"(haan+|han+|ha+|hji|ji|yes+|yup|yeah|yep|ok+|okay|no+|nahi+|na+|hello|hi|hey|"
        r"theek|thik|sahi|correct|done|post|submit|please|pls|thanks|thank\s*you|"
        r"हाँ+|हां+|जी+|नहीं+|ना+|ठीक|सही)",
        low,
    ):
        return False
    if re.search(
        r"lakh|lac|crore|\bcr\b|₹|rs\.?|\bkm\b|hour|hrs?|bech|sell|buy|kharid|"
        r"\btata\b|\bjcb\b|\beicher\b|tipper|dumper|truck|photo|otp|password|"
        r"model|price|rate|budget|year|saal|post\s*kr|submit",
        low,
    ):
        return False
    # Digits usually mean price/year/km — allow only sector/NH style
    if re.search(r"\d", raw) and not re.search(r"(?:sector|sec\.?|phase|nh)\s*\d", low):
        return False
    if len(raw.split()) > 5:
        return False
    return True


def ensure_model_fallback(payload: dict) -> bool:
    """When model is blank but brand is set (esp. JCB/equipment), use brand as model.

    Returns True if model was filled.
    """
    if not _blank(payload.get("model")):
        return False
    brand = str(payload.get("brand") or "").strip()
    if not brand:
        return False
    # Reject year-like brands being copied into model elsewhere
    if re.fullmatch(r"(?:19|20)\d{2}", brand):
        return False
    cat = normalize_vehicle_category(payload.get("category") or payload.get("type") or "") or ""
    equipment = {
        "JCB", "Excavator", "Poclain", "Loader", "Crane", "Crusher",
        "Backhoe Loader", "Grader",
    }
    if cat in equipment or brand.upper() == (cat or "").upper() or brand.upper() == "JCB":
        payload["model"] = brand[:80]
        return True
    return False


def _match_state_name(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    for cue, label in sorted(_STATE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        # Bare "up"/"mp"/"mh" only as whole token — still risky in "price up";
        # require short place-like text for abbreviations.
        if cue in {"up", "mp", "mh", "rj", "gj"}:
            if not re.fullmatch(rf"{re.escape(cue)}", low) and not re.search(
                rf"(?:state|location|city|rajya|in)\s+{re.escape(cue)}\b|\b{re.escape(cue)}\s+(?:state|me|mein)\b",
                low,
            ):
                continue
        if re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", low):
            return label
    return None


def normalize_location(city: Any = None, state: Any = None, location: Any = None) -> dict:
    city_s = str(city or "").strip()
    state_s = str(state or "").strip()
    loc_s = str(location or "").strip()
    blob = " ".join(x for x in [city_s, state_s, loc_s] if x)
    low = blob.lower()
    for cue, (c, s) in _CITY_STATE.items():
        if re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", low):
            return {"city": c, "state": s, "country": "India", "location": c}
    known_state = _match_state_name(blob) or _match_state_name(state_s) or _match_state_name(loc_s)
    result: dict[str, Any] = {"country": "India"}
    if city_s and _looks_like_place(city_s):
        result["city"] = city_s[:80]
        result["location"] = city_s[:80]
    if state_s and (_looks_like_place(state_s) or _match_state_name(state_s)):
        result["state"] = (_match_state_name(state_s) or state_s)[:80]
    elif known_state:
        result["state"] = known_state
    if loc_s and _looks_like_place(loc_s):
        # Free-text place only when it looks like a location (not a full chat turn)
        if not result.get("city"):
            result["city"] = loc_s[:80]
        result["location"] = (result.get("city") or loc_s)[:80]
        if not result.get("state"):
            st = _match_state_name(loc_s)
            if st:
                result["state"] = st
    elif loc_s and known_state and not result.get("city") and not result.get("location"):
        # Explicit state-only answer ("Madhya Pradesh" / "MP")
        result["state"] = known_state
        result["location"] = known_state
    if not result.get("city") and not result.get("state") and not result.get("location"):
        return {}
    return result


def _extract_bare_price(text: str, year: Any = None) -> int | None:
    """Pick a bare integer price from text (not years / short model codes).

    Prefers the largest plausible amount >= 10_000 so multiline dumps
    ``Tata / 2567 / 2021 / 2000000`` keep 2000000, not 2567.
    """
    if not (text or "").strip():
        return None
    year_skip = set()
    if not _blank(year):
        try:
            year_skip.add(str(int(year)))
        except (TypeError, ValueError):
            pass
    candidates: list[int] = []
    for raw in re.findall(r"\d[\d,]{3,}", text.replace(" ", "")):
        digits = raw.replace(",", "")
        if _YEAR_RE.match(digits) or digits in year_skip:
            continue
        if re.fullmatch(r"\d{3,4}", digits):
            # Model codes (1613, 2567) — not rupee prices
            continue
        try:
            val = int(float(digits))
        except (TypeError, ValueError):
            continue
        if val < 10_000 or val > 50_00_00_000:
            continue
        if 1900 <= val <= 2035:
            continue
        candidates.append(val)
    if not candidates:
        # Lone message: "2000000" or "20,00,000"
        cur = normalize_currency(text.strip())
        if cur and int(cur["value"]) >= 10_000:
            return int(cur["value"])
        return None
    return max(candidates)


def extract_fields(messages: list | None, fields: dict | None = None) -> dict:
    """Merge explicit fields with light NL extraction from latest messages."""
    out = dict(fields or {})
    texts = []
    for msg in messages or []:
        if isinstance(msg, dict):
            texts.append(str(msg.get("text") or ""))
        else:
            texts.append(str(msg or ""))
    blob = " ".join(texts).strip()
    if not blob:
        return out

    if _blank(out.get("category")):
        cat = resolve_category(blob)
        if cat:
            out["category"] = cat

    if _blank(out.get("brand")):
        low = blob.lower()
        for cue, canon in sorted(_BRAND_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", low):
                out["brand"] = canon
                break

    if _blank(out.get("model")):
        m = re.search(r"\b(\d{3,4}[A-Za-z]?|3DX|4DX|JS\d{2,3})\b", blob, re.I)
        if m and not re.fullmatch(r"(?:19|20)\d{2}", m.group(1)):
            out["model"] = m.group(1).upper() if m.group(1).isdigit() else m.group(1)

    if _blank(out.get("year")):
        y = normalize_year(blob)
        if y and y.get("status") != "INVALID":
            out["year"] = y["value"]

    if _blank(out.get("expected_price")) and _blank(out.get("price")):
        # Prefer segments with lakh/crore/₹
        for m in re.finditer(
            r"(?:₹|rs\.?\s*)?\d+(?:[.,]\d+)?\s*(?:lakh|lac|lacs|l|crore|cr)\b|"
            r"(?:₹|rs\.?\s*)\d[\d,]{3,}",
            blob,
            re.I,
        ):
            cur = normalize_currency(m.group(0))
            if cur:
                out["expected_price"] = cur["value"]
                out["price"] = cur["value"]
                break
        # Bare rupee amounts: multiline dumps like "Tata\n2567\n2021\n2000000"
        # or a lone "2000000" when asking for price (no ₹/lakh marker).
        if _blank(out.get("expected_price")) and _blank(out.get("price")):
            bare = _extract_bare_price(blob, year=out.get("year"))
            if bare:
                out["expected_price"] = bare
                out["price"] = bare

    if _blank(out.get("running_km")) and _blank(out.get("operating_hours")) and _blank(out.get("km")):
        for m in re.finditer(
            r"(\d+(?:[.,]\d+)?\s*(?:हजार|k|km|hours?|hrs?|घंटे?))",
            blob,
            re.I,
        ):
            unit = normalize_units(m.group(1))
            if not unit:
                continue
            if unit["unit"] == "h":
                out["operating_hours"] = unit["value"]
            else:
                out["running_km"] = unit["value"]
                out["km"] = unit["value"]
            break

    if _blank(out.get("city")) and _blank(out.get("location")) and _blank(out.get("state")):
        loc = normalize_location(location=blob)
        if loc.get("city"):
            out["city"] = loc["city"]
            out["location"] = loc["city"]
        if loc.get("state"):
            out["state"] = loc["state"]

    return out


def _field_record(value: Any, source: str, status: str, confidence: float = 1.0, **extra) -> dict:
    rec = {
        "value": value,
        "source": source,
        "status": status,
        "confidence": confidence,
    }
    rec.update(extra)
    return rec


def _source_for(payload: dict, key: str, field_sources: dict | None = None) -> str:
    if field_sources and field_sources.get(key):
        return str(field_sources[key])
    conf = (payload.get("confidence") or {}).get(key) or ""
    if conf in {"FROM_BACKEND", "VERIFIED_BACKEND"}:
        return "VERIFIED_BACKEND"
    if conf in {"CONFIRMED_BY_CUSTOMER", "USER_CONFIRMED"}:
        return "USER_CONFIRMED"
    if conf in {"INFERRED_BY_AI", "AI_INFERRED"}:
        return "AI_INFERRED"
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if isinstance(src.get("backend"), dict) and key in src["backend"]:
        return "VERIFIED_BACKEND"
    if isinstance(src.get("inferred"), dict) and key in src["inferred"]:
        return "AI_INFERRED"
    if isinstance(src.get("customer"), dict) and key in src["customer"]:
        return "USER"
    return "USER"


def detect_conflicts(payload: dict, field_sources: dict | None = None, documents: dict | None = None) -> list[dict]:
    conflicts = []
    docs = documents or payload.get("documents") or {}
    if not isinstance(docs, dict):
        docs = {}
    for key, doc_val in docs.items():
        user_val = payload.get(key)
        if _blank(user_val) or _blank(doc_val):
            continue
        if str(user_val).strip().lower() != str(doc_val).strip().lower():
            conflicts.append({
                "field": key,
                "user_value": user_val,
                "document_value": doc_val,
                "values": [
                    {"value": user_val, "source": _source_for(payload, key, field_sources)},
                    {"value": doc_val, "source": "RC_DOCUMENT"},
                ],
                "status": "CONFLICT",
                "resolution": "USER_CONFIRMATION_REQUIRED",
            })
    # Explicit multi-source conflicts carried on payload
    for item in payload.get("field_conflicts") or []:
        if isinstance(item, dict) and item.get("field"):
            conflicts.append(item)
    return conflicts


def validate_ranges(normalized: dict, category: str = "") -> list[dict]:
    errors = []
    year = normalized.get("year")
    if year is not None:
        try:
            y = int(year)
        except (TypeError, ValueError):
            y = None
        if y is not None:
            if y > _current_year():
                errors.append({
                    "field": "year",
                    "code": "FUTURE_YEAR",
                    "message": "Vehicle year cannot be greater than current year.",
                })
            elif y < _YEAR_MIN:
                errors.append({
                    "field": "year",
                    "code": "YEAR_TOO_OLD",
                    "message": f"Vehicle year before {_YEAR_MIN} is not accepted.",
                })

    price = normalized.get("price") or normalized.get("expected_price")
    if price is not None:
        try:
            p = int(price)
        except (TypeError, ValueError):
            p = None
        if p is not None:
            if p <= 0:
                errors.append({
                    "field": "price",
                    "code": "INVALID_PRICE",
                    "message": "Price must be greater than 0.",
                })
            elif p > _PRICE_OUTLIER_MAX:
                errors.append({
                    "field": "price",
                    "code": "PRICE_OUTLIER",
                    "message": "Price looks unusually high and needs confirmation.",
                    "severity": True,
                })

    for key in ("km", "running_km", "operating_hours", "hours"):
        val = normalized.get(key)
        if val is None:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n < 0:
            errors.append({
                "field": key,
                "code": "NEGATIVE_VALUE",
                "message": f"{key} cannot be negative.",
            })
    return errors


def validate_cross_fields(normalized: dict, category: str = "") -> list[dict]:
    flags = []
    reg = normalized.get("registration_year")
    year = normalized.get("year")
    try:
        if reg is not None and year is not None and int(reg) < int(year):
            flags.append({
                "field": "registration_year",
                "code": "REG_BEFORE_MFG",
                "message": "Registration year cannot be before manufacturing year.",
            })
    except (TypeError, ValueError):
        pass

    fuel = str(normalized.get("fuel") or normalized.get("fuel_type") or "").lower()
    cat = (category or normalized.get("category") or "").upper()
    if cat in {"EXCAVATOR", "JCB", "POCLAIN", "LOADER", "CRANE"} and fuel in {"petrol", "पेट्रोल"}:
        flags.append({
            "field": "fuel",
            "code": "SUSPICIOUS_FUEL",
            "message": "Petrol is unusual for this machine category.",
            "severity": True,
        })

    hours = normalized.get("hours") or normalized.get("operating_hours")
    if cat in {"TRUCK", "TIPPER", "DUMPER"} and hours is not None:
        try:
            if int(hours) > 0 and int(hours) < 100:
                flags.append({
                    "field": "operating_hours",
                    "code": "SUSPICIOUS_HOURS",
                    "message": "Hours look unusual for a truck listing.",
                    "severity": True,
                })
        except (TypeError, ValueError):
            pass
    return flags


def build_missing_fields(normalized: dict, category: str, intent: str = "SELL") -> list[dict]:
    schema = load_category_schema(category) if intent.upper() == "SELL" else {
        "required": ["category", "budget", "state"],
        "priorities": {"category": 100, "budget": 95, "state": 90},
    }
    required = list(schema.get("required") or [])
    priorities = dict(schema.get("priorities") or {})
    # Map schema keys onto normalized aliases
    aliases = {
        "price": ("price", "expected_price"),
        "location": ("state", "city", "location"),
        "budget": ("budget", "budget_max"),
        "hours": ("hours", "operating_hours", "running"),
        "km": ("km", "running_km", "running"),
        "photos": ("photos", "media_ids", "photo_count"),
    }
    # User explicitly skipped optional/runtime fields ("nahi malum" / "post kr do")
    skipped = {str(x).lower() for x in (normalized.get("skipped_asks") or []) if x}
    skip_hours = bool(skipped & {"hours", "operating_hours", "optional", "km", "running_km"})
    skip_km = bool(skipped & {"km", "running_km", "optional", "hours", "operating_hours"})
    missing = []
    for key in required:
        if key == "hours" and skip_hours:
            continue
        if key == "km" and skip_km:
            continue
        keys = aliases.get(key, (key,))
        present = False
        for k in keys:
            val = normalized.get(k)
            if key == "photos":
                if isinstance(val, (list, tuple)) and len(val) > 0:
                    present = True
                elif isinstance(val, (int, float)) and int(val) > 0:
                    present = True
            elif not _blank(val):
                present = True
                break
        if not present:
            missing.append({"field": key, "priority": int(priorities.get(key, 50))})
    missing.sort(key=lambda x: (-x["priority"], x["field"]))
    return missing


def calculate_confidence(field_status: dict, conflicts: list, validation_errors: list) -> dict:
    scores = []
    for key, status in (field_status or {}).items():
        if status == "CONFIRMED":
            scores.append(1.0)
        elif status == "NORMALIZED":
            scores.append(0.9)
        elif status == "EXTRACTED":
            scores.append(0.8)
        elif status == "INFERRED":
            scores.append(0.55)
        elif status == "UNCERTAIN":
            scores.append(0.4)
        elif status in {"CONFLICT", "INVALID"}:
            scores.append(0.1)
    avg = sum(scores) / len(scores) if scores else 0.0
    if conflicts:
        avg = min(avg, 0.45)
    hard_errors = [e for e in validation_errors if not e.get("severity")]
    if hard_errors:
        avg = min(avg, 0.3)
    return {"score": round(avg, 3), "fields": len(scores)}


def calculate_quality_score(
    *,
    missing: list,
    conflicts: list,
    validation_errors: list,
    duplicate: dict,
    photos: dict,
    confidence: dict,
) -> dict:
    score = 100
    score -= min(60, 12 * len(missing))
    score -= min(30, 15 * len(conflicts))
    hard = [e for e in validation_errors if not e.get("severity")]
    soft = [e for e in validation_errors if e.get("suspicious")]
    score -= min(40, 20 * len(hard))
    score -= min(15, 5 * len(soft))
    if duplicate.get("possible_duplicate"):
        score -= 10
    received = int(photos.get("received") or 0)
    valid = int(photos.get("valid") or received)
    if received and valid < received:
        score -= 5 * (received - valid)
    conf = float((confidence or {}).get("score") or 0)
    score = int(max(0, min(100, round(score * (0.7 + 0.3 * conf)))))
    if score >= 90:
        status = "READY_FOR_CONFIRMATION"
    elif score >= 75:
        status = "NEEDS_MINOR_CONFIRMATION"
    elif score >= 50:
        status = "INCOMPLETE"
    else:
        status = "NOT_READY"
    return {"score": score, "status": status}


def check_photos(payload: dict, db: "Session | None" = None, conv: "AiConversation | None" = None) -> dict:
    media_ids = [x for x in (payload.get("media_ids") or []) if x]
    received = len(media_ids)
    valid = received
    duplicate = 0
    if db is not None and conv is not None and media_ids:
        from ..models import AiMedia

        rows = (
            db.query(AiMedia)
            .filter(AiMedia.conversation_id == conv.id, AiMedia.id.in_([int(x) for x in media_ids if str(x).isdigit()]))
            .all()
        )
        seen = set()
        valid = 0
        for row in rows:
            key = row.meta_media_id or row.local_path or str(row.id)
            if key in seen:
                duplicate += 1
                continue
            seen.add(key)
            if row.local_path or row.meta_media_id:
                valid += 1
        received = max(received, len(rows))
    return {
        "received": received,
        "valid": valid,
        "duplicate": duplicate,
        "status": "OK" if valid >= 0 else "PHOTO_ISSUE",
    }


def check_duplicates(
    db: "Session | None",
    normalized: dict,
    *,
    account_id: str | None = None,
    mobile: str | None = None,
) -> dict:
    if db is None:
        return {"possible_duplicate": False}
    from ..models import AiListingDraft, Product

    brand = str(normalized.get("brand") or "").strip().lower()
    model = str(normalized.get("model") or "").strip().lower()
    year = str(normalized.get("year") or "").strip()
    if not brand and not model:
        return {"possible_duplicate": False}
    q = db.query(Product)
    if mobile:
        digits = re.sub(r"\D", "", mobile)[-10:]
        if digits:
            q = q.filter(Product.mobile.contains(digits))
    rows = q.order_by(Product.id.desc()).limit(40).all()
    for row in rows:
        title = f"{row.title or ''} {row.description or ''} {row.category or ''}".lower()
        brand_ok = brand and brand in title
        model_ok = (not model) or (model and model in title)
        year_ok = (not year) or (year and year in title)
        if brand_ok and model_ok and year_ok:
            return {
                "possible_duplicate": True,
                "matched_listing_id": str(getattr(row, "ref", None) or row.id),
                "confidence": 0.9 if year else 0.75,
            }
    if mobile:
        drafts = (
            db.query(AiListingDraft)
            .filter(
                AiListingDraft.mobile == mobile[-10:],
                AiListingDraft.status.in_(["POSTED", "PENDING_REVIEW", "READY_FOR_REVIEW", "CONFIRMED"]),
            )
            .order_by(AiListingDraft.id.desc())
            .limit(20)
            .all()
        )
        for draft in drafts:
            blob = f"{draft.title or ''} {draft.customer_json or ''} {draft.confirmed_json or ''}".lower()
            if brand and brand in blob and ((not model) or model in blob):
                if year and year not in blob:
                    continue
                return {
                    "possible_duplicate": True,
                    "matched_listing_id": draft.card_id or str(draft.id),
                    "confidence": 0.85,
                }
    return {"possible_duplicate": False}


def build_canonical_listing(normalized: dict, intent: str, category: str, photos: dict) -> dict:
    vehicle = {
        k: normalized[k]
        for k in ("brand", "model", "year", "km", "running_km", "operating_hours", "hours", "condition")
        if not _blank(normalized.get(k))
    }
    commercial = {}
    price = normalized.get("price") or normalized.get("expected_price")
    if not _blank(price):
        commercial = {"price": int(price) if str(price).isdigit() or isinstance(price, (int, float)) else price, "currency": "INR"}
    location = {
        k: normalized[k]
        for k in ("city", "state", "country", "location")
        if not _blank(normalized.get(k))
    }
    if location and "country" not in location:
        location["country"] = "India"
    title = listing_title({
        "brand": normalized.get("brand"),
        "model": normalized.get("model"),
        "category": category,
    })
    listing = {
        "intent": (intent or "SELL").upper(),
        "category": category,
        "vehicle": vehicle or title,
        "title": title,
        "commercial": commercial,
        "location": location,
        "media": {"photo_count": int(photos.get("valid") or photos.get("received") or 0)},
        "brand": normalized.get("brand"),
        "model": normalized.get("model"),
        "year": normalized.get("year"),
        "price": commercial.get("price") if commercial else None,
        "state": location.get("state"),
        "city": location.get("city"),
    }
    return {k: v for k, v in listing.items() if v not in (None, "", {}, [])}


def build_confirmation_requirements(
    field_status: dict,
    conflicts: list,
    validation_errors: list,
    readiness: str,
) -> dict:
    fields = []
    for key, status in (field_status or {}).items():
        if status in {"INFERRED", "UNCERTAIN", "CONFLICT"}:
            fields.append(key)
    for err in validation_errors:
        if err.get("suspicious") or err.get("code") == "PRICE_OUTLIER":
            if err.get("field") and err["field"] not in fields:
                fields.append(err["field"])
    for c in conflicts:
        f = c.get("field")
        if f and f not in fields:
            fields.append(f)
    required = readiness in {
        "READY_FOR_CONFIRMATION",
        "CONFLICT_REQUIRES_USER",
        "DUPLICATE_WARNING",
    } or bool(fields)
    return {"required": required, "fields": fields}


def normalize_fields(payload: dict, messages: list | None = None, field_sources: dict | None = None) -> tuple[dict, dict]:
    extracted = extract_fields(messages, {
        k: payload.get(k)
        for k in (
            "intent", "category", "type", "brand", "model", "year", "registration_year",
            "km", "running_km", "operating_hours", "running", "hours",
            "expected_price", "price", "budget", "budget_max",
            "location", "state", "city", "condition", "fuel", "fuel_type",
            "owners", "description", "media_ids",
        )
        if not _blank(payload.get(k))
    })

    category = resolve_category(extracted.get("category") or extracted.get("type") or payload.get("category"))
    prefer_hours = category in HOUR_CATEGORIES

    brand_model = normalize_brand_model(extracted.get("brand"), extracted.get("model"))
    year_rec = normalize_year(extracted.get("year"))
    price_rec = normalize_currency(extracted.get("expected_price") or extracted.get("price"))
    budget_rec = normalize_currency(extracted.get("budget") or extracted.get("budget_max"))
    km_rec = normalize_units(
        extracted.get("km") or extracted.get("running_km") or extracted.get("running"),
        prefer_hours=prefer_hours,
    )
    hours_rec = normalize_units(
        extracted.get("hours") or extracted.get("operating_hours"),
        prefer_hours=True,
    )
    loc = normalize_location(extracted.get("city"), extracted.get("state"), extracted.get("location"))

    normalized: dict[str, Any] = {
        "intent": (extracted.get("intent") or payload.get("intent") or "").upper() or None,
        "category": category or None,
    }
    field_status: dict[str, str] = {}

    if brand_model.get("brand"):
        normalized["brand"] = brand_model["brand"]
        src = _source_for(payload, "brand", field_sources)
        field_status["brand"] = "INFERRED" if "INFER" in src.upper() else "CONFIRMED"
    if brand_model.get("model"):
        normalized["model"] = brand_model["model"]
        src = _source_for(payload, "model", field_sources)
        field_status["model"] = "INFERRED" if "INFER" in src.upper() else "CONFIRMED"

    if year_rec:
        normalized["year"] = year_rec["value"]
        field_status["year"] = year_rec.get("status") or "NORMALIZED"
        if field_status["year"] == "NORMALIZED" and _source_for(payload, "year", field_sources) in {
            "USER", "USER_CONFIRMED", "CONFIRMED_BY_CUSTOMER", "customer",
        }:
            field_status["year"] = "CONFIRMED"

    if price_rec:
        normalized["price"] = price_rec["value"]
        normalized["expected_price"] = price_rec["value"]
        field_status["price"] = "CONFIRMED"
        field_status["expected_price"] = "CONFIRMED"

    if budget_rec:
        normalized["budget"] = budget_rec["value"]
        field_status["budget"] = "CONFIRMED"

    if km_rec:
        if km_rec["unit"] == "h":
            normalized["operating_hours"] = km_rec["value"]
            normalized["hours"] = km_rec["value"]
            field_status["operating_hours"] = "NORMALIZED"
        else:
            normalized["km"] = km_rec["value"]
            normalized["running_km"] = km_rec["value"]
            field_status["km"] = "NORMALIZED"

    if hours_rec and hours_rec["unit"] == "h":
        normalized["operating_hours"] = hours_rec["value"]
        normalized["hours"] = hours_rec["value"]
        field_status["operating_hours"] = "NORMALIZED"

    if loc:
        normalized.update(loc)
        if loc.get("state"):
            field_status["state"] = "CONFIRMED"
        if loc.get("city") or loc.get("location"):
            field_status["location"] = "CONFIRMED"

    if not _blank(extracted.get("condition")):
        normalized["condition"] = str(extracted["condition"]).strip()[:80]
        field_status["condition"] = "CONFIRMED"

    if payload.get("media_ids"):
        normalized["media_ids"] = list(payload.get("media_ids") or [])
        field_status["photos"] = "EXTRACTED"

    # Preserve inferred labels from payload confidence
    conf = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
    for key, label in conf.items():
        if label in {"INFERRED_BY_AI", "AI_INFERRED"} and key in normalized:
            field_status[key] = "INFERRED"

    return normalized, field_status


def filter_payload(
    payload: dict,
    *,
    db: Session | None = None,
    conv: AiConversation | None = None,
    messages: list | None = None,
    field_sources: dict | None = None,
    documents: dict | None = None,
    request_id: str = "",
    draft_id: str | int | None = None,
    draft_version: int | None = None,
) -> FilterResult:
    """Full Data Filter pipeline. Never invents values; never silently overwrites conflicts."""
    started = time.perf_counter()
    try:
        base = dict(payload or {})
        intent = (base.get("intent") or "").upper()
        normalized, field_status = normalize_fields(base, messages=messages, field_sources=field_sources)
        if intent:
            normalized["intent"] = intent
        category = normalized.get("category") or ""
        schema = load_category_schema(category) if category else {}

        conflicts = detect_conflicts(base, field_sources=field_sources, documents=documents)
        for c in conflicts:
            f = c.get("field")
            if f:
                field_status[f] = "CONFLICT"

        validation_errors = validate_ranges(normalized, category)
        validation_errors.extend(validate_cross_fields(normalized, category))
        for err in validation_errors:
            if err.get("code") == "FUTURE_YEAR":
                field_status["year"] = "INVALID"

        photos = check_photos(base, db=db, conv=conv)
        duplicate = check_duplicates(
            db,
            normalized,
            account_id=str(base.get("profile_id") or base.get("account_id") or ""),
            mobile=(conv.mobile if conv else None) or str(base.get("whatsapp_number") or ""),
        )

        missing = build_missing_fields(normalized, category, intent or "SELL")
        # Photos are optional for readiness unless schema required them and none provided
        confidence = calculate_confidence(field_status, conflicts, validation_errors)
        quality = calculate_quality_score(
            missing=missing,
            conflicts=conflicts,
            validation_errors=validation_errors,
            duplicate=duplicate,
            photos=photos,
            confidence=confidence,
        )

        hard_errors = [e for e in validation_errors if not e.get("suspicious")]
        if conflicts:
            readiness = "CONFLICT_REQUIRES_USER"
        elif hard_errors:
            readiness = "INVALID_DATA"
        elif missing:
            readiness = "MISSING_REQUIRED_DATA"
        elif photos.get("received") and photos.get("valid", 0) == 0:
            readiness = "PHOTO_ISSUE"
        elif duplicate.get("possible_duplicate"):
            readiness = "DUPLICATE_WARNING"
        else:
            readiness = "READY_FOR_CONFIRMATION"

        # Inferred-only critical fields still need confirmation before ready
        inferred_critical = [
            k for k, st in field_status.items()
            if st == "INFERRED" and k in {"brand", "model", "year", "price", "expected_price", "category"}
        ]
        if readiness == "READY_FOR_CONFIRMATION" and inferred_critical:
            readiness = "READY_FOR_CONFIRMATION"  # still ready, but confirmation.fields lists them

        confirmation = build_confirmation_requirements(field_status, conflicts, validation_errors, readiness)
        if readiness == "READY_FOR_CONFIRMATION":
            confirmation["required"] = True

        canonical = build_canonical_listing(normalized, intent or "SELL", category, photos)
        ready = readiness in {"READY_FOR_CONFIRMATION", "DUPLICATE_WARNING"} and not missing and not hard_errors and not conflicts

        result = FilterResult(
            success=True,
            ready=ready,
            readiness=readiness,
            data=canonical,
            normalized_data={k: v for k, v in normalized.items() if k != "media_ids"},
            field_status=field_status,
            missing_fields=missing,
            conflicts=conflicts,
            validation_errors=validation_errors,
            duplicate_check=duplicate,
            quality=quality,
            confirmation=confirmation,
            photos=photos,
            request_id=request_id or f"REQ-{int(time.time())}",
            draft={
                "draft_id": draft_id or base.get("draft_id"),
                "version": draft_version if draft_version is not None else int(base.get("draft_version") or 1),
            },
            classification={
                "intent": intent or normalized.get("intent"),
                "category": category,
                "schema": schema.get("category"),
            },
            schema_version=schema.get("schema_version") or SCHEMA_VERSION,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "data_filter request_id=%s draft=%s category=%s readiness=%s missing=%s conflicts=%s quality=%s ms=%s",
            result.request_id,
            result.draft.get("draft_id"),
            category,
            readiness,
            len(missing),
            len(conflicts),
            quality.get("score"),
            elapsed_ms,
        )
        return result
    except Exception as exc:
        log.exception("data_filter failed")
        return FilterResult(
            success=False,
            ready=False,
            readiness="SYSTEM_ERROR",
            error=str(exc)[:300],
            request_id=request_id or "",
        )


def filter_collected(payload: dict, messages: list | None = None) -> dict:
    """Normalize a collected payload into flat listing fields (no DB)."""
    result = filter_payload(payload, messages=messages)
    out = dict(result.normalized_data)
    # Keep string price human-friendly when original had lakh wording? Tests use structured apply.
    if result.normalized_data.get("category"):
        out["category"] = result.normalized_data["category"]
    if result.normalized_data.get("brand"):
        out["brand"] = result.normalized_data["brand"]
    if result.normalized_data.get("model"):
        out["model"] = result.normalized_data["model"]
    if result.normalized_data.get("year") is not None:
        out["year"] = result.normalized_data["year"]
    if result.normalized_data.get("price") is not None:
        out["expected_price"] = result.normalized_data["price"]
        out["price"] = result.normalized_data["price"]
    if result.normalized_data.get("state"):
        out["state"] = result.normalized_data["state"]
    if result.normalized_data.get("city"):
        out["city"] = result.normalized_data["city"]
    out["_filter"] = {
        "readiness": result.readiness,
        "quality": result.quality,
        "missing_fields": result.missing_fields,
        "validation_errors": result.validation_errors,
        "conflicts": result.conflicts,
    }
    return out


def is_collection_ready(payload: dict) -> bool:
    """True when category-schema required fields are present (pre-confirmation)."""
    intent = (payload.get("intent") or "").upper()
    if intent not in {"BUY", "SELL"}:
        return False
    # Equipment often has brand == model line (JCB) — fill before readiness check
    ensure_model_fallback(payload)
    # Drop yes/hello garbage that slipped into location fields
    for key in ("city", "location", "state"):
        val = str(payload.get(key) or "").strip()
        if val and not _looks_like_place(val) and not _match_state_name(val):
            payload[key] = None
    # Use schema missing detection on a normalized view without inventing
    normalized, _ = normalize_fields(payload)
    if intent:
        normalized["intent"] = intent
    category = normalized.get("category") or normalize_vehicle_category(payload.get("category") or "")
    # Merge raw payload values for readiness so partially-normalized still works
    merged = dict(payload)
    merged.update({k: v for k, v in normalized.items() if not _blank(v)})
    if category:
        merged["category"] = category
    ensure_model_fallback(merged)
    missing = build_missing_fields(merged, category, intent)
    # Photos not required for collection_ready (asked later)
    missing = [m for m in missing if m.get("field") != "photos"]
    if missing:
        return False
    # Invalid year blocks readiness
    errors = validate_ranges(merged, category)
    if any(e.get("code") == "FUTURE_YEAR" for e in errors):
        return False
    return True


def filter_memory(db: Session, conv: AiConversation) -> FilterResult:
    """Run Data Filter against the live conversation payload and persist summary."""
    from .tools import _payload, _write_payload, _draft_for

    payload = _payload(conv)
    draft = _draft_for(db, conv)
    draft_version = 1
    try:
        meta = {}
        if draft.confirmed_json:
            meta = json.loads(draft.confirmed_json or "{}") if isinstance(draft.confirmed_json, str) else {}
        draft_version = int((meta or {}).get("_draft_version") or 1) + 1
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        log.warning("data_filter draft_version parse failed: %s", exc)
        draft_version = 1

    result = filter_payload(
        payload,
        db=db,
        conv=conv,
        request_id=f"REQ-{conv.id}-{draft_version}",
        draft_id=draft.id,
        draft_version=draft_version,
    )

    # Persist filter snapshot onto payload for chat_memory / confirmation UX
    summary = dict(result.normalized_data)
    summary["vehicle"] = result.data.get("vehicle") or listing_title(payload)
    summary["category"] = result.classification.get("category") or payload.get("category")
    summary["readiness"] = result.readiness
    summary["quality"] = result.quality
    summary["_draft_version"] = draft_version
    summary["_filter_version"] = FILTER_VERSION

    payload["summary_json"] = summary
    payload["data_status"] = "COMPLETE" if result.ready else "INCOMPLETE"
    payload["missing_fields"] = [m["field"] if isinstance(m, dict) else m for m in result.missing_fields]
    payload.setdefault("confidence", {}).update({
        k: ("INFERRED_BY_AI" if v == "INFERRED" else "CONFIRMED_BY_CUSTOMER")
        for k, v in result.field_status.items()
    })
    # Apply safe normalized representations (not semantic changes)
    for key in ("category", "brand", "model", "year", "state", "city"):
        if not _blank(result.normalized_data.get(key)):
            payload[key] = result.normalized_data[key]
    if not _blank(result.normalized_data.get("price")):
        # Keep human price string if already set; also store numeric
        if _blank(payload.get("expected_price")):
            payload["expected_price"] = str(result.normalized_data["price"])
    if result.normalized_data.get("km") and _blank(payload.get("running_km")):
        payload["running_km"] = str(result.normalized_data["km"])
    if result.normalized_data.get("operating_hours") and _blank(payload.get("operating_hours")):
        payload["operating_hours"] = str(result.normalized_data["operating_hours"])

    payload["filter_result"] = {
        "readiness": result.readiness,
        "quality": result.quality,
        "missing_fields": result.missing_fields,
        "conflicts": result.conflicts,
        "validation_errors": result.validation_errors,
        "duplicate_check": result.duplicate_check,
        "confirmation": result.confirmation,
    }
    _write_payload(conv, payload)

    draft.title = listing_title(payload)
    draft.intent = (payload.get("intent") or draft.intent or "").upper()
    if result.ready and draft.status not in {"POSTED", "READY_FOR_REVIEW", "CONFIRMED"}:
        draft.status = "COLLECTING"
    try:
        draft.confirmed_json = json.dumps(summary, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        log.warning("data_filter confirmed_json dump failed: %s", exc)

    # Attach listing object on result.data for callers
    if "vehicle" not in result.data:
        result.data["vehicle"] = summary.get("vehicle")
    return result


def final_validation(db: Session, conv: AiConversation) -> FilterResult:
    """Post-confirmation safety pass before data_push."""
    result = filter_memory(db, conv)
    if result.conflicts or any(e.get("code") == "FUTURE_YEAR" for e in result.validation_errors):
        result.ready = False
        if result.conflicts:
            result.readiness = "CONFLICT_REQUIRES_USER"
        else:
            result.readiness = "INVALID_DATA"
    return result


def apply_filter_to_payload(payload: dict) -> dict:
    """In-place enrich payload with filter outputs (no DB). Used by tools."""
    result = filter_payload(payload)
    payload["missing_fields"] = [m["field"] if isinstance(m, dict) else m for m in result.missing_fields]
    payload["filter_result"] = result.as_dict()
    payload["data_status"] = "COMPLETE" if result.ready else "INCOMPLETE"
    if result.normalized_data.get("category"):
        payload["category"] = result.normalized_data["category"]
        payload["type"] = result.normalized_data["category"]
    return payload


# --- Markdown-aligned aliases (Data_filter.py instruction §50) ---

def validate_required_fields(normalized: dict, category: str, intent: str = "SELL") -> list[dict]:
    return build_missing_fields(normalized, category, intent)


def validate_field_types(normalized: dict) -> list[dict]:
    """Type sanity checks — flag impossible types without inventing replacements."""
    errors = []
    if "year" in normalized and normalized["year"] is not None:
        try:
            int(normalized["year"])
        except (TypeError, ValueError):
            errors.append({
                "field": "year",
                "code": "INVALID_TYPE",
                "message": "Year must be a number.",
            })
    for key in ("price", "expected_price", "budget", "km", "running_km", "operating_hours", "hours"):
        if key not in normalized or normalized[key] is None:
            continue
        val = normalized[key]
        if isinstance(val, str) and not re.search(r"\d", val):
            errors.append({
                "field": key,
                "code": "INVALID_TYPE",
                "message": f"{key} must contain a numeric value.",
            })
    return errors


def check_master_data(normalized: dict) -> list[dict]:
    """Soft master-data match — never invent models; only flag unrecognized cues."""
    warnings = []
    brand = str(normalized.get("brand") or "").strip()
    model = str(normalized.get("model") or "").strip()
    if brand:
        known = {v.lower() for v in _BRAND_ALIASES.values()} | set(_BRAND_ALIASES.keys())
        if brand.lower() not in known and len(brand) < 2:
            warnings.append({
                "field": "brand",
                "code": "BRAND_NOT_RECOGNIZED",
                "message": "Brand not found in master aliases.",
                "suspicious": True,
            })
    if model and re.fullmatch(r"9{3,}", model):
        warnings.append({
            "field": "model",
            "code": "MODEL_NOT_RECOGNIZED",
            "message": "Model does not look valid.",
            "suspicious": True,
        })
    cat = normalized.get("category")
    if cat and not normalize_vehicle_category(str(cat)):
        warnings.append({
            "field": "category",
            "code": "CATEGORY_NOT_RECOGNIZED",
            "message": "Category is not in InfraDealer taxonomy.",
        })
    return warnings


def check_document_consistency(payload: dict, documents: dict | None = None) -> list[dict]:
    return detect_conflicts(payload, documents=documents)


def check_photo_quality(payload: dict, db: "Session | None" = None, conv: "AiConversation | None" = None) -> dict:
    photos = check_photos(payload, db=db, conv=conv)
    received = int(photos.get("received") or 0)
    valid = int(photos.get("valid") or 0)
    if received <= 0:
        score = 0.0
        status = "MISSING"
    elif valid <= 0:
        score = 0.2
        status = "PHOTO_ISSUE"
    elif valid < received:
        score = 0.7
        status = "ACCEPTABLE"
    else:
        score = 0.85
        status = "ACCEPTABLE"
    return {"photo_quality": {"score": score, "status": status}, **photos}


def build_conflicts(payload: dict, field_sources: dict | None = None, documents: dict | None = None) -> list[dict]:
    return detect_conflicts(payload, field_sources=field_sources, documents=documents)


def build_result(result: FilterResult) -> dict:
    return result.as_dict()


def process_listing_intelligence(
    payload: dict,
    *,
    db: "Session | None" = None,
    conv: "AiConversation | None" = None,
    messages: list | None = None,
    field_sources: dict | None = None,
    documents: dict | None = None,
    request_id: str = "",
) -> dict:
    """Full Data Filter entrypoint matching markdown pipeline order."""
    result = filter_payload(
        payload,
        db=db,
        conv=conv,
        messages=messages,
        field_sources=field_sources,
        documents=documents,
        request_id=request_id,
    )
    # Extra type + master-data soft checks folded into output
    type_errs = validate_field_types(result.normalized_data)
    master = check_master_data(result.normalized_data)
    for err in type_errs:
        if err not in result.validation_errors:
            result.validation_errors.append(err)
    for warn in master:
        if warn.get("code") == "CATEGORY_NOT_RECOGNIZED" or not warn.get("suspicious"):
            if warn not in result.validation_errors:
                result.validation_errors.append(warn)
    photo_q = check_photo_quality(payload, db=db, conv=conv)
    result.photos = {**result.photos, **photo_q}
    if type_errs and result.readiness == "READY_FOR_CONFIRMATION":
        result.readiness = "INVALID_DATA"
        result.ready = False
    return result.as_dict()
