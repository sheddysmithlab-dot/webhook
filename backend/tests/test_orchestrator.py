"""Four-agent orchestrator + account_filter coordination tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, Chat, Contact, InfraDealerAccountState, User
from app.ai.account_filter import (
    apply_remote_account,
    collect_whatsapp_user,
    normalize_phone,
    read_account_details,
    sync_conversation_account,
    verify_account,
)
from app.ai.orchestrator import (
    ORCHESTRATOR_VERSION,
    commit_workflow_state,
    correlation_snapshot,
    ensure_correlation,
    handle_message,
    handshake,
    handshake_response,
)
from app.ai.tools import _payload, _write_payload


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_phone_normalize():
    assert normalize_phone("+91 98765-43210") == "9876543210"
    assert normalize_phone("919876543210") == "9876543210"
    print("OK phone normalize")


def test_account_filter_types():
    db = _session()
    v = verify_account(db, "9000000001")
    assert v.account_type == "missing"
    assert v.can_post is False
    assert v.eligibility == "NOT_ELIGIBLE"

    db.add(User(name="Office", mobile="9000000002", role="office"))
    db.flush()
    v = verify_account(db, "9000000002")
    assert v.account_type == "office" and v.can_post and v.eligibility == "ELIGIBLE"

    db.add(User(name="Free", mobile="9000000003", role="user"))
    db.flush()
    v = verify_account(db, "9000000003")
    assert v.account_type == "free" and v.can_post and v.eligibility == "ELIGIBLE"

    db.add(User(name="Token", mobile="9000000004", role="token"))
    db.add(InfraDealerAccountState(mobile="9000000004", account_status="ACCOUNT_FOUND", meta_json='{"credits":0}'))
    db.flush()
    v = verify_account(db, "9000000004")
    assert v.account_type == "token" and v.can_post is False and v.eligibility == "NOT_ELIGIBLE"

    db.add(User(name="Broker", mobile="9000000005", role="broker"))
    db.add(
        InfraDealerAccountState(
            mobile="9000000005",
            account_status="ACCOUNT_FOUND",
            meta_json='{"broker_subscription_active": true}',
        )
    )
    db.flush()
    v = verify_account(db, "9000000005")
    assert v.account_type == "broker" and v.can_post and v.eligibility == "ELIGIBLE"
    print("OK account filter types")


def test_whatsapp_collect_and_remote():
    db = _session()
    conv = AiConversation(mobile="9876543210", conversation_id="CONV_9876543210", state="NEW", payload_json="{}")
    db.add(conv)
    db.add(Chat(
        conversation_id="CONV_9876543210",
        from_mobile="919876543210",
        from_name="Ramesh Kumar",
        direction="inbound",
        body="Namaste",
    ))
    db.add(Contact(mobile="9876543210", wa_id="919876543210", name="Ramesh Kumar"))
    db.flush()

    wa = collect_whatsapp_user(db, conv, persist=True)
    assert wa.mobile == "9876543210"
    assert wa.wa_name == "Ramesh Kumar"
    assert _payload(conv).get("wa_name") == "Ramesh Kumar"

    st = InfraDealerAccountState(mobile="9876543210", account_status="CHECKING")
    db.add(st)
    db.flush()
    apply_remote_account(
        st,
        {
            "account_found": True,
            "code": "ACCOUNT_FOUND",
            "account": {
                "user_id": "USR99",
                "name": "Ramesh Kumar",
                "phone": "+919876543210",
                "status": "verified",
                "account_type": "token",
                "credits": 0,
            },
        },
    )
    details = read_account_details(db, "9876543210")
    assert details.infradealer_user_id == "USR99"
    assert details.webhook_connected is True
    v = sync_conversation_account(db, conv)
    assert _payload(conv).get("account_eligibility") == "NOT_ELIGIBLE"
    assert _payload(conv).get("infradealer_user_id") == "USR99"
    assert v.wa_user and v.wa_user.wa_name == "Ramesh Kumar"
    print("OK whatsapp collect + remote")


def test_orchestrator_correlation_and_handshake():
    db = _session()
    conv = AiConversation(mobile="9111222333", conversation_id="CONV_X", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    pl = _payload(conv)
    ensure_correlation(pl, conv, {"request_id": "REQ-1", "correlation_id": "COR-1", "event_id": "EVT-1"})
    _write_payload(conv, pl)
    snap = correlation_snapshot(_payload(conv), conv)
    assert snap["request_id"] == "REQ-1"
    assert snap["workflow_id"].startswith("WF-")
    assert snap["conversation_id"] == "CONV_X"

    req = handshake(
        source_agent="orchestrator",
        target_agent="account_filter",
        event_type="RESOLVE_IDENTITY",
        workflow_id=snap["workflow_id"],
        request_id="REQ-1",
    )
    resp = handshake_response(req, source_agent="account_filter", result_type="IDENTITY_CONTEXT", workflow_state="IDENTITY_RESOLVED")
    assert resp["success"] and resp["request_id"] == "REQ-1"

    commit_workflow_state(db, conv, "IDENTITY_RESOLVED", source_agent="orchestrator")
    assert _payload(conv).get("master_workflow_state") == "IDENTITY_RESOLVED"
    assert ORCHESTRATOR_VERSION.startswith("four-agent-")
    print("OK orchestrator correlation")


def test_orchestrator_handle_message_chain():
    db = _session()
    conv = AiConversation(mobile="9888777666", conversation_id="CONV_ORCH", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    reply = handle_message(db, conv, "Tata 1618 bechna hai 2023 Indore 23 lakh")
    assert isinstance(reply, str) and len(reply) > 0
    pl = _payload(conv)
    assert pl.get("request_id")
    assert pl.get("workflow_id")
    assert pl.get("master_workflow_state")
    assert (pl.get("intent") or "").upper() == "SELL" or pl.get("brand") or pl.get("rm_state")
    print("OK orchestrator handle_message chain")


if __name__ == "__main__":
    test_phone_normalize()
    test_account_filter_types()
    test_whatsapp_collect_and_remote()
    test_orchestrator_correlation_and_handshake()
    test_orchestrator_handle_message_chain()
    print("ALL OK")
