"""InfraDealer outbound integration — API client, events, outbox."""

from __future__ import annotations

import uuid
from typing import Any

from .service import InfraDealerIntegrationService, get_integration_service


def push_listing(
    *,
    phone: str,
    name: str,
    title: str,
    category: str,
    price: float,
    condition: str = "",
    city: str = "",
    description: str = "",
    ref: str = "",
) -> dict[str, Any] | None:
    """Fire-and-forget listing push used by WhatsApp form / catalog publish_card."""
    from ..config import settings
    from ..database import SessionLocal
    from .payloads import listing_push_status, map_post_ad_category, seller_contact_digits, strip_contact_from_text

    contact = seller_contact_digits(phone)
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    e164 = f"+91{digits[-10:]}" if len(digits) >= 10 else (phone or "")
    owner = (name or "Seller")[:120]
    rid = str(uuid.uuid4())
    ad_category = map_post_ad_category(category or "Other")
    price_int = int(price) if price else None
    listing = {
        "intent": "SELL",
        "title": (title or "Listing")[:200],
        "category": ad_category,
        "owner_name": owner,
        "price": price_int,
        "expected_price": price_int,
        "description": strip_contact_from_text(description or ""),
        "city": city or "",
        "seller_name": owner,
        "seller_contact": contact,
        "contact_number": contact,
        "condition": condition or "",
        "location": city or "",
        "status": listing_push_status(),
        "auto_publish": settings.infradealer_auto_publish,
        "publish": settings.infradealer_auto_publish,
        "ref": ref or "",
    }
    listing = {k: v for k, v in listing.items() if v not in (None, "", [])}
    body: dict[str, Any] = {
        "request_id": rid,
        "event": "listing.push",
        "source": "whatsapp_form",
        "auto_publish": settings.infradealer_auto_publish,
        "customer": {
            "name": owner,
            "phone": e164,
            "seller_name": owner,
            "seller_contact": contact,
        },
        "listing": listing,
        "media": [],
    }

    db = SessionLocal()
    try:
        svc = get_integration_service(db)
        item = svc.enqueue("LISTING_PUSH", body, mobile=contact, request_id=rid)
        if item:
            svc.process_outbox_item(item)
        db.commit()
        return body
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "InfraDealerIntegrationService",
    "get_integration_service",
    "push_listing",
]
