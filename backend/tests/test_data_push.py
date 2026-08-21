"""data_push agent — gates, idempotency, admin events, URL safety."""

from __future__ import annotations

import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation
from app.ai.data_push import (
    AGENT_VERSION,
    STATUS_RANK,
    _can_transition,
    calculate_payload_hash,
    create_notification_event,
    generate_idempotency_key,
    get_submission_status,
    listing_open_url,
    parse_admin_response,
    process_admin_event,
    public_listing_url,
    push_listing,
    sign_payload,
    validate_confirmation,
    validate_live_url,
    validate_submission,
    validate_version,
    verify_admin_event,
    wa_open_token,
)
from app.ai.tools import _write_payload


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_public_listing_url():
    assert public_listing_url("104") == "https://infradealer.com/listings/104"
    assert public_listing_url("") == ""
    print("OK public_listing_url")


def test_wa_open_link_has_token():
    url = listing_open_url("104", mobile="9876543210")
    assert url.startswith("https://infradealer.com/listings/104")
    assert "from=whatsapp" in url
    assert "wa=9876543210" in url
    assert "t=" in url
    assert wa_open_token("9876543210", "104")
    remote = listing_open_url(
        "104",
        mobile="9876543210",
        payload={"listing": {"url": "https://infradealer.com/listing/104"}},
    )
    assert "/listings/104" in remote
    print("OK wa open link")


def test_validate_live_url():
    assert validate_live_url("https://infradealer.com/listings/1")
    assert validate_live_url("https://www.infradealer.com/listings/1")
    assert not validate_live_url("http://infradealer.com/listings/1")
    assert not validate_live_url("https://evil.com/phish")
    assert not validate_live_url("")
    print("OK validate_live_url")


def test_confirmation_gate():
    ok, reason = validate_confirmation({})
    assert not ok and reason == "CONFIRMATION_OR_VALIDATION_FAILED"
    ok, _ = validate_confirmation({"customer_confirmed": True})
    assert ok
    ok, _ = validate_confirmation({"confirmation": {"confirmed": True}})
    assert ok
    print("OK confirmation gate")


def test_stale_confirmation():
    ok, reason = validate_version({"draft_version": 7, "confirmed_version": 6})
    assert not ok and reason == "STALE_CONFIRMATION"
    ok, _ = validate_version({"draft_version": 7, "confirmed_version": 7})
    assert ok
    print("OK stale confirmation")


def test_submission_blocked_missing_data():
    payload = {
        "customer_confirmed": True,
        "confirmed_version": 1,
        "draft_version": 1,
        "intent": "SELL",
    }
    ok, reason = validate_submission(payload)
    assert not ok
    assert reason in {"MISSING_REQUIRED_DATA", "CONFIRMATION_OR_VALIDATION_FAILED"}
    print("OK submission blocked missing data")


def test_valid_submission_gate():
    payload = {
        "customer_confirmed": True,
        "confirmed_version": 1,
        "draft_version": 1,
        "intent": "SELL",
        "category": "truck",
        "brand": "TATA",
        "model": "1618",
        "expected_price": "23 lakh",
        "state": "Madhya Pradesh",
        "city": "Indore",
        "filter_result": {"readiness": "READY_FOR_CONFIRMATION", "conflicts": []},
    }
    ok, reason = validate_submission(payload)
    assert ok, reason
    print("OK valid submission gate")


def test_idempotency_and_hash():
    key = generate_idempotency_key(50129, 7)
    assert key == "INF-DRAFT-50129-V7"
    h1 = calculate_payload_hash({"a": 1, "b": 2})
    h2 = calculate_payload_hash({"b": 2, "a": 1})
    assert h1 == h2
    assert len(h1) == 64
    print("OK idempotency + hash")


def test_parse_admin_response_codes():
    ack = parse_admin_response({"success": True, "listing_id": "INF-1", "status": "UNDER_REVIEW"}, 201)
    assert ack["success"] and ack["status"] == "UNDER_REVIEW"
    perm = parse_admin_response({"success": False, "code": "INVALID_CATEGORY"}, 400)
    assert perm["permanent"] and not perm["retry"]
    retry = parse_admin_response({}, 503)
    assert retry["retry"] and not retry["permanent"]
    print("OK parse_admin_response")


