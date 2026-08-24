"""Phase 1: Media understanding config resolver (vision + voice).

Vision  → Z.AI glm-4.6v-flash (same Z.AI account/key as text chat, only model slug differs).
Voice   → Groq Whisper (separate transcription service; does NOT violate the Z.AI-only
          chat rule, because transcription is not a chat completion).

This module is a thin accessor over `resolve_ai_config` so callers (voice.py, vision.py,
runner.py) get a single, typed view of the media-understanding config without re-reading
the DB row each time.

Hard rules (enforced by callers, not here):
- Vision is gated on the Z.AI api_key (same account). No separate vision key.
- Voice is gated on the Groq api_key. If Groq key is missing, voice stays disabled
  and the bot falls back to the "[voice note]" placeholder (existing behavior).
- Both features are feature-flagged (`ai_vision_enabled`, `ai_voice_enabled`) so they
  can be rolled back by toggling a single env var or DB column.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..services import resolve_ai_config

log = logging.getLogger("infradealer.ai.media_config")


def resolve_media_config(db: Session) -> dict:
    """Return a single dict describing vision + voice availability for this turn.

    Keys:
        vision_enabled: bool   — Z.AI vision model may be called this turn
        vision_model:   str     — model slug (default glm-4.6v-flash, free tier)
        vision_api_key: str     — Z.AI key (same as chat)
        vision_api_base: str    — Z.AI base (same as chat)
        voice_enabled:  bool    — Groq Whisper may be called this turn
        groq_api_key:   str     — Groq key (separate from Z.AI)
        groq_api_base:  str     — Groq base (OpenAI-compatible)
        groq_whisper_model: str — Whisper model slug (default whisper-large-v3-turbo)
    """
    cfg = resolve_ai_config(db)
    return {
        "vision_enabled": bool(cfg.get("vision_enabled")),
        "vision_model": cfg.get("vision_model") or "glm-4.6v-flash",
        "vision_api_key": cfg.get("api_key") or "",
        "vision_api_base": cfg.get("api_base") or "",
        "voice_enabled": bool(cfg.get("voice_enabled")),
        "groq_api_key": cfg.get("groq_api_key") or "",
        "groq_api_base": cfg.get("groq_api_base") or "https://api.groq.com/openai/v1",
        "groq_whisper_model": cfg.get("groq_whisper_model") or "whisper-large-v3-turbo",
    }


def vision_enabled(db: Session) -> bool:
    """True if Z.AI vision OCR may run this turn (flag + Z.AI key both present)."""
    return bool(resolve_media_config(db).get("vision_enabled"))


def voice_enabled(db: Session) -> bool:
    """True if Groq Whisper transcription may run this turn (flag + Groq key both present)."""
    return bool(resolve_media_config(db).get("voice_enabled"))
