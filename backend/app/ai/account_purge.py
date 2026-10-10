"""Erase everything the WhatsApp agent stored about one mobile after its InfraDealer account is deleted.

BlockedNumber rows are kept on purpose: deleting an account must not unblock a number.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    AiConversation,
    AiEvent,
    AiListingDraft,
    AiMedia,
    AiOfficeSession,
    BroadcastRecipient,
    Chat,
    Contact,
    InfraDealerAccountState,
    InfraDealerCallback,
    InfraDealerOutbox,
    InfraDealerRequest,
    Message,
    Otp,
    Product,
    Submission,
    User,
)

log = logging.getLogger("infradealer.ai.account_purge")


def _json_mentions(column, phone: str, listing_ids: list[str]):
    """LIKE filters for JSON text that names this phone or one of the deleted listings."""
    clauses = [column.like(f"%{phone}%")]
    for lid in listing_ids:
        clauses += [column.like(f'%"listing_id": "{lid}"%'), column.like(f'%"listing_id":"{lid}"%')]
    return or_(*clauses)


def _remove_file(path: str) -> bool:
    if not path:
        return False
    try:
        root = Path(settings.ai_media_dir).resolve()
        file = Path(path).resolve()
        if root not in file.parents or not file.is_file():
            return False
        file.unlink()
        return True
    except OSError:
        log.warning("media file delete failed: %s", path)
        return False


def purge_mobile(db: Session, mobile: str, listing_ids: list | tuple = ()) -> dict:
    """Delete all conversations, chats, drafts, media and logs for ``mobile`` (last 10 digits)."""
    phone = "".join(ch for ch in str(mobile or "") if ch.isdigit())[-10:]
    if len(phone) != 10:
        return {}
    lids = [str(x) for x in listing_ids if str(x).strip().isdigit()]
    out: dict[str, int] = {}

    def count(label: str, n: int) -> None:
        if n:
            out[label] = out.get(label, 0) + n

    convs = db.query(AiConversation).filter(AiConversation.mobile == phone).all()
    conv_ids = [c.id for c in convs]
    conv_keys = [c.conversation_id for c in convs if c.conversation_id]

    draft_q = db.query(AiListingDraft).filter(
        or_(
            AiListingDraft.mobile == phone,
            AiListingDraft.conversation_id.in_(conv_ids or [0]),
            _json_mentions(AiListingDraft.confirmed_json, phone, lids),
            _json_mentions(AiListingDraft.customer_json, phone, lids),
        )
    )
    draft_ids = [d.id for d in draft_q.all()]

    media = (
        db.query(AiMedia)
        .filter(or_(AiMedia.conversation_id.in_(conv_ids or [0]), AiMedia.draft_id.in_(draft_ids or [0])))
        .all()
    )
    count("media_files", sum(1 for m in media if _remove_file(m.local_path)))
    media_ids = [m.id for m in media]

    count("outbox", db.query(InfraDealerOutbox).filter(
        or_(
            InfraDealerOutbox.mobile.like(f"%{phone}"),
            InfraDealerOutbox.conversation_id.in_(conv_ids or [0]),
            InfraDealerOutbox.draft_id.in_(draft_ids or [0]),
            _json_mentions(InfraDealerOutbox.payload_json, phone, lids),
        )
    ).delete(synchronize_session=False))
    count("requests", db.query(InfraDealerRequest).filter(
        or_(
            InfraDealerRequest.mobile.like(f"%{phone}"),
            InfraDealerRequest.conversation_id.in_(conv_ids or [0]),
            InfraDealerRequest.draft_id.in_(draft_ids or [0]),
            _json_mentions(InfraDealerRequest.payload_json, phone, lids),
        )
    ).delete(synchronize_session=False))
    count("callbacks", db.query(InfraDealerCallback).filter(
        _json_mentions(InfraDealerCallback.payload_json, phone, lids)
    ).delete(synchronize_session=False))
    count("chats", db.query(Chat).filter(
        or_(
            Chat.from_mobile.like(f"%{phone}"),
            Chat.to_mobile.like(f"%{phone}"),
            Chat.conversation_id.in_(conv_keys or [""]),
            Chat.media_id.in_(media_ids or [0]),
        )
    ).delete(synchronize_session=False))
    count("media", db.query(AiMedia).filter(AiMedia.id.in_(media_ids or [0])).delete(synchronize_session=False))

    # Other conversations (e.g. the office line) may point at a draft being removed.
    db.query(AiConversation).filter(AiConversation.draft_id.in_(draft_ids or [0])).update(
        {AiConversation.draft_id: None}, synchronize_session=False
    )
    product_ids = [
        pid for (pid,) in db.query(AiListingDraft.posted_product_id)
        .filter(AiListingDraft.id.in_(draft_ids or [0]), AiListingDraft.posted_product_id.isnot(None))
        .all()
    ]
    count("drafts", db.query(AiListingDraft).filter(AiListingDraft.id.in_(draft_ids or [0])).delete(synchronize_session=False))
    count("events", db.query(AiEvent).filter(AiEvent.mobile == phone).delete(synchronize_session=False))
    count("conversations", db.query(AiConversation).filter(AiConversation.id.in_(conv_ids or [0])).delete(synchronize_session=False))

    message_ids = [mid for (mid,) in db.query(Message.id).filter(Message.from_mobile.like(f"%{phone}")).all()]
    count("submissions", db.query(Submission).filter(
        or_(Submission.mobile == phone, Submission.message_id.in_(message_ids or [0]))
    ).delete(synchronize_session=False))
    count("messages", db.query(Message).filter(Message.id.in_(message_ids or [0])).delete(synchronize_session=False))

    users = db.query(User.id).filter(User.mobile == phone).all()
    user_ids = [uid for (uid,) in users]
    count("products", db.query(Product).filter(
        or_(Product.mobile == phone, Product.user_id.in_(user_ids or [0]), Product.id.in_(product_ids or [0]))
    ).delete(synchronize_session=False))
    count("otps", db.query(Otp).filter(Otp.mobile == phone).delete(synchronize_session=False))
    count("contacts", db.query(Contact).filter(Contact.mobile == phone).delete(synchronize_session=False))
    count("broadcast_recipients", db.query(BroadcastRecipient).filter(BroadcastRecipient.to_mobile == phone).delete(synchronize_session=False))
    count("account_state", db.query(InfraDealerAccountState).filter(InfraDealerAccountState.mobile.like(f"%{phone}")).delete(synchronize_session=False))
    count("office_sessions", db.query(AiOfficeSession).filter(AiOfficeSession.operator_mobile == phone).delete(synchronize_session=False))
    count("office_targets_cleared", db.query(AiOfficeSession).filter(
        or_(AiOfficeSession.target_phone == phone, AiOfficeSession.pending_phone == phone)
    ).update(
        {
            AiOfficeSession.target_phone: "",
            AiOfficeSession.target_user_id: "",
            AiOfficeSession.target_name: "",
            AiOfficeSession.target_username: "",
            AiOfficeSession.pending_phone: "",
            AiOfficeSession.step: "",
        },
        synchronize_session=False,
    ))
    count("users", db.query(User).filter(User.id.in_(user_ids or [0])).delete(synchronize_session=False))
    db.flush()
    log.info("account purge mobile=***%s removed=%s", phone[-4:], out)
    return out
