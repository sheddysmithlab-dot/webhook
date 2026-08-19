"""
InfraDealer API bridge.

When a WhatsApp user submits a product listing (submission), this module
pushes it to the InfraDealer backend via the authenticated webhook API
(/api/v1/webhook/listing/push).  The backend will create a pending listing
and send a callback back here (to /api/v1/integrations/infradealer/callback)
when an admin approves or rejects it.

Environment variables required:
  INFRADEALER_API_URL     – base URL of InfraDealer backend
                            e.g. https://api.infradealer.com
  INFRADEALER_API_KEY     – X-InfraDealer-Key header value
  INFRADEALER_API_SECRET  – HMAC signing secret (ids_…)
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("infradealer.bridge")

TIMEOUT = 20  # seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def _request_id() -> str:
    return secrets.token_hex(16)


def _sign(secret: str, timestamp: int, request_id: str, raw_body: str) -> str:
    payload = f"{timestamp}.{request_id}.{raw_body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _headers(request_id: str, body: str) -> dict[str, str]:
    if not settings.infradealer_api_key or not settings.infradealer_api_secret:
        raise RuntimeError("INFRADEALER_API_KEY aur INFRADEALER_API_SECRET .env mein set karo.")
    ts = int(time.time())
    sig = _sign(settings.infradealer_api_secret, ts, request_id, body)
    return {
        "Content-Type": "application/json",
        "X-InfraDealer-Key": settings.infradealer_api_key,
        "X-InfraDealer-Timestamp": str(ts),
        "X-InfraDealer-Request-ID": request_id,
        "X-InfraDealer-Signature": sig,
    }


def _base_url() -> str:
    return settings.infradealer_api_url.rstrip("/")


# ── public API ───────────────────────────────────────────────────────────────

def push_listing(
    *,
    phone: str,
    name: str,
    title: str,
    category: str,
    price: float,
    condition: str,
    city: str,
    description: str = "",
    media_urls: list[str] | None = None,
    ref: str = "",
) -> dict[str, Any]:
    """
    Push a WhatsApp-sourced listing to InfraDealer for admin review.

    Returns the parsed response dict from InfraDealer or raises RuntimeError.
    """
    if not settings.infradealer_api_url:
        log.info("INFRADEALER_API_URL not set – listing push skipped.")
        return {"skipped": True, "reason": "no_api_url"}

    request_id = ref or _request_id()

    body_dict: dict[str, Any] = {
        "request_id": request_id,
        "customer": {
            "phone": phone,
            "name": name,
        },
        "listing": {
            "title": title,
            "category": category,
            "price": price,
            "condition": condition,
            "location": city,
            "city": city,
            "description": description,
        },
    }
    if media_urls:
        body_dict["media"] = [{"url": u} for u in media_urls]

    raw_body = json.dumps(body_dict, separators=(",", ":"))
    headers = _headers(request_id, raw_body)

    url = f"{_base_url()}/api/v1/webhook/listing/push"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(url, content=raw_body, headers=headers)
        data: dict = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err_msg = data.get("message") or f"HTTP {resp.status_code}"
            log.warning("InfraDealer listing push failed [%s]: %s", resp.status_code, err_msg)
            return {"ok": False, "error": err_msg, "status_code": resp.status_code, "data": data}
        log.info(
            "InfraDealer listing push ok – listing_id=%s listing_number=%s review_url=%s",
            data.get("listing", {}).get("listing_id"),
            data.get("listing", {}).get("listing_number"),
            data.get("admin_review_url"),
        )
        return {"ok": True, "data": data}
    except httpx.RequestError as exc:
        log.error("InfraDealer push network error: %s", exc)
        return {"ok": False, "error": str(exc)}


def push_account_check(phone: str) -> dict[str, Any]:
    """
    Check if an InfraDealer account exists for the given phone.
    Returns the response dict (code == 'ACCOUNT_FOUND' if exists).
    """
    if not settings.infradealer_api_url:
        return {"skipped": True}

    request_id = _request_id()
    body_dict = {"request_id": request_id, "phone": phone}
    raw_body = json.dumps(body_dict, separators=(",", ":"))
    headers = _headers(request_id, raw_body)

    url = f"{_base_url()}/api/v1/webhook/account/check"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(url, content=raw_body, headers=headers)
        return resp.json() if resp.content else {}
    except httpx.RequestError as exc:
        log.error("InfraDealer account check error: %s", exc)
        return {"ok": False, "error": str(exc)}
