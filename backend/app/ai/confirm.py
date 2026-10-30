"""Confirmation helpers for Relationship Manager / account / legacy engine."""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from ..models import AiConversation, AiListingDraft
from .i18n import t
from .schema import listing_title, loads, dumps
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.confirm")


def _send_listing_button(db: Session, conv: AiConversation, lang: str) -> None:
    """Send interactive button message with listing link after push."""
    try:
        from ..services import get_or_create_settings, send_whatsapp_button, store_chat
        from ..config import settings as app_settings

        payload = _payload(conv)
        if payload.get("listing_review_notified"):
            return  # the "approved" message with the link already went out
        if str(payload.get("listing_status") or "").upper() not in {"POSTED", "LIVE", "APPROVED", "PUBLISHED"}:
            return  # pending review: the "submitted" reply covers it; never claim "live"
        from .data_push import public_listing_url

        listing_url = str(payload.get("listing_url") or "").strip().replace("/listing/", "/listings/")
        listing_id = str(payload.get("infradealer_listing_id") or "").strip()
        if not listing_url and listing_id:
            listing_url = public_listing_url(listing_id)
        if not listing_url:
            return
        meta = get_or_create_settings(db)
        body_text = t(lang, "listing_live_button")
        buttons = [
            {"title": t(lang, "btn_view_listing"), "url": listing_url},
            {"title": t(lang, "btn_browse"), "url": "https://infradealer.com"},
        ]
        result = send_whatsapp_button(meta, conv.mobile, body_text, buttons)
        store_chat(
            db,
            wamid=result.get("wamid") or f"btn.{conv.id}.{int(__import__('time').time())}",
            conversation_id=conv.conversation_id or f"CONV_{conv.mobile}",
            from_mobile=meta.phone_number_id or "infradealer",
            from_name="InfraDealer",
            to_mobile=conv.mobile,
            direction="outbound",
            body=body_text,
            status="sent",
            unread=False,
        )
    except Exception:
        log.exception("listing button send failed for %s", conv.mobile)

_YES_TOKEN = (
    r"haan+|han+|ha+|hji|ji|yes+|yea+h?|yup|yeah|yep|ok+|okay|bilkul|bilkul\s*sahi|"
    r"sahi\s*hai|theek\s*hai|thik\s*hai|correct|confirm|kar\s*do|kar\s*dena|"
    r"submit(\s*kar\s*do)?|done|go\s*ahead|proceed|"
    r"हाँ+|हां+|जी+|सही\s*है|ठीक\s*है|बिल्कुल|कर\s*दो"
)
_YES = re.compile(
    # "yes", "haan", and glued repeats like "yesyes" / "haanhaan"
    rf"^\s*(?:(?:{_YES_TOKEN})[\s!.,]*)+$",
    re.I,
)
_POST_CONFIRM = re.compile(
    r"\b(post\s*(kar|kr|do|dena|karo|krdo|kardo)|submit\s*(kar|kr|do)|"
    r"listing\s*(bana|banao|kar\s*do|post)|bas\s*(yahi|itna|post|submit))\b",
    re.I,
)
_NO = re.compile(
    r"^\s*("
    r"nahi+|na+|no+|nope|galat|wrong|cancel|mat\s*karo|rehne\s*do|"
    r"नहीं+|ना+|गलत|रद्द|रहने\s*दो"
    r")[\s!.]*$",
    re.I,
)
_MOD = re.compile(
    r"\b(price|rate|year|model|brand|location|state|city|km|hours|"
    r"gadi|gaadi|reg(?:istration)?|number\s*plate|regn?|"
    r"badlo|change|update|correct|kar\s*do|nahi|नहीं|लाख|lakh|lac|"
    r"गाड़ी|गाडी|नंबर)\b",
    re.I,
)


def is_yes(text: str) -> bool:
    msg = (text or "").strip()
    if not msg or len(msg) > 80:
        return False
    if _MOD.search(msg) and not _YES.match(msg):
        if re.search(r"\d", msg) or re.search(r"lakh|lac|model|year|brand", msg, re.I):
            return False
    return bool(_YES.match(msg))


