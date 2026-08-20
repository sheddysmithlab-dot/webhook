import json
import logging
import re

import httpx

from ..models import AiConversation, AiListingDraft, Chat
from ..services import resolve_ai_config
from .account import account_busy, handle_account, should_intercept_account, wants_clear_conversation, wants_new_chat
from .confirm import handle_confirmation, handle_vehicle_slot, is_no, is_yes, collection_ready, sync_posted_product
from .extract import extract_from_text
from .i18n import _GREET, _WEAK, language_instruction, pick_language, t
from .memory import (
    customer_annoyed,
    harvest_turn,
    is_repeat_outbound,
    load_reps,
    photos_complete,
    question_kind,
    recent_outbound_bodies,
    seed_memory,
    too_similar,
)
from .prompt import SYSTEM_PROMPT
from .schema import missing_fields, normalize_vehicle_category
from .tools import TOOL_DEFS, _payload, _write_payload, execute_tool
from ..identity import looks_like_price, usable_person_name

log = logging.getLogger("infradealer.ai")

FIELD_KEYS = (
    "intent", "brand", "model", "year", "expected_price", "state",
    "budget", "category", "customer_name", "optional_bundle",
)

_CASUAL = re.compile(
    r"^\s*("
    r"hi+|hii+|hello|hey+|namaste|namaskar|"
    r"kya\s*haal[\w\s]{0,24}|kaise\s*ho[\w\s]{0,20}|kaisa\s*hai[\w\s]{0,20}|"
    r"sab\s*(badiya|theek|achha|ok)[\w\s]{0,16}|"
    r"theek\s*hai|all\s*good|good\s*morning|good\s*evening|"
    r"yaar|bhai|bro|sunao|bolo|reply\s*(to\s*)?do|kuch\s*bolo|"
    r"ok+|okay|hmm+|h+m+"
    r")[\s!?.]*$",
    re.I,
)


def is_casual_chat(text: str) -> bool:
    msg = (text or "").strip()
    if not msg or len(msg) > 60:
        return False
    if wants_clear_conversation(msg) or wants_new_chat(msg):
        return False
    if re.search(r"\b(bech|sell|buy|kharid|price|rate|lakh|photo|card-|delete)\b", msg, re.I):
        return False
    return bool(_CASUAL.match(msg))


def _conv_lang(db, conv: AiConversation, text: str = "") -> str:
    policy = resolve_ai_config(db).get("reply_language") or "auto"
    prev = (getattr(conv, "language", None) or "").strip() or (_payload(conv).get("language") or "")
    lang = pick_language(text, prev, policy)
    conv.language = lang
    payload = _payload(conv)
    if payload.get("language") != lang:
        payload["language"] = lang
        _write_payload(conv, payload)
    return lang


def _ask_key(raw: str) -> str:
    last = (raw or "").strip()
    if last.lower().startswith("ask:"):
        return last.split(":", 1)[1].strip()
    return ""


def _sanitize_reply(text: str, posted: bool = False, lang: str = "hinglish") -> str:
    raw = (text or "").strip()
    raw = re.sub(r"(?is)otp\s*[:\-]*\s*\d{4,8}", "OTP", raw)
    raw = re.sub(r"(?i)(sk-|bearer\s+|api[_-]?key|system prompt|AUTH_|META_SYSTEM)", "[blocked]", raw)
    if not posted:
        raw = re.sub(
            r"(?is)(listing\s+(live|post|publish).{0,40}|aapki listing.{0,30}(live|post).{0,20})",
            t(lang, "listing_not_live"),
            raw,
        )
    raw = raw.replace(SYSTEM_PROMPT[:40], "")
    if len(raw) > 900:
        raw = raw[:890].rsplit(" ", 1)[0] + "…"
    return raw or t(lang, "saving")


def _history(db, conv: AiConversation) -> list[dict]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conv.conversation_id)
        .order_by(Chat.id.desc())
        .limit(16)
        .all()
    )
    out = []
    for row in reversed(rows):
        role = "assistant" if row.direction == "outbound" else "user"
        out.append({"role": role, "content": (row.body or "")[:500]})
    return out


