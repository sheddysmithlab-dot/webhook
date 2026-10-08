"""WhatsApp signup OTP step: wrong OTP, resend, dispute, expiry and server errors."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.ai.account as acc
from app.ai.account import disputes_otp, handle_account, should_intercept_account, wants_otp_resend
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.models import AiConversation

MOBILE = "7599523757"


class Item:
    def __init__(self, status, code):
        self.status = status
        self.business_status = code
        self.last_error = code if status != "DONE" else None


class State:
    registration_id = "REG_1"
    account_status = "OTP_PENDING"


class FakeSvc:
    def __init__(self, verify_code="OTP_INVALID", verify_status="FAILED", request_status="DONE", request_code="OTP_SENT"):
        self.verify_code = verify_code
        self.verify_status = verify_status
        self.request_status = request_status
        self.request_code = request_code
        self.verified = []
        self.requested = 0

    def verify_otp_external(self, conv, otp):
        self.verified.append(otp)
        return Item(self.verify_status, self.verify_code)

    def request_otp(self, conv):
        self.requested += 1
        return Item(self.request_status, self.request_code)

    def process_outbox_item(self, item):
        pass


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _setup(db, monkeypatch, svc, sent_ago=120, **extra):
    conv = AiConversation(mobile=MOBILE, conversation_id=f"CONV_{MOBILE}", state="OTP_PENDING", payload_json="{}")
    db.add(conv)
    db.flush()
    sent = (datetime.now(timezone.utc) - timedelta(seconds=sent_ago)).isoformat()
    _write_payload(conv, {"account_step": "otp", "otp_sent_at": sent, "otp_wrong": 0, **extra})
    state = State()
    monkeypatch.setattr(acc, "_infra", lambda db: svc)
    monkeypatch.setattr(acc, "_state", lambda db, mobile: state)
    return conv


def test_intercepts_otp_followups():
    p = {"account_step": "otp"}
    assert should_intercept_account(p, "892574") is True
    assert should_intercept_account(p, "Shi h") is True
    assert should_intercept_account(p, "RESEND") is True
    assert should_intercept_account(p, "otp nahi aaya") is True
    assert should_intercept_account(p, "89257") is True
    assert should_intercept_account(p, "price badlo") is False


def test_resend_and_dispute_phrases():
    assert wants_otp_resend("RESEND") is True
    assert wants_otp_resend("naya otp") is True
    assert wants_otp_resend("135395") is False
    assert disputes_otp("Shi h") is True
    assert disputes_otp("sahi hai") is True
    assert disputes_otp("otp to sahi tha") is True
    assert disputes_otp("892574 sahi hai") is False
    assert disputes_otp("JCB 3DX sahi condition me hai 18 lakh") is False


def test_wrong_otp_shows_digits_and_tries_left(db, monkeypatch):
    svc = FakeSvc()
    conv = _setup(db, monkeypatch, svc)
    reply = handle_account(db, conv, "892574", "hinglish")
    assert "892574" in reply and "4 try baaki" in reply and "RESEND" in reply
    assert _payload(conv)["otp_wrong"] == 1
    assert svc.requested == 0


def test_fifth_wrong_otp_sends_a_new_one(db, monkeypatch):
    svc = FakeSvc()
    conv = _setup(db, monkeypatch, svc, sent_ago=10, otp_wrong=4)
    reply = handle_account(db, conv, "111111", "hinglish")
    assert svc.requested == 1
    assert "limit poori" in reply and "Naya OTP bhej diya" in reply
    assert _payload(conv)["otp_wrong"] == 0


def test_dispute_resends_for_real(db, monkeypatch):
    svc = FakeSvc()
    conv = _setup(db, monkeypatch, svc)
    reply = handle_account(db, conv, "Shi h", "hinglish")
    assert svc.requested == 1
    assert "match nahi hua" in reply and "Naya OTP bhej diya" in reply


def test_resend_respects_cooldown(db, monkeypatch):
    svc = FakeSvc()
    conv = _setup(db, monkeypatch, svc, sent_ago=20)
    reply = handle_account(db, conv, "RESEND", "hinglish")
    assert svc.requested == 0
    assert "Thoda rukiye" in reply and "bhej diya" not in reply


def test_resend_failure_never_claims_sent(db, monkeypatch):
    svc = FakeSvc(request_status="RETRY", request_code="OTP_REQUEST_FAILED")
    conv = _setup(db, monkeypatch, svc)
    reply = handle_account(db, conv, "otp nahi aaya", "hinglish")
    assert svc.requested == 1
    assert "bhej diya" not in reply and "5 se zyada" in reply


def test_expired_otp_resends(db, monkeypatch):
    svc = FakeSvc(verify_code="OTP_EXPIRED")
    conv = _setup(db, monkeypatch, svc, sent_ago=700)
    reply = handle_account(db, conv, "892574", "hinglish")
    assert svc.requested == 1
    assert "expire" in reply and "Naya OTP bhej diya" in reply


def test_server_error_is_not_called_wrong_otp(db, monkeypatch):
    svc = FakeSvc(verify_code="NETWORK_ERROR")
    conv = _setup(db, monkeypatch, svc)
    reply = handle_account(db, conv, "892574", "hinglish")
    assert "galat" not in reply.lower() and "Server" in reply
    assert _payload(conv).get("otp_wrong") == 0


def test_short_code_asks_for_six_digits(db, monkeypatch):
    svc = FakeSvc()
    conv = _setup(db, monkeypatch, svc)
    reply = handle_account(db, conv, "89257", "hinglish")
    assert "6 digit" in reply and svc.verified == []
