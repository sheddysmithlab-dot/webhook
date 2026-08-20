"""InfraDealer integration admin APIs and inbound callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..database import get_db
from ..infradealer.crypto import decrypt_secret, signed_token
from ..infradealer.service import InfraDealerIntegrationService
from ..infradealer.worker import process_outbox, run_integration_tasks
from ..models import AiMedia, InfraDealerIntegration

admin_router = APIRouter(prefix="/api/admin/infradealer")
callback_router = APIRouter(prefix="/api/v1/integrations/infradealer")


def require_admin(request: Request):
    if not current_user(request):
        raise HTTPException(401, "Login required")


class SaveConfigBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    api_version: str | None = None
    mode: str | None = None
    integration_id: str | None = None
    event_flags: dict[str, bool] | None = None


class EventFlagsBody(BaseModel):
    event_flags: dict[str, bool]


@admin_router.get("")
def get_config(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).public_config()


@admin_router.put("")
def save_config(body: SaveConfigBody, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).save_config(body.model_dump(exclude_none=True))


@admin_router.post("/test")
def test_connection(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).test_connection()


@admin_router.post("/disconnect")
def disconnect(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).disconnect()


@admin_router.post("/regenerate-secret")
def regenerate_secret(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).regenerate_secret()


@admin_router.put("/events")
def update_events(body: EventFlagsBody, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).save_config({"event_flags": body.event_flags})


@admin_router.get("/ledger")
def ledger(
    phone: str = "",
    request_id: str = "",
    user_id: str = "",
    event: str = "",
    status: str = "",
    failed_only: bool = False,
    pending_only: bool = False,
    account_only: bool = False,
    listing_only: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    return InfraDealerIntegrationService(db).list_ledger(
        {
            "phone": phone,
            "request_id": request_id,
            "user_id": user_id,
            "event": event,
            "status": status,
            "failed_only": failed_only,
            "pending_only": pending_only,
            "account_only": account_only,
            "listing_only": listing_only,
            "limit": limit,
        }
    )


@admin_router.get("/ledger/{request_id}")
def ledger_detail(request_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    row = InfraDealerIntegrationService(db).get_request_detail(request_id)
    if not row:
        raise HTTPException(404, "Request not found")
    return row


@admin_router.get("/callbacks")
def callbacks(limit: int = 100, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).list_callbacks(limit=limit)


@admin_router.get("/errors")
def errors(limit: int = 50, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).list_errors(limit=limit)


@admin_router.post("/retry/{request_id}")
def retry_request(request_id: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return InfraDealerIntegrationService(db).manual_retry(request_id)


@admin_router.post("/process-outbox")
def run_outbox(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    result = run_integration_tasks(db)
    return {"ok": True, **result}


def _verify_callback_signature(db: Session, request: Request, raw_body: bytes, payload: dict | None = None) -> bool:
    row = db.query(InfraDealerIntegration).first()
    if not row or not row.api_secret_enc:
        return False
    secret = decrypt_secret(row.api_secret_enc)
    if not secret:
        return False
    sig = (request.headers.get("X-InfraDealer-Signature") or request.headers.get("x-infradealer-signature") or "").strip()
    ts = (request.headers.get("X-InfraDealer-Timestamp") or request.headers.get("x-infradealer-timestamp") or "").strip()
    header_rid = (request.headers.get("X-InfraDealer-Request-ID") or request.headers.get("x-infradealer-request-id") or "").strip()
    if not sig or not ts:
        return False
    if sig.lower().startswith("sha256="):
        sig = sig[7:]
    try:
        age = abs(int(time.time()) - int(ts))
    except ValueError:
        return False
    if age > 900:
        return False
    body_text = raw_body.decode()
    rids = []
    if header_rid:
        rids.append(header_rid)
    payload_rid = str((payload or {}).get("request_id") or "")
    if payload_rid and payload_rid not in rids:
        rids.append(payload_rid)
    rids.append("")
    for rid in rids:
        expected = hmac.new(secret.encode(), f"{ts}.{rid}.{body_text}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig) or hmac.compare_digest(expected, sig.lower()):
            return True
    return False


@callback_router.post("/callback")
async def infradealer_callback(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid JSON")
    svc = InfraDealerIntegrationService(db)
    if not _verify_callback_signature(db, request, raw, payload):
        try:
            svc.log_callback_attempt(payload, status="AUTH_FAILED", error="Invalid callback signature")
        except Exception:
            pass
        raise HTTPException(401, "Invalid callback signature")
    return svc.handle_callback(payload)


@callback_router.get("/media/{media_id}")
def integration_media(media_id: int, t: str = Query(""), db: Session = Depends(get_db)):
    expect = signed_token("media", str(media_id))
    if not t or len(t) != len(expect) or not hmac.compare_digest(expect, t):
        raise HTTPException(403, "Invalid media token")
    row = db.query(AiMedia).filter(AiMedia.id == media_id).first()
    if not row or not row.local_path:
        raise HTTPException(404, "Media nahi mili")
    path = Path(row.local_path).resolve()
    root = Path(settings.ai_media_dir).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(404, "Media path invalid")
    if not path.is_file():
        raise HTTPException(404, "Media file missing")
    return FileResponse(path, media_type=row.mime or "application/octet-stream")
