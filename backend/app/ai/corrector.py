"""AI Corrector — fixes user message typos, spelling, and incomplete words before the agent processes it.

Flow:
    User msg → correct_user_message() → corrected text → agent (detect_intent → collect → reply)

Rules:
- Fix spelling: "gdi" → "gaadi", "jcB" → "JCB", "tipr" → "Tipper"
- Complete words: "h" → "hain", "bechn" → "bechna"
- Normalize grammar: "bechna h" → "bechna hain"
- NEVER change meaning: "bechna" stays "bechna", never → "kharidna"
- NEVER invent price/year: "40 lakh" stays "40 lakh"
- Skip short messages (Haan, Nahi, Ok, OTP, photo)
- Original text saved for audit; corrected text goes to agent
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiConversation, AiEvent
from ..services import resolve_ai_config

log = logging.getLogger("infradealer.ai.corrector")

# Messages too short to bother correcting
_SHORT_SKIP = re.compile(
    r"^\s*(haan+|ha+|han+|yes|no|nahi+|ok+|okay|ji+|skip|baad\s*me|"
    r"photo|photos?|done|theek|sahi|acha|accha|"
    r"\d{1,8}|\[(?:photo|video|media|document|voice)"
    r"|h+m+|n+m+)\s*$",
    re.I,
)
# Single-word messages
_SINGLE_WORD = re.compile(r"^\s*\S+\s*$")

# Common vehicle/machinery brand typos → canonical
_BRAND_FIXES = {
    "jc b": "JCB", "jcb": "JCB", "jcB": "JCB", "j.c.b": "JCB",
    "tata": "Tata", "ta ta": "Tata",
    "ashok leyland": "Ashok Leyland", "ashokleyland": "Ashok Leyland",
    "leyland": "Ashok Leyland",
    "bharat benz": "BharatBenz", "bharatbenz": "BharatBenz",
    "mahindra": "Mahindra", "mahindraa": "Mahindra",
    "eicher": "Eicher",
    "volvo": "Volvo",
    "hitachi": "Hitachi",
    "komatsu": "Komatsu",
    "sany": "Sany",
}

# Common category typos → canonical
_CATEGORY_FIXES = {
    "tipr": "Tipper", "tipper": "Tipper", "tipar": "Tipper",
    "poclen": "Poclain", "poclain": "Poclain", "pocklen": "Poclain",
    "excavater": "Excavator", "excavator": "Excavator",
    "dumper": "Dumper",
    "crane": "Crane",
    "grader": "Grader",
    "crusher": "Crusher",
    "loader": "Loader",
    "backhoe": "Backhoe Loader",
}

# Common word completions
_WORD_COMPLETIONS = {
    "h": "hain", "hain": "hain", "he": "hain", "hai": "hai",
    "bechn": "bechna", "bechna": "bechna",
    "kharid": "kharidna", "kharidna": "kharidna",
    "chahiy": "chahiye", "chahiye": "chahiye",
    "chahi": "chahiye",
    "gdi": "gaadi", "gaadi": "gaadi", "gari": "gaadi", "gaari": "gaadi",
    "sal": "saal", "saal": "saal",
    "lac": "lakh", "lakh": "lakh", "laakh": "lakh",
    "km": "km", "k.m.": "km",
    "model": "model", "modl": "model",
    "rate": "rate", "kimat": "kimat", "keemat": "kimat",
    "location": "location", "loc": "location",
    "state": "state", "rajya": "state",
}

# Words that should NOT be corrected (prices, years, numbers)
_PRESERVE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:lakh|lac|crore|cr|l|₹|rs\.?)\b", re.I)


def _should_skip(text: str) -> bool:
    """Skip correction for short/clear messages."""
    msg = (text or "").strip()
    if not msg or len(msg) < 3:
        return True
    if _SHORT_SKIP.match(msg):
        return True
    if _SINGLE_WORD.match(msg) and msg.lower() in _WORD_COMPLETIONS:
        return False
    if _SINGLE_WORD.match(msg) and len(msg) < 5:
        return True
    return False


def _fast_correct(text: str) -> str:
    """Fast Python-only correction for known typos — no LLM call needed."""
    msg = text
    # Preserve price/year expressions
    preserved = []
    for m in _PRESERVE.finditer(msg):
        preserved.append(m.group())

    words = msg.split()
    fixed = []
    for word in words:
        lower = word.lower().strip(".,!?;:")
        punct = word[len(word.rstrip(".,!?;:") ):]
        stripped = word.rstrip(".,!?;:")

        # Brand fixes
        if lower in _BRAND_FIXES:
            fixed.append(_BRAND_FIXES[lower])
            continue
        # Category fixes
        if lower in _CATEGORY_FIXES:
            fixed.append(_CATEGORY_FIXES[lower])
            continue
        # Word completions
        if lower in _WORD_COMPLETIONS:
            fixed.append(_WORD_COMPLETIONS[lower])
            continue
        fixed.append(stripped)
    return " ".join(fixed)


def _llm_correct(text: str, lang: str, cfg: dict) -> str:
    """LLM-based correction for messages that need understanding."""
    system_prompt = (
        "You are a message corrector for Hindi/Hinglish WhatsApp text about "
        "used trucks and machinery. Fix ONLY:\n"
        "1. Spelling typos: 'gdi'→'gaadi', 'jcB'→'JCB', 'tipr'→'Tipper'\n"
        "2. Incomplete words: 'h'→'hain', 'bechn'→'bechna'\n"
        "3. Grammar: 'bechna h'→'bechna hain'\n"
        "4. Abbreviations: 'trk'→'Truck', 'poclen'→'Poclain'\n\n"
        "STRICT RULES:\n"
        "- NEVER change meaning: 'bechna' stays 'bechna', never become 'kharidna'\n"
        "- NEVER invent or change price: '40 lakh' stays '40 lakh'\n"
        "- NEVER invent or change year: '2018' stays '2018'\n"
        "- NEVER add information not in the original\n"
        "- Keep it natural Hinglish, same language as input\n"
        "- Output ONLY the corrected message, nothing else\n"
        f"- Reply language: {lang}\n"
    )
    user_block = f"ORIGINAL:\n{text}\n\nCORRECTED:"
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_block},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    try:
        with httpx.Client(timeout=3.5) as client:
            resp = client.post(url, headers=headers, json=body)
            data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        choice = (data.get("choices") or [{}])[0]
        content = str((choice.get("message") or {}).get("content") or "").strip()
        if not content:
            return text
        # Safety: if LLM changed meaning, reject
        if _meaning_changed(text, content):
            log.warning("corrector meaning change rejected: %s → %s", text, content)
            return text
        return content
    except Exception as exc:
        log.warning("corrector LLM error: %s", exc)
        return text


def _meaning_changed(original: str, corrected: str) -> bool:
    """Heuristic: detect if correction changed meaning instead of just fixing typos."""
    orig_lower = original.lower()
    corr_lower = corrected.lower()
    # Check intent words flipped
    for word in ("bechna", "bech", "sell", "kharidna", "kharid", "buy"):
        if word in orig_lower and word not in corr_lower:
            return True
        if word not in orig_lower and word in corr_lower:
            return True
    # Check numbers changed (years / prices / models must stay)
    orig_nums = set(re.findall(r"\d+", original))
    corr_nums = set(re.findall(r"\d+", corrected))
    if orig_nums != corr_nums:
        return True
    # Explicit year tokens must not flip (2021 → 2024)
    orig_years = set(re.findall(r"\b(?:19|20)\d{2}\b", original))
    corr_years = set(re.findall(r"\b(?:19|20)\d{2}\b", corrected))
    if orig_years != corr_years:
        return True
    return False


_CLEAR_INTENT = re.compile(
    r"\b(bech|sell|bechna|bechni|bikau|kharid|buy|kharidna|dumper|tipper|gaadi|gadi)\b",
    re.I,
)
_MULTI_NUM_DUMP = re.compile(r"(?:\d[\d,]{2,}.+){2,}", re.S)


def correct_user_message(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str:
    """Main entry: correct user message typos before agent processes it.

    Returns corrected text. Original text is preserved in DB for audit.
    """
    if not getattr(settings, "ai_corrector", True):
        return text
    msg = (text or "").strip()
    if not msg or _should_skip(msg):
        return text
    if media_note and not msg:
        return text

    lang = getattr(conv, "language", "") or "hinglish"

    # Step 1: Fast Python correction for known typos (always runs, no LLM needed)
    fast_fixed = _fast_correct(msg)

    # Step 2: Decide if LLM correction is needed
    cfg = resolve_ai_config(db)
    llm_ready = bool(cfg.get("enabled") and cfg.get("api_key"))
    word_n = len(msg.split())
    # Clear sell/buy / vehicle cues: prefer fast path — do not burn time on corrector LLM
    clear_intent = bool(_CLEAR_INTENT.search(msg))
    # Multi-line brand/model/year/price dumps — never let LLM invent a different year
    multi_dump = bool(_MULTI_NUM_DUMP.search(msg)) or msg.count("\n") >= 2
    needs_llm = (not clear_intent) and (not multi_dump) and ((fast_fixed != msg) or word_n > 5)

    if fast_fixed != msg and (word_n <= 6 or clear_intent or multi_dump):
        # Fast fix is enough for short / clear-intent / multi-number dumps
        corrected = fast_fixed
    elif needs_llm and llm_ready:
        corrected = _llm_correct(msg, lang, cfg)
        if _meaning_changed(msg, corrected):
            log.warning("corrector meaning change rejected: %s → %s", msg, corrected)
            corrected = fast_fixed if fast_fixed != msg else msg
        else:
            corrected = _fast_correct(corrected)
    else:
        corrected = fast_fixed

    # No change → return original
    if corrected == msg or not corrected:
        return text

    # Audit log
    log.info("corrected: '%s' → '%s' (mobile=***%s)", msg, corrected, conv.mobile[-4:] if conv.mobile else "")
    try:
        db.add(AiEvent(
            wamid=conv.last_wamid or "",
            mobile=conv.mobile,
            event_type="ai_correction",
            detail=f"'{msg[:100]}' → '{corrected[:100]}'",
        ))
    except Exception:
        pass

    return corrected


# ──────────────────────────────────────────────────────────────────────────────
# Message classifier + router
# ──────────────────────────────────────────────────────────────────────────────

# Intents that mean the agent should handle it (confirmed)
_CONFIRMED_INTENTS = frozenset({
    "SELL", "BUY", "CONFIRM_LISTING", "VERIFY_OTP", "UPLOAD_PHOTO",
    "PROVIDE_FIELD", "REJECT_CORRECTION", "RESOLVE_CONFLICT",
    "CHECK_LISTING_STATUS", "ASK_LISTING_LINK", "RESUME_WORKFLOW",
    "CANCEL_WORKFLOW", "HUMAN_SUPPORT", "PROMPT_INJECTION",
})

# Max free chat messages before showing options
_FREE_CHAT_LIMIT = 2  # 0-indexed: msg 0, 1 = free chat; msg 2 = options


def classify_message(text: str, payload: dict, media_note: str = "") -> dict:
    """Classify a user message and decide routing.

    Returns:
        {"route": "confirmed"|"unconfirmed"|"options", "text": corrected_text}
    """
    from .chat_memory import detect_intent

    msg = (text or "").strip()
    intent = detect_intent(msg, payload, media_note=media_note)

    if intent in _CONFIRMED_INTENTS:
        return {"route": "confirmed", "text": text, "intent": intent}

    # GREETING with no intent set → unconfirmed (free chat)
    if intent == "GREETING" and not payload.get("intent"):
        free_count = int(payload.get("free_chat_count") or 0)
        if free_count < _FREE_CHAT_LIMIT:
            return {"route": "unconfirmed", "text": text, "intent": intent, "free_count": free_count}
        return {"route": "options", "text": text, "intent": intent, "free_count": free_count}

    # PROVIDE_FIELD with active listing → confirmed
    if intent == "PROVIDE_FIELD" and payload.get("intent"):
        return {"route": "confirmed", "text": text, "intent": intent}

    # OTHER with active listing → confirmed (could be field data)
    if intent == "OTHER" and payload.get("intent"):
        return {"route": "confirmed", "text": text, "intent": intent}

    # Unconfirmed — check free chat counter
    free_count = int(payload.get("free_chat_count") or 0)

    if free_count < _FREE_CHAT_LIMIT:
        return {"route": "unconfirmed", "text": text, "intent": intent, "free_count": free_count}

    # 3rd unconfirmed → show options
    return {"route": "options", "text": text, "intent": intent, "free_count": free_count}


def reset_free_chat_count(payload: dict) -> dict:
    """Reset free chat counter when user enters a confirmed flow."""
    payload["free_chat_count"] = 0
    return payload
