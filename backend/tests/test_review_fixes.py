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


def test_update_on_open_card_shows_full_card_with_photo_details(db, monkeypatch):
    from app.ai.tools import _payload, _write_payload
    from app.models import AiMedia

    eng, conv = _confirm_card(db, monkeypatch)
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="DRAFT", card_id="CARD-006")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    db.add(AiMedia(
        conversation_id=conv.id, draft_id=draft.id, kind="image", extract_kind="vehicle_fields",
        extracted_text=json.dumps({"brand": "JCB", "colour": "Yellow", "tyres": "4",
                                   "registration_number": "BR01AB1234", "details": ["Bucket in good shape"]}),
    ))
    pl = _payload(conv)
    pl.update({"year": 2020, "active_card_id": "CARD-006"})
    _write_payload(conv, pl)
    out = eng.prompt_chat_turn(db, conv, "Model 2020")
    for part in ("Listing details* — CARD-006", "Brand: JCB", "Model: 3DX", "Year: 2020 ✏️",
                 "Rang: Yellow", "Tyre: 4", "Gaadi number: BR01AB1234", "Haan"):
        assert part in out, part
    assert "Bucket in good shape" not in out
    assert "Brand: JCB ✏️" not in out
    assert _payload(conv)["summary_json"]["year"] == 2020


def test_card_ledger_restores_lost_fields_and_keeps_versions(db, monkeypatch, fake_redis):
    from app.ai import card_ledger
    from app.ai.tools import _payload, _write_payload

    fake = fake_redis
    conv = _office_card(db, monkeypatch, "919000777009", brand="Tata", model="1418", category="Truck",
                        year=2020, city="Pune", expected_price=1500000)
    assert card_ledger.sync(conv) == []
    assert card_ledger.load(conv)["fields"]["year"] == 2020

    pl = _payload(conv)
    pl.update({"year": None, "expected_price": None, "model": "1618"})
    _write_payload(conv, pl)
    assert sorted(card_ledger.sync(conv)) == ["expected_price", "year"]
    pl = _payload(conv)
    assert (pl["year"], pl["expected_price"], pl["model"]) == (2020, 1500000, "1618")
    assert card_ledger.load(conv)["v"] == 2

    pl["customer_confirmed"] = True
    pl["city"] = None
    _write_payload(conv, pl)
    assert card_ledger.sync(conv) == []
    final = card_ledger.load(conv)
    assert final["final"] and final["fields"]["model"] == "1618" and "city" not in final["fields"]
    assert len(fake.lists[next(iter(fake.lists))]) == 3


def test_card_ledger_skips_junk_places_and_buy_intent(db, monkeypatch, fake_redis):
    from app.ai import card_ledger
    from app.ai.tools import _payload, _write_payload

    conv = _office_card(db, monkeypatch, "919000777010", brand="Eicher", category="Bus", city="haan")
    card_ledger.sync(conv)
    assert "city" not in card_ledger.load(conv)["fields"]
    pl = _payload(conv)
    pl["intent"] = "BUY"
    _write_payload(conv, pl)
    conv.intent = "BUY"
    card_ledger.sync(conv)
    assert card_ledger.load(conv) == {}


_BUS_DUMP = "BRAND name Force traveler\ncategory bus\n2018 model\nDiscription 18 seter  school passing\nAll paper complete"


def test_fast_correct_keeps_lines_and_bus_dump_is_read():
    from app.ai.corrector import _fast_correct

    fixed = _fast_correct(_BUS_DUMP)
    assert fixed.count("\n") == 4 and "Discription 18 seter school passing" in fixed
    out = extract_fields([fixed])
    assert (out["brand"], out["model"], out["category"], out["year"]) == ("Force", "Traveller", "Bus", 2018)
    assert out["description"] == "18 seter school passing, All paper complete"
    assert extract_fields(["BRAND name Force traveler"])["brand"] == "Force"
    assert not extract_fields(["brand new hai gaadi"]).get("brand")


def test_roman_text_after_hindi_switches_to_hinglish():
    from app.ai.i18n import pick_language

    assert pick_language("Image check kr ke detail nikalo", "hi") == "hinglish"
    assert pick_language("2018", "hi") == "hi"
    assert pick_language("फोटो पढ़ो", "hinglish") == "hi"


