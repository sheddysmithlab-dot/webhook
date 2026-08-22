"""Tests for the AI Corrector module."""

from __future__ import annotations

import pytest

from app.ai.corrector import (
    _fast_correct,
    _meaning_changed,
    _should_skip,
    correct_user_message,
)


def test_should_skip_short():
    assert _should_skip("haan") is True
    assert _should_skip("ok") is True
    assert _should_skip("yes") is True
    assert _should_skip("nahi") is True
    assert _should_skip("123456") is True
    assert _should_skip("") is True
    assert _should_skip("hi") is True


def test_should_skip_long():
    assert _should_skip("gdi bechna h") is False
    assert _should_skip("Tata Signa dumper 2018 42 lakh") is False


def test_fast_correct_brand():
    assert "JCB" in _fast_correct("jcB 3dx bechna h")
    assert "Tata" in _fast_correct("tata 1618 bechna h")


def test_fast_correct_category():
    assert "Tipper" in _fast_correct("tipr bechna h")
    assert "Poclain" in _fast_correct("poclen bechna h")


def test_fast_correct_word_completion():
    assert "gaadi" in _fast_correct("gdi bechna h")
    assert "hain" in _fast_correct("bechna h")
    assert "bechna" in _fast_correct("bechn hain")
    assert "lakh" in _fast_correct("40 lac bechna hain")


def test_fast_correct_preserves_price():
    out = _fast_correct("40 lakh bechna hain")
    assert "40 lakh" in out


def test_fast_correct_no_change():
    assert _fast_correct("Tata 1618 40 lakh bechna hain") == "Tata 1618 40 lakh bechna hain"


def test_meaning_changed_intent():
    assert _meaning_changed("gaadi bechna hain", "gaadi kharidna hain") is True
    assert _meaning_changed("gaadi kharidna hain", "gaadi bechna hain") is True


def test_meaning_changed_same():
    assert _meaning_changed("gdi bechna h", "gaadi bechna hain") is False
    assert _meaning_changed("Tata 1618 40 lakh", "Tata 1618 40 lakh") is False


def test_meaning_changed_numbers():
    assert _meaning_changed("40 lakh bechna", "45 lakh bechna") is True
    assert _meaning_changed("2018 model", "2020 model") is True


def test_correct_user_message_disabled(monkeypatch, db_session):
    from app.config import settings
    from app.models import AiConversation

    monkeypatch.setattr(settings, "ai_corrector", False)
    conv = AiConversation(conversation_id="CONV_TEST", mobile="9999999999", language="hinglish")
    db_session.add(conv)
    db_session.flush()
    out = correct_user_message(db_session, conv, "gdi bechna h")
    assert out == "gdi bechna h"


def test_correct_user_message_short(db_session):
    from app.models import AiConversation

    conv = AiConversation(conversation_id="CONV_TEST2", mobile="9999999998", language="hinglish")
    db_session.add(conv)
    db_session.flush()
    out = correct_user_message(db_session, conv, "haan")
    assert out == "haan"


def test_correct_user_message_fast_fix(db_session):
    from app.models import AiConversation

    conv = AiConversation(conversation_id="CONV_TEST3", mobile="9999999997", language="hinglish")
    db_session.add(conv)
    db_session.flush()
    out = correct_user_message(db_session, conv, "gdi bechna h")
    assert "gaadi" in out.lower()
    assert "hain" in out.lower()
