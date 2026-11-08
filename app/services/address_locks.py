"""Serialize edit leases and address writes on the same database row."""
from datetime import UTC, datetime, timedelta
import secrets

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import IPAddress, User

EDIT_LOCK_DURATION = timedelta(seconds=90)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _address_update(project_id: int, ip_id: int):
    return (
        update(IPAddress).where(IPAddress.project_id == project_id, IPAddress.id == ip_id)
        .execution_options(synchronize_session=False)
    )


def _unlocked(now: datetime):
    return or_(IPAddress.edit_lock_expires_at.is_(None), IPAddress.edit_lock_expires_at <= now)


def _owned(user_id: int | None, token: str):
    return (IPAddress.edit_lock_user_id == user_id, IPAddress.edit_lock_token == token)


def find_address(db: Session, project_id: int, ip_id: int) -> IPAddress | None:
    return db.scalar(select(IPAddress).where(IPAddress.project_id == project_id, IPAddress.id == ip_id))


def lock_owner(db: Session, address: IPAddress) -> str | None:
    if address.edit_lock_expires_at is None or address.edit_lock_expires_at <= _now():
        return None
    return db.scalar(select(User.username).where(User.id == address.edit_lock_user_id)) or ""


def acquire(db: Session, project_id: int, ip_id: int, user_id: int) -> str | None:
    now = _now()
    token = secrets.token_urlsafe(32)
    result = db.execute(_address_update(project_id, ip_id).where(_unlocked(now)).values(
        edit_lock_user_id=user_id, edit_lock_token=token, edit_lock_expires_at=now + EDIT_LOCK_DURATION,
        updated_at=IPAddress.updated_at,
    ))
    return token if result.rowcount == 1 else None


def renew(db: Session, project_id: int, ip_id: int, user_id: int, token: str) -> bool:
    now = _now()
    result = db.execute(
        _address_update(project_id, ip_id)
        .where(*_owned(user_id, token), IPAddress.edit_lock_expires_at > now)
        .values(edit_lock_expires_at=now + EDIT_LOCK_DURATION, updated_at=IPAddress.updated_at)
    )
    return result.rowcount == 1


def release(db: Session, project_id: int, ip_id: int, user_id: int, token: str) -> None:
    db.execute(_address_update(project_id, ip_id).where(*_owned(user_id, token)).values(
        edit_lock_user_id=None, edit_lock_token=None, edit_lock_expires_at=None, updated_at=IPAddress.updated_at,
    ))


def reserve_write(db: Session, project_id: int, ip_id: int, *, user_id: int | None = None, token: str = "") -> bool:
    # Keep the row locked until the caller commits the address and its history.
    # A supplied token must still be current even if the row is otherwise free.
    now = _now()
    conditions = (*_owned(user_id, token), IPAddress.edit_lock_expires_at > now) if token else (_unlocked(now),)
    result = db.execute(_address_update(project_id, ip_id).where(*conditions).values(
        edit_lock_user_id=None, edit_lock_token=None, edit_lock_expires_at=None, updated_at=IPAddress.updated_at,
    ))
    return result.rowcount == 1
