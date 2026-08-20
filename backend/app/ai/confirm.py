"""Final WhatsApp summary + Haan/Yes confirmation before admin JSON."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..identity import looks_like_price, seller_fields, unique_photo_ids
from ..models import AiConversation, AiListingDraft, AiMedia, Product
from .i18n import t
from .schema import _blank, listing_title, missing_fields, normalize_vehicle_category
from .tools import _draft_for, _log, _payload, _write_payload

_YES = re.compile(
    r"^\s*(haan+|han+|ha+|yes+|yeah|yess+|हाँ|हां|हा)(?:\s+(sahi|hai|theek|ji|bhai|bro))*[\s!.]*$",
    re.I,
)
_YES_LOOSE = re.compile(
    r"\b(haan|han\b|yes|yeah|हाँ|हां)\b.{0,24}\b(sahi|theek|correct|confirm)?",
    re.I,
)
_NO = re.compile(r"\b(nahi+|nahin+|no+|galat|wrong|change|galti|नहीं|गलत)\b", re.I)


def is_yes(text: str) -> bool:
    msg = (text or "").strip()
    if not msg or _NO.search(msg):
        return False
    if _YES.match(msg):
        return True
    if len(msg.split()) <= 6 and _YES_LOOSE.search(msg) and not re.search(r"\d", msg):
        return True
    return False


def is_no(text: str) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    return bool(_NO.search(msg)) and not is_yes(msg)


def collection_ready(payload: dict) -> bool:
    intent = (payload.get("intent") or "").upper()
    if intent == "SELL":
        price = looks_like_price(str(payload.get("expected_price") or ""))
        state_ok = not _blank(payload.get("state")) or not _blank(payload.get("location"))
        return bool(
            normalize_vehicle_category(payload.get("category") or payload.get("type") or "")
            and not _blank(payload.get("brand"))
            and not _blank(payload.get("model"))
            and not _blank(payload.get("year"))
            and price
            and state_ok
        )
    if intent == "BUY":
        want = not _blank(payload.get("brand")) or bool(normalize_vehicle_category(payload.get("category") or payload.get("type") or ""))
        cat_ok = bool(normalize_vehicle_category(payload.get("category") or payload.get("type") or ""))
        budget = looks_like_price(str(payload.get("budget") or payload.get("budget_max") or ""))
        state_ok = not _blank(payload.get("state")) or not _blank(payload.get("location"))
        return bool(cat_ok and want and budget and state_ok)
    return False


def snapshot(db: Session, conv: AiConversation, payload: dict) -> dict:
    name, phone = seller_fields(db, conv, None, payload)
    photos = unique_photo_ids(db, conv, payload)
    vehicle = " ".join(str(x) for x in [payload.get("brand"), payload.get("model")] if x).strip()
    rate = payload.get("expected_price") or payload.get("budget") or ""
    loc = payload.get("state") or payload.get("location") or ""
    if payload.get("state") and payload.get("city") and payload.get("city") != payload.get("state"):
        loc = f"{payload.get('state')} / {payload.get('city')}"
    data = {
        "vehicle": vehicle,
        "category": normalize_vehicle_category(payload.get("category") or payload.get("type") or "") or payload.get("category") or "",
        "year": payload.get("year") or "",
        "rate": rate,
        "location": loc,
        "state": payload.get("state") or "",
        "city": payload.get("city") or payload.get("location") or "",
        "running": payload.get("running") or payload.get("running_km") or payload.get("operating_hours") or "",
        "owners": payload.get("owners") or "",
        "finance_amount": payload.get("finance_amount") or "",
        "finance_condition": payload.get("finance_condition") or "",
        "tyre_percent": payload.get("tyre_percent") or "",
        "work_issues": payload.get("work_issues") or "",
        "condition": payload.get("condition") or "",
        "phone": phone or conv.mobile,
        "name": name,
        "whatsapp": conv.mobile,
        "intent": (payload.get("intent") or conv.intent or "").upper(),
        "photos": photos,
        "photo_count": len(photos),
    }
    return {k: v for k, v in data.items() if v not in (None, "", [], 0) or k in {"vehicle", "category", "year", "rate", "location", "phone", "photos"}}


def summary_text(data: dict, lang: str) -> str:
    lines = [
        f"Vehicle : {data.get('vehicle') or '—'}",
        f"Category : {data.get('category') or '—'}",
        f"Year : {data.get('year') or '—'}",
        f"Rate : {data.get('rate') or '—'}",
        f"Location : {data.get('location') or '—'}",
    ]
    if data.get("running"):
        lines.append(f"Running : {data['running']}")
    if data.get("owners"):
        lines.append(f"Owners : {data['owners']}")
    if data.get("finance_amount"):
        lines.append(f"Finance : {data['finance_amount']}")
    if data.get("finance_condition"):
        lines.append(f"Finance condition : {data['finance_condition']}")
    if data.get("tyre_percent"):
        lines.append(f"Tyre : {data['tyre_percent']}")
    if data.get("work_issues"):
        lines.append(f"Work/issues : {data['work_issues']}")
    if data.get("condition"):
        lines.append(f"Condition : {data['condition']}")
    if data.get("phone"):
        lines.append(f"Phone : {data['phone']}")
    if data.get("name"):
        lines.append(f"Name : {data['name']}")
    n = data.get("photo_count") or len(data.get("photos") or [])
    if n:
        lines.append(f"Photos : {n}")
    return "\n".join(lines) + "\n\n" + t(lang, "confirm_ask")


def send_summary(db: Session, conv: AiConversation, lang: str) -> str:
    payload = _payload(conv)
    data = snapshot(db, conv, payload)
    payload["awaiting_confirm"] = True
    payload["customer_confirmed"] = False
    payload["summary_json"] = data
    conv.state = "AWAITING_CONFIRMATION"
    conv.error_message = "ask:confirm"
    _write_payload(conv, payload)
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first() if conv.draft_id else None
    if draft and draft.status == "CONFIRMED":
        draft.status = "COLLECTING"
        draft.confirmed_json = "{}"
    return summary_text(data, lang)


def push_confirmed_listing(db: Session, conv: AiConversation) -> dict:
    from .tools import execute_tool

    return execute_tool(db, conv, "submit_for_review", {})


def confirm_prefix(db: Session, conv: AiConversation, lang: str) -> str:
    from ..config import settings

    payload = _payload(conv)
    status = str(payload.get("listing_status") or "").upper()
    url = str(payload.get("listing_url") or "").strip()
    if settings.infradealer_auto_publish and url and status == "POSTED":
        return t(lang, "confirm_pushed_live", url=url)
    return t(lang, "confirm_pushed")


def lock_confirmed(db: Session, conv: AiConversation) -> dict:
    payload = _payload(conv)
    data = snapshot(db, conv, payload)
    payload["customer_confirmed"] = True
    payload["awaiting_confirm"] = False
    payload["confirmed_json"] = data
    payload["listing_status"] = "CONFIRMED"
    payload["data_status"] = "COMPLETE"
    conv.state = "CONFIRMED"
    _write_payload(conv, payload)
    draft = _draft_for(db, conv)
    draft.status = "CONFIRMED"
    draft.intent = conv.intent or data.get("intent") or draft.intent
    draft.title = (data.get("vehicle") or listing_title(payload) or "InfraDealer listing")[:200]
    draft.customer_json = json.dumps(data, ensure_ascii=False)
    draft.confirmed_json = json.dumps(data, ensure_ascii=False)
    ids = _int_ids(payload.get("media_ids"))
    if ids:
        db.query(AiMedia).filter(AiMedia.id.in_(ids)).update({"draft_id": draft.id}, synchronize_session=False)
    _log(db, conv, "customer_confirmed", {"draft_id": draft.id, "json": data})
    return data


def _draft_row(db: Session, conv: AiConversation) -> AiListingDraft | None:
    if not conv.draft_id:
        return None
    return db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()


def handle_confirmation(db: Session, conv: AiConversation, text: str, fields: dict, lang: str) -> str | None:
    payload = _payload(conv)
    if conv.state == "AWAITING_VEHICLE_CHOICE" or payload.get("awaiting_vehicle_choice"):
        return None
    awaiting = conv.state == "AWAITING_CONFIRMATION" or payload.get("awaiting_confirm")
    confirmed = conv.state in {"CONFIRMED", "READY_FOR_REVIEW", "COMPLETED"} or payload.get("customer_confirmed")
    if confirmed:
        draft = _draft_row(db, conv)
        if draft and draft.status == "POSTED":
            return None
        if fields:
            return send_summary(db, conv, lang)
        return None
    if awaiting:
        if fields and not is_yes(text):
            data = snapshot(db, conv, payload)
            prev = payload.get("summary_json") if isinstance(payload.get("summary_json"), dict) else {}
            if prev and data == prev:
                conv.error_message = "ask:confirm"
                payload["awaiting_confirm"] = True
                _write_payload(conv, payload)
                return t(lang, "confirm_ready")
            return send_summary(db, conv, lang)
        if is_yes(text):
            lock_confirmed(db, conv)
            push_confirmed_listing(db, conv)
            prefix = confirm_prefix(db, conv, lang)
            from .account import start_account
            try:
                return start_account(db, conv, lang, prefix=prefix)
            except Exception:
                import logging
                logging.getLogger("infradealer.ai.confirm").exception("account start after confirm failed")
                return prefix + "\n\n" + t(lang, "account_ask")
        if is_no(text):
            conv.error_message = "ask:confirm_fix"
            payload["awaiting_confirm"] = True
            _write_payload(conv, payload)
            return t(lang, "confirm_fix")
        return None
    if collection_ready(payload):
        if (payload.get("intent") or "").upper() == "SELL" and not payload.get("optional_asked"):
            return None
        if (payload.get("intent") or "").upper() == "SELL" and payload.get("optional_asked") and not payload.get("optional_done"):
            return None
        return send_summary(db, conv, lang)
    return None


_ALAG = re.compile(
    r"\b("
    r"alag(\s+(gadi|gaadi|listing|vehicle|machine))?"
    r"|(dusri|doosri|doosari|nayi|nai|naya|new|another|different|second)\s+(gadi|gaadi|listing|vehicle|machine)"
    r"|ek aur|aur ek|one more|nayi listing"
    r"|अलग(\s+गाड़[ीि])?|दूसरी(\s+गाड़[ीि])?|नई(\s+गाड़[ीि])?|नयी(\s+गाड़[ीि])?|एक और"
    r")\b",
    re.I,
)
_ALAG_SHORT = re.compile(r"^\s*(alag|nayi|nai|naya|dusri|doosri|different|new|अलग|नयी|नई|दूसरी)\s*[.!]?\s*$", re.I)
_UPDATE = re.compile(
    r"\b(update|usi|same|yahi|yehi|purani|edit|sahi kar|change kar|isi (me|mein|listing)|"
    r"इसी|अपडेट|वही|यही)\b",
    re.I,
)
_UPDATE_SHORT = re.compile(r"^\s*(update|usi|same|yahi|yehi|edit|अपडेट|इसी|वही|यही)\s*[.!]?\s*$", re.I)
_MORE_VEHICLE = re.compile(
    r"\b("
    r"or he|or hai|aur hai|aur he|aur bhi|or bhi|ek aur|aur ek|"
    r"alag|dusri|doosri|nayi gadi|nai gadi|"
    r"batau|batao|"
    r"और (है|भी)|एक और|बताऊं|बताऊँ|बताओ"
    r")\b",
    re.I,
)
_JUST_GADI = re.compile(r"^\s*(gadi|gaadi|gaadi|vehicle|गाड़ी|गाडी)\s*[?!.]*\s*$", re.I)


def vehicle_label(data: dict) -> str:
    bits = [data.get("brand"), data.get("model"), data.get("year")]
    label = " ".join(str(x) for x in bits if x).strip()
    return label or (data.get("vehicle") or "current gadi")


def says_alag(text: str, *, awaiting: bool = False) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    if _UPDATE.search(msg) and not _ALAG.search(msg) and not (awaiting and _ALAG_SHORT.match(msg)):
        return False
    if awaiting and _ALAG_SHORT.match(msg):
        return True
    return bool(_ALAG.search(msg))


def says_update(text: str, *, awaiting: bool = False) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    if says_alag(msg, awaiting=awaiting):
        return False
    if awaiting and (_UPDATE_SHORT.match(msg) or is_yes(msg)):
        return True
    return bool(_UPDATE.search(msg))


def vehicle_conflict(current: dict, incoming: dict) -> bool:
    cur_b = (current.get("brand") or "").strip().lower()
    cur_m = str(current.get("model") or "").strip().lower()
    new_b = (incoming.get("brand") or "").strip().lower()
    new_m = str(incoming.get("model") or "").strip().lower()
    if not cur_b and not cur_m:
        return False
    if not new_b and not new_m:
        return False
    if new_b and cur_b and new_b != cur_b:
        return True
    if new_m and cur_m and new_m != cur_m:
        return True
    return False


def _int_ids(raw) -> list[int]:
    out = []
    for x in raw or []:
        if isinstance(x, int) or str(x).isdigit():
            n = int(x)
            if n not in out:
                out.append(n)
    return out


def _stash_turn_media(db: Session, payload: dict, media_note: str) -> None:
    if not media_note:
        return
    found = [int(x) for x in re.findall(r"id=(\d+)", media_note)]
    if not found:
        return
    ids = _int_ids(payload.get("media_ids"))
    pending = _int_ids(payload.get("pending_media_ids"))
    for mid in found:
        if mid in ids:
            ids.remove(mid)
        if mid not in pending:
            pending.append(mid)
    payload["media_ids"] = ids
    payload["pending_media_ids"] = pending
    db.query(AiMedia).filter(AiMedia.id.in_(found)).update({"draft_id": None}, synchronize_session=False)


def _apply_pending_fields(db: Session, conv: AiConversation, fields: dict) -> None:
    from .tools import execute_tool
    if not fields:
        return
    if fields.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": fields["intent"]})
    if fields.get("contact_phone"):
        payload = _payload(conv)
        payload["contact_phone"] = fields["contact_phone"]
        _write_payload(conv, payload)
    veh = {k: v for k, v in fields.items() if k not in {"intent", "contact_phone"} and v not in (None, "")}
    if veh:
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)


def start_new_listing(db: Session, conv: AiConversation, pending: dict, pending_media: list[int]) -> None:
    from .schema import empty_payload

    old = _payload(conv)
    keep_keys = (
        "language", "wa_name", "whatsapp_number", "customer_name", "contact_phone",
        "profile_id", "profile_status", "ai_introduced",
        "account_onboarded", "account_step", "account_role", "account_password_set",
    )
    payload = empty_payload()
    for key in keep_keys:
        if old.get(key):
            payload[key] = old[key]
    payload["intent"] = (pending.get("intent") or old.get("intent") or "SELL")
    payload["media_ids"] = list(pending_media or [])
    payload["awaiting_vehicle_choice"] = False
    payload["pending_vehicle"] = {}
    payload["pending_media_ids"] = []
    conv.draft_id = None
    conv.state = "SELL_DATA_COLLECTION" if payload["intent"] == "SELL" else "BUY_DATA_COLLECTION"
    conv.intent = payload["intent"]
    conv.error_message = ""
    _write_payload(conv, payload)
    draft = _draft_for(db, conv)
    draft.status = "COLLECTING"
    draft.intent = conv.intent or ""
    draft.confirmed_json = "{}"
    draft.customer_json = "{}"
    if pending_media:
        db.query(AiMedia).filter(AiMedia.id.in_(pending_media)).update({"draft_id": draft.id}, synchronize_session=False)
    _apply_pending_fields(db, conv, pending)
    _log(db, conv, "vehicle_new", {"draft_id": draft.id, "pending": pending})


def sync_posted_product(db: Session, conv: AiConversation) -> None:
    from ..identity import parse_listing_price

    draft = _draft_row(db, conv)
    if not draft or draft.status != "POSTED" or not draft.posted_product_id:
        return
    payload = _payload(conv)
    data = snapshot(db, conv, payload)
    payload["confirmed_json"] = data
    _write_payload(conv, payload)
    draft.confirmed_json = json.dumps(data, ensure_ascii=False)
    draft.customer_json = draft.confirmed_json
    draft.title = (data.get("vehicle") or draft.title or "")[:200]
    prod = db.query(Product).filter(Product.id == draft.posted_product_id).first()
    if not prod:
        return
    if data.get("vehicle"):
        prod.title = str(data["vehicle"])[:200]
    if data.get("location"):
        prod.city = str(data["location"])[:80]
    price = parse_listing_price(str(data.get("rate") or ""))
    if price:
        prod.price = price
    if data.get("phone"):
        prod.mobile = str(data["phone"])[:10]
    if data.get("photos"):
        prod.photo_ids = json.dumps(data["photos"])


def apply_as_update(db: Session, conv: AiConversation, pending: dict, pending_media: list[int]) -> None:
    payload = _payload(conv)
    payload["awaiting_vehicle_choice"] = False
    payload["pending_vehicle"] = {}
    ids = _int_ids(payload.get("media_ids"))
    for mid in pending_media or []:
        if mid not in ids:
            ids.append(mid)
    payload["media_ids"] = ids
    payload["pending_media_ids"] = []
    _write_payload(conv, payload)
    _apply_pending_fields(db, conv, pending)
    if pending_media and conv.draft_id:
        db.query(AiMedia).filter(AiMedia.id.in_(pending_media)).update({"draft_id": conv.draft_id}, synchronize_session=False)
    draft = _draft_row(db, conv)
    if draft and draft.status == "POSTED":
        sync_posted_product(db, conv)
        return
    payload = _payload(conv)
    if payload.get("customer_confirmed") or conv.state in {"CONFIRMED", "COMPLETED"}:
        payload["customer_confirmed"] = False
        _write_payload(conv, payload)
    _log(db, conv, "vehicle_update", {"draft_id": conv.draft_id, "pending": pending})


def ask_vehicle_choice(db: Session, conv: AiConversation, incoming: dict, media_note: str, lang: str) -> str:
    payload = _payload(conv)
    pending = dict(payload.get("pending_vehicle") or {})
    pending.update({k: v for k, v in (incoming or {}).items() if v not in (None, "")})
    payload["pending_vehicle"] = pending
    payload["awaiting_vehicle_choice"] = True
    payload["awaiting_confirm"] = False
    _stash_turn_media(db, payload, media_note)
    conv.state = "AWAITING_VEHICLE_CHOICE"
    conv.error_message = "ask:vehicle_choice"
    _write_payload(conv, payload)
    _log(db, conv, "vehicle_choice", {"current": vehicle_label(payload), "incoming": vehicle_label(pending)})
    current = vehicle_label(payload)
    incoming_label = vehicle_label(pending)
    return t(lang, "vehicle_choice", current=current, incoming=incoming_label)


def _finish_new_listing(db: Session, conv: AiConversation, pending: dict, pending_media: list[int], lang: str) -> str:
    start_new_listing(db, conv, pending, pending_media)
    nxt = _payload(conv)
    extra = t(lang, "vehicle_new_ok")
    if collection_ready(nxt):
        return extra + "\n\n" + send_summary(db, conv, lang)
    miss = [m for m in missing_fields(nxt) if m != "customer_name"]
    if miss:
        conv.error_message = f"ask:{miss[0]}"
        return extra + "\n" + t(lang, miss[0])
    return extra


def _finish_update(db: Session, conv: AiConversation, pending: dict, pending_media: list[int], lang: str) -> str:
    apply_as_update(db, conv, pending, pending_media)
    extra = t(lang, "vehicle_update_ok")
    draft = _draft_row(db, conv)
    if draft and draft.status == "POSTED":
        return extra
    nxt = _payload(conv)
    if collection_ready(nxt) or nxt.get("customer_confirmed") or conv.state in {"CONFIRMED", "COMPLETED", "AWAITING_CONFIRMATION"}:
        return extra + "\n\n" + send_summary(db, conv, lang)
    return extra


def listing_locked(conv: AiConversation, payload: dict) -> bool:
    if payload.get("customer_confirmed") or payload.get("listing_status") in {"CONFIRMED", "POSTED"}:
        return True
    return conv.state in {"CONFIRMED", "COMPLETED", "READY_FOR_REVIEW"}


def wants_another_vehicle(text: str, conv: AiConversation, payload: dict) -> bool:
    if not listing_locked(conv, payload):
        return False
    msg = (text or "").strip()
    if not msg:
        return False
    if _JUST_GADI.match(msg):
        return True
    return bool(_MORE_VEHICLE.search(msg))


def has_active_listing(conv: AiConversation, payload: dict) -> bool:
    if payload.get("brand") or payload.get("model") or payload.get("customer_confirmed") or payload.get("awaiting_confirm"):
        return True
    return conv.state in {
        "AWAITING_CONFIRMATION", "CONFIRMED", "COMPLETED", "READY_FOR_REVIEW",
    }


def handle_vehicle_slot(db: Session, conv: AiConversation, text: str, fields: dict, media_note: str, lang: str) -> str | None:
    payload = _payload(conv)
    awaiting = conv.state == "AWAITING_VEHICLE_CHOICE" or payload.get("awaiting_vehicle_choice")
    if awaiting:
        pending = dict(payload.get("pending_vehicle") or {})
        if fields:
            pending.update({k: v for k, v in fields.items() if v not in (None, "")})
            payload["pending_vehicle"] = pending
        _stash_turn_media(db, payload, media_note)
        _write_payload(conv, payload)
        pending_media = _int_ids(payload.get("pending_media_ids"))
        if says_alag(text, awaiting=True):
            start_new_listing(db, conv, pending, pending_media)
            return None
        if says_update(text, awaiting=True):
            apply_as_update(db, conv, pending, pending_media)
            return None
        return t(lang, "vehicle_choice", current=vehicle_label(payload), incoming=vehicle_label(pending))

    if vehicle_conflict(payload, fields or {}):
        return ask_vehicle_choice(db, conv, fields or {}, media_note, lang)

    if (says_alag(text) or wants_another_vehicle(text, conv, payload)) and (
        has_active_listing(conv, payload) or listing_locked(conv, payload)
    ):
        _stash_turn_media(db, payload, media_note)
        _write_payload(conv, payload)
        pending = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
        pending_media = _int_ids(payload.get("pending_media_ids"))
        start_new_listing(db, conv, pending, pending_media)
        return None
    return None

