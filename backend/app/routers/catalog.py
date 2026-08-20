from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from pathlib import Path

from ..config import settings
from ..database import get_db
from ..identity import photo_payload
from ..models import AiMedia, BlockedNumber, Message, Otp, Product, Submission, User
from ..parser import CATEGORIES, CONDITIONS
from datetime import timedelta

from ..infradealer import push_listing as infra_push_listing
from ..services import (
    create_otp,
    deliver_otp,
    get_or_create_settings,
    hash_otp,
    next_ref,
    normalize_mobile,
    utcnow,
    valid_mobile,
)

router = APIRouter(prefix="/api")


class SubmitIn(BaseModel):
    ref: str = ""
    message_id: int | None = None
    title: str
    category: str
    price: float = Field(gt=0)
    condition: str
    city: str
    mobile: str
    name: str
    description: str = ""
    consent: bool = False


class OtpIn(BaseModel):
    submission_id: int
    mobile: str
    code: str = ""


def product_out(p: Product) -> dict:
    return {
        "id": p.id,
        "ref": p.ref,
        "title": p.title,
        "category": p.category,
        "price": p.price,
        "condition": p.condition,
        "city": p.city,
        "seller_name": p.seller_name,
        "mobile": p.mobile,
        "user_id": p.user_id,
        "consent": p.consent,
        "status": p.status,
        "spam_flags": p.spam_flags,
        "views": p.views,
        "description": p.description,
        "photos": photo_payload(p),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def is_blocked(db: Session, mobile: str) -> bool:
    return db.query(BlockedNumber).filter(BlockedNumber.mobile == mobile).first() is not None


def user_by_mobile(db: Session, mobile: str) -> User | None:
    return db.query(User).filter(User.mobile == mobile).first()


def publish_card(db: Session, sub: Submission, user: User | None, mode: str) -> Product:
    prod = Product(
        ref=sub.ref,
        title=sub.title,
        category=sub.category,
        price=sub.price,
        condition=sub.condition,
        city=sub.city,
        seller_name=sub.name,
        mobile=sub.mobile,
        user_id=user.id if user else None,
        consent=sub.consent,
        status="published" if sub.consent else "draft",
        spam_flags=1 if sub.dup_flag else 0,
        description=sub.description or "",
    )
    db.add(prod)
    sub.status = "published"
    sub.account_mode = mode
    db.flush()

    # ── Push to InfraDealer for admin review (fire-and-forget, non-blocking) ──
    try:
        infra_push_listing(
            phone=sub.mobile,
            name=sub.name,
            title=sub.title,
            category=sub.category,
            price=float(sub.price),
            condition=sub.condition or "",
            city=sub.city,
            description=sub.description or "",
            ref=sub.ref,
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("infradealer.bridge").error("push_listing exception: %s", exc)

    return prod


@router.get("/meta/options")
def options():
    return {"categories": CATEGORIES, "conditions": CONDITIONS}


@router.get("/products")
def list_products(
    q: str = "",
    category: str = "",
    city: str = "",
    condition: str = "",
    min_price: float | None = None,
    max_price: float | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Product).filter(Product.status == "published")
    if category:
        query = query.filter(Product.category == category)
    if city:
        query = query.filter(Product.city == city)
    if condition:
        query = query.filter(Product.condition == condition)
    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)
    rows = query.order_by(Product.created_at.desc()).all()
    if q:
        needle = q.lower()
        rows = [
            p for p in rows
            if needle in " ".join([p.title, p.category, p.city, p.seller_name, p.mobile]).lower()
        ]
    published = db.query(Product).filter(Product.status == "published").all()
    return {
        "items": [product_out(p) for p in rows],
        "categories": sorted({p.category for p in published if p.category}),
        "cities": sorted({p.city for p in published if p.city}),
    }


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id, Product.status != "deleted").first()
    if not p:
        raise HTTPException(404, "Product nahi mila")
    p.views = (p.views or 0) + 1
    db.commit()
    db.refresh(p)
    return product_out(p)


@router.get("/products/{product_id}/photos/{media_id}")
def product_photo(product_id: int, media_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id, Product.status == "published").first()
    if not p:
        raise HTTPException(404, "Product nahi mila")
    allowed = {item["id"] for item in photo_payload(p)}
    if media_id not in allowed:
        raise HTTPException(404, "Photo nahi mili")
    row = db.query(AiMedia).filter(AiMedia.id == media_id).first()
    if not row or not row.local_path:
        raise HTTPException(404, "Photo nahi mili")
    path = Path(row.local_path).resolve()
    root = Path(settings.ai_media_dir).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(404, "Photo path invalid")
    if not path.is_file():
        raise HTTPException(404, "Photo file missing")
    return FileResponse(path, media_type=row.mime or "image/jpeg")


