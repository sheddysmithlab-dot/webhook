"""Phase 2: Voice note transcription via Groq Whisper.

When a WhatsApp audio/voice note arrives, `runner.attach_media` downloads it to
disk and stores an `AiMedia` row. This module transcribes that file via Groq's
OpenAI-compatible `/audio/transcriptions` endpoint and returns the text, so the
rest of the agent (corrector → classify → orchestrator) can process it like any
other text message instead of seeing the dead "[voice note]" placeholder.

Hard rules:
- Groq is a transcription SERVICE, not a chat provider — using it does NOT
  violate the project's Z.AI-only chat rule.
- Gated on `ai_voice_enabled` flag + `groq_api_key` (see media_config.voice_enabled).
- 25 MB file cap matches Groq's free-tier upload limit; larger files are skipped
  with a graceful note (bot continues with the "[voice note]" placeholder).
- Any failure (network, non-200, empty body) returns "" — the caller falls back
  to the existing placeholder text. Never raises.
- Never invents content. The transcript is exactly what Whisper returned.
"""

from __future__ import annotations

import logging
import os

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation, AiMedia
from .media_config import resolve_media_config

log = logging.getLogger("infradealer.ai.voice")

# Groq free-tier upload cap. Larger files are skipped (not retried) to avoid
# burning the rate limit on hopeless requests.
MAX_FILE_BYTES = 25 * 1024 * 1024

# Map InfraDealer language codes → Whisper ISO 639-1 hints.
# Romanized scripts (hinglish, pa_roman, …) have no Whisper code; we hint the
# base language so Whisper's multilingual model can pick the right phonemes.
_LANG_HINT = {
    "hi": "hi", "hinglish": "hi", "mr": "hi", "mr_roman": "hi",
    "en": "en",
    "pa": "pa", "pa_roman": "pa",
    "gu": "gu", "gu_roman": "gu",
    "ta": "ta", "ta_roman": "ta",
    "te": "te", "te_roman": "te",
    "kn": "kn", "kn_roman": "kn",
    "ml": "ml", "ml_roman": "ml",
    "bn": "bn", "bn_roman": "bn",
    "ur": "ur", "ur_roman": "ur",
}


def _language_hint(conv_lang: str) -> str:
    return _LANG_HINT.get((conv_lang or "").strip(), "")


def transcribe_audio(db: Session, conv: AiConversation, media_row: AiMedia) -> str:
    """Transcribe a downloaded voice note via Groq Whisper.

    Returns the transcript text, or "" on any failure / skip. Never raises.
    Stores nothing here — the caller persists `extracted_text` / `extract_kind`
    on the AiMedia row so this function stays side-effect free and testable.
    """
    cfg = resolve_media_config(db)
    if not cfg.get("voice_enabled"):
        return ""

    path = (media_row.local_path or "").strip()
    if not path or not os.path.exists(path):
        log.warning("voice.skip: file missing path=%s", path)
        return ""

    try:
        size = os.path.getsize(path)
    except OSError as exc:
        log.warning("voice.skip: stat failed %s: %s", path, exc)
        return ""
    if size <= 0:
        log.warning("voice.skip: empty file %s", path)
        return ""
    if size > MAX_FILE_BYTES:
        log.warning("voice.skip: file too large %d bytes (max %d)", size, MAX_FILE_BYTES)
        return ""

    groq_key = cfg.get("groq_api_key") or ""
    groq_base = (cfg.get("groq_api_base") or "https://api.groq.com/openai/v1").rstrip("/")
    model = cfg.get("groq_whisper_model") or "whisper-large-v3-turbo"
    if not groq_key:
        return ""

    url = groq_base + "/audio/transcriptions"
    headers = {"Authorization": f"Bearer {groq_key}"}
    lang_hint = _language_hint(getattr(conv, "language", "") or "")

    try:
        with open(path, "rb") as fh:
            files = {"file": (os.path.basename(path) or "voice.ogg", fh, "application/octet-stream")}
            data = {
                "model": model,
                "response_format": "json",
                "temperature": "0.0",
            }
            if lang_hint:
                data["language"] = lang_hint
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, headers=headers, files=files, data=data)
    except Exception as exc:
        log.warning("voice.http error: %s", exc)
        return ""

    if resp.status_code >= 400:
        log.warning("voice.http %s %s", resp.status_code, (resp.text or "")[:200])
        return ""

    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    text = (data.get("text") or "").strip()
    if not text:
        log.warning("voice.empty: no text in response keys=%s", list(data.keys()))
        return ""
    return text