def _idle_office_conv(db, mobile):
    from datetime import datetime, timedelta, timezone

    from app.ai.tools import _payload, _write_payload
    from app.models import AiMedia

    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="SELL_DATA_COLLECTION")
    db.add(conv)
    db.flush()
    old = AiListingDraft(conversation_id=conv.id, mobile=mobile[-10:], status="COLLECTING", card_id="CARD-127")
    db.add(old)
    db.flush()
    conv.draft_id = old.id
    for i in range(5):
        db.add(AiMedia(conversation_id=conv.id, draft_id=old.id, kind="image", local_path=f"/x/{i}.jpg",
                       meta_media_id=f"old{i}"))
    db.add(AiMedia(conversation_id=conv.id, draft_id=old.id, kind="image", extract_kind="vehicle_fields",
                   extracted_text=json.dumps({"brand": "ASHOK LEYLAND", "registration_number": "BR28GB299"})))
    pl = _payload(conv)
    pl.update({"intent": "SELL", "brand": "Ashok Leyland", "model": "5525", "active_card_id": "CARD-127",
               "last_user_message_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    _write_payload(conv, pl)
    db.commit()
    return conv, old


def test_photos_after_idle_start_a_new_card_and_get_read(db, monkeypatch):
    from app.ai import runner, vision
    from app.models import AiMedia

    conv, old = _idle_office_conv(db, "919000777011")
    monkeypatch.setattr(runner, "download_whatsapp_media", lambda meta, mid, d, name: (f"/x/{mid}.jpg", "image/jpeg"))
    monkeypatch.setattr(runner, "_newer_inbound_exists", lambda *a: False)
    monkeypatch.setattr("app.ai.media_config.vision_enabled", lambda db_: True)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image", lambda *a, **k: json.dumps(
        {"category": "Bus", "brand": "Force", "model": "Traveller", "colour": "Yellow"}))
    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "Photo mil gaya.")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (d, "off"))
    out = runner.process_inbound(db, mobile="919000777011", text="[photo]", wamid="wa-new-1",
                                 media={"id": "new1", "kind": "image", "mime": "image/jpeg"}, send=False)
    row = db.query(AiMedia).filter(AiMedia.meta_media_id == "new1").one()
    assert row.draft_id != old.id and row.extract_kind == "vehicle_fields"
    assert "Force" in out and "BR28GB299" not in out


def test_new_photo_set_on_full_card_replaces_old_set(db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.ai import runner, vision
    from app.ai.engine import _media_ack
    from app.ai.tools import _payload, _write_payload
    from app.models import AiMedia

    conv, card = _idle_office_conv(db, "919000777016")
    pl = _payload(conv)
    pl["last_user_message_at"] = datetime.now(timezone.utc).isoformat()
    pl["media_ids"] = [r.id for r in db.query(AiMedia).filter(AiMedia.draft_id == card.id, AiMedia.local_path != "")]
    _write_payload(conv, pl)
    db.query(AiMedia).filter(AiMedia.draft_id == card.id).update(
        {"created_at": datetime.utcnow() - timedelta(minutes=10)}, synchronize_session=False)
    db.commit()
    monkeypatch.setattr(runner, "download_whatsapp_media", lambda meta, mid, d, name: (f"/x/{mid}.jpg", "image/jpeg"))
    monkeypatch.setattr(runner, "_newer_inbound_exists", lambda *a: False)
    monkeypatch.setattr("app.ai.media_config.vision_enabled", lambda db_: True)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image", lambda *a, **k: json.dumps(
        {"category": "Truck", "brand": "Ashok Leyland", "model": "5525", "colour": "Orange"}))
    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "Photo mil gaya.")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (d, "off"))

    out = runner.process_inbound(db, mobile="919000777016", text="[photo]", wamid="wa-set2-1",
                                 media={"id": "set2a", "kind": "image", "mime": "image/jpeg"}, send=False)
    row = db.query(AiMedia).filter(AiMedia.meta_media_id == "set2a").one()
    assert conv.draft_id == card.id and row.draft_id == card.id and row.extract_kind == "vehicle_fields"
    assert db.query(AiMedia).filter(AiMedia.draft_id == card.id, AiMedia.kind == "image").count() == 1
    assert _payload(conv)["media_ids"] == [row.id]
    assert "Orange" in out and "BR28GB299" not in out

    for i in range(4):
        runner.attach_media(db, conv, f"wa-set2-{i + 2}", {"id": f"set2{i}", "kind": "image"})
    assert runner.attach_media(db, conv, "wa-set2-9", {"id": "set2x", "kind": "image"}) == "photo_rejected_max"
    assert "5 photo" in _media_ack("hinglish", "photo_rejected_max")


