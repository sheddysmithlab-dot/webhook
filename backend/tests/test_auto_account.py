"""Every WhatsApp sender gets an account for their own number on the first message (no OTP/password)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.ai.account_filter as af
import app.infradealer.service as svc_mod
from app.ai.account import needs_account_gate
from app.ai.tools import _payload, _write_payload, execute_tool
from app.database import Base
from app.models import AiConversation, BlockedNumber, InfraDealerAccountState, User

SENDER = "9123456780"


class FakeClient:
    def __init__(self, status=200, created=True):
        self.status = status
        self.created = created
        self.calls = []

    def auto_account(self, phone, name=""):
        self.calls.append((phone, name))
        if self.status >= 300:
            return {"ok": False, "http_status": self.status, "body": {"success": False, "code": "INTERNAL_ERROR"}}
        return {
            "ok": True,
            "http_status": self.status,
            "body": {
                "success": True,
                "code": "ACCOUNT_FOUND",
                "created": self.created,
                "account": {"user_id": "901", "name": "seller.6780", "phone": phone, "account_type": "free"},
            },
        }


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


@pytest.fixture()
def fake(monkeypatch):
    client = FakeClient()

    class Svc(svc_mod.InfraDealerIntegrationService):
        def _client(self, row=None):
            return client

    monkeypatch.setattr(svc_mod, "get_integration_service", lambda db: Svc(db))
    af._auto_failed_at.clear()
    return client


def _conv(db, mobile=SENDER, **payload):
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, dict(payload))
    return conv


def test_first_message_creates_own_account_without_otp(db, fake):
    conv = _conv(db)
    verdict = af.sync_conversation_account(db, conv)
    assert fake.calls == [(SENDER, "")]
    assert verdict.found and verdict.can_post and verdict.onboarded
    pl = _payload(conv)
    assert pl["account_onboarded"] and pl["account_step"] == "done"
    assert pl["infradealer_user_id"] == "901"
    assert pl["account_created_notice"] and pl["account_auto_created"]
    assert not needs_account_gate(pl)
    user = db.query(User).filter(User.mobile == SENDER).one()
    assert user.account_ready and user.source == "whatsapp_auto"


def test_linked_account_is_not_requested_again(db, fake):
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    af.sync_conversation_account(db, conv)
    assert len(fake.calls) == 1


def test_existing_website_account_is_linked_without_notice(db, fake):
    fake.created = False
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    pl = _payload(conv)
    assert pl["account_onboarded"] and not pl.get("account_created_notice")


def test_stuck_signup_form_is_cleared(db, fake):
    conv = _conv(db, account_step="reg_password", reg_name="Ramesh", verification_status="otp_pending")
    conv.error_message = "ask:account_reg_password"
    af.sync_conversation_account(db, conv)
    pl = _payload(conv)
    assert pl["account_step"] == "done" and not pl.get("reg_name") and pl.get("verification_status") != "otp_pending"
    assert conv.error_message == ""


def test_only_the_sender_number_is_ever_sent(db, fake):
    conv = _conv(db, customer_name="Ramesh")
    af.sync_conversation_account(db, conv)
    assert [c[0] for c in fake.calls] == [SENDER]
    assert db.query(InfraDealerAccountState).one().mobile == SENDER


def test_backend_failure_is_retried_later_not_every_message(db, fake):
    fake.status = 500
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    af.sync_conversation_account(db, conv)
    assert len(fake.calls) == 1
    assert not _payload(conv).get("account_onboarded")


def test_blocked_number_gets_no_account(db, fake):
    db.add(BlockedNumber(mobile=SENDER))
    db.flush()
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    assert fake.calls == []


def test_listing_otp_tool_skipped_for_ready_account(db, fake):
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    assert execute_tool(db, conv, "send_otp", {}).get("skipped")


def test_account_notice_is_shown_once(db, fake):
    from app.ai.orchestrator import _with_account_notice

    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    first = _with_account_notice(conv, "Gaadi ki category batayein", "hinglish")
    assert first.startswith("✅ Aapka InfraDealer account") and SENDER in first
    assert _with_account_notice(conv, "Brand batayein", "hinglish") == "Brand batayein"


def _user_prompt_conv(db, monkeypatch):
    from app.ai import engine as eng
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    monkeypatch.setattr(eng, "handle_account_info", lambda *a, **k: None)
    monkeypatch.setattr(eng, "prompt_chat_enabled", lambda db: True)
    monkeypatch.setattr(eng, "llm_configured", lambda db: True)
    monkeypatch.setattr(eng, "llm_reply", lambda *a, **k: pytest.fail("LLM must not run"))
    conv = _conv(db)
    af.sync_conversation_account(db, conv)
    return eng, conv


_ACCOUNT_TALK = ("otp", "password", "account bana", "username", "email")


def test_new_user_photos_first_then_details(db, fake, monkeypatch):
    eng, conv = _user_prompt_conv(db, monkeypatch)
    out = eng.prompt_chat_turn(db, conv, "[photo]", "image id=5 caption='' saved=True")
    assert "mil gaya" in out and "category" in out.lower()
    out = eng.prompt_chat_turn(db, conv, "Excavator")
    assert "brand" in out.lower() or "company" in out.lower()
    out = eng.prompt_chat_turn(db, conv, "Tata Hitachi")
    pl = _payload(conv)
    assert (pl["intent"], pl["category"], pl["brand"]) == ("SELL", "Excavator", "Tata Hitachi")
    assert not any(w in out.lower() for w in _ACCOUNT_TALK)


def test_new_user_details_first_gets_card_with_photo_ask(db, fake, monkeypatch):
    eng, conv = _user_prompt_conv(db, monkeypatch)
    out = eng.prompt_chat_turn(db, conv, "Tipper Tata 2019 Indore 12 lakh")
    for _ in range(3):
        if _payload(conv).get("awaiting_confirm"):
            break
        out = eng.prompt_chat_turn(db, conv, "skip")
    assert _payload(conv)["awaiting_confirm"]
    assert "photo" in out.lower() and not any(w in out.lower() for w in _ACCOUNT_TALK)


def test_photo_first_starts_a_listing_for_any_user(db):
    from app.ai import engine as eng

    conv = _conv(db, mobile="9000000001")
    pl = eng.prepare_prompt_state(db, conv, "", "[photo]")
    assert pl["intent"] == "SELL"


def test_vehicle_details_first_start_a_listing(db):
    from app.ai import engine as eng

    conv = _conv(db, mobile="9000000002")
    pl = eng.prepare_prompt_state(db, conv, "JCB 3DX 2018 Indore 15 lakh")
    assert pl["intent"] == "SELL"


def test_buyer_question_is_not_turned_into_a_listing(db):
    from app.ai import engine as eng

    conv = _conv(db, mobile="9000000003")
    pl = eng.prepare_prompt_state(db, conv, "JCB 3DX chahiye")
    assert str(pl.get("intent") or "").upper() != "SELL"
