"""Tests for AI understanding + routing layer."""

from __future__ import annotations

from app.ai.corrector import classify_message, reset_free_chat_count, _FREE_CHAT_LIMIT


def test_classify_sell_confirmed():
    result = classify_message("bechna", {})
    assert result["route"] == "confirmed"
    assert result["intent"] == "SELL"


def test_classify_buy_confirmed():
    result = classify_message("kharidna", {})
    assert result["route"] == "confirmed"
    assert result["intent"] == "BUY"


def test_classify_devanagari_sell():
    result = classify_message("\u092c\u0947\u091a\u0928\u093e", {})
    assert result["route"] == "confirmed"
    assert result["intent"] == "SELL"


def test_classify_greeting_unconfirmed():
    result = classify_message("kaise ho", {})
    assert result["route"] == "unconfirmed"


def test_classify_other_with_listing_confirmed():
    result = classify_message("Tata 1618", {"intent": "SELL"})
    assert result["route"] == "confirmed"


def test_classify_other_without_listing_unconfirmed():
    result = classify_message("random text", {})
    assert result["route"] == "unconfirmed"


def test_classify_free_chat_count():
    result = classify_message("kaise ho", {"free_chat_count": 0})
    assert result["route"] == "unconfirmed"
    assert result["free_count"] == 0


def test_classify_options_on_3rd():
    result = classify_message("kaise ho", {"free_chat_count": _FREE_CHAT_LIMIT})
    assert result["route"] == "options"


def test_classify_photo_confirmed():
    result = classify_message("[photo]", {}, media_note="image")
    assert result["route"] == "confirmed"
    assert result["intent"] == "UPLOAD_PHOTO"


def test_reset_free_chat_count():
    payload = {"free_chat_count": 2, "intent": "SELL"}
    out = reset_free_chat_count(payload)
    assert out["free_chat_count"] == 0


def test_infradealer_options_i18n():
    from app.ai.i18n import t
    msg = t("hinglish", "infradealer_options")
    assert "bechna" in msg.lower() or "sell" in msg.lower()
    assert "kharidna" in msg.lower() or "buy" in msg.lower()
