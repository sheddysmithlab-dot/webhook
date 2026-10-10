"""account.deleted callback erases everything the agent stored for that mobile."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.infradealer.service import InfraDealerIntegrationService
from app.models import (
    AiConversation,
    AiEvent,
    AiListingDraft,
    AiMedia,
    AiOfficeSession,
    BlockedNumber,
    Chat,
    Contact,
    InfraDealerAccountState,
    InfraDealerCallback,
    InfraDealerOutbox,
    InfraDealerRequest,
    Otp,
    User,
)

GONE = "9123456780"
KEEP = "9000000001"
OFFICE = "8224000829"


def _db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _seed(db, mobile, media_dir):
    user = User(name="Ramesh", mobile=mobile, source="whatsapp_auto", role="user", account_ready=True)
    db.add(user)
    db.flush()
    conv = AiConversation(mobile=mobile, conversation_id=f"CONV_{mobile}", profile_id=user.id, payload_json="{}")
    db.add(conv)
    db.flush()
    draft = AiListingDraft(conversation_id=conv.id, mobile=mobile, user_id=user.id, card_id="CARD-001")
    db.add(draft)
    db.flush()
    conv.draft_id = draft.id
    photo = media_dir / f"{mobile}.jpg"
    photo.write_bytes(b"x")
    media = AiMedia(conversation_id=conv.id, draft_id=draft.id, local_path=str(photo))
    db.add(media)
    db.add_all([
        Chat(conversation_id=f"CONV_{mobile}", from_mobile=f"91{mobile}", body="JCB bechni hai"),
        Chat(conversation_id=f"CONV_{mobile}", from_mobile="infradealer", to_mobile=mobile, direction="outbound", body="ok"),
        AiEvent(mobile=mobile, event_type="USER_MESSAGE_RECEIVED"),
        Contact(mobile=mobile, name="Ramesh"),
        Otp(mobile=mobile, code_hash="h", status="sent", expires_at=datetime.utcnow()),
        InfraDealerAccountState(mobile=mobile, account_status="ACCOUNT_FOUND", infradealer_user_id="901"),
        InfraDealerOutbox(event_type="LISTING_PUSH", request_id=f"r-{mobile}", mobile=mobile, conversation_id=conv.id),
        InfraDealerRequest(request_id=f"q-{mobile}", event_type="LISTING_PUSH", mobile=mobile),
    ])
    db.flush()
    return photo


def test_account_deleted_callback_erases_the_mobile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ai_media_dir", str(tmp_path))
    db = _db()
    gone_photo = _seed(db, GONE, tmp_path)
    keep_photo = _seed(db, KEEP, tmp_path)
    db.add(BlockedNumber(mobile=GONE))
    office_draft = AiListingDraft(
        conversation_id=db.query(AiConversation).filter_by(mobile=KEEP).one().id,
        mobile=OFFICE,
        card_id="CARD-009",
        confirmed_json=json.dumps({"seller_contact": GONE, "brand": "JCB"}),
    )
    db.add(office_draft)
    db.add(AiOfficeSession(operator_mobile=OFFICE, target_phone=GONE, target_user_id="901", target_name="Ramesh"))
    db.add(InfraDealerCallback(callback_id="c1", event_type="listing.posted", payload_json=json.dumps({"listing": {"listing_id": "131"}})))
    db.commit()

    res = InfraDealerIntegrationService(db).handle_callback(
        {"event": "account.deleted", "request_id": "del-1", "account": {"phone": GONE, "user_id": "901", "listing_ids": ["131"]}}
    )
    assert res["ok"] and res["removed"]["conversations"] == 1

    for model, col in (
        (User, User.mobile), (AiConversation, AiConversation.mobile), (AiListingDraft, AiListingDraft.mobile),
        (AiEvent, AiEvent.mobile), (Contact, Contact.mobile), (Otp, Otp.mobile),
        (InfraDealerAccountState, InfraDealerAccountState.mobile), (InfraDealerOutbox, InfraDealerOutbox.mobile),
        (InfraDealerRequest, InfraDealerRequest.mobile),
    ):
        assert db.query(model).filter(col == GONE).count() == 0, model.__name__
    assert db.query(Chat).filter(Chat.conversation_id == f"CONV_{GONE}").count() == 0
    assert db.query(AiMedia).count() == 1 and not gone_photo.exists() and keep_photo.exists()
    assert db.query(AiListingDraft).filter_by(card_id="CARD-009").count() == 0
    session = db.query(AiOfficeSession).one()
    assert (session.target_phone, session.target_user_id, session.target_name) == ("", "", "")
    callbacks = db.query(InfraDealerCallback).all()
    assert [c.callback_id for c in callbacks] == ["del-1"] and GONE not in callbacks[0].payload_json
    assert db.query(BlockedNumber).filter_by(mobile=GONE).count() == 1

    assert db.query(User).filter_by(mobile=KEEP).count() == 1
    assert db.query(AiConversation).filter_by(mobile=KEEP).count() == 1
    assert db.query(Chat).filter(Chat.conversation_id == f"CONV_{KEEP}").count() == 2