def test_no_state_downgrade():
    assert _can_transition("UNDER_REVIEW", "APPROVED")
    assert _can_transition("APPROVED", "LIVE")
    assert not _can_transition("LIVE", "UNDER_REVIEW")
    assert not _can_transition("APPROVED", "UNDER_REVIEW")
    assert STATUS_RANK["LIVE"] > STATUS_RANK["REJECTED"]
    print("OK no state downgrade")


def test_hmac_sign_and_verify():
    body = '{"event":"LISTING_SUBMIT"}'
    ok, _reason = verify_admin_event({}, body)
    assert ok
    headers = sign_payload(body, timestamp="1700000000", secret="test-secret")
    assert headers["X-InfraDealer-Signature"]
    expected = hmac.new(
        b"test-secret",
        b"1700000000." + body.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-InfraDealer-Signature"] == expected
    print("OK hmac sign")


def test_notification_event_rejects_bad_url():
    note = create_notification_event(
        notification_type="LISTING_APPROVED",
        listing_id="INF-1",
        status="LIVE",
        live_url="https://evil.com/x",
    )
    assert note["event"] == "USER_NOTIFICATION_REQUIRED"
    assert note["live_url"] == ""
    note2 = create_notification_event(
        notification_type="LISTING_LIVE",
        live_url="https://infradealer.com/listings/1",
    )
    assert note2["live_url"].startswith("https://infradealer.com")
    print("OK notification URL safety")


def test_agent_version():
    assert AGENT_VERSION.startswith("data-push-")
    print("OK agent version")


def test_push_blocked_without_confirm():
    db = _session()
    conv = AiConversation(mobile="9000000001", conversation_id="CONV_PUSH1", state="DRAFT", payload_json="{}")
    db.add(conv)
    db.flush()
    result = push_listing(db, conv)
    assert result.ok is False
    assert result.status == "SUBMISSION_BLOCKED"
    print("OK push blocked without confirm")


def test_admin_approval_requires_listing_id():
    db = _session()
    conv = AiConversation(mobile="9000000002", conversation_id="CONV_PUSH2", state="READY", payload_json="{}")
    db.add(conv)
    db.flush()
    out = process_admin_event(db, conv, {"event": "LISTING_APPROVED", "event_id": "E1"})
    assert out.get("ok") is False
    assert out.get("reason_code") == "INVALID_ADMIN_EVENT"
    print("OK approval requires listing_id")


def test_admin_event_idempotent_and_no_downgrade():
    db = _session()
    conv = AiConversation(mobile="9000000003", conversation_id="CONV_PUSH3", state="READY", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, {"listing_status": "UNDER_REVIEW"})

    first = process_admin_event(
        db,
        conv,
        {
            "event": "LISTING_APPROVED",
            "event_id": "ADM-EVT-1",
            "listing_id": "INF-LIST-100",
            "live_url": "https://infradealer.com/listings/100",
            "status": "APPROVED",
        },
    )
    assert first.get("ok") is True
    status1 = get_submission_status(db, conv)["status"]
    assert status1 in {"APPROVED", "LIVE"}

    dup = process_admin_event(
        db,
        conv,
        {
            "event": "LISTING_APPROVED",
            "event_id": "ADM-EVT-1",
            "listing_id": "INF-LIST-100",
            "live_url": "https://infradealer.com/listings/100",
        },
    )
    assert dup.get("duplicate") is True

    stale = process_admin_event(
        db,
        conv,
        {
            "event": "LISTING_UNDER_REVIEW",
            "event_id": "ADM-EVT-2",
            "listing_id": "INF-LIST-100",
        },
    )
    assert stale.get("ok") is True
    final = get_submission_status(db, conv)["status"]
    assert STATUS_RANK.get(final.upper(), 0) >= STATUS_RANK.get(status1.upper(), 0)
    print("OK admin event idempotent + no downgrade")


if __name__ == "__main__":
    test_public_listing_url()
    test_wa_open_link_has_token()
    test_validate_live_url()
    test_confirmation_gate()
    test_stale_confirmation()
    test_submission_blocked_missing_data()
    test_valid_submission_gate()
    test_idempotency_and_hash()
    test_parse_admin_response_codes()
    test_no_state_downgrade()
    test_hmac_sign_and_verify()
    test_notification_event_rejects_bad_url()
    test_agent_version()
    test_push_blocked_without_confirm()
    test_admin_approval_requires_listing_id()
    test_admin_event_idempotent_and_no_downgrade()
    print("ALL OK")