def _draft_status(db, conv: AiConversation) -> str:
    if not conv.draft_id:
        return ""
    row = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()
    return (row.status if row else "") or ""


def _clarify(lang: str, payload: dict) -> str:
    key = _next_ask_key(payload)
    if key and key in FIELD_KEYS:
        return t(lang, "not_understood") + " " + t(lang, key)
    if payload.get("brand") or payload.get("model"):
        label = " ".join(x for x in [payload.get("brand"), payload.get("model")] if x)
        return t(lang, "not_understood") + " " + t(lang, "intent_confirm", label=label)
    return t(lang, "not_understood") + " " + t(lang, "intent")


def _is_unclear(text: str, fields: dict, last_key: str, media_note: str) -> bool:
    msg = (text or "").strip()
    if media_note or not msg:
        return False
    if _GREET.match(msg) or _WEAK.match(msg):
        return False
    if fields:
        return False
    if last_key in {
        "year", "location", "state", "expected_price", "budget",
        "customer_name", "brand", "model", "category",
    } and len(msg.split()) <= 8:
        return False
    letters = sum(1 for ch in msg if ch.isalpha() or "\u0900" <= ch <= "\u0d7f")
    return letters >= 8 or len(msg.split()) >= 3


def extract_turn(text: str, extra_reps=None, media_note: str = "") -> dict:
    fields = extract_from_text(text, extra_reps=extra_reps)
    low = (text or "").lower()
    dump = bool(fields.get("brand") or fields.get("model") or fields.get("year"))
    buyish = bool(re.search(r"kharid|buy|chahiye|lena |lene ", low))
    sellish = bool(
        media_note
        or re.search(r"bech|sell|bikau|dena|fitness|bima|insurance|tax|kimat|keemat|photo", low)
        or re.search(r"[6-9]\d{9}", low)
    )
    if not fields.get("intent") and dump and sellish and not buyish:
        fields["intent"] = "SELL"
    return fields


def apply_extraction(db, conv: AiConversation, text: str, extra_reps=None, media_note: str = "", fields=None) -> dict:
    if account_busy(_payload(conv)) and (_payload(conv).get("account_step") == "password"):
        return {}
    if fields is None:
        fields = extract_turn(text, extra_reps=extra_reps, media_note=media_note)
    if not fields:
        return fields
    if fields.get("intent"):
        execute_tool(db, conv, "save_customer_data", {"intent": fields["intent"]})
    veh = {k: v for k, v in fields.items() if k not in {"intent", "contact_phone"}}
    if veh:
        veh["source"] = "customer"
        execute_tool(db, conv, "save_vehicle_data", veh)
    if fields.get("contact_phone"):
        payload = _payload(conv)
        payload["contact_phone"] = fields["contact_phone"]
        _write_payload(conv, payload)
    return fields


def _next_ask_key(payload: dict) -> str | None:
    miss = missing_fields(payload)
    ask = [m for m in miss if m != "customer_name"]
    if not ask:
        return None
    return ask[0]


def _facts(payload: dict) -> str:
    bits = []
    for key in ("category", "brand", "model", "year", "state", "expected_price", "budget"):
        val = (payload.get(key) or "").strip() if isinstance(payload.get(key), str) else payload.get(key)
        if val:
            bits.append(str(val))
    return " ".join(bits[:5])


def _with_ack(lang: str, key: str, payload: dict, **kwargs) -> str:
    q = t(lang, key, **kwargs)
    facts = _facts(payload)
    if facts and key not in {"intent"}:
        return t(lang, "ack", facts=facts) + q
    return q


def _next_question(payload: dict, lang: str) -> str | None:
    key = _next_ask_key(payload)
    if key:
        return _with_ack(lang, key if key in FIELD_KEYS else "more_detail", payload)
    if payload.get("intent") == "SELL" and not payload.get("optional_asked"):
        return t(lang, "optional_bundle")
    return None


