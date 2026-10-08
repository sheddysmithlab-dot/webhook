"""Office operator (postdesk) mode: customer select/create, confirm guard, listing target."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.ai.office_mode as om
from app.ai.account import handle_same_number_account_policy
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.models import AiConversation, AiOfficeSession

OPERATOR = "8224000829"
CUSTOMER = {"user_id": "77", "username": "ramesh.kumar", "name": "ramesh.kumar", "phone": "9876543210", "status": "active"}


class FakeClient:
    def __init__(self, operators=(OPERATOR,), customers=None):
        self.operators = set(operators)
        self.customers = dict(customers or {})
        self.created = []

    def _res(self, status, body):
        return {"ok": 200 <= status < 300, "http_status": status, "body": body}

    def office_customer_lookup(self, operator_phone, phone):
        if operator_phone not in self.operators:
            return self._res(403, {"success": False, "code": "OFFICE_NOT_AUTHORIZED"})
        cust = self.customers.get(phone)
        if not cust:
            return self._res(200, {"success": True, "found": False, "phone": phone})
        return self._res(200, {"success": True, "found": True, "customer": cust})

    def office_customer_create(self, operator_phone, phone, name):
        if operator_phone not in self.operators:
            return self._res(403, {"success": False, "code": "OFFICE_NOT_AUTHORIZED"})
        if phone in self.customers:
            return self._res(409, {"success": False, "code": "ACCOUNT_EXISTS", "customer": self.customers[phone]})
        cust = {"user_id": "501", "username": "suresh.patel", "name": name, "phone": phone, "status": "active"}
        self.customers[phone] = cust
        self.created.append((phone, name))
        return self._res(201, {"success": True, "code": "ACCOUNT_CREATED", "customer": cust})


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


@pytest.fixture()
def client(monkeypatch):
    fake = FakeClient(customers={"9876543210": CUSTOMER})
    monkeypatch.setattr(om, "_client", lambda db: fake)
    monkeypatch.setattr(om, "signature_enforced", lambda db: True)
    om._denied.clear()
    return fake


def _conv(db, mobile=OPERATOR, **payload):
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, dict(payload))
    return conv


def test_select_existing_customer(db, client):
    conv = _conv(db)
    reply = om.handle_office_turn(db, conv, "customer 9876543210")
    assert "Customer selected" in reply
    assert "Mobile: 9876543210" in reply and "User ID: ramesh.kumar" in reply
    row = db.query(AiOfficeSession).one()
    assert (row.target_user_id, row.target_phone) == ("77", "9876543210")


def test_select_accepts_country_code_and_spaces(db, client):
    conv = _conv(db)
    assert "Customer selected" in om.handle_office_turn(db, conv, "Customer +91 98765 43210")


def test_unknown_customer_suggests_create(db, client):
    conv = _conv(db)
    reply = om.handle_office_turn(db, conv, "customer 9123456780")
    assert "create customer 9123456780" in reply
    assert om.active_target(db.query(AiOfficeSession).one()) is None


def test_create_customer_asks_name_then_creates_without_otp(db, client):
    conv = _conv(db)
    ask = om.handle_office_turn(db, conv, "create customer 9123456780")
    assert "naam" in ask.lower()
    done = om.handle_office_turn(db, conv, "Suresh Patel")
    assert done.startswith("✅ New account created")
    assert "Mobile: 9123456780" in done and "Name: Suresh Patel" in done
    assert "Username: suresh.patel" in done and "Created by: Office / Postdesk" in done
    assert client.created == [("9123456780", "Suresh Patel")]
    assert om.active_target(db.query(AiOfficeSession).one())["user_id"] == "501"


def test_create_for_existing_number_selects_it(db, client):
    conv = _conv(db)
    reply = om.handle_office_turn(db, conv, "create customer 9876543210")
    assert "pehle se hai" in reply
    assert client.created == []


def test_create_name_step_can_be_cancelled(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "create customer 9123456780")
    assert "cancel" in om.handle_office_turn(db, conv, "cancel").lower()
    assert client.created == []


def test_non_operator_commands_fall_through(db, client):
    conv = _conv(db, mobile="9000111333")
    assert om.handle_office_turn(db, conv, "customer 9876543210") is None
    assert om.handle_office_turn(db, conv, "create customer 9123456780") is None
    assert db.query(AiOfficeSession).count() == 0
    assert client.created == []


def test_public_agent_number_is_never_operator(db, client):
    conv = _conv(db, mobile="8224000826")
    assert om.handle_office_turn(db, conv, "customer 9876543210") is None


def test_office_mode_off_without_meta_signature(db, client, monkeypatch):
    monkeypatch.setattr(om, "signature_enforced", lambda db: False)
    conv = _conv(db)
    assert om.handle_office_turn(db, conv, "customer 9876543210") is None
    assert om.office_listing_context(db, conv, "office") == {"office_mode": False, "target": None}


def test_customer_change_and_clear(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert "Name: ramesh.kumar" in om.handle_office_turn(db, conv, "customer")
    assert "hata diya" in om.handle_office_turn(db, conv, "customer change")
    assert om.active_target(db.query(AiOfficeSession).one()) is None
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert "clear" in om.handle_office_turn(db, conv, "customer clear").lower()
    assert om.active_target(db.query(AiOfficeSession).one()) is None


def test_ordinary_customer_sentence_is_not_a_command(db, client):
    conv = _conv(db)
    assert om.handle_office_turn(db, conv, "customer ne bola price 5 lakh") is None


def test_yes_without_target_is_blocked(db, client):
    conv = _conv(db, account_type="office", awaiting_confirm=True)
    reply = om.handle_office_turn(db, conv, "haan")
    assert "kis customer" in reply


def test_yes_with_target_continues_to_normal_submit(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    pl = _payload(conv)
    pl["awaiting_confirm"] = True
    _write_payload(conv, pl)
    assert om.handle_office_turn(db, conv, "yes") is None


def test_confirm_reply_shows_post_to_target(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    pl = _payload(conv)
    pl["awaiting_confirm"] = True
    _write_payload(conv, pl)
    out = om.decorate_office_reply(db, conv, "Please confirm details:\nVehicle: JCB 3DX")
    assert "Post to: ramesh.kumar, 9876543210" in out
    assert out.rstrip().endswith("Confirm? YES/NO")


def test_listing_context_carries_target_user_id(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    ctx = om.office_listing_context(db, conv, "office")
    assert ctx["office_mode"] is True
    assert ctx["target"]["user_id"] == "77" and ctx["target"]["phone"] == "9876543210"


def test_regular_customer_has_no_office_context(db, client):
    conv = _conv(db, mobile="9000111333", account_type="free")
    assert om.office_listing_context(db, conv, "free") is None


def test_same_number_policy_skipped_only_for_operator(db, client):
    op = _conv(db)
    om.handle_office_turn(db, op, "customer 9876543210")
    assert handle_same_number_account_policy(db, op, "contact 9123456780 pe call karo", "hinglish") is None

    other = _conv(db, mobile="9000111333")
    assert handle_same_number_account_policy(db, other, "9123456780 ka account banao", "hinglish")


def _office_draft(db, conv):
    from app.models import AiListingDraft, Chat

    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="READY", title="Tata 407")
    db.add(draft)
    db.add(Chat(conversation_id=conv.conversation_id, direction="inbound", from_mobile=conv.mobile,
                body="Seller number 9988776655 hai"))
    db.flush()
    return draft


def test_listing_payload_posts_to_selected_customer(db, client):
    from app.infradealer.payloads import build_listing_payload

    conv = _conv(db, account_type="office", brand="Tata", model="407", expected_price="5 lakh", state="Madhya Pradesh")
    om.handle_office_turn(db, conv, "customer 9876543210")
    draft = _office_draft(db, conv)
    body = build_listing_payload(db, conv, draft, _payload(conv), "req-office-t1", infradealer_user_id="5")
    assert body["customer"]["phone"].endswith(OPERATOR)
    assert body["customer"]["office_mode"] is True
    assert body["customer"]["target_user_id"] == "77"
    assert body["listing"]["seller_contact"] == "9876543210"
    assert body["customer"]["name"] == "ramesh.kumar"


def test_listing_payload_downgrades_office_without_signature(db, client, monkeypatch):
    from app.infradealer.payloads import build_listing_payload

    conv = _conv(db, account_type="office", brand="Tata", model="407", state="Madhya Pradesh")
    om.handle_office_turn(db, conv, "customer 9876543210")
    monkeypatch.setattr(om, "signature_enforced", lambda db: False)
    draft = _office_draft(db, conv)
    body = build_listing_payload(db, conv, draft, _payload(conv), "req-office-t2")
    assert body["customer"]["office_mode"] is False
    assert "target_user_id" not in body["customer"]


def test_natural_sentence_selects_customer(db, client):
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "98765 43210\n\nis nummber wale account se ek vehicle post krna h")
    assert "Customer selected" in reply and "User ID: ramesh.kumar" in reply
    assert om.active_target(db.query(AiOfficeSession).one())["user_id"] == "77"


def test_natural_sentence_unknown_number_offers_create(db, client):
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "91115 54173 is account related detail check karo")
    assert "koi account nahi hai: 9111554173" in reply and "naam" in reply
    done = om.handle_office_turn(db, conv, "Shivraj Singh")
    assert done.startswith("✅ New account created") and "Mobile: 9111554173" in done
    assert client.created == [("9111554173", "Shivraj Singh")]


def test_natural_sentence_ignored_for_regular_users(db, client):
    conv = _conv(db, mobile="9000111333", account_type="free")
    assert om.handle_office_turn(db, conv, "98765 43210 wale account se post karna hai") is None
    assert db.query(AiOfficeSession).count() == 0


def test_natural_sentence_needs_account_word(db, client):
    conv = _conv(db, account_type="office")
    assert om.handle_office_turn(db, conv, "seller contact 98765 43210, JCB 3DX 2019") is None


DETAILED = {
    **CUSTOMER,
    "tokens": 12,
    "listings_total": 3,
    "listings_live": 1,
    "listings_pending": 1,
    "listings_rejected": 1,
    "recent_listings": [
        {"id": "41", "title": "JCB 3DX 2019", "status": "approved", "price": 2500000,
         "url": "https://infradealer.com/listings/41"},
        {"id": "40", "title": "Tata 407", "status": "pending", "price": 550000},
    ],
}


def test_screenshot_sentence_selects_customer_without_account_word(db, client):
    client.customers["9111554173"] = {**CUSTOMER, "user_id": "88", "phone": "9111554173", "username": "shiv"}
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "is number se listing daal do 9111554173")
    assert "Customer selected" in reply and "Mobile: 9111554173" in reply
    assert om.active_target(db.query(AiOfficeSession).one())["user_id"] == "88"


@pytest.mark.parametrize("text", [
    "9876543210",
    "+91 98765 43210",
    "98765 43210 wale ki listing dikhao",
    "9876543210 ka detail do",
    "9876543210 select karo",
])
def test_short_operator_messages_with_mobile_select(db, client, text):
    conv = _conv(db, account_type="office")
    assert "Customer selected" in om.handle_office_turn(db, conv, text)


def test_operator_select_unknown_bare_number_offers_create(db, client):
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "9111554173")
    assert "koi account nahi hai: 9111554173" in reply


def test_vehicle_description_with_seller_mobile_is_not_hijacked(db, client):
    conv = _conv(db, account_type="office")
    text = "JCB 3DX 2019 model, price 25 lakh, owner contact 9876543210, Indore"
    assert om.handle_office_turn(db, conv, text) is None


def test_select_shows_wallet_and_listings(db, client):
    client.customers["9876543210"] = DETAILED
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "customer 9876543210")
    assert "Wallet: 12 tokens" in reply
    assert "Listings: 3 total | Live 1 | Pending 1 | Rejected 1" in reply
    assert "JCB 3DX 2019 — ₹25 lakh — Live" in reply
    assert "https://infradealer.com/listings/41" in reply
    assert "Tata 407 — ₹5.5 lakh — Pending" in reply


def test_account_detail_shows_selected_customer_not_office(db, client):
    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    client.customers["9876543210"] = DETAILED
    reply = om.handle_office_turn(db, conv, "MERE ACCOUNT KI DETAILDO")
    assert reply.startswith("📋 Customer account")
    assert "Mobile: 9876543210" in reply and "Wallet: 12 tokens" in reply


def test_account_detail_without_selection_asks_for_customer(db, client):
    conv = _conv(db, account_type="office")
    reply = om.handle_office_turn(db, conv, "account ki detail do")
    assert "koi customer select nahi" in reply and "customer 98XXXXXXXX" in reply


def test_listing_list_request_shows_details(db, client):
    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    client.customers["9876543210"] = DETAILED
    assert "Recent listings:" in om.handle_office_turn(db, conv, "listing dikhao")


def test_vehicle_detail_message_is_not_account_detail(db, client):
    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert om.handle_office_turn(db, conv, "vehicle detail bhej raha hu") is None


def test_greeting_shows_office_menu(db, client):
    conv = _conv(db, account_type="office")
    menu = om.handle_office_turn(db, conv, "HII")
    assert "Office Mode" in menu and "koi customer select nahi" in menu
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert "Selected: ramesh.kumar, 9876543210" in om.handle_office_turn(db, conv, "hi")


def test_regular_user_greeting_and_detail_fall_through(db, client):
    conv = _conv(db, mobile="9000111333", account_type="free")
    assert om.handle_office_turn(db, conv, "hii") is None
    assert om.handle_office_turn(db, conv, "mere account ki detail do") is None
    assert om.handle_office_turn(db, conv, "9876543210") is None
    assert db.query(AiOfficeSession).count() == 0


def test_operator_recognised_from_cached_account_state(db, client):
    from app.models import InfraDealerAccountState

    db.add(InfraDealerAccountState(mobile=OPERATOR, account_status="ACCOUNT_FOUND",
                                   meta_json='{"account_type": "office"}'))
    db.flush()
    conv = _conv(db)
    assert "Customer selected" in om.handle_office_turn(db, conv, "is number se listing daal do 9876543210")


def _card(db, conv, status="COLLECTING", **fields):
    from app.models import AiListingDraft

    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status=status, title="x",
                           card_id=f"CARD-{db.query(AiListingDraft).count() + 1:03d}")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    pl = _payload(conv)
    pl.update(fields)
    _write_payload(conv, pl)
    return draft


SECOND = {"user_id": "88", "username": "shiv", "name": "shiv", "phone": "9111554173", "status": "active"}


def test_switching_customer_starts_a_fresh_card(db, client):
    client.customers["9111554173"] = SECOND
    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    old = _card(db, conv, brand="Mahindra", model="575", expected_price=450000, city="Indore")
    reply = om.handle_office_turn(db, conv, "is number se listing daal do 9111554173")
    assert "Customer selected" in reply and "adhoora card band" in reply
    pl = _payload(conv)
    assert conv.draft_id != old.id
    assert not pl.get("brand") and not pl.get("expected_price") and not pl.get("city")


def test_first_selection_keeps_details_already_sent(db, client):
    conv = _conv(db, account_type="office")
    draft = _card(db, conv, brand="Tata", model="3118")
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert conv.draft_id == draft.id and _payload(conv)["brand"] == "Tata"


def test_selection_after_posted_card_starts_fresh(db, client):
    conv = _conv(db, account_type="office")
    posted = _card(db, conv, status="POSTED", brand="Tata", model="407", infradealer_listing_id="55")
    om.handle_office_turn(db, conv, "customer 9876543210")
    assert conv.draft_id != posted.id
    assert not _payload(conv).get("infradealer_listing_id")


def test_new_vehicle_after_submitted_card_opens_new_card(db, client):
    from app.ai.confirm import maybe_start_new_card

    conv = _conv(db, mobile="9000111333", account_type="free")
    posted = _card(db, conv, status="PENDING_REVIEW", brand="Mahindra", model="575", operating_hours=2500)
    assert maybe_start_new_card(db, conv, {"brand": "Tata", "model": "3118", "year": 2018})
    assert conv.draft_id != posted.id and not _payload(conv).get("operating_hours")


def test_office_paste_of_another_vehicle_opens_new_card(db, client):
    from app.ai.confirm import maybe_start_new_card

    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    draft = _card(db, conv, brand="Mahindra", model="575", operating_hours=2500, city="Indore")
    assert maybe_start_new_card(db, conv, {"brand": "Tata", "model": "3118", "year": 2018, "city": "Khandwa"})
    assert conv.draft_id != draft.id
    assert not _payload(conv).get("operating_hours")


def test_regular_user_brand_correction_keeps_card(db, client):
    from app.ai.confirm import maybe_start_new_card

    conv = _conv(db, mobile="9000111333", account_type="free")
    draft = _card(db, conv, brand="Tata", model="1618", city="Indore")
    assert not maybe_start_new_card(db, conv, {"brand": "Eicher", "model": "1618", "year": 2019})
    assert conv.draft_id == draft.id


def test_office_cannot_reopen_previous_customers_card(db, client):
    from app.ai.session_memory import prepare_turn

    client.customers["9111554173"] = SECOND
    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    _card(db, conv, status="POSTED", brand="Mahindra", model="575")
    om.handle_office_turn(db, conv, "customer 9111554173")
    prep = prepare_turn(db, conv, "last listing me price badlo")
    assert prep["mode"] != "engine_update"


def test_office_can_edit_card_made_for_current_customer(db, client):
    from app.ai.session_memory import prepare_turn

    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    _card(db, conv, status="POSTED", brand="Tata", model="407")
    prep = prepare_turn(db, conv, "last listing me price badlo")
    assert prep["mode"] == "engine_update"


def test_office_submit_reply_has_no_cooldown(db, client):
    from app.ai.i18n import t

    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    out = om.decorate_office_reply(db, conv, t("hi", "submitted"))
    assert "10 मिनट" not in out and "रिव्यू" not in out and "Agli listing" in out
    assert "ramesh.kumar" in out and "submit ho gayi" in out
    pl = _payload(conv)
    pl["listing_status"] = "POSTED"
    _write_payload(conv, pl)
    assert "post ho gayi (live)" in om.decorate_office_reply(db, conv, t("hi", "submitted"))
    regular = _conv(db, mobile="9000111333", account_type="free")
    assert "10 मिनट" in om.decorate_office_reply(db, regular, t("hi", "submitted"))


def test_listing_button_skipped_after_approved_message(db, client, monkeypatch):
    from app import services
    from app.ai.confirm import _send_listing_button

    sent = []
    monkeypatch.setattr(services, "send_whatsapp_button", lambda *a, **k: sent.append(a) or {})
    conv = _conv(db)
    pl = _payload(conv)
    pl["listing_url"] = "https://infradealer.com/listings/5"
    pl["listing_review_notified"] = True
    _write_payload(conv, pl)
    _send_listing_button(db, conv, "hi")
    assert sent == []


def test_full_listing_text_asks_photos_before_summary(db, client, monkeypatch):
    from app.ai import engine as eng
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    monkeypatch.setattr(eng, "llm_configured", lambda db: False)
    conv = _conv(db, mobile="9000222444", account_type="free")
    pl = _payload(conv)
    pl.update({
        "intent": "SELL", "category": "Truck", "brand": "Eicher", "model": "3015", "year": 2019,
        "expected_price": 1200000, "running_km": 250000, "state": "Madhya Pradesh", "city": "Sagar",
        "account_eligibility": "ELIGIBLE", "account_can_post": True, "account_onboarded": True,
    })
    _write_payload(conv, pl)
    monkeypatch.setattr(eng, "prepare_prompt_state", lambda db, conv, text, media_note: _payload(conv))
    monkeypatch.setattr(eng, "handle_account_info", lambda *a, **k: None)
    monkeypatch.setattr(eng, "needs_account_gate", lambda payload: False)
    monkeypatch.setattr(eng, "prompt_chat_enabled", lambda db: True)
    out = eng.prompt_chat_turn(db, conv, "Eicher Pro 3015 truck 2019 Sagar 12 lakh")
    assert "photo" in (out or "").lower() or "फोटो" in (out or "")
    assert not _payload(conv).get("awaiting_confirm")


def test_account_switch_request_clears_customer(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    reply = om.handle_office_turn(
        db, conv, "Ab account change karke dusre number se dalna hai\nAnother account se posting dalna hai"
    )
    assert "customer 98XXXXXXXX" in reply
    assert om.active_target(db.query(AiOfficeSession).one()) is None


def test_vehicle_text_is_not_an_account_switch():
    assert not om._wants_account_switch("Tata 3118 tipper 2018 model price 23 lakh, naya tyre")
    assert not om._wants_account_switch("dusre customer 9876543210 ka account")
    assert not om._wants_account_switch("photo change karni hai")


def test_posted_card_is_closed_on_next_message(db, client):
    from app.ai.tools import _draft_for
    from app.models import AiListingDraft

    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    pl = _payload(conv)
    pl.update({"intent": "SELL", "brand": "Tata", "model": "3118", "office_draft_floor": 3})
    _write_payload(conv, pl)
    draft = _draft_for(db, conv)
    draft.status = "POSTED"
    db.flush()
    om.handle_office_turn(db, conv, "Yes")
    assert conv.draft_id is None
    pl = _payload(conv)
    assert not pl.get("brand") and pl.get("office_draft_floor") == 3
    assert db.get(AiListingDraft, draft.id).status == "POSTED"


def test_unposted_card_is_kept(db, client):
    from app.ai.tools import _draft_for

    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    pl = _payload(conv)
    pl.update({"intent": "SELL", "brand": "Tata", "awaiting_confirm": True})
    _write_payload(conv, pl)
    draft = _draft_for(db, conv)
    om.handle_office_turn(db, conv, "Yes")
    assert conv.draft_id == draft.id and _payload(conv).get("brand") == "Tata"


def test_running_km_in_lakh_is_extracted():
    from app.ai.data_filteration import extract_fields

    out = extract_fields(["Ashok Leyland 2518 tipper, 2017 model, Price 15 lakh, Running 3.2 lakh km"])
    assert out["price"] == 1500000
    assert out["km"] == 320000


def test_office_line_skips_listing_cooldown(db, client):
    from app.ai.chat_memory import _office_line

    op = _conv(db, account_type="office")
    om.handle_office_turn(db, op, "customer 9876543210")
    assert _office_line(db, op) is True
    assert _office_line(db, _conv(db, mobile="9000111333", account_type="free")) is False


def test_hindi_tata_dump_extracts_price_and_city():
    from app.ai.engine import extract_turn

    text = ("Tata 3118  hayva BS 4*\n12 टायर टिपर (हाइवा)\nसितम्बर 2018 मॉडल\n"
            "*👉 लोकेशन -\nखंडवा, मध्य प्रदेश*\n👉 कीमत - 23. लाख रुपए मात्र")
    fields = extract_turn(text)
    assert fields["brand"] == "Tata" and fields["model"] == "3118" and fields["year"] == 2018
    assert fields["expected_price"] == 2300000
    assert fields["city"] == "Khandwa" and fields["state"] == "Madhya Pradesh"


def test_operator_told_why_office_mode_is_off(db, client, monkeypatch):
    monkeypatch.setattr(om, "signature_enforced", lambda db: False)
    office = _conv(db, account_type="office")
    assert "Meta App Secret" in om.handle_office_turn(db, office, "91115 54173 wale account se post karna h")
    assert "Meta App Secret" in om.handle_office_turn(db, office, "customer 9876543210")
    assert om.handle_office_turn(db, office, "JCB bechni hai") is None
    regular = _conv(db, mobile="9000111333", account_type="free")
    assert om.handle_office_turn(db, regular, "customer 9876543210") is None


def test_new_card_never_borrows_previous_cards_photos(db, client):
    from app.identity import unique_photo_ids
    from app.models import AiMedia

    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    tractor = _card(db, conv, brand="Mahindra", model="575")
    db.add(AiMedia(conversation_id=conv.id, draft_id=tractor.id, kind="image", local_path="/m/1.jpg", meta_media_id="a"))
    db.flush()
    assert len(unique_photo_ids(db, conv, _payload(conv))) == 1
    _card(db, conv, brand="Tata", model="3118", media_ids=[])
    assert unique_photo_ids(db, conv, _payload(conv)) == []


def test_new_card_after_chat_clear_is_not_treated_as_already_pushed(db, client):
    from app.ai.cards import clear_card_chat_data
    from app.ai.tools import _draft_for

    conv = _conv(db, account_type="office")
    om.handle_office_turn(db, conv, "customer 9876543210")
    posted = _card(db, conv, status="APPROVED", brand="Tata", model="3118",
                   infradealer_listing_id="103", listing_url="https://infradealer.com/listings/103",
                   listing_status="POSTED")
    clear_card_chat_data(db, conv, posted)
    assert conv.draft_id is None and _payload(conv)["infradealer_listing_id"] == "103"
    pl = _payload(conv)
    pl.update(brand="Ashok Leyland", model="2518", year=2017)
    _write_payload(conv, pl)
    fresh = _draft_for(db, conv)
    pl = _payload(conv)
    assert fresh.id != posted.id and pl["brand"] == "Ashok Leyland"
    assert not pl.get("infradealer_listing_id") and not pl.get("listing_url") and not pl.get("listing_status")


def test_listing_button_uses_single_cta_url(monkeypatch):
    import app.services as services

    sent = {}

    class _Resp:
        status_code = 200
        is_success = True
        content = b"{}"

        def json(self):
            return {"messages": [{"id": "wamid.X"}]}

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            sent.update(json)
            return _Resp()

    monkeypatch.setattr(services.httpx, "Client", _Client)

    class _Meta:
        phone_number_id = "123"
        system_user_token = "tok"
        graph_version = "v21.0"

    services.send_whatsapp_button(_Meta(), "9876543210", "Live!", [
        {"title": "View listing", "url": "https://infradealer.com/listing/1"},
        {"title": "Browse", "url": "https://infradealer.com"},
    ])
    inter = sent["interactive"]
    assert inter["type"] == "cta_url"
    assert inter["action"] == {"name": "cta_url", "parameters": {
        "display_text": "View listing", "url": "https://infradealer.com/listing/1"}}


def test_revoked_operator_session_is_dropped(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    client.operators.clear()
    assert om.handle_office_turn(db, conv, "customer 9876543210") is None
    assert db.query(AiOfficeSession).count() == 0
