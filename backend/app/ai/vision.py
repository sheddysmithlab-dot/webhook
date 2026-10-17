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
import threading
import time

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation, AiMedia
from .media_config import resolve_media_config

log = logging.getLogger("infradealer.ai.vision")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
GROQ_MAX_IMAGE_BYTES = 3 * 1024 * 1024
GROQ_RETRY_SECONDS = 4.0
# Keeps a 3-photo burst well inside the 45 s per-chat lock.
GROQ_MAX_RETRY_SECONDS = 8.0
# The free glm-4.6v-flash answers "1305 overloaded" in bursts; a short wait usually gets through.
ZAI_TRIES = 3
ZAI_RETRY_SECONDS = 2.0
_GROQ_GATE = threading.Lock()

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


PHOTO_SEND_PIXELS = 350_000
SCREENSHOT_SEND_PIXELS = 800_000


def _shrunk_jpeg(path: str) -> bytes:
    """Re-encode large images as smaller JPEGs: vision APIs bill (and rate-limit) by pixels.

    Screenshots / papers (PNG or tall phone shots) keep more pixels so small text stays legible.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return b""
    import io

    try:
        with Image.open(path) as img:
            is_png = (img.format or "").upper() == "PNG"
            img = ImageOps.exif_transpose(img).convert("RGB")
            w, h = img.size
            tall = max(w, h) >= 1.8 * min(w, h)
            limit = SCREENSHOT_SEND_PIXELS if (is_png or tall) else PHOTO_SEND_PIXELS
            if w * h > limit:
                scale = (limit / float(w * h)) ** 0.5
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            return buf.getvalue()
    except Exception as exc:
        log.info("vision.shrink skipped %s: %s", os.path.basename(path), exc)
        return b""


def _data_url(media_row: AiMedia) -> str:
    small = _shrunk_jpeg(media_row.local_path)
    if small:
        return "data:image/jpeg;base64," + base64.b64encode(small).decode("ascii")
    with open(media_row.local_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    mime = (media_row.mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    return f"data:{mime};base64,{b64}"


def extract_text_from_image(
    db: Session,
    conv: AiConversation,
    media_row: AiMedia,
    prompt: str = _OCR_PROMPT,
    cfg: dict | None = None,
) -> str:
    """OCR a document image via Z.AI glm-4.6v-flash. Returns text or "".

    Never raises. Side-effect free — caller persists extracted_text/extract_kind.
    Pass `cfg` when calling from a worker thread (the db session is not thread-safe).
    """
    cfg = cfg if cfg is not None else resolve_media_config(db)
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
    if not content and groq_key and len(data_url) * 3 // 4 <= GROQ_MAX_IMAGE_BYTES:
        # Groq's per-minute token budget is small: one image at a time.
        with _GROQ_GATE:
            content = _vision_chat(
                (cfg.get("groq_api_base") or "").rstrip("/"), groq_key,
                cfg.get("groq_vision_model") or "qwen/qwen3.8-27b", prompt, data_url,
                {"reasoning_effort": "none"},
            )
    return content


def _retry_after(body: str) -> float:
    """Seconds Groq asks us to wait ("Please try again in 5.12s"), capped."""
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", body)
    if not m:
        return GROQ_RETRY_SECONDS
    wait = int(m.group(1) or 0) * 60 + float(m.group(2))
    return min(max(wait + 0.5, 1.0), GROQ_MAX_RETRY_SECONDS)


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
    groq = "groq" in api_base
    tries = 2 if groq else ZAI_TRIES
    try:
        with httpx.Client(timeout=20.0) as client:
            for attempt in range(tries):
                resp = client.post(api_base + "/chat/completions", headers=headers, json=body)
                text = resp.text or ""
                # A spent daily quota or an empty balance does not come back in seconds.
                if resp.status_code != 429 or attempt == tries - 1 or "per day" in text or '"1113"' in text:
                    break
                time.sleep(_retry_after(text) if groq else ZAI_RETRY_SECONDS * (attempt + 1))
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
    "Study the whole image carefully: badges, grille/cabin logos, model numbers on doors or bumpers, "
    "stickers, number plate and every printed label/value.\n"
    "Rules: brand = the vehicle MAKER only (Tata, Ashok Leyland, Eicher, BharatBenz, Mahindra, Volvo, "
    "Scania, JCB, Tata Hitachi, Hyundai, Komatsu, Caterpillar, L&T, SANY, Kobelco, ACE, Escorts, "
    "Swaraj, Sonalika, Force, SML Isuzu...). model = the series or number on the badge (e.g. Signa "
    "2825.K, Prima 3530, LPT 1613, 1109, Ecomet 1615, Pro 2049, 3DX). Ignore phone camera / app "
    "watermarks (e.g. 'Shot on ...', POVA, Redmi, vivo, OPPO, realme, Samsung, timestamps) and "
    "dealer phone numbers — never use them as brand, model or place. "
    "Only an OUTSIDE view of a tall van-shaped minibus with a flat sloping nose and a Force badge is a "
    "Force Traveller (category Bus). A photo taken INSIDE a cabin or body (seats, bed, roof, dashboard, "
    "garlands) does not show the vehicle type: leave category, brand and model out. "
    "If no maker logo or badge is readable, leave brand and model out instead of guessing from the "
    "body shape.\n"
    "Reply with ONLY one JSON object, omitting keys you cannot read: "
    '"image_type" (vehicle_photo, rc_document, app_screenshot, insurance, other), '
    '"category" (one of Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, '
    'Excavator, Grader, Crusher, Bus, Tractor, Road Roller, Trailer, Transit Mixer), '
    '"brand", "model", "manufacturing_year", "registration_number", "vehicle_class", '
    '"registration_date", "registering_authority", "fuel", "emission_norm", "hypothecated", '
    '"insurance_valid_upto", "fitness_valid_upto", "tax_valid_upto", "puc_valid_upto", '
    '"permit_valid_upto", "national_permit_valid_upto", '
    '"colour", "body_type" (e.g. tipper body, open body, container, trailer, bucket), '
    '"tyres" (count of wheels/tyres visible or implied by axles), "axles", "cabin" (day cab, '
    'sleeper, AC...), "condition" (short, e.g. good paint, rust on body, tyres worn), '
    '"details" (list of up to 8 short English facts seen in the image that a buyer would want, '
    "e.g. 'Rear tipper body with hydraulic ram', 'Front bumper slightly dented'). "
    "Copy printed values exactly. Never guess a value you cannot see."
)

VISION_FILL_KEYS = ("category", "brand", "model", "year", "state", "city", "fuel")
MAX_VISION_READS_PER_CARD = 5
BURST_READ_WORKERS = 2
# Also picks up photos whose read failed (rate limit) on the next photo turn.
BURST_WINDOW_SECONDS = 600

_WATERMARK = re.compile(
    r"\b(?:pova|redmi|vivo|oppo|realme|samsung|galaxy|iphone|oneplus|tecno|infinix|poco|xiaomi|"
    r"motorola|moto|nokia|honor|itel|lava|shot\s*on|camera|ai\s*camera|\d+g)\b",
    re.I,
)
VISUAL_KEYS = ("colour", "body_type", "tyres", "axles", "cabin", "condition")

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


def read_image(db: Session, conv: AiConversation, media_row: AiMedia, cfg: dict | None = None) -> dict:
    """Raw JSON the vision model read from a vehicle photo or vehicle document ({} on failure)."""
    if cfg is None:
        raw = extract_text_from_image(db, conv, media_row, prompt=_VEHICLE_PROMPT)
    else:
        raw = extract_text_from_image(db, conv, media_row, prompt=_VEHICLE_PROMPT, cfg=cfg)
    return parse_read(raw)


def parse_read(raw: str) -> dict:
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
    from .data_filteration import canonical_brand, cv_model

    brand = _clean(data.get("brand"))
    model = _clean(data.get("model"))
    if _WATERMARK.search(brand):
        brand = ""
    if _WATERMARK.search(model):
        model = ""
    series = cv_model(brand) if brand and not canonical_brand(brand) else None
    if series:
        brand, model = series[0], model or series[1]
    if brand:
        out["brand"] = canonical_brand(brand) or extract_fields([brand]).get("brand") or brand.title()[:40]
    if model and shown_model(out.get("brand") or brand, model):
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


# Indian plate after stripping spaces/hyphens: MH12AB1234 / BR28G3913 / DL1CAB1234
_INDIAN_PLATE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$", re.I)


def looks_like_registration_number(value: str) -> bool:
    """True only for plate-like text — drop OCR junk like '14 chki passing 2021'."""
    raw = _clean(value)
    if not raw or len(raw) > 20:
        return False
    compact = re.sub(r"[\s\-./]", "", raw).upper()
    if not _INDIAN_PLATE.match(compact):
        return False
    # Must have at least one letter block + digits (not pure model year text)
    return bool(re.search(r"[A-Z]", compact) and re.search(r"\d", compact))


def document_facts(data: dict) -> dict:
    out: dict = {}
    for k in DOC_FACT_KEYS:
        v = _clean(data.get(k))
        if not v:
            continue
        if k == "registration_number" and not looks_like_registration_number(v):
            continue
        out[k] = v[:60]
    return out


def extract_vehicle_fields(db: Session, conv: AiConversation, media_row: AiMedia) -> dict:
    """Listing fields read from a vehicle photo or document ({} when nothing reliable)."""
    return listing_fields(read_image(db, conv, media_row))


def fill_listing_from_photo(
    db: Session, conv: AiConversation, media_row: AiMedia, is_document: bool = False,
) -> dict:
    """Fill only blank card fields from a photo / RC / Vahan screenshot. Returns what was filled.

    Vehicle photos are capped per card; images the sender labels as documents are always read.
    """
    if not is_document and (media_row.extract_kind or _reads_left(db, conv, media_row.draft_id) <= 0):
        return {}
    return _apply_read(conv, media_row, read_image(db, conv, media_row))


def photo_guessed_keys(conv: AiConversation, payload: dict) -> set[str]:
    """Card fields that still hold the value a photo read guessed (the user never typed them)."""
    guessed = payload.get("vision_filled") if isinstance(payload.get("vision_filled"), dict) else {}
    if not conv.draft_id or guessed.get("draft") != conv.draft_id:
        return set()
    return {
        k for k, v in (guessed.get("fields") or {}).items()
        if str(payload.get(k) or "").strip().lower() == str(v or "").strip().lower() != ""
    }


def _unread_images(db: Session, conv: AiConversation, draft_id):
    return db.query(AiMedia).filter(
        AiMedia.conversation_id == conv.id,
        AiMedia.draft_id == draft_id,
        AiMedia.kind == "image",
        AiMedia.extract_kind == "",
        AiMedia.local_path != "",
    )


def newest_unread_image(db: Session, conv: AiConversation) -> AiMedia | None:
    """Latest not-yet-read photo of the open card (the turn's own photo may have been rejected)."""
    if not conv.draft_id:
        return None
    return _unread_images(db, conv, conv.draft_id).order_by(AiMedia.id.desc()).first()


def recent_unread_image(db: Session, conv: AiConversation) -> AiMedia | None:
    """Newest unread photo of the open card from the current burst."""
    from datetime import datetime, timedelta

    if not conv.draft_id:
        return None
    return (
        _unread_images(db, conv, conv.draft_id)
        .filter(AiMedia.created_at >= datetime.utcnow() - timedelta(seconds=BURST_WINDOW_SECONDS))
        .order_by(AiMedia.id.desc())
        .first()
    )


def read_card_images(db: Session, conv: AiConversation) -> list[int]:
    """"Photo padh ke detail do": read this card's unread photos now. Returns ids read."""
    from .media_config import vision_enabled
    from .tools import _payload

    if not vision_enabled(db) or (_payload(conv).get("intent") or "").upper() == "BUY":
        return []
    row = newest_unread_image(db, conv)
    if row is None:
        return []
    try:
        return fill_listing_from_burst(db, conv, row, window_seconds=None)[1]
    except Exception:
        log.exception("vision.read_card_images failed")
        return []


# One deferred re-read per conversation so a 6-photo burst does not spawn 6 threads.
# Value is (started_at, draft_id_or_0) so a post/new-card can cancel stale followups.
_photo_followups: dict[int, tuple[float, int]] = {}
_PHOTO_FOLLOWUP_LOCK = threading.Lock()
_PHOTO_FOLLOWUP_DELAYS = (12.0, 28.0, 45.0)
_SUBMITTED_STATUSES = frozenset({
    "READY_FOR_REVIEW", "PENDING_REVIEW", "POSTED", "APPROVED", "LIVE", "PUBLISHED",
})


def cancel_photo_reread(conv_id: int | None) -> None:
    """Stop a deferred photo report so it cannot fire after the listing is already posted."""
    if not conv_id:
        return
    with _PHOTO_FOLLOWUP_LOCK:
        _photo_followups.pop(int(conv_id), None)


def schedule_photo_reread(conv_id: int, lang: str = "hinglish", draft_id: int | None = None) -> bool:
    """After a busy vision turn, keep retrying unread photos and WhatsApp the report — no 'detail do'."""
    now = time.time()
    draft_key = int(draft_id or 0)
    with _PHOTO_FOLLOWUP_LOCK:
        started, _ = _photo_followups.get(conv_id) or (0.0, 0)
        if now - started < 90:
            return False
        _photo_followups[conv_id] = (now, draft_key)

    def _work() -> None:
        from ..database import SessionLocal
        from ..services import get_or_create_settings, send_whatsapp_text, store_chat
        from .confirm import card_submitted
        from .i18n import t
        from .office_mode import decorate_office_reply
        from .tools import _payload, _write_payload

        try:
            for wait in _PHOTO_FOLLOWUP_DELAYS:
                time.sleep(wait)
                with _PHOTO_FOLLOWUP_LOCK:
                    if conv_id not in _photo_followups:
                        return  # cancelled after submit / new card
                db = SessionLocal()
                try:
                    conv = db.get(AiConversation, conv_id)
                    if conv is None or not conv.draft_id:
                        return
                    # Stale: user posted this card, or moved to another draft.
                    if draft_key and int(conv.draft_id) != draft_key:
                        return
                    if card_submitted(db, conv):
                        return
                    pl = _payload(conv)
                    if pl.get("customer_confirmed") or (
                        str(pl.get("listing_status") or "").upper() in _SUBMITTED_STATUSES
                    ):
                        return
                    if newest_unread_image(db, conv) is None:
                        return
                    read_ids = read_card_images(db, conv)
                    if not read_ids:
                        continue
                    report = image_report(db, conv, lang, media_ids=read_ids) or card_details(db, conv, lang)
                    if not report:
                        continue
                    # Re-check after the (slow) vision read — YES may have posted meanwhile.
                    if card_submitted(db, conv) or _payload(conv).get("customer_confirmed"):
                        return
                    body = report
                    place_note = ""
                    try:
                        from .confirm import no_place_note

                        place_note = no_place_note(_payload(conv), lang)
                    except Exception:
                        pass
                    if place_note:
                        body = f"{body}\n\n{place_note}"
                    body = f"{body}\n\n{t(lang, 'confirm_ask')}"
                    body = decorate_office_reply(db, conv, body)
                    meta = get_or_create_settings(db)
                    result = send_whatsapp_text(meta, conv.mobile, body)
                    store_chat(
                        db,
                        wamid=result.get("wamid") or f"vision.{conv.id}.{int(time.time())}",
                        conversation_id=conv.conversation_id or f"CONV_{conv.mobile}",
                        from_mobile=meta.phone_number_id or "infradealer",
                        from_name="InfraDealer AI",
                        to_mobile=conv.mobile,
                        direction="outbound",
                        body=body,
                        status="sent",
                        unread=False,
                    )
                    pl = _payload(conv)
                    pl.pop("photo_reread_pending", None)
                    _write_payload(conv, pl)
                    db.commit()
                    log.info(
                        "vision.followup_sent mobile=***%s images=%d",
                        (conv.mobile or "")[-4:],
                        len(read_ids),
                    )
                    return
                except Exception:
                    log.exception("vision.followup attempt failed conv=%s", conv_id)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                finally:
                    db.close()
        finally:
            with _PHOTO_FOLLOWUP_LOCK:
                _photo_followups.pop(conv_id, None)

    threading.Thread(target=_work, name=f"photo-reread-{conv_id}", daemon=True).start()
    return True


def fill_listing_from_burst(
    db: Session, conv: AiConversation, media_row: AiMedia, is_document: bool = False,
    window_seconds: int | None = BURST_WINDOW_SECONDS,
) -> tuple[dict, list[int]]:
    """Read this image plus the unread photos sent with it (in parallel). Returns (filled, ids read)."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    rows = [media_row]
    q = _unread_images(db, conv, media_row.draft_id).filter(AiMedia.id != media_row.id)
    if window_seconds:
        q = q.filter(AiMedia.created_at >= datetime.utcnow() - timedelta(seconds=window_seconds))
    rows += q.order_by(AiMedia.id.desc()).limit(MAX_VISION_READS_PER_CARD - 1).all()
    if media_row.extract_kind and not is_document:
        rows = rows[1:]
    # Papers the sender labels as documents are always read; photos share the card cap.
    rows = rows[:_reads_left(db, conv, media_row.draft_id) + (1 if is_document else 0)]
    if not rows:
        return {}, []
    cfg = resolve_media_config(db)
    files = [SimpleNamespace(local_path=r.local_path, mime=r.mime) for r in rows]
    # Free vision tiers rate-limit bursts: more than two calls at once mostly come back 429.
    with ThreadPoolExecutor(max_workers=min(BURST_READ_WORKERS, len(files))) as pool:
        raws = list(pool.map(
            lambda f: extract_text_from_image(None, None, f, prompt=_VEHICLE_PROMPT, cfg=cfg), files,
        ))
    from .tools import _payload

    reads = [(row, parse_read(raw)) for row, raw in zip(rows, raws)]
    main = _main_brand([d for _, d in reads], prefer=_payload(conv).get("brand"))
    # Photos agreeing with the burst's vehicle fill the card first; an odd read
    # (cabin interior, a second vehicle) is kept for the report but fills nothing.
    reads.sort(key=lambda rd: not _same_brand(rd[1], main))
    filled: dict = {}
    read_ids: list[int] = []
    for row, data in reads:
        if data:
            read_ids.append(row.id)
        if data and not _same_brand(data, main):
            row.extracted_text = json.dumps(data, ensure_ascii=False)
            row.extract_kind = "vehicle_fields"
            continue
        filled.update({k: v for k, v in _apply_read(conv, row, data).items() if k not in filled})
    # SessionLocal has autoflush off: the report query must see these reads.
    db.flush()
    return filled, read_ids


def _read_brand(data: dict | None) -> str:
    return _clean(listing_fields(data or {}).get("brand")).lower() if data else ""


def _same_brand(data: dict | None, main: str) -> bool:
    b = _read_brand(data)
    return not b or not main or b == main


def _main_brand(reads: list[dict], prefer=None) -> str:
    """The vehicle these photos show: the card's brand when a photo agrees with it, else the brand
    most reads agree on (ties go to the read with the most details — an exterior shot beats a
    cabin interior). A typed brand no photo shows means the photos were misread, not discarded."""
    score: dict[str, tuple[int, int]] = {}
    for data in reads:
        b = _read_brand(data)
        if not b:
            continue
        n, rich = score.get(b, (0, 0))
        score[b] = (n + 1, max(rich, sum(1 for k, v in data.items() if k != "details" and _clean(v))))
    want = _clean(prefer).lower()
    if want in score:
        return want
    return max(score, key=score.get) if score else ""


def _reads_left(db: Session, conv: AiConversation, draft_id) -> int:
    """Vision reads still allowed on this card. Only photos actually read count — a rate-limited
    attempt stores nothing, so it is retried on the next photo or "detail do"."""
    if not draft_id:
        return MAX_VISION_READS_PER_CARD
    done = db.query(AiMedia).filter(
        AiMedia.conversation_id == conv.id,
        AiMedia.draft_id == draft_id,
        AiMedia.extract_kind.in_(["vehicle_fields", "document_fields"]),
    ).count()
    return max(0, MAX_VISION_READS_PER_CARD - done)


def vision_cap_left(db: Session, conv: AiConversation) -> bool:
    return _reads_left(db, conv, conv.draft_id) > 0


def _apply_read(conv: AiConversation, media_row: AiMedia, data: dict) -> dict:
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    missing = [k for k in VISION_FILL_KEYS if payload.get(k) in (None, "", [], {})]
    found = listing_fields(data)
    facts = document_facts(data)
    filled = {k: v for k, v in found.items() if k in missing}
    if filled or facts:
        payload = _payload(conv)
        for k, v in filled.items():
            if payload.get(k) in (None, "", [], {}):
                payload[k] = v
        if filled:
            guessed = payload.get("vision_filled") if isinstance(payload.get("vision_filled"), dict) else {}
            if guessed.get("draft") != conv.draft_id:
                guessed = {"draft": conv.draft_id, "fields": {}}
            guessed["fields"] = {**filled, **(guessed.get("fields") or {})}
            payload["vision_filled"] = guessed
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


_DETAIL_ASK = re.compile(
    r"(?:image|photo|pic|picture|tasveer|foto|फोटो|इमेज|तस्वीर)\S*.{0,40}?"
    r"(?:read|padh|parh|detail|details|batao|bataiye|bata\s*do|nikalo|nikal\w*|dekho|dekh\s*kar|info|jankari|जानकारी|पढ़|बताओ)"
    r"|(?:read|padh|parh|detail|details|jankari|पढ़).{0,25}(?:image|photo|pic|tasveer|फोटो|इमेज)",
    re.I,
)

_REPORT_LABELS = {
    "hinglish": {
        "title": "📷 *Photo se mili jankari*", "seen": "*Photo me dikha:*",
        "category": "Category", "brand": "Brand", "model": "Model", "year": "Year", "fuel": "Fuel",
        "place": "RTO / Jagah", "registration_number": "Gaadi number", "vehicle_class": "Vehicle class",
        "registration_date": "Registration date", "emission_norm": "Emission norm",
        "hypothecated": "Loan / hypothecation", "insurance_valid_upto": "Insurance valid till",
        "fitness_valid_upto": "Fitness valid till", "tax_valid_upto": "Tax valid till",
        "puc_valid_upto": "PUC valid till", "permit_valid_upto": "Permit valid till",
        "national_permit_valid_upto": "National permit till", "colour": "Rang", "body_type": "Body",
        "tyres": "Tyre", "axles": "Axle", "cabin": "Cabin", "condition": "Haalat",
        "card_title": "📋 *Listing details*", "updated": "update hua", "km": "KM", "hours": "Hours",
        "price": "Price", "description": "Details",
    },
    "hi": {
        "title": "📷 *फोटो से मिली जानकारी*", "seen": "*फोटो में दिखा:*",
        "category": "कैटेगरी", "brand": "ब्रांड", "model": "मॉडल", "year": "साल", "fuel": "फ्यूल",
        "place": "RTO / जगह", "registration_number": "गाड़ी नंबर", "vehicle_class": "वाहन क्लास",
        "registration_date": "रजिस्ट्रेशन तारीख", "emission_norm": "एमिशन नॉर्म",
        "hypothecated": "लोन / हाइपोथिकेशन", "insurance_valid_upto": "इंश्योरेंस वैध तक",
        "fitness_valid_upto": "फिटनेस वैध तक", "tax_valid_upto": "टैक्स वैध तक",
        "puc_valid_upto": "PUC वैध तक", "permit_valid_upto": "परमिट वैध तक",
        "national_permit_valid_upto": "नेशनल परमिट तक", "colour": "रंग", "body_type": "बॉडी",
        "tyres": "टायर", "axles": "एक्सल", "cabin": "केबिन", "condition": "हालत",
        "card_title": "📋 *लिस्टिंग डिटेल*", "updated": "अपडेट हुआ", "km": "KM", "hours": "घंटे",
        "price": "कीमत", "description": "विवरण",
    },
    "en": {
        "title": "📷 *Details read from the photo*", "seen": "*Seen in the photo:*",
        "category": "Category", "brand": "Brand", "model": "Model", "year": "Year", "fuel": "Fuel",
        "place": "RTO / Place", "registration_number": "Registration no.", "vehicle_class": "Vehicle class",
        "registration_date": "Registration date", "emission_norm": "Emission norm",
        "hypothecated": "Loan / hypothecation", "insurance_valid_upto": "Insurance valid till",
        "fitness_valid_upto": "Fitness valid till", "tax_valid_upto": "Tax valid till",
        "puc_valid_upto": "PUC valid till", "permit_valid_upto": "Permit valid till",
        "national_permit_valid_upto": "National permit till", "colour": "Colour", "body_type": "Body",
        "tyres": "Tyres", "axles": "Axles", "cabin": "Cabin", "condition": "Condition",
        "card_title": "📋 *Listing details*", "updated": "updated", "km": "KM", "hours": "Hours",
        "price": "Price", "description": "Details",
    },
}


_PRIVATE_DETAIL = re.compile(
    r"owner|father|husband|s/o|d/o|w/o|mobile|phone|contact|address|मालिक|पिता|\d{10}|"
    r"chassis|engine\s*no|engine\s*number",
    re.I,
)


def wants_image_details(text: str) -> bool:
    return bool(_DETAIL_ASK.search(text or ""))


_CARD_DETAIL_WORD = re.compile(r"\b(?:detail\w*|detial\w*|deatil\w*|detal\w*|jaa?nkari|info)\b|डिटेल|जानकारी", re.I)
_CARD_DETAIL_VERB = re.compile(
    r"\b(?:bata\w*|dikha\w*|show|send|bhej\w*|chahiye|chaiye|do|dijiye|dena|nikal\w*|mili|mila|kaha|kahan|kya)\b"
    r"|बताओ|बताइए|दिखाओ|चाहिए|दो",
    re.I,
)
_CARD_DETAIL_NOT = re.compile(
    r"\b(?:account|acount|customer|grahak|balance|tokens?|wallet|khata|listings?|profile|"
    r"sahi|shi|galat|theek|thik|correct|wrong|change|badal\w*|update)\b",
    re.I,
)


def wants_card_details(text: str) -> bool:
    """A bare "details batao / details nahi mili" — about the open card, not the account."""
    msg = text or ""
    return bool(
        _CARD_DETAIL_WORD.search(msg) and _CARD_DETAIL_VERB.search(msg)
        and not _CARD_DETAIL_NOT.search(msg) and not re.search(r"\d", msg) and len(msg.split()) <= 8
    )


def card_has_photos(db: Session, conv: AiConversation) -> bool:
    if not conv.draft_id:
        return False
    return db.query(AiMedia.id).filter(
        AiMedia.conversation_id == conv.id,
        AiMedia.draft_id == conv.draft_id,
        AiMedia.kind == "image",
    ).first() is not None


def _card_reads(db: Session, conv: AiConversation, media_ids: list[int] | None = None) -> list[dict]:
    """Reads of the given images, else of the latest photo burst (one vehicle, not the whole draft)."""
    from datetime import timedelta

    q = db.query(AiMedia).filter(
        AiMedia.conversation_id == conv.id,
        AiMedia.extract_kind.in_(["vehicle_fields", "document_fields"]),
    )
    if media_ids is not None:
        rows = q.filter(AiMedia.id.in_(media_ids or [-1])).order_by(AiMedia.id.desc()).all()
    else:
        if conv.draft_id:
            # Only this card's photos — an older card's reads are another vehicle.
            q = q.filter(AiMedia.draft_id == conv.draft_id)
        latest = q.order_by(AiMedia.id.desc()).first()
        if latest is None:
            return []
        q = q.filter(AiMedia.draft_id == latest.draft_id)
        if latest.created_at is not None:
            q = q.filter(AiMedia.created_at >= latest.created_at - timedelta(seconds=BURST_WINDOW_SECONDS))
        rows = q.order_by(AiMedia.id.desc()).limit(MAX_VISION_READS_PER_CARD).all()
    from .tools import _payload

    datas = [d for d in (parse_read(row.extracted_text) for row in rows) if d]
    # A photo of another vehicle (or a misread interior) in the same burst must not mix into this report.
    main = _main_brand(datas, prefer=_payload(conv).get("brand"))
    return [d for d in datas if _same_brand(d, main)]


_NA = re.compile(r"^(n/?a|na|none|null|unknown|not\s+(visible|available|clear)|-+)$", re.I)


def _labels(lang: str) -> dict:
    return _REPORT_LABELS.get(lang) or _REPORT_LABELS["hi" if lang == "hi" else "hinglish"]


def _merge_reads(reads: list[dict]) -> tuple[dict, list[str]]:
    """Newest read wins per key; free-text details de-duplicated, private / N/A ones dropped."""
    merged: dict = {}
    details: list[str] = []
    for data in reads:
        for k, v in data.items():
            if k == "details":
                for d in v if isinstance(v, list) else [v]:
                    d = _clean(d)[:90]
                    if _PRIVATE_DETAIL.search(d) or _NA.match(d) or re.search(r":\s*(n/?a|none|unknown)\s*$", d, re.I):
                        continue
                    if d and d.lower() not in {x.lower() for x in details}:
                        details.append(d)
            elif k not in merged and _clean(v) and not _NA.match(_clean(v)) and not isinstance(v, (list, dict)):
                merged[k] = v
    return merged, details


def _drop_misread_brand(details: list[str], merged: dict, typed_brand) -> list[str]:
    """Photo lines naming the brand vision guessed, when the seller typed a different one."""
    seen = _clean(listing_fields(merged).get("brand"))
    typed = _clean(typed_brand)
    if not seen or not typed or seen.lower() == typed.lower():
        return details
    word = re.compile(rf"\b{re.escape(seen)}\b", re.I)
    return [d for d in details if not word.search(d)]


def _report_text(title: str, rows: list[tuple[str, object]], details: list[str], labels: dict,
                 updated: set | frozenset = frozenset()) -> str:
    lines = [title]
    lines += [
        f"• {labels[k]}: {v}" + (f" ✏️ _{labels['updated']}_" if k in updated else "")
        for k, v in rows if v not in (None, "")
    ]
    if details:
        lines += ["", labels["seen"]] + [f"• {d}" for d in details[:8]]
    return "\n".join(lines) if len(lines) > 1 else ""


def image_report(
    db: Session, conv: AiConversation, lang: str = "hinglish", media_ids: list[int] | None = None,
) -> str:
    """WhatsApp-formatted summary of what was read from the latest images ("" if none)."""
    reads = _card_reads(db, conv, media_ids)
    if not reads:
        return ""
    from .tools import _payload

    labels = _labels(lang)
    merged, details = _merge_reads(reads)
    fields = listing_fields(merged)
    # What the seller typed beats a guess from the photo (vision mixes up look-alike bodies).
    typed = _payload(conv)
    details = _drop_misread_brand(details, merged, typed.get("brand"))
    for k in ("category", "brand", "model", "year"):
        if typed.get(k) not in (None, "", [], {}):
            fields[k] = typed[k]
    place = ", ".join(x for x in (fields.get("city"), fields.get("state")) if x)
    rows = [
        ("category", fields.get("category")), ("brand", fields.get("brand")),
        ("model", shown_model(fields.get("brand"), fields.get("model"))), ("year", fields.get("year")),
        ("fuel", fields.get("fuel")),
        ("place", place),
    ]
    rows += [(k, _clean(merged.get(k))[:60]) for k in DOC_FACT_KEYS if k != "registering_authority"]
    rows += [(k, _clean(merged.get(k))[:60]) for k in VISUAL_KEYS]
    return _report_text(labels["title"], rows, details, labels)


def _draft_reads(db: Session, conv: AiConversation, draft_id, payload: dict) -> list[dict]:
    """This card's photo/paper reads, minus odd photos of a different vehicle than the rest."""
    if not draft_id:
        return []
    rows = (
        db.query(AiMedia)
        .filter(
            AiMedia.conversation_id == conv.id,
            AiMedia.draft_id == draft_id,
            AiMedia.extract_kind.in_(["vehicle_fields", "document_fields"]),
        )
        .order_by(AiMedia.id.desc())
        .limit(MAX_VISION_READS_PER_CARD)
        .all()
    )
    datas = [d for d in (parse_read(row.extracted_text) for row in rows) if d]
    main = _main_brand(datas, prefer=payload.get("brand"))
    return [d for d in datas if _same_brand(d, main)]


# Background of the photo, not the vehicle.
_SCENE_DETAIL = re.compile(r"\bparked\b|\bsurface\b|\bbackground\b|\bsky\b|\bstanding\b|\bground\b|\bwatermark\b", re.I)
_PUBLIC_DOC_KEYS = tuple(k for k in DOC_FACT_KEYS if k not in {"registration_number", "hypothecated"})


def public_photo_facts(db: Session, conv: AiConversation, draft_id, payload: dict) -> list[str]:
    """Website-safe 'Label: value' lines read from this card's photos (no plate/owner/loan)."""
    merged, details = _merge_reads(_draft_reads(db, conv, draft_id, payload))
    details = _drop_misread_brand(details, merged, payload.get("brand"))
    labels = _REPORT_LABELS["en"]
    docs = payload.get("vehicle_docs") if isinstance(payload.get("vehicle_docs"), dict) else {}
    lines = []
    for k in VISUAL_KEYS:
        v = _clean(merged.get(k))[:60]
        if v and not (k == "condition" and payload.get("condition")):
            lines.append(f"{labels[k]}: {v}")
    for k in _PUBLIC_DOC_KEYS:
        v = _clean(merged.get(k))[:60]
        if v and not docs.get(k) and k in labels:
            lines.append(f"{labels[k]}: {v}")
    lines += [d for d in details if not _PRIVATE_DETAIL.search(d) and not _SCENE_DETAIL.search(d)][:8]
    return lines


def card_details(
    db: Session, conv: AiConversation, lang: str = "hinglish", updated: set | frozenset = frozenset(),
) -> str:
    """Full card: what the user typed (wins) + everything read from this card's photos/papers."""
    from .tools import _payload

    payload = _payload(conv)
    labels = _labels(lang)
    merged, details = _merge_reads(_draft_reads(db, conv, conv.draft_id, payload))
    details = _drop_misread_brand(details, merged, payload.get("brand"))
    seen = listing_fields(merged)
    docs = payload.get("vehicle_docs") if isinstance(payload.get("vehicle_docs"), dict) else {}

    def pick(key):
        v = payload.get(key)
        return v if v not in (None, "", [], {}) else seen.get(key)

    place = ", ".join(str(x) for x in dict.fromkeys(x for x in (pick("city"), pick("state")) if x))
    rows = [
        ("category", pick("category")), ("brand", pick("brand")), ("model", shown_model(pick("brand"), pick("model"))),
        ("year", pick("year")), ("fuel", pick("fuel")), ("place", place),
        ("km", payload.get("running_km")), ("hours", payload.get("operating_hours")),
        ("price", payload.get("expected_price")), ("description", _clean(payload.get("description"))[:200]),
    ]
    rows += [
        (k, _clean(docs.get(k) or merged.get(k))[:60]) for k in DOC_FACT_KEYS if k != "registering_authority"
    ]
    rows += [(k, _clean(payload.get(k) if k == "condition" and payload.get(k) else merged.get(k))[:60])
             for k in VISUAL_KEYS]
    card = payload.get("active_card_id") or ""
    title = labels["card_title"] + (f" — {card}" if card else "")
    marks = {"place" if k in {"city", "state", "location"} else
             {"running_km": "km", "operating_hours": "hours", "expected_price": "price"}.get(k, k) for k in updated}
    # An edit confirmation shows the fields; the photo observations went out with the photo report.
    return _report_text(title, rows, [] if updated else details, labels, marks)


def shown_model(brand, model) -> str:
    """Model worth showing: not the brand again ("JCB JCB") nor a cut-off read of it ("ATA" for TATA)."""
    m, b = _clean(model), _clean(brand)
    if not m:
        return ""
    if b and m.lower() in b.lower():
        return ""
    return m
