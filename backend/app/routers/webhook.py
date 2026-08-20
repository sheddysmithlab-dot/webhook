import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..ai.runner import parse_meta_message, process_inbound
from ..config import settings
from ..database import SessionLocal, get_db
from ..contacts import upsert_chat_contact
from ..models import AiMedia, BlockedNumber, Broadcast, BroadcastRecipient, Chat, Message
from ..parser import parse_message
from ..infradealer import push_listing as infra_push_listing
from ..services import (
    get_or_create_settings,
    meta_ts_ms,
    next_ref,
    normalize_mobile,
    resolve_ai_config,
    send_whatsapp_delete,
    send_whatsapp_text,
    store_chat,
    utcnow,
    valid_mobile,
    verify_meta_signature,
)

router = APIRouter()
log = logging.getLogger("infradealer")


def ingest_inbound(db, from_mobile: str, body: str, wamid: str = "", name: str = "", to_mobile: str = "", ts_ms: int = 0):
    from_mobile = normalize_mobile(from_mobile)
    wamid = wamid or ""
    existing_msg = db.query(Message).filter(Message.wamid == wamid).first() if wamid else None
    if existing_msg:
        return existing_msg, False
    blocked = db.query(BlockedNumber).filter(BlockedNumber.mobile == from_mobile).first() is not None
    parsed = parse_message(body)
    ref = None if blocked else next_ref(db, "ID-")
    wamid = wamid or f"local.{ref or from_mobile}.{int(utcnow().timestamp())}"
    msg = Message(
        wamid=wamid,
        from_mobile=from_mobile,
        text=body,
        ref=ref,
        product_name=parsed["product_name"],
        category=parsed["category"],
        price=str(parsed["price"] or ""),
        price_num=parsed["price_num"],
        condition=parsed["condition"],
        city=parsed["city"],
        parsed_mobile=parsed["mobile"] or from_mobile,
        status="blocked" if blocked else "received",
        direction="inbound",
        source="webhook",
    )
    db.add(msg)
    conv = f"CONV_{from_mobile}"
    existing = None
    if wamid:
        existing = db.query(Chat).filter(Chat.wamid == wamid, Chat.direction == "inbound").first()
    if not existing:
        store_chat(
            db,
            wamid=wamid,
            conversation_id=conv,
            from_mobile=from_mobile,
            from_name=name or "",
            to_mobile=to_mobile,
            direction="inbound",
            body=body,
            status="delivered",
            unread=True,
            timestamp_ms=ts_ms or int(utcnow().timestamp() * 1000),
        )
        upsert_chat_contact(
            db,
            from_mobile,
            name=name,
            city=(parsed.get("city") or "").strip(),
            wa_id=from_mobile,
            at=datetime.fromtimestamp(ts_ms / 1000) if ts_ms else utcnow(),
        )
    return msg, True


def run_ai_job(mobile: str, text: str, wamid: str, name: str, media: dict | None):
    db = SessionLocal()
    try:
        process_inbound(db, mobile=mobile, text=text, wamid=wamid, name=name, media=media, send=True)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("ai inbound job failed")
    finally:
        db.close()


@router.get("/webhook/whatsapp")
def verify_webhook(
    db: Session = Depends(get_db),
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
):
    meta = get_or_create_settings(db)
    if mode != "subscribe" or not meta.verify_token or token != meta.verify_token:
        raise HTTPException(403, "Verify token mismatch")
    return Response(content=challenge, media_type="text/plain")


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    raw = await request.body()
    meta = get_or_create_settings(db)
    sig = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(meta.app_secret, raw, sig):
        raise HTTPException(403, "Invalid signature")
    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    meta.last_delivery = utcnow()
    entries = payload.get("entry") or []
    stored = 0
    ai_jobs = []
    for entry in entries:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            phone = (value.get("metadata") or {}).get("display_phone_number") or meta.phone_number_id
            for msg in value.get("messages") or []:
                parsed_in = parse_meta_message(msg)
                body = parsed_in["text"]
                if not body and not parsed_in["media"]:
                    continue
                frm = msg.get("from") or ""
                is_new = not (msg.get("id") and db.query(Chat).filter(Chat.wamid == msg.get("id"), Chat.direction == "inbound").first())
                name = contacts.get(frm) or contacts.get(normalize_mobile(frm), "")
                row, created = ingest_inbound(
                    db,
                    from_mobile=frm,
                    body=body or "[media]",
                    wamid=msg.get("id") or "",
                    name=name,
                    to_mobile=normalize_mobile(phone),
                    ts_ms=meta_ts_ms(msg.get("timestamp")),
                )
                stored += 1
                if created and is_new:
                    mobile = normalize_mobile(frm)
                    if resolve_ai_config(db)["enabled"]:
                        ai_jobs.append({
                            "mobile": mobile,
                            "text": body or "[media]",
                            "wamid": msg.get("id") or "",
                            "name": name,
                            "media": parsed_in["media"],
                        })
                    else:
                        try:
                            ack = f"InfraDealer: message mil gaya ({row.ref or 'saved'})."
                            send_whatsapp_text(meta, frm, ack)
                        except Exception as exc:
                            log.warning("auto-ack failed for %s: %s", frm, exc)
            for st in value.get("statuses") or []:
                wamid = st.get("id") or ""
                status = st.get("status") or ""
                if wamid and status:
                    chat = db.query(Chat).filter(Chat.wamid == wamid).first()
                    if chat:
                        chat.status = status
    db.commit()
    for job in ai_jobs:
        background.add_task(run_ai_job, **job)
    return {"ok": True, "stored": stored, "ai_queued": len(ai_jobs)}


