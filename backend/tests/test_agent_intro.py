"""The first reply of a fresh chat says the bot is the InfraDealer AI Agent."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.ai.agent_intro as intro
import app.ai.office_mode as office_mode
from app.database import Base
from app.models import AiConversation
from app.services import store_chat, utcnow

MOBILE = "6269990785"
CONV_ID = f"CONV_{MOBILE}"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(office_mode, "is_office_session", lambda db, conv: False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _conv(db):
    conv = AiConversation(mobile=MOBILE, conversation_id=CONV_ID, state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    return conv


def _outbound(db, hours_ago: float):
    store_chat(
        db,
        wamid=f"out.{hours_ago}",
        conversation_id=CONV_ID,
        from_mobile="infradealer",
        to_mobile=MOBILE,
        direction="outbound",
        body="old reply",
        timestamp_ms=int((utcnow().timestamp() - hours_ago * 3600) * 1000),
    )
    db.flush()


def test_first_reply_gets_intro(db):
    out = intro.with_agent_intro(db, _conv(db), CONV_ID, MOBILE, "hinglish", "Gaadi ki details bhejiye.")
    assert out.startswith("🤖 Main *InfraDealer AI Agent* hoon")
    assert out.endswith("Gaadi ki details bhejiye.")


def test_no_intro_mid_conversation(db):
    conv = _conv(db)
    _outbound(db, hours_ago=1)
    assert intro.with_agent_intro(db, conv, CONV_ID, MOBILE, "hinglish", "Model batayein?") == "Model batayein?"


def test_intro_again_after_long_gap(db):
    conv = _conv(db)
    _outbound(db, hours_ago=intro.INTRO_GAP_HOURS + 1)
    assert "InfraDealer AI Agent" in intro.with_agent_intro(db, conv, CONV_ID, MOBILE, "en", "Hi")


def test_no_double_intro(db):
    reply = "Namaste! Main InfraDealer AI Agent hoon. Aap gaadi bechna chahte ho ya kharidna?"
    assert intro.with_agent_intro(db, _conv(db), CONV_ID, MOBILE, "hinglish", reply) == reply


def test_office_number_skips_intro(db, monkeypatch):
    monkeypatch.setattr(office_mode, "is_office_session", lambda db, conv: True)
    assert intro.with_agent_intro(db, _conv(db), CONV_ID, MOBILE, "hinglish", "Menu") == "Menu"
