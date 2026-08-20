from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pathlib import Path
import json

from ..ai.runner import conversation_public
from ..ai.schema import loads
from ..auth import current_user
from ..config import settings
from ..database import get_db
from ..contacts import list_contacts, sync_contacts_from_chats
from ..models import (
    AiConversation,
    AiEvent,
    AiListingDraft,
    AiMedia,
    BlockedNumber,
    Broadcast,
    BroadcastRecipient,
    Chat,
    Contact,
    Message,
    Otp,
    Product,
    Submission,
    User,
)
from ..services import (
    get_or_create_settings,
    next_ref,
    normalize_ai_api_base,
    normalize_ai_model,
    normalize_mobile,
    resolve_ai_config,
    settings_public,
    valid_mobile,
    ZAI_API_BASE,
    ZAI_MODEL,
    is_openai_api_base,
    is_zai_api_base,
)
from ..ai.i18n import normalize_policy
from ..identity import listing_category, parse_listing_price, seller_fields, unique_photo_ids

router = APIRouter(prefix="/api/admin")


def require_admin(request: Request):
    if not current_user(request):
        raise HTTPException(401, "Login required")


def _contacts_rows(db: Session) -> list[dict]:
    return list_contacts(db)


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return {
        "messages": db.query(Message).count(),
        "chats": db.query(Chat).count(),
        "contacts": db.query(Contact).count(),
        "submissions": db.query(Submission).filter(Submission.status != "deleted").count(),
        "otps": db.query(Otp).count(),
        "products": db.query(Product).filter(Product.status != "deleted").count(),
        "users": db.query(User).count(),
        "blocked": db.query(BlockedNumber).count(),
        "broadcasts": db.query(Broadcast).count(),
        "ai_drafts": db.query(AiListingDraft).filter(AiListingDraft.status.in_(["CONFIRMED", "READY_FOR_REVIEW", "POSTED", "NEEDS_INFO", "REJECTED"])).count(),
        "ai_pending": db.query(AiListingDraft).filter(AiListingDraft.status.in_(["CONFIRMED", "READY_FOR_REVIEW"])).count(),
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
            "role": (u.role or "user"),
            "source": u.source,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "cards": cards,
        })
    return out