def test_text_after_photo_burst_reads_left_photos_and_sends_dropped_report(db, monkeypatch):
    from datetime import datetime, timezone

    from app.ai import runner, vision
    from app.ai.tools import _payload, _write_payload
    from app.models import AiEvent, AiMedia, Chat

    conv, card = _idle_office_conv(db, "919000777017")
    db.query(AiMedia).filter(AiMedia.draft_id == card.id).update({"draft_id": None}, synchronize_session=False)
    pl = _payload(conv)
    pl["last_user_message_at"] = datetime.now(timezone.utc).isoformat()
    _write_payload(conv, pl)
    caption = AiMedia(conversation_id=conv.id, draft_id=card.id, kind="image", local_path="/x/c.jpg",
                      extract_kind="vehicle_fields",
                      extracted_text=json.dumps({"brand": "Tata", "registration_number": "UP51AT3987"}))
    unread = [AiMedia(conversation_id=conv.id, draft_id=card.id, kind="image", local_path=f"/x/u{i}.jpg")
              for i in range(2)]
    db.add_all([caption, *unread])
    db.flush()
    # The caption photo's report went out on a reply that was dropped as stale.
    runner._queue_photo_report(conv, [caption.id])
    db.commit()
    monkeypatch.setattr("app.ai.media_config.vision_enabled", lambda db_: True)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image", lambda *a, **k: json.dumps(
        {"brand": "Tata", "colour": "Pink", "tyres": "14"}))
    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "Kya ye details sahi hain?")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (d, "off"))

    out = runner.process_inbound(db, mobile="919000777017", text="Price 20 lakh", wamid="wa-txt-1", send=False)
    assert all(r.extract_kind == "vehicle_fields" for r in unread)
    for part in ("Photo se mili jankari", "UP51AT3987", "Pink", "Tyre: 14", "Kya ye details sahi hain?"):
        assert part in out, part
    assert not _payload(conv).get("photo_report_pending")

    for i, w in enumerate(("wa-p1", "wa-p2")):
        db.add(Chat(conversation_id=conv.conversation_id, direction="inbound", from_mobile=conv.mobile, wamid=w,
                    body="[photo]"))
        db.flush()
    db.add(AiEvent(wamid="wa-p2", mobile=conv.mobile, event_type="inbound", detail="[photo]"))
    db.flush()
    assert not runner._newer_inbound_exists(db, "919000777017", "wa-p1")
    db.add(Chat(conversation_id=conv.conversation_id, direction="inbound", from_mobile=conv.mobile, wamid="wa-p3",
                body="[photo]"))
    db.flush()
    assert runner._newer_inbound_exists(db, "919000777017", "wa-p1")


def test_burst_report_sees_reads_without_autoflush(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai import vision
    from app.database import Base
    from app.models import AiMedia

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, autoflush=False)()
    conv = AiConversation(mobile="919000777014", conversation_id="CONV_9000777014", state="NEW")
    db.add(conv)
    db.flush()
    row = AiMedia(conversation_id=conv.id, kind="image", local_path="/x/a.jpg")
    db.add(row)
    db.flush()
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image", lambda *a, **k: json.dumps({"brand": "Force", "colour": "Yellow"}))
    _, ids = vision.fill_listing_from_burst(db, conv, row)
    assert "Rang: Yellow" in vision.image_report(db, conv, "hinglish", media_ids=ids)


def test_detail_request_reads_this_cards_photos_only(db, monkeypatch):
    from app.ai import runner, vision
    from app.models import AiMedia

    conv, old = _idle_office_conv(db, "919000777012")
    new = AiListingDraft(conversation_id=conv.id, mobile="9000777012", status="COLLECTING", card_id="CARD-128")
    db.add(new)
    db.flush()
    conv.draft_id = new.id
    db.add(AiMedia(conversation_id=conv.id, draft_id=new.id, kind="image", local_path="/x/bus.jpg"))
    from app.ai.tools import _payload, _write_payload

    pl = _payload(conv)
    pl.update({"brand": "Force", "model": None, "active_card_id": "CARD-128",
               "last_user_message_at": pl["last_user_message_at"].replace("2026", "2999")})
    _write_payload(conv, pl)
    db.commit()
    monkeypatch.setattr("app.ai.media_config.vision_enabled", lambda db_: True)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image", lambda *a, **k: json.dumps(
        {"category": "Bus", "brand": "Force", "model": "Traveller", "colour": "Yellow"}))
    monkeypatch.setattr(runner, "_ai_respond", lambda *a, **k: "ok")
    monkeypatch.setattr("app.ai.polish.polish_reply", lambda db, u, d, l: (d, "off"))
    out = runner.process_inbound(db, mobile="919000777012", text="Image check kr ke detail nikalo", send=False)
    assert "Brand: Force" in out and "Rang: Yellow" in out
    assert "Ashok" not in out and "BR28GB299" not in out


