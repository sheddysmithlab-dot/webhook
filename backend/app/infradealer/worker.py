"""Outbox worker — processes pending InfraDealer integration events."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import InfraDealerOutbox
from .service import InfraDealerIntegrationService

log = logging.getLogger("infradealer.integration.worker")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _backoff_seconds(attempt: int) -> int:
    base = min(2 ** attempt, 300)
    return base + random.randint(0, max(1, base // 4))


def process_outbox(db: Session, limit: int = 20) -> int:
    now = _now()
    rows = (
        db.query(InfraDealerOutbox)
        .filter(
            InfraDealerOutbox.status.in_(["PENDING", "RETRY"]),
            (InfraDealerOutbox.next_retry_at.is_(None)) | (InfraDealerOutbox.next_retry_at <= now),
        )
        .order_by(InfraDealerOutbox.id.asc())
        .limit(limit)
        .all()
    )
    svc = InfraDealerIntegrationService(db)
    done = 0
    for row in rows:
        try:
            svc.process_outbox_item(row)
            done += 1
        except Exception as exc:
            log.exception("outbox %s failed: %s", row.id, exc)
            row.attempts = (row.attempts or 0) + 1
            row.status = "FAILED" if (row.attempts or 0) >= (row.max_attempts or 0) else "RETRY"
            row.last_error = str(exc)[:280]
            row.next_retry_at = _now() + timedelta(seconds=_backoff_seconds(row.attempts))
    db.commit()
    return done


def run_integration_tasks(db: Session, *, outbox_limit: int = 20, poll_limit: int = 15) -> dict:
    from ..ai.cards import process_due_card_cleanups

    svc = InfraDealerIntegrationService(db)
    processed = process_outbox(db, limit=outbox_limit)
    polled = 0
    try:
        polled = svc.poll_pending_listings(limit=poll_limit)
        db.commit()
    except Exception:
        log.exception("listing status poll failed")
    cleared = 0
    try:
        cleared = process_due_card_cleanups(db)
    except Exception:
        log.exception("card cleanup failed")
    return {"outbox_processed": processed, "listings_polled": polled, "cards_cleared": cleared}