def _photo_prompt(payload: dict, media_note: str, lang: str, photo_count: int | None = None) -> str | None:
    if payload.get("photos_complete"):
        return None
    n = photo_count if photo_count is not None else len(payload.get("media_ids") or [])
    if "DOWNLOAD_FAILED" in (media_note or ""):
        return t(lang, "photo_fail")
    if n <= 0:
        return _with_ack(lang, "photos", payload)
    if n < 2:
        return t(lang, "photo_need_min", count=n)
    if n >= 5:
        payload["photos_complete"] = True
        return t(lang, "photos_enough")
    if payload.get("photos_prompted"):
        return None
    return t(lang, "photo_more")


def fallback_reply(db, conv: AiConversation, text: str, media_note: str = "") -> str:
    lang = _conv_lang(db, conv, text)
    payload = _payload(conv)
    msg = (text or "").strip()
    low = msg.lower()
    posted = _draft_status(db, conv) == "POSTED"

    if posted:
        return t(lang, "continue_chat")

    if conv.state == "OTP_PENDING" or payload.get("verification_status") == "otp_pending":
        digits = re.sub(r"\D", "", msg)
        if len(digits) == 6:
            result = execute_tool(db, conv, "verify_otp", {"code": digits})
            if not result.get("ok"):
                return t(lang, "otp_mismatch")
            submitted = execute_tool(db, conv, "submit_for_review", {})
            if submitted.get("status") == "READY_FOR_REVIEW":
                return t(lang, "otp_ok_review")
            return t(lang, "otp_ok_pending")
        return t(lang, "otp_ask")

    if conv.state in {"READY_FOR_REVIEW", "COMPLETED", "CONFIRMED"}:
        return t(lang, "continue_chat")

    if conv.state == "AWAITING_CONFIRMATION":
        from .confirm import handle_confirmation
        hit = handle_confirmation(db, conv, text, {}, lang)
        if hit:
            return hit

    if re.search(r"(system prompt|skip otp|listing live|sql |database)", low):
        return t(lang, "ignore_inject")

    if not payload.get("intent"):
        if payload.get("brand") or payload.get("model") or payload.get("type"):
            conv.state = "INTENT_PENDING"
            conv.error_message = "ask:intent"
            label = " ".join(x for x in [payload.get("brand"), payload.get("model")] if x) or "ye"
            return t(lang, "intent_confirm", label=label)
        conv.state = "NEW"
        conv.error_message = "ask:intent"
        return t(lang, "intent")

    if payload.get("intent") == "SELL" and media_note:
        from .cards import photos_status

        st = photos_status(db, conv.draft_id)
        extra = _photo_prompt(payload, media_note, lang, photo_count=st["count"])
        if extra == t(lang, "photos_enough"):
            payload["photos_complete"] = True
            _write_payload(conv, payload)
        still = [m for m in missing_fields(payload) if m not in {"photos", "customer_name"}]
        if extra and still:
            if "photo_more" in (extra or "") or extra == t(lang, "photo_more") or extra == t(lang, "photo_need_min", count=st["count"]):
                payload["photos_prompted"] = True
                _write_payload(conv, payload)
            return extra
        if extra and st["need_more"]:
            payload["photos_prompted"] = True
            _write_payload(conv, payload)
            return extra

    q_key = _next_ask_key(payload)
    if q_key:
        conv.error_message = f"ask:{q_key}"
        return _with_ack(lang, q_key, payload)

    last_ask = _ask_key(conv.error_message)
    if payload.get("intent") == "SELL" and last_ask == "optional_bundle" and not payload.get("optional_done"):
        payload["optional_done"] = True
        payload["optional_asked"] = True
        _write_payload(conv, payload)

    if payload.get("intent") == "SELL" and not payload.get("optional_asked"):
        payload["optional_asked"] = True
        conv.error_message = "ask:optional_bundle"
        _write_payload(conv, payload)
        return t(lang, "optional_bundle")

    if collection_ready(payload) and not payload.get("customer_confirmed"):
        from .confirm import send_summary
        return send_summary(db, conv, lang)

    nxt = _next_question(payload, lang)
    return nxt or t(lang, "saving")


