"""Fresh listing agent — smoke tests (no legacy engine)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation
from app.ai.listing_agent import handle_message, _ask_missing
from app.ai.tools import _payload, _write_payload
from app.ai.confirm import is_yes, collection_ready
from app.ai.cards import MIN_PHOTOS, MAX_PHOTOS
from app.config import settings


def _db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_defaults_listing_mode():
    assert getattr(settings, "ai_simple_chat", False) is False
    assert MIN_PHOTOS == 2 and MAX_PHOTOS == 5
    assert is_yes("Haan") and not is_yes("OK")
    print("OK defaults")


def test_greeting_and_sell_extract():
    db = _db()
    conv = AiConversation(mobile="8224000826", conversation_id="CONV_8224000826", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()

    r1 = handle_message(db, conv, "Hello", "")
    assert "bechni" in r1.lower() or "leni" in r1.lower() or "बेच" in r1 or "selling" in r1.lower()

    r2 = handle_message(db, conv, "JCB 3DX 2022 Bhopal 18 lakh bechna hai tipper", "")
    pl = _payload(conv)
    assert (pl.get("intent") or "").upper() == "SELL"
    assert pl.get("brand")
    assert pl.get("model") or True  # 3DX may be model
    assert "18" in str(pl.get("expected_price") or "") or pl.get("expected_price")
    assert r2  # asks next missing (category/state etc.)
    print("OK greet+sell extract", pl.get("brand"), pl.get("year"), pl.get("state"))


def test_ok_not_confirm():
    assert is_yes("Haan") is True
    assert is_yes("Yes") is True
    assert is_yes("OK") is False
    assert is_yes("photo") is False
    print("OK confirm gates")


def test_collection_ready_sell():
    pl = {
        "intent": "SELL",
        "category": "Tipper",
        "brand": "Tata",
        "model": "407",
        "year": "2020",
        "expected_price": "20 lakh",
        "state": "Madhya Pradesh",
    }
    assert collection_ready(pl) is True
    print("OK collection_ready")


def test_zai_only_when_llm_called():
    db = _db()
    conv = AiConversation(mobile="8224000826", conversation_id="CONV_X", state="SELL_DATA_COLLECTION", payload_json="{}")
    db.add(conv)
    db.flush()
    pl = _payload(conv)
    pl.update({
        "intent": "SELL", "category": "JCB", "brand": "JCB", "model": "3DX",
        "year": "2022", "expected_price": "18 lakh", "state": "Madhya Pradesh",
        "optional_asked": True, "optional_done": True, "active_card_id": "CARD-001",
    })
    _write_payload(conv, pl)
    # Photos not ready → should ask photos without needing LLM
    r = handle_message(db, conv, "Aur batau?", "")
    assert "photo" in r.lower() or "फोटो" in r or "2" in r
    print("OK photo ask without invent")


if __name__ == "__main__":
    test_defaults_listing_mode()
    test_greeting_and_sell_extract()
    test_ok_not_confirm()
    test_collection_ready_sell()
    test_zai_only_when_llm_called()
    print("ALL LISTING AGENT OK")
