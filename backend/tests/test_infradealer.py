"""InfraDealer integration unit tests."""

import json
import uuid

import pytest

from app.infradealer.crypto import encrypt_secret, decrypt_secret, mask_secret, short_key
from app.infradealer.events import classify_response, should_retry, load_event_flags, event_enabled
from app.infradealer.service import InfraDealerIntegrationService


def test_encrypt_roundtrip():
    plain = "secret-key-12345"
    enc = encrypt_secret(plain)
    assert enc.startswith("enc:")
    assert decrypt_secret(enc) == plain
    assert mask_secret(plain).endswith("2345")
    assert short_key("idk_9ea6ae049609ca89648ba09d1b79b6922e2c7ca3d81177f4") == "idk_9ea6…77f4"


def test_classify_response():
    assert classify_response(200, {"success": True}) == "SUCCESS"
    assert classify_response(404, {"code": "ACCOUNT_NOT_FOUND"}) == "BUSINESS_ERROR"
    assert classify_response(404, {}) == "BUSINESS_ERROR"
    assert classify_response(401, {}) == "AUTH_ERROR"
    assert classify_response(503, {}) == "SERVER_ERROR"
    assert classify_response(0, {}, "timeout") == "NETWORK_ERROR"


def test_year_parse():
    from app.infradealer.payloads import _year
    assert _year("2018") == 2018
    assert _year("18 model") is None
    assert _year("year 2018 ke") == 2018
    assert _year("abcd") is None


def test_otp_redact(db_session):
    svc = InfraDealerIntegrationService(db_session)
    out = svc._redact({"otp": "123456", "phone": "+9198", "nested": {"otp": "000000"}})
    assert out["otp"] == "[REDACTED]"
    assert out["nested"]["otp"] == "[REDACTED]"
    assert out["phone"] == "+9198"


def test_should_retry():
    assert should_retry("NETWORK_ERROR", 0) is True
    assert should_retry("SERVER_ERROR", 503) is True
    assert should_retry("AUTH_ERROR", 401) is False
    assert should_retry("BUSINESS_ERROR", 400, "OTP_INVALID") is False


def test_event_flags():
    flags = load_event_flags('{"listing_push": false}')
    assert flags["listing_push"] is False
    assert flags["account_check"] is True
    assert event_enabled(flags, "listing_push") is False
    assert event_enabled(flags, "account_check") is True


def test_endpoint_urls():
    from app.infradealer.events import endpoint_url, normalize_base_url
    base = "https://api.infradealer.com/api/v1/webhook"
    assert normalize_base_url("https://api.infradealer.com") == base
    assert endpoint_url(base, "account.check") == base + "/account/check"
    assert endpoint_url(base, "connection.test") == base + "/test"
    assert endpoint_url(base, "listing.push") == base + "/listing/push"
    assert endpoint_url(base, "status") == base + "/status"


def test_service_public_config(db_session):
    svc = InfraDealerIntegrationService(db_session)
    cfg = svc.public_config()
    assert "integration_id" in cfg
    assert "callback_url" in cfg
    assert "stats" in cfg


def test_enqueue_skips_without_url(db_session):
    svc = InfraDealerIntegrationService(db_session)
    row = svc.get_or_create_config()
    row.base_url = ""
    db_session.commit()
    item = svc.enqueue("ACCOUNT_CHECK", {"request_id": str(uuid.uuid4()), "event": "account.check"})
    assert item is None


def test_callback_idempotent(db_session):
    svc = InfraDealerIntegrationService(db_session)
    payload = {
        "callback_id": "cb-test-1",
        "event": "listing.posted",
        "request_id": str(uuid.uuid4()),
        "listing": {"listing_id": "L1", "status": "POSTED"},
    }
    r1 = svc.handle_callback(payload)
    db_session.commit()
    r2 = svc.handle_callback(payload)
    db_session.commit()
    assert r1["ok"] is True
    assert r2.get("duplicate") is True


def test_callback_event_aliases_and_listing_fields():
    from app.infradealer.events import listing_public_url, listing_reject_reason, normalize_callback_event

    assert normalize_callback_event({"event": "LISTING_POSTED"}) == "listing.posted"
    assert normalize_callback_event({"code": "LISTING_REJECTED"}) == "listing.rejected"
    assert normalize_callback_event({"listing": {"status": "POSTED"}}) == "listing.posted"
    assert normalize_callback_event({"listing": {"status": "rejected"}}) == "listing.rejected"
    assert listing_public_url({"listing": {"url": "https://infradealer.com/listing/abc"}}) == "https://infradealer.com/listings/abc"
    assert listing_public_url({}, "L99").endswith("/listings/L99")
    assert listing_reject_reason({"reason": "Photos blurry, resend cabin shot"}) == "Photos blurry, resend cabin shot"


