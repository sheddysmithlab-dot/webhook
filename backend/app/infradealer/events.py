"""Event types, endpoint mapping, and response classification."""

from __future__ import annotations

import json
import re

DEFAULT_WEBHOOK_BASE = "https://api.infradealer.com/api/v1/webhook"

DEFAULT_EVENT_FLAGS = {
    "account_check": True,
    "account_create": True,
    "otp_request": True,
    "otp_verify": True,
    "password_reset_request": True,
    "password_reset_confirm": True,
    "listing_push": True,
    "media_push": False,
    "profile_update": False,
}

INTERNAL_TO_API = {
    "ACCOUNT_CHECK": "account.check",
    "ACCOUNT_CREATE": "account.create",
    "OTP_REQUEST": "otp.request",
    "OTP_VERIFY": "otp.verify",
    "PASSWORD_RESET_REQUEST": "password.reset.request",
    "PASSWORD_RESET_CONFIRM": "password.reset.confirm",
    "LISTING_PUSH": "listing.push",
    "MEDIA_PUSH": "media.push",
    "PROFILE_UPDATE": "profile.update",
    "CONNECTION_TEST": "connection.test",
    "STATUS": "status",
}

API_PATHS = {
    "connection.test": "/test",
    "account.check": "/account/check",
    "account.create": "/account/create",
    "otp.request": "/otp/request",
    "otp.verify": "/otp/verify",
    "password.reset.request": "/password/reset/request",
    "password.reset.confirm": "/password/reset/confirm",
    "listing.push": "/listing/push",
    "media.push": "/media",
    "profile.update": "/profile/update",
    "status": "/status",
}

API_METHODS = {
    "status": "GET",
}

BUSINESS_CODES = {
    "ACCOUNT_NOT_FOUND",
    "ACCOUNT_BLOCKED",
    "ACCOUNT_EXISTS",
    "INVALID_PHONE",
    "INVALID_API_KEY",
    "INVALID_SIGNATURE",
    "INVALID_PAYLOAD",
    "DUPLICATE_REQUEST",
    "LISTING_VALIDATION_FAILED",
    "TOKEN_INSUFFICIENT",
    "OTP_REQUEST_FAILED",
    "OTP_INVALID",
    "OTP_EXPIRED",
    "OTP_ATTEMPTS_EXCEEDED",
    "ACCOUNT_CREATION_FAILED",
    "OTP_REQUIRED",
    "OTP_PENDING",
    "OTP_SENT",
    "PASSWORD_UPDATED",
    "ACCOUNT_CREATED",
    "ACCOUNT_FOUND",
    "ACCOUNT_REQUIRED",
    "PENDING_REVIEW",
    "LISTING_POSTED",
    "LISTING_PENDING_REVIEW",
    "LISTING_REJECTED",
    "LIVE",
    "POSTED",
    "PUBLISHED",
    "INTERNAL_ERROR",
}

RETRYABLE_HTTP = {502, 503, 504, 408, 429}
NON_RETRY_BUSINESS = BUSINESS_CODES - {
    "ACCOUNT_NOT_FOUND",
    "OTP_REQUIRED",
    "OTP_PENDING",
    "OTP_SENT",
    "ACCOUNT_REQUIRED",
    "PENDING_REVIEW",
    "PASSWORD_UPDATED",
}


def normalize_base_url(raw: str | None) -> str:
    url = re.sub(r"/+$", "", str(raw or "").strip())
    if not url:
        return DEFAULT_WEBHOOK_BASE
    if url.endswith("/api/v1/webhook"):
        return url
    if url.endswith("/api/v1"):
        return url + "/webhook"
    if re.search(r"/api/v1/webhook$", url):
        return url
    if "://" in url and "/webhook" not in url.split("://", 1)[-1]:
        return url + "/api/v1/webhook"
    return url


def endpoint_url(base_url: str, api_event: str) -> str:
    path = API_PATHS.get(api_event, "")
    return f"{normalize_base_url(base_url)}{path}"


def load_event_flags(raw: str | None) -> dict[str, bool]:
    try:
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            out = dict(DEFAULT_EVENT_FLAGS)
            out.update({k: bool(v) for k, v in data.items()})
            return out
    except json.JSONDecodeError:
        pass
    return dict(DEFAULT_EVENT_FLAGS)


def event_enabled(flags: dict[str, bool], event_type: str) -> bool:
    key = event_type.lower().replace(".", "_")
    if key in flags:
        return bool(flags[key])
    alt = INTERNAL_TO_API.get(event_type.upper(), "").replace(".", "_")
    return bool(flags.get(alt, True))


