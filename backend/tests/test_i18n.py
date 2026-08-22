"""Tests for the multilingual i18n dictionary."""

from __future__ import annotations

from app.ai.i18n import (
    DEFAULT_LANG,
    SUPPORTED_LANGS,
    all_keys,
    keys_for_lang,
    language_instruction,
    normalize_policy,
    normalize_reply,
    pick_language,
    t,
    validate_dictionary,
)


def test_all_keys_defined():
    keys = all_keys()
    assert len(keys) == 86
    assert "greet" in keys
    assert "account_otp" in keys
    assert "confirm_ready" in keys
    assert "free_chat_fallback" in keys


def test_core_languages_complete():
    for lang in ("hinglish", "hi", "en"):
        assert len(keys_for_lang(lang)) == 86, f"{lang} missing keys"


def test_t_hinglish():
    assert "Namaste" in t("hinglish", "greet")
    assert "Brand" in t("hinglish", "brand")


def test_t_english():
    assert "Hello" in t("en", "greet")
    assert "brand" in t("en", "brand")


def test_t_with_kwargs():
    out = t("hinglish", "approved", link="https://x.in/1")
    assert "https://x.in/1" in out


def test_t_fallback_chain():
    # ta falls back to ta_roman then en
    out = t("ta", "greet")
    assert len(out) > 10
    # kn falls back to kn_roman then en
    out = t("kn", "greet")
    assert len(out) > 10


def test_t_missing_key_returns_marker():
    out = t("hinglish", "nonexistent_key_xyz")
    assert out == "[nonexistent_key_xyz]"


def test_t_unknown_lang_falls_back():
    out = t("klingon", "greet")
    assert len(out) > 10


def test_t_never_returns_raw_key():
    for key in all_keys():
        for lang in SUPPORTED_LANGS:
            out = t(lang, key)
            assert out != key, f"Raw key returned for {lang}/{key}"


def test_pick_language_devanagari():
    assert pick_language("नमस्ते") == "hi"


def test_pick_language_gurmukhi():
    assert pick_language("ਸਤ ਸ੍ਰੀ") == "pa"


def test_pick_language_tamil():
    assert pick_language("வணக்கம்") == "ta"


def test_pick_language_telugu():
    assert pick_language("నమస్కారం") == "te"


def test_pick_language_english():
    assert pick_language("hello what is the price") == "en"


def test_pick_language_default():
    assert pick_language("") == DEFAULT_LANG


def test_pick_language_policy_override():
    assert pick_language("namaste", policy="hindi") == "hi"
    assert pick_language("namaste", policy="english") == "en"


def test_pick_language_roman_punjabi():
    assert pick_language("sat sri akal") == "pa_roman"


def test_pick_language_roman_tamil():
    assert pick_language("vanakkam saaptu") == "ta_roman"


def test_pick_language_previous_sticky():
    prev = pick_language("namaste", previous="hi")
    assert prev == "hi"
    # Devanagari text switches from hinglish to hi
    assert pick_language("नमस्ते", previous="hinglish") == "hi"


def test_normalize_policy():
    assert normalize_policy("auto") == "auto"
    assert normalize_policy("en") == "en"
    assert normalize_policy("hi") == "hi"
    assert normalize_policy("hinglish") == "hinglish"
    assert normalize_policy("english") == "en"
    assert normalize_policy("hindi") == "hi"
    assert normalize_policy("xyz") == "auto"


def test_normalize_reply():
    assert normalize_reply("hinglish") == "hinglish"
    assert normalize_reply("xyz") == DEFAULT_LANG


def test_language_instruction():
    assert "Devanagari" in language_instruction("hi")
    assert "English" in language_instruction("en")
    assert "Hinglish" in language_instruction("hinglish")
    assert "Punjabi" in language_instruction("pa")
    assert "Tamil" in language_instruction("ta")
    assert "Hinglish" in language_instruction("unknown")


def test_validate_dictionary_returns_list():
    issues = validate_dictionary()
    assert isinstance(issues, list)
    # Core languages should have zero issues
    core_issues = [i for i in issues if "hinglish" in i or " hi " in i or " en " in i]
    assert len(core_issues) == 0


def test_supported_langs_count():
    assert len(SUPPORTED_LANGS) == 21
    assert "hinglish" in SUPPORTED_LANGS
    assert "hi" in SUPPORTED_LANGS
    assert "en" in SUPPORTED_LANGS
    assert "pa" in SUPPORTED_LANGS
    assert "ta" in SUPPORTED_LANGS
    assert "ur" in SUPPORTED_LANGS
