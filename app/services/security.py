import html
import hashlib
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from markupsafe import Markup
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import LoginRateLimitEvent

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "_csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def csrf_input(request: Request) -> Markup:
    token = html.escape(ensure_csrf_token(request), quote=True)
    return Markup(f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{token}">')


async def require_csrf_token(request: Request) -> None:
    if request.method.upper() in SAFE_METHODS:
        return

    session_token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(session_token, str) or not session_token:
        raise HTTPException(status_code=403, detail="CSRF token missing")

    form = await request.form()
    form_token = form.get(CSRF_FORM_FIELD)
    if not isinstance(form_token, str) or not secrets.compare_digest(session_token, form_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")


@dataclass
class _RateLimitRecord:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginRateLimiter:
    def __init__(self) -> None:
        self._records: dict[str, _RateLimitRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(client_ip: str, username: str) -> str:
        return f"{client_ip}:{username.strip().casefold()}"

    def retry_after(
        self,
        client_ip: str,
        username: str,
        *,
        window_seconds: int,
        now: float | None = None,
    ) -> int | None:
        current_time = time.time() if now is None else now
        key = self._key(client_ip, username)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            record.failures = [item for item in record.failures if current_time - item <= window_seconds]
            if record.locked_until > current_time:
                return max(1, int(record.locked_until - current_time))
            if not record.failures:
                self._records.pop(key, None)
        return None

    def record_failure(
        self,
        client_ip: str,
        username: str,
        *,
        attempts: int,
        window_seconds: int,
        lockout_seconds: int,
        now: float | None = None,
    ) -> int | None:
        current_time = time.time() if now is None else now
        key = self._key(client_ip, username)
        with self._lock:
            record = self._records.setdefault(key, _RateLimitRecord())
            record.failures = [item for item in record.failures if current_time - item <= window_seconds]
            record.failures.append(current_time)
            if len(record.failures) >= attempts:
                record.locked_until = current_time + lockout_seconds
                record.failures.clear()
                return lockout_seconds
        return None

    def reset(self, client_ip: str, username: str) -> None:
        key = self._key(client_ip, username)
        with self._lock:
            self._records.pop(key, None)


def _login_rate_limit_key(client_ip: str, username: str) -> str:
    identity = f"{client_ip}:{username.strip().casefold()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _prune_login_rate_limit_events(
    db: Session,
    *,
    identity_hash: str,
    keep_after: datetime,
) -> None:
    db.execute(
        delete(LoginRateLimitEvent).where(
            LoginRateLimitEvent.identity_hash == identity_hash,
            LoginRateLimitEvent.created_at < keep_after,
        )
    )


def login_rate_limit_retry_after(
    db: Session,
    client_ip: str,
    username: str,
    *,
    attempts: int,
    window_seconds: int,
    lockout_seconds: int,
    now: datetime | None = None,
) -> int | None:
    current_time = now or datetime.utcnow()
    identity_hash = _login_rate_limit_key(client_ip, username)
    keep_seconds = max(window_seconds, lockout_seconds) + window_seconds
    keep_after = current_time - timedelta(seconds=keep_seconds)
    _prune_login_rate_limit_events(db, identity_hash=identity_hash, keep_after=keep_after)

    events = db.scalars(
        select(LoginRateLimitEvent.created_at)
        .where(
            LoginRateLimitEvent.identity_hash == identity_hash,
            LoginRateLimitEvent.created_at >= keep_after,
        )
        .order_by(LoginRateLimitEvent.created_at.desc())
        .limit(attempts)
    ).all()
    if len(events) < attempts:
        db.commit()
        return None

    newest = events[0]
    oldest = events[-1]
    if newest - oldest > timedelta(seconds=window_seconds):
        db.commit()
        return None

    unlock_at = newest + timedelta(seconds=lockout_seconds)
    remaining = (unlock_at - current_time).total_seconds()
    db.commit()
    return max(1, math.ceil(remaining)) if remaining > 0 else None


def record_login_failure(
    db: Session,
    client_ip: str,
    username: str,
    *,
    attempts: int,
    window_seconds: int,
    lockout_seconds: int,
    now: datetime | None = None,
) -> int | None:
    current_time = now or datetime.utcnow()
    db.add(LoginRateLimitEvent(identity_hash=_login_rate_limit_key(client_ip, username), created_at=current_time))
    db.commit()
    return login_rate_limit_retry_after(
        db,
        client_ip,
        username,
        attempts=attempts,
        window_seconds=window_seconds,
        lockout_seconds=lockout_seconds,
        now=current_time,
    )


def reset_login_failures(db: Session, client_ip: str, username: str) -> None:
    db.execute(delete(LoginRateLimitEvent).where(LoginRateLimitEvent.identity_hash == _login_rate_limit_key(client_ip, username)))
    db.commit()
