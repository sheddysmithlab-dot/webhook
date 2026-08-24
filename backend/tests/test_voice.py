"""Phase 2: Groq Whisper voice transcription tests.

Covers:
- voice_enabled gating (flag + Groq key)
- successful transcription replaces placeholder text
- Groq failure (non-200, network error, empty body) → graceful fallback to ""
- 25 MB file cap → skip
- missing file → skip
- language hint mapping
- never raises on any failure path
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AiConversation, AiMedia, MetaSettings
from app.ai.voice import transcribe_audio, _language_hint
from app.ai.media_config import voice_enabled


def _session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    return Session()


def _enable_voice(db, groq_key="gsk_test"):
    row = db.query(MetaSettings).order_by(MetaSettings.id.asc()).first()
    if not row:
        row = MetaSettings()
        db.add(row)
    row.ai_voice_enabled = True
    row.groq_api_key = groq_key
    db.commit()
    return row


def _conv(db, mobile="919999999999"):
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", state="NEW", language="hi")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _media_row(db, conv, path="/tmp/voice.ogg"):
    m = AiMedia(conversation_id=conv.id, kind="audio", mime="audio/ogg", local_path=path)
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
    """Replaces httpx.Client so transcribe_audio never hits the network."""
    resp = _FakeResp(200, {"text": "Tata tipper bechna hai 40 lakh"})
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
    _FakeClient.resp = _FakeResp(200, {"text": "Tata tipper bechna hai 40 lakh"})
    _FakeClient.exc = None
    yield
    _FakeClient.resp = _FakeResp(200, {"text": "Tata tipper bechna hai 40 lakh"})
    _FakeClient.exc = None


def _patch_httpx(monkeypatch):
    monkeypatch.setattr("app.ai.voice.httpx.Client", _FakeClient)


def test_voice_disabled_returns_empty(monkeypatch):
    db = _session()
    conv = _conv(db)
    m = _media_row(db, conv)
    # No enable_voice() call → voice stays disabled
    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_voice_disabled_when_groq_key_missing(monkeypatch):
    db = _session()
    _enable_voice(db, groq_key="")  # flag on but no key
    conv = _conv(db)
    m = _media_row(db, conv)
    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_transcribe_success_replaces_placeholder(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    # real temp file so os.path.exists passes
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake-ogg-content")
    m = _media_row(db, conv, path=str(audio))
    _patch_httpx(monkeypatch)

    text = transcribe_audio(db, conv, m)
    assert text == "Tata tipper bechna hai 40 lakh"
    db.close()


def test_transcribe_http_error_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")
    m = _media_row(db, conv, path=str(audio))
    _FakeClient.resp = _FakeResp(500, text="groq down")
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_transcribe_network_exception_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")
    m = _media_row(db, conv, path=str(audio))
    _FakeClient.exc = ConnectionError("network gone")
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""  # never raises
    db.close()


def test_transcribe_empty_response_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")
    m = _media_row(db, conv, path=str(audio))
    _FakeClient.resp = _FakeResp(200, json_data={"text": ""})
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_transcribe_missing_file_returns_empty(monkeypatch):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    m = _media_row(db, conv, path="/nonexistent/path/voice.ogg")
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_transcribe_empty_file_returns_empty(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"")  # 0 bytes
    m = _media_row(db, conv, path=str(audio))
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_transcribe_oversize_file_skipped(monkeypatch, tmp_path):
    db = _session()
    _enable_voice(db)
    conv = _conv(db)
    audio = tmp_path / "voice.ogg"
    # write a file just over 25 MB
    audio.write_bytes(b"\x00" * (25 * 1024 * 1024 + 1))
    m = _media_row(db, conv, path=str(audio))
    _patch_httpx(monkeypatch)

    assert transcribe_audio(db, conv, m) == ""
    db.close()


def test_language_hint_mapping():
    assert _language_hint("hi") == "hi"
    assert _language_hint("hinglish") == "hi"
    assert _language_hint("en") == "en"
    assert _language_hint("pa") == "pa"
    assert _language_hint("ta") == "ta"
    assert _language_hint("unknown") == ""
    assert _language_hint("") == ""


def test_voice_enabled_helper(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_voice_enabled", False)
    monkeypatch.setattr(settings, "groq_api_key", "")

    db = _session()
    assert voice_enabled(db) is False
    _enable_voice(db)
    assert voice_enabled(db) is True
    db.close()
