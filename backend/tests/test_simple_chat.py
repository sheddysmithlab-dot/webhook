"""Clean Z.AI simple chat — no listing/card/OTP."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.prompt import SIMPLE_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.ai.simple_chat import _sanitize, _user_content, simple_respond
from app.config import settings


def test_simple_prompt_is_clean():
    assert "InfraDealer WhatsApp AI assistant" in SIMPLE_SYSTEM_PROMPT
    assert "CARD-001" not in SIMPLE_SYSTEM_PROMPT
    assert "OTP" not in SIMPLE_SYSTEM_PROMPT or "Do not invent" in SIMPLE_SYSTEM_PROMPT
    assert "listing" in SIMPLE_SYSTEM_PROMPT.lower()  # says do not start listing
    assert getattr(settings, "ai_simple_chat", True) is True
    print("OK clean prompt + flag")


def test_sanitize_and_media():
    assert "Reply Bob" not in _sanitize("Reply Bob\nHello")
    assert "[blocked]" in _sanitize("sk-abc123secret")
    assert "photo" in _user_content("[photo]", "image id=1").lower()
    assert "voice" in _user_content("[voice note]", "audio").lower()
    from app.ai.simple_chat import _local_fast_reply

    hi = _local_fast_reply("Hello", "")
    assert hi and "Namaste" in hi
    photo = _local_fast_reply("[photo]", "image")
    assert photo and "Photo" in photo
    assert "listing" not in photo.lower() or "Listing form" in photo
    print("OK sanitize/media")


def test_simple_respond_calls_zai_only():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    conv = MagicMock()
    conv.conversation_id = "CONV_8224000826"
    conv.state = "NEW"

    fake_cfg = {
        "enabled": True,
        "api_key": "test-key",
        "api_base": "https://api.z.ai/api/paas/v4",
        "model": "glm-4.5-flash",
        "config_error": "",
        "provider": "z.ai",
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b'{"choices":[{"message":{"content":"Namaste! Main theek hoon."}}]}'
    fake_resp.json.return_value = {
        "choices": [{"message": {"content": "Namaste! Main theek hoon."}}],
    }

    with patch("app.ai.simple_chat.resolve_ai_config", return_value=fake_cfg), patch(
        "app.ai.simple_chat.httpx.Client"
    ) as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = fake_resp
        client_cls.return_value = client

        reply = simple_respond(db, conv, "Kaise ho?", "")
        assert "theek" in reply.lower() or "Namaste" in reply
        assert conv.state == "CHAT"
        args, kwargs = client.post.call_args
        assert "api.z.ai" in args[0]
        body = kwargs["json"]
        assert body["model"] == "glm-4.5-flash"
        assert "tools" not in body
        assert body["messages"][0]["role"] == "system"
        assert "CARD" not in body["messages"][0]["content"]
        assert "openai.com" not in args[0]
    print("OK Z.AI-only simple_respond")


def test_simple_respond_no_openai_fallback_on_error():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    conv = MagicMock()
    conv.conversation_id = "CONV_X"
    fake_cfg = {
        "enabled": True,
        "api_key": "test-key",
        "api_base": "https://api.z.ai/api/paas/v4",
        "model": "glm-4.5-flash",
        "config_error": "",
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.content = b'{"error":{"message":"boom"}}'
    fake_resp.text = "boom"
    fake_resp.json.return_value = {"error": {"message": "boom"}}

    with patch("app.ai.simple_chat.resolve_ai_config", return_value=fake_cfg), patch(
        "app.ai.simple_chat.httpx.Client"
    ) as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = fake_resp
        client_cls.return_value = client
        reply = simple_respond(db, conv, "Hello", "")
        assert "technical" in reply.lower() or "dikkat" in reply.lower()
        assert client.post.call_count == 1
    print("OK no provider fallback on error")


if __name__ == "__main__":
    test_simple_prompt_is_clean()
    test_sanitize_and_media()
    test_simple_respond_calls_zai_only()
    test_simple_respond_no_openai_fallback_on_error()
    print("ALL OK")
