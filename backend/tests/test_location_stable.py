"""Regression: location must not flip on later listing detail messages."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation
from app.ai.chat_memory import collect_message
from app.ai.data_filteration import extract_fields, normalize_location, normalize_year
from app.ai.extract import extract_from_text, extract_state
from app.ai.tools import execute_tool, _payload


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_normalize_location_rejects_price_text():
    assert normalize_location(location="18 lakh") == {}
    assert normalize_location(location="price up karo") == {}
    assert normalize_location(location="Tata 1613 bechna hai") == {}
    print("OK reject price-as-location")


def test_normalize_location_keeps_real_places():
    loc = normalize_location(location="Indore")
    assert loc.get("city") == "Indore"
    assert loc.get("state") == "Madhya Pradesh"
    dew = normalize_location(city="Dewas", location="Dewas")
    assert dew.get("city") == "Dewas"
    mp = normalize_location(location="Madhya Pradesh")
    assert mp.get("state") == "Madhya Pradesh"
    print("OK real places")


def test_extract_fields_does_not_set_location_from_price():
    out = extract_fields([{"text": "18 lakh", "source": "USER"}], {})
    assert out.get("location") in (None, "")
    assert out.get("state") in (None, "")
    assert out.get("city") in (None, "")
    assert normalize_year("18 lakh") is None
    print("OK price extract no location")


def test_extract_fields_preserves_seeded_location():
    out = extract_fields(
        [{"text": "18 lakh", "source": "USER"}],
        {"city": "Indore", "location": "Indore", "state": "Madhya Pradesh"},
    )
    assert out.get("city") == "Indore"
    assert out.get("location") == "Indore"
    assert out.get("state") == "Madhya Pradesh"
    print("OK seeded location preserved")


def test_extract_state_ignores_price_up():
    assert extract_state("price up karo") is None
    assert extract_state("up") == "Uttar Pradesh"
    assert extract_state("Madhya Pradesh") == "Madhya Pradesh"
    assert extract_state("मध्य प्रदेश") == "Madhya Pradesh"
    print("OK extract_state")


def test_collect_message_keeps_location_across_detail_turns():
    db = _session()
    conv = AiConversation(
        mobile="9000111000",
        conversation_id="CONV_LOC",
        state="SELL_DATA_COLLECTION",
        payload_json="{}",
        intent="SELL",
    )
    db.add(conv)
    db.flush()
    execute_tool(db, conv, "save_customer_data", {"intent": "SELL"})
    execute_tool(
        db,
        conv,
        "save_vehicle_data",
        {
            "brand": "Tata",
            "model": "1613",
            "year": "2019",
            "city": "Indore",
            "location": "Indore",
            "state": "Madhya Pradesh",
            "source": "customer",
        },
    )
    collect_message(db, conv, "18 lakh")
    pl = _payload(conv)
    assert pl.get("city") == "Indore"
    assert pl.get("location") == "Indore"
    assert pl.get("state") == "Madhya Pradesh"

    collect_message(db, conv, "2 lakh km")
    pl = _payload(conv)
    assert pl.get("location") == "Indore"

    collect_message(db, conv, "price up kar do")
    pl = _payload(conv)
    assert pl.get("location") == "Indore"
    assert pl.get("state") == "Madhya Pradesh"
    print("OK location stable across detail turns")


def test_explicit_location_change_still_works():
    db = _session()
    conv = AiConversation(
        mobile="9000111001",
        conversation_id="CONV_LOC2",
        state="SELL_DATA_COLLECTION",
        payload_json="{}",
        intent="SELL",
    )
    db.add(conv)
    db.flush()
    execute_tool(db, conv, "save_customer_data", {"intent": "SELL"})
    execute_tool(
        db,
        conv,
        "save_vehicle_data",
        {"city": "Indore", "location": "Indore", "state": "Madhya Pradesh", "source": "customer"},
    )
    collect_message(db, conv, "location Bhopal kar do")
    pl = _payload(conv)
    assert pl.get("city") == "Bhopal"
    assert pl.get("location") == "Bhopal"
    print("OK explicit location change")


def test_extract_from_text_location_karo():
    assert extract_from_text("Location karo").get("state") in (None, "")
    assert extract_from_text("Location. मध्य प्रदेश")["state"] == "Madhya Pradesh"
    print("OK extract_from_text")


if __name__ == "__main__":
    test_normalize_location_rejects_price_text()
    test_normalize_location_keeps_real_places()
    test_extract_fields_does_not_set_location_from_price()
    test_extract_fields_preserves_seeded_location()
    test_extract_state_ignores_price_up()
    test_collect_message_keeps_location_across_detail_turns()
    test_explicit_location_change_still_works()
    test_extract_from_text_location_karo()
    print("ALL OK")