# Soft confirms while a confirm card is open — not bare "ok" outside that context.
_SOFT_CONFIRM = re.compile(
    r"(?:isme|is\s*me|is\s*mein|इसमें|इस\s*मे)\s*(?:in\s*)?(?:puri|poori|पूर्ण|पूरी)\s*"
    r"(?:detail|details|डिटेल|डिटेल्स)"
    r"|(?:ye|yeh|ये)?\s*details?\s*(?:sahi|complete|puri|पूर्ण)\s*(?:hain?|hai|हैं|है)?"
    r"|details?\s*(?:are\s*)?(?:complete|sahi|ok)"
    r"|पूरी\s*डिटेल(?:स)?"
    r"|sab\s*(?:detail|details)\s*(?:sahi|hai|hain|complete|aa\s*gayi|aa\s*gyi)",
    re.I,
)


def is_soft_confirm(text: str) -> bool:
    """True for 'isme puri detail hai' style replies — only use when awaiting_confirm."""
    msg = (text or "").strip()
    if not msg or len(msg) > 120:
        return False
    if re.search(r"\b(badlo|change|update|price|year|brand|model|nahi|नहीं)\b", msg, re.I):
        return False
    if re.search(r"\d{3,}", msg):  # prices / years / plates → likely a correction dump
        return False
    return bool(_SOFT_CONFIRM.search(msg))


def confirms_listing(text: str, *, awaiting: bool = False) -> bool:
    """Yes / post-now, plus soft 'details complete' only while a confirm card is open."""
    if is_yes(text) or _POST_CONFIRM.search(text or ""):
        return True
    return bool(awaiting and is_soft_confirm(text))


def is_no(text: str) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    return bool(_NO.match(msg))


def confirmation_has_modification(text: str) -> bool:
    """True when message looks like confirm + correction (not pure yes)."""
    from .data_filteration import _PLATE_CUE, registration_number_from_text

    msg = (text or "").strip()
    if not msg:
        return False
    # A gaadi number alone during confirm is a correction, not a yes.
    if registration_number_from_text(msg) or _PLATE_CUE.search(msg):
        return True
    if is_yes(msg) and not re.search(r"\d|lakh|lac|model|year|brand|badlo|change|gadi|gaadi|नंबर", msg, re.I):
        return False
    return bool(
        re.search(r"\b(haan|han|yes|हाँ)\b", msg, re.I)
        and re.search(r"\b(price|rate|year|model|lakh|lac|badlo|change|gadi|gaadi|कर\s*दो)\b|\d", msg, re.I)
    )


def collection_ready(payload: dict) -> bool:
    from .data_filteration import is_collection_ready

    return is_collection_ready(payload)


def snapshot(db_or_payload=None, conv: AiConversation | None = None, payload: dict | None = None) -> dict:
    """Confirmed listing snapshot. Supports snapshot(payload) or snapshot(db, conv, payload)."""
    if isinstance(db_or_payload, dict) and conv is None and payload is None:
        pl = db_or_payload
    elif payload is not None:
        pl = payload
    elif conv is not None:
        pl = _payload(conv)
    else:
        pl = {}
    keys = (
        "intent", "category", "type", "brand", "model", "year", "running_km",
        "operating_hours", "expected_price", "budget", "state", "city", "location",
        "fuel", "owners", "condition", "description", "active_card_id",
    )
    out = {k: pl.get(k) for k in keys if pl.get(k) not in (None, "", [], {})}
    out["vehicle"] = listing_title(pl)
    out["card_id"] = pl.get("active_card_id") or out.get("active_card_id") or ""
    if conv is not None and getattr(conv, "draft_id", None):
        # Prefer draft card_id when present
        try:
            draft = db_or_payload.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first() if db_or_payload is not None else None
            if draft and draft.card_id:
                out["card_id"] = draft.card_id
        except Exception:
            pass
    return out