def test_listing_delete_request_is_logged_for_admin(db, monkeypatch):
    from app.ai import data_push
    from app.models import AiEvent

    conv = AiConversation(mobile="919000777013", conversation_id="CONV_9000777013", state="DONE")
    db.add(conv)
    db.flush()
    monkeypatch.setattr(data_push, "get_submission_status", lambda db_, conv_: {"status": "LIVE", "listing_id": "203"})
    out = data_push.handle_post_listing_query(db, conv, "Ye posting delete kr do ismein data missing hai", "hinglish")
    assert "admin team" in out
    ev = db.query(AiEvent).filter(AiEvent.event_type == "listing_delete_request").one()
    assert '"203"' in ev.detail


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


def test_website_payload_fills_fields_and_puts_the_rest_in_description(db):
    """After YES: every detail goes to its website field; the rest goes into the description."""
    from app.infradealer.payloads import build_listing_payload
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777009", conversation_id="CONV_9000777009", state="NEW")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="READY", title="Force Traveller")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    payload = {
        "intent": "SELL", "category": "Bus", "brand": "Force", "model": "Traveller",
        "registration_year": 2019, "running_km": "180000", "expected_price": "8 lakh",
        "state": "Bihar", "city": "Patna", "description": "18 seter school passing MH12AB1234 call 9876543210",
        "condition": "Very good", "owners": "2", "fuel": "Diesel", "tyre_percent": "70%",
        "vehicle_docs": {"insurance_valid_upto": "22-Jul-2027", "registration_number": "BR06GG8841",
                         "hypothecated": "Yes"},
    }
    conv.payload_json = json.dumps(payload)
    db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", extract_kind="vehicle_fields",
                   extracted_text=json.dumps({"brand": "Force", "colour": "Yellow", "body_type": "Minibus",
                                              "owner_name": "R***", "details": ["Roof carrier", "Owner: Ram"]})))
    db.flush()

    listing = build_listing_payload(db, conv, draft, payload, "req-desc-1")["listing"]
    assert (listing["category"], listing["make_model"], listing["year"]) == ("buses", "Force Traveller", 2019)
    assert (listing["km"], listing["price"], listing["state"], listing["city"]) == (180000, 800000, "Bihar", "Patna")
    desc = listing["description"]
    assert desc.startswith("18 seter school passing")
    for line in ("Condition: Very good", "Owners: 2", "Fuel: Diesel", "Tyre: 70%",
                 "Insurance valid till: 22-Jul-2027", "Colour: Yellow", "Body: Minibus", "Roof carrier"):
        assert line in desc, line
    for secret in ("MH12AB1234", "BR06GG8841", "9876543210", "Ram", "R***", "Hypothecat"):
        assert secret not in desc, secret


def test_hours_machine_keeps_unit_in_description(db):
    from app.infradealer.payloads import _ad_description

    text = _ad_description({"operating_hours": "6500", "condition": "Good"}, {}, "JCB 3DX", [], "Hours")
    assert text.startswith("JCB 3DX for sale.")
    assert "Usage: 6500 hours" in text and "Condition: Good" in text
    assert "BS 6 2024" in _ad_description({"description": "BS 6 2024 model"}, {}, "Truck")


def test_place_filter_rejects_filler_but_keeps_real_towns():
    for word in ("bhi", "bhai", "wahi"):
        assert not _looks_like_place(word), word
    for city in ("Sirsa", "Shrirampur", "Kharsia"):
        assert _looks_like_place(city), city
    assert not _looks_like_place("5 lac") and not _looks_like_place("Rs 500000")


def test_website_make_model_not_doubled_and_photo_scene_dropped(db):
    from app.infradealer.payloads import build_listing_payload
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777010", conversation_id="CONV_9000777010", state="NEW")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="READY", title="JCB Backhoe Loader")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    payload = {"intent": "SELL", "category": "JCB", "brand": "JCB", "model": "JCB", "operating_hours": "6500"}
    conv.payload_json = json.dumps(payload)
    db.add(AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", extract_kind="vehicle_fields",
                   extracted_text=json.dumps({"brand": "JCB", "details": ["Parked on a gravel surface", "Rusty bucket"]})))
    db.flush()
    listing = build_listing_payload(db, conv, draft, payload, "req-desc-2")["listing"]
    assert listing["make_model"] == "JCB" and "model" not in listing
    assert (listing["km"], listing["km_unit"]) == (6500, "Hours")
    assert "Rusty bucket" in listing["description"] and "gravel" not in listing["description"]
    assert "Usage: 6500 hours" in listing["description"]


