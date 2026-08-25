import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

import bcrypt
from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings

log = logging.getLogger("infradealer.auth")

COOKIE = "idw_sid"
TTL = 8 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
_fails: dict[str, list[float]] = {}


def _secret() -> str:
    return (settings.auth_session_secret or settings.admin_token or "").strip()


def _email() -> str:
    return (settings.auth_email or "").strip().lower()


def _str_eq(left: str, right: str) -> bool:
    a = (left or "").encode("utf-8")
    b = (right or "").encode("utf-8")
    n = max(len(a), len(b), 1)
    return hmac.compare_digest(a.ljust(n, b"\0"), b.ljust(n, b"\0")) and len(a) == len(b)


def _client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "0.0.0.0")


def _locked(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    _fails[ip] = hits
    return len(hits) >= MAX_FAILS


def _record_fail(ip: str) -> None:
    _fails.setdefault(ip, []).append(time.time())


def _clear_fails(ip: str) -> None:
    _fails.pop(ip, None)


def verify_password(password: str) -> bool:
    raw = (password or "").encode("utf-8")
    hashed = (settings.auth_password_hash or "").encode("utf-8")
    if not hashed or not raw:
        return False
    try:
        return bcrypt.checkpw(raw, hashed)
    except ValueError:
        return False


def sign_session(email: str) -> str:
    payload = json.dumps({"e": email, "x": int(time.time()) + TTL}, separators=(",", ":"))
    body = urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(token: str | None) -> str | None:
    if not token or "." not in token or not _secret():
        return None
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        data = json.loads(urlsafe_b64decode(body + pad).decode())
    except (ValueError, json.JSONDecodeError):
        return None
    if int(data.get("x") or 0) < time.time():
        return None
    email = str(data.get("e") or "").lower()
    want = _email()
    if not email or not want or not _str_eq(email, want):
        return None
    return email


def current_user(request: Request) -> str | None:
    return read_session(request.cookies.get(COOKIE))


def set_session_cookie(response: Response, email: str) -> None:
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        COOKIE,
        sign_session(email),
        max_age=TTL,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def authenticate(request: Request, email: str, password: str) -> None:
    ip = _client_ip(request)
    if _locked(ip):
        log.warning("login lockout ip_hash=%s", hashlib.sha256(ip.encode()).hexdigest()[:12])
        raise HTTPException(429, "Too many attempts. 15 minute baad try karo.")
    want_email = _email()
    got_email = (email or "").strip().lower()
    if not want_email or not settings.auth_password_hash or not _secret():
        raise HTTPException(503, "Login is not configured.")
    email_ok = _str_eq(got_email, want_email)
    pass_ok = verify_password(password)
    if not (email_ok and pass_ok):
        _record_fail(ip)
        raise HTTPException(401, "Invalid email or password.")
    _clear_fails(ip)


def is_public(request: Request) -> bool:
    path = request.url.path
    method = request.method.upper()
    if method == "OPTIONS":
        return True
    if path == "/api/health":
        return True
    if path in {"/api/auth/login", "/api/auth/logout", "/api/auth/me"}:
        return True
    if path == "/webhook/whatsapp":
        return True
    if path == "/api/v1/integrations/infradealer/callback":
        return True
    if path.startswith("/api/v1/integrations/infradealer/media/"):
        return True
    # Marketplace catalog + photos must load without session (img tags / public browse).
    if method == "GET" and path == "/api/products":
        return True
    if method == "GET" and path == "/api/meta/options":
        return True
    if method == "GET" and re.fullmatch(r"/api/products/\d+", path):
        return True
    if method == "GET" and re.fullmatch(r"/api/products/\d+/photos/\d+", path):
        return True
    return False


class SessionGate(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if is_public(request):
            return await call_next(request)
        if current_user(request):
            return await call_next(request)
        return JSONResponse({"detail": "Login required"}, status_code=401)