def summary_text(payload_or_snap: dict, lang: str = "hinglish", card_id: str = "") -> str:
    """Human-readable confirmation card text. Accepts payload or snapshot dict."""
    snap = payload_or_snap if isinstance(payload_or_snap, dict) else {}
    # If looks like raw payload (has brand but no vehicle), normalize
    if snap.get("brand") and not snap.get("vehicle"):
        snap = {**snapshot(snap), **snap}
    card = card_id or snap.get("card_id") or snap.get("active_card_id") or ""
    lines = []
    if card:
        lines.append(f"Card : {card}")
    vehicle = snap.get("vehicle") or listing_title(snap)
    if vehicle:
        lines.append(f"Vehicle: {vehicle}")
    # Thin titles like bare "JCB" — still show brand/model/category when present.
    brand = str(snap.get("brand") or "").strip()
    model = str(snap.get("model") or "").strip()
    category = str(snap.get("category") or snap.get("type") or "").strip()
    title_l = vehicle.lower()
    if brand and brand.lower() not in title_l:
        lines.append(f"Brand: {brand}")
    if model and model.lower() not in title_l:
        lines.append(f"Model: {model}")
    if category and category.lower() not in title_l:
        lines.append(f"Category: {category}")
    for label, key in (
        ("Year", "year"),
        ("KM", "running_km"),
        ("Hours", "operating_hours"),
        ("Price", "expected_price"),
        ("Budget", "budget"),
        ("State", "state"),
        ("City", "city"),
        ("Colour", "colour"),
        ("Body", "body_type"),
        ("Details", "description"),
    ):
        if key == "city" and str(snap.get("city") or "").strip().lower() == str(snap.get("state") or "").strip().lower():
            continue
        if snap.get(key):
            lines.append(f"{label}: {snap[key]}")
    body = "\n".join(lines) if lines else "Listing details"
    code = (lang or "hinglish").lower()
    if code in {"hi", "hindi"}:
        return f"कृपया कन्फर्म करें:\n{body}\n\nसही हैं तो हाँ लिखें।"
    if code in {"en", "english"}:
        return f"Please confirm:\n{body}\n\nReply YES to confirm."
    return f"Please confirm details:\n{body}\n\nSahi hain to Haan likhiye."


def no_photo_note(payload: dict, lang: str = "hinglish") -> str:
    """Listing needs at least 2 photos: say so on the summary before the YES."""
    if str(payload.get("intent") or "").upper() != "SELL" or len(payload.get("media_ids") or []) >= 2 or payload.get("photos_complete"):
        return ""
    code = (lang or "hinglish").lower()
    if code in {"hi", "hindi"}:
        return "📷 कम से कम 2 फोटो ज़रूरी हैं — हाँ से पहले गाड़ी की फोटो भेजें।"
    if code in {"en", "english"}:
        return "📷 At least 2 photos are required — send the vehicle photos before replying YES."
    return "📷 Kam se kam 2 photo zaroori hain — Haan se pehle gaadi ki photos bhejiye."


def no_place_note(payload: dict, lang: str = "hinglish") -> str:
    """Buyers filter by state: ask for it on the summary (not blocking — the YES still posts)."""
    if str(payload.get("intent") or "").upper() != "SELL":
        return ""
    if any(payload.get(k) not in (None, "") for k in ("state", "city", "location")):
        return ""
    code = (lang or "hinglish").lower()
    if code in {"hi", "hindi"}:
        return "📍 गाड़ी किस राज्य/शहर में है? लिख दें तो खरीदार आसानी से ढूँढ पाएँगे।"
    if code in {"en", "english"}:
        return "📍 Which state/city is the vehicle in? Adding it helps buyers find it."
    return "📍 Gaadi kis state/city me hai? Likh dijiye — buyers ko dhoondhne me aasani hogi."


def summary_notes(payload: dict, lang: str = "hinglish") -> list[str]:
    return [n for n in (no_place_note(payload, lang), no_photo_note(payload, lang)) if n]


def send_summary(db: Session, conv: AiConversation, lang: str = "hinglish") -> str:
    from .buyer import buyer_reply, is_buyer

    payload = _payload(conv)
    if is_buyer(payload):
        return buyer_reply(db, conv, lang)
    text = summary_text(payload, lang=lang)
    for note in summary_notes(payload, lang):
        text += "\n" + note
    payload["awaiting_confirm"] = True
    payload["confirm_rejected"] = False
    payload["summary_json"] = snapshot(payload)
    _write_payload(conv, payload)
    return text


