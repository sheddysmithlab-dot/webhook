"""Build InfraDealer API payloads from AI conversation data.

Maps WhatsApp-collected fields onto the InfraDealer Post Your Ad card:
title, category, make_model, owner_name, year, km, km_unit, price,
description, state, city, area, seller_name, seller_contact, photos (max 5).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import settings
from ..identity import listing_category, parse_listing_price, seller_fields, unique_photo_ids, extract_shared_listing_contact
from ..models import AiConversation, AiListingDraft
from ..ai.schema import listing_title, loads as schema_loads, normalize_vehicle_category
from .crypto import signed_token

POST_AD_CATEGORY = {
    "Truck": "trucks",
    "Dumper": "dumpers",
    "Tipper": "dumpers",
    "Crane": "crane",
    "Poclain": "excavator",
    "Excavator": "excavator",
    "JCB": "jcb",
    "Backhoe Loader": "jcb",
    "Loader": "loaders",
    "Crusher": "crushers",
    "Grader": "road-roller",
    "Other": "others",
}
# Display labels kept for title/description context only
POST_AD_CATEGORY_LABEL = {
    "trucks": "Trucks",
    "dumpers": "Dumpers / Tippers",
    "crane": "Crane",
    "excavator": "Excavators / Pockland",
    "jcb": "JCB / Backhoe Loaders",
    "loaders": "Loaders",
    "crushers": "Crushers",
    "road-roller": "Road Roller",
    "others": "Others",
    "buses": "Buses",
    "tractors": "Tractors",
}
HOUR_CATEGORIES = {
    "JCB", "Excavator", "Poclain", "Loader", "Crane", "Crusher",
    "Grader", "Backhoe Loader",
}
_PHONE_RE = re.compile(r"(?:\+91[\s-]*)?\b\d{10}\b")


def listing_push_status() -> str:
    return "LIVE" if settings.infradealer_auto_publish else "PENDING_REVIEW"


def map_post_ad_category(internal: str) -> str:
    """Map internal AI category → frontend/API slug (trucks, jcb, …)."""
    canon = normalize_vehicle_category(internal) or (internal or "").strip()
    if canon in POST_AD_CATEGORY:
        return POST_AD_CATEGORY[canon]
    raw = str(internal or "").strip().lower()
    # Already a slug or display name
    for slug, label in POST_AD_CATEGORY_LABEL.items():
        if raw == slug or raw == label.lower():
            return slug
    aliases = {
        "truck": "trucks",
        "trucks": "trucks",
        "tipper": "dumpers",
        "dumper": "dumpers",
        "dumpers / tippers": "dumpers",
        "jcb / backhoe loaders": "jcb",
        "excavators / pockland": "excavator",
        "road roller": "road-roller",
    }
    return aliases.get(raw) or POST_AD_CATEGORY.get(canon, "others")


def strip_contact_from_text(text: str) -> str:
    cleaned = _PHONE_RE.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def seller_contact_digits(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def _running_number(raw) -> int | None:
    text = str(raw or "").replace(",", "")
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if n > 0 else None


def _km_and_unit(payload: dict, category: str) -> tuple[int | None, str]:
    hours = _running_number(payload.get("operating_hours"))
    km = _running_number(payload.get("running_km"))
    running = str(payload.get("running") or "")
    running_n = _running_number(running)
    low = running.lower()
    if hours:
        return hours, "Hours"
    if "hour" in low or "hrs" in low:
        return running_n, "Hours"
    if km:
        return km, "KM"
    if running_n and category in HOUR_CATEGORIES:
        return running_n, "Hours"
    if running_n:
        return running_n, "KM"
    return None, "Hours" if category in HOUR_CATEGORIES else "KM"


def _ad_description(payload: dict, confirmed: dict, title: str) -> str:
    raw = str(payload.get("description") or confirmed.get("description") or "").strip()
    if raw:
        return strip_contact_from_text(raw)
    lines = []
    if title:
        lines.append(f"{title} for sale.")
    cond = payload.get("condition") or confirmed.get("condition")
    if cond:
        lines.append(f"Condition: {cond}")
    running = payload.get("running") or payload.get("running_km") or payload.get("operating_hours")
    if running:
        lines.append(f"Running: {running}")
    for label, key in (
        ("Owners", "owners"),
        ("Finance", "finance_amount"),
        ("Finance condition", "finance_condition"),
        ("Tyre", "tyre_percent"),
        ("Work/issues", "work_issues"),
    ):
        val = payload.get(key) or confirmed.get(key)
        if val:
            lines.append(f"{label}: {val}")
    text = "\n".join(lines) or f"{title or 'Equipment'} for sale on InfraDealer."
    return strip_contact_from_text(text)


def _phone_e164(mobile: str) -> str:
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def _year(val) -> int | None:
    text = str(val or "").strip()
    if not text:
        return None
    m = re.search(r"((?:19|20)\d{2})", text)
    if m:
        return int(m.group(1))
    try:
        n = int(text)
    except ValueError:
        return None
    return n if 1900 <= n <= 2100 else None


def media_public_url(media_id: int) -> str:
    token = signed_token("media", str(media_id))
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/integrations/infradealer/media/{int(media_id)}?t={token}"


def _resolve_account_type(payload: dict | None) -> str:
    data = payload if isinstance(payload, dict) else {}
    ctx = data.get("account_context") if isinstance(data.get("account_context"), dict) else {}
    account = ctx.get("account") if isinstance(ctx.get("account"), dict) else {}
    raw = (
        data.get("account_type")
        or data.get("account_label")
        or account.get("type")
        or ctx.get("type")
        or ""
    )
    return str(raw).strip().lower()


def build_listing_payload(
    db,
    conv: AiConversation,
    draft: AiListingDraft | None,
    payload: dict,
    request_id: str,
    infradealer_user_id: str = "",
) -> dict[str, Any]:
    name, _legacy_phone = seller_fields(db, conv, draft, payload)
    confirmed = payload.get("confirmed_json") or payload.get("summary_json") or {}
    if isinstance(confirmed, str):
        try:
            confirmed = json.loads(confirmed)
        except json.JSONDecodeError:
            confirmed = {}
    media_ids = unique_photo_ids(db, conv, payload)[:5]
    media = [
        {
            "media_id": str(mid),
            "type": "image",
            "url": media_public_url(mid),
            "reference": f"whatsapp:{mid}",
        }
        for mid in media_ids
    ]
    price_raw = payload.get("expected_price") or payload.get("budget") or confirmed.get("rate") or ""
    price_num = parse_listing_price(str(price_raw)) if price_raw else 0.0
    year = _year(payload.get("year") or confirmed.get("year"))
    internal_cat = listing_category(
        payload,
        str(payload.get("category") or confirmed.get("category") or "Other"),
        draft.title if draft else "",
    )
    ad_category = map_post_ad_category(internal_cat)
    brand = str(payload.get("brand") or "").strip()
    model = str(payload.get("model") or "").strip()
    make_model = " ".join(p for p in [brand, model] if p).strip() or str(confirmed.get("vehicle") or "")
    title = (draft.title if draft and draft.title else "") or listing_title(payload) or make_model
    km_val, km_unit = _km_and_unit(payload, internal_cat)

    # Account identity = WhatsApp peer (office verified line). Listing contact =
    # number shared in chat. Never publish the office line as seller_contact.
    account_mobile = seller_contact_digits(conv.mobile if conv else "")
    shared_contact = seller_contact_digits(extract_shared_listing_contact(db, conv, payload))
    account_type = _resolve_account_type(payload)
    is_office = account_type in {"office", "staff", "admin"}

    # Prefer any chat-shared number that differs from the channel identity.
    # Office posts must never fall back to the verified office line.
    if shared_contact and shared_contact != account_mobile:
        contact = shared_contact
    elif is_office:
        contact = ""
    else:
        contact = shared_contact or seller_contact_digits(_legacy_phone) or account_mobile

    owner = name or conv.customer_name or "Seller"
    state = payload.get("state") or confirmed.get("state") or ""
    city = payload.get("city") or confirmed.get("city") or ""
    if city and city == state:
        city = ""
    area = payload.get("area") or payload.get("locality") or ""
    description = _ad_description(payload, confirmed, title)
    customer = {
        "name": owner,
        # Always the WhatsApp / InfraDealer account number for account lookup.
        "phone": _phone_e164(account_mobile or conv.mobile),
        "seller_name": owner,
        "seller_contact": contact,
        "account_type": account_type or None,
    }
    if infradealer_user_id:
        customer["user_id"] = infradealer_user_id
    listing = {
        "intent": (payload.get("intent") or conv.intent or "SELL").upper(),
        "title": title[:200],
        "category": ad_category,
        "make_model": make_model[:120],
        "model_name": make_model[:120],
        "owner_name": owner[:120],
        "year": year,
        "manufacturing_year": year,
        "km": km_val,
        "kilometers": km_val,
        "km_unit": km_unit,
        "price": int(price_num) if price_num else None,
        "expected_price": int(price_num) if price_num else price_raw,
        "description": description,
        "state": state,
        "city": city,
        "area": area,
        "locality": area,
        "seller_name": owner[:120],
        "seller_contact": contact,
        "contact_number": contact,
        "brand": brand,
        "model": model,
        "type": str(payload.get("type") or internal_cat).lower().replace(" ", "_"),
        "running_km": km_val if km_unit == "KM" else None,
        "operating_hours": km_val if km_unit == "Hours" else None,
        "condition": payload.get("condition") or "",
        "location": state or payload.get("location") or city,
        "owners": payload.get("owners") or "",
        "finance_amount": payload.get("finance_amount") or "",
        "tyre_percent": payload.get("tyre_percent") or "",
        "finance_condition": payload.get("finance_condition") or "",
        "work_issues": payload.get("work_issues") or "",
        "status": listing_push_status(),
        "auto_publish": settings.infradealer_auto_publish,
        "publish": settings.infradealer_auto_publish,
        "photos": [{"url": item["url"], "media_id": item["media_id"]} for item in media],
    }
    listing = {k: v for k, v in listing.items() if v not in (None, "", [])}
    # Keep empty seller_contact visible for office so API can reject clearly
    if is_office and not contact:
        listing["seller_contact"] = ""
        listing["contact_number"] = ""
        customer["seller_contact"] = ""
        customer["account_type"] = account_type or "office"
    body = {
        "request_id": request_id,
        "event": "listing.push",
        "source": "whatsapp_ai",
        "auto_publish": settings.infradealer_auto_publish,
        "customer": customer,
        "listing": listing,
        "media": media,
        "conversation_id": conv.conversation_id,
        "draft_id": draft.id if draft else None,
        "card_id": getattr(draft, "card_id", None) if draft else None,
    }
    return body


def build_account_check_payload(mobile: str, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "account.check",
        "customer": {"phone": _phone_e164(mobile)},
    }


def build_account_create_payload(
    name: str,
    mobile: str,
    request_id: str,
    *,
    username: str = "",
    email: str = "",
    password: str = "",
) -> dict[str, Any]:
    customer: dict[str, Any] = {"name": name, "phone": _phone_e164(mobile)}
    if username:
        customer["username"] = username
    if email:
        customer["email"] = email
    if password:
        customer["password"] = password
    return {
        "request_id": request_id,
        "event": "account.create",
        "customer": customer,
        "name": name,
        "phone": _phone_e164(mobile),
        "username": username or None,
        "email": email or None,
        "password": password or None,
        "source": "whatsapp_ai",
    }


def build_otp_verify_payload(
    registration_id: str,
    mobile: str,
    otp: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "otp.verify",
        "registration_id": registration_id,
        "phone": _phone_e164(mobile),
        "otp": otp,
    }


def payload_from_conv(conv: AiConversation) -> dict:
    return schema_loads(conv.payload_json)


def build_otp_request_payload(registration_id: str, mobile: str, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "otp.request",
        "registration_id": registration_id,
        "phone": _phone_e164(mobile),
    }


def build_password_reset_request_payload(mobile: str, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "password.reset.request",
        "phone": _phone_e164(mobile),
        "customer": {"phone": _phone_e164(mobile)},
    }


def build_password_reset_confirm_payload(
    mobile: str,
    otp: str,
    new_password: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "password.reset.confirm",
        "phone": _phone_e164(mobile),
        "otp": otp,
        "new_password": new_password,
        "customer": {"phone": _phone_e164(mobile)},
    }


def build_media_payload(local_id, kind: str, mime: str, request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "event": "media.push",
        "media": {
            "local_id": str(local_id),
            "type": kind or "image",
            "mime": mime or "image/jpeg",
            "url": media_public_url(int(local_id)) if str(local_id).isdigit() else "",
            "reference": f"whatsapp:{local_id}",
        },
    }
