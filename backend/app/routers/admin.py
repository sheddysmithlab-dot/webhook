from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    BlockedNumber,
    Broadcast,
    BroadcastRecipient,
    Chat,
    Message,
    Otp,
    Product,
    Submission,
    User,
)
from ..services import get_or_create_settings, normalize_mobile, settings_public, valid_mobile

router = APIRouter(prefix="/api/admin")


def require_admin(x_admin_token: str | None = Header(default=None)):
    from ..config import settings
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(401, "Admin token galat hai.")


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return {
        "messages": db.query(Message).count(),
        "chats": db.query(Chat).count(),
        "submissions": db.query(Submission).filter(Submission.status != "deleted").count(),
        "otps": db.query(Otp).count(),
        "products": db.query(Product).filter(Product.status != "deleted").count(),
        "users": db.query(User).count(),
        "blocked": db.query(BlockedNumber).count(),
        "broadcasts": db.query(Broadcast).count(),
    }


@router.get("/messages")
def messages(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Message).order_by(Message.id.desc()).all()
    return [
        {
            "id": m.id,
            "from": m.from_mobile,
            "ref": m.ref,
            "text": m.text,
            "parsed": f"{m.product_name} · Rs {m.price or '—'} · {m.city or '—'}",
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


@router.get("/submissions")
def submissions(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Submission).order_by(Submission.id.desc()).all()
    return [
        {
            "id": s.id,
            "ref": s.ref,
            "name": s.name,
            "mobile": s.mobile,
            "title": s.title,
            "consent": s.consent,
            "status": s.status,
            "dup_flag": s.dup_flag,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in rows
    ]


@router.delete("/submissions/{sub_id}")
def delete_submission(sub_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    s = db.query(Submission).filter(Submission.id == sub_id).first()
    if not s:
        raise HTTPException(404, "Submission nahi mili")
    s.status = "deleted"
    db.commit()
    return {"ok": True}


@router.get("/otps")
def otps(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Otp).order_by(Otp.id.desc()).all()
    return [
        {
            "id": o.id,
            "mobile": o.mobile,
            "status": o.status,
            "attempts": o.attempts,
            "max_attempts": o.max_attempts,
            "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]


@router.get("/products")
def products(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Product).filter(Product.status != "deleted").order_by(Product.id.desc()).all()
    return [
        {
            "id": p.id,
            "ref": p.ref,
            "title": p.title,
            "price": p.price,
            "seller_name": p.seller_name,
            "mobile": p.mobile,
            "status": p.status,
            "spam_flags": p.spam_flags,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]


@router.post("/products/{product_id}/toggle")
def toggle_product(product_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product nahi mila")
    p.status = "draft" if p.status == "published" else "published"
    db.commit()
    return {"ok": True, "status": p.status}


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        raise HTTPException(404, "Product nahi mila")
    p.status = "deleted"
    db.commit()
    return {"ok": True}


@router.get("/users")
def users(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(User).order_by(User.id.desc()).all()
    out = []
    for u in rows:
        cards = db.query(Product).filter(Product.user_id == u.id, Product.status != "deleted").count()
        out.append({
            "id": u.id,
            "name": u.name,
            "mobile": u.mobile,
            "source": u.source,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "cards": cards,
        })
    return out


@router.get("/blocked")
def blocked(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(BlockedNumber).order_by(BlockedNumber.id.desc()).all()
    return [{"id": b.id, "mobile": b.mobile} for b in rows]


class BlockIn(BaseModel):
    mobile: str


@router.post("/blocked")
def add_block(body: BlockIn, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    mobile = normalize_mobile(body.mobile)
    if not valid_mobile(mobile):
        raise HTTPException(400, "Valid 10-digit number daalein.")
    if db.query(BlockedNumber).filter(BlockedNumber.mobile == mobile).first():
        return {"ok": True}
    db.add(BlockedNumber(mobile=mobile))
    for p in db.query(Product).filter(Product.mobile == mobile, Product.status == "published").all():
        p.status = "blocked"
    db.commit()
    return {"ok": True}


@router.delete("/blocked/{mobile}")
def unblock(mobile: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    mobile = normalize_mobile(mobile)
    row = db.query(BlockedNumber).filter(BlockedNumber.mobile == mobile).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@router.get("/chats")
def chats(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Chat).order_by(Chat.timestamp_ms.desc(), Chat.id.desc()).all()
    return [_chat_out(c) for c in rows]


@router.get("/broadcasts")
def broadcasts(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = db.query(Broadcast).order_by(Broadcast.id.desc()).all()
    out = []
    for b in rows:
        recs = db.query(BroadcastRecipient).filter(BroadcastRecipient.broadcast_id == b.id).all()
        out.append({
            "id": b.id,
            "message": b.message,
            "recipients": [r.to_mobile for r in recs],
            "delivered": b.delivered,
            "total": b.total,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        })
    return out


def _chat_out(c: Chat) -> dict:
    return {
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
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return settings_public(get_or_create_settings(db))


class SettingsIn(BaseModel):
    app_secret: str = ""
    app_id: str = ""
    waba_id: str = ""
    phone_number_id: str = ""
    system_user_token: str = ""
    graph_version: str = "v23.0"
    test_recipient: str = ""
    field_messages: bool = True
    field_template_status: bool = True
    field_account_alerts: bool = False


@router.put("/settings")
def save_settings(body: SettingsIn, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    row = get_or_create_settings(db)
    if body.test_recipient and not valid_mobile(body.test_recipient):
        raise HTTPException(400, "Valid test recipient chahiye (6-9 se shuru 10-digit).")
    row.app_secret = body.app_secret.strip()
    row.app_id = body.app_id.strip()
    row.waba_id = body.waba_id.strip()
    row.phone_number_id = body.phone_number_id.strip()
    row.system_user_token = body.system_user_token.strip()
    row.graph_version = body.graph_version or "v23.0"
    row.test_recipient = normalize_mobile(body.test_recipient) if body.test_recipient else ""
    row.field_messages = body.field_messages
    row.field_template_status = body.field_template_status
    row.field_account_alerts = body.field_account_alerts
    db.commit()
    db.refresh(row)
    return settings_public(row)


@router.post("/settings/regenerate-token")
def regen(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    from ..services import gen_verify_token
    row = get_or_create_settings(db)
    row.verify_token = gen_verify_token()
    db.commit()
    return settings_public(row)


@router.get("/export/{kind}")
def export_csv(kind: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    from fastapi.responses import PlainTextResponse

    def guard(s):
        t = "" if s is None else str(s)
        if t[:1] in "=+-@":
            t = "'" + t
        if any(ch in t for ch in '",\n'):
            t = '"' + t.replace('"', '""') + '"'
        return t

    rows = []
    if kind == "msgs":
        rows = [["Time", "From", "Ref", "Message", "Parsed"]] + [
            [m.created_at, m.from_mobile, m.ref, m.text, f"{m.product_name}|{m.price}|{m.city}"]
            for m in db.query(Message).all()
        ]
    elif kind == "chats":
        rows = [["Time", "Conversation", "From", "Direction", "Message", "Status"]] + [
            [c.created_at, c.conversation_id, c.from_mobile, c.direction, c.body, c.status]
            for c in db.query(Chat).all()
        ]
    elif kind == "subs":
        rows = [["Time", "Ref", "Name", "Mobile", "Title", "Status"]] + [
            [s.created_at, s.ref, s.name, s.mobile, s.title, s.status]
            for s in db.query(Submission).all()
        ]
    elif kind == "otps":
        rows = [["Time", "Mobile", "Status", "Attempts"]] + [
            [o.created_at, o.mobile, o.status, f"{o.attempts}/{o.max_attempts}"]
            for o in db.query(Otp).all()
        ]
    elif kind == "prods":
        rows = [["Time", "Ref", "Title", "Price", "Seller", "Mobile", "Status"]] + [
            [p.created_at, p.ref, p.title, p.price, p.seller_name, p.mobile, p.status]
            for p in db.query(Product).all()
        ]
    elif kind == "users":
        rows = [["User", "Mobile", "Source"]] + [[u.name, u.mobile, u.source] for u in db.query(User).all()]
    elif kind == "blocked":
        rows = [["Number"]] + [[b.mobile] for b in db.query(BlockedNumber).all()]
    elif kind == "bc":
        rows = [["Time", "Message", "Delivered", "Total"]] + [
            [b.created_at, b.message, b.delivered, b.total] for b in db.query(Broadcast).all()
        ]
    else:
        raise HTTPException(400, "Unknown export")
    csv = "\n".join(",".join(guard(c) for c in r) for r in rows)
    return PlainTextResponse("\ufeff" + csv, media_type="text/csv")
