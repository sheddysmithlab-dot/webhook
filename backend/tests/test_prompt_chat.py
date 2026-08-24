"""Phase-1/2 prompt-chat flag + state + wiring tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.prompt import (
    SYSTEM_PROMPT,
    build_current_state,
    prompt_chat_enabled,
    soft_rules_fallback,
    unified_chat_enabled,
)
from app.models import AiConversation


def test_system_prompt_has_wa_account_match_rule():
    low = SYSTEM_PROMPT.lower()
    assert "whatsapp number" in low or "wa_account_matched" in low
    assert "registered" in low or "wrong" in low
    assert "not created" in low or "account is not created" in low


def test_build_current_state_wa_unmatched():
    conv = AiConversation(
        conversation_id="wa:nomatch",
        mobile="919900000099",
        state="NEW",
        payload_json="{}",
    )
    payload = {
        "account_type": "MISSING",
        "account_reason": "ACCOUNT_NOT_FOUND",
        "account_eligibility": "NOT_ELIGIBLE",
        "wa_account_matched": False,
        "account_context": {"account": {"found": False, "type": "MISSING"}},
    }
    st = build_current_state(conv, payload, "hinglish")
    assert st["wa_account_matched"] is False
    assert st["account_found"] is False
    assert st["account_reason"] == "ACCOUNT_NOT_FOUND"


def test_account_block_reply_prefers_wa_mismatch():
    from app.ai.chat_memory import _account_block_reply

    msg = _account_block_reply("hinglish", {
        "account_reason": "ACCOUNT_NOT_FOUND",
        "account_type": "MISSING",
    })
    low = (msg or "").lower()
    assert "whatsapp" in low or "number" in low or "number" in low or "match" in low
    assert "account" in low


def test_build_current_state_phase3():
    conv = AiConversation(
        conversation_id="wa:st",
        mobile="919900000001",
        state="COLLECTING",
        payload_json="{}",
    )
    payload = {
        "intent": "SELL",
        "brand": "Ashok Leyland",
        "model": "1920",
        "rm_state": "DATA_COLLECTION",
        "master_workflow_state": "DATA_COLLECTION",
        "account_onboarded": True,
        "media_ids": ["a", "b"],
    }
    st = build_current_state(conv, payload, "hinglish", media_note="image")
    assert st["phase"] == "prompt_chat_v3"
    assert st["data"]["brand"] == "Ashok Leyland"
    assert st["photo_count"] == 2
    assert st["photos_complete"] is True
    assert st["next_ask"]
    assert st["media"] == "image"
    assert st["listing_active"] is True
    assert st["offer_menu"] is False


def test_build_current_state_offer_menu_small_talk():
    conv = AiConversation(
        conversation_id="wa:menu",
        mobile="919900000002",
        state="NEW",
        payload_json="{}",
    )
    st = build_current_state(
        conv, {}, "hinglish", offer_menu=True, route_hint="options"
    )
    assert st["phase"] == "prompt_chat_v3"
    assert st["offer_menu"] is True
    assert st["route_hint"] == "options"
    assert st["listing_active"] is False


def test_unified_chat_follows_prompt_flag(monkeypatch):
    from app.ai.prompt import unified_chat_enabled
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    assert unified_chat_enabled(None) is True
    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    assert unified_chat_enabled(None) is False


def test_soft_rules_follows_flag(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    assert soft_rules_fallback(None) is True
    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    assert soft_rules_fallback(None) is False


def test_prompt_chat_flag_off(monkeypatch, db_session):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    assert prompt_chat_enabled(db_session) is False
    assert prompt_chat_enabled(None) is False


def test_prompt_chat_flag_on_without_key(monkeypatch, db_session):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    assert prompt_chat_enabled(None) is True


def test_prepare_prompt_state_sets_next_ask(monkeypatch, db_session):
    from app.ai import engine as eng
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    conv = AiConversation(
        conversation_id="wa:test-prep",
        mobile="919999000099",
        state="NEW",
        payload_json='{"intent":"SELL","brand":"Tata"}',
    )
    db_session.add(conv)
    db_session.commit()
    pl = eng.prepare_prompt_state(db_session, conv, "2020 model hai", "")
    assert pl.get("intent") == "SELL" or pl.get("brand")
    assert "next_ask" in pl or pl.get("missing_fields") is not None


def test_prompt_chat_turn_respects_flag(monkeypatch, db_session):
    from app.ai import engine as eng
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    conv = AiConversation(
        conversation_id="wa:test-prompt-1",
        mobile="919999000001",
        state="NEW",
        payload_json="{}",
    )
    db_session.add(conv)
    db_session.commit()
    assert eng.prompt_chat_turn(db_session, conv, "gaadi bechna hai") is None


def test_prompt_chat_turn_hard_clear(monkeypatch, db_session):
    from app.ai import engine as eng
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    monkeypatch.setattr(eng, "llm_configured", lambda db: False)
    conv = AiConversation(
        conversation_id="wa:test-prompt-clear",
        mobile="919999000002",
        state="NEW",
        payload_json="{}",
    )
    db_session.add(conv)
    db_session.commit()
    out = eng.prompt_chat_turn(db_session, conv, "chat clear kar do")
    assert out is None or isinstance(out, str)


def test_orchestrator_falls_back_when_prompt_disabled(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import AiConversation
    from app.ai.orchestrator import handle_message
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", False)
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    mobile = "919888777001"
    conv = AiConversation(
        conversation_id=f"wa:{mobile}",
        mobile=mobile,
        state="NEW",
        payload_json="{}",
    )
    db.add(conv)
    db.commit()

    called = {"rm": False}

    def fake_rm(db, conv, text, media_note=""):
        called["rm"] = True
        return "rules reply"

    monkeypatch.setattr("app.ai.chat_memory.handle_message", fake_rm)
    reply = handle_message(db, conv, "hello")
    assert called["rm"] is True
    assert reply == "rules reply"


def test_orchestrator_uses_prompt_chat_when_enabled(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import AiConversation
    from app.ai.orchestrator import handle_message
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    mobile = "919888777002"
    conv = AiConversation(
        conversation_id=f"wa:{mobile}",
        mobile=mobile,
        state="NEW",
        payload_json="{}",
    )
    db.add(conv)
    db.commit()

    called = {"prompt": False, "rm": False}

    def fake_prompt(db, conv, text, media_note=""):
        called["prompt"] = True
        return "llm natural reply"

    def fake_rm(db, conv, text, media_note=""):
        called["rm"] = True
        return "rules reply"

    monkeypatch.setattr("app.ai.prompt.prompt_chat_enabled", lambda db=None: True)
    monkeypatch.setattr("app.ai.engine.prompt_chat_turn", fake_prompt)
    monkeypatch.setattr("app.ai.chat_memory.handle_message", fake_rm)
    reply = handle_message(db, conv, "Ashok Leyland tipper bechna hai")
    assert called["prompt"] is True
    assert called["rm"] is False
    assert reply == "llm natural reply"


def test_orchestrator_falls_back_if_llm_empty(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import AiConversation
    from app.ai.orchestrator import handle_message
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    mobile = "919888777003"
    conv = AiConversation(
        conversation_id=f"wa:{mobile}",
        mobile=mobile,
        state="NEW",
        payload_json="{}",
    )
    db.add(conv)
    db.commit()

    called = {"rm": False}

    def fake_prompt(db, conv, text, media_note=""):
        return None

    def fake_rm(db, conv, text, media_note=""):
        called["rm"] = True
        return "fallback rules"

    monkeypatch.setattr("app.ai.prompt.prompt_chat_enabled", lambda db=None: True)
    monkeypatch.setattr("app.ai.engine.prompt_chat_turn", fake_prompt)
    monkeypatch.setattr("app.ai.chat_memory.handle_message", fake_rm)
    monkeypatch.setattr("app.ai.free_chat.free_chat_enabled", lambda db: False)
    reply = handle_message(db, conv, "bechna hai")
    assert called["rm"] is True
    assert reply == "fallback rules"


def test_chat_memory_soft_skips_sell_reask(monkeypatch):
    """Phase-2 soft: repeating bechna with intent already SELL continues collection."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.ai.chat_memory import handle_message
    from app.ai.tools import _write_payload
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    conv = AiConversation(
        conversation_id="wa:soft1",
        mobile="919111222333",
        state="COLLECTING_SELL",
        payload_json="{}",
    )
    db.add(conv)
    db.commit()
    _write_payload(conv, {
        "intent": "SELL",
        "brand": "Tata",
        "rm_state": "DATA_COLLECTION",
        "account_onboarded": True,
        "account_eligibility": "ELIGIBLE",
    })
    db.commit()

    reply = handle_message(db, conv, "bechna hai")
    assert reply
    assert len(reply) > 5
