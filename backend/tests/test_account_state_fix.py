"""Regression: account state get-or-create must not UniqueViolation on check+apply."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, InfraDealerAccountState, InfraDealerOutbox
from app.infradealer.service import InfraDealerIntegrationService, account_mobile


def _db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_account_mobile_norm():
    assert account_mobile("918224000826") == "8224000826"
    assert account_mobile("8224000826") == "8224000826"
    print("OK account_mobile")


def test_get_or_create_idempotent():
    db = _db()
    svc = InfraDealerIntegrationService(db)
    a = svc.get_or_create_account_state("9189893313715", conversation_id=1, account_status="CHECKING")
    b = svc.get_or_create_account_state("9893313715", conversation_id=1, account_status="CHECKING")
    assert a.id == b.id
    assert a.mobile == "9893313715"
    assert db.query(InfraDealerAccountState).count() == 1
    print("OK get_or_create idempotent")


def test_check_then_apply_no_unique_violation():
    """Mirrors confirm→start_account: check_account add + process apply add."""
    db = _db()
    conv = AiConversation(mobile="9893313715", conversation_id="CONV_T", state="CONFIRMED", payload_json="{}")
    db.add(conv)
    db.flush()

    svc = InfraDealerIntegrationService(db)
    # Simulate check_account create without HTTP
    st = svc.get_or_create_account_state(conv.mobile, conversation_id=conv.id, account_status="CHECKING")
    item = InfraDealerOutbox(
        event_type="ACCOUNT_CHECK",
        status="PENDING",
        mobile=conv.mobile,
        conversation_id=conv.id,
        payload_json="{}",
        request_id="test-rid",
    )
    db.add(item)
    db.flush()
    # Apply path used to create a second row → UniqueViolation
    svc._apply_business_result(
        item,
        {"account_found": False},
        "ACCOUNT_NOT_FOUND",
        "BUSINESS",
        http_status=404,
    )
    db.flush()
    assert db.query(InfraDealerAccountState).count() == 1
    assert st.account_status == "NOT_FOUND"
    print("OK check+apply single row")


if __name__ == "__main__":
    test_account_mobile_norm()
    test_get_or_create_idempotent()
    test_check_then_apply_no_unique_violation()
    print("ALL OK")
