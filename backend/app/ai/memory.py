"""Lightweight AI memory helpers — Relationship Manager hot path does not need LLM memory DB.

Stubs keep legacy engine.py / startup imports safe.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..models import Chat

log = logging.getLogger("infradealer.ai.memory")

_SEEDED = False


def seed_memory(db: Session | None = None) -> None:
    global _SEEDED
    if _SEEDED:
        return
    _SEEDED = True
    log.debug("ai memory seed complete (stub)")


def load_reps(db: Session | None = None) -> list[str]:
    return []


def prompt_block(db: Session | None = None) -> str:
    return ""


def harvest_turn(db: Session | None = None, *args, **kwargs) -> None:
    return None


def customer_annoyed(text: str = "") -> bool:
    return bool(re.search(r"\b(bakwas|pagal|irritat|bar bar|same\s+question)\b", text or "", re.I))


def photos_complete(payload: dict | None = None) -> bool:
    ids = (payload or {}).get("media_ids") or []
    return len([i for i in ids if i]) >= 2


def question_kind(text: str = "") -> str:
    t = (text or "").lower()
    if "price" in t or "rate" in t or "lakh" in t:
        return "price"
    if "year" in t or "model" in t:
        return "year"
    if "photo" in t:
        return "photos"
    return "other"


def recent_outbound_bodies(db: Session, conversation_id: str, limit: int = 6) -> list[str]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id, Chat.direction == "outbound")
        .order_by(Chat.id.desc())
        .limit(limit)
        .all()
    )
    return [r.body or "" for r in rows]


def too_similar(a: str, b: str, threshold: float = 0.86) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


def is_repeat_outbound(db: Session, conversation_id: str, text: str) -> bool:
    bodies = recent_outbound_bodies(db, conversation_id, 3)
    return any(too_similar(text, b) for b in bodies)
