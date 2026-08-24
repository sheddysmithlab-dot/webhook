"""OTP delivery must use DLT SMS — never WhatsApp."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_deliver_otp_never_whatsapp(monkeypatch):
    from app.config import settings
    from app import services

    monkeypatch.setattr(settings, "sms_provider", "log")
    channel = services.deliver_otp(None, "919876543210", "123456")
    assert channel == "log"
    assert channel != "whatsapp"


def test_send_dlt_sms_normalizes_mobile(monkeypatch):
    from app.config import settings
    from app.sms_dlt import send_dlt_sms, _digits_msisdn

    assert _digits_msisdn("9876543210") == "919876543210"
    assert _digits_msisdn("+91 98765 43210") == "919876543210"
    monkeypatch.setattr(settings, "sms_provider", "log")
    assert send_dlt_sms("9876543210", "654321") == "log"


def test_account_otp_copy_mentions_sms():
    from app.ai.i18n import t

    msg = t("hinglish", "account_otp").lower()
    assert "sms" in msg
    assert "whatsapp pe nahi" in msg or "not on whatsapp" in t("en", "account_otp").lower()
