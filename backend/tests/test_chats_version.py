"""Chat list polling only ships the full list when something changed."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.routers.webhook import list_chats
from app.services import store_chat


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _msg(db, body="hi", **kw):
    row = store_chat(
        db, wamid=f"w.{body}", conversation_id="CONV_9999999999", from_mobile="9999999999",
        direction="inbound", body=body, **kw,
    )
    db.flush()
    return row


def _poll(db, version):
    return list_chats(q="", unread=False, from_number="", version=version, db=db)


def test_legacy_call_still_returns_list(db):
    _msg(db)
    assert isinstance(list_chats(q="", unread=False, from_number="", version=None, db=db), list)


def test_unchanged_poll_skips_list(db):
    _msg(db)
    first = _poll(db, "none")
    assert first["changed"] and len(first["chats"]) == 1
    again = _poll(db, first["version"])
    assert again == {"version": first["version"], "changed": False}


@pytest.mark.parametrize("change", ["new_message", "read", "status", "delete"])
def test_any_change_ships_list(db, change):
    row = _msg(db)
    v = _poll(db, "none")["version"]
    if change == "new_message":
        _msg(db, body="second")
    elif change == "read":
        row.unread = False
    elif change == "status":
        row.status = "read"
    else:
        db.delete(row)
    db.flush()
    res = _poll(db, v)
    assert res["changed"] and res["version"] != v
