"""10-min idle memory reset + last-listing update resume."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiListingDraft
from app.ai.session_memory import (
    find_last_listing_draft,
    is_memory_idle,
    prepare_turn,
    reset_idle_chat_memory,
    resume_last_listing_for_update,
    touch_activity,
    wants_update_last_listing,
)
from app.ai.tools import _payload, _write_payload


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_wants_update_last_listing():
    assert wants_update_last_listing("last listing me price badlo")
    assert wants_update_last_listing("pichhli listing update karo")
    assert wants_update_last_listing("usme rate change kar do")
    assert not wants_update_last_listing("Tata 1618 bechna hai")
    print("OK wants_update_last_listing")


def test_idle_reset_forgets_topic():
    db = _session()
    conv = AiConversation(mobile="9000111222", conversation_id="CONV_IDLE", state="SELL_DATA_COLLECTION", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, {
        "intent": "SELL",
        "brand": "Tata",
        "model": "1618",
        "rm_state": "DATA_COLLECTION",
        "last_user_message_at": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=11)).isoformat(),
    })
    assert is_memory_idle(conv) is True
    prep = prepare_turn(db, conv, "hi")
    assert prep["mode"] == "new_chat"
    assert prep["reset"] is True
    pl = _payload(conv)
    assert pl.get("brand") in (None, "")
    assert pl.get("chat_cleared") is True
    assert pl.get("memory_reset_reason") == "USER_IDLE_AFTER_LAST_MSG"
    # This new message should refresh the user clock (not a recurring timer)
    assert pl.get("last_user_message_at")
    print("OK idle reset")


def test_last_listing_resume_from_db():
    db = _session()
    conv = AiConversation(mobile="9000333444", conversation_id="CONV_LAST", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(
        conversation_id=conv.id,
        mobile="9000333444",
        card_id="CARD-001",
        intent="SELL",
        status="POSTED",
        title="Tata 1618",
        confirmed_json=json.dumps({
            "brand": "Tata",
            "model": "1618",
            "year": "2023",
            "expected_price": "23 lakh",
            "state": "Madhya Pradesh",
            "category": "Truck",
        }),
        inferred_json="{}",
    )
    db.add(draft)
    db.flush()

    assert find_last_listing_draft(db, "9000333444").id == draft.id
    loaded = resume_last_listing_for_update(db, conv)
    assert loaded is not None
    pl = _payload(conv)
    assert pl.get("brand") == "Tata"
    assert pl.get("listing_edit_mode") is True
    assert conv.draft_id == draft.id

    prep = prepare_turn(db, conv, "last listing me price 22 lakh kar do")
    assert prep["mode"] == "engine_update"
    assert prep.get("card_id") == "CARD-001"
    print("OK last listing resume")


def test_fresh_activity_not_idle():
    db = _session()
    conv = AiConversation(mobile="9000555666", conversation_id="CONV_FRESH", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    touch_activity(conv)
    assert is_memory_idle(conv) is False
    prep = prepare_turn(db, conv, "Tata bechna hai")
    assert prep["mode"] == "continue"
    print("OK fresh activity")


if __name__ == "__main__":
    test_wants_update_last_listing()
    test_idle_reset_forgets_topic()
    test_last_listing_resume_from_db()
    test_fresh_activity_not_idle()
    print("ALL OK")
