"""Z.AI response step: rules decide WHAT to say, Z.AI phrases it for WhatsApp.

Flow per turn:
    user msg → Z.AI corrector (understanding) → rules/tools (backend tasks)
    → Z.AI polish (natural reply) → WhatsApp

The rules' draft is the source of truth. Z.AI formats it for WhatsApp and fixes
garbled or repeated text; the rewrite is used only when it keeps every number,
link, question and YES/NO prompt of the draft; otherwise the draft is sent.
"""

from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar

from sqlalchemy.orm import Session

from ..config import settings
from ..services import resolve_ai_config, zai_chat

log = logging.getLogger("infradealer.ai.polish")

# Set by llm_reply / free_chat when the reply text itself came from Z.AI.
LLM_WROTE_REPLY: ContextVar[bool] = ContextVar("llm_wrote_reply", default=False)

_PAUSE_AFTER_ERROR = 60.0
_pause_until = 0.0
_MAX_DRAFT = 1600
_URL = re.compile(r"https?://\S+", re.I)
# Secrets and one-time codes never leave the backend.
_SECRET = re.compile(r"otp|password|पासवर्ड|ओटीपी|username|user\s*id\s*:", re.I)
_YES_NO = re.compile(r"yes\s*/\s*no", re.I)
_LANG_NAME = {"hi": "Hindi (Devanagari)", "hindi": "Hindi (Devanagari)", "en": "English", "english": "English"}


def _numbers(text: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+", text or "")}


def should_polish(draft: str) -> bool:
    msg = (draft or "").strip()
    return bool(msg) and len(msg) <= _MAX_DRAFT and not _SECRET.search(msg)


def rewrite_is_safe(draft: str, rewrite: str) -> bool:
    from .engine import _claims_submission

    out = (rewrite or "").strip()
    if not out or len(out) > max(2 * len(draft), len(draft) + 300):
        return False
    if _numbers(out) != _numbers(draft):
        return False
    if set(_URL.findall(out)) != set(_URL.findall(draft)):
        return False
    if ("?" in draft) != ("?" in out):
        return False
    if _YES_NO.search(draft) and not _YES_NO.search(out):
        return False
    return _claims_submission(draft) or not _claims_submission(out)


def _call_zai(cfg: dict, user_text: str, draft: str, lang: str) -> str:
    language = _LANG_NAME.get(
        (lang or "").lower(), "the same language and script as the DRAFT (Hinglish = Roman Hindi)",
    )
    system = (
        "You are the WhatsApp voice of InfraDealer (used trucks & construction machinery marketplace). "
        "Turn the DRAFT into one clean, well-formatted WhatsApp message that sounds like a friendly "
        "human sales executive. You may open with a brief acknowledgement of the customer's message "
        "(e.g. 'Bahut badhiya Sir', 'Samajh gaya Sir') without repeating its details.\n"
        "FORMAT: a *bold* heading only when the draft lists details; one detail per line as "
        "'• Label: value'; blank line between sections; short lines; end with the single question or "
        "next step from the draft. Use • bullets, never numbered lists. Plain messages stay 1-3 lines.\n"
        "FIX: remove repeated sentences or duplicate detail lines, fix broken/garbled or half-cut "
        "sentences, mixed-up labels and grammar.\n"
        "STRICT: keep every fact — every number, date, registration/card id, phone number, link, brand, "
        "model, option and 'YES/NO' line exactly; keep the same question; do not add facts, offers, "
        "links or promises; never say a listing is submitted, posted, live or approved unless the "
        "DRAFT says so; address the user as Sir/Ma'am only; no greetings with names. "
        f"Write in {language}. Output only the message."
    )
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"CUSTOMER_MESSAGE:\n{(user_text or '')[:400]}\n\nDRAFT:\n{draft}"},
        ],
        "temperature": 0.3,
        "max_tokens": 900,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    timeout = float(getattr(settings, "ai_polish_timeout", 6.0))
    if len(draft) > 300:
        timeout = max(timeout, 12.0)
    resp = zai_chat(cfg, body, timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"http {resp.status_code}")
    data = resp.json() if resp.content else {}
    choice = ((data or {}).get("choices") or [{}])[0]
    return str((choice.get("message") or {}).get("content") or "").strip()


def polish_reply(db: Session, user_text: str, draft: str, lang: str) -> tuple[str, str]:
    """Returns (reply, outcome); outcome is polished / kept / skipped / off / error."""
    if not getattr(settings, "ai_polish", True):
        return draft, "off"
    cfg = resolve_ai_config(db)
    if not (cfg.get("enabled") and cfg.get("api_key")):
        return draft, "off"
    if not should_polish(draft):
        return draft, "skipped"
    global _pause_until
    if time.monotonic() < _pause_until:
        return draft, "paused"
    try:
        out = _call_zai(cfg, user_text, draft, lang)
    except Exception as exc:
        # Z.AI is slow/overloaded: stop adding its timeout to every reply for a while.
        _pause_until = time.monotonic() + _PAUSE_AFTER_ERROR
        log.warning("ai.polish error: %s", exc)
        return draft, "error"
    if not rewrite_is_safe(draft, out):
        return draft, "kept"
    return out, "polished"
