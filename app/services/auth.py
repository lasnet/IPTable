import hashlib
import secrets
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import User

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected_digest = password_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False

    if algorithm != PBKDF2_ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(digest, expected_digest)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(
        select(User).where(func.lower(User.username) == username.strip().lower(), User.is_active.is_(True))
    )
    if user is None or not verify_password(password, user.password_hash):
        return None

    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


def bootstrap_initial_admin(db: Session, settings: Settings) -> None:
    if not settings.initial_admin_password:
        return

    username = settings.initial_admin_username.strip()
    db.execute(
        update(User)
        .where(func.lower(User.username) != username.lower(), User.is_admin.is_(True))
        .values(
            is_admin=False,
            can_create=False,
            can_edit=False,
            can_delete=False,
            can_manage_columns=False,
        )
    )
    existing = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if existing is not None:
        existing.is_admin = True
        existing.is_active = True
        existing.can_create = True
        existing.can_edit = True
        existing.can_delete = True
        existing.can_manage_columns = True
        db.commit()
        return

    db.add(
        User(
            username=username,
            password_hash=hash_password(settings.initial_admin_password),
            is_admin=True,
            can_create=True,
            can_edit=True,
            can_delete=True,
            can_manage_columns=True,
        )
    )
    db.commit()


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    first_name: str,
    last_name: str,
    description: str,
    can_create: bool = False,
    can_edit: bool = False,
    can_delete: bool = False,
    can_manage_columns: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        description=description.strip(),
        is_active=is_active,
        can_create=can_create,
        can_edit=can_edit,
        can_delete=can_delete,
        can_manage_columns=can_manage_columns,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
