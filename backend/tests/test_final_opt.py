"""Final optimization tests: Redis fallback, stale skip, cards, photos, account."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiListingDraft, AiMedia, Chat, InfraDealerAccountState, User
from app.redis_cache import (
    get_latest_wamid,
    is_latest_wamid,
    mobile_lock,
    reset_redis_client,
    set_latest_wamid,
)
from app.ai.account import should_intercept_account
from app.ai.account_filter import verify_account
from app.ai.cards import (
    card_photo_count,
    clear_card_chat_data,
    photos_ready,
    photos_status,
    schedule_card_cleanup,
    switch_active_card,
)
from app.ai.runner import _is_latest_inbound, parse_meta_message
from app.ai.tools import _draft_for, _payload, _write_payload


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_redis_fallback_latest_and_lock():
    reset_redis_client()
    # Force no Redis URL behavior via local maps
    set_latest_wamid("9988776655", "wamid-A")
    assert get_latest_wamid("9988776655") == "wamid-A"
    assert is_latest_wamid("9988776655", "wamid-A") is True
    assert is_latest_wamid("9988776655", "wamid-B") is False

    set_latest_wamid("9988776655", "wamid-B")
    assert is_latest_wamid("9988776655", "wamid-A") is False
    assert is_latest_wamid("9988776655", "wamid-B") is True

    with mobile_lock("9988776655") as held:
        assert held is True
    print("OK redis fallback latest+lock")


def test_stale_inbound_db_and_redis():
    reset_redis_client()
    db = _session()
    mobile = "9988776655"
    conv_id = f"CONV_{mobile}"
    db.add(Chat(conversation_id=conv_id, from_mobile=mobile, direction="inbound", body="a", wamid="A", timestamp_ms=1))
    db.add(Chat(conversation_id=conv_id, from_mobile=mobile, direction="inbound", body="b", wamid="B", timestamp_ms=2))
    db.flush()
    set_latest_wamid(mobile, "B")
    assert _is_latest_inbound(db, conv_id, "B", mobile=mobile) is True
    assert _is_latest_inbound(db, conv_id, "A", mobile=mobile) is False
    print("OK stale inbound check")


def test_reply_prefix_strip():
    p = parse_meta_message({"type": "text", "text": {"body": "Reply Bhoj Sillu"}})
    assert p["text"] == "Bhoj Sillu"
    print("OK reply prefix strip")


def test_account_no_hijack():
    assert should_intercept_account({"account_step": "ask_exists"}, "haan") is True
    assert should_intercept_account({"account_step": "ask_exists"}, "JCB 3DX bechna hai 18 lakh") is False
    assert should_intercept_account({"account_step": "otp"}, "123456") is True
    assert should_intercept_account({"account_step": "otp"}, "price badlo") is False
    print("OK account no hijack")


def test_photos_limits():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_P", state="SELL_DATA_COLLECTION")
    db.add(conv)
    db.flush()
    draft = _draft_for(db, conv)
    assert photos_ready(db, draft.id) is False
    db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path="/tmp/1.jpg"))
    db.flush()
    assert photos_status(db, draft.id)["need_more"] is True
    db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path="/tmp/2.jpg"))
    db.flush()
    assert photos_ready(db, draft.id) is True
    for i in range(3, 6):
        db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=f"/tmp/{i}.jpg"))
    db.flush()
    assert card_photo_count(db, draft.id) == 5
    assert photos_status(db, draft.id)["at_max"] is True
    print("OK photos 1/2/5")


def test_multi_card_isolation_and_cleanup():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_M", state="SELL_DATA_COLLECTION", language="hinglish")
    db.add(conv)
    db.flush()
    d1 = _draft_for(db, conv)
    pl = _payload(conv)
    pl["brand"] = "Tata"
    _write_payload(conv, pl)
    from app.ai.cards import persist_active_card_session

    persist_active_card_session(db, conv)
    conv.draft_id = None
    d2 = _draft_for(db, conv)
    pl2 = _payload(conv)
    pl2["brand"] = "JCB"
    _write_payload(conv, pl2)
    persist_active_card_session(db, conv)

    switch_active_card(db, conv, "CARD-001")
    assert _payload(conv).get("brand") == "Tata"
    switch_active_card(db, conv, "CARD-002")
    assert _payload(conv).get("brand") == "JCB"

    d1.status = "POSTED"
    schedule_card_cleanup(d1, minutes=0)
    d1.cleanup_at = datetime.utcnow() - timedelta(minutes=1)
    clear_card_chat_data(db, conv, d1)
    assert d1.status == "CLEARED"
    assert conv.draft_id == d2.id
    assert _payload(conv).get("brand") == "JCB"
    print("OK multi-card + cleanup isolation")


def test_account_filter_types():
    db = _session()
    assert verify_account(db, "9000000001").eligibility == "NOT_ELIGIBLE"
    db.add(User(name="Office", mobile="9000000002", role="office"))
    db.flush()
    assert verify_account(db, "9000000002").eligibility == "ELIGIBLE"
    db.add(User(name="Token", mobile="9000000004", role="token"))
    db.add(InfraDealerAccountState(mobile="9000000004", account_status="ACCOUNT_FOUND", meta_json='{"credits":0}'))
    db.flush()
    assert verify_account(db, "9000000004").eligibility == "NOT_ELIGIBLE"
    db.add(User(name="Broker", mobile="9000000005", role="broker"))
    db.add(
        InfraDealerAccountState(
            mobile="9000000005",
            account_status="ACCOUNT_FOUND",
            meta_json='{"broker_subscription_active": true}',
        )
    )
    db.flush()
    assert verify_account(db, "9000000005").eligibility == "ELIGIBLE"
    print("OK account filter")


if __name__ == "__main__":
    test_redis_fallback_latest_and_lock()
    test_stale_inbound_db_and_redis()
    test_reply_prefix_strip()
    test_account_no_hijack()
    test_photos_limits()
    test_multi_card_isolation_and_cleanup()
    test_account_filter_types()
    # Keep existing suite
    from tests.test_smart_agent_cards import (
        test_ambiguous_card_clarification,
        test_card_ids_and_isolation,
        test_chat_card_switch_reply,
        test_summary_includes_card,
        test_whatsapp_user_collect_and_remote_details,
    )

    test_card_ids_and_isolation()
    test_ambiguous_card_clarification()
    test_chat_card_switch_reply()
    test_summary_includes_card()
    test_whatsapp_user_collect_and_remote_details()
    print("ALL FINAL OPT CHECKS PASSED")
