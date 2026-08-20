"""Clean Z.AI-only WhatsApp chat — no listing/card/account/OTP workflows.

Old engine/cards/confirm/account intelligence is intentionally unused here.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation, Chat
from ..services import resolve_ai_config
from .prompt import SIMPLE_SYSTEM_PROMPT

log = logging.getLogger("infradealer.ai.simple")

_FALLBACK = (
    "Ji, abhi network thoda slow hai. "
    "Ek chhota message bhej dijiye — main turant jawab dunga."
)

_GREET = re.compile(
    r"^\s*(hi+|hii+|hello|hey+|namaste|namaskar|hola|"
    r"kaise\s*ho|kya\s*haal|sab\s*theek|good\s*morning|good\s*evening)"
    r"[\s!?.]*$",
    re.I,
)

_LISTING_BIAS = re.compile(
    r"(?i)(to proceed with listing|listing|CARD-\d+|bechni hai ya|otp|"
    r"kripya.*(brand|model|year|photo|state)|"
    r"provide a few more details|numbered|"
    r"^\s*\d+\.\s*(what|which|how|truck|model|cabin|registration))",
)


def _history(db: Session, conversation_id: str, limit: int = 8) -> list[dict]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id)
        .order_by(Chat.id.desc())
        .limit(limit * 2)  # fetch extra so we can filter listing-biased turns
        .all()
    )
    out: list[dict] = []
    for row in reversed(rows):
        role = "assistant" if row.direction == "outbound" else "user"
        body = (row.body or "").strip()
        if not body:
            continue
        body = re.sub(r"(?i)^\s*reply\s+", "", body).strip()
        if not body:
            continue
        # Drop old listing-form replies so they cannot steer the new clean chat
        if role == "assistant" and _LISTING_BIAS.search(body):
            continue
        if role == "assistant" and "technical dikkat" in body.lower():
            continue
        if role == "assistant" and "network thoda slow" in body.lower():
            continue
        out.append({"role": role, "content": body[:400]})
    return out[-limit:]


def _sanitize(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"(?is)otp\s*[:\-]*\s*\d{4,8}", "OTP", raw)
    raw = re.sub(
        r"(?i)(sk-|bearer\s+|api[_-]?key|system prompt|AUTH_|META_SYSTEM|AI_API_KEY)",
        "[blocked]",
        raw,
    )
    raw = re.sub(r"(?im)^\s*reply\s+[^\n]*$", "", raw)
    raw = re.sub(r"(?i)^\s*reply\s+", "", raw).strip()
    # Soft-block listing questionnaire style if model slips
    if _LISTING_BIAS.search(raw) and re.search(r"(?m)^\s*\d+\.\s+", raw):
        return (
            "Photo / baat samajh gaya. "
            "Aap freely bataiye — main normal chat me help karunga. "
            "Listing form abhi start nahi kar raha."
        )
    if len(raw) > 700:
        raw = raw[:690].rsplit(" ", 1)[0] + "…"
    return raw


def _user_content(text: str, media_note: str = "") -> str:
    msg = re.sub(r"(?i)^\s*reply\s+", "", (text or "").strip()).strip()
    note = (media_note or "").strip()
    if note and "DOWNLOAD_FAILED" in note:
        return (msg + "\n[media download failed]").strip() or "[media]"
    if note.startswith("image") or note.startswith("photo") or "[photo]" in msg.lower():
        if not msg or msg in {"[photo]", "[image]"}:
            return "[User sent a photo — acknowledge casually; do NOT start a listing form]"
        return f"{msg}\n[User also sent a photo — do NOT start a listing form]"
    if note.startswith("audio") or msg == "[voice note]":
        return "[User sent a voice note — politely ask them to type a short message]"
    if note.startswith("video") or msg == "[video]":
        return "[User sent a video — acknowledge casually; no listing form]"
    if note.startswith("document") or msg == "[document]":
        return "[User sent a document — acknowledge casually]"
    return msg or "[empty message]"


def _local_fast_reply(text: str, media_note: str = "") -> str | None:
    """Skip Z.AI for trivial turns — avoids timeout/429 and listing invent."""
    msg = (text or "").strip()
    note = (media_note or "").strip().lower()
    is_photo = (
        note.startswith("image")
        or note.startswith("photo")
        or msg.lower() in {"[photo]", "[image]"}
    )
    if is_photo and (not msg or msg.lower() in {"[photo]", "[image]"}):
        return (
            "Photo mil gaya 👍 "
            "Bataiye aap kis baare me baat karna chahte hain — "
            "main normal chat me help karunga."
        )
    if _GREET.match(msg):
        return "Namaste! Main theek hoon. Aap kaise hain? Bataiye, aaj kis baare me baat karni hai?"
    return None


def simple_respond(
    db: Session,
    conv: AiConversation,
    text: str,
    media_note: str = "",
) -> str:
    """Normal WhatsApp conversation via Z.AI only. No listing/card/account flows."""
    conv.state = "CHAT"
    conv.error_message = ""

    fast = _local_fast_reply(text, media_note)
    if fast:
        return fast

    cfg = resolve_ai_config(db)
    if cfg.get("config_error") and not cfg.get("api_key"):
        log.error("simple_chat skipped: %s", cfg["config_error"])
        return _FALLBACK
    if not cfg.get("enabled") or not cfg.get("api_key"):
        log.error("simple_chat skipped: %s", cfg.get("config_error") or "Z.AI not configured")
        return _FALLBACK
    if "z.ai" not in (cfg.get("api_base") or "").lower():
        log.error("simple_chat skipped: non-Z.AI base %s", cfg.get("api_base"))
        return _FALLBACK

    history = _history(db, conv.conversation_id, limit=8)
    user_msg = _user_content(text, media_note)
    if history and history[-1].get("role") == "user":
        history = history[:-1]
    messages = [{"role": "system", "content": SIMPLE_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg[:800]})

    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en",
    }
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 180,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }

    last_err = ""
    for attempt in range(2):
        try:
            with httpx.Client(timeout=25.0) as client:
                resp = client.post(url, headers=headers, json=body)
            data = resp.json() if resp.content else {}
            if resp.status_code == 429:
                last_err = "429 rate limit"
                log.warning("simple_chat Z.AI 429 attempt=%s", attempt + 1)
                time.sleep(1.2 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                err = ""
                if isinstance(data, dict):
                    err = str((data.get("error") or {}).get("message") or data)[:240]
                log.error("simple_chat Z.AI http %s %s", resp.status_code, err or (resp.text or "")[:240])
                return _FALLBACK
            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            msg = choice.get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                log.warning("simple_chat empty content keys=%s", list(msg.keys()) if isinstance(msg, dict) else msg)
                return _FALLBACK
            return _sanitize(content) or _FALLBACK
        except httpx.TimeoutException:
            last_err = "timeout"
            log.error("simple_chat Z.AI timeout attempt=%s", attempt + 1)
            continue
        except Exception as exc:
            log.exception("simple_chat Z.AI error: %s", exc)
            return _FALLBACK

    log.error("simple_chat giving fallback after retries (%s)", last_err)
    return _FALLBACK