def test_website_payload_drops_junk_city(db):
    from app.infradealer.payloads import build_listing_payload

    conv = AiConversation(mobile="919000777011", conversation_id="CONV_9000777011", state="NEW")
    db.add(conv)
    db.flush()
    payload = {"intent": "SELL", "category": "Bus", "brand": "Force", "state": "Haryana", "city": "bhi"}
    listing = build_listing_payload(db, conv, None, payload, "req-desc-3")["listing"]
    assert "city" not in listing and listing["state"] == "Haryana"
    payload["city"] = "Sirsa"
    assert build_listing_payload(db, conv, None, payload, "req-desc-4")["listing"]["city"] == "Sirsa"


def test_rejected_card_is_pushed_again_after_edits(db, monkeypatch):
    """Website rejected the listing; later edits reset the draft to COLLECTING — YES must push again."""
    from app.ai.data_push import _idempotent_short_circuit
    from app.infradealer.service import InfraDealerIntegrationService
    from app.models import InfraDealerOutbox

    pl = {"intent": "SELL", "category": "Truck", "brand": "BharatBenz", "model": "1314",
          "listing_status": "REJECTED", "infradealer_listing_id": "211",
          "submission": {"status": "REJECTED", "idempotency_key": "k1"}}
    conv = AiConversation(mobile="919000777012", conversation_id="CONV_9000777012", state="NEW",
                          payload_json=json.dumps(pl))
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="COLLECTING", title="BharatBenz 1314")
    db.add(draft)
    db.flush()
    db.add(InfraDealerOutbox(event_type="LISTING_PUSH", request_id="old-211", status="DONE",
                             conversation_id=conv.id, draft_id=draft.id))
    db.flush()

    svc = InfraDealerIntegrationService(db)
    assert svc.listing_rejected(draft, pl) and not svc.listing_already_pushed(conv, draft, pl)
    queued = []
    monkeypatch.setattr(svc, "enqueue", lambda event, body, **kw: queued.append(kw.get("request_id")) or "new")
    assert svc.push_listing_for_draft(conv, draft, pl) == "new"
    assert queued and queued[0] != "old-211"
    assert _idempotent_short_circuit(pl, "k1", "r") is None


def test_burst_interior_misread_does_not_fill_card(db, monkeypatch, tmp_path):
    from app.ai import vision
    from app.ai.tools import _payload, _write_payload
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777013", conversation_id="CONV_9000777013", state="NEW")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="COLLECTING")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    _write_payload(conv, {"intent": "SELL"})
    answers = {
        "a.jpg": {"category": "Truck", "brand": "Tata", "model": "1314", "registration_number": "RJ14AJ5094",
                  "colour": "White", "tyres": "10"},
        "b.jpg": {"category": "Bus", "brand": "Force", "model": "Traveller", "details": ["Cabin interior"]},
    }
    rows = []
    for name in ("a.jpg", "b.jpg"):
        p = tmp_path / name
        p.write_bytes(b"x")
        row = AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=str(p), mime="image/jpeg")
        db.add(row)
        db.flush()
        rows.append(row)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image",
                        lambda db_, conv_, f, prompt="", cfg=None: json.dumps(answers[f.local_path.split("\\")[-1].split("/")[-1]]))
    filled, ids = vision.fill_listing_from_burst(db, conv, rows[1], window_seconds=None)
    pl = _payload(conv)
    assert (pl["brand"], pl["model"], pl["category"]) == ("Tata", "1314", "Truck")
    assert sorted(ids) == sorted(r.id for r in rows)
    report = vision.image_report(db, conv, "hinglish", media_ids=ids)
    assert "Tata" in report and "Force" not in report and "Cabin interior" not in report


