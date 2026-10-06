"""Meta status webhooks: failure reason kept, no tick downgrade."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiEvent, Chat
from app.wa_status import apply_status, delivery_error_reason


def _db_with_chat(status: str = "sent"):
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Chat(
        wamid="wamid.OUT1", conversation_id="CONV_9925000001", from_mobile="",
        to_mobile="9925000001", direction="outbound", body="hii", status=status,
    ))
    db.flush()
    return db


def test_failed_status_keeps_code_and_reason():
    db = _db_with_chat()
    apply_status(db, {
        "id": "wamid.OUT1", "status": "failed", "recipient_id": "919925000001",
        "errors": [{"code": 131026, "title": "Message undeliverable", "error_data": {"details": "x"}}],
    })
    chat = db.query(Chat).one()
    assert chat.status == "failed:131026"
    assert "deliver nahi" in delivery_error_reason(chat.status).lower()
    ev = db.query(AiEvent).one()
    assert ev.event_type == "wa_delivery_failed" and ev.mobile == "9925000001" and "131026" in ev.detail


def test_delivered_then_late_sent_does_not_downgrade():
    db = _db_with_chat()
    apply_status(db, {"id": "wamid.OUT1", "status": "read"})
    apply_status(db, {"id": "wamid.OUT1", "status": "delivered"})
    assert db.query(Chat).one().status == "read"
    assert delivery_error_reason("read") == ""


def test_reason_for_unknown_and_missing_code():
    assert "999" in delivery_error_reason("failed:999")
    assert delivery_error_reason("failed")
    assert "24 ghante" in delivery_error_reason("failed:131047")


def test_unknown_wamid_is_ignored():
    db = _db_with_chat()
    apply_status(db, {"id": "wamid.OTHER", "status": "delivered"})
    assert db.query(Chat).one().status == "sent"
