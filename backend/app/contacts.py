"""WhatsApp contacts saved from inbound chat + Meta webhook profile name."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Chat, Contact
from .parser import parse_message
from .services import normalize_mobile, utcnow, valid_mobile


def _ts_from_chat(chat: Chat) -> datetime:
    if chat.timestamp_ms:
        try:
            return datetime.fromtimestamp(chat.timestamp_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError):
            pass
    return chat.created_at or utcnow()


def upsert_chat_contact(
    db: Session,
    mobile: str,
    *,
    name: str = "",
    city: str = "",
    wa_id: str = "",
    at: datetime | None = None,
) -> Contact | None:
    """Save/update one contact from an inbound WhatsApp message."""
    mobile = normalize_mobile(mobile)
    if not valid_mobile(mobile):
        return None
    clean_name = (name or "").strip()[:120]
    clean_city = (city or "").strip()[:80]
    when = at or utcnow()
    row = db.query(Contact).filter(Contact.mobile == mobile).first()
    if not row:
        row = Contact(mobile=mobile, message_count=0)
        db.add(row)
    if clean_name:
        row.name = clean_name
    if clean_city:
        row.city = clean_city
    if wa_id:
        row.wa_id = normalize_mobile(wa_id) or wa_id[:20]
    row.message_count = int(row.message_count or 0) + 1
    if not row.last_message_at or when >= row.last_message_at:
        row.last_message_at = when
    return row


def sync_contacts_from_chats(db: Session) -> int:
    """Rebuild contacts from inbound WhatsApp chat history (Meta profile names in from_name)."""
    agg: dict[str, dict] = {}
    chats = (
        db.query(Chat)
        .filter(Chat.direction == "inbound", Chat.is_test == False)  # noqa: E712
        .order_by(Chat.timestamp_ms.asc(), Chat.id.asc())
        .all()
    )
    for chat in chats:
        mobile = normalize_mobile(chat.from_mobile)
        if not valid_mobile(mobile):
            continue
        parsed = parse_message(chat.body or "")
        city = (parsed.get("city") or "").strip()[:80]
        when = _ts_from_chat(chat)
        bucket = agg.get(mobile)
        if not bucket:
            bucket = {
                "mobile": mobile,
                "name": "",
                "city": "",
                "wa_id": mobile,
                "message_count": 0,
                "last_message_at": when,
            }
            agg[mobile] = bucket
        bucket["message_count"] += 1
        if chat.from_name and chat.from_name.strip():
            bucket["name"] = chat.from_name.strip()[:120]
        if city:
            bucket["city"] = city
        if when >= bucket["last_message_at"]:
            bucket["last_message_at"] = when

    updated = 0
    for mobile, data in agg.items():
        row = db.query(Contact).filter(Contact.mobile == mobile).first()
        if not row:
            row = Contact(mobile=mobile)
            db.add(row)
        if data["name"]:
            row.name = data["name"]
        if data["city"]:
            row.city = data["city"]
        row.wa_id = data["wa_id"]
        row.message_count = data["message_count"]
        row.last_message_at = data["last_message_at"]
        updated += 1
    return updated


def list_contacts(db: Session) -> list[dict]:
    rows = db.query(Contact).order_by(Contact.last_message_at.desc().nullslast(), Contact.id.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name or "—",
            "mobile": c.mobile,
            "city": c.city or "—",
            "messages": int(c.message_count or 0),
            "last_at": c.last_message_at.isoformat() if c.last_message_at else None,
        }
        for c in rows
    ]
