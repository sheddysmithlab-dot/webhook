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


def test_revoked_operator_session_is_dropped(db, client):
    conv = _conv(db)
    om.handle_office_turn(db, conv, "customer 9876543210")
    client.operators.clear()
    assert om.handle_office_turn(db, conv, "customer 9876543210") is None
    assert db.query(AiOfficeSession).count() == 0
