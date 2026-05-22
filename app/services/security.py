import html
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from markupsafe import Markup

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
