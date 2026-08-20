import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from .ai.i18n import normalize_policy
from .config import settings
from .models import Chat, MetaSettings, Otp

log = logging.getLogger("infradealer")


def normalize_ai_api_base(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return "https://api.openai.com/v1"
    low = raw.lower()
    if "z.ai/manage-apikey" in low or "z.ai/model-api" in low or low in {"https://z.ai", "http://z.ai", "https://chat.z.ai"}:
        return "https://api.z.ai/api/paas/v4"
    if "api.z.ai" in low:
        raw = re.sub(r"/chat/completions/?$", "", raw, flags=re.I).rstrip("/")
        if raw.rstrip("/").endswith("/api"):
            return "https://api.z.ai/api/paas/v4"
        return raw
    return raw


def normalize_ai_model(model: str, api_base: str = "") -> str:
    raw = (model or "").strip()
    if "z.ai" in (api_base or "").lower():
        slug = re.sub(r"[\s_]+", "-", raw.lower()).strip("-")
        slug = slug.replace("glm-4-5", "glm-4.5").replace("glm4.5", "glm-4.5")
        if slug in {"glm-4.5flash", "glm-4.5-flash"}:
            return "glm-4.5-flash"
        if slug in {"glm-4.5", "glm-4.5-air"}:
            return "glm-4.5"
        if slug.startswith("gpt"):
            return "glm-4.5-flash"
        return slug or "glm-4.5-flash"
    return raw or "gpt-4o-mini"


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


def hash_user_password(raw: str) -> str:
    import bcrypt
    text = (raw or "").strip().encode("utf-8")[:72]
    return bcrypt.hashpw(text, bcrypt.gensalt(rounds=12)).decode("utf-8")


def normalize_mobile(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def to_whatsapp_id(value: str) -> str:
    """Graph API needs country code. Indian 10-digit numbers go as 91XXXXXXXXXX."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 10 and digits[0] in "6789":
        return "91" + digits
    if digits.startswith("91") and len(digits) >= 12:
        return digits
    return digits


def meta_ts_ms(raw) -> int:
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return int(utcnow().timestamp() * 1000)
    if n < 10_000_000_000:
        return n * 1000
    return n


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
            ai_enabled=settings.ai_enabled,
            ai_api_key=settings.ai_api_key or "",
            ai_api_base=settings.ai_api_base or "https://api.openai.com/v1",
            ai_model=settings.ai_model or "gpt-4o-mini",
            ai_reply_language=getattr(settings, "ai_reply_language", None) or "auto",
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
    if not getattr(row, "ai_api_base", None):
        row.ai_api_base = settings.ai_api_base or "https://api.openai.com/v1"
    if not getattr(row, "ai_model", None):
        row.ai_model = settings.ai_model or "gpt-4o-mini"
    base = normalize_ai_api_base(getattr(row, "ai_api_base", None) or "")
    model = normalize_ai_model(getattr(row, "ai_model", None) or "", base)
    if getattr(row, "ai_api_base", None) != base:
        row.ai_api_base = base
    if getattr(row, "ai_model", None) != model:
        row.ai_model = model
    if not (getattr(row, "ai_api_key", None) or "").strip() and settings.ai_api_key:
        row.ai_api_key = settings.ai_api_key
    if not (getattr(row, "ai_reply_language", None) or "").strip():
        row.ai_reply_language = getattr(settings, "ai_reply_language", None) or "auto"
    return row


def resolve_ai_config(db: Session) -> dict:
    row = get_or_create_settings(db)
    key = (getattr(row, "ai_api_key", None) or settings.ai_api_key or "").strip()
    enabled = True if getattr(row, "ai_enabled", None) is None else bool(row.ai_enabled)
    base = normalize_ai_api_base(getattr(row, "ai_api_base", None) or settings.ai_api_base or "https://api.openai.com/v1")
    model = normalize_ai_model(getattr(row, "ai_model", None) or settings.ai_model or "", base)
    return {
        "enabled": enabled,
        "api_key": key,
        "api_base": base,
        "model": model,
        "reply_language": normalize_policy(getattr(row, "ai_reply_language", None) or settings.ai_reply_language or "auto"),
    }


def _key_hint(key: str) -> str:
    raw = (key or "").strip()
    if len(raw) < 8:
        return ""
    return f"{raw[:3]}…{raw[-4:]}"


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
        "ai_enabled": bool(getattr(row, "ai_enabled", True)),
        "ai_api_base": getattr(row, "ai_api_base", None) or "https://api.openai.com/v1",
        "ai_model": getattr(row, "ai_model", None) or "gpt-4o-mini",
        "ai_reply_language": normalize_policy(getattr(row, "ai_reply_language", None) or "auto"),
        "ai_api_key_set": bool((getattr(row, "ai_api_key", None) or "").strip()),
        "ai_api_key_hint": _key_hint(getattr(row, "ai_api_key", None) or ""),
        "configured": {
            "callback_url": bool(row.callback_url),
            "verify_token": bool(row.verify_token),
            "app_secret": bool(row.app_secret),
            "app_id": bool(row.app_id),
            "waba_id": bool(row.waba_id),
            "phone_number_id": bool(row.phone_number_id),
            "system_user_token": bool(row.system_user_token),
            "ai_api_key": bool((getattr(row, "ai_api_key", None) or "").strip()),
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


def send_whatsapp_text(meta: MetaSettings, to: str, body: str, preview_url: bool = False) -> dict:
    to = to_whatsapp_id(to)
    if not meta.phone_number_id or not meta.system_user_token:
        raise RuntimeError("Phone Number ID aur System User Token save karo.")
    if not to:
        raise RuntimeError("Valid WhatsApp recipient missing.")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": bool(preview_url), "body": body},
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


def send_whatsapp_delete(meta: MetaSettings, message_id: str) -> dict:
    message_id = (message_id or "").strip()
    if not meta.phone_number_id or not meta.system_user_token:
        raise RuntimeError("Phone Number ID aur System User Token save karo.")
    if not message_id:
        raise RuntimeError("Message ID missing hai.")
    payload = {
        "messaging_product": "whatsapp",
        "status": "deleted",
        "message_id": message_id,
    }
    url = graph_url(meta, f"{meta.phone_number_id}/messages")
    headers = {"Authorization": f"Bearer {meta.system_user_token}"}
    with httpx.Client(timeout=20) as client:
        resp = client.post(url, json=payload, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(data.get("error", {}).get("message") or f"Graph API HTTP {resp.status_code}")
        return {"http_status": resp.status_code, "payload": payload, "response": data}


def download_whatsapp_media(meta: MetaSettings, media_id: str, dest_dir: str, filename: str) -> tuple[str, str]:
    """Save Cloud API media to disk. Returns (local_path, mime). Empty path on failure."""
    if not media_id or not meta.system_user_token:
        return "", ""
    headers = {"Authorization": f"Bearer {meta.system_user_token}"}
    try:
        with httpx.Client(timeout=40, follow_redirects=True) as client:
            meta_resp = client.get(graph_url(meta, media_id), headers=headers)
            info = meta_resp.json() if meta_resp.content else {}
            if meta_resp.status_code >= 400:
                log.warning("media meta failed %s %s", media_id, info)
                return "", ""
            url = info.get("url") or ""
            mime = info.get("mime_type") or ""
            if not url:
                return "", mime
            file_resp = client.get(url, headers=headers)
            if file_resp.status_code >= 400:
                log.warning("media download failed %s %s", media_id, file_resp.status_code)
                return "", mime
            os.makedirs(dest_dir, exist_ok=True)
            ext = "bin"
            if "jpeg" in mime or "jpg" in mime:
                ext = "jpg"
            elif "png" in mime:
                ext = "png"
            elif "webp" in mime:
                ext = "webp"
            elif "mp4" in mime:
                ext = "mp4"
            elif "pdf" in mime:
                ext = "pdf"
            path = os.path.join(dest_dir, f"{filename}.{ext}")
            with open(path, "wb") as fh:
                fh.write(file_resp.content)
            return path, mime
    except Exception as exc:
        log.warning("media download error %s: %s", media_id, exc)
        return "", ""


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
