"""Prefix the first bot reply of a fresh chat with the InfraDealer AI Agent intro."""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models import AiConversation, Chat
from ..services import utcnow
from .i18n import t

log = logging.getLogger("infradealer.ai")

# A chat with no outbound message for this long counts as a new conversation.
INTRO_GAP_HOURS = 12

_HAS_INTRO = re.compile(
    r"infradealer\s+ai\s+(?:agent|एजेंट)|infradealer\s+(?:ka|का)\s+ai|ai\s+relationship\s+manager",
    re.I,
)


def _conversation_ids(conv_id: str, mobile: str) -> list[str]:
    tail = (mobile or "")[-10:]
    ids = {conv_id, f"CONV_{mobile}", f"CONV_{tail}", f"CONV_91{tail}"}
    return [i for i in ids if i]


def recently_messaged(db: Session, conv_id: str, mobile: str) -> bool:
    since_ms = int(utcnow().timestamp() * 1000) - INTRO_GAP_HOURS * 3600 * 1000
    row = (
        db.query(Chat.id)
        .filter(
            Chat.direction == "outbound",
            Chat.conversation_id.in_(_conversation_ids(conv_id, mobile)),
            Chat.timestamp_ms >= since_ms,
        )
        .first()
    )
    return row is not None


def with_agent_intro(db: Session, conv: AiConversation, conv_id: str, mobile: str, lang: str, reply: str) -> str:
    body = (reply or "").strip()
    if not body or _HAS_INTRO.search(body):
        return reply
    try:
        from .office_mode import is_office_session

        if is_office_session(db, conv) or recently_messaged(db, conv_id, mobile):
            return reply
    except Exception:
        log.exception("agent_intro: check failed mobile=***%s", (mobile or "")[-4:])
        return reply
    return t(lang, "intro") + "\n\n" + body
