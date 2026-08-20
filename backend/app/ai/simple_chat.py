"""Clean Z.AI-only WhatsApp chat — no listing/card/account/OTP workflows.

Old engine/cards/confirm/account intelligence is intentionally unused here.
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation, Chat
from ..services import resolve_ai_config
from .prompt import SIMPLE_SYSTEM_PROMPT

log = logging.getLogger("infradealer.ai.simple")

_FALLBACK = (
    "Ji, abhi thodi technical dikkat aa rahi hai. "
    "Kripya ek pal baad phir se message bhej dijiye."
)


def _history(db: Session, conversation_id: str, limit: int = 12) -> list[dict]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id)
        .order_by(Chat.id.desc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for row in reversed(rows):
        role = "assistant" if row.direction == "outbound" else "user"
        body = (row.body or "").strip()
        if not body:
            continue
        # Never feed profile-name junk / Reply prefix into context
        body = re.sub(r"(?i)^\s*reply\s+", "", body).strip()
        if not body:
            continue
        out.append({"role": role, "content": body[:500]})
    return out


def _sanitize(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"(?is)otp\s*[:\-]*\s*\d{4,8}", "OTP", raw)
    raw = re.sub(
        r"(?i)(sk-|bearer\s+|api[_-]?key|system prompt|AUTH_|META_SYSTEM|AI_API_KEY)",
        "[blocked]",
        raw,
    )
    # Strip Meta "Reply Name" style lines / prefixes
    raw = re.sub(r"(?im)^\s*reply\s+[^\n]*$", "", raw)
    raw = re.sub(r"(?i)^\s*reply\s+", "", raw).strip()
    if len(raw) > 900:
        raw = raw[:890].rsplit(" ", 1)[0] + "…"
    return raw


def _user_content(text: str, media_note: str = "") -> str:
    msg = re.sub(r"(?i)^\s*reply\s+", "", (text or "").strip()).strip()
    note = (media_note or "").strip()
    if note and "DOWNLOAD_FAILED" in note:
        return (msg + "\n[media download failed]").strip() or "[media]"
    if note.startswith("image") or note.startswith("photo") or "[photo]" in msg.lower():
        if not msg or msg in {"[photo]", "[image]"}:
            return "[User sent a photo]"
        return f"{msg}\n[User also sent a photo]"
    if note.startswith("audio") or msg == "[voice note]":
        return "[User sent a voice note — ask them to type if needed]"
    if note.startswith("video") or msg == "[video]":
        return "[User sent a video]"
    if note.startswith("document") or msg == "[document]":
        return "[User sent a document]"
    return msg or "[empty message]"


def simple_respond(
    db: Session,
    conv: AiConversation,
    text: str,
    media_note: str = "",
) -> str:
    """Normal WhatsApp conversation via Z.AI only. No listing/card/account flows."""
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

    # Soft mark conversation as plain chat — do not run old listing state machine
    conv.state = "CHAT"
    conv.error_message = ""

    history = _history(db, conv.conversation_id, limit=12)
    user_msg = _user_content(text, media_note)
    # Inbound Chat row is stored before this call — drop trailing duplicate user turn
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
        "temperature": 0.6,
        "max_tokens": 280,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.post(url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
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
        log.error("simple_chat Z.AI timeout")
        return _FALLBACK
    except Exception as exc:
        log.exception("simple_chat Z.AI error: %s", exc)
        return _FALLBACK
