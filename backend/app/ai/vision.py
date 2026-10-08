"""Phase 3: Document image OCR via Z.AI glm-4.6v-flash (free vision model).

Reuses the SAME Z.AI account/api_key as text chat — only the model slug differs.
Gated on `ai_vision_enabled`. Only runs on document-like media (RC book,
insurance, fitness cert) to avoid burning the free-tier rate limit on every
casual vehicle photo. Never raises; returns "" on any failure.
"""

from __future__ import annotations

import base64
import json
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


_VEHICLE_PROMPT = (
    "This photo shows a commercial vehicle or construction machine for sale in India. "
    "Look at the whole image: the vehicle body, brand badge/logo, model name written on it, "
    "and any visible text or stickers. Reply with ONLY a JSON object using these keys, "
    "omitting any you cannot see clearly: "
    '"category" (one of Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, '
    'Excavator, Grader, Crusher), "brand", "model", "year" (only if a year is printed). '
    "Never guess."
)

VISION_FILL_KEYS = ("category", "brand", "model", "year")
MAX_VISION_READS_PER_CARD = 2


def extract_vehicle_fields(db: Session, conv: AiConversation, media_row: AiMedia) -> dict:
    """Listing fields read from a vehicle photo ({} when nothing reliable)."""
    from datetime import date

    from .data_filteration import extract_fields
    from .schema import normalize_vehicle_category

    raw = extract_text_from_image(db, conv, media_row, prompt=_VEHICLE_PROMPT)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    cat = normalize_vehicle_category(str(data.get("category") or ""))
    if cat and cat != "Other":
        out["category"] = cat
    brand = str(data.get("brand") or "").strip()
    if brand:
        out["brand"] = extract_fields([brand]).get("brand") or brand.title()[:40]
    model = re.sub(r"\s+", " ", str(data.get("model") or "")).strip()
    if model and model.lower() not in {"unknown", "n/a", "na", "none"}:
        out["model"] = model[:40]
    try:
        year = int(str(data.get("year") or "").strip()[:4])
    except ValueError:
        year = 0
    if 1980 <= year <= date.today().year:
        out["year"] = year
    return out


def fill_listing_from_photo(db: Session, conv: AiConversation, media_row: AiMedia) -> dict:
    """Fill only blank card fields from a vehicle photo; capped per card. Returns what was filled."""
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    missing = [k for k in VISION_FILL_KEYS if payload.get(k) in (None, "", [], {})]
    if not missing:
        return {}
    reads = payload.get("vision_reads") if isinstance(payload.get("vision_reads"), dict) else {}
    if reads.get("draft") != conv.draft_id:
        reads = {"draft": conv.draft_id, "n": 0}
    if int(reads.get("n") or 0) >= MAX_VISION_READS_PER_CARD:
        return {}
    reads["n"] = int(reads.get("n") or 0) + 1
    payload["vision_reads"] = reads
    _write_payload(conv, payload)

    found = extract_vehicle_fields(db, conv, media_row)
    filled = {k: v for k, v in found.items() if k in missing}
    if filled:
        payload = _payload(conv)
        for k, v in filled.items():
            if payload.get(k) in (None, "", [], {}):
                payload[k] = v
        _write_payload(conv, payload)
        media_row.extracted_text = json.dumps(found, ensure_ascii=False) if found else ""
        media_row.extract_kind = "vehicle_fields"
    return filled