def test_photo_misread_of_typed_brand_still_reports_and_failed_reads_are_retried(db, monkeypatch, tmp_path):
    from app.ai import vision
    from app.ai.tools import _payload, _write_payload
    from app.models import AiMedia

    conv = AiConversation(mobile="919000777015", conversation_id="CONV_9000777015", state="NEW")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="COLLECTING")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    _write_payload(conv, {"intent": "SELL", "brand": "Mahindra", "model": "Jeeto Strong", "category": "Pickup"})
    answers = {
        "a.jpg": json.dumps({"category": "Pickup", "brand": "Force", "model": "Strong", "colour": "White",
                             "details": ["Open cargo deck", "Front grille badge indicates Force brand"]}),
        "b.jpg": json.dumps({"brand": "Force", "colour": "White", "tyres": "4"}),
        "c.jpg": "",
        "d.jpg": "",
    }
    rows = []
    for name in answers:
        p = tmp_path / name
        p.write_bytes(b"x")
        row = AiMedia(conversation_id=conv.id, draft_id=draft.id, kind="image", local_path=str(p), mime="image/jpeg")
        db.add(row)
        db.flush()
        rows.append(row)
    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: {})
    monkeypatch.setattr(vision, "extract_text_from_image",
                        lambda db_, conv_, f, prompt="", cfg=None: answers[f.local_path.replace("\\", "/").split("/")[-1]])
    filled, ids = vision.fill_listing_from_burst(db, conv, rows[-1], window_seconds=None)
    assert sorted(ids) == sorted(r.id for r in rows[:2])
    pl = _payload(conv)
    assert (pl["brand"], pl["model"]) == ("Mahindra", "Jeeto Strong")
    assert vision._reads_left(db, conv, draft.id) == vision.MAX_VISION_READS_PER_CARD - 2
    report = vision.image_report(db, conv, "hinglish", media_ids=ids)
    assert "Mahindra" in report and "Jeeto Strong" in report and "White" in report and "Open cargo deck" in report
    assert "Force" not in report
    assert "White" in vision.card_details(db, conv, "hinglish")

    answers["c.jpg"] = json.dumps({"brand": "Mahindra", "details": ["Jeeto badge on grille"]})
    answers["d.jpg"] = json.dumps({"colour": "White"})
    monkeypatch.setattr("app.ai.media_config.vision_enabled", lambda db_: True)
    assert sorted(vision.read_card_images(db, conv)) == sorted(r.id for r in rows[2:])
    assert "Jeeto badge on grille" in vision.image_report(db, conv, "hinglish")


MARATHI_AD = (
    "🌟 विक्रीसाठी उपलब्ध आहे! 🌟\n"
    "\u200bइशर प्रो २०८० एक्सपीटी टिपर (Eicher Pro 2080 XPT Tipper)\n"
    "गाडी एकदम चांगल्या कंडिशनमध्ये आणि सर्व कागदपत्रांसह विक्रीसाठी उपलब्ध आहे.\n"
    "\u200bमॉडेल: २०२३\n"
    "\u200bपासिंग आणि इन्शुरन्स: एप्रिल २०२७ पर्यंत (सर्व कागदपत्रे चोख आणि क्लिअर)\n"
    "\u200bओनर: २ (दुसरा मालक)\n"
    "\u200bरंग/इंधन: डिझेल\n"
    "\u200bकिलोमीटर: १,३१,००० किमी\n"
    "\u200bटायर्स: २ नवीन आणि ४ लोकल टायर्स\n"
    "\u200bविशेष वैशिष्ट्य: पॉवर स्टीयरिंग\n"
    "\u200b💰 किंमत: ₹ १४,००,०००/- (चौदा लाख रुपये) - किंचित तडजोड संभव.\n"
    "\u200b📍 पत्ता: कान्हे फाटा, तळेगांव दाभाडे, मुंबई-पुणे रोड, ता. मावळ, जि. पुणे.\n"
    "\u200b📞 संपर्क करा:\n\u200b९४२२३५६१६१\n\u200b९६०४४२२९७७"
)


def test_indic_digits_and_invisible_marks_are_normalized():
    from app.ai.i18n import normalize_input_text

    assert normalize_input_text("९६०४४२२९७७") == "9604422977"
    assert normalize_input_text("\u200bमॉडेल: २०२३ ₹ १४,००,०००") == "मॉडेल: 2023 ₹ 14,00,000"
    assert normalize_input_text("૧૨ ৩৪ ੫੬") == "12 34 56"
    assert normalize_input_text("Tata 407") == "Tata 407"


def test_marathi_ad_with_devanagari_digits_fills_every_field():
    from app.ai.engine import extract_turn
    from app.ai.i18n import normalize_input_text

    f = extract_turn(normalize_input_text(MARATHI_AD))
    assert (f["brand"], f["model"], f["category"], f["year"]) == ("Eicher", "Pro 2080 XPT", "Tipper", 2023)
    assert (f["expected_price"], f["running_km"], f["owners"], f["fuel"]) == (1400000, 131000, "2", "Diesel")
    assert (f["city"], f["state"], f["intent"]) == ("Pune", "Maharashtra", "SELL")
    desc = f["description"]
    assert "पॉवर स्टीयरिंग" in desc and "2 नवीन आणि 4 लोकल टायर्स" in desc and "एप्रिल 2027" in desc
    assert "9604422977" not in desc and "9422356161" not in desc and "संपर्क" not in desc


