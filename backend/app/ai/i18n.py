"""InfraDealer AI — multilingual i18n infrastructure.

Messages live in i18n_messages.py (key-centric dict).
This module provides: t(), pick_language(), validate_dictionary(), etc.
"""

from __future__ import annotations

import logging
import re

from .i18n_messages import MESSAGES

log = logging.getLogger("infradealer.ai.i18n")

DEFAULT_LANG = "hinglish"
SUPPORTED_LANGS = (
    "hinglish", "hi", "en",
    "pa", "pa_roman", "gu", "gu_roman", "mr", "mr_roman",
    "ta", "ta_roman", "te", "te_roman", "kn", "kn_roman",
    "ml", "ml_roman", "bn", "bn_roman", "ur", "ur_roman",
)
POLICY_LANGS = ("auto",) + SUPPORTED_LANGS

LANG_LABELS = {
    "auto": "Auto-detect",
    "hinglish": "Hinglish (Roman Hindi)",
    "hi": "Hindi (Devanagari)",
    "en": "English",
    "pa": "Punjabi (Gurmukhi)",
    "pa_roman": "Punjabi (Roman)",
    "gu": "Gujarati",
    "gu_roman": "Gujarati (Roman)",
    "mr": "Marathi (Devanagari)",
    "mr_roman": "Marathi (Roman)",
    "ta": "Tamil",
    "ta_roman": "Tamil (Roman)",
    "te": "Telugu",
    "te_roman": "Telugu (Roman)",
    "kn": "Kannada",
    "kn_roman": "Kannada (Roman)",
    "ml": "Malayalam",
    "ml_roman": "Malayalam (Roman)",
    "bn": "Bengali",
    "bn_roman": "Bengali (Roman)",
    "ur": "Urdu",
    "ur_roman": "Urdu (Roman)",
}

FALLBACK_CHAIN = {
    "hi": ["hinglish", "en"],
    "hinglish": ["en"],
    "pa": ["pa_roman", "hinglish", "en"],
    "pa_roman": ["hinglish", "en"],
    "gu": ["gu_roman", "hinglish", "en"],
    "gu_roman": ["hinglish", "en"],
    "mr": ["mr_roman", "hi", "hinglish", "en"],
    "mr_roman": ["hinglish", "en"],
    "ta": ["ta_roman", "en"],
    "ta_roman": ["en"],
    "te": ["te_roman", "en"],
    "te_roman": ["en"],
    "kn": ["kn_roman", "en"],
    "kn_roman": ["en"],
    "ml": ["ml_roman", "en"],
    "ml_roman": ["en"],
    "bn": ["bn_roman", "hinglish", "en"],
    "bn_roman": ["hinglish", "en"],
    "ur": ["ur_roman", "en"],
    "ur_roman": ["en"],
    "en": ["hinglish"],
}

# Script detection
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_GURMUKHI = re.compile(r"[\u0A00-\u0A7F]")
_GUJARATI = re.compile(r"[\u0A80-\u0AFF]")
_TAMIL = re.compile(r"[\u0B80-\u0BFF]")
_TELUGU = re.compile(r"[\u0C00-\u0C7F]")
_KANNADA = re.compile(r"[\u0C80-\u0CFF]")
_MALAYALAM = re.compile(r"[\u0D00-\u0D7F]")
_BENGALI = re.compile(r"[\u0980-\u09FF]")
_URDU = re.compile(r"[\u0600-\u06FF]")

_SCRIPT_MAP = [
    ("hi", _DEVANAGARI), ("pa", _GURMUKHI), ("gu", _GUJARATI),
    ("ta", _TAMIL), ("te", _TELUGU), ("kn", _KANNADA),
    ("ml", _MALAYALAM), ("bn", _BENGALI), ("ur", _URDU),
]

_GREET = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|hola)\b", re.I)
_WEAK = re.compile(r"^\s*(ok+|okay|hmm+|han|haan|ji)\s*$", re.I)

_ROMAN_HINTS = [
    ("pa_roman", re.compile(r"\b(sat\s*sri|balle|kida|jao)\b", re.I)),
    ("gu_roman", re.compile(r"\b(kem\s*cho|ahu|bhalu|khabar)\b", re.I)),
    ("mr_roman", re.compile(r"\b(kay\s*mi|bharat|kasa|barobar|aho)\b", re.I)),
    ("ta_roman", re.compile(r"\b(vanakkam|sollu|saaptu|illai|romba)\b", re.I)),
    ("te_roman", re.compile(r"\b(namaskaram|cheppu|undhi|ledhu|vastha)\b", re.I)),
    ("kn_roman", re.compile(r"\b(namaskara|hogi|illa|haage|yelli)\b", re.I)),
    ("ml_roman", re.compile(r"\b(namaskaram|undu|alla|engotto|enthu)\b", re.I)),
    ("bn_roman", re.compile(r"\b(namaskar|ache|kemon|bhalo|kichu)\b", re.I)),
    ("ur_roman", re.compile(r"\b(adab|salam|kya|haan|nahin|theek)\b", re.I)),
]


