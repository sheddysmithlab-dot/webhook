"""InfraDealer four-agent master orchestrator / workflow engine.

Coordinates account_filter → chat_memory → Data_filter → data_push as ONE
shared system with correlation IDs, workflow authority, and structured events.

Does NOT replace specialized agents — only routes and commits shared state.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AiConversation, AiEvent
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.orchestrator")

ORCHESTRATOR_VERSION = "four-agent-1.0"
EVENT_SCHEMA_VERSION = "1.0"

# Master workflow states (markdown §6) — authority lives here
MASTER_STATES = {
    "NEW",
    "IDENTITY_RESOLVED",
    "INTENT_IDENTIFIED",
    "ACCOUNT_CREATION_REQUIRED",
    "ACCOUNT_VERIFICATION",
    "LISTING_CREATION",
    "DATA_COLLECTION",
    "DATA_VALIDATION",
    "WAITING_FOR_MISSING_DATA",
    "WAITING_FOR_USER_CONFIRMATION",
    "USER_CONFIRMED",
    "SUBMISSION_PENDING",
    "SUBMITTED",
    "UNDER_REVIEW",
    "APPROVED",
    "LIVE",
    "REJECTED",
    "USER_NOTIFIED",
    "CORRECTION_REQUIRED",
    "RESUBMITTED",
    "RETRYING",
    "FAILED",
    "RECOVERY_PENDING",
    "HUMAN_HANDOFF",
    "INVALID_IDENTITY",
}

# chat_memory rm_state → master workflow state
_RM_TO_MASTER = {
    "NEW_SESSION": "NEW",
    "ACCOUNT_CONTEXT_LOADED": "IDENTITY_RESOLVED",
    "IDENTITY_PENDING": "ACCOUNT_CREATION_REQUIRED",
    "ACCOUNT_CREATION": "ACCOUNT_CREATION_REQUIRED",
    "OTP_VERIFICATION": "ACCOUNT_VERIFICATION",
    "INTENT_DETECTION": "INTENT_IDENTIFIED",
    "CATEGORY_SELECTION": "LISTING_CREATION",
    "LISTING_CREATION": "LISTING_CREATION",
    "DATA_COLLECTION": "DATA_COLLECTION",
    "WAITING_FOR_MISSING_DATA": "WAITING_FOR_MISSING_DATA",
    "DATA_VALIDATION": "DATA_VALIDATION",
    "WAITING_FOR_USER_CONFIRMATION": "WAITING_FOR_USER_CONFIRMATION",
    "USER_CONFIRMED": "USER_CONFIRMED",
    "SUBMISSION_IN_PROGRESS": "SUBMISSION_PENDING",
    "SUBMITTED_TO_ADMIN": "SUBMITTED",
    "UNDER_REVIEW": "UNDER_REVIEW",
    "APPROVED": "APPROVED",
    "LIVE": "LIVE",
    "REVISION_REQUIRED": "CORRECTION_REQUIRED",
    "REJECTED": "REJECTED",
    "HUMAN_HANDOFF": "HUMAN_HANDOFF",
    "PAUSED": "DATA_COLLECTION",
    "DATA_CONFLICT_PENDING_USER_DECISION": "WAITING_FOR_MISSING_DATA",
}

EVENT_TYPES = {
    "USER_MESSAGE_RECEIVED",
    "IDENTITY_RESOLVED",
    "ACCOUNT_NOT_FOUND",
    "ACCOUNT_CREATED",
    "ACCOUNT_VERIFIED",
    "INTENT_DETECTED",
    "WORKFLOW_STARTED",
    "DRAFT_CREATED",
    "DRAFT_UPDATED",
    "DATA_FILTER_REQUESTED",
    "DATA_FILTER_COMPLETED",
    "DATA_MISSING",
    "DATA_CONFLICT",
    "DUPLICATE_DETECTED",
    "READY_FOR_CONFIRMATION",
    "USER_CONFIRMED",
    "USER_CHANGED_DATA",
    "SUBMISSION_REQUESTED",
    "SUBMISSION_SENT",
    "ADMIN_ACKNOWLEDGED",
    "ADMIN_REVIEW_STARTED",
    "LISTING_APPROVED",
    "LISTING_REJECTED",
    "LISTING_LIVE",
    "USER_NOTIFICATION_REQUIRED",
    "HUMAN_HANDOFF_REQUIRED",
    "SYSTEM_ERROR",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_ids() -> dict:
    return {
        "event_id": f"EVT-{uuid.uuid4().hex[:10]}",
        "request_id": f"REQ-{uuid.uuid4().hex[:12]}",
        "correlation_id": f"COR-{uuid.uuid4().hex[:12]}",
    }


def ensure_correlation(payload: dict, conv: AiConversation, ids: dict | None = None) -> dict:
    """Shared correlation chain — all four agents must preserve these."""
    ids = ids or {}
    if not payload.get("workflow_id"):
        payload["workflow_id"] = f"WF-{conv.id}"
    if not payload.get("conversation_id"):
        payload["conversation_id"] = conv.conversation_id
    if ids.get("request_id"):
        payload["request_id"] = ids["request_id"]
    elif not payload.get("request_id"):
        payload["request_id"] = f"REQ-{uuid.uuid4().hex[:12]}"
    if ids.get("correlation_id"):
        payload["correlation_id"] = ids["correlation_id"]
    elif not payload.get("correlation_id"):
        payload["correlation_id"] = payload["request_id"]
    if ids.get("event_id"):
        payload["last_event_id"] = ids["event_id"]
    if conv.draft_id:
        payload["draft_id"] = conv.draft_id
    payload.setdefault("draft_version", int(payload.get("draft_version") or 1))
    return payload


def correlation_snapshot(payload: dict, conv: AiConversation) -> dict:
    return {
        "account_id": payload.get("profile_id") or payload.get("infradealer_user_id"),
        "conversation_id": payload.get("conversation_id") or conv.conversation_id,
        "workflow_id": payload.get("workflow_id"),
        "draft_id": payload.get("draft_id") or conv.draft_id,
        "draft_version": payload.get("draft_version") or 1,
        "request_id": payload.get("request_id"),
        "event_id": payload.get("last_event_id"),
        "correlation_id": payload.get("correlation_id"),
    }


def emit_event(
    db: Session,
    conv: AiConversation,
    event_type: str,
    *,
    source_agent: str,
    payload: dict | None = None,
    detail: dict | None = None,
) -> dict:
    """Centralized structured event (agents must not pass free-form text alone)."""
    pl = payload if payload is not None else _payload(conv)
    event = {
        "event_id": f"EVT-{uuid.uuid4().hex[:10]}",
        "event_type": event_type,
        "timestamp": _now_iso(),
        "source_agent": source_agent,
        "schema_version": EVENT_SCHEMA_VERSION,
        "orchestrator_version": ORCHESTRATOR_VERSION,
        **correlation_snapshot(pl, conv),
        "payload": detail or {},
    }
    db.add(AiEvent(
        wamid=conv.last_wamid or "",
        mobile=conv.mobile,
        event_type=event_type[:64],
        detail=json.dumps(event, ensure_ascii=False, default=str)[:4000],
    ))
    history = list(pl.get("agent_events") or [])
    history.append({
        "event_type": event_type,
        "source_agent": source_agent,
        "event_id": event["event_id"],
        "ts": event["timestamp"],
    })
    pl["agent_events"] = history[-50:]
    pl["last_event_id"] = event["event_id"]
    _write_payload(conv, pl)
    return event


def handshake(
    *,
    source_agent: str,
    target_agent: str,
    event_type: str,
    workflow_id: str = "",
    account_id: Any = None,
    draft_id: Any = None,
    draft_version: int = 1,
    request_id: str = "",
    correlation_id: str = "",
    payload: dict | None = None,
) -> dict:
    """Inter-agent call envelope (markdown §45)."""
    rid = request_id or f"REQ-{uuid.uuid4().hex[:12]}"
    return {
        "request_id": rid,
        "correlation_id": correlation_id or rid,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "event_type": event_type,
        "workflow_id": workflow_id,
        "account_id": account_id,
        "draft_id": draft_id,
        "draft_version": draft_version,
        "payload": payload or {},
    }


def handshake_response(
    request: dict,
    *,
    source_agent: str,
    result_type: str,
    workflow_state: str,
    success: bool = True,
    payload: dict | None = None,
) -> dict:
    return {
        "request_id": request.get("request_id"),
        "success": success,
        "source_agent": source_agent,
        "result_type": result_type,
        "workflow_state": workflow_state,
        "payload": payload or {},
    }


def commit_workflow_state(
    db: Session,
    conv: AiConversation,
    new_state: str,
    *,
    source_agent: str = "orchestrator",
    reason: str = "",
) -> str:
    """Only the workflow engine commits master state transitions."""
    state = (new_state or "").upper()
    if state not in MASTER_STATES:
        # Map unknown → keep prior or NEW
        mapped = _RM_TO_MASTER.get(new_state, "")
        state = mapped if mapped in MASTER_STATES else state
    if state not in MASTER_STATES:
        return _payload(conv).get("master_workflow_state") or "NEW"

    payload = _payload(conv)
    old = payload.get("master_workflow_state") or "NEW"
    if old == state:
        return state
    payload["master_workflow_state"] = state
    payload["workflow_state"] = state
    _write_payload(conv, payload)
    emit_event(
        db,
        conv,
        "WORKFLOW_STARTED" if old in {"", "NEW"} else "DRAFT_UPDATED",
        source_agent=source_agent,
        detail={"old_state": old, "new_state": state, "reason": reason},
    )
    return state


def sync_master_from_rm(db: Session, conv: AiConversation) -> str:
    payload = _payload(conv)
    rm = payload.get("rm_state") or payload.get("workflow_state") or ""
    listing = str(payload.get("listing_status") or "").upper()
    if listing == "LIVE":
        target = "LIVE"
    elif listing in {"REJECTED"}:
        target = "REJECTED"
    elif listing in {"PENDING_REVIEW", "UNDER_REVIEW", "READY_FOR_REVIEW"}:
        target = "UNDER_REVIEW"
    elif listing in {"APPROVED"}:
        target = "APPROVED"
    else:
        target = _RM_TO_MASTER.get(rm, payload.get("master_workflow_state") or "NEW")
    return commit_workflow_state(db, conv, target, source_agent="orchestrator", reason=f"rm={rm}")


def run_account_filter(db: Session, conv: AiConversation, request: dict) -> dict:
    from .account_filter import resolve_identity, sync_conversation_account

    sync_conversation_account(db, conv)
    resolved = resolve_identity(
        db,
        conv,
        request_id=str(request.get("request_id") or ""),
        event_id=str(request.get("correlation_id") or ""),
    )
    payload = _payload(conv)
    ensure_correlation(payload, conv)
    payload["account_context"] = resolved.get("account_context_compact") or payload.get("account_context")
    if resolved.get("account", {}).get("found"):
        commit_workflow_state(db, conv, "IDENTITY_RESOLVED", source_agent="account_filter")
        emit_event(db, conv, "IDENTITY_RESOLVED", source_agent="account_filter", detail={
            "eligibility": resolved.get("account", {}).get("eligibility"),
            "type": resolved.get("account", {}).get("type"),
        })
    else:
        commit_workflow_state(db, conv, "ACCOUNT_CREATION_REQUIRED", source_agent="account_filter")
        emit_event(db, conv, "ACCOUNT_NOT_FOUND", source_agent="account_filter")
    _write_payload(conv, payload)
    return handshake_response(
        request,
        source_agent="account_filter",
        result_type="IDENTITY_CONTEXT",
        workflow_state=payload.get("master_workflow_state") or "IDENTITY_RESOLVED",
        payload=resolved,
    )


def run_data_filter(db: Session, conv: AiConversation, request: dict) -> dict:
    from .data_filter import filter_memory

    emit_event(db, conv, "DATA_FILTER_REQUESTED", source_agent="orchestrator")
    commit_workflow_state(db, conv, "DATA_VALIDATION", source_agent="Data_filter")
    result = filter_memory(db, conv)
    data = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
    readiness = str(data.get("readiness") or "")
    if readiness == "READY_FOR_CONFIRMATION" or data.get("ready"):
        emit_event(db, conv, "READY_FOR_CONFIRMATION", source_agent="Data_filter", detail={"readiness": readiness})
    elif data.get("conflicts"):
        emit_event(db, conv, "DATA_CONFLICT", source_agent="Data_filter", detail={"conflicts": data.get("conflicts")})
    elif data.get("missing_fields"):
        emit_event(db, conv, "DATA_MISSING", source_agent="Data_filter", detail={"missing": data.get("missing_fields")})
    else:
        emit_event(db, conv, "DATA_FILTER_COMPLETED", source_agent="Data_filter", detail={"readiness": readiness})
    return handshake_response(
        request,
        source_agent="Data_filter",
        result_type="FILTER_RESULT",
        workflow_state=_payload(conv).get("master_workflow_state") or "DATA_VALIDATION",
        payload=data,
    )


def run_data_push(db: Session, conv: AiConversation, request: dict) -> dict:
    from .data_push import push_listing

    emit_event(db, conv, "SUBMISSION_REQUESTED", source_agent="orchestrator")
    commit_workflow_state(db, conv, "SUBMISSION_PENDING", source_agent="data_push")
    result = push_listing(db, conv)
    out = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
    if out.get("ok"):
        commit_workflow_state(db, conv, "SUBMITTED", source_agent="data_push")
        emit_event(db, conv, "SUBMISSION_SENT", source_agent="data_push", detail={
            "submission_id": out.get("submission_id"),
            "idempotency_key": out.get("idempotency_key"),
        })
    elif out.get("status") == "RETRY":
        commit_workflow_state(db, conv, "RETRYING", source_agent="data_push")
        emit_event(db, conv, "SYSTEM_ERROR", source_agent="data_push", detail=out)
    else:
        emit_event(db, conv, "SYSTEM_ERROR", source_agent="data_push", detail=out)
    return handshake_response(
        request,
        source_agent="data_push",
        result_type="PUSH_RESULT",
        workflow_state=_payload(conv).get("master_workflow_state") or "SUBMISSION_PENDING",
        success=bool(out.get("ok")),
        payload=out,
    )


def handle_admin_status_event(db: Session, conv: AiConversation, event: dict) -> dict:
    """Admin → data_push → workflow → chat_memory notification path."""
    from .chat_memory import handle_admin_approval, handle_admin_rejection
    from .data_push import process_admin_event
    from .i18n import pick_language

    payload = _payload(conv)
    lang = pick_language("", str(getattr(conv, "language", "") or ""), "auto")
    req = handshake(
        source_agent="admin",
        target_agent="data_push",
        event_type=str(event.get("event") or "ADMIN_EVENT"),
        workflow_id=str(payload.get("workflow_id") or ""),
        account_id=payload.get("profile_id"),
        draft_id=payload.get("draft_id") or conv.draft_id,
        draft_version=int(payload.get("draft_version") or 1),
        request_id=str(payload.get("request_id") or ""),
        payload=event,
    )
    result = process_admin_event(db, conv, event)
    status = str(result.get("status") or "").upper()
    reply = ""
    if "REJECT" in status or status == "REJECTED":
        commit_workflow_state(db, conv, "REJECTED", source_agent="data_push")
        emit_event(db, conv, "LISTING_REJECTED", source_agent="data_push", detail=result)
        reply = handle_admin_rejection(db, conv, event, lang)
        commit_workflow_state(db, conv, "CORRECTION_REQUIRED", source_agent="chat_memory")
    elif status in {"LIVE", "APPROVED", "POSTED"}:
        emit_event(
            db,
            conv,
            "LISTING_LIVE" if status in {"LIVE", "POSTED"} else "LISTING_APPROVED",
            source_agent="data_push",
            detail=result,
        )
        commit_workflow_state(db, conv, "LIVE" if status in {"LIVE", "POSTED"} else "APPROVED", source_agent="data_push")
        reply = handle_admin_approval(db, conv, event, lang)
    elif status in {"UNDER_REVIEW"}:
        commit_workflow_state(db, conv, "UNDER_REVIEW", source_agent="data_push")
        emit_event(db, conv, "ADMIN_REVIEW_STARTED", source_agent="data_push", detail=result)
    return handshake_response(
        req,
        source_agent="data_push",
        result_type="ADMIN_STATUS",
        workflow_state=_payload(conv).get("master_workflow_state") or status,
        success=bool(result.get("ok", True)),
        payload={"admin_result": result, "user_reply": reply},
    )


def handle_message(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str:
    """
    Master turn:
      USER → (10-min memory / last-listing) → account_filter → chat_memory ↔ filter/push
    Last-listing updates resume from DB and use the listing engine.
    """
    started = time.perf_counter()
    ids = new_ids()
    payload = _payload(conv)
    ensure_correlation(payload, conv, ids)
    _write_payload(conv, payload)

    emit_event(
        db,
        conv,
        "USER_MESSAGE_RECEIVED",
        source_agent="orchestrator",
        detail={"text_len": len(text or ""), "has_media": bool(media_note)},
    )

    from .i18n import pick_language, t
    from .session_memory import prepare_turn

    prep = prepare_turn(db, conv, text)
    lang = pick_language(text, str(getattr(conv, "language", "") or ""), "auto")

    if prep.get("missing_last_listing"):
        reply = t(lang, "last_listing_missing")
        sync_master_from_rm(db, conv)
        return reply

    if prep.get("mode") == "engine_update":
        emit_event(
            db,
            conv,
            "DRAFT_UPDATED",
            source_agent="orchestrator",
            detail={"reason": "LAST_LISTING_UPDATE", "card_id": prep.get("card_id")},
        )
        from .engine import respond as engine_respond

        card = prep.get("card_id") or ""
        head = t(lang, "last_listing_loaded", card=card) if card else ""
        try:
            body = engine_respond(db, conv, text, media_note)
        except Exception:
            log.exception("engine update path failed — falling back to chat_memory")
            from .chat_memory import handle_message as rm_handle

            body = rm_handle(db, conv, text, media_note)
        reply = (head + "\n\n" + (body or "")).strip() if head else (body or "")
        sync_master_from_rm(db, conv)
        return reply

    # Agent 1 — WHO?
    af_req = handshake(
        source_agent="orchestrator",
        target_agent="account_filter",
        event_type="RESOLVE_IDENTITY",
        workflow_id=str(payload.get("workflow_id") or ""),
        account_id=payload.get("profile_id"),
        draft_id=payload.get("draft_id") or conv.draft_id,
        draft_version=int(payload.get("draft_version") or 1),
        request_id=ids["request_id"],
        correlation_id=ids["correlation_id"],
    )
    try:
        run_account_filter(db, conv, af_req)
    except Exception:
        log.exception("account_filter failed — continuing to chat_memory")
        emit_event(db, conv, "SYSTEM_ERROR", source_agent="account_filter", detail={"stage": "identity"})

    # Agent 2 — WHAT / WHAT NEXT?
    from .chat_memory import handle_message as rm_handle

    reply = rm_handle(db, conv, text, media_note)
    if prep.get("mode") == "new_chat" and prep.get("reset"):
        # Soft intro so it feels like a fresh conversation after 10-min memory reset
        intro = t(lang, "memory_reset_new_chat")
        body = (reply or "").strip()
        if body and intro not in body:
            reply = intro + "\n\n" + body
        elif not body:
            reply = intro

    sync_master_from_rm(db, conv)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    emit_event(
        db,
        conv,
        "DRAFT_UPDATED",
        source_agent="orchestrator",
        detail={
            "elapsed_ms": elapsed_ms,
            "master_state": _payload(conv).get("master_workflow_state"),
            "rm_state": _payload(conv).get("rm_state"),
            "prep_mode": prep.get("mode"),
        },
    )
    return reply


# Alias matching markdown naming
workflow_engine_handle = handle_message
