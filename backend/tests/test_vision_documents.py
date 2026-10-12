"""Vision: vehicle photos, RC and Vahan/mParivahan screenshots fill the listing card."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import vision
from app.ai.tools import _payload, _write_payload
from app.database import Base
from app.infradealer.payloads import _ad_description
from app.models import AiConversation, AiMedia

VAHAN_SCREENSHOT = {
    "image_type": "app_screenshot",
    "registration_number": "BR06GG8841",
    "owner_name": "B***U K***R",
    "registering_authority": "MUZAFFARPUR, Bihar",
    "vehicle_class": "Goods Carrier(HGV)",
    "fuel": "DIESEL",
    "emission_norm": "BHARAT STAGE VI",
    "hypothecated": "Yes",
    "registration_date": "31-Jul-2025",
    "fitness_valid_upto": "28-Jul-2027",
    "tax_valid_upto": "29-Oct-2026",
    "insurance_valid_upto": "22-Jul-2027",
    "puc_valid_upto": "28-Jul-2027",
    "permit_valid_upto": "01-Aug-2030",
    "national_permit_valid_upto": "01-Aug-2027",
}


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _setup(db, tmp_path, monkeypatch, answer: dict, **payload):
    conv = AiConversation(mobile="9000000001", conversation_id="CONV_9000000001", state="NEW", payload_json="{}")
    db.add(conv)
    db.flush()
    _write_payload(conv, dict(payload))
    img = tmp_path / "shot.jpg"
    img.write_bytes(b"x")
    row = AiMedia(conversation_id=conv.id, kind="image", local_path=str(img), mime="image/jpeg")
    db.add(row)
    db.flush()
    calls = []

    def fake(db_, conv_, media_row, prompt=""):
        calls.append(prompt)
        return "```json\n" + json.dumps(answer) + "\n```"

    monkeypatch.setattr(vision, "extract_text_from_image", fake)
    return conv, row, calls


def test_vahan_screenshot_fills_card(db, tmp_path, monkeypatch):
    conv, row, _ = _setup(db, tmp_path, monkeypatch, VAHAN_SCREENSHOT, intent="SELL")
    filled = vision.fill_listing_from_photo(db, conv, row)
    assert filled == {"category": "Truck", "year": 2025, "city": "Muzaffarpur", "state": "Bihar", "fuel": "Diesel"}
    pl = _payload(conv)
    assert pl["location"] and "Muzaffarpur" in pl["location"]
    assert pl["registration_year"] == 2025
    docs = pl["vehicle_docs"]
    assert docs["registration_number"] == "BR06GG8841"
    assert docs["insurance_valid_upto"] == "22-Jul-2027"
    assert docs["national_permit_valid_upto"] == "01-Aug-2027"
    assert "owner_name" not in docs
    assert row.extract_kind == "document_fields"


def test_document_details_never_overwrite_user_values(db, tmp_path, monkeypatch):
    conv, row, _ = _setup(
        db, tmp_path, monkeypatch, VAHAN_SCREENSHOT,
        intent="SELL", category="Tipper", year=2024, state="Bihar", city="Patna",
    )
    filled = vision.fill_listing_from_photo(db, conv, row)
    assert filled == {"fuel": "Diesel"}
    pl = _payload(conv)
    assert (pl["category"], pl["year"], pl["city"]) == ("Tipper", 2024, "Patna")


def test_captioned_document_read_even_when_card_full(db, tmp_path, monkeypatch):
    conv, row, calls = _setup(
        db, tmp_path, monkeypatch, VAHAN_SCREENSHOT,
        intent="SELL", category="Truck", brand="Tata", model="4825", year=2025,
        state="Bihar", city="Muzaffarpur", fuel="Diesel",
    )
    assert vision.fill_listing_from_photo(db, conv, row) == {}
    assert calls == []
    vision.fill_listing_from_photo(db, conv, row, is_document=True)
    assert len(calls) == 1
    assert _payload(conv)["vehicle_docs"]["fitness_valid_upto"] == "28-Jul-2027"


def test_ad_description_shows_validity_but_not_owner_or_plate(db, tmp_path, monkeypatch):
    conv, row, _ = _setup(db, tmp_path, monkeypatch, VAHAN_SCREENSHOT, intent="SELL")
    vision.fill_listing_from_photo(db, conv, row)
    text = _ad_description(_payload(conv), {}, "Tata Truck")
    assert "Fuel: Diesel" in text
    assert "Insurance valid till: 22-Jul-2027" in text
    assert "Emission norm: BHARAT STAGE VI" in text
    assert "BR06GG8841" not in text and "K***R" not in text and "Hypothecat" not in text


def _media_cfg(**over):
    cfg = {
        "vision_enabled": True, "vision_model": "glm-4.6v-flash", "vision_api_key": "zk",
        "vision_api_base": "https://zai.test/v4", "groq_api_key": "gk",
        "groq_api_base": "https://groq.test/v1", "groq_vision_model": "qwen/test-vl",
    }
    cfg.update(over)
    return cfg


def _image_row(db, tmp_path):
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    row = AiMedia(conversation_id=1, kind="image", local_path=str(img), mime="image/jpeg")
    db.add(row)
    db.flush()
    return row


def test_groq_used_when_zai_fails(db, tmp_path, monkeypatch):
    row = _image_row(db, tmp_path)
    calls = []

    def fake_chat(base, key, model, prompt, data_url, extra):
        calls.append((base, model, extra))
        return "" if "zai" in base else '{"fuel": "DIESEL"}'

    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: _media_cfg())
    monkeypatch.setattr(vision, "_vision_chat", fake_chat)
    assert vision.extract_text_from_image(db, None, row, prompt="p") == '{"fuel": "DIESEL"}'
    assert [c[1] for c in calls] == ["glm-4.6v-flash", "qwen/test-vl"]
    assert "thinking" not in calls[1][2]


def test_groq_not_called_when_zai_answers(db, tmp_path, monkeypatch):
    row = _image_row(db, tmp_path)
    calls = []

    def fake_chat(base, key, model, prompt, data_url, extra):
        calls.append(model)
        return '{"category": "Truck"}'

    monkeypatch.setattr(vision, "resolve_media_config", lambda db_: _media_cfg())
    monkeypatch.setattr(vision, "_vision_chat", fake_chat)
    vision.extract_text_from_image(db, None, row, prompt="p")
    assert calls == ["glm-4.6v-flash"]


def test_idle_reset_before_image_keeps_image_fields(db, tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from app.ai.session_memory import prepare_turn, reset_if_idle

    old = (datetime.utcnow() - timedelta(minutes=30)).isoformat()
    conv, row, _ = _setup(
        db, tmp_path, monkeypatch, VAHAN_SCREENSHOT,
        intent="SELL", brand="Tata", category="Tipper", last_user_message_at=old,
    )
    assert reset_if_idle(db, conv) is True
    assert _payload(conv).get("brand") in (None, "")
    vision.fill_listing_from_photo(db, conv, row, is_document=True)
    prep = prepare_turn(db, conv, "")
    assert prep["mode"] == "new_chat"
    pl = _payload(conv)
    assert (pl["category"], pl["city"], pl["fuel"]) == ("Truck", "Muzaffarpur", "Diesel")


def test_truck_model_line_is_not_a_city():
    from app.ai.data_filteration import extract_fields

    out = extract_fields(["Signa"])
    assert (out.get("brand"), out.get("model")) == ("Tata", "Signa")
    assert "city" not in out and "location" not in out
    assert extract_fields(["Muzaffarpur"]).get("city") == "Muzaffarpur"


def test_vision_reads_cap_survives_save(db, tmp_path, monkeypatch):
    conv, row, calls = _setup(db, tmp_path, monkeypatch, {"image_type": "vehicle_photo"}, intent="SELL")
    for _ in range(vision.MAX_VISION_READS_PER_CARD + 2):
        vision.fill_listing_from_photo(db, conv, row)
    assert len(calls) == vision.MAX_VISION_READS_PER_CARD