def classify_response(http_status: int, body: dict | None, error: str = "") -> str:
    if error and not http_status:
        low = error.lower()
        if "timeout" in low or "timed out" in low:
            return "NETWORK_ERROR"
        return "NETWORK_ERROR"
    if http_status in RETRYABLE_HTTP:
        return "RATE_LIMIT" if http_status == 429 else "SERVER_ERROR"
    if http_status in {401, 403}:
        return "AUTH_ERROR"
    data = body or {}
    code = str(data.get("code") or data.get("error_code") or "").upper()
    if code in BUSINESS_CODES or data.get("account_found") is False:
        return "BUSINESS_ERROR"
    if http_status == 404:
        return "BUSINESS_ERROR"
    if http_status == 422 or http_status == 400:
        return "VALIDATION_ERROR"
    if http_status >= 500:
        return "SERVER_ERROR"
    if http_status >= 400:
        return "VALIDATION_ERROR"
    if http_status >= 200 and http_status < 300:
        return "SUCCESS"
    return "SERVER_ERROR"


def should_retry(response_class: str, http_status: int, business_code: str = "") -> bool:
    if response_class in {"NETWORK_ERROR", "SERVER_ERROR", "RATE_LIMIT"}:
        return True
    if http_status in RETRYABLE_HTTP:
        return True
    if business_code in {"OTP_INVALID", "OTP_EXPIRED", "OTP_ATTEMPTS_EXCEEDED"}:
        return False
    if business_code in NON_RETRY_BUSINESS:
        return False
    if response_class in {"AUTH_ERROR", "VALIDATION_ERROR", "BUSINESS_ERROR"}:
        return False
    return False


def normalize_callback_event(payload: dict | None) -> str:
    data = payload or {}
    code = str(data.get("code") or data.get("event_code") or "").strip().upper()
    code_map = {
        "LISTING_POSTED": "listing.posted",
        "LISTING_APPROVED": "listing.posted",
        "LISTING_LIVE": "listing.posted",
        "LISTING_REJECTED": "listing.rejected",
        "ACCOUNT_CREATED": "account.created",
    }
    if code in code_map:
        return code_map[code]
    raw = str(data.get("event") or data.get("type") or data.get("callback_event") or "").strip()
    key = raw.lower().replace("_", ".").replace(" ", "")
    aliases = {
        "listing.posted": "listing.posted",
        "listing.approved": "listing.posted",
        "listing.live": "listing.posted",
        "listingposted": "listing.posted",
        "listing.rejected": "listing.rejected",
        "listingrejected": "listing.rejected",
        "account.created": "account.created",
        "accountcreated": "account.created",
    }
    if key in aliases:
        return aliases[key]
    listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
    extra = data.get("data") if isinstance(data.get("data"), dict) else {}
    for src in (listing, extra, data):
        if not isinstance(src, dict):
            continue
        status = str(src.get("status") or src.get("listing_status") or "").strip().upper()
        if status in {"POSTED", "LIVE", "PUBLISHED", "APPROVED", "ACTIVE"}:
            return "listing.posted"
        if status in {"REJECTED", "DECLINED", "DENIED"}:
            return "listing.rejected"
    return key


def listing_public_url(payload: dict | None, listing_id: str = "") -> str:
    data = payload or {}
    listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
    extra = data.get("data") if isinstance(data.get("data"), dict) else {}
    for src in (listing, extra, data):
        if not isinstance(src, dict):
            continue
        for key in ("url", "public_url", "listing_url", "permalink", "link", "share_url", "web_url"):
            val = str(src.get(key) or "").strip()
            if val.startswith("http"):
                # Prefer /listings/ path used by live InfraDealer cards
                if "/listing/" in val and "/listings/" not in val:
                    return val.replace("/listing/", "/listings/", 1)
                return val
    lid = str(listing_id or listing.get("listing_id") or listing.get("id") or "").strip()
    if lid:
        return f"https://infradealer.com/listings/{lid}"
    return ""


def listing_reject_reason(payload: dict | None) -> str:
    data = payload or {}
    listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
    extra = data.get("data") if isinstance(data.get("data"), dict) else {}
    for src in (listing, extra, data):
        if not isinstance(src, dict):
            continue
        for key in (
            "reason",
            "rejection_reason",
            "reject_reason",
            "rejection_message",
            "admin_message",
            "message",
            "comment",
            "notes",
        ):
            val = str(src.get(key) or "").strip()
            if val and key == "message" and val.lower() in {"ok", "success", "listing_rejected"}:
                continue
            if val:
                return val
    return ""
