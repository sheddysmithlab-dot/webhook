"""A customer who wants to buy gets matching listings, never a sell card."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.ai.buyer as buyer
from app.ai import engine as eng
from app.ai.confirm import collection_ready, send_summary
from app.ai.data_filteration import _looks_like_place, _match_state_name
from app.ai.schema import normalize_vehicle_category
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.models import AiConversation

LISTINGS = [
    {"id": 501, "title": "Toyota Innova Crysta 2021", "price": 1700000, "year": 2021, "city": "Ludhiana", "state": "Punjab"},
    {"id": 502, "title": "Toyota Innova Crysta 2019", "price": 1500000, "year": 2019, "city": "Amritsar", "state": "Punjab"},
    {"id": 503, "title": "Toyota Innova Crysta 2023", "price": 2600000, "year": 2023, "city": "Patiala", "state": "Punjab"},
]


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def api(monkeypatch):
    calls = []

    def fake_fetch(params):
        calls.append(dict(params))
        return list(LISTINGS) if params.get("state") == "Punjab" else []

    monkeypatch.setattr(buyer, "fetch_listings", fake_fetch)
    return calls


def _conv(db, mobile="6269990785", **payload):
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, dict(payload))
    return conv


def _turn(db, conv, text):
    pl = eng.prepare_prompt_state(db, conv, text)
    if pl.get("next_ask"):
        conv.error_message = f"ask:{pl['next_ask']}"
    return pl


def test_innova_buyer_chat_never_becomes_a_sell_card(db, api):
    conv = _conv(db)
    pl = _turn(db, conv, "Pb ki innova crysta chahiye")
    assert pl["intent"] == "BUY"
    assert pl["brand"] == "Toyota" and pl["model"] == "Innova Crysta"
    assert pl["state"] == "Punjab"
    assert pl["next_ask"] == "budget"

    _turn(db, conv, "20 ke baad ka model")
    pl = _turn(db, conv, "18 lakh")
    assert pl["intent"] == "BUY"
    assert not pl.get("expected_price")
    assert int(float(pl["budget"])) == 1800000
    assert collection_ready(pl)

    reply = send_summary(db, conv, "hinglish")
    assert "Innova Crysta" in reply and "Punjab" in reply
    assert "https://infradealer.com/listings/501" in reply
    assert "/listings/503" not in reply  # over budget
    assert "/listings/502" not in reply  # older than 2020
    assert "Haan" not in reply and "Card" not in reply
    assert "search=Innova+Crysta" in reply and "state=Punjab" in reply
    assert not _payload(conv).get("awaiting_confirm")
    assert api and api[0]["search"] == "Innova Crysta"

    assert send_summary(db, conv, "hinglish") == ""


def test_changed_budget_searches_again(db, api):
    conv = _conv(db, intent="BUY", brand="Toyota", model="Innova Crysta", category="Other",
                 state="Punjab", budget="1800000", year="2020")
    assert send_summary(db, conv, "hinglish")
    conv.error_message = "ask:budget"
    pl = _turn(db, conv, "25 lakh")
    assert int(float(pl["budget"])) == 2500000
    assert "/listings/503" in send_summary(db, conv, "hinglish")


def test_no_state_match_falls_back_to_other_states(db, monkeypatch):
    monkeypatch.setattr(buyer, "fetch_listings", lambda p: [] if p.get("state") else list(LISTINGS[:1]))
    conv = _conv(db, intent="BUY", brand="Toyota", model="Innova Crysta", category="Other",
                 state="Kerala", budget="2000000")
    reply = send_summary(db, conv, "hinglish")
    assert "Kerala me abhi nahi mili" in reply and "/listings/501" in reply


def test_no_match_still_gives_search_link(db, monkeypatch):
    monkeypatch.setattr(buyer, "fetch_listings", lambda p: [])
    conv = _conv(db, intent="BUY", category="Tipper", brand="Tata", state="Punjab", budget="1800000")
    reply = send_summary(db, conv, "hinglish")
    assert "listing nahi hai" in reply
    assert "https://infradealer.com/listings?search=Tata&state=Punjab" in reply


def test_buy_intent_detection():
    assert eng.extract_turn("Pb ki innova crysta chahiye").get("intent") == "BUY"
    assert eng.extract_turn("Mujhe equipment kharidna hai").get("intent") == "BUY"
    assert eng.extract_turn("JCB 3DX 2019 chahiye budget 20 lakh").get("intent") == "BUY"
    assert eng.extract_turn("token chahiye").get("intent") is None
    assert eng.extract_turn("listing ka link chahiye").get("intent") is None
    assert eng.extract_turn("JCB 3DX 2018 Indore 15 lakh").get("intent") != "BUY"
    assert eng.extract_turn("Innova crysta bechni hai").get("intent") != "BUY"


def test_seller_mid_card_saying_chahiye_stays_seller(db):
    conv = _conv(db, intent="SELL", category="Tipper", brand="Tata")
    eng.apply_extraction(db, conv, "JCB chahiye")
    assert _payload(conv)["intent"] == "SELL"


def test_office_line_never_turns_buyer(db, monkeypatch):
    import app.ai.office_mode as om

    monkeypatch.setattr(om, "is_office_session", lambda db, conv: True)
    conv = _conv(db)
    pl = eng.prepare_prompt_state(db, conv, "Innova crysta chahiye Punjab 18 lakh")
    assert pl["intent"] == "SELL"


def test_vehicle_words_are_not_places_and_cars_are_other():
    assert not _looks_like_place("Four wheeler")
    assert not _looks_like_place("car")
    assert normalize_vehicle_category("Four wheeler") == "Other"
    assert normalize_vehicle_category("4 wheeler") == "Other"
    assert _match_state_name("pb ki innova") == "Punjab"
    assert _match_state_name("pb") == "Punjab"
    assert _match_state_name("pbx") is None
