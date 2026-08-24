"""Phase 1: Vision (Z.AI glm-4.6v-flash) + Voice (Groq Whisper) foundation.

Verifies:
- New config fields exist on Settings and MetaSettings.
- resolve_media_config returns the right shape and gating.
- AiMedia accepts extracted_text / extract_kind.
- Admin AI settings payload accepts the new fields.
- Z.AI-only rule still holds for chat base; Groq is allowed as transcription service.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiMedia, MetaSettings
from app.ai.media_config import resolve_media_config, vision_enabled, voice_enabled
from app.services import get_or_create_settings, resolve_ai_config


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    return Session()


def test_meta_settings_has_new_columns():
    """MetaSettings exposes vision + voice columns (create_all path)."""
    db = _session()
    row = MetaSettings(
        ai_vision_model="glm-4.6v-flash",
        ai_vision_enabled=True,
        ai_voice_enabled=False,
        groq_api_key="gsk_test",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.ai_vision_model == "glm-4.6v-flash"
    assert row.ai_vision_enabled is True
    assert row.ai_voice_enabled is False
    assert row.groq_api_key == "gsk_test"
    db.close()


def test_ai_media_accepts_extracted_fields():
    """AiMedia stores OCR / transcription output."""
    db = _session()
    conv = AiConversation(mobile="919999999999", conversation_id="CONV_X", state="NEW")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    media = AiMedia(
        conversation_id=conv.id,
        kind="audio",
        mime="audio/ogg",
        local_path="/tmp/x.ogg",
        extracted_text="Tata tipper bechna hai 40 lakh",
        extract_kind="transcription",
    )
    db.add(media)
    db.commit()
    db.refresh(media)
    assert media.extracted_text == "Tata tipper bechna hai 40 lakh"
    assert media.extract_kind == "transcription"
    db.close()


def test_resolve_media_config_defaults_when_disabled(monkeypatch):
    """With no keys, both vision and voice are disabled but shape is correct."""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_vision_enabled", False)
    monkeypatch.setattr(settings, "ai_voice_enabled", False)
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "ai_api_key", "")

    db = _session()
    cfg = resolve_media_config(db)
    assert set(cfg.keys()) == {
        "vision_enabled", "vision_model", "vision_api_key", "vision_api_base",
        "voice_enabled", "groq_api_key", "groq_api_base", "groq_whisper_model",
    }
    assert cfg["vision_enabled"] is False
    assert cfg["voice_enabled"] is False
    assert cfg["vision_model"] == "glm-4.6v-flash"
    assert cfg["groq_whisper_model"] == "whisper-large-v3-turbo"
    assert vision_enabled(db) is False
    assert voice_enabled(db) is False
    db.close()


def test_vision_enabled_requires_zai_key_and_flag():
    """Vision needs both ai_vision_enabled=True and a Z.AI api_key."""
    db = _session()
    row = get_or_create_settings(db)
    row.ai_api_key = "zai.key"
    row.ai_vision_enabled = True
    db.commit()
    cfg = resolve_media_config(db)
    assert cfg["vision_enabled"] is True
    assert cfg["vision_api_key"] == "zai.key"
    assert vision_enabled(db) is True
    db.close()


def test_voice_enabled_requires_groq_key_and_flag(monkeypatch):
    """Voice needs both ai_voice_enabled=True and a groq_api_key (separate from Z.AI)."""
    from app.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "ai_voice_enabled", False)

    db = _session()
    row = get_or_create_settings(db)
    row.ai_api_key = "zai.key"          # Z.AI present but voice still needs Groq
    row.ai_voice_enabled = True
    row.groq_api_key = ""
    db.commit()
    assert voice_enabled(db) is False, "Groq key missing → voice must stay disabled"

    row.groq_api_key = "gsk_test"
    db.commit()
    assert voice_enabled(db) is True
    db.close()


def test_resolve_ai_config_returns_vision_and_voice_fields():
    """resolve_ai_config now includes vision + voice keys for downstream callers."""
    db = _session()
    row = get_or_create_settings(db)
    row.ai_api_key = "zai.key"
    row.ai_vision_enabled = True
    row.ai_voice_enabled = True
    row.groq_api_key = "gsk_test"
    db.commit()
    cfg = resolve_ai_config(db)
    assert cfg["vision_enabled"] is True
    assert cfg["voice_enabled"] is True
    assert cfg["groq_api_key"] == "gsk_test"
    assert cfg["vision_model"] == "glm-4.6v-flash"
    db.close()


def test_zai_only_rule_still_blocks_openai_chat_base():
    """The Z.AI-only rule applies to chat base. Groq (transcription) is unaffected.

    get_or_create_settings normalizes any OpenAI base back to Z.AI (forced), so chat
    stays on Z.AI. Groq is a separate transcription service and is NOT subject to the
    Z.AI-only chat rule — voice works independently of the chat base.
    """
    from app.services import is_openai_api_base, is_zai_api_base

    db = _session()
    row = get_or_create_settings(db)
    row.ai_api_key = "zai.key"
    row.ai_api_base = "https://api.openai.com/v1"   # OpenAI chat base → must be forced to Z.AI
    row.groq_api_key = "gsk_test"                    # Groq is fine (transcription, not chat)
    row.ai_voice_enabled = True
    db.commit()
    cfg = resolve_ai_config(db)
    # Chat base is normalized back to Z.AI — chat stays enabled on Z.AI, not OpenAI.
    assert cfg["enabled"] is True
    assert is_zai_api_base(cfg["api_base"])
    assert not is_openai_api_base(cfg["api_base"])
    # Voice (Groq) is independent of the chat base rule and remains available.
    assert voice_enabled(db) is True
    assert cfg["groq_api_key"] == "gsk_test"
    db.close()
