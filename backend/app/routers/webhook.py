import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import BlockedNumber, Broadcast, BroadcastRecipient, Chat, Message
from ..parser import parse_message
from ..services import (
    get_or_create_settings,
    next_ref,
    normalize_mobile,
    send_whatsapp_text,
    store_chat,
    utcnow,
    valid_mobile,
    verify_meta_signature,
)

router = APIRouter()


def ingest_inbound(db, from_mobile: str, body: str, wamid: str = "", name: str = "", to_mobile: str = "", ts_ms: int = 0):
    from_mobile = normalize_mobile(from_mobile)
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
    return msg


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
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
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
    for entry in entries:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
            phone = (value.get("metadata") or {}).get("display_phone_number") or meta.phone_number_id
            for msg in value.get("messages") or []:
                body = ((msg.get("text") or {}).get("body")) or ""
                if not body:
                    continue
                frm = msg.get("from") or ""
                ingest_inbound(
                    db,
                    from_mobile=frm,
                    body=body,
                    wamid=msg.get("id") or "",
                    name=contacts.get(frm, ""),
                    to_mobile=normalize_mobile(phone),
                    ts_ms=int(msg.get("timestamp") or 0) * 1000,
                )
                stored += 1
            for st in value.get("statuses") or []:
                wamid = st.get("id") or ""
                status = st.get("status") or ""
                if wamid and status:
                    chat = db.query(Chat).filter(Chat.wamid == wamid).first()
                    if chat:
                        chat.status = status
    db.commit()
    return {"ok": True, "stored": stored}


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
    return [
        {
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
        }
        for c in rows
    ]


@router.post("/api/chats/{chat_id}/read")
def mark_read(chat_id: int, db: Session = Depends(get_db)):
    c = db.query(Chat).filter(Chat.id == chat_id).first()
    if not c:
        raise HTTPException(404, "Chat nahi mili")
    c.unread = False
    db.commit()
    return {"ok": True}


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
    msg = ingest_inbound(db, body.from_mobile, body.text.strip(), name=body.name)
    db.commit()
    return {"ok": True, "ref": msg.ref, "id": msg.id}
