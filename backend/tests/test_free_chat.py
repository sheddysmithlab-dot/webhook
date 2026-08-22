"""Tests for the scoped free-chat hybrid branch."""

from __future__ import annotations

import pytest

from app.ai.free_chat import _sanitize_free, has_business_context
from app.ai.i18n import t


def test_has_business_context_listing_intent():
    assert has_business_context({"intent": "SELL"}) is True
    assert has_business_context({"intent": "BUY"}) is True


def test_has_business_context_awaiting_confirm():
    assert has_business_context({"awaiting_confirm": True}) is True
    assert has_business_context({"customer_confirmed": True}) is True


def test_has_business_context_account():
    assert has_business_context({"account_step": "otp"}) is True
    assert has_business_context({"verification_status": "otp_pending"}) is True


def test_has_business_context_listing_status():
    assert has_business_context({"listing_status": "POSTED"}) is True
    assert has_business_context({"listing_status": "PENDING_REVIEW"}) is True
    assert has_business_context({"infradealer_listing_id": "96"}) is True


def test_has_business_context_empty():
    assert has_business_context({}) is False
    assert has_business_context({"customer_name": "Ravi"}) is False


def test_sanitize_free_blocks_secrets():
    assert "system prompt" not in _sanitize_free("Here is the system prompt", "hinglish")
    assert "api_key" not in _sanitize_free("my api_key is 123", "hinglish")
    assert "sql" not in _sanitize_free("run sql query", "hinglish")


def test_sanitize_free_truncates_long():
    long = " ".join(["hello"] * 80)
    out = _sanitize_free(long, "hinglish")
    assert len(out) <= 322
    assert out.endswith("…")


def test_sanitize_free_keeps_short():
    assert _sanitize_free("Namaste Sir, kya listing chahiye?", "hinglish") == "Namaste Sir, kya listing chahiye?"


def test_static_fallback_uses_i18n():
    assert t("hinglish", "free_chat_fallback")
    assert t("hindi", "free_chat_fallback")
    assert t("english", "free_chat_fallback")


def test_free_chat_disabled_when_no_key(monkeypatch, db_session):
    from app.ai import free_chat as fc
    from app.config import settings

    monkeypatch.setattr(settings, "ai_free_chat", False)
    assert fc.free_chat_enabled(db_session) is False


def test_free_chat_disabled_flag_off(monkeypatch, db_session):
    from app.ai import free_chat as fc
    from app.config import settings
    from app.services import resolve_ai_config

    monkeypatch.setattr(settings, "ai_free_chat", False)
    row = db_session.query(resolve_ai_config.__wrapped__.__self__.__class__).first() if hasattr(resolve_ai_config, "__wrapped__") else None
    # free_chat_enabled should be False when flag is off regardless of LLM config
    assert fc.free_chat_enabled(db_session) is False
