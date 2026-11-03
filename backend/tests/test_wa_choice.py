"""General WhatsApp interactive choices (sell/buy, confirm, menu, account, …)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import wa_choice as wc
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.models import AiConversation


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _conv(db, mobile="9000111222"):
    row = AiConversation(
        mobile=mobile,
        conversation_id=f"CONV_{mobile}",
        state="NEW",
        payload_json="{}",
    )
    db.add(row)
    db.flush()
    return row


def _take(conv):
    return wc.take_wa_interactive_ui(conv)


def test_resolve_wa_choice_ids():
    assert wc.resolve_wa_choice_text(wc.WA_BTN_SELL) == "sell"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_BUY) == "buy"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_YES) == "Haan"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_NO) == "Nahi"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_SKIP) == "skip"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_BROKER) == "broker"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_SESSION_NEW) == "nayi listing"
    assert wc.resolve_wa_choice_text("wa_cat_truck") == "Truck"
    assert wc.resolve_wa_choice_text(wc.WA_BTN_MENU_SELL) == "1"


def test_arm_and_take_confirm(db):
    conv = _conv(db)
    wc.arm_confirm_choice(conv)
    ui = _take(conv)
    assert ui["mode"] == "buttons"
    assert {b["title"] for b in ui["buttons"]} == {"Haan", "Nahi"}


def test_ensure_arms_intent_on_greet(db):
    conv = _conv(db)
    reply = "Namaste! Main InfraDealer AI Agent hoon. Aap gaadi/machine bechna chahte ho ya kharidna?"
    wc.ensure_interactive_for_reply(conv, reply)
    ui = _take(conv)
    assert ui["mode"] == "buttons"
    ids = {b["id"] for b in ui["buttons"]}
    assert wc.WA_BTN_SELL in ids and wc.WA_BTN_BUY in ids


def test_ensure_arms_intent_confirm(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(conv, "Toh Tata 407 bechna hai ya kharidna?")
    ui = _take(conv)
    assert ui["mode"] == "buttons"
    assert {b["id"] for b in ui["buttons"]} == {wc.WA_BTN_SELL, wc.WA_BTN_BUY}


def test_ensure_arms_menu_list(db):
    conv = _conv(db)
    reply = "Sir, kya aap karna chahte hain?\n\n1. Sell\n2. Buy\n\nNumber reply kijiye."
    wc.ensure_interactive_for_reply(conv, reply)
    ui = _take(conv)
    assert ui["mode"] == "list"
    assert ui["button_label"] == "Select option"
    assert len(ui["rows"]) == 4


def test_ensure_arms_confirm_when_ask_in_reply(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(conv, "Brand: Tata\n\nKya ye details sahi hain? Haan likhiye.")
    ui = _take(conv)
    assert ui["mode"] == "buttons"
    assert ui["buttons"][0]["title"] == "Haan"


def test_ensure_arms_place_confirm(db):
    conv = _conv(db)
    reply = (
        "Brand: Tata\n\n📍 Gaadi kis state/city me hai? Likh dijiye — buyers ko dhoondhne me aasani hogi.\n"
        "Neeche *Haan* (post) ya *Skip location* dabayein.\n\n"
        "Kya ye details sahi hain? Neeche *Haan* / *Nahi* dabayein."
    )
    wc.ensure_interactive_for_reply(conv, reply)
    ui = _take(conv)
    titles = {b["title"] for b in ui["buttons"]}
    assert titles == {"Haan", "Skip location", "Nahi"}


def test_ensure_arms_optional_skip_continue(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Kuch aur details bhejna hai? (KM / owners)\nNeeche *Skip* / *Continue* dabayein.",
    )
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Skip", "Continue"}


def test_ensure_arms_year_confirm(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(conv, "Year 2019 confirm kar doon? Neeche *Haan* / *Nahi* dabayein.")
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Haan", "Nahi"}


def test_ensure_arms_category_list(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Kaunsi category hai? (Truck / Tipper / JCB / Excavator / Crane / Bus / Tractor / Other)",
    )
    ui = _take(conv)
    assert ui["mode"] == "list"
    assert len(ui["rows"]) == 10
    assert ui["rows"][0]["title"] == "Truck"


def test_ensure_arms_account_offer(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Aapka InfraDealer account nahi mila.\nKya main WhatsApp se aapka account bana du?",
    )
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Haan", "Nahi"}


def test_ensure_arms_role(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(conv, "Aap broker hain ya user? Broker ke liye alag process hai.")
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Broker", "User"}


def test_ensure_arms_username(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Aapke liye username suggest kar raha hoon:\n*ramesh123*\n\nKya ye username theek hai?",
    )
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Haan", "Change"}


def test_ensure_arms_chat_cleared(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Chat clear ho gayi. Nayi listing shuru karein ya last listing update karein.",
    )
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Nayi listing", "Last update"}


def test_ensure_arms_after_submit(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Ji Sir, listing submit ho chuki hai. Nayi listing chahiye?",
    )
    ui = _take(conv)
    assert {b["title"] for b in ui["buttons"]} == {"Haan", "Nahi"}
    assert wc.resolve_wa_choice_text(ui["buttons"][0]["id"]) == "nayi listing"


def test_ensure_arms_account_next(db):
    conv = _conv(db)
    wc.ensure_interactive_for_reply(
        conv,
        "Aur kuch chahiye? Vehicle bechna/kharidna, listing, ya password help — bataiye.",
    )
    ui = _take(conv)
    assert ui["mode"] == "list"
    assert len(ui["rows"]) == 4


def test_ensure_skips_confirm_without_ask_text(db):
    conv = _conv(db)
    pl = _payload(conv)
    pl["awaiting_confirm"] = True
    _write_payload(conv, pl)
    wc.ensure_interactive_for_reply(conv, "Photo mil gayi — thoda wait.")
    assert _payload(conv).get("wa_ui_pending") in (False, None)


def test_office_ui_blocks_general_arm(db):
    conv = _conv(db)
    pl = _payload(conv)
    pl["office_button_set"] = "pick"
    pl["office_send_choice_buttons"] = True
    _write_payload(conv, pl)
    wc.ensure_interactive_for_reply(conv, "Aap sell karna chahte ho ya buy?")
    assert _payload(conv).get("wa_ui_pending") in (False, None)