@router.get("/contacts")
def contacts(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    sync_contacts_from_chats(db)
    db.commit()
    return list_contacts(db)


@router.post("/contacts/sync")
def contacts_sync(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    count = sync_contacts_from_chats(db)
    db.commit()
    return {"ok": True, "synced": count, "total": db.query(Contact).count()}


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


class AiSettingsIn(BaseModel):
    ai_enabled: bool = True
    ai_api_key: str = ""
    ai_api_base: str = ZAI_API_BASE
    ai_model: str = ZAI_MODEL
    ai_reply_language: str = "auto"


@router.put("/settings/ai")
def save_ai_settings(body: AiSettingsIn, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    row = get_or_create_settings(db)
    row.ai_enabled = bool(body.ai_enabled)
    incoming_base = (body.ai_api_base or "").strip() or ZAI_API_BASE
    if is_openai_api_base(incoming_base):
        raise HTTPException(
            400,
            f"Only Z.AI is allowed. Use {ZAI_API_BASE} and model {ZAI_MODEL}. OpenAI/Groq/OpenRouter are disabled.",
        )
    row.ai_api_base = normalize_ai_api_base(incoming_base)
    row.ai_model = normalize_ai_model(body.ai_model or ZAI_MODEL, row.ai_api_base)[:80]
    row.ai_reply_language = normalize_policy(body.ai_reply_language)
    incoming = (body.ai_api_key or "").strip()
    if incoming:
        row.ai_api_key = incoming[:1024]
    db.commit()
    db.refresh(row)
    return settings_public(row)


@router.post("/settings/ai/test")
def test_ai_settings(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    cfg = resolve_ai_config(db)
    if cfg.get("config_error") and not cfg.get("api_key"):
        raise HTTPException(400, cfg["config_error"])
    if not cfg["enabled"]:
        raise HTTPException(400, cfg.get("config_error") or "AI setup off hai. Pehle enable karke Z.AI key save karo.")
    if not cfg["api_key"]:
        raise HTTPException(400, "Z.AI API key save nahi hai.")
    if not is_zai_api_base(cfg["api_base"]):
        raise HTTPException(400, f"Only Z.AI allowed. Got base={cfg['api_base']}")
    import httpx
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
                json={
                    "model": cfg["model"],
                    "messages": [{"role": "user", "content": "Reply with OK"}],
                    "max_tokens": 8,
                    "thinking": {"type": "disabled"},
                    "enable_thinking": False,
                },
            )
    except Exception as exc:
        raise HTTPException(400, f"Z.AI API connect nahi hua: {exc}") from exc
    if resp.status_code >= 400:
        data = resp.json() if resp.content else {}
        err = (data.get("error") or {}).get("message") if isinstance(data, dict) else resp.text
        raise HTTPException(400, err or f"Z.AI API HTTP {resp.status_code}")
    return {"ok": True, "provider": "z.ai", "model": cfg["model"], "base": cfg["api_base"]}


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
        rows = [["User", "Mobile", "Category", "Source"]] + [[u.name, u.mobile, getattr(u, "role", None) or "user", u.source] for u in db.query(User).all()]
    elif kind == "contacts":
        crows = _contacts_rows(db)
        rows = [["Name", "Mobile", "City", "Messages", "Last Message"]] + [
            [c["name"], c["mobile"], c["city"], c.get("messages", 0), c["last_at"]] for c in crows
        ]
    elif kind == "blocked":
        rows = [["Number"]] + [[b.mobile] for b in db.query(BlockedNumber).all()]
    elif kind == "bc":
        rows = [["Time", "Message", "Delivered", "Total"]] + [
            [b.created_at, b.message, b.delivered, b.total] for b in db.query(Broadcast).all()
        ]
    elif kind == "ai":
        rows = [["Time", "Mobile", "Intent", "Title", "Status"]] + [
            [d.created_at, d.mobile, d.intent, d.title, d.status]
            for d in db.query(AiListingDraft).all()
        ]
    else:
        raise HTTPException(400, "Unknown export")
    csv = "\n".join(",".join(guard(c) for c in r) for r in rows)
    return PlainTextResponse("\ufeff" + csv, media_type="text/csv")


def _draft_bundle(db: Session, draft: AiListingDraft) -> dict:
    conv = db.query(AiConversation).filter(AiConversation.id == draft.conversation_id).first()
    media = db.query(AiMedia).filter(AiMedia.draft_id == draft.id).order_by(AiMedia.id.asc()).all()
    if conv:
        return conversation_public(conv, media, draft)
    return {"draft_id": draft.id, "status": draft.status}


@router.get("/ai/drafts")
def ai_drafts(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    rows = (
        db.query(AiListingDraft)
        .filter(AiListingDraft.status.in_(["CONFIRMED", "READY_FOR_REVIEW", "POSTED", "NEEDS_INFO", "REJECTED"]))
        .order_by(AiListingDraft.id.desc())
        .all()
    )
    convs = {c.id: c for c in db.query(AiConversation).all()}
    out = []
    for d in rows:
        try:
            confirmed = json.loads(d.confirmed_json or "{}")
        except json.JSONDecodeError:
            confirmed = {}
        if not isinstance(confirmed, dict):
            confirmed = {}
        out.append({
            "id": d.id,
            "mobile": (confirmed.get("phone") or d.mobile),
            "intent": d.intent,
            "title": (confirmed.get("vehicle") or d.title),
            "status": d.status,
            "user_id": d.user_id,
            "posted_product_id": d.posted_product_id,
            "state": (convs.get(d.conversation_id).state if convs.get(d.conversation_id) else ""),
            "name": (confirmed.get("name") or (convs.get(d.conversation_id).customer_name if convs.get(d.conversation_id) else "")),
            "confirmed": confirmed,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        })
    return out


@router.get("/ai/drafts/{draft_id}")
def ai_draft(draft_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "Draft nahi mili")
    data = _draft_bundle(db, draft)
    events = (
        db.query(AiEvent)
        .filter(AiEvent.mobile == draft.mobile)
        .order_by(AiEvent.id.desc())
        .limit(40)
        .all()
    )
    data["events"] = [
        {
            "id": e.id,
            "type": e.event_type,
            "detail": e.detail,
            "wamid": e.wamid,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
    return data


class AiStatusIn(BaseModel):
    status: str
    note: str = ""


@router.post("/ai/drafts/{draft_id}/status")
def ai_draft_status(draft_id: int, body: AiStatusIn, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "Draft nahi mili")
    status = (body.status or "").upper()
    allowed = {"CONFIRMED", "READY_FOR_REVIEW", "NEEDS_INFO", "REJECTED"}
    if status not in allowed:
        raise HTTPException(400, "Ye status AI draft ke liye allowed nahi. Live post /post se hota hai.")
    draft.status = status
    conv = db.query(AiConversation).filter(AiConversation.id == draft.conversation_id).first()
    if conv:
        if status == "REJECTED":
            conv.state = "COMPLETED"
            from ..ai.cards import ensure_card_id, schedule_card_cleanup
            from ..ai.i18n import t
            from ..services import get_or_create_settings, send_whatsapp_text, store_chat, utcnow

            ensure_card_id(db, draft)
            schedule_card_cleanup(draft)
            lang = conv.language or "hinglish"
            reason = (body.note or "").strip()
            if reason:
                text = t(lang, "rejected_card_reason", card=draft.card_id, reason=reason)
            else:
                text = t(lang, "rejected_card", card=draft.card_id)
            text = f"{text}\n\n{t(lang, 'card_cleanup_notice', card=draft.card_id)}"
            try:
                meta = get_or_create_settings(db)
                result = send_whatsapp_text(meta, draft.mobile, text)
                store_chat(
                    db,
                    wamid=result.get("wamid") or f"ai.admin.reject.{draft.id}.{int(utcnow().timestamp())}",
                    conversation_id=conv.conversation_id,
                    from_mobile=meta.phone_number_id or "infradealer",
                    from_name="InfraDealer AI",
                    to_mobile=draft.mobile,
                    direction="outbound",
                    body=text,
                    status="sent",
                    unread=False,
                )
            except Exception:
                pass
        elif status == "NEEDS_INFO":
            conv.state = "DATA_COLLECTING"
        elif status == "READY_FOR_REVIEW":
            conv.state = "READY_FOR_REVIEW"
    db.add(AiEvent(wamid="", mobile=draft.mobile, event_type="admin_status", detail=status))
    db.commit()
    return {"ok": True, "status": draft.status}


@router.post("/ai/drafts/{draft_id}/post")
def ai_draft_post(draft_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """Human-only: create a live marketplace Product from reviewed AI data."""
    draft = db.query(AiListingDraft).filter(AiListingDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "Draft nahi mili")
    if draft.posted_product_id:
        raise HTTPException(400, "Ye draft already post ho chuki hai.")
    conv = db.query(AiConversation).filter(AiConversation.id == draft.conversation_id).first()
    payload = loads(conv.payload_json) if conv else {}
    try:
        snap = json.loads(draft.confirmed_json or "{}")
    except json.JSONDecodeError:
        snap = {}
    if not isinstance(snap, dict) or not snap:
        snap = payload.get("confirmed_json") if isinstance(payload.get("confirmed_json"), dict) else {}
    if not snap:
        raise HTTPException(400, "Customer ne Haan/Yes confirm nahi kiya. Final JSON nahi mili.")
    customer = json.loads(draft.customer_json or "{}") if draft.customer_json else {}
    photos = [int(x) for x in (snap.get("photos") or []) if isinstance(x, int) or str(x).isdigit()]
    if not photos:
        photos = [
            m.id
            for m in db.query(AiMedia)
            .filter(AiMedia.draft_id == draft.id, AiMedia.kind == "image")
            .order_by(AiMedia.id.asc())
            .all()
            if m.local_path
        ]
    title = snap.get("vehicle") or draft.title or payload.get("brand") or "InfraDealer listing"
    raw_price = str(snap.get("rate") or payload.get("expected_price") or customer.get("rate") or "0")
    price = parse_listing_price(raw_price)
    user = db.query(User).filter(User.id == draft.user_id).first() if draft.user_id else None
    if not user:
        user = db.query(User).filter(User.mobile == draft.mobile).first()
    seller_name = (snap.get("name") or "")[:120] or seller_fields(db, conv, draft, payload)[0]
    seller_mobile = snap.get("phone") or seller_fields(db, conv, draft, payload)[1] or draft.mobile
    prod = Product(
        ref=next_ref(db, "AI-"),
        title=str(title)[:200],
        category=listing_category(payload, str(snap.get("category") or payload.get("category") or "Other"), str(title)),
        price=price,
        condition=str(snap.get("condition") or payload.get("condition") or "Used")[:40],
        city=str(snap.get("location") or payload.get("location") or "")[:80],
        seller_name=seller_name[:120],
        mobile=seller_mobile,
        user_id=user.id if user else None,
        consent=True,
        status="published",
        description=str(payload.get("description") or "")[:4000],
        photo_ids=json.dumps([int(x) for x in photos if str(x).isdigit() or isinstance(x, int)]),
    )
    db.add(prod)
    db.flush()
    draft.status = "POSTED"
    draft.posted_product_id = prod.id
    if conv and conv.draft_id == draft.id:
        conv.state = "COMPLETED"
    from ..ai.cards import ensure_card_id, schedule_card_cleanup
    from ..ai.i18n import t
    from ..services import get_or_create_settings, send_whatsapp_text, store_chat, utcnow

    ensure_card_id(db, draft)
    schedule_card_cleanup(draft)
    if conv:
        lang = conv.language or "hinglish"
        # Local marketplace product — no InfraDealer public URL guaranteed
        text = t(lang, "posted_card_nolink", card=draft.card_id)
        text = f"{text}\n\n{t(lang, 'card_cleanup_notice', card=draft.card_id)}"
        try:
            meta = get_or_create_settings(db)
            result = send_whatsapp_text(meta, draft.mobile, text)
            store_chat(
                db,
                wamid=result.get("wamid") or f"ai.admin.post.{draft.id}.{int(utcnow().timestamp())}",
                conversation_id=conv.conversation_id,
                from_mobile=meta.phone_number_id or "infradealer",
                from_name="InfraDealer AI",
                to_mobile=draft.mobile,
                direction="outbound",
                body=text,
                status="sent",
                unread=False,
            )
        except Exception:
            pass
    db.add(AiEvent(wamid="", mobile=draft.mobile, event_type="admin_post", detail=str(prod.id)))
    db.commit()
    return {"ok": True, "product_id": prod.id, "ref": prod.ref, "status": "POSTED"}


@router.get("/ai/media/{media_id}")
def ai_media(media_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):
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