def test_hindi_ad_owner_price_and_place():
    from app.ai.engine import extract_turn

    f = extract_turn(
        "95808 18181 गाड़ी मालिक का नंबर\nअशोक लीलैंड की 22 चक्का टिप ट्रेलर गाड़ी बिकाऊ है डिमांड 25.5 लाख "
        "रुपया है फास्ट आनर गाडी है एकदम कम चली हुई है लोकेशन बस्ती है"
    )
    assert (f["brand"], f["expected_price"], f["owners"], f["intent"]) == ("Ashok Leyland", 2550000, "1", "SELL")
    assert "95808" not in f.get("description", "")


def test_yes_without_two_photos_is_not_pushed(db, monkeypatch):
    from app.ai import chat_memory
    from app.ai.tools import _payload, _write_payload

    conv = AiConversation(mobile="919000777014", conversation_id="CONV_9000777014", state="NEW")
    db.add(conv)
    db.flush()
    _write_payload(conv, {"intent": "SELL", "brand": "Tata", "awaiting_confirm": True})
    monkeypatch.setattr(chat_memory, "execute_tool", lambda *a, **k: pytest.fail("must not submit"))
    out = chat_memory.submit_confirmed_listing(db, conv)
    assert out == {"ok": False, "error": "photos_required", "photos": 0, "status": "SUBMISSION_BLOCKED"}
    assert _payload(conv)["awaiting_confirm"] and not _payload(conv)["customer_confirmed"]
    assert "kam se kam 2 photo" in chat_memory.submit_failure_reply("hinglish", out).lower()


def _poll_service(db, monkeypatch, sent):
    from app.infradealer import service as svc_mod

    svc = svc_mod.get_integration_service(db)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    monkeypatch.setattr(svc, "is_test_mode", lambda: False)

    class _Client:
        def get_status(self, **_):
            return {"body": {"code": "LISTING_POSTED", "listing": {"listing_id": "244", "status": "POSTED"}}}

    monkeypatch.setattr(svc, "_client", lambda *a, **k: _Client())
    monkeypatch.setattr(svc_mod, "get_integration_service", lambda _db: svc)
    monkeypatch.setattr(svc, "_notify_customer", lambda conv, text, **k: sent.append(text) or True)
    return svc


def test_poll_does_not_resend_approval_after_new_card(db, monkeypatch):
    from app.ai.tools import _payload, _write_payload
    from app.models import InfraDealerRequest

    conv = AiConversation(mobile="9000777020", conversation_id="CONV_9000777020", state="NEW")
    db.add(conv)
    db.flush()
    old = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="POSTED", card_id="CARD-001")
    new = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="COLLECTING", card_id="CARD-002")
    db.add_all([old, new])
    db.flush()
    db.add(InfraDealerRequest(
        request_id="r-244", event_type="LISTING_PUSH", status="SUCCESS", business_code="LISTING_PENDING_REVIEW",
        conversation_id=conv.id, draft_id=old.id, mobile=conv.mobile,
    ))
    conv.draft_id = new.id  # "Continue" after the chat clear started a fresh card
    pl = _payload(conv)
    pl.update({"listing_status": "", "listing_review_notified": False})
    _write_payload(conv, pl)
    db.flush()

    sent: list[str] = []
    svc = _poll_service(db, monkeypatch, sent)
    svc.poll_pending_listings()
    assert sent == []
    assert db.query(InfraDealerRequest).one().business_code == "LISTING_POSTED"


def test_decision_notify_is_once_per_card(db, monkeypatch):
    from app.ai.data_push import draft_decision_notified, notify_user_admin_decision
    from app.ai.tools import _payload, _write_payload

    conv = AiConversation(mobile="9000777021", conversation_id="CONV_9000777021", state="NEW")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=conv.mobile, status="PENDING_REVIEW", card_id="CARD-001")
    db.add(draft)
    db.flush()
    sent: list[str] = []
    _poll_service(db, monkeypatch, sent)

    notify_user_admin_decision(db, conv, approved=True, listing_id="244", draft=draft)
    assert len(sent) == 1 and draft_decision_notified(draft) == "POSTED"
    pl = _payload(conv)
    pl["listing_review_notified"] = False  # a new card reset the chat flag
    _write_payload(conv, pl)
    out = notify_user_admin_decision(db, conv, approved=True, listing_id="244", draft=draft)
    assert out.get("skipped") and len(sent) == 1


