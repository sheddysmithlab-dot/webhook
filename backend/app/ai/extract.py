"""Lightweight extract helpers for account / listing cues."""

from __future__ import annotations

import re

_ROLE = {
    "broker": "broker",
    "dealer": "broker",
    "user": "user",
    "seller": "user",
    "buyer": "user",
    "owner": "user",
}


def extract_role(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    for cue, role in _ROLE.items():
        if re.search(rf"\b{re.escape(cue)}\b", low):
            return role
    if re.search(r"ब्रोकर|डीलर", text or ""):
        return "broker"
    return None


def extract_state(text: str) -> str | None:
    """Detect Indian state names. Avoid false hits like 'price up' → Uttar Pradesh."""
    from .data_filteration import _match_state_name

    return _match_state_name(text or "")


def infer_state_from_city(city: str) -> str | None:
    mapping = {
        "indore": "Madhya Pradesh",
        "bhopal": "Madhya Pradesh",
        "ujjain": "Madhya Pradesh",
        "jabalpur": "Madhya Pradesh",
        "gwalior": "Madhya Pradesh",
        "khandwa": "Madhya Pradesh",
        "khargone": "Madhya Pradesh",
        "dewas": "Madhya Pradesh",
        "ratlam": "Madhya Pradesh",
        "burhanpur": "Madhya Pradesh",
        "harda": "Madhya Pradesh",
        "sehore": "Madhya Pradesh",
        "mandsaur": "Madhya Pradesh",
        "barwani": "Madhya Pradesh",
        "mumbai": "Maharashtra",
        "pune": "Maharashtra",
        "nagpur": "Maharashtra",
        "jaipur": "Rajasthan",
        "ahmedabad": "Gujarat",
        "surat": "Gujarat",
        "delhi": "Delhi",
        "lucknow": "Uttar Pradesh",
        "kanpur": "Uttar Pradesh",
        "hyderabad": "Telangana",
        "chennai": "Tamil Nadu",
        "bengaluru": "Karnataka",
        "bangalore": "Karnataka",
        "kolkata": "West Bengal",
    }
    return mapping.get((city or "").strip().lower())


_HINDI_CITIES = {
    "इंदौर": "Indore",
    "भोपाल": "Bhopal",
    "उज्जैन": "Ujjain",
    "जबलपुर": "Jabalpur",
    "ग्वालियर": "Gwalior",
    "खंडवा": "Khandwa",
    "खण्डवा": "Khandwa",
    "खरगोन": "Khargone",
    "देवास": "Dewas",
    "रतलाम": "Ratlam",
    "बुरहानपुर": "Burhanpur",
    "हरदा": "Harda",
    "सीहोर": "Sehore",
    "मंदसौर": "Mandsaur",
    "बड़वानी": "Barwani",
    "मुंबई": "Mumbai",
    "पुणे": "Pune",
    "नागपुर": "Nagpur",
    "जयपुर": "Jaipur",
    "दिल्ली": "Delhi",
}


def _fuzzy_city(text: str) -> str | None:
    cities = (
        "Indore", "Bhopal", "Ujjain", "Jabalpur", "Gwalior",
        "Khandwa", "Khargone", "Dewas", "Ratlam", "Burhanpur", "Harda", "Sehore", "Mandsaur", "Barwani",
        "Mumbai", "Pune", "Nagpur", "Jaipur", "Ahmedabad", "Surat",
        "Delhi", "Lucknow", "Kanpur", "Hyderabad", "Chennai", "Bengaluru", "Kolkata",
    )
    low = (text or "").lower()
    for city in cities:
        if re.search(rf"(?<![a-z]){re.escape(city.lower())}(?![a-z])", low):
            return city
    for hindi, city in _HINDI_CITIES.items():
        if hindi in (text or ""):
            return city
    return None


def extract_from_text(text: str, extra_reps=None) -> dict:
    """Minimal NL harvest used by legacy engine paths."""
    from .data_filteration import extract_fields, resolve_category, _looks_like_place

    out = extract_fields([{"text": text or "", "source": "USER"}], {})
    cat = resolve_category(text)
    if cat:
        out["category"] = cat
    st = extract_state(text or "")
    if st:
        out["state"] = st
        if not out.get("location") and _looks_like_place(text or ""):
            out["location"] = st
    city = _fuzzy_city(text or "")
    if city:
        out["city"] = city
        out["location"] = city
    # Drop garbage location/state left by older extract paths
    for key in ("location", "state", "city"):
        val = out.get(key)
        if val and not _looks_like_place(str(val)) and not extract_state(str(val)) and not _fuzzy_city(str(val)):
            out.pop(key, None)
    return out
