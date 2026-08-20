"""Temporary Redis cache/lock for WhatsApp AI — never replaces Postgres.

Keys (TTL'd):
  wa:lock:{mobile}           — conversation processing lock
  wa:latest:{mobile}         — newest inbound wamid
  wa:active_card:{mobile}    — active Card ID hint
  wa:processing:{mobile}     — processing status
  wa:cleanup:{mobile}:{card} — cleanup due marker (mirrors DB cleanup_at)

If Redis is down/unconfigured, all helpers no-op / return None so callers
fall back to in-process locks + DB checks.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from .config import settings

log = logging.getLogger("infradealer.redis")

_client = None
_client_failed = False
_client_lock = threading.Lock()

# Local fallback locks when Redis is unavailable
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_GUARD = threading.Lock()
_LOCAL_LATEST: dict[str, str] = {}
_LOCAL_ACTIVE_CARD: dict[str, str] = {}


def _key(*parts: str) -> str:
    return "wa:" + ":".join(str(p) for p in parts if p is not None)


def redis_enabled() -> bool:
    return bool((getattr(settings, "redis_url", None) or "").strip())


def get_redis():
    """Lazy Redis client. Returns None on missing config or connection failure."""
    global _client, _client_failed
    if not redis_enabled() or _client_failed:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None or _client_failed:
            return _client
        try:
            import redis

            url = settings.redis_url.strip()
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=0.4,
                socket_timeout=0.6,
                retry_on_timeout=False,
            )
            client.ping()
            _client = client
            log.info("redis connected")
            return _client
        except Exception as exc:
            _client_failed = True
            log.warning("redis unavailable — using local fallback: %s", exc)
            return None


def reset_redis_client() -> None:
    """Test helper: clear cached client / failure flag."""
    global _client, _client_failed
    with _client_lock:
        _client = None
        _client_failed = False


def set_latest_wamid(mobile: str, wamid: str, ttl: int = 3600) -> None:
    if not mobile or not wamid:
        return
    _LOCAL_LATEST[mobile] = wamid
    r = get_redis()
    if not r:
        return
    try:
        r.set(_key("latest", mobile), wamid, ex=ttl)
    except Exception:
        pass


def get_latest_wamid(mobile: str) -> str | None:
    if not mobile:
        return None
    r = get_redis()
    if r:
        try:
            val = r.get(_key("latest", mobile))
            if val:
                return str(val)
        except Exception:
            pass
    return _LOCAL_LATEST.get(mobile)


def is_latest_wamid(mobile: str, wamid: str) -> bool:
    """True if unknown/empty, or matches Redis/local latest."""
    if not wamid:
        return True
    latest = get_latest_wamid(mobile)
    if not latest:
        return True
    return latest == wamid


def set_active_card(mobile: str, card_id: str, ttl: int = 86400) -> None:
    if not mobile:
        return
    if not card_id:
        _LOCAL_ACTIVE_CARD.pop(mobile, None)
        r = get_redis()
        if r:
            try:
                r.delete(_key("active_card", mobile))
            except Exception:
                pass
        return
    _LOCAL_ACTIVE_CARD[mobile] = card_id
    r = get_redis()
    if not r:
        return
    try:
        r.set(_key("active_card", mobile), card_id, ex=ttl)
    except Exception:
        pass


def get_active_card(mobile: str) -> str | None:
    if not mobile:
        return None
    r = get_redis()
    if r:
        try:
            val = r.get(_key("active_card", mobile))
            if val:
                return str(val)
        except Exception:
            pass
    return _LOCAL_ACTIVE_CARD.get(mobile)


def set_processing(mobile: str, status: str, ttl: int = 60) -> None:
    if not mobile:
        return
    r = get_redis()
    if not r:
        return
    try:
        r.set(_key("processing", mobile), status[:40], ex=ttl)
    except Exception:
        pass


def mark_card_cleanup(mobile: str, card_id: str, ttl_seconds: int) -> None:
    """Short-lived marker; DB cleanup_at remains source of truth."""
    if not mobile or not card_id or ttl_seconds <= 0:
        return
    r = get_redis()
    if not r:
        return
    try:
        r.set(_key("cleanup", mobile, card_id), "1", ex=int(ttl_seconds) + 30)
    except Exception:
        pass


def clear_card_cleanup_marker(mobile: str, card_id: str) -> None:
    if not mobile or not card_id:
        return
    r = get_redis()
    if not r:
        return
    try:
        r.delete(_key("cleanup", mobile, card_id))
    except Exception:
        pass


@contextmanager
def mobile_lock(mobile: str, ttl: int = 45) -> Iterator[bool]:
    """Acquire per-mobile lock. Yields True if lock held.

    Prefers Redis SET NX; falls back to process-local threading.Lock.
    """
    phone = (mobile or "").strip()
    if not phone:
        yield True
        return

    r = get_redis()
    token = uuid.uuid4().hex
    redis_held = False
    if r:
        try:
            # Busy-wait briefly so rapid dual webhooks serialize instead of dropping
            deadline = time.time() + min(ttl, 20)
            while time.time() < deadline:
                ok = r.set(_key("lock", phone), token, nx=True, ex=ttl)
                if ok:
                    redis_held = True
                    break
                time.sleep(0.05)
            if not redis_held:
                log.info("redis lock busy mobile=%s", phone[-4:])
                yield False
                return
        except Exception:
            redis_held = False

    local = None
    if not redis_held:
        with _LOCAL_GUARD:
            local = _LOCAL_LOCKS.get(phone)
            if local is None:
                local = threading.Lock()
                _LOCAL_LOCKS[phone] = local
        local.acquire()

    try:
        yield True
    finally:
        if redis_held and r:
            try:
                # Release only if we still own the token
                cur = r.get(_key("lock", phone))
                if cur == token:
                    r.delete(_key("lock", phone))
            except Exception:
                pass
        if local is not None:
            try:
                local.release()
            except RuntimeError:
                pass
