"""Revocable browser sessions shared by all web processes through the database."""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import User, UserSession

SESSION_TOKEN_KEY = "session_token"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_session(db: Session, user: User, timeout_seconds: int) -> str:
    now = datetime.utcnow()
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(UserSession(
        token_hash=_digest(token), user_id=user.id,
        credential_hash=_digest(user.password_hash),
        expires_at=now + timedelta(seconds=timeout_seconds),
    ))
    db.commit()
    return token


def session_user(
    db: Session, token: object, user_id: int, timeout_seconds: int, *, touch: bool = True,
) -> User | None:
    if not isinstance(token, str) or len(token) != 43:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    now = datetime.utcnow()
    valid_session = (
        UserSession.token_hash == _digest(token),
        UserSession.user_id == user.id,
        UserSession.credential_hash == _digest(user.password_hash),
        UserSession.expires_at > now,
    )
    if not touch:
        valid_token = db.scalar(select(UserSession.token_hash).where(*valid_session))
        return user if valid_token is not None else None

    # UPDATE cannot recreate a row concurrently removed by logout/password reset.
    touched = db.execute(update(UserSession).where(*valid_session).values(
        expires_at=now + timedelta(seconds=timeout_seconds),
    ))
    db.commit()
    return user if touched.rowcount == 1 else None


def revoke_session(db: Session, token: object) -> None:
    if isinstance(token, str) and len(token) == 43:
        db.execute(delete(UserSession).where(UserSession.token_hash == _digest(token)))
        db.commit()


def revoke_user_sessions(db: Session, user_id: int) -> None:
    """Caller commits atomically with the credential/account change."""
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
