import json
import logging
import re

from sqlalchemy.orm import Session

from ..models import AiConversation, AiEvent, AiListingDraft, AiMedia, User
from ..services import create_otp, deliver_otp, get_or_create_settings, hash_otp, utcnow
from ..identity import looks_like_price, usable_person_name, wa_profile_name
from .schema import ALLOWED_STATES, HUMAN_ONLY_STATUS, INTENTS, collection_state, dumps, listing_title, loads, missing_fields, normalize_vehicle_category

log = logging.getLogger("infradealer.ai")

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "find_profile_by_mobile",
            "description": "Find InfraDealer profile for the current WhatsApp number only.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_profile",
            "description": "Get the current conversation's linked profile, if any.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_otp",
            "description": "Send OTP to the current WhatsApp number via backend. Never invent a code.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_otp",
            "description": "Verify the 6-digit OTP the customer typed. Backend is the authority.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_customer_data",
            "description": "Save customer-provided profile/intent fields. Do not guess.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": ["BUY", "SELL", "GENERAL_ENQUIRY", "SUPPORT", "EXISTING_LISTING_QUERY", "PROFILE_QUERY", "UNKNOWN"]},
                    "customer_name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_vehicle_data",
            "description": "Save customer-provided vehicle/machine fields. Leave unknown as omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "type": {"type": "string"},
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "year": {"type": "string"},
                    "year_min": {"type": "string"},
                    "registration_year": {"type": "string"},
                    "running": {"type": "string"},
                    "running_km": {"type": "string"},
                    "operating_hours": {"type": "string"},
                    "condition": {"type": "string"},
                    "accident_history": {"type": "string"},
                    "negotiable": {"type": "string"},
                    "location": {"type": "string"},
                    "state": {"type": "string"},
                    "city": {"type": "string"},
                    "owners": {"type": "string"},
                    "finance_amount": {"type": "string"},
                    "tyre_percent": {"type": "string"},
                    "finance_condition": {"type": "string"},
                    "work_issues": {"type": "string"},
                    "expected_price": {"type": "string"},
                    "budget": {"type": "string"},
                    "budget_max": {"type": "string"},
                    "source": {"type": "string", "enum": ["customer", "inferred"]},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_conversation",
            "description": "Persist conversation state label after a step change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": [
                            "NEW", "INTENT_PENDING", "BUY_DATA_COLLECTION", "SELL_DATA_COLLECTION",
                            "PROFILE_CHECKING", "PROFILE_FOUND", "PROFILE_NOT_FOUND",
                            "OTP_PENDING", "OTP_VERIFIED", "PROFILE_CREATED",
                            "DATA_INCOMPLETE", "DATA_COMPLETE", "AWAITING_CONFIRMATION", "CONFIRMED",
                            "READY_FOR_REVIEW", "COMPLETED", "ERROR", "BLOCKED",
                        ]
                    }
                },
                "required": ["state"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_listing_data",
            "description": "Run Data Filter validation on the current listing draft. Returns readiness, missing fields, conflicts, and quality score. Does not invent data.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_for_review",
            "description": "After customer Haan/Yes: push filtered listing card to InfraDealer webhook (direct publish when auto_publish is on).",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


def _payload(conv: AiConversation) -> dict:
    data = loads(conv.payload_json)
    data["whatsapp_number"] = conv.mobile
    data["intent"] = conv.intent or data.get("intent")
    data["customer_name"] = conv.customer_name or data.get("customer_name")
    data["profile_id"] = conv.profile_id
    data["profile_status"] = conv.profile_status
    return data


def _write_payload(conv: AiConversation, payload: dict) -> dict:
    missing_fields(payload)
    conv.payload_json = dumps(payload)
    conv.intent = (payload.get("intent") or conv.intent or "").upper()
    if payload.get("customer_name"):
        n = usable_person_name(payload["customer_name"])
        if n:
            conv.customer_name = n
        else:
            payload["customer_name"] = conv.customer_name or ""
    conv.updated_at = utcnow()
    return payload


def _draft_for(db: Session, conv: AiConversation) -> AiListingDraft:
    from .cards import ensure_card_id

    if conv.draft_id:
        row = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()
        if row and (row.status or "").upper() not in {"CLEARED", "DELETED"}:
            ensure_card_id(db, row)
            return row
    row = AiListingDraft(
        conversation_id=conv.id,
        user_id=conv.profile_id,
        mobile=conv.mobile,
        intent=conv.intent or "",
        status="COLLECTING",
        customer_json="{}",
        inferred_json="{}",
    )
    db.add(row)
    db.flush()
    ensure_card_id(db, row)
    conv.draft_id = row.id
    payload = _payload(conv)
    payload["active_card_id"] = row.card_id
    _write_payload(conv, payload)
    return row


def _log(db: Session, conv: AiConversation, event_type: str, detail: dict, wamid: str = "") -> None:
    db.add(AiEvent(
        wamid=wamid or conv.last_wamid or "",
        mobile=conv.mobile,
        event_type=event_type,
        detail=json.dumps(detail, ensure_ascii=False)[:4000],
    ))


def execute_tool(db: Session, conv: AiConversation, name: str, args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    payload = _payload(conv)
    if name == "find_profile_by_mobile":
        user = db.query(User).filter(User.mobile == conv.mobile).first()
        if user:
            conv.profile_id = user.id
            conv.profile_status = "found"
            conv.customer_name = conv.customer_name or user.name
            if conv.state in {"NEW", "NEW_CHAT", "INTENT_PENDING", "PROFILE_NOT_FOUND", ""}:
                conv.state = "PROFILE_FOUND"
            payload["profile_id"] = user.id
            payload["profile_status"] = "found"
            payload["customer_name"] = user.name
            payload["verification_status"] = "verified"
            payload["otp_verified"] = True
            payload.setdefault("confidence", {})["customer_name"] = "FROM_BACKEND"
            payload.setdefault("source", {}).setdefault("backend", {})["profile_id"] = user.id
            _write_payload(conv, payload)
            return {"ok": True, "found": True, "profile_id": user.id, "name": user.name}
        conv.profile_status = "missing"
        if conv.state in {"NEW", "NEW_CHAT", "INTENT_PENDING", ""}:
            conv.state = "PROFILE_NOT_FOUND"
        payload["profile_status"] = "missing"
        _write_payload(conv, payload)
        return {"ok": True, "found": False}

    if name == "get_profile":
        if not conv.profile_id:
            return {"ok": True, "found": False}
        user = db.query(User).filter(User.id == conv.profile_id).first()
        if not user:
            return {"ok": True, "found": False}
        return {"ok": True, "found": True, "profile_id": user.id, "name": user.name, "mobile": user.mobile}

    if name == "save_customer_data":
        source = payload.setdefault("source", {"customer": {}, "inferred": {}})
        if args.get("intent") in INTENTS:
            prev = (payload.get("intent") or "").upper()
            payload["intent"] = args["intent"]
            conv.intent = args["intent"]
            source["customer"]["intent"] = args["intent"]
            payload.setdefault("confidence", {})["intent"] = "CONFIRMED_BY_CUSTOMER"
            if prev and prev != args["intent"] and args["intent"] in {"BUY", "SELL"}:
                conv.state = collection_state(args["intent"])
            elif conv.state in {"NEW", "NEW_CHAT", "", "INTENT_PENDING"}:
                conv.state = collection_state(args["intent"]) if args["intent"] in {"BUY", "SELL"} else "INTENT_PENDING"
        if args.get("customer_name"):
            n = usable_person_name(args["customer_name"])
            if n:
                payload["customer_name"] = n
                conv.customer_name = n
                source["customer"]["customer_name"] = n
        if args.get("description"):
            payload["description"] = str(args["description"]).strip()[:2000]
            source["customer"]["description"] = payload["description"]
        if conv.intent in {"BUY", "SELL"} and conv.state in {"NEW", "NEW_CHAT", "INTENT_DETECTED", "INTENT_PENDING"}:
            conv.state = collection_state(conv.intent)
        _write_payload(conv, payload)
        return {"ok": True, "missing_fields": payload.get("missing_fields") or []}

    if name == "save_vehicle_data":
        origin = args.get("source") or "customer"
        if origin not in {"customer", "inferred"}:
            origin = "customer"
        source = payload.setdefault("source", {"customer": {}, "inferred": {}})
        bucket = source.setdefault(origin, {})
        keys = (
            "category", "type", "brand", "model", "year", "year_min", "registration_year",
            "running", "running_km", "operating_hours", "condition", "accident_history",
            "negotiable", "location", "state", "city", "owners", "finance_amount",
            "tyre_percent", "finance_condition", "work_issues",
            "expected_price", "budget", "budget_max",
        )
        conf = payload.setdefault("confidence", {})
        for key in keys:
            val = args.get(key)
            if val is None or str(val).strip() == "":
                continue
            clean = str(val).strip()[:200]
            if key in {"category", "type"}:
                canon = normalize_vehicle_category(clean)
                if key == "category" and not canon:
                    continue
                if canon:
                    clean = canon
            if key == "expected_price" and not looks_like_price(clean):
                continue
            if key == "location" and clean.lower() in {"hor", "he", "hai", "or", "and", "the", "yes", "han", "haan"}:
                continue
            if key == "state" and clean.lower() in {"hor", "he", "hai", "or", "and", "the", "yes", "han", "haan"}:
                continue
            if key in {"location", "city", "state"}:
                from .data_filteration import _looks_like_place
                from .extract import _fuzzy_city, extract_state

                if (
                    not _looks_like_place(clean)
                    and not extract_state(clean)
                    and not _fuzzy_city(clean)
                ):
                    continue
                # Never overwrite a confirmed place with a different value unless
                # this turn looks like an explicit place (already filtered above).
                conf_map = payload.get("confidence") if isinstance(payload.get("confidence"), dict) else {}
                prev = payload.get(key)
                if (
                    prev not in (None, "")
                    and str(prev).strip().lower() != clean.lower()
                    and conf_map.get(key) == "CONFIRMED_BY_CUSTOMER"
                    and origin == "inferred"
                ):
                    continue
            if key == "condition" and (len(clean) > 80 or re.search(r"\b(nahi|नहीं|tata|jcb|1613)\b", clean, re.I)):
                continue
            bucket[key] = clean
            if origin == "customer" or not payload.get(key):
                payload[key] = clean
            if origin == "customer":
                conf[key] = "CONFIRMED_BY_CUSTOMER"
            elif origin == "inferred" and conf.get(key) != "CONFIRMED_BY_CUSTOMER":
                conf[key] = "INFERRED_BY_AI"
        cat = normalize_vehicle_category(payload.get("category") or payload.get("type") or "")
        if cat:
            payload["category"] = cat
            payload["type"] = cat
        if origin == "customer" and args.get("state"):
            from .extract import infer_state_from_city
            new_st = str(args.get("state") or "").strip()
            city = str(payload.get("city") or payload.get("location") or "").strip()
            city_st = infer_state_from_city(city) if city else None
            # Never wipe a known city just because a conflicting state arrived
            if new_st and city_st and city_st != new_st and conf.get("city") == "CONFIRMED_BY_CUSTOMER":
                payload["state"] = city_st
                bucket["state"] = city_st
        if payload.get("location") and not payload.get("city"):
            from .data_filteration import _looks_like_place
            if _looks_like_place(str(payload.get("location") or "")):
                payload["city"] = payload["location"]
        if payload.get("city") and not payload.get("state"):
            from .extract import infer_state_from_city
            inferred = infer_state_from_city(str(payload.get("city") or ""))
            if inferred:
                payload["state"] = inferred
                bucket["state"] = inferred
        if conv.state in {"NEW", "NEW_CHAT", "INTENT_DETECTED", "INTENT_PENDING"}:
            conv.state = collection_state(conv.intent)
        elif conv.state not in {
            "AWAITING_CONFIRMATION", "AWAITING_VEHICLE_CHOICE", "CONFIRMED",
            "READY_FOR_REVIEW", "COMPLETED", "OTP_PENDING", "OTP_VERIFIED",
        }:
            if conv.intent == "SELL":
                conv.state = "SELL_DATA_COLLECTION"
            elif conv.intent == "BUY":
                conv.state = "BUY_DATA_COLLECTION"
        # Data Filter quality gate — normalize/validate without inventing values
        try:
            from .data_filteration import apply_filter_to_payload, filter_payload

            apply_filter_to_payload(payload)
            fr = filter_payload(payload)
            if fr.normalized_data.get("category"):
                payload["category"] = fr.normalized_data["category"]
                payload["type"] = fr.normalized_data["category"]
            if fr.normalized_data.get("brand") and not payload.get("brand"):
                payload["brand"] = fr.normalized_data["brand"]
            if fr.validation_errors:
                payload["filter_result"] = fr.as_dict()
        except Exception:
            log.exception("data_filter on save_vehicle_data failed")
        _write_payload(conv, payload)
        draft = _draft_for(db, conv)
        if draft.status in HUMAN_ONLY_STATUS:
            return {"ok": True, "locked": True, "missing_fields": payload.get("missing_fields") or [], "draft_id": draft.id}
        draft.intent = conv.intent or draft.intent
        draft.inferred_json = json.dumps(source.get("inferred") or {}, ensure_ascii=False)
        draft.title = listing_title(payload)
        if draft.status not in {"READY_FOR_REVIEW", "POSTED", "CONFIRMED"}:
            draft.status = "COLLECTING"
        return {"ok": True, "missing_fields": payload.get("missing_fields") or [], "draft_id": draft.id, "readiness": (payload.get("filter_result") or {}).get("readiness")}

    if name == "save_conversation":
        state = str(args.get("state") or "").upper()
        if state in HUMAN_ONLY_STATUS:
            return {"ok": False, "error": "Human-only status"}
        if state not in ALLOWED_STATES:
            return {"ok": False, "error": "Invalid state"}
        conv.state = state
        conv.updated_at = utcnow()
        return {"ok": True, "state": conv.state}

    if name == "send_otp":
        if conv.profile_id and conv.profile_status in {"found", "verified"}:
            return {"ok": True, "skipped": True, "reason": "profile already verified"}
        meta = get_or_create_settings(db)
        try:
            otp = create_otp(db, conv.mobile)
            channel = deliver_otp(meta, conv.mobile, otp._plain_code)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        conv.state = "OTP_PENDING"
        conv.profile_status = "otp_pending"
        payload["profile_status"] = "otp_pending"
        payload["verification_status"] = "otp_pending"
        _write_payload(conv, payload)
        _log(db, conv, "tool", {"tool": "send_otp", "channel": channel})
        return {"ok": True, "channel": channel, "message": "OTP sent by backend. Do not mention any code."}

    if name == "verify_otp":
        code = str(args.get("code") or "").strip()
        if not code.isdigit() or len(code) != 6:
            return {"ok": False, "error": "OTP 6 digit hona chahiye"}
        from ..models import Otp
        otp = db.query(Otp).filter(Otp.mobile == conv.mobile, Otp.status == "sent").order_by(Otp.id.desc()).first()
        if not otp:
            return {"ok": False, "error": "OTP nahi mila"}
        if otp.attempts >= otp.max_attempts:
            otp.status = "blocked"
            return {"ok": False, "error": "OTP attempts khatam"}
        if utcnow() > otp.expires_at:
            otp.status = "expired"
            return {"ok": False, "error": "OTP expire"}
        otp.attempts += 1
        if otp.code_hash != hash_otp(code):
            left = otp.max_attempts - otp.attempts
            return {"ok": False, "error": f"Galat OTP. Left: {left}"}
        otp.status = "verified"
        user = db.query(User).filter(User.mobile == conv.mobile).first()
        created = False
        if not user:
            name = (
                usable_person_name(conv.customer_name)
                or usable_person_name(payload.get("wa_name"))
                or wa_profile_name(db, conv.conversation_id)
                or "Seller"
            )
            user = User(name=name, mobile=conv.mobile, source="whatsapp_ai_otp", role="user")
            db.add(user)
            db.flush()
            created = True
        elif not usable_person_name(user.name):
            good = (
                usable_person_name(conv.customer_name)
                or usable_person_name(payload.get("wa_name"))
                or wa_profile_name(db, conv.conversation_id)
            )
            if good:
                user.name = good
        conv.profile_id = user.id
        conv.profile_status = "verified"
        conv.state = "OTP_VERIFIED"
        payload["profile_id"] = user.id
        payload["profile_status"] = "verified"
        payload["verification_status"] = "verified"
        payload["otp_verified"] = True
        payload.setdefault("source", {}).setdefault("backend", {})["profile_id"] = user.id
        payload["customer_name"] = user.name
        conv.customer_name = user.name
        _write_payload(conv, payload)
        draft = _draft_for(db, conv)
        draft.user_id = user.id
        _log(db, conv, "tool", {"tool": "verify_otp", "profile_id": user.id, "created": created})
        return {"ok": True, "verified": True, "profile_id": user.id, "created": created}

    if name == "validate_listing_data":
        from .data_filteration import filter_memory

        result = filter_memory(db, conv)
        return result.as_dict()

    if name == "submit_for_review":
        from .data_filteration import final_validation
        from .data_push import push_listing

        gate = final_validation(db, conv)
        if not gate.ready and gate.readiness in {"INVALID_DATA", "CONFLICT_REQUIRES_USER", "MISSING_REQUIRED_DATA"}:
            return {
                "ok": False,
                "error": "listing_not_ready",
                "readiness": gate.readiness,
                "missing_fields": gate.missing_fields,
                "conflicts": gate.conflicts,
                "validation_errors": gate.validation_errors,
            }
        result = push_listing(db, conv)
        out = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
        out["filter_readiness"] = gate.readiness
        out["quality"] = gate.quality
        return out

    return {"ok": False, "error": "Unknown or forbidden tool"}
