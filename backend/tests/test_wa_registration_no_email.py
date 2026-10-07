"""WhatsApp signup no longer asks for email; login is username or mobile only."""

from contextlib import nullcontext
from types import SimpleNamespace

import app.ai.account as account
from app.ai.i18n import t
from app.ai.schema import dumps, loads


def _conv(**payload):
    return SimpleNamespace(
        mobile="919876543210",
        payload_json=dumps(payload),
        intent="",
        customer_name="Rahul Sharma",
        profile_id=None,
        profile_status=None,
        error_message=None,
    )


def test_username_step_goes_straight_to_password():
    conv = _conv(account_step="reg_username", reg_name="Rahul Sharma", reg_username_suggest="rahulsharma")
    reply = account.handle_account(None, conv, "haan", "hinglish")
    assert reply == t("hinglish", "account_reg_ask_password")
    data = loads(conv.payload_json)
    assert data["account_step"] == "reg_password"
    assert data["reg_username"] == "rahulsharma"
    assert "email" not in reply.lower()


def test_chat_parked_on_old_email_step_moves_to_password():
    conv = _conv(account_step="reg_email", reg_name="Rahul Sharma", reg_username="rahulsharma")
    reply = account.handle_account(None, conv, "anything", "en")
    assert reply == t("en", "account_reg_ask_password")
    assert loads(conv.payload_json)["account_step"] == "reg_password"


class _FakeSvc:
    def __init__(self, business_status=""):
        self.calls = []
        self.business_status = business_status

    def create_account(self, conv, name, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(business_status=self.business_status, last_error="")

    def process_outbox_item(self, item):
        return None


def _patch_backend(monkeypatch, svc):
    monkeypatch.setattr(account, "_infra", lambda db: svc)
    monkeypatch.setattr(account, "_state", lambda db, mobile: None)
    monkeypatch.setattr(account, "_local_otp_then", lambda *a, **k: "OTP_SENT")
    return SimpleNamespace(begin_nested=nullcontext, flush=lambda: None)


def test_registration_is_submitted_without_email(monkeypatch):
    svc = _FakeSvc()
    db = _patch_backend(monkeypatch, svc)
    conv = _conv(account_step="reg_password", reg_name="Rahul Sharma", reg_username="rahulsharma", reg_email="old@x.com")
    assert account.handle_account(db, conv, "secret1", "hinglish") == "OTP_SENT"
    assert svc.calls == [{"username": "rahulsharma", "password": "secret1"}]


def test_taken_username_asks_for_another(monkeypatch):
    svc = _FakeSvc(business_status="USERNAME_EXISTS")
    db = _patch_backend(monkeypatch, svc)
    conv = _conv(account_step="reg_password", reg_name="Rahul Sharma", reg_username="rahulsharma")
    reply = account.handle_account(db, conv, "secret1", "en")
    assert reply == t("en", "account_reg_username_taken")
    assert loads(conv.payload_json)["account_step"] == "reg_username"


def test_login_wording_mentions_username_or_mobile_not_email():
    for lang in ("hinglish", "hi", "en"):
        for key in ("account_reg_ask_password", "account_reg_done"):
            text = t(lang, key)
            assert "email" not in text.lower()
            assert "ईमेल" not in text
        done = t("en", "account_reg_done")
        assert "*username* or *mobile number*" in done
