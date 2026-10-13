"""Runtime ledger of a sell card's details in Redis — guards against field loss in long chats.

Every turn the card's filled fields are saved as a new version
(wa:card:{mobile}:{draft_id}); a field that was filled before but is blank now
(overwritten by a racing turn, a stale payload, a reset path) is put back.
Corrections replace values, so they become the next version. Once the
customer confirms, the confirmed version is stored as final and the ledger
stops restoring. Postgres stays the source of truth; Redis down = no-op.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..models import AiConversation
from ..redis_cache import _key, get_redis

log = logging.getLogger("infradealer.ai.card_ledger")

LEDGER_KEYS = (
    "category", "type", "brand", "model", "year", "registration_year", "running_km",
    "operating_hours", "expected_price", "state", "city", "location", "fuel", "owners",
    "condition", "tyre_percent", "finance_amount", "finance_condition", "work_issues",
    "accident_history", "negotiable", "description", "vehicle_docs",
)
_PLACE_KEYS = ("state", "city", "location")
TTL_SECONDS = 7 * 24 * 3600
HISTORY = 20


def _blank(v) -> bool:
    return v in (None, "", [], {})


def _keys(conv: AiConversation) -> tuple[str, str] | None:
    if not conv.draft_id or not conv.mobile:
        return None
    base = _key("card", conv.mobile, conv.draft_id)
    return base, base + ":history"


def _valid(key: str, value) -> bool:
    """Place fields are wiped on purpose when they hold junk ("haan", "bus") — never restore junk."""
    if key not in _PLACE_KEYS:
        return True
    from .data_filteration import _looks_like_place, _match_state_name

    text = str(value or "").strip()
    return bool(text) and (_looks_like_place(text) or bool(_match_state_name(text)))


def load(conv: AiConversation) -> dict:
    keys, r = _keys(conv), get_redis()
    if not keys or r is None:
        return {}
    try:
        raw = r.get(keys[0])
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def sync(conv: AiConversation) -> list[str]:
    """Restore lost card fields from the last version, then save the current one. Returns restored keys."""
    from .tools import _payload, _write_payload

    keys, r = _keys(conv), get_redis()
    if not keys or r is None:
        return []
    payload = _payload(conv)
    try:
        if str(payload.get("intent") or "").upper() != "SELL":
            r.delete(*keys)
            return []
        raw = r.get(keys[0])
        led = json.loads(raw) if raw else {}
        if led.get("final"):
            return []
        restored = []
        if not payload.get("customer_confirmed"):
            for k, v in (led.get("fields") or {}).items():
                if k in LEDGER_KEYS and _blank(payload.get(k)) and not _blank(v) and _valid(k, v):
                    payload[k] = v
                    restored.append(k)
            if restored:
                _write_payload(conv, payload)
                log.warning("card_ledger.restored mobile=***%s draft=%s keys=%s",
                            conv.mobile[-4:], conv.draft_id, restored)
        fields = {k: payload[k] for k in LEDGER_KEYS if not _blank(payload.get(k)) and _valid(k, payload[k])}
        final = bool(payload.get("customer_confirmed"))
        if fields != led.get("fields") or final:
            version = {
                "v": int(led.get("v") or 0) + 1,
                "card_id": payload.get("active_card_id") or "",
                "fields": fields,
                "final": final,
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
            blob = json.dumps(version, ensure_ascii=False, default=str)
            pipe = r.pipeline()
            pipe.set(keys[0], blob, ex=TTL_SECONDS)
            pipe.lpush(keys[1], blob)
            pipe.ltrim(keys[1], 0, HISTORY - 1)
            pipe.expire(keys[1], TTL_SECONDS)
            pipe.execute()
        return restored
    except Exception:
        log.exception("card_ledger.sync failed mobile=***%s", conv.mobile[-4:])
        return []
