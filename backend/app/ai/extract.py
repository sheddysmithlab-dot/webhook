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
    low = (text or "").lower()
    states = {
        "madhya pradesh": "Madhya Pradesh",
        "mp": "Madhya Pradesh",
        "maharashtra": "Maharashtra",
        "rajasthan": "Rajasthan",
        "gujarat": "Gujarat",
        "delhi": "Delhi",
        "up": "Uttar Pradesh",
        "uttar pradesh": "Uttar Pradesh",
        "karnataka": "Karnataka",
        "telangana": "Telangana",
        "tamil nadu": "Tamil Nadu",
        "west bengal": "West Bengal",
    }
    for cue, label in sorted(states.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", low):
            return label
    return None


def infer_state_from_city(city: str) -> str | None:
    mapping = {
        "indore": "Madhya Pradesh",
        "bhopal": "Madhya Pradesh",
        "mumbai": "Maharashtra",
        "pune": "Maharashtra",
        "jaipur": "Rajasthan",
        "ahmedabad": "Gujarat",
        "delhi": "Delhi",
        "lucknow": "Uttar Pradesh",
        "hyderabad": "Telangana",
        "chennai": "Tamil Nadu",
        "bengaluru": "Karnataka",
        "bangalore": "Karnataka",
        "kolkata": "West Bengal",
    }
    return mapping.get((city or "").strip().lower())


def _fuzzy_city(text: str) -> str | None:
    cities = (
        "Indore", "Bhopal", "Mumbai", "Pune", "Jaipur", "Ahmedabad",
        "Delhi", "Lucknow", "Hyderabad", "Chennai", "Bengaluru", "Kolkata",
    )
    low = (text or "").lower()
    for city in cities:
        if city.lower() in low:
            return city
    return None


def extract_from_text(text: str, extra_reps=None) -> dict:
    """Minimal NL harvest used by legacy engine paths."""
    from .data_filteration import extract_fields, resolve_category

    out = extract_fields([{"text": text or "", "source": "USER"}], {})
    cat = resolve_category(text)
    if cat:
        out["category"] = cat
    st = extract_state(text or "")
    if st:
        out["state"] = st
    city = _fuzzy_city(text or "")
    if city:
        out["city"] = city
        out["location"] = city
    return out