def handle_confirmation(
    db: Session,
    conv: AiConversation,
    text: str,
    lang: str | dict = "hinglish",
    lang_kw: str | None = None,
) -> str | None:
    """Legacy engine helper — prefer chat_memory.submit_confirmed_listing on hot path.

    Callers historically passed ``fields`` dict as the 4th arg; accept that and
    use ``lang_kw`` / default hinglish so ``t({}, ...)`` never happens.
    """
    from .chat_memory import submit_confirmed_listing
    from .i18n import t

    if isinstance(lang, dict):
        lang = lang_kw if isinstance(lang_kw, str) and lang_kw else "hinglish"
    elif lang_kw and isinstance(lang_kw, str):
        lang = lang_kw
    if not isinstance(lang, str) or not lang:
        lang = "hinglish"

    # New sell/buy intent abandons a stale confirm prompt — let collection/LLM continue.
    low = (text or "").lower()
    if re.search(r"\b(bech|sell|bechna|bechni|bikau|kharid|buy|kharidna)\b", low, re.I):
        if not is_yes(text) and not is_no(text) and not confirmation_has_modification(text):
            payload = _payload(conv)
            if payload.get("awaiting_confirm") or conv.state == "AWAITING_CONFIRMATION":
                payload["awaiting_confirm"] = False
                payload["customer_confirmed"] = False
                _write_payload(conv, payload)
                if conv.state == "AWAITING_CONFIRMATION":
                    conv.state = "COLLECTING"
            return None

    if confirmation_has_modification(text):
        # Clear confirm lock so the same turn's extraction can apply gaadi number / price / etc.
        payload = _payload(conv)
        payload["awaiting_confirm"] = False
        payload["customer_confirmed"] = False
        _write_payload(conv, payload)
        return None
    payload = _payload(conv)
    awaiting = bool(payload.get("awaiting_confirm") or conv.state == "AWAITING_CONFIRMATION")
    if confirms_listing(text, awaiting=awaiting):
        result = submit_confirmed_listing(db, conv)
        if result.get("ok") is False:
            from .chat_memory import submit_failure_reply

            if result.get("error") in {"photos_required", "token_insufficient"} or result.get("status") in {
                "RETRY", "DELIVERY_FAILED", "FAILED", "SUBMISSION_BLOCKED",
            }:
                return submit_failure_reply(lang, result)
            errs = list(result.get("validation_errors") or []) + list(result.get("conflicts") or [])
            if any("PRICE" in str((e or {}).get("code") or "").upper() for e in errs if isinstance(e, dict)):
                return submit_failure_reply(lang, {**result, "reason_code": "PRICE_OUTLIER"})
            if result.get("error") == "listing_not_ready":
                # Inflated/outlier price often lands here with empty missing_fields.
                if any("PRICE" in str((e or {}).get("code") or "").upper() for e in (result.get("validation_errors") or []) if isinstance(e, dict)):
                    return submit_failure_reply(lang, {**result, "reason_code": "PRICE_OUTLIER"})
                return submit_failure_reply(lang, result)
            missing = [m.get("field") if isinstance(m, dict) else m for m in (result.get("missing_fields") or [])]
            if missing and missing[0]:
                # "Confirm again" would just loop — ask for the field that blocked the submit.
                from .engine import FIELD_KEYS, _with_ack

                aliases = {"operating_hours": "hours", "running_km": "km", "price": "expected_price", "location": "state", "city": "state"}
                key = aliases.get(missing[0], missing[0])
                if key not in {*FIELD_KEYS, "hours", "km"}:
                    key = "more_detail"
                conv.error_message = f"ask:{key}"
                return _with_ack(lang, key, _payload(conv))
            return submit_failure_reply(lang, result)
        _send_listing_button(db, conv, lang)
        return t(lang, "submitted")
    if is_no(text):
        payload = _payload(conv)
        payload["awaiting_confirm"] = False
        payload["customer_confirmed"] = False
        # Stop the ready→confirm loop: a bare "No" must ask what to change, not re-dump the card.
        payload["confirm_rejected"] = True
        _write_payload(conv, payload)
        if conv.state == "AWAITING_CONFIRMATION":
            conv.state = "COLLECTING"
        return t(lang, "confirm_what_change")
    return None


def handle_vehicle_slot(db: Session, conv: AiConversation, text: str, lang: str = "hinglish") -> str | None:
    """Legacy multi-vehicle slot — no-op when using card isolation."""
    return None


def sync_posted_product(db: Session, conv: AiConversation, product_id: int | None = None) -> None:
    payload = _payload(conv)
    if product_id:
        payload["posted_product_id"] = product_id
    _write_payload(conv, payload)


def reset_ai_conversation(db: Session, conv: AiConversation) -> None:
    """Clear chat listing memory; keep identity/account fields."""
    keep = {
        "wa_name", "customer_name", "whatsapp_number", "profile_id", "profile_status",
        "account_onboarded", "account_type", "account_eligibility", "otp_verified",
        "infradealer_user_id", "ai_introduced",
    }
    old = _payload(conv)
    fresh = loads("{}")
    for k in keep:
        if old.get(k) is not None:
            fresh[k] = old[k]
    fresh["chat_cleared"] = True
    conv.draft_id = None
    conv.intent = ""
    conv.state = "NEW"
    _write_payload(conv, fresh)