def test_pending_listing_never_gets_live_button(db, monkeypatch):
    from app.ai import confirm
    from app.ai.tools import _write_payload

    conv = AiConversation(mobile="9000777022", conversation_id="CONV_9000777022", state="NEW")
    db.add(conv)
    db.flush()
    _write_payload(conv, {"listing_status": "PENDING_REVIEW", "infradealer_listing_id": "250"})
    monkeypatch.setattr("app.services.send_whatsapp_button", lambda *a, **k: pytest.fail("no live button"))
    confirm._send_listing_button(db, conv, "hinglish")


def test_llm_internal_heading_is_dropped():
    out = _sanitize_reply("*Intent*\n• Bataiye aap karna chahte hain — vehicle bechna ya kharidna?")
    assert out == "Bataiye aap karna chahte hain — vehicle bechna ya kharidna?"
    keep = "*Tata 2518*\n• Price: 12 lakh\n• Year: 2019"
    assert _sanitize_reply(keep) == keep


def test_phone_numbers_are_not_price_or_model():
    from app.ai.engine import extract_turn

    for msg in ("099507 8568", "099507 85689", "+91 99507 85689", "Contact 099507 85689", "Ramesh 9950785689"):
        assert extract_turn(msg) == {}, msg
    f = extract_turn("Mahindra Blazo 2019 price 18 lakh contact 099507 85689")
    assert (f["model"], f["expected_price"]) == ("Blazo", 1800000)
    assert extract_turn("km 85000 2019 model")["year"] == 2019


def test_bare_number_does_not_abandon_office_card(db, monkeypatch):
    from app.ai.confirm import maybe_start_new_card

    conv = _office_card(db, monkeypatch, "919000777023", brand="Mahindra", model="Blazo", category="Truck")
    assert not maybe_start_new_card(db, conv, {"model": "8568", "expected_price": 99507})


def test_model_code_with_digits_is_not_lakh_price():
    from app.ai.engine import extract_turn

    f = extract_turn("Hitachi ZX220LC 2018 model\nPrice ₹ 36.75 Lakhs")
    assert f["expected_price"] == 3675000
    assert f.get("year") == 2018


def test_basti_is_not_bus():
    from app.ai.engine import extract_turn

    f = extract_turn("टाटा 5530 बस्ती उत्तर प्रदेश")
    assert f.get("category") != "Bus"


def test_hindi_hours_spelling_is_hours_not_model():
    from app.ai.engine import extract_turn

    f = extract_turn("2200 घण्टे")
    assert f.get("operating_hours") == 2200
    assert "model" not in f


def test_joined_phone_lists_are_dropped():
    from app.ai.engine import extract_turn

    for msg in ("9310292085..", "7879882913,9755224058", "Mob. 9310292085, 9755224058"):
        assert extract_turn(msg) == {}, msg


def test_yea_is_yes_not_place():
    from app.ai.confirm import _YES, is_yes
    from app.ai.engine import extract_turn

    assert _YES.search("Yea")
    assert not _looks_like_place("Yea")
    assert extract_turn("Yea") == {}
    for msg in ("yesyes", "Yes Yes", "haanhaan", "haan haan"):
        assert is_yes(msg), msg
        assert not _looks_like_place(msg), msg
        assert extract_turn(msg) == {}, msg


def test_rupee_digits_are_not_year():
    from app.ai.engine import extract_turn

    f = extract_turn("Rate..11,50000")
    assert f["expected_price"] == 1150000
    assert "year" not in f
    assert extract_turn("2021last")["year"] == 2021


def test_model_same_as_brand_is_not_shown():
    from app.ai.vision import listing_fields, shown_model

    assert shown_model("JCB", "JCB") == ""
    assert shown_model("Tata", "ATA") == ""
    assert shown_model("Tata", "Signa") == "Signa"
    assert "model" not in listing_fields({"brand": "JCB", "model": "JCB", "category": "Backhoe Loader"})


def test_summary_asks_place_when_missing():
    from app.ai.confirm import no_place_note, summary_notes

    pl = {"intent": "SELL", "brand": "Tata", "category": "Truck", "expected_price": 1500000}
    assert "state/city" in no_place_note(pl)
    assert no_place_note({**pl, "state": "Bihar"}) == ""
    assert no_place_note({**pl, "intent": "BUY"}) == ""
    assert any("state/city" in n for n in summary_notes(pl))
