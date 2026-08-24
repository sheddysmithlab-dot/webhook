"""Phase 3: Z.AI glm-4.6v-flash document OCR tests.

Covers:
- vision_enabled gating (flag + Z.AI key)
- is_document_media heuristic (RC/insurance/fitness cues, PDF)
- successful OCR returns extracted text
- Z.AI failure (non-200, network error, empty body) → graceful fallback ""
- non-image media → skip
- oversize image → skip
- missing file → skip
- never raises
- engine._ocr_extracted_fields merges RC text → vehicle fields
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiMedia, MetaSettings
from app.ai.vision import (
    extract_text_from_image,
    is_document_media,
)
from app.ai.media_config import vision_enabled


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    return Session()


def _enable_vision(db, zai_key="zai.key"):
    row = db.query(MetaSettings).order_by(MetaSettings.id.asc()).first()
    if not row:
        row = MetaSettings()
        db.add(row)
    row.ai_api_key = zai_key
    row.ai_vision_enabled = True
    db.commit()
    return row


def _conv(db, mobile="919999999999"):
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _media_row(db, conv, path="/tmp/rc.jpg", kind="image", mime="image/jpeg", caption=""):
    m = AiMedia(conversation_id=conv.id, kind=kind, mime=mime, local_path=path, caption=caption)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.content = b"x" if json_data is not None or text else b""

    def json(self):
        return self._json


class _FakeClient:
    resp = _FakeResp(200, {"choices": [{"message": {"content": "RC: MH12AB1234\nBrand: Tata\nModel: 407\nYear: 2019"}}]})
    exc = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        if _FakeClient.exc:
            raise _FakeClient.exc
        return _FakeClient.resp


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.resp = _FakeResp(200, {"choices": [{"message": {"content": "RC: MH12AB1234\nBrand: Tata\nYear: 2019"}}]})
    _FakeClient.exc = None
    yield
    _FakeClient.resp = _FakeResp(200, {"choices": [{"message": {"content": "RC: MH12AB1234\nBrand: Tata\nYear: 2019"}}]})
    _FakeClient.exc = None


def _patch_httpx(monkeypatch):
    monkeypatch.setattr("app.ai.vision.httpx.Client", _FakeClient)


# --- is_document_media heuristic ---

def test_is_document_media_pdf():
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, mime="application/pdf")
    assert is_document_media(m) is True
    db.close()


def test_is_document_media_rc_caption():
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, caption="ye rc book hai")
    assert is_document_media(m) is True
    db.close()


def test_is_document_media_insurance_text():
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, caption="", mime="image/jpeg")
    assert is_document_media(m, text="insurance paper bhej raha") is True
    db.close()


def test_is_document_media_fitness_hindi():
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, caption="fitness certificate")
    assert is_document_media(m) is True
    db.close()


def test_is_not_document_media_vehicle_photo():
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, caption="meri gaadi", mime="image/jpeg")
    assert is_document_media(m) is False
    db.close()


def test_is_document_media_none_row():
    assert is_document_media(None) is False


# --- extract_text_from_image gating & failure ---

def test_vision_disabled_returns_empty(monkeypatch, tmp_path):
    db = _session()
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"fake-jpg")
    m = _media_row(db, conv, path=str(img))
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""  # vision not enabled
    db.close()


def test_vision_disabled_when_zai_key_missing(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db, zai_key="")  # flag on but no key
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"x")
    m = _media_row(db, conv, path=str(img))
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_ocr_success_returns_text(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"fake-jpg-content")
    m = _media_row(db, conv, path=str(img))
    _patch_httpx(monkeypatch)
    text = extract_text_from_image(db, conv, m)
    assert "RC: MH12AB1234" in text
    assert "Brand: Tata" in text
    db.close()


def test_ocr_http_error_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"x")
    m = _media_row(db, conv, path=str(img))
    _FakeClient.resp = _FakeResp(500, text="zai down")
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_ocr_network_exception_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"x")
    m = _media_row(db, conv, path=str(img))
    _FakeClient.exc = ConnectionError("network gone")
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""  # never raises
    db.close()


def test_ocr_empty_content_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    img = tmp_path / "rc.jpg"
    img.write_bytes(b"x")
    m = _media_row(db, conv, path=str(img))
    _FakeClient.resp = _FakeResp(200, {"choices": [{"message": {"content": ""}}]})
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_ocr_missing_file_returns_empty(monkeypatch):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    m = _media_row(db, conv, path="/nonexistent/rc.jpg")
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_ocr_non_image_skipped(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    # audio mime, not an image
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")
    m = _media_row(db, conv, path=str(audio), kind="audio", mime="audio/ogg")
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_ocr_oversize_skipped(monkeypatch, tmp_path):
    db = _session()
    _enable_vision(db)
    conv = _conv(db)
    img = tmp_path / "big.jpg"
    img.write_bytes(b"\x00" * (10 * 1024 * 1024 + 1))
    m = _media_row(db, conv, path=str(img))
    _patch_httpx(monkeypatch)
    assert extract_text_from_image(db, conv, m) == ""
    db.close()


def test_vision_enabled_helper():
    db = _session()
    assert vision_enabled(db) is False
    _enable_vision(db)
    assert vision_enabled(db) is True
    db.close()


# --- engine._ocr_extracted_fields merge ---

def test_ocr_fields_merge_from_extracted_text():
    """engine._ocr_extracted_fields pulls the latest OCR row and extracts fields."""
    from app.ai.engine import _ocr_extracted_fields
    from app.ai.extract import extract_from_text

    db = _session()
    conv = _conv(db)
    # Simulate an OCR row that the runner would have populated.
    m = _media_row(db, conv, caption="rc")
    m.extracted_text = "RC: MH12AB1234\nBrand: Tata\nModel: 407\nYear: 2019\nState: Maharashtra"
    m.extract_kind = "ocr"
    db.commit()

    fields = _ocr_extracted_fields(db, conv)
    # extract_from_text should pick up brand/year/state from the OCR text.
    assert fields.get("brand") == "Tata" or fields.get("year") == "2019" or fields.get("state")
    db.close()


def test_ocr_fields_empty_when_no_ocr_row():
    from app.ai.engine import _ocr_extracted_fields
    db = _session()
    conv = _conv(db)
    # No OCR row exists for this conversation.
    assert _ocr_extracted_fields(db, conv) == {}
    db.close()


def test_ocr_fields_empty_when_extracted_text_blank():
    from app.ai.engine import _ocr_extracted_fields
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv, caption="rc")
    m.extracted_text = ""
    m.extract_kind = "ocr"
    db.commit()
    assert _ocr_extracted_fields(db, conv) == {}
    db.close()
