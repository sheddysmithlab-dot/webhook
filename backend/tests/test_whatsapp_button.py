"""Tests for WhatsApp interactive button messages."""

from __future__ import annotations

from app.ai.i18n import t


def test_button_i18n_strings():
    assert t("hinglish", "btn_view_listing") == "Listing Dekhein"
    assert t("hi", "btn_view_listing") == "लिस्टिंग देखें"
    assert t("en", "btn_view_listing") == "View Listing"


def test_browse_button_i18n():
    assert t("hinglish", "btn_browse") == "Aur Listings"
    assert t("en", "btn_browse") == "Browse More"


def test_website_button_i18n():
    assert t("hinglish", "btn_visit_website") == "Website Par Jayein"
    assert t("en", "btn_visit_website") == "Visit Website"


def test_listing_live_button_i18n():
    assert "live" in t("hinglish", "listing_live_button").lower()
    assert "live" in t("en", "listing_live_button").lower()


def test_send_whatsapp_button_import():
    from app.services import send_whatsapp_button
    assert callable(send_whatsapp_button)


def test_send_listing_button_import():
    from app.ai.confirm import _send_listing_button
    assert callable(_send_listing_button)


def test_send_website_button_import():
    from app.ai.account import _send_website_button
    assert callable(_send_website_button)
