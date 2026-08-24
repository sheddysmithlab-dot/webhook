"""Phase 3: Document image OCR via Z.AI glm-4.6v-flash (free vision model).

Reuses the SAME Z.AI account/api_key as text chat — only the model slug differs.
Gated on `ai_vision_enabled`. Only runs on document-like media (RC book,
insurance, fitness cert) to avoid burning the free-tier rate limit on every
casual vehicle photo. Never raises; returns "" on any failure.
"""

from __future__ import annotations

import base64
import logging
import os
import re

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation, AiMedia
from .media_config import resolve_media_config

log = logging.getLogger("infradealer.ai.vision")

MAX_IMAGE_BYTES = 10 * 1024 * 1024

_DOC_CUES = re.compile(
    r"\b("
    r"rc|rc\s*book|r\.c|registration|regd|reg\s*no|"
    r"insurance|policy|bima|policy\s*no|"
    r"fitness|fitness\s*cert|puc|"
    r"paper|document|kagaz|kagaj|patta|praman|pramaan|"
    r"chassis|engine\s*no|"
    r"owner|name\s*of\s*owner|"
    r"valid\s*(?:upto|till|validity)|expiry"
    r")\b",
    re.I,
)

_IMAGE_MIMES = ("image/jpeg", "image/jpg", "image/png", "image/webp")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

_OCR_PROMPT = (
    "Extract all readable text from this document image. Focus on: "
    "registration/RC number, vehicle number plate, chassis/engine number, "
    "brand, model, manufacturing/registration year, owner name, and any dates "
    "(registration date, insurance validity, fitness expiry). "
    "Return ONLY the extracted text exactly as printed, one field per line. "
    "If a field is not visible, omit it. Do not add commentary or guesses."
)


def is_document_media(media_row: AiMedia | None, text: str = "") -> bool:
    """True if this media looks like a document (not a casual vehicle photo)."""
    if media_row is None:
        return False
    mime = (media_row.mime or "").lower()
    if "pdf" in mime:
        return True
    caption = (media_row.caption or "") + " " + (text or "")
    return bool(_DOC_CUES.search(caption))


def _is_image(media_row: AiMedia) -> bool:
    mime = (media_row.mime or "").lower()
    if any(m in mime for m in _IMAGE_MIMES):
        return True
    return (media_row.local_path or "").lower().endswith(_IMAGE_EXTS)


def _data_url(media_row: AiMedia) -> str:
    with open(media_row.local_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    mime = (media_row.mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    return f"data:{mime};base64,{b64}"


def extract_text_from_image(
    db: Session,
    conv: AiConversation,
    media_row: AiMedia,
    prompt: str = _OCR_PROMPT,
) -> str:
    """OCR a document image via Z.AI glm-4.6v-flash. Returns text or "".

    Never raises. Side-effect free — caller persists extracted_text/extract_kind.
    """
    cfg = resolve_media_config(db)
    if not cfg.get("vision_enabled"):
        return ""

    path = (media_row.local_path or "").strip()
    if not path or not os.path.exists(path):
        log.warning("vision.skip: file missing path=%s", path)
        return ""

    if not _is_image(media_row):
        log.info("vision.skip: not an image mime=%s", media_row.mime)
        return ""

    try:
        size = os.path.getsize(path)
    except OSError as exc:
        log.warning("vision.skip: stat failed %s: %s", path, exc)
        return ""
    if size <= 0:
        log.warning("vision.skip: empty file %s", path)
        return ""
    if size > MAX_IMAGE_BYTES:
        log.warning("vision.skip: image too large %d bytes", size)
        return ""

    api_key = cfg.get("vision_api_key") or ""
    api_base = (cfg.get("vision_api_base") or "").rstrip("/")
    model = cfg.get("vision_model") or "glm-4.6v-flash"
    if not api_key or not api_base:
        return ""

    try:
        data_url = _data_url(media_row)
    except Exception as exc:
        log.warning("vision.skip: base64 encode failed %s: %s", path, exc)
        return ""

    url = api_base + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, headers=headers, json=body)
    except Exception as exc:
        log.warning("vision.http error: %s", exc)
        return ""

    if resp.status_code >= 400:
        log.warning("vision.http %s %s", resp.status_code, (resp.text or "")[:200])
        return ""

    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    choice = (data.get("choices") or [{}])[0]
    content = str((choice.get("message") or {}).get("content") or "").strip()
    if not content:
        log.warning("vision.empty: no content keys=%s", list(data.keys()))
        return ""
    return content
