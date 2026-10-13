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
        "nashik": "Maharashtra",
        "thane": "Maharashtra",
        "satara": "Maharashtra",
        "kolhapur": "Maharashtra",
        "solapur": "Maharashtra",
        "ahmednagar": "Maharashtra",
        "aurangabad": "Maharashtra",
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
    "नागपूर": "Nagpur",
    "जयपुर": "Jaipur",
    "दिल्ली": "Delhi",
    "नाशिक": "Nashik",
    "ठाणे": "Thane",
    "सातारा": "Satara",
    "कोल्हापूर": "Kolhapur",
    "कोल्हापुर": "Kolhapur",
    "सोलापूर": "Solapur",
    "सोलापुर": "Solapur",
    "अहमदनगर": "Ahmednagar",
    "औरंगाबाद": "Aurangabad",
    "लखनऊ": "Lucknow",
    "कानपुर": "Kanpur",
    "अहमदाबाद": "Ahmedabad",
    "सूरत": "Surat",
}

# "Mumbai-Pune road" / "मुंबई-पुणे रोड" names a highway, not where the vehicle stands.
_ROAD_RE = re.compile(
    r"[A-Za-z\u0900-\u097F]+\s*[-–]\s*[A-Za-z\u0900-\u097F]+\s+(?:road|rd\.?|highway|marg|रोड|हाईवे|हायवे|महामार्ग|मार्ग)",
    re.I,
)
# "जि. पुणे" / "जिल्हा सातारा" / "Dist. Pune" — the district is the listing city.
_DISTRICT_RE = re.compile(
    r"(?:(?<![\u0900-\u097F])(?:जि\.|जिल्हा|जिला|ज़िला)|\b(?:dist(?:rict)?\.?|jilla|zila|zilla))"
    r"\s*[:\-–]?\s*([A-Za-z]{3,30}|[\u0900-\u097F]{2,30})",
    re.I,
)


def district_city(text: str) -> str | None:
    m = _DISTRICT_RE.search(text or "")
    if not m:
        return None
    name = m.group(1).strip()
    return _fuzzy_city(name) or _HINDI_CITIES.get(name) or (name.title() if name.isascii() else name)


def _fuzzy_city(text: str) -> str | None:
    cities = (
        "Indore", "Bhopal", "Ujjain", "Jabalpur", "Gwalior",
        "Khandwa", "Khargone", "Dewas", "Ratlam", "Burhanpur", "Harda", "Sehore", "Mandsaur", "Barwani",
        "Mumbai", "Pune", "Nagpur", "Jaipur", "Ahmedabad", "Surat",
        "Delhi", "Lucknow", "Kanpur", "Hyderabad", "Chennai", "Bengaluru", "Kolkata",
        "Nashik", "Thane", "Satara", "Kolhapur", "Solapur", "Ahmednagar", "Aurangabad",
    )
    text = _ROAD_RE.sub(" ", text or "")
    low = text.lower()
    for city in cities:
        if re.search(rf"(?<![a-z]){re.escape(city.lower())}(?![a-z])", low):
            return city
    for hindi, city in _HINDI_CITIES.items():
        if hindi in (text or ""):
            return city
    return None


def extract_from_text(text: str, extra_reps=None) -> dict:
    """Minimal NL harvest used by legacy engine paths."""
    from .data_filteration import extract_fields, listing_paste_description, resolve_category, _looks_like_place
    from .i18n import normalize_input_text

    text = normalize_input_text(text or "")
    out = extract_fields([{"text": text, "source": "USER"}], {})
    cat = resolve_category(text)
    if cat:
        out["category"] = cat
    st = extract_state(text or "")
    if st:
        out["state"] = st
        if not out.get("location") and _looks_like_place(text or ""):
            out["location"] = st
    city = district_city(text) or _fuzzy_city(text or "")
    if city:
        out["city"] = city
        out["location"] = city
        if not st and infer_state_from_city(city):
            out["state"] = infer_state_from_city(city)
    if (out.get("brand") or out.get("category")) and not out.get("description"):
        desc = listing_paste_description(text)
        if desc:
            out["description"] = desc
    # Drop garbage location/state left by older extract paths
    for key in ("location", "state", "city"):
        val = out.get(key)
        if val and not _looks_like_place(str(val)) and not extract_state(str(val)) and not _fuzzy_city(str(val)):
            out.pop(key, None)
    return out