def normalize_policy(value: str | None) -> str:
    raw = (value or "auto").strip().lower()
    aliases = {"en": "en", "english": "en", "hi": "hi", "hindi": "hi"}
    if raw in aliases:
        return aliases[raw]
    if raw in POLICY_LANGS:
        return raw
    return "auto"


def normalize_reply(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in SUPPORTED_LANGS:
        return raw
    return DEFAULT_LANG


def pick_language(text: str = "", previous: str = "", policy: str = "auto") -> str:
    pol = normalize_policy(policy)
    if pol in SUPPORTED_LANGS:
        return pol
    if previous in SUPPORTED_LANGS:
        if text:
            for lang, pat in _SCRIPT_MAP:
                if pat.search(text) and previous != lang:
                    return lang
            if _DEVANAGARI.search(text) and previous not in ("hi", "mr"):
                return "hi"
            if re.search(r"\b(the|please|what|how|yes|no)\b", text, re.I) and not _DEVANAGARI.search(text):
                if previous in ("hi", "mr") and re.search(r"\b(the|please|what)\b", text, re.I):
                    return "en"
        return previous
    if text:
        for lang, pat in _SCRIPT_MAP:
            if pat.search(text):
                return lang
        if _DEVANAGARI.search(text):
            return "hi"
        for rlang, pat in _ROMAN_HINTS:
            if pat.search(text):
                return rlang
        if re.search(r"\b(the|please|what|how|yes|want to)\b", text or "", re.I):
            return "en"
    return DEFAULT_LANG


def language_instruction(lang: str = "hinglish") -> str:
    instructions = {
        "hi": "Reply in simple Hindi (Devanagari script).",
        "en": "Reply in clear simple English.",
        "pa": "Reply in Punjabi (Gurmukhi script).",
        "pa_roman": "Reply in Roman Punjabi (Latin script).",
        "gu": "Reply in Gujarati script.",
        "gu_roman": "Reply in Roman Gujarati.",
        "mr": "Reply in Marathi (Devanagari script).",
        "mr_roman": "Reply in Roman Marathi.",
        "ta": "Reply in Tamil script.",
        "ta_roman": "Reply in Roman Tamil.",
        "te": "Reply in Telugu script.",
        "te_roman": "Reply in Roman Telugu.",
        "kn": "Reply in Kannada script.",
        "kn_roman": "Reply in Roman Kannada.",
        "ml": "Reply in Malayalam script.",
        "ml_roman": "Reply in Roman Malayalam.",
        "bn": "Reply in Bengali script.",
        "bn_roman": "Reply in Roman Bengali.",
        "ur": "Reply in Urdu (Nastaliq script).",
        "ur_roman": "Reply in Roman Urdu.",
    }
    return instructions.get(lang, "Reply in Hinglish (Hindi + English, Latin script).")


def t(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    entry = MESSAGES.get(key)
    if not entry:
        log.warning("i18n missing key: %s", key)
        return f"[{key}]"
    text = entry.get(lang)
    if not text:
        for fb in FALLBACK_CHAIN.get(lang, [DEFAULT_LANG, "en"]):
            text = entry.get(fb)
            if text:
                break
    if not text:
        text = entry.get(DEFAULT_LANG) or entry.get("en") or ""
    if not text:
        log.warning("i18n no translation for key: %s lang: %s", key, lang)
        return f"[{key}]"
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("i18n format failed for %s: %s", key, exc)
        return text


def validate_dictionary() -> list[str]:
    issues = []
    for key, langs in MESSAGES.items():
        if not isinstance(langs, dict):
            issues.append(f"Key '{key}' has invalid structure")
            continue
        for lang in SUPPORTED_LANGS:
            if lang not in langs or not langs[lang]:
                issues.append(f"Missing {lang} for key '{key}'")
    return issues


def all_keys() -> list[str]:
    return sorted(MESSAGES.keys())


def keys_for_lang(lang: str) -> list[str]:
    return sorted(k for k, v in MESSAGES.items() if isinstance(v, dict) and v.get(lang))
