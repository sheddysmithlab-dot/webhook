import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .models import Chat, MetaSettings, Otp

log = logging.getLogger("infradealer")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_verify_token() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(32))


def next_ref(db: Session, prefix: str) -> str:
    meta = get_or_create_settings(db)
    meta.seq += 1
    db.add(meta)
    db.flush()
    return f"{prefix}{meta.seq:x}".upper()


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def normalize_mobile(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def valid_mobile(value: str) -> bool:
    return bool(__import__("re").fullmatch(r"[6-9]\d{9}", normalize_mobile(value) or ""))


def get_or_create_settings(db: Session) -> MetaSettings:
    callback = f"{settings.public_base_url.rstrip('/')}/webhook/whatsapp"
    row = db.query(MetaSettings).order_by(MetaSettings.id.asc()).first()
    if not row:
        row = MetaSettings(
            callback_url=callback,
            verify_token=settings.meta_verify_token or gen_verify_token(),
            graph_version="v23.0",
        )
        db.add(row)
        db.flush()
    # Always keep public callback in sync with tunnel / deploy URL
    row.callback_url = callback
    if settings.meta_verify_token:
        row.verify_token = settings.meta_verify_token
    elif not row.verify_token:
        row.verify_token = gen_verify_token()
    if settings.meta_app_id:
        row.app_id = settings.meta_app_id
    if settings.meta_app_secret:
        row.app_secret = settings.meta_app_secret
    if settings.meta_waba_id:
        row.waba_id = settings.meta_waba_id
    if settings.meta_phone_number_id:
        row.phone_number_id = settings.meta_phone_number_id
    if settings.meta_system_user_token:
        row.system_user_token = settings.meta_system_user_token
    if settings.meta_test_recipient:
        row.test_recipient = normalize_mobile(settings.meta_test_recipient)
    return row


def settings_public(row: MetaSettings) -> dict:
    return {
        "callback_url": row.callback_url,
        "verify_token": row.verify_token,
        "app_secret": row.app_secret,
        "app_id": row.app_id,
        "waba_id": row.waba_id,
        "phone_number_id": row.phone_number_id,
        "system_user_token": row.system_user_token,
        "graph_version": row.graph_version,
        "test_recipient": row.test_recipient,
        "webhook_fields": {
            "messages": row.field_messages,
            "message_template_status_update": row.field_template_status,
            "account_alerts": row.field_account_alerts,
        },
        "subscribed": row.subscribed,
        "last_delivery": row.last_delivery.isoformat() if row.last_delivery else None,
        "error_log": json.loads(row.error_log or "[]"),
        "configured": {
            "callback_url": bool(row.callback_url),
            "verify_token": bool(row.verify_token),
            "app_secret": bool(row.app_secret),
            "app_id": bool(row.app_id),
            "waba_id": bool(row.waba_id),
            "phone_number_id": bool(row.phone_number_id),
            "system_user_token": bool(row.system_user_token),
        },
    }


def verify_meta_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    given = header.split("=", 1)[1]
    return hmac.compare_digest(expected, given)


def graph_url(meta: MetaSettings, path: str) -> str:
    return f"https://graph.facebook.com/{meta.graph_version}/{path.lstrip('/')}"


def send_whatsapp_text(meta: MetaSettings, to: str, body: str) -> dict:
    to = normalize_mobile(to)
    if not meta.phone_number_id or not meta.system_user_token:
        raise RuntimeError("Phone Number ID aur System User Token save karo.")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    url = graph_url(meta, f"{meta.phone_number_id}/messages")
    headers = {"Authorization": f"Bearer {meta.system_user_token}"}
    with httpx.Client(timeout=20) as client:
        resp = client.post(url, json=payload, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(data.get("error", {}).get("message") or f"Graph API HTTP {resp.status_code}")
        wamid = ""
        messages = data.get("messages") or []
        if messages:
            wamid = messages[0].get("id") or ""
        return {"wamid": wamid, "http_status": resp.status_code, "payload": payload, "response": data}


def store_chat(db: Session, **kwargs) -> Chat:
    chat = Chat(**kwargs)
    if not chat.timestamp_ms:
        chat.timestamp_ms = int(utcnow().timestamp() * 1000)
    db.add(chat)
    return chat


def create_otp(db: Session, mobile: str) -> Otp:
    mobile = normalize_mobile(mobile)
    window = utcnow() - timedelta(minutes=15)
    recent = db.query(Otp).filter(Otp.mobile == mobile, Otp.created_at >= window).count()
    if recent >= 3:
        raise RuntimeError("OTP limit cross ho gayi (3/15 min). Thodi der baad try karein.")
    pending = db.query(Otp).filter(Otp.mobile == mobile, Otp.status == "sent").all()
    for row in pending:
        row.status = "superseded"
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    rec = Otp(
        mobile=mobile,
        code_hash=hash_otp(code),
        status="sent",
        attempts=0,
        max_attempts=3,
        expires_at=utcnow() + timedelta(seconds=settings.otp_ttl_seconds),
    )
    db.add(rec)
    db.flush()
    rec._plain_code = code  # not persisted
    return rec


def deliver_otp(meta: MetaSettings, mobile: str, code: str) -> str:
    body = f"infradealer OTP: {code}. 5 minute mein expire hoga. Kisi ke saath share na karein."
    if meta.phone_number_id and meta.system_user_token:
        send_whatsapp_text(meta, mobile, body)
        return "whatsapp"
    log.info("OTP for %s (Meta token missing — server log only): %s", mobile, code)
    return "log"
