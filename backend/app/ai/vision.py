"""Phase 3: image reading (vehicle photos, RC, Vahan screenshots) via Z.AI glm-4.6v-flash.

Reuses the SAME Z.AI account/api_key as text chat — only the model slug differs.
When Z.AI fails (the free model is often overloaded), the Groq key used for voice
is tried with `groq_vision_model`. Gated on `ai_vision_enabled`.
Never raises; returns "" on any failure.
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
GROQ_MAX_IMAGE_BYTES = 3 * 1024 * 1024

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
    groq_key = cfg.get("groq_api_key") or ""
    if not (api_key and api_base) and not groq_key:
        return ""

    try:
        data_url = _data_url(media_row)
    except Exception as exc:
        log.warning("vision.skip: base64 encode failed %s: %s", path, exc)
        return ""

    content = ""
    if api_key and api_base:
        content = _vision_chat(
            api_base, api_key, model, prompt, data_url,
            {"thinking": {"type": "disabled"}, "enable_thinking": False},
        )
    # Groq caps base64 image requests at 4 MB.
    if not content and groq_key and size <= GROQ_MAX_IMAGE_BYTES:
        content = _vision_chat(
            (cfg.get("groq_api_base") or "").rstrip("/"), groq_key,
            cfg.get("groq_vision_model") or "qwen/qwen3.8-27b", prompt, data_url,
            {"reasoning_effort": "none"},
        )
    return content


def _vision_chat(api_base: str, api_key: str, model: str, prompt: str, data_url: str, extra: dict) -> str:
    """One OpenAI-compatible image chat call. Returns the reply text or "" (never raises)."""
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
        "max_tokens": 900,
        **extra,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(api_base + "/chat/completions", headers=headers, json=body)
    except Exception as exc:
        log.warning("vision.http error model=%s: %s", model, exc)
        return ""

    if resp.status_code >= 400:
        log.warning("vision.http model=%s %s %s", model, resp.status_code, (resp.text or "")[:200])
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
        log.warning("vision.empty model=%s keys=%s", model, list(data.keys()))
    return content


_VEHICLE_PROMPT = (
    "A seller of a used commercial vehicle or construction machine in India sent this image. "
    "It may be a photo of the vehicle/machine, an RC book or RC smart card, a Vahan / mParivahan / "
    "RTO 'vehicle search' app screenshot, or an insurance / fitness / permit paper. "
    "Read everything visible: badges, stickers and every printed label/value. "
    "Reply with ONLY one JSON object, omitting keys you cannot read: "
    '"image_type" (vehicle_photo, rc_document, app_screenshot, insurance, other), '
    '"category" (one of Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, '
    'Excavator, Grader, Crusher, Bus, Tractor, Road Roller), "brand" (maker), "model", '
    '"manufacturing_year", "registration_number", "vehicle_class", "registration_date", '
    '"registering_authority", "fuel", "emission_norm", "hypothecated", "insurance_valid_upto", '
    '"fitness_valid_upto", "tax_valid_upto", "puc_valid_upto", "permit_valid_upto", '
    '"national_permit_valid_upto". Copy values exactly as printed. Never guess.'
)

VISION_FILL_KEYS = ("category", "brand", "model", "year", "state", "city", "fuel")
MAX_VISION_READS_PER_CARD = 3

_GOODS_CLASS = re.compile(r"goods\s*carrier|\b[hml]gv\b|\bhtv\b|\btruck\b", re.I)
_RTO_WORDS = re.compile(r"\b(?:rto|dto|arto|srto|r\.t\.o\.?|regional transport office)\b", re.I)
_FUELS = {"diesel": "Diesel", "petrol": "Petrol", "cng": "CNG", "lpg": "LPG", "electric": "Electric", "ev": "Electric"}

# Document facts kept on the card (never the owner name or the plate number in the public ad).
DOC_FACT_KEYS = (
    "registration_number", "vehicle_class", "registration_date", "registering_authority",
    "emission_norm", "hypothecated", "insurance_valid_upto", "fitness_valid_upto",
    "tax_valid_upto", "puc_valid_upto", "permit_valid_upto", "national_permit_valid_upto",
)


def _clean(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text.lower() in {"unknown", "n/a", "na", "none", "null", "-"} else text


def _year_in(value) -> int:
    from datetime import date

    m = re.search(r"(?:19|20)\d{2}", str(value or ""))
    year = int(m.group(0)) if m else 0
    return year if 1980 <= year <= date.today().year else 0


def read_image(db: Session, conv: AiConversation, media_row: AiMedia) -> dict:
    """Raw JSON the vision model read from a vehicle photo or vehicle document ({} on failure)."""
    raw = extract_text_from_image(db, conv, media_row, prompt=_VEHICLE_PROMPT)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def listing_fields(data: dict) -> dict:
    """Card fields (category/brand/model/year/state/city/location/fuel) from read_image output."""
    from .data_filteration import extract_fields, normalize_location
    from .schema import normalize_vehicle_category

    out: dict = {}
    cat = normalize_vehicle_category(_clean(data.get("category")))
    if not cat or cat == "Other":
        vclass = _clean(data.get("vehicle_class"))
        cat = normalize_vehicle_category(vclass) or ("Truck" if _GOODS_CLASS.search(vclass) else "")
    if cat and cat != "Other":
        out["category"] = cat
    brand = _clean(data.get("brand"))
    if brand:
        out["brand"] = extract_fields([brand]).get("brand") or brand.title()[:40]
    model = _clean(data.get("model"))
    if model:
        out["model"] = model[:40]
    reg_year = _year_in(data.get("registration_date"))
    year = _year_in(data.get("manufacturing_year") or data.get("year")) or reg_year
    if year:
        out["year"] = year
    if reg_year:
        out["registration_year"] = reg_year
    authority = _RTO_WORDS.sub(" ", _clean(data.get("registering_authority")))
    if authority:
        parts = [p.strip(" .-") for p in authority.split(",") if p.strip(" .-")]
        city = parts[0].title() if parts else ""
        state = parts[-1] if len(parts) > 1 else ""
        out.update({k: v for k, v in normalize_location(city=city, state=state).items() if k != "country"})
    fuel = _FUELS.get(_clean(data.get("fuel")).lower())
    if fuel:
        out["fuel"] = fuel
    return out


def document_facts(data: dict) -> dict:
    return {k: _clean(data.get(k))[:60] for k in DOC_FACT_KEYS if _clean(data.get(k))}


def extract_vehicle_fields(db: Session, conv: AiConversation, media_row: AiMedia) -> dict:
    """Listing fields read from a vehicle photo or document ({} when nothing reliable)."""
    return listing_fields(read_image(db, conv, media_row))


def fill_listing_from_photo(
    db: Session, conv: AiConversation, media_row: AiMedia, is_document: bool = False,
) -> dict:
    """Fill only blank card fields from a photo / RC / Vahan screenshot. Returns what was filled.

    Vehicle photos are capped per card; images the sender labels as documents are always read.
    """
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    missing = [k for k in VISION_FILL_KEYS if payload.get(k) in (None, "", [], {})]
    if not missing and not is_document:
        return {}
    if not is_document:
        reads = payload.get("vision_reads") if isinstance(payload.get("vision_reads"), dict) else {}
        if reads.get("draft") != conv.draft_id:
            reads = {"draft": conv.draft_id, "n": 0}
        if int(reads.get("n") or 0) >= MAX_VISION_READS_PER_CARD:
            return {}
        reads["n"] = int(reads.get("n") or 0) + 1
        payload["vision_reads"] = reads
        _write_payload(conv, payload)

    data = read_image(db, conv, media_row)
    found = listing_fields(data)
    facts = document_facts(data)
    filled = {k: v for k, v in found.items() if k in missing}
    if filled or facts:
        payload = _payload(conv)
        for k, v in filled.items():
            if payload.get(k) in (None, "", [], {}):
                payload[k] = v
        if "city" in filled and payload.get("location") in (None, ""):
            payload["location"] = found.get("location") or filled["city"]
        if found.get("registration_year") and payload.get("registration_year") in (None, ""):
            payload["registration_year"] = found["registration_year"]
        if facts:
            docs = payload.get("vehicle_docs") if isinstance(payload.get("vehicle_docs"), dict) else {}
            payload["vehicle_docs"] = {**docs, **facts}
        _write_payload(conv, payload)
    if data:
        media_row.extracted_text = json.dumps(data, ensure_ascii=False)
        media_row.extract_kind = "document_fields" if facts else "vehicle_fields"
    return filled
