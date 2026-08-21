"""Push filtered listing data to InfraDealer admin via webhook API.

Flow: account_filter → chat_memory → data_filteration → data_push

After customer confirms:
  WhatsApp AI → webhook → InfraDealer listing/push → admin panel
Admin approve / reject callback → AI agent WhatsApp message to user.
On approve: public listing card link (e.g. https://infradealer.com/listings/104)
opens from WhatsApp; WA token lets InfraDealer auto-session without manual login.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..models import AiConversation, AiListingDraft, AiMedia
from ..services import normalize_mobile
from .i18n import t
from .schema import HUMAN_ONLY_STATUS, listing_title
from .tools import _draft_for, _log, _payload, _write_payload

log = logging.getLogger("infradealer.ai.data_push")

PUBLIC_SITE = "https://infradealer.com"
LISTING_PATH = "/listings"  # https://infradealer.com/listings/104


@dataclass
class PushResult:
    ok: bool
    draft_id: int | None = None
    card_id: str = ""
    status: str = ""
    listing_id: str = ""
    listing_url: str = ""
    already_pushed: bool = False
    error: str = ""
    need_confirm: bool = False
    need_photos: bool = False
    account_blocked: bool = False
    buy_link: str = ""
    gaps: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "ok": self.ok,
            "draft_id": self.draft_id,
            "card_id": self.card_id,
            "status": self.status,
            "listing_id": self.listing_id,
            "listing_url": self.listing_url,
            "already_pushed": self.already_pushed,
            "gaps": list(self.gaps),
        }
        if self.error:
            out["error"] = self.error
        if self.need_confirm:
            out["need_confirm"] = True
        if self.need_photos:
            out["need_photos"] = True
        if self.account_blocked:
            out["account_blocked"] = True
            out["buy_link"] = self.buy_link
        out.update(self.extra)
        return out


def listing_id_from_payload(payload: dict | None, listing_id: str = "") -> str:
    data = payload or {}
    listing = data.get("listing") if isinstance(data.get("listing"), dict) else {}
    extra = data.get("data") if isinstance(data.get("data"), dict) else {}
    for src in (listing, extra, data):
        if not isinstance(src, dict):
            continue
        for key in ("listing_id", "id", "listingId", "listing_number"):
            val = str(src.get(key) or "").strip()
            if val:
                return val
    return str(listing_id or "").strip()


def public_listing_url(listing_id: str) -> str:
    """Canonical public card URL — opens on InfraDealer without requiring site login page first."""
    lid = str(listing_id or "").strip()
    if not lid:
        return ""
    return f"{PUBLIC_SITE}{LISTING_PATH}/{lid}"


def wa_open_token(mobile: str, listing_id: str) -> str:
    """HMAC token so InfraDealer can trust WhatsApp redirect and auto-session the seller."""
    from ..infradealer.crypto import signed_token

    phone = normalize_mobile(mobile)
    lid = str(listing_id or "").strip()
    if not phone or not lid:
        return ""
    return signed_token("wa_listing_open", f"{phone}|{lid}", length=32)


def listing_open_url(
    listing_id: str,
    *,
    mobile: str = "",
    payload: dict | None = None,
    prefer_remote: bool = True,
) -> str:
    """Build listing link for WhatsApp. Prefers remote URL if InfraDealer sent one; else /listings/{id}.

    Appends from=whatsapp + wa + t so tap from WhatsApp can auto-login on InfraDealer.
    """
    from ..infradealer.events import listing_public_url as remote_url

    lid = listing_id_from_payload(payload, listing_id)
    base = ""
    if prefer_remote and payload:
        base = remote_url(payload, lid)
        # Normalize legacy /listing/ → /listings/
        if "/listing/" in base and "/listings/" not in base:
            base = base.replace("/listing/", "/listings/", 1)
    if not base:
        base = public_listing_url(lid)
    if not base:
        return ""
    phone = normalize_mobile(mobile)
    token = wa_open_token(phone, lid) if phone and lid else ""
    if not token:
        return base
    sep = "&" if "?" in base else "?"
    qs = urlencode({"from": "whatsapp", "wa": phone, "t": token})
    return f"{base}{sep}{qs}"


def approve_message(lang: str, *, url: str = "", card: str = "") -> str:
    if card and url:
        text = t(lang, "posted_card", card=card, url=url)
        return f"{text}\n\n{t(lang, 'card_cleanup_notice', card=card)}"
    if card:
        text = t(lang, "posted_card_nolink", card=card)
        return f"{text}\n\n{t(lang, 'card_cleanup_notice', card=card)}"
    if url:
        return t(lang, "posted_with_link", url=url)
    return t(lang, "posted")


def reject_message(lang: str, *, reason: str = "", card: str = "") -> str:
    if card and reason:
        text = t(lang, "rejected_card_reason", card=card, reason=reason)
        return f"{text}\n\n{t(lang, 'card_cleanup_notice', card=card)}"
    if card:
        text = t(lang, "rejected_card", card=card)
        return f"{text}\n\n{t(lang, 'card_cleanup_notice', card=card)}"
    if reason:
        return t(lang, "rejected_with_reason", reason=reason)
    return t(lang, "rejected")


def push_listing(db: Session, conv: AiConversation) -> PushResult:
    """API push: filtered/confirmed chat data → InfraDealer admin listing queue (via webhook)."""
    from .account_filter import eligibility_message, sync_conversation_account
    from .cards import ensure_card_id, photos_status
    from .data_filteration import filter_memory

    payload = _payload(conv)
    if not payload.get("customer_confirmed"):
        return PushResult(
            ok=False,
            error="Wait for Haan/Yes on the final summary first.",
            need_confirm=True,
        )

    # Ensure filtered snapshot is fresh before push
    filtered = filter_memory(db, conv, payload)
    if filtered.data:
        payload["filtered_listing"] = filtered.data.get("filtered") or payload.get("filtered_listing")
        if filtered.ready and not payload.get("summary_json"):
            payload["summary_json"] = {k: v for k, v in filtered.data.items() if k != "filtered"}
            _write_payload(conv, payload)

    verdict = sync_conversation_account(db, conv, refresh=True)
    payload = _payload(conv)
    if not verdict.can_post and verdict.reason != "no_account":
        msg = eligibility_message(conv.language or "hinglish", verdict) or "Account not eligible to post."
        return PushResult(ok=False, error=msg, account_blocked=True, buy_link=verdict.buy_link or "")

    draft = _draft_for(db, conv)
    ensure_card_id(db, draft)
    if verdict.reason == "no_account":
        try:
            from ..infradealer.service import InfraDealerIntegrationService

            svc = InfraDealerIntegrationService(db)
            if svc.is_configured():
                st = svc.get_or_create_account_state(conv.mobile, conversation_id=conv.id)
                if st and not st.pending_draft_id:
                    st.pending_draft_id = draft.id
        except Exception:
            log.exception("pending draft stash failed")

    photo = photos_status(db, draft.id)
    if photo["need_more"]:
        return PushResult(
            ok=False,
            error=f"Need at least {photo['min']} photos for {draft.card_id}. Now {photo['count']}.",
            need_photos=True,
            draft_id=draft.id,
            card_id=draft.card_id or "",
            extra={"photo_count": photo["count"]},
        )

    if draft.status in HUMAN_ONLY_STATUS:
        return PushResult(ok=False, error="Already posted by admin", draft_id=draft.id, card_id=draft.card_id or "")

    try:
        from ..infradealer.service import InfraDealerIntegrationService

        svc = InfraDealerIntegrationService(db)
        if svc.is_configured() and svc.listing_already_pushed(conv, draft, payload):
            url = str(payload.get("listing_url") or "")
            lid = str(payload.get("infradealer_listing_id") or "")
            if lid and not url:
                url = listing_open_url(lid, mobile=conv.mobile)
            return PushResult(
                ok=True,
                already_pushed=True,
                draft_id=draft.id,
                card_id=draft.card_id or "",
                status=draft.status or payload.get("listing_status") or "PUSHED",
                listing_id=lid,
                listing_url=url,
            )
    except Exception:
        log.exception("listing duplicate check failed")

    draft.intent = conv.intent
    draft.user_id = conv.profile_id
    draft.title = listing_title(payload)
    if payload.get("confirmed_json"):
        draft.customer_json = json.dumps(payload.get("confirmed_json"), ensure_ascii=False)
        draft.confirmed_json = draft.customer_json
    draft.status = "CONFIRMED"
    payload["listing_status"] = "CONFIRMED"
    payload["data_status"] = "COMPLETE"
    payload["active_card_id"] = draft.card_id
    payload["push_stage"] = "ADMIN_QUEUE"
    conv.state = "CONFIRMED"
    _write_payload(conv, payload)

    ids = [int(x) for x in (payload.get("media_ids") or []) if isinstance(x, int) or str(x).isdigit()]
    if ids:
        ids = ids[:5]
        db.query(AiMedia).filter(AiMedia.id.in_(ids)).update({"draft_id": draft.id}, synchronize_session=False)

    _log(
        db,
        conv,
        "tool",
        {"tool": "data_push", "status": draft.status, "draft_id": draft.id, "card_id": draft.card_id},
    )

    listing_id = ""
    listing_url = ""
    try:
        from ..infradealer.service import InfraDealerIntegrationService

        svc = InfraDealerIntegrationService(db)
        if svc.is_configured():
            item = svc.push_listing_for_draft(conv, draft, payload)
            if item and item.status in {"PENDING", "RETRY"}:
                svc.process_outbox_item(item)
            payload = _payload(conv)
            listing_id = str(payload.get("infradealer_listing_id") or "")
            listing_url = str(payload.get("listing_url") or "")
            if listing_id and not listing_url:
                listing_url = listing_open_url(listing_id, mobile=conv.mobile, payload=payload)
                payload["listing_url"] = listing_url
                _write_payload(conv, payload)
            if (draft.status or "").upper() in {"POSTED", "PENDING_REVIEW", "PUSHED_TO_INFRADEALER"}:
                pass
            elif item and item.status == "DONE":
                draft.status = draft.status or "PENDING_REVIEW"
                payload["listing_status"] = payload.get("listing_status") or "PENDING_REVIEW"
                payload["push_stage"] = "AWAITING_ADMIN"
                _write_payload(conv, payload)
    except Exception:
        log.exception("listing.push failed")

    return PushResult(
        ok=True,
        draft_id=draft.id,
        card_id=draft.card_id or "",
        status=draft.status,
        listing_id=listing_id,
        listing_url=listing_url,
        gaps=[],
    )


def apply_admin_decision(
    db: Session,
    conv: AiConversation,
    *,
    approved: bool,
    listing_id: str = "",
    payload: dict | None = None,
    reason: str = "",
    draft: AiListingDraft | None = None,
) -> str:
    """After admin approve/reject callback: update memory + return WhatsApp AI message body."""
    from ..infradealer.events import listing_reject_reason

    pl = _payload(conv)
    if approved and pl.get("listing_review_notified") and str(pl.get("listing_status") or "").upper() == "POSTED":
        return ""
    if (not approved) and pl.get("listing_review_notified") and str(pl.get("listing_status") or "").upper() == "REJECTED":
        return ""

    lid = listing_id_from_payload(payload, listing_id) or str(pl.get("infradealer_listing_id") or "")
    lang = conv.language or "hinglish"
    card = (draft.card_id if draft else None) or pl.get("active_card_id") or ""

    if approved:
        url = listing_open_url(lid, mobile=conv.mobile, payload=payload)
        pl["listing_status"] = "POSTED"
        pl["listing_review_notified"] = True
        pl["push_stage"] = "LIVE"
        if lid:
            pl["infradealer_listing_id"] = lid
        if url:
            pl["listing_url"] = url
        _write_payload(conv, pl)
        if draft:
            draft.status = "POSTED"
            try:
                from .cards import schedule_card_cleanup

                schedule_card_cleanup(draft)
            except Exception:
                pass
        return approve_message(lang, url=url, card=card or "")

    reject_reason = reason or listing_reject_reason(payload)
    pl["listing_status"] = "REJECTED"
    pl["listing_review_notified"] = True
    pl["push_stage"] = "REJECTED"
    if lid:
        pl["infradealer_listing_id"] = lid
    if reject_reason:
        pl["rejection_reason"] = reject_reason
    _write_payload(conv, pl)
    if draft:
        draft.status = "REJECTED"
        try:
            from .cards import schedule_card_cleanup

            schedule_card_cleanup(draft)
        except Exception:
            pass
    return reject_message(lang, reason=reject_reason, card=card or "")
