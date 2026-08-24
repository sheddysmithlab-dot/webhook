"""Phase-4 observability: reply_path + latency on orchestrator turns."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_orchestrator_emits_reply_path(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import AiConversation, AiEvent
    from app.ai.orchestrator import handle_message
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    mobile = "919700011122"
    conv = AiConversation(
        conversation_id=f"wa:{mobile}",
        mobile=mobile,
        state="NEW",
        payload_json="{}",
    )
    db.add(conv)
    db.commit()

    monkeypatch.setattr("app.ai.prompt.prompt_chat_enabled", lambda db=None: True)
    monkeypatch.setattr(
        "app.ai.engine.prompt_chat_turn",
        lambda db, conv, text, media_note="": "Namaste Sir, kya bechna hai?",
    )
    monkeypatch.setattr("app.ai.free_chat.free_chat_enabled", lambda db: False)

    reply = handle_message(db, conv, "hi")
    assert reply and "bechna" in reply.lower()

    events = db.query(AiEvent).all()
    assert events, "expected orchestrator AiEvent rows"
    blob = " ".join((e.detail or "") + " " + (e.event_type or "") for e in events)
    assert "reply_path" in blob or "prompt_chat" in blob


def test_system_prompt_phase3_guidance():
    from app.ai.prompt import SYSTEM_PROMPT, build_current_state
    from app.models import AiConversation

    assert "offer_menu" in SYSTEM_PROMPT.lower() or "small talk" in SYSTEM_PROMPT.lower()
    conv = AiConversation(
        conversation_id="wa:obs",
        mobile="919700011123",
        state="NEW",
        payload_json="{}",
    )
    st = build_current_state(conv, {}, "hinglish", offer_menu=True, route_hint="options")
    assert st["phase"] == "prompt_chat_v3"
    assert st["offer_menu"] is True
    assert st["route_hint"] == "options"
