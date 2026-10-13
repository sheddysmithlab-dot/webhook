"""Fixes for bugs found by the AI chat review (live chats)."""

from __future__ import annotations

import json

import pytest

from app.ai.data_filteration import _looks_like_place, extract_fields
from app.ai.engine import _sanitize_reply
from app.ai.voice import _upload_name
from app.models import AiConversation, AiListingDraft


@pytest.fixture()
def db(db_session):
    return db_session


def test_negated_live_sentence_is_kept_whole():
    raw = (
        "• Sir, Listing live hone ke liye pehle admin approve karega. Account: TOKEN plan mein hai\n"
        "• Kya aap naya listing banana chahte hain?"
    )
    assert _sanitize_reply(raw, posted=False) == raw


def test_false_live_claim_sentence_is_replaced():
    out = _sanitize_reply("Aapki listing InfraDealer par live ho gayi hai! Link jaldi milega.", posted=False)
    assert "live ho gayi" not in out
    assert "Link jaldi milega." in out
    assert "  " not in out
    assert "live ho gayi" in _sanitize_reply("Aapki listing live ho gayi hai!", posted=True)


def test_english_live_claim_is_replaced():
    assert "is now live" not in _sanitize_reply("Your listing is now live.", posted=False)


def test_paper_words_are_not_places():
    for word in ("Bima", "Bima hai", "Four wheeler", "permit chahiye", "running hai", "Call", "बीमा है"):
        assert not _looks_like_place(word), word
    for city in ("Hajipur", "New Delhi", "Chalisgaon", "Raipur", "पटना"):
        assert _looks_like_place(city), city


def test_tata_numeric_model_is_truck():
    out = extract_fields(["Tata 709 sfc"])
    assert (out.get("category"), out.get("brand"), out.get("model")) == ("Truck", "Tata", "709")
    assert extract_fields(["tata 407 2018 model"]).get("category") == "Truck"
    assert not extract_fields(["Eicher 485"]).get("category")
    assert not extract_fields(["Tata signa 2528"]).get("category")
    assert extract_fields(["Tata 1512 tipper"]).get("category") == "Tipper"


def test_voice_upload_gets_audio_extension():
    assert _upload_name("/data/media/91999_123.bin", "audio/ogg; codecs=opus") == "91999_123.ogg"
    assert _upload_name("/data/media/a.bin", "audio/mpeg") == "a.mp3"
    assert _upload_name("/data/media/a.m4a", "audio/ogg") == "a.m4a"


def test_card_clear_keeps_review_notified(db):
    from app.ai.cards import clear_card_chat_data
    from app.ai.tools import _payload, _write_payload

    conv = AiConversation(mobile="919000777001", conversation_id="CONV_9000777001", state="DONE")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="POSTED", card_id="CARD-001")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    pl = _payload(conv)
    pl.update({"listing_status": "POSTED", "listing_review_notified": True, "active_card_id": "CARD-001"})
    _write_payload(conv, pl)

    clear_card_chat_data(db, conv, draft)
    assert _payload(conv)["listing_review_notified"] is True
    assert json.loads(conv.payload_json or "{}").get("listing_status") == "POSTED"


def test_image_detail_request_sends_report_through_polish(db, monkeypatch):
    from app.ai import runner
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777003", conversation_id="CONV_9000777003", state="NEW")
    db.add(conv)
    db.flush()
    db.add(AiMedia(
        conversation_id=conv.id, kind="image", extract_kind="vehicle_fields",
        extracted_text=json.dumps({"category": "Tipper", "brand": "TATA", "colour": "White"}),
    ))
    db.commit()
    polished = []
    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "Brand bata dijiye.")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (polished.append(d) or d, "polished"))
    out = runner.process_inbound(db, mobile="919000777003", text="Full image read karo har ek detail", send=False)
    assert "Photo se mili jankari" in out and "Brand: Tata" in out and "Rang: White" in out
    assert polished and polished[0] in out


def test_untranscribed_voice_note_asks_to_type(db, monkeypatch):
    from app.ai import runner

    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "Kaunsi category hai?")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (d, "off"))
    out = runner.process_inbound(
        db, mobile="919000777002", text="[voice note]", wamid="",
        media={"kind": "audio", "id": "", "mime": "audio/ogg"}, send=False,
    )
    assert out and "voice note" in out.lower() and "category" not in out.lower()


def test_hindi_brand_and_trailer_are_read():
    out = extract_fields(["Brand name अशोक लेलैंड 5525 टेलर 18 चक्का\n2024 मॉडल"])
    assert (out.get("brand"), out.get("model"), out.get("year")) == ("Ashok Leyland", "5525", 2024)
    assert out.get("category") == "Truck"
    assert not extract_fields(["अशोक लेलैंड 5525 टेलर 18 चक्का"]).get("year")
    assert not extract_fields(["Tata 3118 10 tyre"]).get("year")
    assert extract_fields(["Tata 3118 18 model"]).get("year") == 2018
    assert extract_fields(["Brand name Tata"]).get("brand") == "Tata"
    assert extract_fields(["आयशर 2019"]).get("brand") == "Eicher"


