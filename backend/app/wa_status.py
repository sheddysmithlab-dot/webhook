"""Meta delivery status webhooks → chats.status (+ failure reason for the admin panel).

Failed sends are stored as "failed:<code>" (fits chats.status String(20)); the full Meta error
goes to the log and ai_events so the reason is never lost.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from .models import AiEvent, Chat
from .services import normalize_mobile

log = logging.getLogger("infradealer")

_RANK = {"sent": 1, "delivered": 2, "read": 3}

_REASONS = {
    "131047": "24 ghante se customer ka koi message nahi aaya — customer pehle reply kare, tab normal message jayega (ya approved template bhejo).",
    "131026": "Customer ke phone par deliver nahi ho sakta — WhatsApp purana version, number WhatsApp par active nahi, ya customer ne business ko block kiya.",
    "131049": "Meta ne healthy-ecosystem limit ki wajah se roka — thodi der baad try karo.",
    "130472": "Customer Meta ke experiment group me hai — is samay message nahi jayega.",
    "131050": "Customer ne InfraDealer ke marketing messages band kiye hain.",
    "131056": "Is number ko bahut jaldi-jaldi messages gaye — thoda ruk kar bhejo.",
    "131048": "Spam rate limit — bahut se messages block/report hue, Meta ne sending limit ki.",
    "131042": "Meta billing/payment issue — WhatsApp Manager me payment method check karo.",
    "131031": "WhatsApp Business account locked/restricted hai — WhatsApp Manager check karo.",
    "131021": "Sender aur receiver same number hai.",
    "131051": "Ye message type support nahi hai.",
    "131053": "Media upload fail hua.",
    "131000": "Meta side unknown error — dobara try karo.",
    "368": "Policy violation ki wajah se account temporarily restricted hai.",
}


def delivery_error_reason(status: str) -> str:
    status = status or ""
    if not status.startswith("failed"):
        return ""
    code = status.partition(":")[2]
    if not code:
        return "Meta ne message deliver nahi kiya."
    return _REASONS.get(code, f"Meta ne message deliver nahi kiya (error {code}).")


def apply_status(db: Session, st: dict) -> None:
    wamid = st.get("id") or ""
    status = st.get("status") or ""
    if not (wamid and status):
        return
    stored = status
    if status == "failed":
        err = (st.get("errors") or [{}])[0] or {}
        code = str(err.get("code") or "")
        details = (err.get("error_data") or {}).get("details") or err.get("message") or ""
        recipient = normalize_mobile(st.get("recipient_id") or "")
        log.warning(
            "wa delivery failed code=%s title=%s details=%s to=***%s wamid=%s",
            code, err.get("title") or "", details, recipient[-4:], wamid,
        )
        db.add(AiEvent(
            wamid=wamid,
            mobile=recipient[:10],
            event_type="wa_delivery_failed",
            detail=json.dumps({"code": code, "title": err.get("title") or "", "details": details}, ensure_ascii=False)[:1000],
        ))
        stored = f"failed:{code}"[:20] if code else "failed"
    chat = db.query(Chat).filter(Chat.wamid == wamid).first()
    if not chat:
        return
    if _RANK.get(status, 0) and _RANK.get(status, 0) < _RANK.get(chat.status or "", 0):
        return
    chat.status = stored