@router.get("/api/chats")
def list_chats(
    q: str = "",
    unread: bool = False,
    from_number: str = "",
    db: Session = Depends(get_db),
):
    rows = db.query(Chat).order_by(Chat.timestamp_ms.desc(), Chat.id.desc()).all()
    if unread:
        rows = [c for c in rows if c.unread]
    if from_number:
        needle = normalize_mobile(from_number)
        rows = [c for c in rows if normalize_mobile(c.from_mobile) == needle]
    if q:
        needle = q.lower()
        rows = [c for c in rows if needle in f"{c.from_name} {c.from_mobile} {c.body}".lower()]
    media_ids = {c.media_id for c in rows if c.media_id}
    wamids = [c.wamid for c in rows if c.wamid and not c.media_id]
    by_id: dict[int, AiMedia] = {}
    by_wamid: dict[str, AiMedia] = {}
    if media_ids:
        for row in db.query(AiMedia).filter(AiMedia.id.in_(media_ids)).all():
            by_id[row.id] = row
    if wamids:
        for row in db.query(AiMedia).filter(AiMedia.wamid.in_(wamids)).all():
            if row.wamid and row.wamid not in by_wamid:
                by_wamid[row.wamid] = row
    out = []
    for c in rows:
        media = by_id.get(c.media_id) if c.media_id else by_wamid.get(c.wamid or "")
        item = {
            "id": c.id,
            "wamid": c.wamid,
            "conversation_id": c.conversation_id,
            "from": c.from_mobile,
            "from_name": c.from_name,
            "to": c.to_mobile,
            "direction": c.direction,
            "body": c.body,
            "status": c.status,
            "unread": c.unread,
            "is_test": c.is_test,
            "timestamp": c.timestamp_ms,
            "media_id": media.id if media else None,
            "media_kind": media.kind if media else "",
            "media_mime": media.mime if media else "",
            "media_url": f"/api/chats/media/{media.id}" if media and media.local_path else "",
        }
        out.append(item)
    return out


@router.get("/api/chats/media/{media_id}")
def chat_media(media_id: int, db: Session = Depends(get_db)):
    row = db.query(AiMedia).filter(AiMedia.id == media_id).first()
    if not row or not row.local_path:
        raise HTTPException(404, "Media nahi mili")
    path = Path(row.local_path).resolve()
    root = Path(settings.ai_media_dir).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(404, "Media path invalid")
    if not path.is_file():
        raise HTTPException(404, "Media file missing")
    return FileResponse(path, media_type=row.mime or "application/octet-stream")


@router.post("/api/chats/{chat_id}/read")
def mark_read(chat_id: int, db: Session = Depends(get_db)):
    c = db.query(Chat).filter(Chat.id == chat_id).first()
    if not c:
        raise HTTPException(404, "Chat nahi mili")
    c.unread = False
    db.commit()
    return {"ok": True}


@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Message nahi mila")
    linked = []
    if chat.wamid:
        linked = db.query(Message).filter(Message.wamid == chat.wamid).all()
    for row in linked:
        db.delete(row)
    db.delete(chat)
    db.commit()
    return {"ok": True, "deleted_chat_id": chat_id, "deleted_messages": len(linked)}


