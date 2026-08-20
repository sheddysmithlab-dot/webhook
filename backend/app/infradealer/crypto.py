"""Server-side secret encryption (never expose to frontend)."""

from __future__ import annotations

import base64
import hashlib

from ..config import settings


def _key() -> bytes:
    raw = (settings.auth_session_secret or settings.admin_token or "infradealer-idw").encode()
    return hashlib.sha256(raw).digest()


def encrypt_secret(plain: str) -> str:
    text = (plain or "").strip()
    if not text:
        return ""
    key = _key()
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(text.encode()))
    return "enc:" + base64.urlsafe_b64encode(xored).decode()


def decrypt_secret(blob: str) -> str:
    text = (blob or "").strip()
    if not text:
        return ""
    if not text.startswith("enc:"):
        return text
    key = _key()
    data = base64.urlsafe_b64decode(text[4:].encode())
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode()


def mask_secret(value: str, visible: int = 4) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= visible:
        return "*" * len(text)
    return "*" * (len(text) - visible) + text[-visible:]


def short_key(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 12:
        return f"{text[:8]}…{text[-4:]}"
    return text


def signed_token(purpose: str, value: str, length: int = 24) -> str:
    digest = hashlib.sha256(_key() + f"|{purpose}|{value}".encode()).hexdigest()
    return digest[:length]
