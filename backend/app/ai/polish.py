"""Z.AI response step: rules decide WHAT to say, Z.AI phrases it for WhatsApp.

Flow per turn:
    user msg → Z.AI corrector (understanding) → rules/tools (backend tasks)
    → Z.AI polish (natural reply) → WhatsApp

The rules' draft is the source of truth. A rewrite is used only when it keeps
every number, link and question of the draft; otherwise the draft is sent.
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
_MAX_DRAFT = 450
_URL = re.compile(r"https?://\S+", re.I)
_FIELD_LINE = re.compile(r"^\s*[^\n:]{1,24}\s*:\s*\S", re.M)
_SENSITIVE = re.compile(
    r"otp|password|पासवर्ड|username|user\s*id|wallet|token|card\s*:|confirm|कन्फर्म|post\s*to:|"
    r"submit|सबमिट|review|रिव्यू|live|लाइव|reject|delete|account\s*(ban|create)",
    re.I,
)
_LANG_NAME = {"hi": "Hindi (Devanagari)", "hindi": "Hindi (Devanagari)", "en": "English", "english": "English"}


def _numbers(text: str) -> list[str]:
    return sorted(re.findall(r"\d+", text or ""))


def should_polish(draft: str) -> bool:
    msg = (draft or "").strip()
    if not msg or len(msg) > _MAX_DRAFT:
        return False
    if _URL.search(msg) or _SENSITIVE.search(msg):
        return False
    return len(_FIELD_LINE.findall(msg)) < 2


def rewrite_is_safe(draft: str, rewrite: str) -> bool:
    from .engine import _claims_submission

    out = (rewrite or "").strip()
    if not out or len(out) > max(2 * len(draft), len(draft) + 160):
        return False
    if _numbers(out) != _numbers(draft) or _URL.search(out):
        return False
    if ("?" in draft) != ("?" in out):
        return False
    return not _claims_submission(out)


def _call_zai(cfg: dict, user_text: str, draft: str, lang: str) -> str:
    language = _LANG_NAME.get((lang or "").lower(), "Hinglish (Roman Hindi)")
    system = (
        "You are the WhatsApp voice of InfraDealer (used trucks & construction machinery marketplace). "
        "Rewrite the DRAFT reply into one short, warm, natural WhatsApp message that sounds like a "
        "friendly human sales executive, not a bot. You may open with a brief acknowledgement of the "
        "customer's message (e.g. 'Bahut badhiya Sir', 'Samajh gaya Sir') without repeating its details.\n"
        "STRICT: keep exactly the same meaning and the same question; keep every number, brand, model "
        "and option exactly; do not add facts, offers, links or promises; never say a listing is "
        "submitted, posted, live or under review; address the user as Sir/Ma'am only; no greetings "
        f"with names. Write in {language}. Output only the message."
    )
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"CUSTOMER_MESSAGE:\n{(user_text or '')[:400]}\n\nDRAFT:\n{draft}"},
        ],
        "temperature": 0.3,
        "max_tokens": 220,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    resp = zai_chat(cfg, body, float(getattr(settings, "ai_polish_timeout", 6.0)))
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
