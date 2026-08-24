"""India DLT SMS delivery for OTP (never WhatsApp).

Supports:
  - textguru — InfraDealer production TextGuru v22.0 + DLT (preferred)
  - msg91    — MSG91 Flow / SendOTP style
  - http     — generic JSON/form POST (custom DLT gateway)
  - log      — dev fallback (logs code; no SMS)

Env (see config.py):
  SMS_PROVIDER, SMS_API_KEY, SMS_API_URL, SMS_SENDER_ID,
  SMS_DLT_TEMPLATE_ID, SMS_DLT_ENTITY_ID, SMS_OTP_TEMPLATE,
  TEXTGURU_USERNAME, TEXTGURU_PASSWORD, TEXTGURU_SENDER_ID,
  TEXTGURU_DLT_TEMPLATE_ID, TEXTGURU_API_URL, TEXTGURU_MESSAGE_TEMPLATE
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

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


def _digits_10(mobile: str) -> str:
    return re.sub(r"\D", "", mobile or "")[-10:]


def _otp_message(code: str) -> str:
    tmpl = (
        (getattr(settings, "textguru_message_template", None) or "").strip()
        or (getattr(settings, "sms_otp_template", None) or "").strip()
    )
    if tmpl:
        return tmpl.replace("{otp}", code).replace("{OTP}", code)
    # Exact DLT-registered InfraDealer template (AREANS).
    return (
        f"Your OTP for InfraDealer is {code}. The OTP is valid for 10 minutes. "
        "Please do not share this OTP with anyone. Regards, AREANS"
    )


def send_dlt_sms(mobile: str, code: str) -> str:
    """Send OTP SMS via configured DLT provider. Returns channel label.

    Never sends WhatsApp. Raises RuntimeError on hard failure when provider
    is configured; returns ``log`` when provider is ``log`` or unset in a way
    that allows local/dev without credentials.
    """
    if getattr(settings, "sms_enabled", True) is False:
        log.warning("SMS_ENABLED=false — OTP not sent mobile=***%s", (mobile or "")[-4:])
        raise RuntimeError("SMS OTP disabled (SMS_ENABLED=false)")

    provider = (getattr(settings, "sms_provider", None) or "log").strip().lower()
    msisdn = _digits_msisdn(mobile)
    if len(msisdn) < 12:
        raise RuntimeError("Invalid mobile for SMS OTP")

    if provider in {"", "log", "none", "off"}:
        log.info("OTP SMS (log-only) mobile=***%s code=%s", msisdn[-4:], code)
        return "log"

    if provider in {"textguru", "stpl"}:
        return _send_textguru(msisdn, code)
    if provider in {"msg91", "msg91_flow"}:
        return _send_msg91(msisdn, code)
    if provider in {"http", "generic", "dlt"}:
        return _send_http(msisdn, code)

    raise RuntimeError(f"Unknown SMS_PROVIDER={provider!r} (use textguru|msg91|http|log)")


def _send_textguru(msisdn: str, code: str) -> str:
    """TextGuru Developer API v22.0 — same as api.infradealer.com OTP."""
    username = (getattr(settings, "textguru_username", None) or "").strip()
    password = (getattr(settings, "textguru_password", None) or "").strip()
    # Allow SMS_API_KEY as "username:password" fallback
    if (not username or not password) and (getattr(settings, "sms_api_key", None) or "").strip():
        raw = settings.sms_api_key.strip()
        if ":" in raw:
            username, password = raw.split(":", 1)
    if not username or not password:
        raise RuntimeError("TEXTGURU_USERNAME/PASSWORD missing for TextGuru DLT OTP")

    sender = (
        (getattr(settings, "textguru_sender_id", None) or "").strip()
        or (getattr(settings, "sms_sender_id", None) or "").strip()
        or "AREANS"
    )
    template_id = (
        (getattr(settings, "textguru_dlt_template_id", None) or "").strip()
        or (getattr(settings, "sms_dlt_template_id", None) or "").strip()
        or "1777178540657949209"
    )
    base = (
        (getattr(settings, "textguru_api_url", None) or "").strip()
        or (getattr(settings, "sms_api_url", None) or "").strip()
        or "https://www.textguru.in/api/v22.0/"
    )
    if not base.endswith("/"):
        base += "/"

    digits10 = _digits_10(msisdn)
    if not re.fullmatch(r"[6-9]\d{9}", digits10):
        raise RuntimeError("Invalid 10-digit Indian mobile for TextGuru")

    message = _otp_message(code)
    params = {
        "username": username,
        "password": password,
        "source": sender,
        "dmobile": digits10,
        "dlttempid": template_id,
        "message": message,
    }
    url = f"{base}?{urlencode(params)}"
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(url, headers={"Accept": "application/json, text/plain, */*"})
    body = (resp.text or "").strip()
    ok = resp.status_code < 400 and (
        body.upper().startswith("MSGID:")
        or "success" in body.lower()
        or '"status":"success"' in body.lower()
        or re.search(r"MsgID\s*:", body, re.I) is not None
    )
    if not ok:
        log.warning("TextGuru OTP HTTP %s %s", resp.status_code, body[:240])
        raise RuntimeError("SMS OTP send failed (TextGuru)")
    log.info("OTP SMS via TextGuru DLT mobile=***%s", digits10[-4:])
    return "sms_textguru"


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