def test_listing_push_idempotent_request_id(db_session):
    from app.infradealer.service import listing_push_request_id

    assert listing_push_request_id(42) == "listing-draft-42"
    assert listing_push_request_id(42, rejected=True) != listing_push_request_id(42)


def test_post_ad_card_mapping():
    from app.ai.schema import listing_title
    from app.infradealer.payloads import (
        POST_AD_CATEGORY_LABEL,
        map_post_ad_category,
        seller_contact_digits,
        strip_contact_from_text,
    )

    assert map_post_ad_category("JCB") == "jcb"
    assert POST_AD_CATEGORY_LABEL["jcb"] == "JCB / Backhoe Loaders"
    assert map_post_ad_category("Tipper") == "dumpers"
    assert map_post_ad_category("Poclain") == "excavator"
    assert listing_title({"brand": "JCB", "model": "3DX Super", "category": "Excavator"}) == "JCB 3DX Super Excavator"
    assert "9876543210" not in strip_contact_from_text("Cabin theek hai 9876543210 photos bhejunga")
    assert seller_contact_digits("+919876543210") == "9876543210"


def test_office_listing_uses_chat_shared_phone_not_account(db_session):
    """Office verified WA line identifies the account; chat-shared mobile is seller_contact."""
    from app.infradealer.payloads import build_listing_payload
    from app.models import AiConversation, AiListingDraft, Chat

    office_mobile = "8224000829"
    seller_mobile = "9876543210"
    conv = AiConversation(
        conversation_id=f"CONV_{office_mobile}",
        mobile=office_mobile,
        language="hinglish",
        customer_name="Office Desk",
        payload_json=json.dumps({"account_type": "OFFICE", "brand": "Tata", "model": "407", "expected_price": "5 lakh", "state": "Madhya Pradesh"}),
    )
    db_session.add(conv)
    db_session.flush()
    draft = AiListingDraft(
        conversation_id=conv.id,
        mobile=office_mobile,
        status="READY",
        title="Tata 407",
    )
    db_session.add(draft)
    db_session.add(
        Chat(
            conversation_id=conv.conversation_id,
            direction="inbound",
            from_mobile=office_mobile,
            body=f"Seller number {seller_mobile} hai, photos bhej raha hoon",
        )
    )
    db_session.flush()

    payload = json.loads(conv.payload_json)
    body = build_listing_payload(db_session, conv, draft, payload, "req-office-1", infradealer_user_id="99")
    assert body["customer"]["phone"].endswith(office_mobile)
    assert body["customer"]["seller_contact"] == seller_mobile
    assert body["listing"]["seller_contact"] == seller_mobile
    assert body["listing"]["contact_number"] == seller_mobile
    assert office_mobile not in (
        body["listing"].get("seller_contact"),
        body["customer"].get("seller_contact"),
    )


def test_listing_push_status_auto_publish(monkeypatch):
    from app.infradealer import payloads

    monkeypatch.setattr(payloads.settings, "infradealer_auto_publish", True)
    assert payloads.listing_push_status() == "LIVE"
    monkeypatch.setattr(payloads.settings, "infradealer_auto_publish", False)
    assert payloads.listing_push_status() == "PENDING_REVIEW"


def test_rejected_callback_sets_draft(db_session):
    from app.models import AiConversation, AiListingDraft, InfraDealerRequest

    conv = AiConversation(conversation_id="CONV_9999999999", mobile="9999999999", language="hinglish")
    db_session.add(conv)
    db_session.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile="9999999999", status="PENDING_REVIEW", title="Tata 1613")
    db_session.add(draft)
    db_session.flush()
    rid = str(uuid.uuid4())
    db_session.add(
        InfraDealerRequest(
            request_id=rid,
            event_type="LISTING_PUSH",
            mobile="9999999999",
            conversation_id=conv.id,
            draft_id=draft.id,
            status="SUCCESS",
        )
    )
    db_session.commit()
    svc = InfraDealerIntegrationService(db_session)
    svc.handle_callback(
        {
            "event": "LISTING_REJECTED",
            "request_id": rid,
            "listing": {"listing_id": "L9"},
            "reason": "Cabin photo missing",
        }
    )
    db_session.refresh(draft)
    assert draft.status == "REJECTED"
