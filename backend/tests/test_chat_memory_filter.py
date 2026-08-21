"""chat_memory → data_filteration collect/confirm pipeline."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation
from app.ai.chat_memory import collect_message, read_memory, send_for_confirmation
from app.ai.data_filteration import filter_collected, filter_memory, is_collection_ready
from app.ai.tools import _payload


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_collect_then_filter_ready():
    db = _session()
    conv = AiConversation(mobile="9988776655", conversation_id="CONV_M", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()

    collect_message(db, conv, "Tata 407 bechna hai 2019 model 8 lakh MP")
    pl = read_memory(db, conv)
    assert (pl.get("intent") or "").upper() == "SELL"
    assert pl.get("brand")
    assert pl.get("model") or pl.get("year")

    # Fill remaining required fields explicitly if extract missed any
    from app.ai.chat_memory import apply_fields

    apply_fields(
        db,
        conv,
        {
            "intent": "SELL",
            "category": "Truck",
            "brand": "Tata",
            "model": "407",
            "year": "2019",
            "expected_price": "8 lakh",
            "state": "Madhya Pradesh",
        },
    )
    pl = read_memory(db, conv)
    assert is_collection_ready(pl)
    filtered = filter_collected(pl)
    assert filtered["brand"] == "Tata"
    assert filtered["category"] == "Truck"
    result = filter_memory(db, conv)
    assert result.ready is True
    assert result.data.get("vehicle")
    text = send_for_confirmation(db, conv, "hinglish")
    assert "Vehicle" in text or "vehicle" in text.lower() or "Tata" in text
    assert _payload(conv).get("awaiting_confirm") is True
    assert _payload(conv).get("summary_json")
    print("OK chat_memory → data_filteration confirm")


if __name__ == "__main__":
    test_collect_then_filter_ready()
    print("ALL OK")