def llm_configured(db) -> bool:
    cfg = resolve_ai_config(db)
    return bool(cfg["enabled"] and cfg["api_key"])


def llm_reply(db, conv: AiConversation, text: str, media_note: str) -> str | None:
    cfg = resolve_ai_config(db)
    if not cfg["enabled"] or not cfg["api_key"]:
        return None
    lang = _conv_lang(db, conv, text)
    payload = _payload(conv)
    # Compact state only — full payload slows the model and confuses answers
    slim = {
        k: payload.get(k)
        for k in (
            "intent", "active_card_id", "category", "brand", "model", "year",
            "expected_price", "budget", "state", "city", "location",
            "awaiting_confirm", "customer_confirmed", "account_onboarded",
            "account_step", "ai_introduced", "media_ids", "photos_complete",
            "listing_status",
        )
        if payload.get(k) not in (None, "", [], {}, False)
    }
    if slim.get("media_ids"):
        slim["photo_count"] = len(slim.pop("media_ids") or [])
    recents = recent_outbound_bodies(db, conv.conversation_id, 2)
    user_block = (
        "ANSWER ONLY THE LATEST CUSTOMER MESSAGE below. Do not answer an older question. "
        "If they ask account → answer account. If they ask vehicle/price → answer that. Never mix.\n"
        "CURRENT_STATE: "
        + json.dumps({
            "state": conv.state,
            "reply_language": lang,
            "missing_fields": missing_fields(payload),
            "data": slim,
        }, ensure_ascii=False)
        + "\nMEDIA: "
        + (media_note or "none")
        + "\nCUSTOMER_MESSAGE_START\n"
        + (text or "")[:800]
        + "\nCUSTOMER_MESSAGE_END\n"
        + "Rules: 1 short WhatsApp reply. Sir/Ma'am only — never greet with random WA profile names. "
        "Do not re-ask fields already in CURRENT_STATE.data. Do not start account/OTP unless "
        "customer_confirmed is true and account_onboarded is false AND they just confirmed. "
        "Never invent OTP/password. Reply in {lang}."
    )
    sys = SYSTEM_PROMPT + "\n\n" + language_instruction(lang)
    messages = [{"role": "system", "content": sys}]
    messages.extend(_history(db, conv)[-6:])
    messages.append({"role": "user", "content": user_block})
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en",
    }
    body = {
        "model": cfg["model"],
        "messages": messages,
        "tools": TOOL_DEFS,
        "tool_choice": "auto",
        "temperature": 0.25,
        "max_tokens": 220,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    posted = _draft_status(db, conv) == "POSTED"
    try:
        with httpx.Client(timeout=12.0) as client:
            use_tools = True
            for _ in range(3):  # fast: max 3 rounds
                payload_body = dict(body)
                if not use_tools:
                    payload_body.pop("tools", None)
                    payload_body.pop("tool_choice", None)
                resp = client.post(url, headers=headers, json=payload_body)
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    log.warning("ai api non-json %s %s", resp.status_code, (resp.text or "")[:240])
                    if use_tools:
                        use_tools = False
                        continue
                    return None
                if not isinstance(data, dict):
                    data = {}
                if resp.status_code >= 400:
                    log.warning("ai api http %s %s", resp.status_code, str(data)[:300] or (resp.text or "")[:240])
                    if use_tools:
                        use_tools = False
                        continue
                    return None
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    body["messages"].append(msg)
                    for call in tool_calls:
                        fn = (call.get("function") or {})
                        name = fn.get("name") or ""
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        result = execute_tool(db, conv, name, args)
                        body["messages"].append({
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "content": json.dumps(result, ensure_ascii=False),
                        })
                    continue
                content = (msg.get("content") or "").strip()
                if not content:
                    log.warning("ai api empty content keys=%s", list(msg.keys()))
                    return None
                return _sanitize_reply(content, posted=posted, lang=lang)
    except Exception as exc:
        log.warning("ai api error: %s", exc)
        return None
    return None


def _fast_rule_reply(db, conv: AiConversation, text: str, media_note: str, fields: dict, lang: str) -> str | None:
    """Skip slow LLM when rules already know the next polite answer."""
    from .confirm import is_no, is_yes

    payload = _payload(conv)
    msg = (text or "").strip()
    # Explicit account question from user (not listing)
    if re.search(r"\b(account|otp|password|broker|token)\b", msg, re.I) and not (
        fields.get("brand") or fields.get("model") or fields.get("expected_price")
    ):
        return None
    # Simple yes/no / short field answers → Python only
    last = _ask_key(conv.error_message)
    short = len(msg.split()) <= 8
    if short and (
        is_yes(msg)
        or is_no(msg)
        or looks_like_price(msg)
        or bool(re.fullmatch(r"(?:19|20)\d{2}|\d{2}", msg))
        or (last in {"year", "expected_price", "budget", "state", "location", "category", "brand", "model"} and msg)
        or fields
        or media_note
    ):
        if payload.get("awaiting_confirm") or conv.state == "AWAITING_CONFIRMATION":
            return None  # confirmation handler owns this
        if fields or media_note or last:
            if payload.get("intent") and missing_fields(payload):
                return fallback_reply(db, conv, text, media_note)
            if media_note and payload.get("intent") == "SELL":
                return fallback_reply(db, conv, text, media_note)
            if fields and payload.get("intent"):
                return fallback_reply(db, conv, text, media_note)
    if fields or media_note:
        if payload.get("intent") and missing_fields(payload):
            return fallback_reply(db, conv, text, media_note)
        if media_note and payload.get("intent") == "SELL":
            return fallback_reply(db, conv, text, media_note)
    if collection_ready(payload) and not payload.get("awaiting_confirm") and not payload.get("customer_confirmed"):
        from .confirm import send_summary
        return send_summary(db, conv, lang)
    return None


def apply_followup(db, conv: AiConversation, text: str) -> None:
    """Map a short answer to the last question without re-asking."""
    last = (conv.error_message or "")
    key = _ask_key(last)
    last_l = last.lower()
    msg = (text or "").strip()
    if not msg or (not key and not last_l):
        return
    payload = _payload(conv)
    name_hit = key == "customer_name" or "naam" in last_l or "name" in last_l
    if name_hit and 1 <= len(msg.split()) <= 5:
        n = usable_person_name(msg)
        if n and not re.search(r"\b(bech|kharid|truck|lakh|km|sell|buy)\b", msg.lower()):
            execute_tool(db, conv, "save_customer_data", {"customer_name": n})
    year_hit = key == "year" or "year" in last_l or "saal" in last_l
    if year_hit and not payload.get("year"):
        y = re.search(r"\b((?:19|20)\d{2})\b", msg) or re.search(r"\b(\d{2})\b", msg)
        if y:
            token = y.group(1)
            if len(token) == 2:
                n = int(token)
                token = f"20{token}" if n <= 30 else (f"19{token}" if n >= 90 else token)
            execute_tool(db, conv, "save_vehicle_data", {"year": token, "source": "customer"})
    price_hit = key in {"expected_price", "budget"} or "price" in last_l or "budget" in last_l or "rate" in last_l
    if price_hit and looks_like_price(msg):
        pkey = "budget" if key == "budget" or "budget" in last_l else "expected_price"
        if not payload.get(pkey):
            execute_tool(db, conv, "save_vehicle_data", {pkey: msg[:80], "source": "customer"})
    run_hit = key == "running" or "kilometer" in last_l or "hours" in last_l or " km" in last_l or last_l.endswith("km")
    if run_hit and re.search(r"\d", msg) and not payload.get("running"):
        execute_tool(db, conv, "save_vehicle_data", {"running": msg[:80], "source": "customer"})
    loc_hit = key in {"location", "state"} or "location" in last_l or "kahan" in last_l or "khadi" in last_l or "state" in last_l
    loc_in_msg = bool(re.search(r"\b(location|state|city|rajya)\b|मध्य|प्रदेश|महाराष्ट्र", msg, re.I))
    if (loc_hit or (key.startswith("confirm") and loc_in_msg)) and len(msg.split()) <= 12:
        from .extract import _fuzzy_city, extract_state, infer_state_from_city
        st = extract_state(msg.lower()) or extract_state(msg)
        guess = _fuzzy_city(msg) or _fuzzy_city(msg.split()[0] if msg.split() else "")
        city = (guess or "") if guess else ""
        if not st and city:
            st = infer_state_from_city(city)
        if st or city or (loc_hit and not payload.get("state")):
            data = {"source": "customer"}
            if st:
                data["state"] = st
            if city:
                data["city"] = city.title() if city != "new delhi" else "New Delhi"
                data["location"] = data["city"]
            elif loc_hit and not st and not payload.get("state"):
                data["state"] = msg[:80]
            execute_tool(db, conv, "save_vehicle_data", data)
    cat_hit = key == "category" or any(
        w in last_l
        for w in (
            "category", "ye kya hai", "what is it",
            "truck", "dumper", "tipper", "crane", "poclain", "loader",
            "backhoe", "excavator", "grader", "crusher",
        )
    )
    if cat_hit and not normalize_vehicle_category(payload.get("category") or ""):
        canon = normalize_vehicle_category(msg)
        if canon:
            execute_tool(db, conv, "save_vehicle_data", {"category": canon, "type": canon, "source": "customer"})


def _keep_talking(lang: str, prev: str) -> str:
    first = t(lang, "continue_chat")
    if too_similar(first, prev):
        return t(lang, "continue_chat_2")
    return first


def avoid_repeat(db, conv: AiConversation, lang: str, reply: str, recents: list[str]) -> str:
    text = (reply or "").strip()
    if not text:
        return ""
    if not is_repeat_outbound(text, recents):
        return text
    payload = _payload(conv)
    last_kind = question_kind(recents[0]) if recents else ""
    for key in missing_fields(payload):
        if key in {"customer_name", "photos"} and last_kind in {key, "photos"}:
            continue
        alt = t(lang, key)
        if question_kind(alt) == last_kind or is_repeat_outbound(alt, recents):
            continue
        conv.error_message = f"ask:{key}"
        skipped = list(payload.get("skipped_asks") or [])
        if last_kind and last_kind not in skipped:
            skipped.append(last_kind)
            payload["skipped_asks"] = skipped
            _write_payload(conv, payload)
        return alt
    return ""


def attach_intro(db, conv: AiConversation, lang: str, reply: str) -> str:
    payload = _payload(conv)
    if payload.get("ai_introduced"):
        return reply
    active = bool(
        payload.get("brand")
        or payload.get("model")
        or payload.get("customer_confirmed")
        or conv.draft_id
    )
    payload["ai_introduced"] = True
    _write_payload(conv, payload)
    if active:
        return reply
    intro = t(lang, "intro")
    body = (reply or "").strip()
    if not body:
        return intro
    low = body.lower()
    if "ai executive" in low or "infradealer ka ai" in low or "i am infradealer" in low:
        return body
    if body.lstrip().lower().startswith("namaste"):
        return body
    return intro + "\n\n" + body


def respond(db, conv: AiConversation, text: str, media_note: str = "") -> str:
    from .cards import (
        card_clarification_prompt,
        ensure_card_id,
        needs_card_clarification,
        parse_card_mention,
        switch_active_card,
    )
    from .account_filter import sync_conversation_account

    try:
        sync_conversation_account(db, conv)
    except Exception:
        pass

    lang0 = _conv_lang(db, conv, text)
    if media_note and "photo_rejected_max" in media_note:
        return t(lang0, "photo_at_max")

    mentioned = parse_card_mention(text or "")
    payload_pre = _payload(conv)
    if payload_pre.get("awaiting_card_choice") and mentioned:
        draft = switch_active_card(db, conv, mentioned)
        if draft:
            return t(lang0, "card_switched", card=draft.card_id)
    if mentioned:
        draft = switch_active_card(db, conv, mentioned)
        if draft:
            return t(lang0, "card_switched", card=draft.card_id)

    if needs_card_clarification(db, conv, text or ""):
        return card_clarification_prompt(db, conv, lang0)

    if conv.draft_id:
        try:
            from ..models import AiListingDraft

            draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()
            if draft:
                ensure_card_id(db, draft)
                pl = _payload(conv)
                if pl.get("active_card_id") != draft.card_id:
                    pl["active_card_id"] = draft.card_id
                    _write_payload(conv, pl)
        except Exception:
            pass

    seed_memory(db)
    lang = _conv_lang(db, conv, text)
    extra = load_reps(db)
    fields = extract_turn(text, extra_reps=extra, media_note=media_note)
    recents = recent_outbound_bodies(db, conv.conversation_id, 6)
    payload0 = _payload(conv)

    # 1) Clear / delete conversation — do this BEFORE confirm loop can re-dump the card
    if wants_clear_conversation(text) or (
        wants_new_chat(text) and re.search(r"\b(delete|clear|reset|hata|mita)\b", text or "", re.I)
    ):
        from .confirm import reset_ai_conversation

        reset_ai_conversation(db, conv)
        harvest_turn(db, text, {}, "")
        return t(lang, "chat_cleared")

    if wants_new_chat(text):
        from .confirm import start_new_listing

        start_new_listing(db, conv, {}, [])
        harvest_turn(db, text, {}, "")
        return attach_intro(db, conv, lang, t(lang, "vehicle_new_ok") + "\n" + t(lang, "intent"))

    # 2) Casual greetings — never dump CARD summary
    if is_casual_chat(text) and not fields and not media_note:
        harvest_turn(db, text, {}, "")
        if payload0.get("awaiting_confirm") or conv.state == "AWAITING_CONFIRMATION":
            card = payload0.get("active_card_id") or "CARD"
            return t(lang, "casual_while_confirm", card=card)
        if payload0.get("chat_cleared"):
            payload0["chat_cleared"] = False
            _write_payload(conv, payload0)
            return t(lang, "chat_cleared_followup")
        return t(lang, "casual_hi")

    # After clear, ignore stray "haan/han" that would re-open confirm
    if payload0.get("chat_cleared") and is_yes(text) and not fields:
        payload0["chat_cleared"] = False
        _write_payload(conv, payload0)
        harvest_turn(db, text, {}, "")
        return t(lang, "chat_cleared_followup")

    if account_busy(payload0) and should_intercept_account(payload0, text):
        acc = handle_account(db, conv, text, lang)
        if acc:
            harvest_turn(db, "[account]" if payload0.get("account_step") == "password" else text, {}, _ask_key(conv.error_message))
            cleaned = avoid_repeat(db, conv, lang, _sanitize_reply(acc, posted=False, lang=lang), recents)
            if not (cleaned or "").strip():
                cleaned = _sanitize_reply(acc, posted=False, lang=lang)
            return attach_intro(db, conv, lang, cleaned)
        # Not a clear account answer — fall through to listing chat
    elif account_busy(payload0) and payload0.get("account_step") == "ask_exists":
        # Stuck account prompt while user talks listing — pause account ask
        payload0["account_step"] = ""
        _write_payload(conv, payload0)
        payload0 = _payload(conv)

    slot = handle_vehicle_slot(db, conv, text, fields, media_note, lang)
    if slot:
        harvest_turn(db, text, fields, _ask_key(conv.error_message))
        return attach_intro(db, conv, lang, avoid_repeat(db, conv, lang, _sanitize_reply(slot, posted=False, lang=lang), recents))

    fields = apply_extraction(db, conv, text, extra_reps=extra, media_note=media_note, fields=fields)
    apply_followup(db, conv, text)
    harvest_turn(db, text, fields, _ask_key(conv.error_message))

    payload = _payload(conv)
    last_ask = _ask_key(conv.error_message)
    if last_ask == "optional_bundle" and payload.get("optional_asked") and not payload.get("optional_done"):
        skip_opt = bool(re.search(r"\b(skip|baad me|nahi pata|koi nahi|bas yahi)\b", (text or "").lower()))
        if fields or skip_opt or (text or "").strip():
            payload["optional_done"] = True
            _write_payload(conv, payload)
            payload = _payload(conv)

    if (
        (payload.get("intent") or "").upper() == "SELL"
        and collection_ready(payload)
        and not payload.get("optional_asked")
        and not payload.get("awaiting_confirm")
        and not payload.get("customer_confirmed")
    ):
        payload["optional_asked"] = True
        conv.error_message = "ask:optional_bundle"
        _write_payload(conv, payload)
        harvest_turn(db, text, fields, "optional_bundle")
        return attach_intro(
            db, conv, lang,
            avoid_repeat(db, conv, lang, _sanitize_reply(t(lang, "optional_bundle"), posted=False, lang=lang), recents),
        )
    if photos_complete(text):
        payload["photos_complete"] = True
        _write_payload(conv, payload)
        payload = _payload(conv)
    if customer_annoyed(text):
        skip = _ask_key(conv.error_message)
        skipped = list(payload.get("skipped_asks") or [])
        if skip and skip not in skipped:
            skipped.append(skip)
            payload["skipped_asks"] = skipped
            _write_payload(conv, payload)
            payload = _payload(conv)

    posted = _draft_status(db, conv) == "POSTED"
    if posted and (fields or media_note):
        sync_posted_product(db, conv)

    if conv.state == "OTP_PENDING" or payload.get("verification_status") == "otp_pending":
        digits = re.sub(r"\D", "", text or "")
        if len(digits) == 6:
            otp_reply = fallback_reply(db, conv, text, media_note)
            return _sanitize_reply(otp_reply, posted=posted, lang=lang)

    locked = handle_confirmation(db, conv, text, fields, lang)
    if locked:
        reply = _sanitize_reply(locked, posted=posted, lang=lang)
    else:
        fast = _fast_rule_reply(db, conv, text, media_note, fields, lang)
        if fast:
            reply = _sanitize_reply(fast, posted=posted, lang=lang)
        else:
            reply = llm_reply(db, conv, text, media_note)
            if reply:
                reply = _sanitize_reply(reply, posted=posted, lang=lang)
            else:
                reply = _sanitize_reply(fallback_reply(db, conv, text, media_note), posted=posted, lang=lang)

    if customer_annoyed(text) and reply:
        sorry = t(lang, "sorry_repeat")
        if sorry.lower() not in reply.lower():
            reply = sorry + " " + reply

    # Soft anti-repeat: never blank the only answer for this turn
    cleaned = avoid_repeat(db, conv, lang, reply, recents)
    if cleaned:
        reply = cleaned
    if reply:
        reply = attach_intro(db, conv, lang, reply)
        # Strip accidental profile-name-only / "Reply Name" style junk
        reply = re.sub(r"(?im)^\s*reply\s+[a-z][a-z\s.]{1,40}\s*$", "", reply).strip() or reply
        if is_repeat_outbound(reply, recents) and not (
            conv.state == "AWAITING_CONFIRMATION" or _payload(conv).get("awaiting_confirm")
        ):
            # Prefer next missing field over silence / stale copy
            alt = fallback_reply(db, conv, text, media_note)
            if alt and not too_similar(alt, reply):
                reply = alt
    if not (reply or "").strip():
        payload = _payload(conv)
        if conv.state == "AWAITING_CONFIRMATION" or payload.get("awaiting_confirm"):
            # Never re-dump the full card for unrelated/empty turns — that felt "dumb"
            reply = t(lang, "confirm_ready")
        else:
            reply = fallback_reply(db, conv, text, media_note) or t(lang, "saving")
    return reply
