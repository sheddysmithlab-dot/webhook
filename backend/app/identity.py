"""Seller identity for listing cards. Never treat Ok / [photo] / OTP / year as name or price."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from .models import AiConversation, AiListingDraft, AiMedia, Chat, Product
from .services import normalize_mobile, valid_mobile

_WEAK_NAME = re.compile(
    r"^(ok+|okay|haan+|han+|ji+|yes|no|hmm+|h+m+|theek|achha|accha|acha|"
    r"photo|photos|bhai|sir|namaste|hi+|hello|hey+|"
    r"\[(?:photo|video|media|document|voice).*\])[\s!.]*$",
    re.I,
)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_PHONE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?([6-9]\d{9})(?!\d)")
TRUCK_MODELS = {
    "407", "709", "909", "1109", "1518", "1613", "1618", "2518", "3118", "3518", "3718", "4018", "4923",
}


def usable_person_name(value: str | None) -> str:
    name = re.sub(r"\s+", " ", (value or "").strip())
    if len(name) < 2 or len(name) > 80:
        return ""
    if _WEAK_NAME.match(name):
        return ""
    if re.fullmatch(r"\d{4,10}", name):
        return ""
    if name.lower() in {"seller", "whatsapp user", "user"}:
        return ""
    return name[:120]


def wa_profile_name(db: Session, conversation_id: str) -> str:
    row = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id, Chat.direction == "inbound", Chat.from_name != "")
        .order_by(Chat.id.desc())
        .first()
    )
    return usable_person_name(row.from_name if row else "")


def extract_contact_mobile(text: str, fallback: str = "") -> str:
    found = ""
    for match in _PHONE.finditer(text or ""):
        num = match.group(1)
        if valid_mobile(num):
            found = num
    if found:
        return found
    fb = normalize_mobile(fallback) if fallback else ""
    return fb if valid_mobile(fb) else ""


def looks_like_price(val: str) -> bool:
    raw = (val or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if _YEAR.match(raw):
        return False
    if re.search(r"lakh|lac|\bl\b|₹|rs\.?|crore|\bcr\b", low):
        return True
    nums = re.findall(r"\d+(?:\.\d+)?", raw)
    if not nums:
        return False
    others = [n for n in nums if not _YEAR.match(n)]
    if not others:
        return False
    if all(re.fullmatch(r"\d{3,4}", n) for n in others):
        return False
    return True


def parse_listing_price(raw: str) -> float:
    text = (raw or "").strip()
    if not looks_like_price(text):
        return 0.0
    low = text.lower()
    nums = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in text.replace(",", ""))
    try:
        price = float(next((p for p in nums.split() if p and not _YEAR.match(p)), "0"))
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return 0.0
    if "lakh" in low or "lac" in low or re.search(r"\b\d+(?:\.\d+)?\s*l\b", low):
        price *= 100000
    elif "crore" in low or re.search(r"\bcr\b", low):
        price *= 10000000
    if 1900 <= price <= 2035:
        return 0.0
    return price


def listing_category(payload: dict, fallback: str = "Other", title: str = "") -> str:
    from .ai.schema import normalize_vehicle_category
    canon = normalize_vehicle_category(payload.get("category") or payload.get("type") or fallback)
    if canon:
        return canon
    cat = str(payload.get("category") or fallback or "Other")
    model = str(payload.get("model") or "").upper().replace(" ", "")
    typ = str(payload.get("type") or "").upper()
    blob = f"{model} {title} {payload.get('brand') or ''}".upper()
    if model in TRUCK_MODELS or typ == "TRUCK" or any(re.search(rf"\b{m}\b", blob) for m in TRUCK_MODELS):
        return "Truck"
    if typ in {"TIPPER", "DUMPER"}:
        return "Tipper" if typ == "TIPPER" else "Dumper"
    if typ in {"TANKER", "TRAILER", "PICKUP", "BUS"}:
        return "Truck"
    return (cat[:80] or "Other")


def unique_photo_ids(db: Session, conv: AiConversation | None, payload: dict | None = None) -> list[int]:
    """Return up to 5 unique local image ids for the active listing draft.

    Always merges payload media_ids with images attached to the current draft so
    late photo uploads are included in marketplace push (not just the first id).
    """
    wanted = []
    if payload:
        wanted = [int(x) for x in (payload.get("media_ids") or []) if str(x).isdigit()]
    rows = []
    if conv:
        rows = (
            db.query(AiMedia)
            .filter(AiMedia.conversation_id == conv.id, AiMedia.kind == "image")
            .order_by(AiMedia.id.asc())
            .all()
        )
    by_id = {row.id: row for row in rows}
    ordered: list[int] = []
    seen_meta: set[str] = set()
    seen_ids: set[int] = set()

    def _add(row: AiMedia | None) -> None:
        if not row or not row.local_path or row.id in seen_ids:
            return
        key = str(row.meta_media_id or row.local_path)
        if key in seen_meta:
            return
        seen_meta.add(key)
        seen_ids.add(row.id)
        ordered.append(row.id)

    for mid in wanted:
        _add(by_id.get(mid))

    draft_id = None
    if conv and conv.draft_id:
        draft_id = conv.draft_id
    elif payload and payload.get("draft_id"):
        try:
            draft_id = int(payload.get("draft_id"))
        except (TypeError, ValueError):
            draft_id = None

    draft_rows = [row for row in rows if draft_id and row.draft_id == draft_id]
    # Prefer draft-scoped images; if none tagged, fall back to all conversation images
    # only when wanted was empty (avoid mixing old listing photos into a new draft).
    extras = draft_rows if draft_rows else ([] if ordered else rows)
    for row in extras:
        if len(ordered) >= 5:
            break
        _add(row)

    return ordered[:5]


def seller_fields(db: Session, conv: AiConversation | None, draft: AiListingDraft | None, payload: dict) -> tuple[str, str]:
    wa_name = ""
    chat_blob = ""
    if conv:
        wa_name = usable_person_name((payload or {}).get("wa_name")) or wa_profile_name(db, conv.conversation_id)
        chats = (
            db.query(Chat)
            .filter(Chat.conversation_id == conv.conversation_id, Chat.direction == "inbound")
            .order_by(Chat.id.asc())
            .all()
        )
        chat_blob = " ".join((c.body or "") for c in chats)
    name = (
        usable_person_name(conv.customer_name if conv else "")
        or usable_person_name((payload or {}).get("customer_name"))
        or wa_name
        or "Seller"
    )
    mobile = extract_contact_mobile(chat_blob, "") or extract_contact_mobile(
        str((payload or {}).get("contact_phone") or ""),
        (draft.mobile if draft else "") or (conv.mobile if conv else ""),
    )
    return name, mobile


def photo_payload(product: Product) -> list[dict]:
    try:
        ids = json.loads(product.photo_ids or "[]")
    except json.JSONDecodeError:
        ids = []
    out = []
    for mid in ids:
        try:
            n = int(mid)
        except (TypeError, ValueError):
            continue
        out.append({"id": n, "url": f"/api/products/{product.id}/photos/{n}"})
    return out


def repair_posted_listings(db: Session) -> None:
    drafts = db.query(AiListingDraft).filter(AiListingDraft.status == "POSTED", AiListingDraft.posted_product_id.isnot(None)).all()
    from .ai.schema import loads

    for draft in drafts:
        prod = db.query(Product).filter(Product.id == draft.posted_product_id).first()
        if not prod:
            continue
        conv = db.query(AiConversation).filter(AiConversation.id == draft.conversation_id).first()
        payload = loads(conv.payload_json) if conv else {}
        name, mobile = seller_fields(db, conv, draft, payload)
        if name:
            prod.seller_name = name[:120]
        if mobile:
            prod.mobile = mobile
        ids = unique_photo_ids(db, conv, payload)
        if ids:
            prod.photo_ids = json.dumps(ids)
        if parse_listing_price(str(prod.price)) <= 0 and 1900 <= float(prod.price or 0) <= 2035:
            prod.price = 0
        prod.category = listing_category(payload, prod.category, draft.title or prod.title)
        if conv and not usable_person_name(conv.customer_name) and name != "Seller":
            conv.customer_name = name