@router.post("/api/chats/{chat_id}/delete-for-everyone")
def delete_for_everyone(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Message nahi mila")
    if chat.direction != "outbound":
        raise HTTPException(400, "Delete for everyone sirf sent message par chalega.")
    if not (chat.wamid or "").strip():
        raise HTTPException(400, "Is message ka WhatsApp ID missing hai.")
    meta = get_or_create_settings(db)
    try:
        result = send_whatsapp_delete(meta, chat.wamid)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    chat.body = "[deleted for everyone]"
    chat.status = "deleted"
    db.commit()
    return {"ok": True, "deleted_chat_id": chat.id, "result": result}


@router.delete("/api/chats/thread/{mobile}")
def clear_thread(mobile: str, db: Session = Depends(get_db)):
    mobile = normalize_mobile(mobile)
    if not valid_mobile(mobile):
        raise HTTPException(400, "Valid number chahiye.")
    conv_id = f"CONV_{mobile}"
    chats = db.query(Chat).filter(
        or_(
            Chat.conversation_id == conv_id,
            Chat.from_mobile == mobile,
            Chat.to_mobile == mobile,
        )
    ).all()
    msgs = db.query(Message).filter(
        or_(
            Message.from_mobile == mobile,
            Message.parsed_mobile == mobile,
        )
    ).all()
    for row in chats:
        db.delete(row)
    for row in msgs:
        db.delete(row)
    db.commit()
    return {"ok": True, "deleted_chats": len(chats), "deleted_messages": len(msgs)}


@router.post("/api/chats/to-admin")
def chats_to_admin(db: Session = Depends(get_db)):
    chats = db.query(Chat).filter(Chat.direction == "inbound", Chat.is_test == False).all()  # noqa: E712
    known = {(m.wamid, m.direction) for m in db.query(Message).all()}
    pushed = 0
    for c in chats:
        c.sent_to_admin = True
        key = (c.wamid, "inbound")
        if c.wamid and key in known:
            continue
        parsed = parse_message(c.body)
        db.add(Message(
            wamid=c.wamid,
            from_mobile=c.from_mobile,
            text=c.body,
            ref=next_ref(db, "W-"),
            product_name=parsed["product_name"],
            category=parsed["category"],
            price=str(parsed["price"] or ""),
            price_num=parsed["price_num"],
            condition=parsed["condition"],
            city=parsed["city"],
            parsed_mobile=parsed["mobile"] or normalize_mobile(c.from_mobile),
            status="received",
            direction="inbound",
            source="meta_chat",
        ))
        pushed += 1
        known.add(key)
    db.commit()
    return {"pushed": pushed, "total": len(chats)}


class SendChatIn(BaseModel):
    to: str
    text: str


@router.post("/api/chats/send")
def send_chat(body: SendChatIn, db: Session = Depends(get_db)):
    to = normalize_mobile(body.to)
    text = (body.text or "").strip()
    if not valid_mobile(to):
        raise HTTPException(400, "Valid number chahiye.")
    if not text:
        raise HTTPException(400, "Message khaali hai.")
    meta = get_or_create_settings(db)
    try:
        result = send_whatsapp_text(meta, to, text)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    store_chat(
        db,
        wamid=result["wamid"],
        conversation_id=f"CONV_{to}",
        from_mobile=meta.phone_number_id or "infradealer",
        from_name="InfraDealer",
        to_mobile=to,
        direction="outbound",
        body=text,
        status="sent",
        unread=False,
    )
    db.commit()
    return {"ok": True, **result}


class TestMessageIn(BaseModel):
    text: str = "Test message — Meta Cloud API se bheja gaya."


@router.post("/api/meta/test-message")
def test_message(body: TestMessageIn, db: Session = Depends(get_db)):
    meta = get_or_create_settings(db)
    if not valid_mobile(meta.test_recipient):
        raise HTTPException(400, "Valid test recipient save karo.")
    try:
        result = send_whatsapp_text(meta, meta.test_recipient, body.text)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    store_chat(
        db,
        wamid=result["wamid"],
        conversation_id=f"CONV_{meta.test_recipient}",
        from_mobile=meta.phone_number_id,
        from_name="infradealer",
        to_mobile=meta.test_recipient,
        direction="outbound",
        body=body.text,
        status="sent",
        unread=False,
        is_test=True,
    )
    meta.last_delivery = utcnow()
    db.commit()
    return {"ok": True, **result}


class SubscribeIn(BaseModel):
    pass


@router.post("/api/meta/subscribe")
def subscribe(db: Session = Depends(get_db)):
    meta = get_or_create_settings(db)
    if not meta.app_id:
        raise HTTPException(400, "App ID set karo pehle.")
    fields = []
    if meta.field_messages:
        fields.append("messages")
    if meta.field_template_status:
        fields.append("message_template_status_update")
    if meta.field_account_alerts:
        fields.append("account_alerts")
    if not fields:
        raise HTTPException(400, "Kam se kam ek webhook field select karo (messages).")
    meta.subscribed = True
    meta.last_delivery = utcnow()
    db.commit()
    return {"ok": True, "fields": fields, "note": "Meta console mein bhi yahi fields subscribe karo."}


class BroadcastIn(BaseModel):
    message: str
    recipients: list[str]


@router.post("/api/broadcast")
def broadcast(body: BroadcastIn, db: Session = Depends(get_db)):
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(400, "Broadcast message khaali hai.")
    recs = []
    seen = set()
    for raw in body.recipients:
        num = normalize_mobile(raw)
        if not valid_mobile(num) or num in seen:
            continue
        seen.add(num)
        recs.append(num)
    if not recs:
        raise HTTPException(400, "Koi valid recipient nahi.")
    meta = get_or_create_settings(db)
    rec = Broadcast(message=text, total=len(recs), delivered=0)
    db.add(rec)
    db.flush()
    delivered = 0
    errors = []
    for to in recs:
        wamid = ""
        status = "sent"
        try:
            result = send_whatsapp_text(meta, to, text)
            wamid = result["wamid"]
            delivered += 1
        except RuntimeError as exc:
            status = "failed"
            errors.append({"to": to, "error": str(exc)})
        db.add(BroadcastRecipient(broadcast_id=rec.id, to_mobile=to, wamid=wamid, status=status))
        store_chat(
            db,
            wamid=wamid or f"bc.{rec.id}.{to}",
            conversation_id=f"CONV_{to}",
            from_mobile=meta.phone_number_id or "infradealer",
            from_name="infradealer",
            to_mobile=to,
            direction="outbound",
            body=text,
            status=status,
            unread=False,
            broadcast_id=rec.id,
        )
    rec.delivered = delivered
    rec.total = len(recs)
    db.commit()
    return {"ok": True, "id": rec.id, "delivered": delivered, "total": len(recs), "errors": errors}


class InboundIn(BaseModel):
    from_mobile: str
    text: str
    name: str = ""


@router.post("/api/chats/inbound")
def manual_inbound(body: InboundIn, db: Session = Depends(get_db)):
    """Local/dev helper: Meta-shaped inbound without waiting for Graph webhook."""
    if not valid_mobile(body.from_mobile):
        raise HTTPException(400, "Valid sender number chahiye.")
    if not (body.text or "").strip():
        raise HTTPException(400, "Message khaali hai.")
    msg, _created = ingest_inbound(db, body.from_mobile, body.text.strip(), name=body.name)
    db.commit()
    if resolve_ai_config(db)["enabled"]:
        run_ai_job(normalize_mobile(body.from_mobile), body.text.strip(), msg.wamid, body.name, None)
    return {"ok": True, "ref": msg.ref, "id": msg.id}


# ── InfraDealer moderation callback ──────────────────────────────────────────
# InfraDealer backend calls this URL when an admin approves or rejects a listing.
# URL: POST /api/v1/integrations/infradealer/callback
# (This must match the callback_url stored in webhook_integrations on InfraDealer)

class InfraDealerCallbackIn(BaseModel):
    event: str = ""          # listing.approved | listing.rejected
    code: str = ""           # LISTING_APPROVED | LISTING_REJECTED | LISTING_ALREADY_EXISTS
    message: str = ""        # Human-readable message to forward to user
    reply_to_user: str = ""  # Preferred field — same as message, for WhatsApp reply
    phone: str | None = None
    listing: dict = {}


@router.post("/api/v1/integrations/infradealer/callback")
async def infradealer_callback(request: Request, db: Session = Depends(get_db)):
    """
    InfraDealer backend se aata hai jab admin kisi listing ko approve ya reject karta hai.
    Yahan hum user ko WhatsApp par reply bhejte hain.
    """
    raw = await request.body()
    try:
        payload: dict = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")

    event = payload.get("event") or payload.get("code") or ""
    phone = (payload.get("phone") or
             (payload.get("listing") or {}).get("seller_contact") or "")
    phone = normalize_mobile(str(phone))
    reply_text = (payload.get("reply_to_user") or
                  payload.get("message") or "").strip()

    import logging
    log = logging.getLogger("infradealer.callback")
    log.info("InfraDealer callback received: event=%s phone=%s", event, phone)

    if not reply_text:
        return {"ok": True, "note": "no_reply_text"}

    if valid_mobile(phone):
        meta = get_or_create_settings(db)
        try:
            result = send_whatsapp_text(meta, phone, reply_text)
            store_chat(
                db,
                wamid=result.get("wamid") or f"infra.cb.{phone}.{int(utcnow().timestamp())}",
                conversation_id=f"CONV_{phone}",
                from_mobile=meta.phone_number_id or "infradealer",
                from_name="InfraDealer",
                to_mobile=phone,
                direction="outbound",
                body=reply_text,
                status="sent",
                unread=False,
            )
            db.commit()
            log.info("WhatsApp reply sent to %s for event=%s", phone, event)
            return {"ok": True, "sent": True, "phone": phone, "event": event}
        except RuntimeError as exc:
            log.error("WhatsApp reply failed for %s: %s", phone, exc)
            return {"ok": False, "error": str(exc), "phone": phone}
    else:
        log.warning("InfraDealer callback: invalid/missing phone '%s', reply not sent.", phone)
        return {"ok": True, "sent": False, "note": "invalid_phone", "event": event}