def _office_card(db, monkeypatch, mobile, **fields):
    from app.ai import office_mode
    from app.ai.tools import _payload, _write_payload

    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="COLLECTING")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="DRAFT", card_id="CARD-125")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    pl = _payload(conv)
    pl.update({"intent": "SELL", **fields})
    _write_payload(conv, pl)
    monkeypatch.setattr(office_mode, "is_office_session", lambda db_, conv_: True)
    return conv


def test_brand_fix_on_same_model_keeps_the_card(db, monkeypatch):
    from app.ai.confirm import maybe_start_new_card

    conv = _office_card(db, monkeypatch, "919000777005", brand="Name", model="5525", category="Truck")
    assert not maybe_start_new_card(db, conv, {"brand": "Ashok Leyland", "model": "5525"})
    conv = _office_card(db, monkeypatch, "919000777015", brand="Tata", model="2518", category="Truck")
    assert maybe_start_new_card(db, conv, {"brand": "Ashok Leyland", "model": "2518"})


def _confirm_card(db, monkeypatch):
    from app.ai import engine as eng
    from app.ai.confirm import send_summary
    from app.ai.tools import _payload, _write_payload
    from app.config import settings

    monkeypatch.setattr(settings, "ai_prompt_chat", True)
    conv = AiConversation(mobile="919000777006", conversation_id="CONV_9000777006", state="COLLECTING")
    db.add(conv)
    db.flush()
    pl = _payload(conv)
    pl.update({
        "intent": "SELL", "category": "Excavator", "brand": "JCB", "model": "3DX", "year": 2019,
        "operating_hours": "6000", "expected_price": 2200000, "state": "Bihar", "city": "Patna",
        "photos_complete": True, "photo_count": 2,
        "account_eligibility": "ELIGIBLE", "account_can_post": True, "account_onboarded": True,
    })
    _write_payload(conv, pl)
    send_summary(db, conv, "hinglish")
    monkeypatch.setattr(eng, "prepare_prompt_state", lambda db_, conv_, text, media_note: _payload(conv_))
    monkeypatch.setattr(eng, "handle_account_info", lambda *a, **k: None)
    monkeypatch.setattr(eng, "needs_account_gate", lambda payload: False)
    monkeypatch.setattr(eng, "prompt_chat_enabled", lambda db_: True)
    monkeypatch.setattr(eng, "llm_configured", lambda db_: True)
    monkeypatch.setattr(eng, "llm_reply", lambda *a, **k: pytest.fail("LLM must not run"))
    return eng, conv


def test_open_confirm_card_skip_and_photo_ask_for_yes(db, monkeypatch):
    eng, conv = _confirm_card(db, monkeypatch)
    assert "Haan" in eng.prompt_chat_turn(db, conv, "Skip")
    out = eng.prompt_chat_turn(db, conv, "[photo]", "image id=9 caption='' saved=True")
    assert "mil gaya" in out and "Haan" in out


def test_open_confirm_card_reshows_after_change(db, monkeypatch):
    from app.ai.tools import _payload, _write_payload

    eng, conv = _confirm_card(db, monkeypatch)
    pl = _payload(conv)
    pl["year"] = 2020
    _write_payload(conv, pl)
    out = eng.prompt_chat_turn(db, conv, "Model 2020")
    assert "2020" in out and _payload(conv)["awaiting_confirm"]


def test_inbound_lock_commits_before_release(db, monkeypatch):
    from contextlib import contextmanager

    from app.ai import runner

    events = []

    @contextmanager
    def fake_lock(mobile):
        yield True
        events.append("released")

    monkeypatch.setattr(runner, "mobile_lock", fake_lock)
    monkeypatch.setattr(db, "commit", lambda: events.append("commit"))
    with runner._inbound_lock("919000777007", 1, db):
        pass
    assert events == ["commit", "released"]


def test_photo_report_covers_only_this_vehicle(db):
    from datetime import datetime, timedelta

    from app.ai import vision
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777008", conversation_id="CONV_9000777008", state="NEW")
    db.add(conv)
    db.flush()
    now = datetime.utcnow()

    def media(minutes_ago, data):
        row = AiMedia(conversation_id=conv.id, draft_id=166, kind="image", extract_kind="vehicle_fields",
                      extracted_text=json.dumps(data), created_at=now - timedelta(minutes=minutes_ago))
        db.add(row)
        db.flush()
        return row

    media(40, {"brand": "TATA", "registration_number": "BR01AB1234", "body_type": "Tipper body"})
    jcb = media(1, {"brand": "JCB", "model": "3DX", "colour": "Yellow", "details": ["Engine: N/A", "Bucket ok"]})
    media(0, {"brand": "TATA", "body_type": "Half body"})
    other = media(0, {"brand": "JCB", "condition": "Good", "axles": "N/A"})

    report = vision.image_report(db, conv, "hinglish", media_ids=[jcb.id])
    assert "JCB" in report and "Rang: Yellow" in report and "Bucket ok" in report
    assert "BR01AB1234" not in report and "Tipper body" not in report and "N/A" not in report

    asked = vision.image_report(db, conv, "hinglish")
    assert "JCB" in asked and "Haalat: Good" in asked
    assert "Half body" not in asked and "BR01AB1234" not in asked and "N/A" not in asked
    assert other.id
