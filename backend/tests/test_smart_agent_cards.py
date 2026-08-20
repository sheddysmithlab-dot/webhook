"""Lightweight chat simulation for smart AI agent Card ID + account filter rules."""

from __future__ import annotations

import os
import sys

# Allow running from backend/ as cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, migrate_schema
from app.models import AiConversation, AiListingDraft, AiMedia, User
from app.ai.cards import (
    ensure_card_id,
    format_card_id,
    next_card_seq,
    parse_card_mention,
    photos_status,
    switch_active_card,
)
from app.ai.account_filter import verify_account, classify_user
from app.ai.engine import respond
from app.ai.tools import _draft_for, _payload, _write_payload


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # migrate_schema uses settings DB — skip for memory; columns exist via models
    return sessionmaker(bind=engine)()


def test_card_ids_and_isolation():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_9988776655", state="NEW")
    db.add(conv)
    db.flush()

    d1 = _draft_for(db, conv)
    assert d1.card_id == "CARD-001", d1.card_id
    assert _payload(conv).get("active_card_id") == "CARD-001"

    # Second card
    conv.draft_id = None
    d2 = _draft_for(db, conv)
    assert d2.card_id == "CARD-002", d2.card_id
    assert d1.card_id != d2.card_id

    # Switch back
    switched = switch_active_card(db, conv, "CARD-001")
    assert switched.id == d1.id
    assert conv.draft_id == d1.id
    assert parse_card_mention("CARD-002 pe baat karo") == "CARD-002"
    print("OK card ids + isolation")


def test_photos_min_max():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_9988776655", state="SELL_DATA_COLLECTION")
    db.add(conv)
    db.flush()
    draft = _draft_for(db, conv)
    st = photos_status(db, draft.id)
    assert st["need_more"] is True
    for i in range(2):
        db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=f"/tmp/{i}.jpg"))
    db.flush()
    st = photos_status(db, draft.id)
    assert st["ready"] is True
    assert st["count"] == 2
    print("OK photos min 2")


def test_account_filter_types():
    db = _session()
    # missing
    v = verify_account(db, "9000000001")
    assert v.account_type == "missing"
    assert v.can_post is False

    u = User(name="Office", mobile="9000000002", role="office")
    db.add(u)
    db.flush()
    v = verify_account(db, "9000000002")
    assert v.account_type == "office" and v.can_post

    u2 = User(name="Free", mobile="9000000003", role="user")
    db.add(u2)
    db.flush()
    v = verify_account(db, "9000000003")
    assert v.account_type == "free" and v.can_post
    print("OK account filter")


def test_chat_card_switch_reply():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_9988776655", state="SELL_DATA_COLLECTION", language="hinglish")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    ensure_card_id(db, d1)
    conv.draft_id = None
    d2 = _draft_for(db, conv)
    ensure_card_id(db, d2)
    db.commit()

    reply = respond(db, conv, "CARD-001", "")
    assert "CARD-001" in reply, reply
    assert conv.draft_id == d1.id
    print("OK chat card switch:", reply[:80])


if __name__ == "__main__":
    test_card_ids_and_isolation()
    test_photos_min_max()
    test_account_filter_types()
    test_chat_card_switch_reply()
    print("ALL SIM CHECKS PASSED")