def start_new_listing(db: Session, conv: AiConversation, fields: dict | None = None, media_ids: list | None = None) -> AiListingDraft:
    """Start a fresh draft/card for another vehicle."""
    from .tools import _draft_for

    payload = _payload(conv)
    # Persist previous card session if any
    try:
        from .cards import persist_active_card_session

        persist_active_card_session(db, conv)
    except Exception:
        pass
    try:
        from .vision import cancel_photo_reread

        cancel_photo_reread(conv.id)
    except Exception:
        pass
    conv.draft_id = None
    for key in (
        "brand", "model", "year", "expected_price", "running_km", "operating_hours",
        "category", "type", "state", "city", "location", "media_ids", "awaiting_confirm",
        "customer_confirmed", "summary_json", "confirmed_json", "listing_status",
        "submission", "filter_result", "missing_fields",
    ):
        payload[key] = [] if key == "media_ids" else ({} if key.endswith("json") or key in {"filter_result", "submission", "summary_json", "confirmed_json"} else None)
    from copy import deepcopy

    from .cards import CARD_SESSION_KEYS
    from .schema import empty_payload

    defaults = empty_payload()
    intent = payload.get("intent")
    for key in CARD_SESSION_KEYS + _CARD_PUSH_KEYS:
        payload[key] = deepcopy(defaults.get(key))
    payload["media_ids"] = list(media_ids or [])
    payload["draft_version"] = 1
    payload["intent"] = (fields or {}).get("intent") or intent or "SELL"
    if fields:
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
    _write_payload(conv, payload)
    draft = _draft_for(db, conv)
    return draft


_CARD_PUSH_KEYS = (
    "push_stage", "submission", "pending_notification", "listing_edit_mode", "editing_draft_id",
    "confirmed_version", "rm_state", "workflow_state", "master_workflow_state", "chat_cleared",
)
_SUBMITTED = {"READY_FOR_REVIEW", "PENDING_REVIEW", "POSTED", "APPROVED", "LIVE", "PUBLISHED"}
_NEW_CARD_KEYS = (
    "brand", "model", "year", "expected_price", "state", "city", "location",
    "operating_hours", "running_km", "category",
)


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def card_submitted(db: Session, conv: AiConversation) -> bool:
    """Active card was already sent to InfraDealer — a new vehicle needs a new card."""
    if not conv.draft_id:
        return False
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == conv.draft_id).first()
    return bool(draft) and (draft.status or "").upper() in _SUBMITTED


def maybe_start_new_card(db: Session, conv: AiConversation, fields: dict | None) -> bool:
    """Open a fresh card when the message describes another vehicle.

    Always after the active card was submitted; for the office line also when a
    different brand/model arrives on an unsubmitted card (operators paste
    listings back to back for different customers).
    """
    fields = fields or {}
    if not conv.draft_id or not (fields.get("brand") or fields.get("model")):
        return False
    if sum(1 for k in _NEW_CARD_KEYS if fields.get(k)) < 2:
        return False
    payload = _payload(conv)
    if payload.get("listing_edit_mode"):
        return False
    fresh = card_submitted(db, conv)
    if not fresh:
        cur_brand, cur_model = _norm(payload.get("brand")), _norm(payload.get("model"))
        new_brand, new_model = _norm(fields.get("brand")), _norm(fields.get("model"))
        # Same model under a real brand fixes a junk brand ("Name 5525" → "Ashok Leyland 5525");
        # Tata 2518 vs Ashok Leyland 2518 stay different vehicles.
        from .data_filteration import canonical_brand

        fixes_brand = bool(
            new_model and new_model == cur_model and cur_brand and not canonical_brand(cur_brand)
        )
        # Typed details correcting what a photo read guessed are the same vehicle (photos stay on this card).
        from .vision import photo_guessed_keys

        guessed = photo_guessed_keys(conv, payload)
        fixes_photo = bool(guessed) and all(
            not new or not cur or new == cur or key in guessed
            for key, new, cur in (("brand", new_brand, cur_brand), ("model", new_model, cur_model))
        )
        # A bare number with no brand ("8568") is too weak to abandon a card with photos.
        bare_number = not new_brand and new_model.isdigit()
        different = not (fixes_brand or fixes_photo or bare_number) and bool(
            (new_brand and cur_brand and new_brand != cur_brand)
            or (new_model and cur_model and new_model != cur_model and (not new_brand or new_brand == cur_brand))
        )
        if different:
            from .office_mode import is_office_session

            fresh = is_office_session(db, conv)
    if not fresh:
        return False
    log.info("new vehicle → new card mobile=***%s", (conv.mobile or "")[-4:])
    start_new_listing(db, conv, {}, [])
    pl = _payload(conv)
    pl["announce_new_card"] = True
    _write_payload(conv, pl)
    return True
