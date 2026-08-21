"""Fresh InfraDealer WhatsApp listing agent — clean orchestration on Z.AI.

Does NOT use legacy engine.respond. Reuses stable helpers: cards, confirm,
account_filter, extract parsers, tools, i18n.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..identity import looks_like_price
from ..models import AiConversation, Chat
from ..services import resolve_ai_config
from .account import (
    account_busy,
    handle_account,
    should_intercept_account,
    wants_clear_conversation,
    wants_new_chat,
)
from .account_filter import sync_conversation_account
from .agent_criteria import AGENT_CRITERIA
from .cards import (
    MAX_PHOTOS,
    MIN_PHOTOS,
    card_clarification_prompt,
    ensure_card_id,
    needs_card_clarification,
    parse_card_mention,
    photos_status,
    switch_active_card,
)
from .confirm import (
    collection_ready,
    handle_confirmation,
    is_no,
    is_yes,
    reset_ai_conversation,
    send_summary,
    start_new_listing,
)
from .extract import extract_from_text
from .i18n import pick_language, t
from .schema import missing_fields, normalize_vehicle_category
from .tools import _draft_for, _payload, _write_payload, execute_tool

log = logging.getLogger("infradealer.ai.listing_agent")

_FALLBACK = "Ji Sir, ek pal — details save kar raha hoon."
_GREET = re.compile(
    r"^\s*(hi+|hii+|hello|hey+|namaste|namaskar|kaise\s*ho|kya\s*haal)[\s!?.]*$",
    re.I,
)
_SKIP_OPT = re.compile(r"\b(skip|baad me|nahi pata|koi nahi|bas yahi)\b", re.I)


def _lang(db: Session, conv: AiConversation, text: str) -> str:
    from ..services import resolve_ai_config as _cfg

    policy = _cfg(db).get("reply_language") or "auto"
    prev = (getattr(conv, "language", None) or "").strip() or (_payload(conv).get("language") or "")
    lang = pick_language(text, prev, policy)
    conv.language = lang
    pl = _payload(conv)
    if pl.get("language") != lang:
        pl["language"] = lang
        _write_payload(conv, pl)
    return lang


def _slim_state(payload: dict, conv: AiConversation, photo_count: int) -> dict:
    keys = (
        "intent", "active_card_id", "category", "brand", "model", "year",
        "expected_price", "budget", "state", "city", "location",
        "awaiting_confirm", "customer_confirmed", "account_onboarded",
        "account_step", "optional_asked", "optional_done", "photos_complete",
        "listing_status",
    )
    slim = {k: payload.get(k) for k in keys if payload.get(k) not in (None, "", [], {}, False)}
    slim["photo_count"] = photo_count
    slim["photos_min"] = MIN_PHOTOS
    slim["photos_max"] = MAX_PHOTOS
    slim["conv_state"] = conv.state
    return slim


def _history(db: Session, conversation_id: str, limit: int = 6) -> list[dict]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id)
        .order_by(Chat.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for row in reversed(rows):
        body = re.sub(r"(?i)^\s*reply\s+", "", (row.body or "").strip())
        if not body:
            continue
        role = "assistant" if row.direction == "outbound" else "user"
        out.append({"role": role, "content": body[:400]})
    return out


def _sanitize(text: str, lang: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"(?is)otp\s*[:\-]*\s*\d{4,8}", "OTP", raw)
    raw = re.sub(r"(?i)(sk-|bearer\s+|api[_-]?key|system prompt|AI_API_KEY)", "[blocked]", raw)
    raw = re.sub(r"(?im)^\s*reply\s+[^\n]*$", "", raw)
    raw = re.sub(r"(?i)^\s*reply\s+", "", raw).strip()
    if len(raw) > 900:
        raw = raw[:890].rsplit(" ", 1)[0] + "…"
    return raw or t(lang, "saving")


def _apply_fields(db: Session, conv: AiConversation, fields: dict) -> None:
    if not fields:
        return
    if fields.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": fields["intent"]})
    veh = {k: v for k, v in fields.items() if k not in {"intent", "contact_phone"} and v not in (None, "")}
    if veh:
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)
    if fields.get("contact_phone"):
        pl = _payload(conv)
        pl["contact_phone"] = fields["contact_phone"]
        _write_payload(conv, pl)


def _ask_missing(lang: str, payload: dict) -> str | None:
    miss = [m for m in missing_fields(payload) if m not in {"customer_name", "photos"}]
    if not miss:
        return None
    key = miss[0]
    q = t(lang, key if key in {
        "intent", "category", "brand", "model", "year", "expected_price", "budget", "state", "location",
    } else "more_detail")
    bits = [str(payload.get(k)) for k in ("brand", "model", "year", "state", "expected_price") if payload.get(k)]
    if bits and key != "intent":
        return t(lang, "ack", facts=" ".join(bits[:4])) + q
    return q


def _photo_line(db: Session, conv: AiConversation, lang: str, media_note: str) -> str | None:
    pl = _payload(conv)
    if (pl.get("intent") or "").upper() != "SELL":
        return None
    st = photos_status(db, conv.draft_id)
    n = st["count"]
    if "DOWNLOAD_FAILED" in (media_note or ""):
        return t(lang, "photo_fail")
    if "photo_rejected_max" in (media_note or "") or n >= MAX_PHOTOS:
        pl["photos_complete"] = True
        _write_payload(conv, pl)
        return t(lang, "photos_enough") if n >= MIN_PHOTOS else t(lang, "photo_at_max")
    if n <= 0 and media_note:
        return t(lang, "photo_need_min", count=0)
    if 0 < n < MIN_PHOTOS:
        return t(lang, "photo_need_min", count=n)
    if MIN_PHOTOS <= n < MAX_PHOTOS and media_note:
        return t(lang, "photo_more")
    if n >= MIN_PHOTOS:
        pl["photos_complete"] = True
        _write_payload(conv, pl)
    return None


def _fast_path(db: Session, conv: AiConversation, text: str, fields: dict, lang: str, media_note: str) -> str | None:
    """Deterministic answers — skip LLM when Python already knows the next line."""
    pl = _payload(conv)
    msg = (text or "").strip()

    if pl.get("awaiting_confirm") or conv.state == "AWAITING_CONFIRMATION":
        return None  # confirm handler owns this

    if _GREET.match(msg) and not fields and not media_note:
        if pl.get("intent"):
            nxt = _ask_missing(lang, pl)
            base = t(lang, "casual_hi")
            return f"{base}\n{nxt}" if nxt else base
        return t(lang, "casual_hi")

    # Short field answers after we already have intent
    short = len(msg.split()) <= 10
    if short and pl.get("intent") and (fields or looks_like_price(msg) or re.fullmatch(r"(?:19|20)\d{2}|\d{2}", msg) or media_note):
        photo = _photo_line(db, conv, lang, media_note) if media_note else None
        if photo and not collection_ready(_payload(conv)):
            return photo
        pl = _payload(conv)
        if collection_ready(pl) and not pl.get("optional_asked") and (pl.get("intent") or "").upper() == "SELL":
            return None  # let orchestrator ask optional / summary
        ask = _ask_missing(lang, pl)
        if ask:
            return ask
        st = photos_status(db, conv.draft_id)
        if (pl.get("intent") or "").upper() == "SELL" and st["need_more"]:
            return t(lang, "photo_need_min", count=st["count"])
        if collection_ready(pl) and st["ready"] and not pl.get("awaiting_confirm"):
            return None
    return None


def _call_zai(db: Session, conv: AiConversation, text: str, lang: str, media_note: str) -> str | None:
    cfg = resolve_ai_config(db)
    if not cfg.get("enabled") or not cfg.get("api_key"):
        log.error("listing_agent: %s", cfg.get("config_error") or "Z.AI not configured")
        return None
    if "z.ai" not in (cfg.get("api_base") or "").lower():
        log.error("listing_agent: non-Z.AI base blocked")
        return None

    pl = _payload(conv)
    st = photos_status(db, conv.draft_id)
    slim = _slim_state(pl, conv, st["count"])
    history = _history(db, conv.conversation_id, 6)
    if history and history[-1].get("role") == "user":
        history = history[:-1]

    user_block = (
        "CURRENT_STATE: "
        + json.dumps({
            "missing_fields": missing_fields(pl),
            "data": slim,
        }, ensure_ascii=False)
        + "\nMEDIA: "
        + (media_note or "none")
        + "\nCUSTOMER_MESSAGE_START\n"
        + (text or "")[:800]
        + "\nCUSTOMER_MESSAGE_END\n"
        + "Answer ONLY the latest message. One short WhatsApp reply. "
        "Do not invent backend facts. Do not start OTP unless account_step is active. "
        f"Reply language: {lang}."
    )
    messages = [{"role": "system", "content": AGENT_CRITERIA}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 220,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            log.error("listing_agent Z.AI http %s %s", resp.status_code, str(data)[:200])
            return None
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return _sanitize(content, lang) if content else None
    except httpx.TimeoutException:
        log.error("listing_agent Z.AI timeout")
        return None
    except Exception as exc:
        log.exception("listing_agent Z.AI error: %s", exc)
        return None


def handle_message(db: Session, conv: AiConversation, text: str, media_note: str = "") -> str:
    """Main entry — fresh listing agent."""
    try:
        sync_conversation_account(db, conv)
    except Exception:
        log.exception("account filter sync failed for %s", conv.mobile)
    lang = _lang(db, conv, text)
    msg = (text or "").strip()

    if media_note and "photo_rejected_max" in media_note:
        return t(lang, "photo_at_max")

    # Card switch / clarify
    mentioned = parse_card_mention(msg)
    if mentioned:
        draft = switch_active_card(db, conv, mentioned)
        if draft:
            return t(lang, "card_switched", card=draft.card_id)
    if needs_card_clarification(db, conv, msg):
        return card_clarification_prompt(db, conv, lang)

    if conv.draft_id:
        draft = _draft_for(db, conv)
        ensure_card_id(db, draft)
        pl = _payload(conv)
        if draft.card_id and pl.get("active_card_id") != draft.card_id:
            pl["active_card_id"] = draft.card_id
            _write_payload(conv, pl)

    # Clear / new chat
    if wants_clear_conversation(msg) or (
        wants_new_chat(msg) and re.search(r"\b(delete|clear|reset|hata|mita)\b", msg, re.I)
    ):
        reset_ai_conversation(db, conv)
        return t(lang, "chat_cleared")
    if wants_new_chat(msg):
        start_new_listing(db, conv, {}, [])
        return t(lang, "vehicle_new_ok") + "\n" + t(lang, "intent")

    pl0 = _payload(conv)
    if _GREET.match(msg) and not media_note and not extract_from_text(msg):
        if pl0.get("awaiting_confirm"):
            return t(lang, "casual_while_confirm", card=pl0.get("active_card_id") or "CARD")
        return t(lang, "casual_hi")

    # Account only when answering account (never hijack listing)
    if account_busy(pl0) and should_intercept_account(pl0, msg):
        acc = handle_account(db, conv, msg, lang)
        if acc:
            return _sanitize(acc, lang)

    fields = extract_from_text(msg)
    # Soft sell intent if dumping vehicle details with photo
    if not fields.get("intent") and (fields.get("brand") or fields.get("model") or media_note):
        low = msg.lower()
        if re.search(r"\b(bech|sell|bikau|dena)\b", low) or media_note:
            if not re.search(r"\b(kharid|buy|chahiye|lena)\b", low):
                fields["intent"] = "SELL"
    _apply_fields(db, conv, fields)

    # Map short answers to last ask
    last = (conv.error_message or "")
    ask_key = last.split(":", 1)[1].strip() if last.lower().startswith("ask:") else ""
    pl = _payload(conv)
    if ask_key == "year" and not pl.get("year"):
        y = re.search(r"\b((?:19|20)\d{2}|\d{2})\b", msg)
        if y:
            tok = y.group(1)
            if len(tok) == 2:
                n = int(tok)
                tok = f"20{tok}" if n <= 30 else (f"19{tok}" if n >= 90 else tok)
            execute_tool(db, conv, "save_vehicle_data", {"year": tok, "source": "customer"})
    if ask_key in {"expected_price", "budget"} and looks_like_price(msg):
        key = "budget" if ask_key == "budget" else "expected_price"
        execute_tool(db, conv, "save_vehicle_data", {key: msg[:80], "source": "customer"})
    if ask_key == "category" and not normalize_vehicle_category(pl.get("category") or ""):
        cat = normalize_vehicle_category(msg)
        if cat:
            execute_tool(db, conv, "save_vehicle_data", {"category": cat, "type": cat, "source": "customer"})
    if ask_key in {"state", "location"} and len(msg.split()) <= 12:
        from .extract import extract_state, infer_state_from_city, _fuzzy_city

        st = extract_state(msg.lower())
        city = _fuzzy_city(msg.split()[0]) if msg.split() else None
        data = {"source": "customer"}
        if st:
            data["state"] = st
        if city:
            data["city"] = city.title()
            data["location"] = data["city"]
            data.setdefault("state", infer_state_from_city(city) or "")
        elif not st:
            data["state"] = msg[:80]
        execute_tool(db, conv, "save_vehicle_data", {k: v for k, v in data.items() if v})

    pl = _payload(conv)

    # Confirm loop
    locked = handle_confirmation(db, conv, msg, fields, lang)
    if locked:
        return _sanitize(locked, lang)

    # Ensure draft + card when collecting
    if pl.get("intent") in {"SELL", "BUY"} and not conv.draft_id:
        draft = _draft_for(db, conv)
        ensure_card_id(db, draft)
        pl = _payload(conv)
        pl["active_card_id"] = draft.card_id
        _write_payload(conv, pl)
        pl = _payload(conv)

    # Photos
    if media_note:
        photo = _photo_line(db, conv, lang, media_note)
        pl = _payload(conv)
        still = [m for m in missing_fields(pl) if m not in {"photos", "customer_name"}]
        if photo and still:
            conv.error_message = f"ask:{still[0]}"
            return photo
        if photo and photos_status(db, conv.draft_id)["need_more"]:
            return photo

    # Optional bundle once
    if (
        (pl.get("intent") or "").upper() == "SELL"
        and collection_ready(pl)
        and not pl.get("optional_asked")
        and not pl.get("awaiting_confirm")
    ):
        pl["optional_asked"] = True
        conv.error_message = "ask:optional_bundle"
        _write_payload(conv, pl)
        return t(lang, "optional_bundle")

    if pl.get("optional_asked") and not pl.get("optional_done"):
        if fields or _SKIP_OPT.search(msg) or msg:
            pl["optional_done"] = True
            _write_payload(conv, pl)
            pl = _payload(conv)

    # Ready for summary?
    st = photos_status(db, conv.draft_id)
    if (
        collection_ready(pl)
        and not pl.get("awaiting_confirm")
        and not pl.get("customer_confirmed")
        and (pl.get("intent") or "").upper() == "BUY"
    ):
        return send_summary(db, conv, lang)
    if (
        collection_ready(pl)
        and not pl.get("awaiting_confirm")
        and not pl.get("customer_confirmed")
        and (pl.get("intent") or "").upper() == "SELL"
        and pl.get("optional_asked")
        and st["ready"]
    ):
        return send_summary(db, conv, lang)
    if (
        collection_ready(pl)
        and (pl.get("intent") or "").upper() == "SELL"
        and pl.get("optional_asked")
        and st["need_more"]
    ):
        return t(lang, "photo_need_min", count=st["count"])

    # Fast path
    fast = _fast_path(db, conv, msg, fields, lang, media_note)
    if fast:
        miss = missing_fields(_payload(conv))
        if miss:
            conv.error_message = f"ask:{miss[0]}"
        return _sanitize(fast, lang)

    # No intent yet
    pl = _payload(conv)
    if not pl.get("intent"):
        if pl.get("brand") or pl.get("model"):
            label = " ".join(x for x in [pl.get("brand"), pl.get("model")] if x) or "ye"
            conv.error_message = "ask:intent"
            return t(lang, "intent_confirm", label=label)
        conv.error_message = "ask:intent"
        return t(lang, "intent")

    ask = _ask_missing(lang, pl)
    if ask:
        miss = missing_fields(pl)
        if miss:
            conv.error_message = f"ask:{miss[0]}"
        return ask

    # LLM for natural / ambiguous turns
    llm = _call_zai(db, conv, msg, lang, media_note)
    if llm:
        return llm

    ask = _ask_missing(lang, _payload(conv))
    return ask or _FALLBACK
