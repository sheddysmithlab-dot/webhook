"""InfraDealer data_push — Secure Listing Submission & Admin Status Sync.

Final gateway between user-confirmed / Data-Filter-validated listing data
and the InfraDealer Admin Panel. Does NOT invent data, chat with users,
or approve/reject listings itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiConversation, AiEvent, AiListingDraft, InfraDealerCallback, InfraDealerOutbox

log = logging.getLogger("infradealer.ai.data_push")

AGENT_VERSION = "data-push-2.0"
PAYLOAD_VERSION = "1.0"
EVENT_VERSION = "1.0"

MAX_RETRIES = int(getattr(settings, "infradealer_max_retries", 3) or 3)
CONNECT_TIMEOUT = 3
READ_TIMEOUT = float(getattr(settings, "infradealer_timeout", 10) or 10)

ALLOWED_LIVE_DOMAINS = {
    "infradealer.com",
    "www.infradealer.com",
    "api.infradealer.com",
}

# Higher rank wins — never downgrade (markdown §30 / §32)
STATUS_RANK = {
    "DRAFT": 10,
    "USER_CONFIRMED": 20,
    "SUBMISSION_PENDING": 30,
    "SUBMISSION_BLOCKED": 35,
    "SENDING": 40,
    "RETRYING": 45,
    "DELIVERY_FAILED": 48,
    "FAILED": 49,
    "ADMIN_ACKNOWLEDGED": 50,
    "SUBMITTED": 50,
    "UNDER_REVIEW": 60,
    "PENDING_REVIEW": 60,
    "READY_FOR_REVIEW": 60,
    "REJECTED": 70,
    "CORRECTION_REQUIRED": 72,
    "APPROVED": 80,
    "LIVE": 90,
    "SUSPENDED": 85,
    "EXPIRED": 86,
    "DELETED": 95,
}

TRANSIENT_HTTP = {408, 425, 429, 500, 502, 503, 504}
PERMANENT_HTTP = {400, 401, 403, 404, 422}


@dataclass
class PushResult:
    ok: bool = False
    status: str = "ERROR"
    reason_code: str = ""
    submission_id: str = ""
    listing_id: str = ""
    listing_url: str = ""
    request_id: str = ""
    idempotency_key: str = ""
    payload_hash: str = ""
    message: str = ""
    notification: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {
            "ok": self.ok,
            "status": self.status,
            "reason_code": self.reason_code,
            "submission_id": self.submission_id,
            "listing_id": self.listing_id,
            "listing_url": self.listing_url,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "message": self.message,
            "notification": self.notification,
            "agent_version": AGENT_VERSION,
        }
        out.update(self.detail or {})
        return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _blank(val: Any) -> bool:
    return val is None or str(val).strip() in {"", "null", "None", "unknown"}


def public_listing_url(listing_id: str) -> str:
    lid = str(listing_id or "").strip()
    if not lid:
        return ""
    return f"https://infradealer.com/listings/{lid}"


def wa_open_token(mobile: str, listing_id: str) -> str:
    from ..infradealer.crypto import signed_token

    return signed_token("wa_open", f"{mobile[-10:]}:{listing_id}", length=24)


def listing_open_url(listing_id: str, mobile: str = "", payload: dict | None = None) -> str:
    remote = ""
    if payload:
        listing = payload.get("listing") if isinstance(payload.get("listing"), dict) else {}
        remote = str(listing.get("url") or payload.get("listing_url") or "")
    if remote:
        remote = remote.replace("/listing/", "/listings/")
        base = remote.split("?")[0]
    else:
        base = public_listing_url(listing_id)
    if not base or not validate_live_url(base):
        return ""
    if mobile and listing_id:
        token = wa_open_token(mobile, listing_id)
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}from=whatsapp&wa={mobile[-10:]}&t={token}"
    return base


def validate_live_url(url: str) -> bool:
    """Only allow InfraDealer domains — never invent or accept arbitrary hosts."""
    text = (url or "").strip()
    if not text.startswith("https://"):
        return False
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:
        return False
    if host in ALLOWED_LIVE_DOMAINS:
        return True
    return host.endswith(".infradealer.com")


def generate_idempotency_key(draft_id: Any, version: int | str) -> str:
    return f"INF-DRAFT-{draft_id}-V{version}"


def calculate_payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_payload(payload: dict) -> dict:
    """Stable copy for hashing / audit (no secrets)."""
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def sign_payload(raw_body: str, *, timestamp: str | None = None, secret: str | None = None) -> dict:
    """HMAC-SHA256 headers for outgoing webhook (secret never in JSON body)."""
    ts = timestamp or str(int(time.time()))
    sec = (secret or getattr(settings, "infradealer_api_secret", "") or "").strip()
    if not sec:
        return {
            "X-InfraDealer-Timestamp": ts,
            "X-InfraDealer-Request-ID": "",
            "X-InfraDealer-Signature": "",
            "X-InfraDealer-Event": "LISTING_SUBMIT",
        }
    digest = hmac.new(sec.encode(), f"{ts}.{raw_body}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-InfraDealer-Timestamp": ts,
        "X-InfraDealer-Signature": digest,
        "X-InfraDealer-Event": "LISTING_SUBMIT",
    }


def verify_admin_event(headers: dict | None, raw_body: str, *, max_skew_sec: int = 300) -> tuple[bool, str]:
    """Verify incoming admin status event signature."""
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    secret = (getattr(settings, "infradealer_api_secret", "") or "").strip()
    if not secret:
        # Dev / unconfigured — accept only if explicitly unsigned mode
        return True, "NO_SECRET_CONFIGURED"
    ts = headers.get("x-infradealer-timestamp") or headers.get("x-timestamp") or ""
    sig = headers.get("x-infradealer-signature") or headers.get("x-signature") or ""
    if not ts or not sig:
        return False, "MISSING_SIGNATURE"
    try:
        skew = abs(int(time.time()) - int(ts))
    except ValueError:
        return False, "INVALID_TIMESTAMP"
    if skew > max_skew_sec:
        return False, "TIMESTAMP_SKEW"
    expected = hmac.new(secret.encode(), f"{ts}.{raw_body}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False, "BAD_SIGNATURE"
    return True, "OK"


def validate_confirmation(payload: dict) -> tuple[bool, str]:
    if not payload.get("customer_confirmed"):
        conf = payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
        if not conf.get("confirmed"):
            return False, "CONFIRMATION_OR_VALIDATION_FAILED"
    return True, ""


def validate_version(payload: dict) -> tuple[bool, str]:
    current = int(payload.get("draft_version") or 1)
    confirmed = payload.get("confirmed_version")
    if confirmed is None:
        conf = payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
        confirmed = conf.get("confirmed_version") or conf.get("version")
    if confirmed is None:
        # First confirm in-session: treat current as confirmed
        return True, ""
    try:
        confirmed_i = int(confirmed)
    except (TypeError, ValueError):
        return False, "STALE_CONFIRMATION"
    if confirmed_i != current:
        return False, "STALE_CONFIRMATION"
    return True, ""


def validate_submission(payload: dict, conv: AiConversation | None = None) -> tuple[bool, str]:
    """Lightweight final safety gate — not a second Data Filter."""
    ok, reason = validate_confirmation(payload)
    if not ok:
        return False, reason
    ok, reason = validate_version(payload)
    if not ok:
        return False, reason

    fr = payload.get("filter_result") if isinstance(payload.get("filter_result"), dict) else {}
    readiness = str(fr.get("readiness") or payload.get("data_status") or "").upper()
    if readiness in {"INVALID_DATA", "CONFLICT_REQUIRES_USER", "SYSTEM_ERROR"}:
        return False, "CONFIRMATION_OR_VALIDATION_FAILED"
    if fr.get("conflicts"):
        return False, "CONFIRMATION_OR_VALIDATION_FAILED"

    intent = (payload.get("intent") or (conv.intent if conv else "") or "").upper()
    if intent not in {"BUY", "SELL"}:
        return False, "MISSING_REQUIRED_DATA"
    if _blank(payload.get("category") or payload.get("type")):
        return False, "MISSING_REQUIRED_DATA"
    if intent == "SELL":
        if _blank(payload.get("brand")) or _blank(payload.get("model")):
            return False, "MISSING_REQUIRED_DATA"
        if _blank(payload.get("expected_price") or payload.get("price")):
            return False, "MISSING_REQUIRED_DATA"
        if _blank(payload.get("state") or payload.get("location") or payload.get("city")):
            return False, "MISSING_REQUIRED_DATA"
    return True, ""


def build_payload(
    db: Session,
    conv: AiConversation,
    draft: AiListingDraft,
    payload: dict,
    *,
    request_id: str,
    idempotency_key: str,
) -> dict:
    """Canonical LISTING_SUBMIT envelope for Admin / integration layer."""
    from ..infradealer.payloads import build_listing_payload

    version = int(payload.get("draft_version") or 1)
    account_id = payload.get("profile_id") or conv.profile_id or ""
    fr = payload.get("filter_result") if isinstance(payload.get("filter_result"), dict) else {}
    nd = fr.get("normalized_data") if isinstance(fr.get("normalized_data"), dict) else {}
    summary = payload.get("confirmed_json") or payload.get("summary_json") or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            summary = {}

    data = {
        "brand": payload.get("brand") or nd.get("brand") or summary.get("brand"),
        "model": payload.get("model") or nd.get("model") or summary.get("model"),
        "year": payload.get("year") or nd.get("year") or summary.get("year"),
        "km": payload.get("running_km") or nd.get("km") or summary.get("km"),
        "hours": payload.get("operating_hours") or nd.get("operating_hours"),
        "location": payload.get("city") or payload.get("location") or nd.get("city") or summary.get("location"),
        "state": payload.get("state") or nd.get("state") or summary.get("state"),
        "price": nd.get("price") or payload.get("expected_price") or summary.get("price"),
    }
    data = {k: v for k, v in data.items() if not _blank(v)}

    infra_user = ""
    try:
        from ..infradealer.service import get_integration_service, account_mobile

        svc = get_integration_service(db)
        state = svc.get_or_create_account_state(account_mobile(conv.mobile), conversation_id=conv.id)
        infra_user = (state.infradealer_user_id if state else "") or ""
    except Exception:
        infra_user = ""

    listing_body = build_listing_payload(db, conv, draft, payload, request_id, infra_user)
    envelope = {
        "event": "LISTING_SUBMIT",
        "event_version": EVENT_VERSION,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "agent_version": AGENT_VERSION,
        "payload_version": PAYLOAD_VERSION,
        "schema_version": fr.get("schema_version") or payload.get("schema_version") or "",
        "filter_version": fr.get("filter_version") or "",
        "account": {"account_id": account_id, "mobile": conv.mobile},
        "conversation": {
            "conversation_id": conv.conversation_id,
            "workflow_id": payload.get("workflow_id") or f"WF-{conv.id}",
        },
        "listing": {
            "draft_id": draft.id,
            "card_id": draft.card_id,
            "version": version,
            "intent": (payload.get("intent") or "SELL").upper(),
            "category": payload.get("category"),
            "data": data,
        },
        "validation": {
            "status": "VALID" if validate_submission(payload, conv)[0] else "INVALID",
            "schema_version": fr.get("schema_version") or "",
            "filter_version": fr.get("filter_version") or "",
            "readiness": fr.get("readiness") or "",
        },
        "confirmation": {
            "confirmed": True,
            "version": version,
            "confirmed_version": int(payload.get("confirmed_version") or version),
            "timestamp": payload.get("confirmed_at") or _now_iso(),
        },
        "infradealer_body": listing_body,
    }
    return envelope


def parse_admin_response(response: dict | None, http_status: int = 0) -> dict:
    response = response if isinstance(response, dict) else {}
    listing_id = str(
        response.get("listing_id")
        or response.get("id")
        or (response.get("listing") or {}).get("id")
        or ""
    )
    submission_id = str(response.get("submission_id") or response.get("request_id") or "")
    status = str(response.get("status") or response.get("business_code") or "").upper()
    code = str(response.get("code") or response.get("business_code") or "")
    success = bool(response.get("success")) or http_status in {200, 201} or bool(listing_id)
    if http_status in PERMANENT_HTTP:
        return {
            "success": False,
            "permanent": True,
            "retry": False,
            "listing_id": listing_id,
            "submission_id": submission_id,
            "status": "PERMANENT_FAILURE",
            "admin_error_code": code or str(http_status),
            "admin_error_message": str(response.get("message") or response.get("error") or "")[:300],
        }
    if http_status in TRANSIENT_HTTP or (not success and http_status >= 500):
        return {
            "success": False,
            "permanent": False,
            "retry": True,
            "listing_id": listing_id,
            "submission_id": submission_id,
            "status": "RETRY",
            "admin_error_code": code or str(http_status),
            "admin_error_message": str(response.get("message") or "transient failure")[:300],
        }
    if success:
        return {
            "success": True,
            "permanent": False,
            "retry": False,
            "listing_id": listing_id,
            "submission_id": submission_id or response.get("request_id") or "",
            "status": status or "UNDER_REVIEW",
            "live_url": response.get("live_url") or response.get("url") or "",
        }
    return {
        "success": False,
        "permanent": True,
        "retry": False,
        "listing_id": listing_id,
        "submission_id": submission_id,
        "status": "PERMANENT_FAILURE",
        "admin_error_code": code or "UNKNOWN",
        "admin_error_message": str(response.get("message") or "submission failed")[:300],
    }


def _audit(db: Session, conv: AiConversation, event_type: str, detail: dict) -> None:
    safe = {k: v for k, v in detail.items() if k.lower() not in {"secret", "api_key", "signature", "otp"}}
    db.add(AiEvent(
        wamid=conv.last_wamid or "",
        mobile=conv.mobile,
        event_type=event_type,
        detail=json.dumps(safe, ensure_ascii=False, default=str)[:4000],
    ))


def store_submission(payload: dict, envelope: dict, *, status: str) -> dict:
    """Persist submission snapshot on conversation payload (no silent field mutation)."""
    version = int(payload.get("draft_version") or 1)
    sub = {
        "submission_id": envelope.get("request_id") or f"SUB-{uuid.uuid4().hex[:10]}",
        "request_id": envelope.get("request_id"),
        "idempotency_key": envelope.get("idempotency_key"),
        "payload_hash": envelope.get("payload_hash") or calculate_payload_hash(envelope.get("listing") or {}),
        "draft_id": (envelope.get("listing") or {}).get("draft_id"),
        "draft_version": version,
        "status": status,
        "listing_id": payload.get("infradealer_listing_id") or "",
        "live_url": payload.get("listing_url") or "",
        "retry_count": int((payload.get("submission") or {}).get("retry_count") or 0),
        "submitted_at": _now_iso(),
        "agent_version": AGENT_VERSION,
    }
    payload["submission"] = sub
    payload["push_stage"] = status
    payload["listing_status"] = status
    return sub


def create_notification_event(
    *,
    notification_type: str,
    listing_id: str = "",
    status: str = "",
    reason_code: str = "",
    reason_text: str = "",
    live_url: str = "",
    account_id: Any = None,
    submission_id: str = "",
) -> dict:
    return {
        "event": "USER_NOTIFICATION_REQUIRED",
        "type": notification_type,
        "account_id": account_id,
        "listing_id": listing_id,
        "submission_id": submission_id,
        "status": status,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "live_url": live_url if validate_live_url(live_url) else "",
    }


def _can_transition(old_status: str, new_status: str) -> bool:
    old_r = STATUS_RANK.get((old_status or "").upper(), 0)
    new_r = STATUS_RANK.get((new_status or "").upper(), 0)
    if new_r == 0:
        return False
    # Allow sideways / equal for idempotent re-delivery of same state
    return new_r >= old_r


def get_submission_status(db: Session, conv: AiConversation, submission_id: str = "") -> dict:
    """Backend-truth status for chat_memory status questions."""
    from .tools import _payload

    payload = _payload(conv)
    sub = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    listing_id = payload.get("infradealer_listing_id") or sub.get("listing_id") or ""
    live_url = payload.get("listing_url") or sub.get("live_url") or ""
    if live_url and not validate_live_url(live_url):
        live_url = ""
    status = (
        payload.get("listing_status")
        or sub.get("status")
        or "DRAFT"
    )
    sid = submission_id or sub.get("submission_id") or sub.get("request_id") or ""

    # Prefer outbox/request truth when present
    if conv.draft_id:
        outbox = (
            db.query(InfraDealerOutbox)
            .filter(InfraDealerOutbox.draft_id == conv.draft_id)
            .order_by(InfraDealerOutbox.id.desc())
            .first()
        )
        if outbox:
            sid = sid or outbox.request_id
            if outbox.business_status:
                status = outbox.business_status
            elif outbox.status == "DONE":
                status = status if STATUS_RANK.get(str(status).upper(), 0) >= 50 else "UNDER_REVIEW"

    return {
        "submission_id": sid,
        "listing_id": listing_id,
        "status": status,
        "live_url": live_url if validate_live_url(live_url) else "",
        "idempotency_key": sub.get("idempotency_key") or "",
        "draft_version": sub.get("draft_version") or payload.get("draft_version"),
    }


def process_approval(db: Session, conv: AiConversation, event: dict) -> dict:
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    listing_id = str(event.get("listing_id") or "")
    live_url = str(event.get("live_url") or event.get("url") or "")
    if not listing_id:
        return {"ok": False, "reason_code": "INVALID_ADMIN_EVENT", "message": "listing_id required"}
    if live_url and not validate_live_url(live_url):
        live_url = ""
    new_status = str(event.get("status") or "APPROVED").upper()
    if new_status in {"APPROVED", "LIVE", "PUBLISHED", "POSTED"}:
        # APPROVED ≠ LIVE unless contract/auto_publish says so
        if new_status == "APPROVED" and not getattr(settings, "infradealer_auto_publish", False) and not live_url:
            target = "APPROVED"
        elif live_url or new_status in {"LIVE", "PUBLISHED", "POSTED"}:
            target = "LIVE"
        else:
            target = "APPROVED"
    else:
        target = new_status

    old = str(payload.get("listing_status") or "")
    if not _can_transition(old, target):
        return {"ok": True, "status": old, "ignored": True, "reason_code": "NO_DOWNGRADE"}

    payload["listing_status"] = target
    payload["infradealer_listing_id"] = listing_id
    if live_url:
        payload["listing_url"] = live_url
    sub = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    sub.update({
        "listing_id": listing_id,
        "status": target,
        "live_url": live_url or sub.get("live_url") or "",
        "acknowledged_at": sub.get("acknowledged_at") or _now_iso(),
        "completed_at": _now_iso() if target == "LIVE" else sub.get("completed_at"),
    })
    payload["submission"] = sub
    note = create_notification_event(
        notification_type="LISTING_APPROVED" if target == "APPROVED" else "LISTING_LIVE",
        listing_id=listing_id,
        status=target,
        live_url=live_url,
        account_id=payload.get("profile_id") or conv.profile_id,
        submission_id=sub.get("submission_id") or "",
    )
    payload["pending_notification"] = note
    _write_payload(conv, payload)
    _audit(db, conv, "data_push_approval", {"old": old, "new": target, "listing_id": listing_id})
    return {"ok": True, "status": target, "notification": note}


def process_rejection(db: Session, conv: AiConversation, event: dict) -> dict:
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    listing_id = str(event.get("listing_id") or payload.get("infradealer_listing_id") or "")
    reason_code = str(event.get("reason_code") or event.get("code") or "REJECTED")
    reason_text = str(event.get("reason_text") or event.get("reason") or event.get("message") or "")
    old = str(payload.get("listing_status") or "")
    if not _can_transition(old, "REJECTED"):
        # LIVE/APPROVED should not silently become REJECTED from stale event unless same or higher
        if STATUS_RANK.get(old.upper(), 0) > STATUS_RANK["REJECTED"]:
            return {"ok": True, "status": old, "ignored": True, "reason_code": "NO_DOWNGRADE"}

    payload["listing_status"] = "REJECTED"
    payload["rejection_reason"] = reason_text or reason_code
    # Preserve draft / confirmation for correction workflow — do NOT delete
    payload["customer_confirmed"] = False
    payload["awaiting_confirm"] = False
    sub = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    sub.update({
        "listing_id": listing_id or sub.get("listing_id") or "",
        "status": "REJECTED",
        "reason_code": reason_code,
        "reason_text": reason_text,
        "reviewed_at": _now_iso(),
    })
    payload["submission"] = sub
    note = create_notification_event(
        notification_type="LISTING_REJECTED",
        listing_id=listing_id,
        status="REJECTED",
        reason_code=reason_code,
        reason_text=reason_text,
        account_id=payload.get("profile_id") or conv.profile_id,
        submission_id=sub.get("submission_id") or "",
    )
    payload["pending_notification"] = note
    _write_payload(conv, payload)
    _audit(db, conv, "data_push_rejection", {
        "old": old, "listing_id": listing_id,
        "reason_code": reason_code, "reason_text": reason_text,
    })
    return {"ok": True, "status": "REJECTED", "notification": note}


def process_admin_event(
    db: Session,
    conv: AiConversation,
    event: dict,
    *,
    headers: dict | None = None,
    raw_body: str = "",
) -> dict:
    """Inbound Admin Panel status event — verify, idempotent, no state downgrade."""
    from .tools import _payload, _write_payload

    event = event if isinstance(event, dict) else {}
    raw = raw_body or json.dumps(event, ensure_ascii=False, sort_keys=True)
    ok, reason = verify_admin_event(headers, raw)
    if not ok:
        return {"ok": False, "reason_code": "REJECT_EVENT", "message": reason}

    event_id = str(event.get("event_id") or event.get("id") or "")
    payload = _payload(conv)
    processed = list(payload.get("processed_admin_events") or [])
    if event_id and event_id in processed:
        return {"ok": True, "duplicate": True, "reason_code": "ALREADY_PROCESSED"}

    # Persist callback row when possible
    try:
        cb = InfraDealerCallback(
            callback_id=event_id or f"evt-{uuid.uuid4().hex[:12]}",
            event_type=str(event.get("event") or event.get("status") or "")[:40],
            request_id=str(event.get("request_id") or event.get("submission_id") or "")[:64],
            payload_json=json.dumps(event, ensure_ascii=False)[:8000],
            status="RECEIVED",
            processed=False,
        )
        db.add(cb)
        db.flush()
    except Exception:
        cb = None

    etype = str(event.get("event") or event.get("status") or "").upper()
    if "REJECT" in etype:
        result = process_rejection(db, conv, event)
    elif any(x in etype for x in ("APPROVE", "LIVE", "PUBLISH", "POSTED")):
        if "APPROVE" in etype and not event.get("listing_id"):
            return {"ok": False, "reason_code": "INVALID_ADMIN_EVENT", "message": "listing_id required"}
        result = process_approval(db, conv, event)
    elif "REVIEW" in etype or etype in {"LISTING_RECEIVED", "UNDER_REVIEW", "RECEIVED"}:
        payload = _payload(conv)
        old = str(payload.get("listing_status") or "")
        if _can_transition(old, "UNDER_REVIEW"):
            payload["listing_status"] = "UNDER_REVIEW"
            sub = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
            sub["status"] = "UNDER_REVIEW"
            payload["submission"] = sub
            _write_payload(conv, payload)
        result = {"ok": True, "status": "UNDER_REVIEW"}
    else:
        result = {"ok": True, "status": etype or "RECEIVED", "ignored": True}

    if event_id:
        payload = _payload(conv)
        processed = list(payload.get("processed_admin_events") or [])
        processed.append(event_id)
        payload["processed_admin_events"] = processed[-100:]
        _write_payload(conv, payload)
    if cb is not None:
        cb.processed = True
        cb.status = "PROCESSED"
    return result


def handle_failure(payload: dict, *, retry: bool, error_code: str, message: str) -> dict:
    sub = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    retries = int(sub.get("retry_count") or 0) + 1
    sub["retry_count"] = retries
    sub["last_error"] = message[:300]
    sub["admin_error_code"] = error_code
    if retry and retries <= MAX_RETRIES:
        sub["status"] = "RETRYING"
        payload["listing_status"] = "RETRYING"
        payload["push_stage"] = "RETRYING"
    else:
        sub["status"] = "DELIVERY_FAILED" if retry else "FAILED"
        payload["listing_status"] = sub["status"]
        payload["push_stage"] = sub["status"]
    payload["submission"] = sub
    return sub


def push_listing(db: Session, conv: AiConversation) -> PushResult:
    """Submit confirmed draft to Admin via InfraDealer integration (idempotent)."""
    from .data_filter import final_validation
    from .tools import _draft_for, _payload, _write_payload

    payload = _payload(conv)

    # Gate: confirmation + version + safety
    ok, reason = validate_submission(payload, conv)
    if not ok:
        status = "STALE_CONFIRMATION" if reason == "STALE_CONFIRMATION" else "SUBMISSION_BLOCKED"
        _audit(db, conv, "data_push_blocked", {"reason": reason})
        return PushResult(
            ok=False,
            status=status,
            reason_code=reason or "CONFIRMATION_OR_VALIDATION_FAILED",
            message="Submission blocked — confirmation/validation/version failed",
        )

    # Final Data Filter safety pass
    gate = final_validation(db, conv)
    payload = _payload(conv)
    if not gate.ready and gate.readiness in {"INVALID_DATA", "CONFLICT_REQUIRES_USER", "MISSING_REQUIRED_DATA"}:
        return PushResult(
            ok=False,
            status="SUBMISSION_BLOCKED",
            reason_code="CONFIRMATION_OR_VALIDATION_FAILED",
            message=f"Data filter blocked submission: {gate.readiness}",
            detail={"missing_fields": gate.missing_fields, "conflicts": gate.conflicts},
        )

    # Lock confirmation version to current draft
    version = int(payload.get("draft_version") or 1)
    payload["confirmed_version"] = version
    payload["confirmed_at"] = payload.get("confirmed_at") or _now_iso()
    payload["customer_confirmed"] = True

    draft = _draft_for(db, conv)
    request_id = f"listing-draft-{draft.id}-v{version}"
    idem = generate_idempotency_key(draft.id, version)

    # Idempotent short-circuit: same key already acknowledged
    existing = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
    if (
        existing.get("idempotency_key") == idem
        and STATUS_RANK.get(str(existing.get("status") or "").upper(), 0) >= STATUS_RANK["ADMIN_ACKNOWLEDGED"]
    ):
        return PushResult(
            ok=True,
            status=str(existing.get("status") or "UNDER_REVIEW"),
            submission_id=str(existing.get("submission_id") or ""),
            listing_id=str(existing.get("listing_id") or payload.get("infradealer_listing_id") or ""),
            listing_url=str(existing.get("live_url") or payload.get("listing_url") or ""),
            request_id=str(existing.get("request_id") or request_id),
            idempotency_key=idem,
            payload_hash=str(existing.get("payload_hash") or ""),
            message="Idempotent replay — existing submission returned",
            detail={"duplicate_skipped": True},
        )

    envelope = build_payload(db, conv, draft, payload, request_id=request_id, idempotency_key=idem)
    listing_hash = calculate_payload_hash(envelope.get("listing") or {})
    envelope["payload_hash"] = listing_hash
    store_submission(payload, envelope, status="SENDING")
    _write_payload(conv, payload)
    _audit(db, conv, "data_push_started", {
        "request_id": request_id,
        "idempotency_key": idem,
        "payload_hash": listing_hash,
        "draft_version": version,
    })

    try:
        from ..infradealer.service import get_integration_service

        svc = get_integration_service(db)
        # Integration service owns HTTPS + outbox; we keep AI-layer gates here
        outbox = svc.push_listing_for_draft(conv, draft, payload)
        listing_id = str(payload.get("infradealer_listing_id") or "")
        listing_url = str(payload.get("listing_url") or "")
        if outbox is not None:
            listing_id = listing_id or str(getattr(outbox, "business_status", "") and payload.get("infradealer_listing_id") or "")
            # Refresh payload after service side-effects
            payload = _payload(conv)
            listing_id = str(payload.get("infradealer_listing_id") or listing_id or "")
            listing_url = str(payload.get("listing_url") or listing_url or "")
            if listing_url and not validate_live_url(listing_url):
                listing_url = ""
                payload["listing_url"] = ""

        # Do not mark LIVE without admin confirmation / valid URL
        ack_status = "UNDER_REVIEW"
        if outbox is not None and (outbox.status or "").upper() == "DONE":
            ack_status = "ADMIN_ACKNOWLEDGED"
        elif outbox is None and not getattr(settings, "infradealer_base_url", ""):
            ack_status = "READY_FOR_REVIEW"

        draft.status = "READY_FOR_REVIEW"
        payload["listing_status"] = "PENDING_REVIEW" if ack_status != "READY_FOR_REVIEW" else "READY_FOR_REVIEW"
        sub = store_submission(payload, envelope, status=ack_status if ack_status != "READY_FOR_REVIEW" else "UNDER_REVIEW")
        sub["listing_id"] = listing_id
        sub["live_url"] = listing_url
        sub["acknowledged_at"] = _now_iso()
        payload["submission"] = sub
        if listing_id:
            payload["infradealer_listing_id"] = listing_id
        payload["push_stage"] = "PUSHED_TO_INFRADEALER"
        note = create_notification_event(
            notification_type="LISTING_SUBMITTED",
            listing_id=listing_id,
            status=payload["listing_status"],
            account_id=payload.get("profile_id") or conv.profile_id,
            submission_id=sub.get("submission_id") or request_id,
        )
        payload["pending_notification"] = note
        _write_payload(conv, payload)
        _audit(db, conv, "data_push_ack", {
            "status": ack_status,
            "listing_id": listing_id,
            "request_id": request_id,
        })
        return PushResult(
            ok=True,
            status=ack_status if ack_status != "READY_FOR_REVIEW" else "SUBMITTED",
            submission_id=str(sub.get("submission_id") or request_id),
            listing_id=listing_id,
            listing_url=listing_url,
            request_id=request_id,
            idempotency_key=idem,
            payload_hash=listing_hash,
            message="Listing submitted for admin review",
            notification=note,
            detail={"draft_id": draft.id, "card_id": draft.card_id, "outbox_id": getattr(outbox, "id", None)},
        )
    except Exception as exc:
        log.exception("data_push failed")
        payload = _payload(conv)
        err = str(exc)[:300]
        # Classify — network-ish → retry; otherwise permanent
        retry = bool(re.search(r"timeout|timed out|503|502|504|connection|unavailable", err, re.I))
        sub = handle_failure(payload, retry=retry, error_code="SYSTEM_ERROR", message=err)
        _write_payload(conv, payload)
        _audit(db, conv, "data_push_failed", {"retry": retry, "error": err})
        note = create_notification_event(
            notification_type="LISTING_DELIVERY_FAILED",
            status=sub.get("status") or "FAILED",
            reason_code="SYSTEM_ERROR",
            reason_text="InfraDealer server temporarily unavailable; submission preserved for retry.",
            account_id=payload.get("profile_id") or conv.profile_id,
            submission_id=str(sub.get("submission_id") or request_id),
        )
        return PushResult(
            ok=False,
            status="RETRY" if retry and int(sub.get("retry_count") or 0) <= MAX_RETRIES else "DELIVERY_FAILED",
            reason_code="SYSTEM_ERROR",
            request_id=request_id,
            idempotency_key=idem,
            payload_hash=listing_hash,
            message=err,
            notification=note,
            detail={"user_data_error": False, "system_error": True, "retry_count": sub.get("retry_count")},
        )


def handle_post_listing_query(db: Session, conv: AiConversation, text: str, lang: str = "hinglish") -> str | None:
    """Status / link / last-post queries — backend truth via get_submission_status."""
    from .i18n import t

    low = (text or "").lower()
    status = get_submission_status(db, conv)
    link = status.get("live_url") or ""
    if link and not validate_live_url(link):
        link = ""
    st = str(status.get("status") or "").upper()

    if re.search(r"\b(link|url)\b|listing\s*link", low):
        return t(lang, "approved", link=link) if link else t(lang, "link_missing")
    if re.search(r"(last|pichhli|previous).{0,20}(post|listing)|listing\s*status|meri\s+listing", low):
        if st == "LIVE" and link:
            return t(lang, "approved", link=link)
        if st in {"PENDING_REVIEW", "UNDER_REVIEW", "SUBMITTED", "ADMIN_ACKNOWLEDGED", "APPROVED", "READY_FOR_REVIEW"}:
            return t(lang, "not_live")
        return t(lang, "status_ask", status=st or "DRAFT")
    if re.search(r"(delete|hata|mita).{0,24}(listing|post|ad)|(listing|post).{0,24}(delete|hata)", low):
        return t(lang, "handoff")  # listing delete is admin/human — never silent wipe
    return None


def has_recent_listing(db: Session, conv: AiConversation) -> bool:
    """True if this conversation already submitted / has a listing id."""
    payload = None
    try:
        from .tools import _payload

        payload = _payload(conv)
    except Exception:
        payload = {}
    if payload.get("infradealer_listing_id") or payload.get("listing_url"):
        return True
    st = str(payload.get("listing_status") or "").upper()
    return st in {"LIVE", "PENDING_REVIEW", "UNDER_REVIEW", "SUBMITTED", "APPROVED", "READY_FOR_REVIEW", "POSTED"}


def mark_listing_review_notified(conv: AiConversation, *, error: str = "") -> None:
    """Persist that approve/reject WhatsApp was attempted (success or fail)."""
    from .tools import _payload, _write_payload

    payload = _payload(conv)
    if error:
        payload["listing_review_notify_error"] = str(error)[:500]
        payload["listing_review_notified"] = False
        payload["listing_review_notify_at"] = _now_iso()
    else:
        payload["listing_review_notified"] = True
        payload["listing_review_notify_error"] = ""
        payload["listing_review_notify_at"] = _now_iso()
    _write_payload(conv, payload)


def should_retry_decision_notify(conv: AiConversation) -> bool:
    """Retry WhatsApp notify when decision exists but prior Graph send failed."""
    from .tools import _payload

    payload = _payload(conv)
    if payload.get("listing_review_notified"):
        return False
    status = str(payload.get("listing_status") or "").upper()
    if status not in {"POSTED", "LIVE", "APPROVED", "REJECTED"}:
        return False
    # Always allow retry if never marked notified, or last attempt errored
    return True


def notify_user_admin_decision(
    db: Session,
    conv: AiConversation,
    *,
    approved: bool,
    listing_id: str = "",
    payload: dict | None = None,
    reason: str = "",
    draft: Any = None,
    force: bool = False,
) -> dict:
    """Update shared state + send WhatsApp approve/reject message (Admin is final authority)."""
    from .i18n import pick_language, t
    from .tools import _payload, _write_payload

    remote = payload if isinstance(payload, dict) else {}
    pl = _payload(conv)
    if pl.get("listing_review_notified") and not force:
        return {"sent": False, "skipped": True, "error": "", "text": ""}

    lid = str(
        listing_id
        or pl.get("infradealer_listing_id")
        or (remote.get("listing") or {}).get("listing_id")
        or (remote.get("listing") or {}).get("id")
        or ""
    )
    live_url = str(
        remote.get("live_url")
        or remote.get("listing_url")
        or (remote.get("listing") or {}).get("url")
        or (remote.get("listing") or {}).get("listing_url")
        or pl.get("listing_url")
        or ""
    )
    if live_url:
        live_url = live_url.replace("/listing/", "/listings/")
    if not live_url and lid:
        live_url = public_listing_url(lid)
    if live_url and not validate_live_url(live_url):
        live_url = public_listing_url(lid) if lid else ""

    open_url = listing_open_url(lid, mobile=conv.mobile, payload={"listing_url": live_url}) if lid else live_url

    # Sync shared listing state (Admin is final authority)
    if approved:
        pl["listing_status"] = "POSTED"
        pl["infradealer_listing_id"] = lid or pl.get("infradealer_listing_id") or ""
        if open_url or live_url:
            pl["listing_url"] = open_url or live_url
        sub = pl.get("submission") if isinstance(pl.get("submission"), dict) else {}
        sub.update({"status": "LIVE", "listing_id": lid, "live_url": open_url or live_url})
        pl["submission"] = sub
        if draft is not None:
            draft.status = "POSTED"
        text = t(
            pick_language("", str(getattr(conv, "language", "") or ""), "auto"),
            "approved",
            link=open_url or live_url or public_listing_url(lid),
        )
    else:
        reason_text = str(reason or remote.get("reason_text") or remote.get("message") or "Needs correction")
        pl["listing_status"] = "REJECTED"
        pl["rejection_reason"] = reason_text
        pl["customer_confirmed"] = False
        pl["awaiting_confirm"] = False
        sub = pl.get("submission") if isinstance(pl.get("submission"), dict) else {}
        sub.update({"status": "REJECTED", "reason_text": reason_text, "listing_id": lid})
        pl["submission"] = sub
        if draft is not None:
            draft.status = "REJECTED"
        text = t(
            pick_language("", str(getattr(conv, "language", "") or ""), "auto"),
            "rejected",
            reason=reason_text,
        )

    _write_payload(conv, pl)

    # Send WhatsApp via integration service helper when possible
    try:
        from ..infradealer.service import get_integration_service

        svc = get_integration_service(db)
        sent = svc._notify_customer(conv, text, preview_url=bool(approved and (open_url or live_url)))
        # After approve/reject: 10-min silence → wipe live chat (listing memory kept)
        if sent and draft is not None:
            try:
                from .cards import schedule_card_cleanup

                schedule_card_cleanup(draft, minutes=10)
            except Exception:
                log.exception("schedule_card_cleanup failed")
        return {"sent": bool(sent), "error": "" if sent else "send_failed", "text": text}
    except Exception as exc:
        mark_listing_review_notified(conv, error=str(exc)[:500])
        return {"sent": False, "error": str(exc)[:300], "text": text}
