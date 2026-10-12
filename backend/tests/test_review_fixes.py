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
