"""Ad / new-lead welcome menu + 1/2/3 choice mapping; idle reset notice never shown."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.lead_welcome import (
    MENU_CHOICES,
    PARTNER_TEXT,
    WELCOME_ECHO_REPLY,
    WELCOME_TEXT,
    is_ad_prefill,
    is_welcome_echo,
    is_lead_inquiry,
    lead_welcome_reply,
    resolve_menu_choice,
)
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.models import AiConversation, Chat

UNREGISTERED = {"account_reason": "ACCOUNT_NOT_FOUND", "account_type": "MISSING"}
REGISTERED = {"account_onboarded": True, "account_step": "done", "account_type": "BROKER", "profile_id": "P1"}


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _conv(db, payload: dict, mobile: str = "9000111333") -> AiConversation:
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, dict(payload))
    return conv


def test_detection():
    assert is_ad_prefill("Hello! Can I get more info on this?")
    assert is_ad_prefill("I'm interested in this")
    assert not is_ad_prefill("Tata 1618 bechna hai")
    assert is_lead_inquiry("Hello! Can I get more info on this?")
    assert is_lead_inquiry("hi")
    assert is_lead_inquiry("Namaste ji 🙏")
    assert is_lead_inquiry("details batao")
    assert not is_lead_inquiry("JCB kharidna hai")
    assert not is_lead_inquiry("Tata 1618 bechna hai 22 lakh")
    assert not is_lead_inquiry("account banao")
    assert not is_lead_inquiry("mera wallet token kitna hai")


def test_unregistered_greeting_gets_welcome_once():
    db = _session()
    conv = _conv(db, UNREGISTERED)
    assert lead_welcome_reply(db, conv, _payload(conv), "Hello! Can I get more info on this?") == WELCOME_TEXT
    assert "account" not in WELCOME_TEXT.lower()
    pl = _payload(conv)
    assert pl["lead_welcome_sent"] is True and pl["lead_menu_pending"] is True
    assert lead_welcome_reply(db, conv, _payload(conv), "hi") == ""


def test_ad_prefill_welcomes_registered_user_too():
    db = _session()
    conv = _conv(db, REGISTERED)
    assert lead_welcome_reply(db, conv, _payload(conv), "Hello! Can I get more info on this?") == WELCOME_TEXT
    assert _payload(conv).get("lead_source") == "ad"


def test_registered_plain_greeting_goes_to_normal_flow():
    db = _session()
    conv = _conv(db, REGISTERED)
    assert lead_welcome_reply(db, conv, _payload(conv), "hi") == ""


def test_ad_detected_from_original_text_when_corrector_rewrote_it():
    db = _session()
    conv = _conv(db, REGISTERED)
    conv.last_wamid = "wamid.AD1"
    db.add(Chat(
        wamid="wamid.AD1",
        conversation_id=conv.conversation_id,
        from_mobile=conv.mobile,
        direction="inbound",
        body="Hello! Can I get more info on this?",
    ))
    db.flush()
    rewritten = "Namaste, kya is par aur jaankari mil sakti hai"
    assert lead_welcome_reply(db, conv, _payload(conv), rewritten) == WELCOME_TEXT


def test_no_welcome_mid_listing_media_or_account_form():
    db = _session()
    conv = _conv(db, {**UNREGISTERED, "intent": "SELL", "brand": "Tata"})
    assert lead_welcome_reply(db, conv, _payload(conv), "hi") == ""

    conv2 = _conv(db, UNREGISTERED, mobile="9000111444")
    assert lead_welcome_reply(db, conv2, _payload(conv2), "hi", media_note="photo") == ""

    conv3 = _conv(db, {**UNREGISTERED, "account_step": "otp"}, mobile="9000111555")
    assert lead_welcome_reply(db, conv3, _payload(conv3), "Hello! Can I get more info on this?") == ""


def test_ad_prefill_that_is_the_welcome_text_gets_short_menu_not_account_offer():
    db = _session()
    conv = _conv(db, {**REGISTERED, "lead_welcome_sent": True, "intent": "SELL"})
    reply = lead_welcome_reply(db, conv, _payload(conv), WELCOME_TEXT)
    assert reply == WELCOME_ECHO_REPLY
    assert "account" not in reply.lower()
    assert _payload(conv)["lead_menu_pending"] is True

    conv2 = _conv(db, UNREGISTERED, mobile="9000111666")
    corrected = "dY`< Hello Welcome to InfraDealer dYso Thanks for contacting us We help buyers and sellers 1 Buy equipment 2 Sell equipment"
    assert lead_welcome_reply(db, conv2, _payload(conv2), corrected) == WELCOME_ECHO_REPLY
    assert not is_welcome_echo("Welcome sir, JCB bechna hai")


def test_menu_choices():
    db = _session()
    for raw, expected_text, expected_reply in [
        ("1", MENU_CHOICES["1"], ""),
        ("2️⃣", MENU_CHOICES["2"], ""),
        ("Option 1", MENU_CHOICES["1"], ""),
        ("3", "3", PARTNER_TEXT),
        ("JCB chahiye Indore me", "JCB chahiye Indore me", ""),
    ]:
        conv = _conv(db, {**UNREGISTERED, "lead_welcome_sent": True, "lead_menu_pending": True},
                     mobile=f"90002{abs(hash(raw)) % 100000:05d}")
        text, reply = resolve_menu_choice(conv, _payload(conv), raw)
        assert (text, reply) == (expected_text, expected_reply), raw
        assert _payload(conv)["lead_menu_pending"] is False
        assert resolve_menu_choice(conv, _payload(conv), "1") == ("1", "")


def test_menu_choice_ignored_without_pending_menu():
    db = _session()
    conv = _conv(db, UNREGISTERED)
    assert resolve_menu_choice(conv, _payload(conv), "2") == ("2", "")


def test_orchestrator_ad_click_then_menu_pick(monkeypatch):
    from app.ai.orchestrator import handle_message
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    db = _session()
    conv = AiConversation(mobile="919700099911", conversation_id="wa:919700099911", state="NEW", payload_json="{}")
    db.add(conv)
    db.commit()

    seen = []

    def fake_rm(db, conv, text, media_note=""):
        seen.append(text)
        return "rules reply"

    monkeypatch.setattr("app.ai.chat_memory.handle_message", fake_rm)

    assert handle_message(db, conv, "Hello! Can I get more info on this?") == WELCOME_TEXT
    assert seen == []
    assert handle_message(db, conv, "1") == "rules reply"
    assert seen == [MENU_CHOICES["1"]]


def test_idle_reset_notice_not_sent_to_customer():
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "ai", "orchestrator.py"),
        encoding="utf-8",
    ).read()
    assert "memory_reset_new_chat" not in src