@router.get("/messages/by-ref/{ref}")
def message_by_ref(ref: str, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.ref == ref).first()
    if not msg:
        raise HTTPException(404, "Ye reference system mein nahi mila.")
    age = utcnow() - (msg.created_at or utcnow())
    if age > timedelta(hours=settings.ref_ttl_hours):
        raise HTTPException(410, "Ye reference expire ho gaya (48h).")
    return {
        "id": msg.id,
        "from": msg.from_mobile,
        "text": msg.text,
        "ref": msg.ref,
        "parsed": {
            "product_name": msg.product_name,
            "category": msg.category,
            "price": msg.price,
            "price_num": msg.price_num,
            "condition": msg.condition,
            "city": msg.city,
            "mobile": msg.parsed_mobile or msg.from_mobile,
        },
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


@router.post("/submissions")
def submit(body: SubmitIn, db: Session = Depends(get_db)):
    mobile = normalize_mobile(body.mobile)
    if not valid_mobile(mobile):
        raise HTTPException(400, "Valid 10-digit mobile (6-9 se shuru) chahiye.")
    if not body.consent:
        raise HTTPException(400, "Public card consent zaroori hai.")
    if is_blocked(db, mobile):
        raise HTTPException(403, "Ye number block hai. Support se contact karein.")

    ref = body.ref.strip() or next_ref(db, "W-")
    if body.ref.strip():
        existing = db.query(Submission).filter(Submission.ref == ref, Submission.status != "deleted").first()
        if existing:
            raise HTTPException(400, "Ye reference already use ho chuka hai. Naya WhatsApp message bhejein.")

    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.query(Submission).filter(
        Submission.mobile == mobile,
        Submission.status != "deleted",
        Submission.created_at >= start,
    ).count()
    if today_count >= 3:
        raise HTTPException(429, "Aapne aaj 3+ submissions kar diye hain. Kal try karein (spam limit).")

    week = utcnow() - timedelta(days=7)
    title_l = body.title.lower().strip()
    dup = False
    for p in db.query(Product).filter(Product.mobile == mobile, Product.created_at >= week).all():
        if p.title.lower().strip() == title_l and p.price == body.price and p.city == body.city:
            dup = True
            break

    sub = Submission(
        ref=ref,
        message_id=body.message_id,
        name=body.name.strip(),
        mobile=mobile,
        title=body.title.strip(),
        category=body.category,
        price=body.price,
        condition=body.condition,
        city=body.city.strip(),
        description=body.description.strip(),
        consent=True,
        status="pending_account",
        dup_flag=dup,
    )
    db.add(sub)
    db.flush()

    user = user_by_mobile(db, mobile)
    if user:
        prod = publish_card(db, sub, user, "linked")
        db.commit()
        return {"ok": True, "need_otp": False, "account_mode": "linked", "product": product_out(prod)}

    sub.status = "otp_pending"
    meta = get_or_create_settings(db)
    try:
        otp = create_otp(db, mobile)
        channel = deliver_otp(meta, mobile, otp._plain_code)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(429, str(exc)) from exc
    db.commit()
    return {
        "ok": True,
        "need_otp": True,
        "submission_id": sub.id,
        "mobile": mobile,
        "otp_channel": channel,
        "message": (
            "OTP WhatsApp par bheja gaya."
            if channel == "whatsapp"
            else "OTP generate ho gaya. Meta token nahi hai, isliye code backend terminal log mein hai."
        ),
    }


@router.post("/otp/verify")
def verify_otp(body: OtpIn, db: Session = Depends(get_db)):
    mobile = normalize_mobile(body.mobile)
    otp = db.query(Otp).filter(Otp.mobile == mobile, Otp.status == "sent").order_by(Otp.id.desc()).first()
    if not otp:
        raise HTTPException(400, "OTP nahi mila. Resend karein.")
    if otp.attempts >= otp.max_attempts:
        otp.status = "blocked"
        db.commit()
        raise HTTPException(403, "3 attempts khatam — number temporarily block.")
    if utcnow() > otp.expires_at:
        otp.status = "expired"
        db.commit()
        raise HTTPException(400, "OTP expire ho gaya. Resend dabayen.")
    otp.attempts += 1
    if otp.code_hash != hash_otp(str(body.code).strip()):
        db.commit()
        left = otp.max_attempts - otp.attempts
        raise HTTPException(400, f"Galat OTP. Attempts left: {left}")
    otp.status = "verified"

    sub = db.query(Submission).filter(Submission.id == body.submission_id).first()
    if not sub:
        raise HTTPException(404, "Submission nahi mili.")
    user = user_by_mobile(db, mobile)
    mode = "linked"
    if not user:
        user = User(name=sub.name, mobile=mobile, source="whatsapp_otp")
        db.add(user)
        db.flush()
        mode = "created"
    prod = publish_card(db, sub, user, mode)
    db.commit()
    return {"ok": True, "account_mode": mode, "product": product_out(prod)}


@router.post("/otp/resend")
def resend_otp(body: OtpIn, db: Session = Depends(get_db)):
    mobile = normalize_mobile(body.mobile)
    meta = get_or_create_settings(db)
    try:
        otp = create_otp(db, mobile)
        channel = deliver_otp(meta, mobile, otp._plain_code)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc)) from exc
    db.commit()
    return {"ok": True, "otp_channel": channel}
