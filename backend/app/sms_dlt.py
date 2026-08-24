"""India DLT SMS delivery for OTP (never WhatsApp).

Supports:
  - msg91  — MSG91 Flow / SendOTP style
  - http   — generic JSON/form POST (custom DLT gateway)
  - log    — dev fallback (logs code; no SMS)

Env (see config.py):
  SMS_PROVIDER, SMS_API_KEY, SMS_API_URL, SMS_SENDER_ID,
  SMS_DLT_TEMPLATE_ID, SMS_DLT_ENTITY_ID, SMS_OTP_TEMPLATE
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("infradealer.sms")


def _digits_msisdn(mobile: str) -> str:
    """Normalize to 91XXXXXXXXXX for India SMS gateways."""
    d = re.sub(r"\D", "", mobile or "")
    if len(d) == 10:
        return "91" + d
    if d.startswith("0") and len(d) == 11:
        return "91" + d[1:]
    if d.startswith("91") and len(d) >= 12:
        return d[:12]
    return d


def _otp_message(code: str) -> str:
    tmpl = (getattr(settings, "sms_otp_template", None) or "").strip()
    if tmpl:
        return tmpl.replace("{otp}", code).replace("{OTP}", code)
    # Keep under typical DLT approved length; exact text must match registered template.
    return f"Your InfraDealer OTP is {code}. Valid for 5 minutes. Do not share with anyone."


def send_dlt_sms(mobile: str, code: str) -> str:
    """Send OTP SMS via configured DLT provider. Returns channel label.

    Never sends WhatsApp. Raises RuntimeError on hard failure when provider
    is configured; returns ``log`` when provider is ``log`` or unset in a way
    that allows local/dev without credentials.
    """
    provider = (getattr(settings, "sms_provider", None) or "log").strip().lower()
    msisdn = _digits_msisdn(mobile)
    if len(msisdn) < 12:
        raise RuntimeError("Invalid mobile for SMS OTP")

    if provider in {"", "log", "none", "off"}:
        log.info("OTP SMS (log-only) mobile=***%s code=%s", msisdn[-4:], code)
        return "log"

    if provider in {"msg91", "msg91_flow"}:
        return _send_msg91(msisdn, code)
    if provider in {"http", "generic", "dlt"}:
        return _send_http(msisdn, code)

    raise RuntimeError(f"Unknown SMS_PROVIDER={provider!r} (use msg91|http|log)")


def _send_msg91(msisdn: str, code: str) -> str:
    api_key = (getattr(settings, "sms_api_key", None) or "").strip()
    if not api_key:
        raise RuntimeError("SMS_API_KEY missing for MSG91")
    template_id = (getattr(settings, "sms_dlt_template_id", None) or "").strip()
    base = (getattr(settings, "sms_api_url", None) or "https://control.msg91.com/api/v5/flow").rstrip("/")
    # Prefer Flow API when template id present
    if template_id:
        url = base if base.endswith("flow") or "/flow" in base else base
        payload: dict[str, Any] = {
            "template_id": template_id,
            "short_url": "0",
            "recipients": [{"mobiles": msisdn, "otp": code, "var": code}],
        }
        headers = {
            "authkey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            log.warning("MSG91 flow HTTP %s %s", resp.status_code, (resp.text or "")[:240])
            raise RuntimeError("SMS OTP send failed (MSG91)")
        log.info("OTP SMS via MSG91 flow mobile=***%s", msisdn[-4:])
        return "sms_msg91"

    # Fallback: simple sendotp endpoint
    url = (getattr(settings, "sms_api_url", None) or "https://control.msg91.com/api/v5/otp").rstrip("/")
    params = {
        "otp": code,
        "mobile": msisdn,
        "otp_expiry": max(1, int(getattr(settings, "otp_ttl_seconds", 300) // 60) or 5),
    }
    if template_id:
        params["template_id"] = template_id
    headers = {"authkey": api_key, "Accept": "application/json"}
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url, params=params, headers=headers)
    if resp.status_code >= 400:
        log.warning("MSG91 otp HTTP %s %s", resp.status_code, (resp.text or "")[:240])
        raise RuntimeError("SMS OTP send failed (MSG91)")
    log.info("OTP SMS via MSG91 otp mobile=***%s", msisdn[-4:])
    return "sms_msg91"


def _send_http(msisdn: str, code: str) -> str:
    """Generic DLT gateway: POST JSON with common field names."""
    url = (getattr(settings, "sms_api_url", None) or "").strip()
    api_key = (getattr(settings, "sms_api_key", None) or "").strip()
    if not url:
        raise RuntimeError("SMS_API_URL missing for http SMS provider")
    sender = (getattr(settings, "sms_sender_id", None) or "").strip()
    template_id = (getattr(settings, "sms_dlt_template_id", None) or "").strip()
    entity_id = (getattr(settings, "sms_dlt_entity_id", None) or "").strip()
    message = _otp_message(code)
    payload: dict[str, Any] = {
        "mobile": msisdn,
        "phone": msisdn,
        "to": msisdn,
        "otp": code,
        "message": message,
        "text": message,
        "sender": sender,
        "sender_id": sender,
        "dlt_template_id": template_id,
        "template_id": template_id,
        "entity_id": entity_id,
        "dlt_entity_id": entity_id,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
        headers["authkey"] = api_key
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json=payload, headers=headers)
    if resp.status_code >= 400:
        log.warning("DLT HTTP SMS %s %s", resp.status_code, (resp.text or "")[:240])
        raise RuntimeError("SMS OTP send failed (http)")
    log.info("OTP SMS via http DLT mobile=***%s", msisdn[-4:])
    return "sms_dlt"
