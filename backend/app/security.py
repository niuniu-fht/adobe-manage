import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import settings


SESSION_MAX_AGE_SECONDS = 12 * 3600
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def check_login_rate_limit(client_ip: str) -> None:
    now = time.time()
    attempts = _login_attempts[client_ip]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Too many login attempts")
    attempts.append(now)


def verify_access_key(provided: str) -> bool:
    expected = settings.access_key
    return bool(expected and provided) and hmac.compare_digest(provided, expected)


def create_session(request: Request) -> str:
    csrf_token = secrets.token_urlsafe(24)
    request.session.clear()
    request.session.update(
        {"authenticated": True, "login_at": int(time.time()), "csrf_token": csrf_token}
    )
    return csrf_token


def require_auth(request: Request) -> None:
    session = request.session or {}
    login_at = int(session.get("login_at") or 0)
    if not session.get("authenticated") or login_at < int(time.time()) - SESSION_MAX_AGE_SECONDS:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Authentication required")


def require_ops_key(request: Request) -> None:
    expected = str(settings.ops_key or "")
    provided = str(request.headers.get("x-adobe2api-ops-key") or "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid operations key")


def require_csrf(request: Request) -> None:
    require_auth(request)
    expected = str((request.session or {}).get("csrf_token") or "")
    provided = str(request.headers.get("x-csrf-token") or "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
