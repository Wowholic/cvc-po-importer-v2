"""One shared team login. The username, password, and signing secret come from the environment."""

import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

SESSION_COOKIE = "po_session"
SESSION_MAX_AGE = 60 * 60 * 12
_MAX_FAILURES = 5
_WINDOW_SECONDS = 15 * 60
_LOCK_SECONDS = 15 * 60
_attempts = {}


def auth_settings():
    user = os.environ.get("AUTH_USER", "").strip()
    password = os.environ.get("AUTH_PASSWORD", "")
    secret = os.environ.get("AUTH_SECRET", "")
    if not user:
        return None, "Set AUTH_USER before signing in."
    if len(password) < 12:
        return None, "AUTH_PASSWORD must be at least 12 characters."
    if len(secret) < 32:
        return None, "AUTH_SECRET must be at least 32 characters."
    return {"user": user, "password": password, "secret": secret}, ""


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )


def check_credentials(user: str, password: str) -> bool:
    settings, _error = auth_settings()
    if settings is None:
        return False
    user_ok = _equal(user.strip(), settings["user"])
    password_ok = _equal(password, settings["password"])
    return user_ok and password_ok


def _sign(payload: str, settings: dict) -> str:
    key = hmac.new(
        settings["secret"].encode(),
        f"session-key:{settings['password']}".encode(),
        hashlib.sha256,
    ).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def create_session(now: float | None = None) -> str:
    settings, error = auth_settings()
    if settings is None:
        raise RuntimeError(error)
    moment = time.time() if now is None else now
    payload = urlsafe_b64encode(json.dumps({"exp": moment + SESSION_MAX_AGE}).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload, settings)}"


def session_expiry(token: str | None, now: float | None = None) -> float | None:
    settings, _error = auth_settings()
    if settings is None or not token:
        return None
    payload, separator, signature = token.partition(".")
    if not payload or not separator or not signature or not _equal(signature, _sign(payload, settings)):
        return None
    try:
        padded = payload + ("=" * (-len(payload) % 4))
        data = json.loads(urlsafe_b64decode(padded.encode()))
        expiry = data.get("exp")
    except (ValueError, json.JSONDecodeError):
        return None
    moment = time.time() if now is None else now
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or expiry <= moment:
        return None
    return float(expiry)


def verify_session(token: str | None, now: float | None = None) -> bool:
    return session_expiry(token, now) is not None


def client_ip(headers, remote: str | None) -> str:
    forwarded = headers.get("X-Forwarded-For", "")
    address = forwarded.split(",")[0].strip() if forwarded else (remote or "")
    return address or "unknown"


def locked_for(ip: str, now: float | None = None) -> float:
    moment = time.time() if now is None else now
    entry = _attempts.get(ip)
    if not entry or entry["locked_until"] <= moment:
        return 0
    return entry["locked_until"] - moment


def record_failure(ip: str, now: float | None = None) -> None:
    moment = time.time() if now is None else now
    _prune(moment)
    entry = _attempts.get(ip)
    if entry is None or moment - entry["first_at"] > _WINDOW_SECONDS:
        entry = {"failures": 0, "first_at": moment, "locked_until": 0}
    entry["failures"] += 1
    if entry["failures"] >= _MAX_FAILURES:
        entry["locked_until"] = moment + _LOCK_SECONDS
    _attempts[ip] = entry


def clear_failures(ip: str) -> None:
    _attempts.pop(ip, None)


def _prune(now: float) -> None:
    stale = [
        ip for ip, entry in _attempts.items()
        if entry["locked_until"] <= now and now - entry["first_at"] > _WINDOW_SECONDS
    ]
    for ip in stale:
        _attempts.pop(ip, None)
