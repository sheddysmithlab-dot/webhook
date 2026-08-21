"""InfraDealer integration service — events, outbox, state, callbacks."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AiConversation,
    AiListingDraft,
    InfraDealerAccountState,
    InfraDealerCallback,
    InfraDealerIntegration,
    InfraDealerOutbox,
    InfraDealerRequest,
)
from .client import InfraDealerApiClient
from .crypto import decrypt_secret, encrypt_secret, mask_secret, short_key
from .events import (
    DEFAULT_EVENT_FLAGS,
    DEFAULT_WEBHOOK_BASE,
    INTERNAL_TO_API,
    classify_response,
    event_enabled,
    listing_public_url,
    listing_reject_reason,
    load_event_flags,
    normalize_base_url,
    normalize_callback_event,
    should_retry,
)
from .payloads import build_listing_payload

log = logging.getLogger("infradealer.integration")

_BUSINESS_OK = {
    "ACCOUNT_NOT_FOUND", "OTP_REQUIRED", "PENDING_REVIEW", "ACCOUNT_REQUIRED",
    "ACCOUNT_EXISTS", "ACCOUNT_FOUND", "ACCOUNT_CREATED", "LISTING_POSTED",
    "LISTING_PENDING_REVIEW", "LIVE", "POSTED", "PUBLISHED",
}

_LISTING_PUSH_ACTIVE = {"PENDING", "RETRY", "DONE"}
_LISTING_LIVE_CODES = {"LISTING_POSTED", "LIVE", "POSTED", "PUBLISHED", "APPROVED"}
_LISTING_PENDING_CODES = {"LISTING_PENDING_REVIEW", "PENDING_REVIEW", "PENDING"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def listing_push_request_id(draft_id: int, *, rejected: bool = False) -> str:
    if rejected:
        return str(uuid.uuid4())
    return f"listing-draft-{int(draft_id)}"


def account_mobile(mobile: str | None) -> str:
    """Always store/lookup InfraDealerAccountState by last 10 digits."""
    return "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]


def get_integration_service(db: Session) -> "InfraDealerIntegrationService":
    return InfraDealerIntegrationService(db)


class InfraDealerIntegrationService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_account_state(
        self,
        mobile: str | None,
        *,
        conversation_id: int | None = None,
        account_status: str | None = None,
    ) -> InfraDealerAccountState | None:
        """Idempotent account-state row. Avoids UniqueViolation when check+apply both insert."""
        from sqlalchemy.exc import IntegrityError

        phone = account_mobile(mobile)
        if not phone:
            return None
        state = (
            self.db.query(InfraDealerAccountState)
            .filter(InfraDealerAccountState.mobile == phone)
            .first()
        )
        if state:
            if conversation_id and not state.conversation_id:
                state.conversation_id = conversation_id
            if account_status:
                state.account_status = account_status
            return state
        for obj in list(self.db.new):
            if isinstance(obj, InfraDealerAccountState) and obj.mobile == phone:
                if conversation_id and not obj.conversation_id:
                    obj.conversation_id = conversation_id
                if account_status:
                    obj.account_status = account_status
                return obj
        state = InfraDealerAccountState(
            mobile=phone,
            conversation_id=conversation_id,
            account_status=account_status or "NOT_REQUESTED",
        )
        try:
            with self.db.begin_nested():
                self.db.add(state)
                self.db.flush()
        except IntegrityError:
            state = (
                self.db.query(InfraDealerAccountState)
                .filter(InfraDealerAccountState.mobile == phone)
                .first()
            )
            if not state:
                raise
            if conversation_id and not state.conversation_id:
                state.conversation_id = conversation_id
            if account_status:
                state.account_status = account_status
        return state

    def get_or_create_config(self) -> InfraDealerIntegration:
        row = self.db.query(InfraDealerIntegration).first()
        if row:
            return row
        callback = f"{settings.public_base_url.rstrip('/')}/api/v1/integrations/infradealer/callback"
        row = InfraDealerIntegration(
            integration_id=str(uuid.uuid4()),
            callback_url=callback,
            event_flags_json=json.dumps(DEFAULT_EVENT_FLAGS),
            mode=(settings.infradealer_mode or "LIVE").upper(),
            api_version=settings.infradealer_api_version or "v1",
            base_url=normalize_base_url(settings.infradealer_base_url or DEFAULT_WEBHOOK_BASE),
        )
        if settings.infradealer_api_key:
            row.api_key_enc = encrypt_secret(settings.infradealer_api_key)
        if settings.infradealer_api_secret:
            row.api_secret_enc = encrypt_secret(settings.infradealer_api_secret)
        self.db.add(row)
        self.db.flush()
        return row

    def is_configured(self) -> bool:
        row = self.get_or_create_config()
        return bool(row.base_url and row.api_key_enc)

    def is_test_mode(self) -> bool:
        return (self.get_or_create_config().mode or "LIVE").upper() == "TEST"

    def public_config(self) -> dict:
        row = self.get_or_create_config()
        flags = load_event_flags(row.event_flags_json)
        stats = self.dashboard_stats()
        key = decrypt_secret(row.api_key_enc)
        last_req = (
            self.db.query(InfraDealerRequest).order_by(InfraDealerRequest.id.desc()).first()
        )
        return {
            "base_url": row.base_url,
            "api_key_masked": mask_secret(key),
            "api_key_short": short_key(key),
            "api_secret_set": bool(row.api_secret_enc),
            "integration_id": row.integration_id,
            "callback_url": row.callback_url or f"{settings.public_base_url.rstrip('/')}/api/v1/integrations/infradealer/callback",
            "api_version": row.api_version or "v1",
            "mode": row.mode or "LIVE",
            "connected": row.connected,
            "connection_status": row.connection_status or "DISCONNECTED",
            "event_flags": flags,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
            "last_error_code": row.last_error_code or "",
            "avg_latency_ms": row.avg_latency_ms or 0,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_request_at": last_req.created_at.isoformat() if last_req and last_req.created_at else None,
            "stats": stats,
            "health": self.health_snapshot(row),
        }

    def save_config(self, body: dict) -> dict:
        row = self.get_or_create_config()
        if body.get("base_url") is not None:
            row.base_url = normalize_base_url(str(body["base_url"]).strip())
        if body.get("api_version"):
            row.api_version = str(body["api_version"]).strip().lstrip("/")
        if body.get("mode"):
            row.mode = str(body["mode"]).upper()
        if body.get("integration_id"):
            row.integration_id = str(body["integration_id"]).strip()[:64]
        if body.get("api_key"):
            row.api_key_enc = encrypt_secret(str(body["api_key"]).strip())
        if body.get("api_secret"):
            row.api_secret_enc = encrypt_secret(str(body["api_secret"]).strip())
        if body.get("event_flags"):
            flags = load_event_flags(row.event_flags_json)
            flags.update({k: bool(v) for k, v in body["event_flags"].items()})
            row.event_flags_json = json.dumps(flags)
        row.callback_url = f"{settings.public_base_url.rstrip('/')}/api/v1/integrations/infradealer/callback"
        row.updated_at = _now()
        self.db.commit()
        return self.public_config()

    def disconnect(self) -> dict:
        row = self.get_or_create_config()
        row.connected = False
        row.connection_status = "DISCONNECTED"
        row.updated_at = _now()
        self.db.commit()
        return self.public_config()

    def regenerate_secret(self) -> dict:
        row = self.get_or_create_config()
        new_secret = uuid.uuid4().hex + uuid.uuid4().hex
        row.api_secret_enc = encrypt_secret(new_secret)
        row.integration_id = str(uuid.uuid4())
        row.updated_at = _now()
        self.db.commit()
        out = self.public_config()
        out["api_secret_once"] = new_secret
        return out

    def _client(self, row: InfraDealerIntegration | None = None) -> InfraDealerApiClient | None:
        row = row or self.get_or_create_config()
        if not row.base_url or not row.api_key_enc:
            return None
        return InfraDealerApiClient.from_integration(row, timeout=settings.infradealer_timeout)

    def test_connection(self) -> dict:
        row = self.get_or_create_config()
        client = self._client(row)
        if not client:
            return {"ok": False, "error": "Base URL and API key required", "latency_ms": 0}
        result = client.test_connection()
        ok = bool(result.get("ok"))
        row.last_sync_at = _now()
        if ok:
            row.connected = True
            row.connection_status = "CONNECTED"
            row.last_success_at = _now()
            row.avg_latency_ms = result.get("latency_ms") or 0
        else:
            row.connected = False
            row.connection_status = "DISCONNECTED"
            row.last_error_at = _now()
            row.last_error_code = str(result.get("error") or result.get("http_status") or "FAILED")
        self._record_request(
            event_type="CONNECTION_TEST",
            request_id=result.get("request_id") or str(uuid.uuid4()),
            mobile="",
            result=result,
            payload={"event": "connection.test"},
        )
        self.db.commit()
        return {
            "ok": ok,
            "authentication": bool(client.api_key),
            "api": ok,
            "response": bool(result.get("ok")),
            "latency_ms": result.get("latency_ms") or 0,
            "http_status": result.get("http_status"),
            "message": "CONNECTION SUCCESSFUL" if ok else "CONNECTION FAILED",
        }

    def _find_listing_push_outbox(self, draft_id: int) -> InfraDealerOutbox | None:
        return (
            self.db.query(InfraDealerOutbox)
            .filter(
                InfraDealerOutbox.draft_id == draft_id,
                InfraDealerOutbox.event_type == "LISTING_PUSH",
                InfraDealerOutbox.status.in_(list(_LISTING_PUSH_ACTIVE)),
            )
            .order_by(InfraDealerOutbox.id.desc())
            .first()
        )

    def listing_already_pushed(self, conv: AiConversation, draft: AiListingDraft, payload: dict | None = None) -> bool:
        pl = payload or {}
        if pl.get("infradealer_listing_id"):
            return True
        if (draft.status or "").upper() in {"POSTED", "PENDING_REVIEW", "APPROVED", "LIVE"}:
            return True
        if str(pl.get("listing_status") or "").upper() in {"POSTED", "PENDING_REVIEW", "PUSHED_TO_INFRADEALER"}:
            return True
        return self._find_listing_push_outbox(draft.id) is not None

    def enqueue(
        self,
        event_type: str,
        payload: dict,
        *,
        conversation_id: int | None = None,
        draft_id: int | None = None,
        mobile: str = "",
        parent_request_id: str = "",
        request_id: str | None = None,
    ) -> InfraDealerOutbox | None:
        row = self.get_or_create_config()
        flags = load_event_flags(row.event_flags_json)
        if not event_enabled(flags, event_type):
            log.info("event %s disabled", event_type)
            return None
        if not row.base_url or not row.api_key_enc:
            log.info("InfraDealer not configured — skip %s", event_type)
            return None
        rid = request_id or payload.get("request_id") or str(uuid.uuid4())
        body = dict(payload)
        body["request_id"] = rid
        if row.mode == "TEST":
            body["mode"] = "TEST"
        existing = self.db.query(InfraDealerOutbox).filter(InfraDealerOutbox.request_id == rid).first()
        if existing:
            return existing
        item = InfraDealerOutbox(
            event_type=event_type.upper(),
            request_id=rid,
            status="PENDING",
            attempts=0,
            payload_json=json.dumps(body, ensure_ascii=False),
            parent_request_id=parent_request_id or "",
            conversation_id=conversation_id,
            draft_id=draft_id,
            mobile=(mobile or "")[-10:],
            max_attempts=settings.infradealer_max_retries,
        )
        self.db.add(item)
        return item

    def process_outbox_item(self, item: InfraDealerOutbox) -> None:
        row = self.get_or_create_config()
        client = self._client(row)
        if not client:
            item.status = "SKIPPED"
            item.last_error = "not_configured"
            item.processed_at = _now()
            return
        payload = json.loads(item.payload_json or "{}")
        api_event = INTERNAL_TO_API.get(item.event_type.upper(), "")
        if not api_event:
            item.status = "FAILED"
            item.last_error = "unknown_event"
            item.processed_at = _now()
            return
        item.attempts = (item.attempts or 0) + 1
        result = client.request(api_event, payload, request_id=item.request_id)
        body = result.get("body") or {}
        response_class = classify_response(result.get("http_status") or 0, body, result.get("error") or "")
        business_code = str(body.get("code") or body.get("error_code") or "").upper()
        if not business_code and item.event_type.upper() == "ACCOUNT_CHECK":
            if body.get("account_found") is False or (result.get("http_status") == 404):
                business_code = "ACCOUNT_NOT_FOUND"
            elif body.get("account_found") or (body.get("account") or {}).get("user_id"):
                business_code = "ACCOUNT_EXISTS"
        self._record_request(
            event_type=item.event_type,
            request_id=item.request_id,
            mobile=item.mobile,
            result=result,
            payload=payload,
            conversation_id=item.conversation_id,
            draft_id=item.draft_id,
            outbox_id=item.id,
            parent_request_id=item.parent_request_id,
        )
        self._apply_business_result(item, body, business_code, response_class, result.get("http_status") or 0)
        if "otp" in payload:
            item.payload_json = json.dumps(self._redact(payload), ensure_ascii=False)
        ok = result.get("ok") or response_class == "SUCCESS"
        if ok or (response_class == "BUSINESS_ERROR" and business_code in _BUSINESS_OK):
            item.status = "DONE"
            item.business_status = business_code or "SUCCESS"
            item.processed_at = _now()
            row.last_success_at = _now()
            row.connected = True
            row.connection_status = "CONNECTED"
        elif item.event_type.upper() == "OTP_VERIFY":
            item.status = "FAILED"
            item.business_status = business_code or response_class
            item.last_error = business_code or result.get("error") or response_class
            item.processed_at = _now()
            row.last_error_at = _now()
            row.last_error_code = (item.last_error or "")[:80]
        elif should_retry(response_class, result.get("http_status") or 0, business_code):
            if (item.attempts or 0) >= (item.max_attempts or 0):
                item.status = "FAILED"
                item.processed_at = _now()
            else:
                item.status = "RETRY"
                item.next_retry_at = _now() + timedelta(seconds=min(2 ** item.attempts, 300))
            item.last_error = business_code or result.get("error") or str(result.get("http_status"))
            row.last_error_at = _now()
            row.last_error_code = item.last_error[:80]
        else:
            item.status = "FAILED"
            item.business_status = business_code or response_class
            item.last_error = business_code or result.get("error") or response_class
            item.processed_at = _now()
            row.last_error_at = _now()
            row.last_error_code = item.last_error[:80]
        row.last_sync_at = _now()
        if result.get("latency_ms"):
            prev = row.avg_latency_ms or 0
            row.avg_latency_ms = int((prev + result["latency_ms"]) / 2) if prev else result["latency_ms"]

    def _apply_business_result(
        self,
        item: InfraDealerOutbox,
        body: dict,
        code: str,
        response_class: str,
        http_status: int = 0,
    ) -> None:
        state = None
        if item.mobile:
            state = self.get_or_create_account_state(
                item.mobile, conversation_id=item.conversation_id
            )
        et = item.event_type.upper()
        if et == "ACCOUNT_CHECK":
            if body.get("account_found") or code in {"ACCOUNT_EXISTS", "ACCOUNT_FOUND"} or (http_status in {200, 201} and (body.get("account") or {}).get("user_id")):
                if state:
                    state.profile_status = "FOUND"
                    state.account_status = "ACCOUNT_EXISTS"
                    acct = body.get("account") or {}
                    state.infradealer_user_id = str(acct.get("user_id") or state.infradealer_user_id or "")
            elif code == "ACCOUNT_NOT_FOUND" or body.get("account_found") is False or http_status == 404:
                if state:
                    state.profile_status = "NOT_FOUND"
                    state.account_status = "NOT_FOUND"
            if state:
                try:
                    from ..ai.account_filter import apply_remote_account

                    apply_remote_account(state, body if isinstance(body, dict) else {})
                except Exception:
                    log.exception("account detail persist failed for %s", state.mobile)
        elif et == "ACCOUNT_CREATE":
            if state:
                state.account_status = "REQUESTED"
            if code == "OTP_REQUIRED":
                if state:
                    state.account_status = "OTP_PENDING"
                    state.registration_id = str(body.get("registration_id") or "")
            elif code == "ACCOUNT_CREATED" or (body.get("success") and (body.get("account") or {}).get("user_id")):
                if state:
                    state.account_status = "ACCOUNT_CREATED"
                    state.profile_status = "VERIFIED"
                    acct = body.get("account") or {}
                    state.infradealer_user_id = str(acct.get("user_id") or "")
                    try:
                        from ..ai.account_filter import apply_remote_account

                        apply_remote_account(state, body if isinstance(body, dict) else {})
                    except Exception:
                        log.exception("account detail persist failed for %s", state.mobile)
                    self._retry_pending_listing(state)
        elif et == "OTP_VERIFY":
            if code == "OTP_INVALID":
                if state:
                    state.account_status = "OTP_PENDING"
            elif code == "OTP_EXPIRED":
                if state:
                    state.account_status = "OTP_PENDING"
            elif code == "ACCOUNT_CREATED" or body.get("success"):
                if state:
                    state.account_status = "ACCOUNT_CREATED"
                    state.profile_status = "VERIFIED"
                    acct = body.get("account") or {}
                    state.infradealer_user_id = str(acct.get("user_id") or state.infradealer_user_id or "")
                    try:
                        from ..ai.account_filter import apply_remote_account

                        apply_remote_account(state, body if isinstance(body, dict) else {})
                    except Exception:
                        log.exception("account detail persist failed for %s", state.mobile)
                self._retry_pending_listing(state)
        elif et == "MEDIA_PUSH":
            remote = str((body.get("media") or {}).get("media_id") or body.get("media_id") or "")
            if remote and item.payload_json:
                try:
                    saved = json.loads(item.payload_json)
                    saved["remote_media_id"] = remote
                    item.payload_json = json.dumps(saved)
                except json.JSONDecodeError:
                    pass
        elif et == "LISTING_PUSH":
            from ..config import settings
            from .events import listing_public_url

            listing = body.get("listing") if isinstance(body.get("listing"), dict) else {}
            listing_id = str(listing.get("listing_id") or listing.get("id") or body.get("listing_id") or "")
            code = str(code or "").upper()
            ok = bool(body.get("success") or response_class == "SUCCESS")
            auto = settings.infradealer_auto_publish
            pending_review = code in _LISTING_PENDING_CODES or str(listing.get("status") or body.get("status") or "").lower() == "pending"
            live_now = ok and not pending_review and (code in _LISTING_LIVE_CODES or (auto and code not in _LISTING_PENDING_CODES))

            if ok and pending_review:
                if item.draft_id:
                    draft = self.db.get(AiListingDraft, item.draft_id)
                    if draft and draft.status not in {"POSTED", "APPROVED", "LIVE", "REJECTED"}:
                        draft.status = "PENDING_REVIEW"
                if state:
                    meta = {}
                    try:
                        meta = json.loads(state.meta_json or "{}")
                    except json.JSONDecodeError:
                        meta = {}
                    meta["listing_status"] = "PENDING_REVIEW"
                    if listing_id:
                        meta["listing_id"] = listing_id
                    state.meta_json = json.dumps(meta)
                conv = self.db.get(AiConversation, item.conversation_id) if item.conversation_id else None
                if conv:
                    from ..ai.data_push import listing_open_url
                    from ..ai.tools import _payload, _write_payload

                    pl = _payload(conv)
                    pl["listing_status"] = "PENDING_REVIEW"
                    pl["listing_review_notified"] = False
                    pl["push_stage"] = "AWAITING_ADMIN"
                    if listing_id:
                        pl["infradealer_listing_id"] = listing_id
                    url = listing_open_url(listing_id, mobile=conv.mobile, payload=body)
                    if url:
                        pl["listing_url"] = url
                    _write_payload(conv, pl)
            elif ok and live_now:
                if item.draft_id:
                    draft = self.db.get(AiListingDraft, item.draft_id)
                    if draft and draft.status not in {"POSTED", "APPROVED", "LIVE"}:
                        draft.status = "POSTED"
                if state:
                    meta = {}
                    try:
                        meta = json.loads(state.meta_json or "{}")
                    except json.JSONDecodeError:
                        meta = {}
                    meta["listing_status"] = "POSTED"
                    if listing_id:
                        meta["listing_id"] = listing_id
                    state.meta_json = json.dumps(meta)
                conv = self.db.get(AiConversation, item.conversation_id) if item.conversation_id else None
                if conv:
                    from ..ai.data_push import listing_open_url
                    from ..ai.tools import _payload, _write_payload

                    pl = _payload(conv)
                    pl["listing_status"] = "POSTED"
                    pl["listing_review_notified"] = True
                    pl["push_stage"] = "LIVE"
                    if listing_id:
                        pl["infradealer_listing_id"] = listing_id
                    url = listing_open_url(listing_id, mobile=conv.mobile, payload=body)
                    if url:
                        pl["listing_url"] = url
                    _write_payload(conv, pl)
            elif ok and not auto:
                if item.draft_id:
                    draft = self.db.get(AiListingDraft, item.draft_id)
                    if draft and draft.status not in {"POSTED", "APPROVED", "LIVE"}:
                        draft.status = "PENDING_REVIEW"
                if state:
                    meta = {}
                    try:
                        meta = json.loads(state.meta_json or "{}")
                    except json.JSONDecodeError:
                        meta = {}
                    meta["listing_status"] = "PUSHED_TO_INFRADEALER"
                    listing = body.get("listing") or {}
                    if listing.get("listing_id"):
                        meta["listing_id"] = listing.get("listing_id")
                    state.meta_json = json.dumps(meta)
            elif code in {"ACCOUNT_NOT_FOUND", "ACCOUNT_REQUIRED"}:
                if state:
                    state.pending_draft_id = item.draft_id
                    state.account_status = "NOT_FOUND"
                if item.draft_id:
                    draft = self.db.get(AiListingDraft, item.draft_id)
                    if draft:
                        draft.status = "ACCOUNT_REQUIRED"
                conv = self.db.get(AiConversation, item.conversation_id) if item.conversation_id else None
                if conv and state and state.account_status != "OTP_PENDING":
                    name = (conv.customer_name or "Seller")[:120]
                    self.create_account(conv, name)

    def _retry_pending_listing(self, state: InfraDealerAccountState | None) -> None:
        if not state or not state.pending_draft_id:
            return
        draft = self.db.get(AiListingDraft, state.pending_draft_id)
        if not draft:
            state.pending_draft_id = None
            return
        conv = self.db.get(AiConversation, draft.conversation_id)
        if not conv:
            state.pending_draft_id = None
            return
        from ..ai.tools import _payload

        payload = _payload(conv)
        if self.listing_already_pushed(conv, draft, payload):
            state.pending_draft_id = None
            return
        self.push_listing_for_draft(conv, draft, payload)
        state.pending_draft_id = None

    def push_listing_for_draft(self, conv: AiConversation, draft: AiListingDraft, payload: dict) -> InfraDealerOutbox | None:
        rejected = (draft.status or "").upper() == "REJECTED"
        existing = self._find_listing_push_outbox(draft.id)
        if existing and not (existing.status == "DONE" and rejected):
            return existing
        if not rejected and self.listing_already_pushed(conv, draft, payload):
            return existing
        row = self.get_or_create_config()
        flags = load_event_flags(row.event_flags_json)
        state = self.get_or_create_account_state(conv.mobile, conversation_id=conv.id)
        user_id = state.infradealer_user_id if state else ""
        rid = listing_push_request_id(draft.id, rejected=rejected)
        listing_payload = build_listing_payload(self.db, conv, draft, payload, rid, user_id)
        if event_enabled(flags, "media_push"):
            listing_payload = self._push_media_then_listing(listing_payload)
        item = self.enqueue(
            "LISTING_PUSH",
            listing_payload,
            conversation_id=conv.id,
            draft_id=draft.id,
            mobile=conv.mobile,
            request_id=rid,
        )
        if state:
            state.last_request_id = rid
        return item

    def _push_media_then_listing(self, listing_payload: dict) -> dict:
        media = listing_payload.get("media") or []
        updated = []
        from ..models import AiMedia
        from .payloads import build_media_payload

        for item in media:
            local_id = item.get("media_id")
            row = self.db.get(AiMedia, int(local_id)) if str(local_id).isdigit() else None
            mid = str(uuid.uuid4())
            payload = build_media_payload(local_id, (row.kind if row else "image"), (row.mime if row else ""), mid)
            boxed = self.enqueue("MEDIA_PUSH", payload, request_id=mid, mobile=listing_payload.get("customer", {}).get("phone", "")[-10:])
            if boxed:
                self.process_outbox_item(boxed)
                try:
                    saved = json.loads(boxed.payload_json or "{}")
                    remote = saved.get("remote_media_id")
                    if remote:
                        item = {**item, "media_id": remote}
                except json.JSONDecodeError:
                    pass
            updated.append(item)
        listing_payload["media"] = updated
        return listing_payload

    def check_account(self, conv: AiConversation) -> InfraDealerOutbox | None:
        from .payloads import build_account_check_payload

        rid = str(uuid.uuid4())
        payload = build_account_check_payload(conv.mobile, rid)
        item = self.enqueue("ACCOUNT_CHECK", payload, conversation_id=conv.id, mobile=account_mobile(conv.mobile), request_id=rid)
        if item:
            state = self.get_or_create_account_state(
                conv.mobile, conversation_id=conv.id, account_status="CHECKING"
            )
            if state:
                state.account_status = "CHECKING"
                state.last_request_id = rid
        return item

    def create_account(self, conv: AiConversation, name: str) -> InfraDealerOutbox | None:
        from .payloads import build_account_create_payload

        rid = str(uuid.uuid4())
        phone = account_mobile(conv.mobile)
        payload = build_account_create_payload(name, phone, rid)
        return self.enqueue("ACCOUNT_CREATE", payload, conversation_id=conv.id, mobile=phone, request_id=rid)

    def verify_otp_external(self, conv: AiConversation, otp: str, registration_id: str = "") -> InfraDealerOutbox | None:
        from .payloads import build_otp_verify_payload

        state = self.get_or_create_account_state(conv.mobile, conversation_id=conv.id)
        reg = registration_id or (state.registration_id if state else "")
        if not reg:
            return None
        rid = str(uuid.uuid4())
        phone = account_mobile(conv.mobile)
        payload = build_otp_verify_payload(reg, phone, otp, rid)
        return self.enqueue("OTP_VERIFY", payload, conversation_id=conv.id, mobile=phone, request_id=rid)

    def request_otp(self, conv: AiConversation) -> InfraDealerOutbox | None:
        from .payloads import build_otp_request_payload

        state = self.get_or_create_account_state(conv.mobile, conversation_id=conv.id)
        if not state or not state.registration_id:
            return None
        rid = str(uuid.uuid4())
        phone = account_mobile(conv.mobile)
        payload = build_otp_request_payload(state.registration_id, phone, rid)
        return self.enqueue("OTP_REQUEST", payload, conversation_id=conv.id, mobile=phone, request_id=rid)

    def handle_callback(self, payload: dict, signature: str = "", timestamp: str = "") -> dict:
        event = normalize_callback_event(payload)
        request_id = str(payload.get("request_id") or "")
        listing = payload.get("listing") if isinstance(payload.get("listing"), dict) else {}
        listing_id = str(listing.get("listing_id") or listing.get("id") or payload.get("listing_id") or "")
        callback_id = str(payload.get("callback_id") or request_id or listing_id or str(uuid.uuid4()))
        if listing_id and event in {"listing.posted", "listing.rejected"}:
            dup_lid = (
                self.db.query(InfraDealerCallback)
                .filter(
                    InfraDealerCallback.event_type == event,
                    InfraDealerCallback.payload_json.like(f"%{listing_id}%"),
                    InfraDealerCallback.processed.is_(True),
                )
                .first()
            )
            if dup_lid:
                return {"ok": True, "duplicate": True}
        dup = self.db.query(InfraDealerCallback).filter(InfraDealerCallback.callback_id == callback_id).first()
        if dup and dup.processed:
            return {"ok": True, "duplicate": True}
        if request_id and event:
            dup2 = (
                self.db.query(InfraDealerCallback)
                .filter(
                    InfraDealerCallback.request_id == request_id,
                    InfraDealerCallback.event_type == event,
                    InfraDealerCallback.processed.is_(True),
                )
                .first()
            )
            if dup2:
                return {"ok": True, "duplicate": True}
        row = InfraDealerCallback(
            callback_id=callback_id,
            event_type=event,
            request_id=request_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
            status="RECEIVED",
        )
        self.db.add(row)
        if event == "listing.posted":
            self._on_listing_posted(request_id, listing_id, payload)
        elif event == "listing.rejected":
            self._on_listing_rejected(request_id, listing_id, payload)
        elif event == "account.created":
            self._on_account_created(payload)
        row.processed = True
        row.status = "PROCESSED"
        self.db.commit()
        return {"ok": True}

    def log_callback_attempt(self, payload: dict, *, status: str, error: str = "") -> None:
        callback_id = str(payload.get("callback_id") or payload.get("request_id") or uuid.uuid4())
        event = normalize_callback_event(payload if isinstance(payload, dict) else {})
        row = InfraDealerCallback(
            callback_id=f"fail-{callback_id}-{int(_now().timestamp())}",
            event_type=event or "unknown",
            request_id=str(payload.get("request_id") or ""),
            payload_json=json.dumps(payload if isinstance(payload, dict) else {"raw": str(payload)[:500]}, ensure_ascii=False),
            status=status,
            processed=False,
        )
        if error:
            row.payload_json = json.dumps(
                {"error": error[:280], "payload": payload if isinstance(payload, dict) else {}},
                ensure_ascii=False,
            )
        self.db.add(row)
        self.db.commit()

    def _resolve_request(self, request_id: str, listing_id: str = "", phone: str = "") -> InfraDealerRequest | None:
        if request_id:
            row = (
                self.db.query(InfraDealerRequest)
                .filter(InfraDealerRequest.request_id == request_id)
                .order_by(InfraDealerRequest.id.desc())
                .first()
            )
            if row:
                return row
        digits = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
        if digits:
            row = (
                self.db.query(InfraDealerRequest)
                .filter(
                    InfraDealerRequest.mobile == digits,
                    InfraDealerRequest.event_type == "LISTING_PUSH",
                )
                .order_by(InfraDealerRequest.id.desc())
                .first()
            )
            if row:
                return row
        if listing_id:
            states = (
                self.db.query(InfraDealerAccountState)
                .filter(InfraDealerAccountState.meta_json.like(f"%{listing_id}%"))
                .all()
            )
            for state in states:
                if state.last_request_id:
                    row = (
                        self.db.query(InfraDealerRequest)
                        .filter(InfraDealerRequest.request_id == state.last_request_id)
                        .order_by(InfraDealerRequest.id.desc())
                        .first()
                    )
                    if row:
                        return row
                if state.conversation_id:
                    row = (
                        self.db.query(InfraDealerRequest)
                        .filter(
                            InfraDealerRequest.conversation_id == state.conversation_id,
                            InfraDealerRequest.event_type == "LISTING_PUSH",
                        )
                        .order_by(InfraDealerRequest.id.desc())
                        .first()
                    )
                    if row:
                        return row
        return None

    def _resolve_conversation(self, req: InfraDealerRequest | None, payload: dict) -> AiConversation | None:
        if req and req.conversation_id:
            conv = self.db.get(AiConversation, req.conversation_id)
            if conv:
                return conv
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        phone = str(customer.get("phone") or payload.get("phone") or (req.mobile if req else "") or "")
        digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
        if not digits:
            return None
        return (
            self.db.query(AiConversation)
            .filter(AiConversation.mobile == digits)
            .order_by(AiConversation.id.desc())
            .first()
        )

    def _notify_customer(self, conv: AiConversation | None, text: str, *, preview_url: bool = False) -> None:
        msg = (text or "").strip()
        if not conv or not msg or self.is_test_mode():
            return
        try:
            from ..services import get_or_create_settings, send_whatsapp_fast, store_chat

            # Flush so listing status is durable before Graph call
            try:
                self.db.flush()
            except Exception:
                pass
            meta = get_or_create_settings(self.db)
            result = send_whatsapp_fast(meta, conv.mobile, msg, preview_url=preview_url)
            store_chat(
                self.db,
                wamid=result.get("wamid") or f"cb.{conv.id}.{int(_now().timestamp())}",
                conversation_id=conv.conversation_id or f"CONV_{conv.mobile}",
                from_mobile=meta.phone_number_id or "infradealer",
                from_name="InfraDealer",
                to_mobile=conv.mobile,
                direction="outbound",
                body=msg,
                status="sent",
                unread=False,
            )
            log.info("customer WhatsApp notify ok mobile=***%s", (conv.mobile or "")[-4:])
        except Exception:
            log.exception("customer WhatsApp notify failed for %s", conv.mobile)

    def _on_listing_posted(self, request_id: str, listing_id: str, payload: dict) -> None:
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        req = self._resolve_request(request_id, listing_id, str(customer.get("phone") or payload.get("phone") or ""))
        conv = self._resolve_conversation(req, payload)
        draft = self.db.get(AiListingDraft, req.draft_id) if req and req.draft_id else None
        if not conv:
            if draft:
                draft.status = "POSTED"
            return
        from ..ai.data_push import apply_admin_decision

        text = apply_admin_decision(
            self.db,
            conv,
            approved=True,
            listing_id=listing_id,
            payload=payload,
            draft=draft,
        )
        if text:
            from ..ai.tools import _payload

            url = str(_payload(conv).get("listing_url") or "")
            self._notify_customer(conv, text, preview_url=bool(url))

    def _on_listing_rejected(self, request_id: str, listing_id: str, payload: dict) -> None:
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        req = self._resolve_request(request_id, listing_id, str(customer.get("phone") or payload.get("phone") or ""))
        conv = self._resolve_conversation(req, payload)
        draft = self.db.get(AiListingDraft, req.draft_id) if req and req.draft_id else None
        if not conv:
            if draft:
                draft.status = "REJECTED"
            return
        from ..ai.data_push import apply_admin_decision
        from ..infradealer.events import listing_reject_reason

        text = apply_admin_decision(
            self.db,
            conv,
            approved=False,
            listing_id=listing_id,
            payload=payload,
            reason=listing_reject_reason(payload),
            draft=draft,
        )
        if text:
            self._notify_customer(conv, text)

    def _on_account_created(self, payload: dict) -> None:
        acct = payload.get("account") or {}
        phone = str(acct.get("phone") or "").replace("+91", "")[-10:]
        if not phone:
            return
        state = self.get_or_create_account_state(phone)
        if not state:
            return
        state.account_status = "ACCOUNT_CREATED"
        state.profile_status = "VERIFIED"
        state.infradealer_user_id = str(acct.get("user_id") or "")
        self._retry_pending_listing(state)

    @staticmethod
    def _remote_listing_status(body: dict | None) -> str:
        data = body or {}
        listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
        extra = data.get("data") if isinstance(data.get("data"), dict) else {}
        code = str(data.get("code") or data.get("error_code") or "").upper()
        if code in {"LISTING_POSTED", "LIVE", "POSTED", "PUBLISHED", "APPROVED"}:
            return "posted"
        if code == "LISTING_REJECTED":
            return "rejected"
        if code in {"LISTING_PENDING_REVIEW", "PENDING_REVIEW", "PENDING"}:
            return "pending"
        for src in (listing, extra, data):
            if not isinstance(src, dict):
                continue
            status = str(src.get("status") or src.get("listing_status") or "").strip().upper()
            if status in {"POSTED", "LIVE", "PUBLISHED", "APPROVED", "ACTIVE"}:
                return "posted"
            if status in {"REJECTED", "DECLINED", "DENIED"}:
                return "rejected"
            if status in {"PENDING", "PENDING_REVIEW", "REVIEW", "UNDER_REVIEW"}:
                return "pending"
        return "unknown"

    def poll_pending_listings(self, limit: int = 15) -> int:
        if not self.is_configured() or self.is_test_mode():
            return 0
        client = self._client()
        if not client:
            return 0
        rows = (
            self.db.query(InfraDealerRequest)
            .filter(
                InfraDealerRequest.event_type == "LISTING_PUSH",
                InfraDealerRequest.status == "SUCCESS",
                InfraDealerRequest.business_code.in_(list(_LISTING_PENDING_CODES) + ["", "SUCCESS"]),
            )
            .order_by(InfraDealerRequest.id.desc())
            .limit(min(limit, 50))
            .all()
        )
        checked = 0
        for req in rows:
            conv = self.db.get(AiConversation, req.conversation_id) if req.conversation_id else None
            if not conv:
                continue
            from ..ai.tools import _payload

            pl = _payload(conv)
            if pl.get("listing_review_notified"):
                continue
            listing_status = str(pl.get("listing_status") or "").upper()
            if listing_status in {"POSTED", "REJECTED"}:
                continue
            if listing_status not in {"PENDING_REVIEW", "PUSHED_TO_INFRADEALER", "CONFIRMED", ""}:
                continue
            try:
                saved = json.loads(req.response_json or "{}")
            except json.JSONDecodeError:
                saved = {}
            listing = saved.get("listing") if isinstance(saved.get("listing"), dict) else {}
            listing_id = str(
                pl.get("infradealer_listing_id")
                or listing.get("listing_id")
                or listing.get("id")
                or ""
            )
            result = client.get_status(request_id=req.request_id, listing_id=listing_id)
            body = result.get("body") or {}
            remote = self._remote_listing_status(body)
            checked += 1
            if remote == "pending":
                continue
            notify_payload = dict(body)
            notify_payload.setdefault("request_id", req.request_id)
            if listing_id and "listing" not in notify_payload:
                notify_payload["listing"] = {"listing_id": listing_id, "status": remote.upper()}
            if remote == "posted":
                self._on_listing_posted(req.request_id, listing_id, notify_payload)
            elif remote == "rejected":
                self._on_listing_rejected(req.request_id, listing_id, notify_payload)
        return checked

    def _record_request(
        self,
        *,
        event_type: str,
        request_id: str,
        mobile: str,
        result: dict,
        payload: dict,
        conversation_id: int | None = None,
        draft_id: int | None = None,
        outbox_id: int | None = None,
        parent_request_id: str = "",
    ) -> InfraDealerRequest:
        body = result.get("body") or {}
        code = str(body.get("code") or body.get("error_code") or "").upper()
        response_class = classify_response(result.get("http_status") or 0, body, result.get("error") or "")
        safe_payload = self._redact(payload)
        safe_response = self._redact(body if isinstance(body, dict) else {})
        status = "SUCCESS" if result.get("ok") or response_class == "SUCCESS" else (
            "BUSINESS" if response_class == "BUSINESS_ERROR" else "FAILED"
        )
        row = InfraDealerRequest(
            request_id=request_id,
            event_type=event_type,
            mobile=(mobile or "")[-10:],
            user_id=str((body.get("account") or {}).get("user_id") or ""),
            http_status=result.get("http_status") or 0,
            status=status,
            response_class=response_class,
            business_code=code,
            latency_ms=result.get("latency_ms") or 0,
            safe_headers_json=json.dumps(result.get("safe_headers") or {}),
            payload_json=json.dumps(safe_payload, ensure_ascii=False),
            response_json=json.dumps(safe_response, ensure_ascii=False),
            error_message=str(result.get("error") or "")[:280],
            conversation_id=conversation_id,
            draft_id=draft_id,
            outbox_id=outbox_id,
            parent_request_id=parent_request_id,
        )
        self.db.add(row)
        return row

    def _redact(self, data: dict) -> dict:
        out = {}
        for k, v in (data or {}).items():
            key = str(k).lower()
            if key in {"otp", "api_secret", "password", "signature"}:
                out[k] = "[REDACTED]"
            elif isinstance(v, dict):
                out[k] = self._redact(v)
            else:
                out[k] = v
        return out

    def dashboard_stats(self) -> dict:
        today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        q = self.db.query(InfraDealerRequest).filter(InfraDealerRequest.created_at >= today)
        total = q.count()
        success = q.filter(InfraDealerRequest.status == "SUCCESS").count()
        failed = q.filter(InfraDealerRequest.status == "FAILED").count()
        pending = self.db.query(InfraDealerOutbox).filter(InfraDealerOutbox.status.in_(["PENDING", "RETRY"])).count()
        account_matches = (
            self.db.query(InfraDealerRequest)
            .filter(InfraDealerRequest.event_type == "ACCOUNT_CHECK", InfraDealerRequest.status == "SUCCESS")
            .count()
        )
        new_accounts = (
            self.db.query(InfraDealerAccountState)
            .filter(InfraDealerAccountState.account_status == "ACCOUNT_CREATED")
            .count()
        )
        otp_pending = (
            self.db.query(InfraDealerAccountState)
            .filter(InfraDealerAccountState.account_status == "OTP_PENDING")
            .count()
        )
        listing_pushes = self.db.query(InfraDealerRequest).filter(InfraDealerRequest.event_type == "LISTING_PUSH").count()
        listing_errors = (
            self.db.query(InfraDealerRequest)
            .filter(InfraDealerRequest.event_type == "LISTING_PUSH", InfraDealerRequest.status != "SUCCESS")
            .count()
        )
        callback_errors = (
            self.db.query(InfraDealerCallback)
            .filter(InfraDealerCallback.status.in_(["FAILED", "ERROR"]))
            .count()
        )
        return {
            "requests_today": total,
            "successful": success,
            "failed": failed,
            "pending": pending,
            "account_matches": account_matches,
            "new_accounts": new_accounts,
            "otp_pending": otp_pending,
            "listing_pushes": listing_pushes,
            "listing_errors": listing_errors,
            "callback_errors": callback_errors,
        }

    def health_snapshot(self, row: InfraDealerIntegration | None = None) -> dict:
        row = row or self.get_or_create_config()
        stats = self.dashboard_stats()
        total = stats["requests_today"] or 1
        failure_rate = round((stats["failed"] / total) * 100, 1)
        return {
            "api_online": row.connection_status == "CONNECTED",
            "authentication_valid": bool(row.api_key_enc),
            "last_successful_request": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error": row.last_error_at.isoformat() if row.last_error_at else None,
            "average_latency_ms": row.avg_latency_ms or 0,
            "failure_rate": failure_rate,
        }

    def list_ledger(self, filters: dict) -> list[dict]:
        q = self.db.query(InfraDealerRequest).order_by(InfraDealerRequest.id.desc())
        if filters.get("phone"):
            q = q.filter(InfraDealerRequest.mobile.contains(str(filters["phone"])[-10:]))
        if filters.get("request_id"):
            q = q.filter(InfraDealerRequest.request_id.contains(filters["request_id"]))
        if filters.get("user_id"):
            q = q.filter(InfraDealerRequest.user_id.contains(filters["user_id"]))
        if filters.get("event"):
            q = q.filter(InfraDealerRequest.event_type == filters["event"].upper())
        if filters.get("status"):
            q = q.filter(InfraDealerRequest.status == filters["status"].upper())
        if filters.get("failed_only"):
            q = q.filter(InfraDealerRequest.status == "FAILED")
        if filters.get("pending_only"):
            pending_ids = [
                o.request_id
                for o in self.db.query(InfraDealerOutbox.request_id).filter(InfraDealerOutbox.status.in_(["PENDING", "RETRY"]))
            ]
            q = q.filter(InfraDealerRequest.request_id.in_(pending_ids or ["__none__"]))
        if filters.get("account_only"):
            q = q.filter(InfraDealerRequest.event_type.in_(["ACCOUNT_CHECK", "ACCOUNT_CREATE", "OTP_REQUEST", "OTP_VERIFY"]))
        if filters.get("listing_only"):
            q = q.filter(InfraDealerRequest.event_type == "LISTING_PUSH")
        if filters.get("date_from"):
            q = q.filter(InfraDealerRequest.created_at >= filters["date_from"])
        if filters.get("date_to"):
            q = q.filter(InfraDealerRequest.created_at <= filters["date_to"])
        limit = min(int(filters.get("limit") or 100), 500)
        rows = q.limit(limit).all()
        ids = [r.request_id for r in rows]
        attempts = {}
        if ids:
            for ob in self.db.query(InfraDealerOutbox).filter(InfraDealerOutbox.request_id.in_(ids)):
                attempts[ob.request_id] = ob.attempts
        return [
            {
                "id": r.id,
                "time": r.created_at.isoformat() if r.created_at else None,
                "request_id": r.request_id,
                "event": r.event_type,
                "phone": self._mask_phone(r.mobile),
                "user_id": r.user_id or "—",
                "status": r.status,
                "business_code": r.business_code,
                "attempts": attempts.get(r.request_id, 1),
                "http_status": r.http_status,
                "latency_ms": r.latency_ms,
                "conversation_id": r.conversation_id,
                "draft_id": r.draft_id,
            }
            for r in rows
        ]

    def get_request_detail(self, request_id: str) -> dict | None:
        row = (
            self.db.query(InfraDealerRequest)
            .filter(InfraDealerRequest.request_id == request_id)
            .order_by(InfraDealerRequest.id.desc())
            .first()
        )
        if not row:
            return None
        return {
            "request_id": row.request_id,
            "event": row.event_type,
            "status": row.status,
            "response_class": row.response_class,
            "business_code": row.business_code,
            "http_status": row.http_status,
            "latency_ms": row.latency_ms,
            "headers": json.loads(row.safe_headers_json or "{}"),
            "payload": json.loads(row.payload_json or "{}"),
            "response": json.loads(row.response_json or "{}"),
            "error": row.error_message,
            "conversation_id": row.conversation_id,
            "draft_id": row.draft_id,
            "parent_request_id": row.parent_request_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def list_callbacks(self, limit: int = 100) -> list[dict]:
        rows = (
            self.db.query(InfraDealerCallback)
            .order_by(InfraDealerCallback.id.desc())
            .limit(min(limit, 500))
            .all()
        )
        return [
            {
                "id": r.id,
                "time": r.created_at.isoformat() if r.created_at else None,
                "callback_id": r.callback_id,
                "event": r.event_type,
                "request_id": r.request_id,
                "status": r.status,
                "processed": r.processed,
            }
            for r in rows
        ]

    def list_errors(self, limit: int = 50) -> list[dict]:
        rows = (
            self.db.query(InfraDealerRequest)
            .filter(InfraDealerRequest.status.in_(["FAILED", "BUSINESS"]))
            .order_by(InfraDealerRequest.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "time": r.created_at.isoformat() if r.created_at else None,
                "request_id": r.request_id,
                "event": r.event_type,
                "phone": self._mask_phone(r.mobile),
                "error_code": r.business_code or r.response_class,
                "retry_status": r.status,
                "next_action": "manual_retry" if r.response_class in {"NETWORK_ERROR", "SERVER_ERROR"} else "review",
            }
            for r in rows
        ]

    def manual_retry(self, request_id: str) -> dict:
        orig = (
            self.db.query(InfraDealerOutbox)
            .filter(InfraDealerOutbox.request_id == request_id)
            .first()
        )
        if not orig:
            req = self.db.query(InfraDealerRequest).filter(InfraDealerRequest.request_id == request_id).first()
            if not req:
                return {"ok": False, "error": "Request not found"}
            payload = json.loads(req.payload_json or "{}")
            item = self.enqueue(
                req.event_type,
                payload,
                conversation_id=req.conversation_id,
                draft_id=req.draft_id,
                mobile=req.mobile,
                parent_request_id=request_id,
            )
        else:
            if orig.status == "DONE":
                return {"ok": False, "error": "Already succeeded"}
            orig.status = "PENDING"
            orig.next_retry_at = None
            item = orig
        if item:
            self.process_outbox_item(item)
            self.db.commit()
            return {"ok": True, "request_id": item.request_id, "status": item.status}
        return {"ok": False, "error": "Could not retry"}

    @staticmethod
    def _mask_phone(mobile: str) -> str:
        digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
        if len(digits) < 4:
            return "—"
        return f"+91{'X' * (len(digits) - 4)}{digits[-4:]}"
