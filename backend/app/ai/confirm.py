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
        listing_url = str(payload.get("listing_url") or "").strip()
        listing_id = str(payload.get("infradealer_listing_id") or "").strip()
        if not listing_url and listing_id:
            listing_url = f"https://infradealer.com/listing/{listing_id}"
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

_YES = re.compile(
    r"^\s*("
    r"haan+|han+|ha+|hji|ji|yes+|yup|yeah|yep|ok+|okay|bilkul|bilkul\s*sahi|"
    r"sahi\s*hai|theek\s*hai|thik\s*hai|correct|confirm|kar\s*do|kar\s*dena|"
    r"submit(\s*kar\s*do)?|done|go\s*ahead|proceed|"
    r"हाँ+|हां+|जी+|सही\s*है|ठीक\s*है|बिल्कुल|कर\s*दो"
    r")[\s!.]*$",
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
    r"badlo|change|update|correct|kar\s*do|nahi|नहीं|लाख|lakh|lac)\b",
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


def is_no(text: str) -> bool:
    msg = (text or "").strip()
    if not msg:
        return False
    return bool(_NO.match(msg))


def confirmation_has_modification(text: str) -> bool:
    """True when message looks like confirm + correction (not pure yes)."""
    msg = (text or "").strip()
    if not msg:
        return False
    if is_yes(msg) and not re.search(r"\d|lakh|lac|model|year|brand|badlo|change", msg, re.I):
        return False
    return bool(
        re.search(r"\b(haan|han|yes|हाँ)\b", msg, re.I)
        and re.search(r"\b(price|rate|year|model|lakh|lac|badlo|change|कर\s*दो)\b|\d", msg, re.I)
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
    for label, key in (
        ("Year", "year"),
        ("KM", "running_km"),
        ("Hours", "operating_hours"),
        ("Price", "expected_price"),
        ("Budget", "budget"),
        ("State", "state"),
        ("City", "city"),
    ):
        if snap.get(key):
            lines.append(f"{label}: {snap[key]}")
    body = "\n".join(lines) if lines else "Listing details"
    code = (lang or "hinglish").lower()
    if code in {"hi", "hindi"}:
        return f"कृपया कन्फर्म करें:\n{body}\n\nसही हैं तो हाँ लिखें।"
    if code in {"en", "english"}:
        return f"Please confirm:\n{body}\n\nReply YES to confirm."
    return f"Please confirm details:\n{body}\n\nSahi hain to Haan likhiye."


def send_summary(db: Session, conv: AiConversation, lang: str = "hinglish") -> str:
    payload = _payload(conv)
    text = summary_text(payload, lang=lang)
    payload["awaiting_confirm"] = True
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
        payload = _payload(conv)
        payload["awaiting_confirm"] = False
        payload["customer_confirmed"] = False
        _write_payload(conv, payload)
        return t(lang, "unclear")
    if is_yes(text):
        result = submit_confirmed_listing(db, conv)
        if result.get("ok") is False:
            if result.get("error") == "token_insufficient":
                buy = result.get("buy_link") or "https://infradealer.com/wallet"
                return t(lang, "tokens_buy", link=buy)
            return t(lang, "submit_blocked")
        _send_listing_button(db, conv, lang)
        return t(lang, "submitted")
    if is_no(text):
        payload = _payload(conv)
        payload["awaiting_confirm"] = False
        _write_payload(conv, payload)
        return t(lang, "unclear")
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
    conv.draft_id = None
    for key in (
        "brand", "model", "year", "expected_price", "running_km", "operating_hours",
        "category", "type", "state", "city", "location", "media_ids", "awaiting_confirm",
        "customer_confirmed", "summary_json", "confirmed_json", "listing_status",
        "submission", "filter_result", "missing_fields",
    ):
        payload[key] = [] if key == "media_ids" else ({} if key.endswith("json") or key in {"filter_result", "submission", "summary_json", "confirmed_json"} else None)
    payload["media_ids"] = list(media_ids or [])
    payload["draft_version"] = 1
    payload["intent"] = (fields or {}).get("intent") or payload.get("intent") or "SELL"
    if fields:
        for k, v in fields.items():
            if v is not None:
                payload[k] = v
    _write_payload(conv, payload)
    draft = _draft_for(db, conv)
    return draft
