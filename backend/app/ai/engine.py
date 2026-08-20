import json
import logging
import re

import httpx

from ..models import AiConversation, AiListingDraft, Chat
from ..services import resolve_ai_config
from .account import account_busy, handle_account, wants_new_chat
from .confirm import handle_confirmation, handle_vehicle_slot, is_no, is_yes, collection_ready, sync_posted_product
from .extract import extract_from_text
from .i18n import _GREET, _WEAK, language_instruction, pick_language, t
from .memory import (
    customer_annoyed,
    harvest_turn,
    harvest_with_llm,
    is_repeat_outbound,
    load_reps,
    photos_complete,
    prompt_block,
    question_kind,
    recent_outbound_bodies,
    seed_memory,
    too_similar,
)
from .prompt import SYSTEM_PROMPT
from .schema import missing_fields, review_gaps, normalize_vehicle_category
from .tools import TOOL_DEFS, _payload, _write_payload, execute_tool
from ..identity import looks_like_price, usable_person_name

log = logging.getLogger("infradealer.ai")

FIELD_KEYS = (
    "intent", "brand", "model", "year", "expected_price", "state",
    "budget", "category", "customer_name", "optional_bundle",
)


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


def _photo_prompt(payload: dict, media_note: str, lang: str) -> str | None:
    if payload.get("photos_complete"):
        return None
    n = len(payload.get("media_ids") or [])
    if "DOWNLOAD_FAILED" in (media_note or ""):
        return t(lang, "photo_fail")
    if n == 0:
        return _with_ack(lang, "photos", payload)
    if payload.get("photos_prompted"):
        return None
    if n >= 1:
        return t(lang, "photo_more")
    return None


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
        extra = _photo_prompt(payload, media_note, lang)
        still = [m for m in missing_fields(payload) if m not in {"photos", "customer_name"}]
        if extra and still:
            if extra == t(lang, "photo_more"):
                payload["photos_prompted"] = True
                _write_payload(conv, payload)
            return extra
        if extra and "photos" in (payload.get("missing_fields") or []):
            if extra == t(lang, "photo_more"):
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
    learned = prompt_block(db)
    recents = recent_outbound_bodies(db, conv.conversation_id, 4)
    user_block = (
        "CURRENT_STATE: "
        + json.dumps({
            "state": conv.state,
            "mobile": conv.mobile,
            "profile_status": conv.profile_status,
            "profile_id": conv.profile_id,
            "reply_language": lang,
            "draft_status": _draft_status(db, conv),
            "review_gaps": review_gaps(payload),
            "missing_fields": missing_fields(payload),
            "photos_complete": bool(payload.get("photos_complete")),
            "data": payload,
        }, ensure_ascii=False)
        + "\nMEDIA: "
        + (media_note or "none")
        + "\nCUSTOMER_MESSAGE_START\n"
        + (text or "")[:1500]
        + "\nCUSTOMER_MESSAGE_END\n"
        + "You MUST send a real WhatsApp reply. Never empty. Tamiz: Sir/Ma'am, aap, ji. Never bhai/slang. "
        + "Talk normally. If the message is outside listing training, still answer with your own judgment, then continue. "
        + "CUSTOMER TYPING is messy: spelling/typos are normal. Infer the meaning. Never ask them to retype due to spelling. indor=Indore, kimat=price, madal=model, fotu=photo. "
        + "Do not repeat your last messages or the same question type. If that field was already asked, skip it. "
        + "LAST_ASSISTANT:\n"
        + "\n---\n".join((recents or ["(none)"])[:4])
        + "\n"
        + "Do not introduce yourself again if CURRENT_STATE.data.ai_introduced is true — backend already did that once. "
        + "After CONFIRMED/POSTED the customer may still talk (hi, or he, aur hai, gadi, batau) — keep chatting. "
        + "If they hint another vehicle, collect the NEW one. If they hint a change, update the same one. "
        + "Rate is money only, never a model code like 1613. Location is a city, never words like Hor/he/hai. "
        + "Understand mixed Hindi+English. Repeat what you got, then ONE polite question if listing is still open. "
        + "Do not re-ask fields already in CURRENT_STATE.data. Prefer inferring messy spelling over saying you did not understand. "
        + "MANDATORY for listing (never skip): category (truck/dumper/tipper/crane/poclain/loader/backhoe/jcb/excavator/grader/crusher/other), company/brand, model name, manufacturing year, price, STATE. "
        + "OPTIONAL fields: ask ALL in ONE message once, never again. "
        + "After Haan/Yes, one-time account: InfraDealer account hai?, OTP, password, broker ya user. "
        + "If CURRENT_STATE.state is AWAITING_CONFIRMATION and they correct anything in natural language "
        + "(Location मध्य प्रदेश / Madhya pradesh / rate 40 lakh) WITHOUT saying nahi, call save_vehicle_data "
        + "and resend the updated summary card. Look at previous messages if they say 'location karo'. Never stay silent. "
        + "Never invent OTP. Never show password back. Never publish. Reply in {lang}."
    )
    sys = SYSTEM_PROMPT + "\n\n" + language_instruction(lang)
    if learned:
        sys += "\n\n" + learned
    messages = [{"role": "system", "content": sys}]
    messages.extend(_history(db, conv)[-10:])
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
        "temperature": 0.7,
        "max_tokens": 500,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }
    posted = _draft_status(db, conv) == "POSTED"
    try:
        with httpx.Client(timeout=45) as client:
            use_tools = True
            for _ in range(8):
                payload = dict(body)
                if not use_tools:
                    payload.pop("tools", None)
                    payload.pop("tool_choice", None)
                resp = client.post(url, headers=headers, json=payload)
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
    seed_memory(db)
    lang = _conv_lang(db, conv, text)
    extra = load_reps(db)
    fields = extract_turn(text, extra_reps=extra, media_note=media_note)
    recents = recent_outbound_bodies(db, conv.conversation_id, 6)
    payload0 = _payload(conv)
    if wants_new_chat(text):
        from .confirm import start_new_listing

        start_new_listing(db, conv, {}, [])
        harvest_turn(db, text, {}, "")
        return attach_intro(db, conv, lang, t(lang, "vehicle_new_ok") + "\n" + t(lang, "intent"))
    if account_busy(payload0):
        acc = handle_account(db, conv, text, lang)
        if acc:
            harvest_turn(db, "[account]" if payload0.get("account_step") == "password" else text, {}, _ask_key(conv.error_message))
            cleaned = avoid_repeat(db, conv, lang, _sanitize_reply(acc, posted=False, lang=lang), recents)
            if not (cleaned or "").strip():
                cleaned = _sanitize_reply(acc, posted=False, lang=lang)
            return attach_intro(db, conv, lang, cleaned)
        harvest_turn(db, text, {}, _ask_key(conv.error_message))
        return attach_intro(db, conv, lang, t(lang, "otp_ask") if payload0.get("account_step") == "otp" else t(lang, "account_ask"))

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
        reply = llm_reply(db, conv, text, media_note)
        if reply:
            reply = _sanitize_reply(reply, posted=posted, lang=lang)
        else:
            reply = _sanitize_reply(fallback_reply(db, conv, text, media_note), posted=posted, lang=lang)

    if customer_annoyed(text) and reply:
        sorry = t(lang, "sorry_repeat")
        if sorry.lower() not in reply.lower():
            reply = sorry + " " + reply

    reply = avoid_repeat(db, conv, lang, reply, recents)
    if reply:
        reply = attach_intro(db, conv, lang, reply)
        if is_repeat_outbound(reply, recents) and not (
            conv.state == "AWAITING_CONFIRMATION" or _payload(conv).get("awaiting_confirm")
        ):
            reply = ""
    if not (reply or "").strip():
        payload = _payload(conv)
        if conv.state == "AWAITING_CONFIRMATION" or payload.get("awaiting_confirm"):
            from .confirm import send_summary, snapshot
            prev = payload.get("summary_json") if isinstance(payload.get("summary_json"), dict) else {}
            data = snapshot(db, conv, payload)
            if data != prev:
                reply = send_summary(db, conv, lang)
            else:
                reply = t(lang, "confirm_ready")
        else:
            reply = t(lang, "saving")
    try:
        if _payload(conv).get("account_step") != "password":
            harvest_with_llm(db, text)
    except Exception:
        log.debug("harvest skipped", exc_info=True)
    return reply
