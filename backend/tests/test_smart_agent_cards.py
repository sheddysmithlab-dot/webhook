"""Smart WhatsApp AI agent — Card ID, isolation, photos, account, cleanup sims."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiListingDraft, AiMedia, InfraDealerAccountState, User
from app.ai.cards import (
    card_photo_count,
    clear_card_chat_data,
    ensure_card_id,
    needs_card_clarification,
    parse_card_mention,
    persist_active_card_session,
    photos_ready,
    photos_status,
    schedule_card_cleanup,
    switch_active_card,
)
from app.ai.account_filter import verify_account
from app.ai.confirm import summary_text, snapshot
from app.ai.engine import respond
from app.ai.tools import _draft_for, _payload, _write_payload


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_card_ids_and_isolation():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_9988776655", state="NEW")
    db.add(conv)
    db.flush()

    d1 = _draft_for(db, conv)
    assert d1.card_id == "CARD-001", d1.card_id
    assert _payload(conv).get("active_card_id") == "CARD-001"

    conv.draft_id = None
    d2 = _draft_for(db, conv)
    assert d2.card_id == "CARD-002", d2.card_id
    assert d1.card_id != d2.card_id

    switched = switch_active_card(db, conv, "CARD-001")
    assert switched.id == d1.id
    assert conv.draft_id == d1.id
    assert parse_card_mention("CARD-002 pe baat karo") == "CARD-002"
    print("OK card ids + isolation")


def test_session_restore_on_switch():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_A", state="SELL_DATA_COLLECTION", language="hinglish")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    pl = _payload(conv)
    pl["brand"] = "Tata"
    pl["model"] = "1618"
    pl["expected_price"] = "18 lakh"
    _write_payload(conv, pl)
    persist_active_card_session(db, conv)

    conv.draft_id = None
    d2 = _draft_for(db, conv)
    pl2 = _payload(conv)
    pl2["brand"] = "JCB"
    pl2["model"] = "3DX"
    _write_payload(conv, pl2)
    persist_active_card_session(db, conv)

    switch_active_card(db, conv, "CARD-001")
    restored = _payload(conv)
    assert restored.get("brand") == "Tata", restored
    assert restored.get("model") == "1618"
    assert restored.get("active_card_id") == "CARD-001"
    assert conv.draft_id == d1.id

    switch_active_card(db, conv, "CARD-002")
    restored2 = _payload(conv)
    assert restored2.get("brand") == "JCB"
    assert restored2.get("model") == "3DX"
    print("OK session restore on switch")


def test_ambiguous_card_clarification():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_B", state="SELL_DATA_COLLECTION", language="hinglish")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    d1.status = "COLLECTING"
    conv.draft_id = None
    d2 = _draft_for(db, conv)
    d2.status = "COLLECTING"
    db.flush()

    assert needs_card_clarification(db, conv, "isme price change karo") is True
    reply = respond(db, conv, "isme price 17 lakh kar do", "")
    assert "CARD-001" in reply or "CARD-002" in reply, reply
    assert "card" in reply.lower() or "Card" in reply or "CARD" in reply
    print("OK ambiguous clarification:", reply[:90])


def test_photos_min_max():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_C", state="SELL_DATA_COLLECTION")
    db.add(conv)
    db.flush()
    draft = _draft_for(db, conv)
    st = photos_status(db, draft.id)
    assert st["need_more"] is True
    assert photos_ready(db, draft.id) is False
    for i in range(2):
        db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=f"/tmp/{i}.jpg"))
    db.flush()
    assert photos_ready(db, draft.id) is True
    for i in range(2, 5):
        db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=f"/tmp/{i}.jpg"))
    db.flush()
    assert card_photo_count(db, draft.id) == 5
    assert photos_status(db, draft.id)["at_max"] is True
    print("OK photos min 2 / max 5")


def test_account_filter_types():
    db = _session()
    v = verify_account(db, "9000000001")
    assert v.account_type == "missing"
    assert v.can_post is False
    assert v.eligibility == "NOT_ELIGIBLE"

    u = User(name="Office", mobile="9000000002", role="office")
    db.add(u)
    db.flush()
    v = verify_account(db, "9000000002")
    assert v.account_type == "office" and v.can_post and v.eligibility == "ELIGIBLE"

    u2 = User(name="Free", mobile="9000000003", role="user")
    db.add(u2)
    db.flush()
    v = verify_account(db, "9000000003")
    assert v.account_type == "free" and v.can_post and v.eligibility == "ELIGIBLE"

    u3 = User(name="Token", mobile="9000000004", role="token")
    st = InfraDealerAccountState(mobile="9000000004", account_status="ACCOUNT_FOUND", meta_json='{"credits":0}')
    db.add_all([u3, st])
    db.flush()
    v = verify_account(db, "9000000004")
    assert v.account_type == "token" and v.can_post is False and v.eligibility == "NOT_ELIGIBLE"

    u4 = User(name="Broker", mobile="9000000005", role="broker")
    st2 = InfraDealerAccountState(
        mobile="9000000005",
        account_status="ACCOUNT_FOUND",
        meta_json='{"broker_subscription_active": true}',
    )
    db.add_all([u4, st2])
    db.flush()
    v = verify_account(db, "9000000005")
    assert v.account_type == "broker" and v.can_post and v.eligibility == "ELIGIBLE"
    print("OK account filter ELIGIBLE/NOT_ELIGIBLE")


def test_chat_card_switch_reply():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_D", state="SELL_DATA_COLLECTION", language="hinglish")
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


def test_summary_includes_card():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_E", state="SELL_DATA_COLLECTION")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    pl = _payload(conv)
    pl.update(
        {
            "brand": "JCB",
            "model": "3DX",
            "category": "JCB",
            "year": "2022",
            "expected_price": "18 lakh",
            "state": "Madhya Pradesh",
            "active_card_id": d1.card_id,
        }
    )
    _write_payload(conv, pl)
    data = snapshot(db, conv, pl)
    text = summary_text(data, "en")
    assert "Card :" in text and "CARD-001" in text, text
    print("OK summary Card line")


def test_cleanup_isolates_other_card():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_F", state="SELL_DATA_COLLECTION", language="hinglish")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    pl = _payload(conv)
    pl["brand"] = "Tata"
    _write_payload(conv, pl)
    persist_active_card_session(db, conv)

    conv.draft_id = None
    d2 = _draft_for(db, conv)
    pl2 = _payload(conv)
    pl2["brand"] = "JCB"
    _write_payload(conv, pl2)
    persist_active_card_session(db, conv)

    d1.status = "POSTED"
    schedule_card_cleanup(d1, minutes=0)
    d1.cleanup_at = datetime.utcnow() - timedelta(minutes=1)
    # Active is CARD-002
    clear_card_chat_data(db, conv, d1)
    assert d1.status == "CLEARED"
    assert conv.draft_id == d2.id  # other card still active
    assert _payload(conv).get("brand") == "JCB"
    # CARD-002 session still on draft
    saved = json.loads(d2.inferred_json or "{}")
    assert saved.get("brand") == "JCB"
    print("OK cleanup isolates other card")


if __name__ == "__main__":
    test_card_ids_and_isolation()
    test_session_restore_on_switch()
    test_ambiguous_card_clarification()
    test_photos_min_max()
    test_account_filter_types()
    test_chat_card_switch_reply()
    test_summary_includes_card()
    test_cleanup_isolates_other_card()
    print("ALL SIM CHECKS PASSED")
