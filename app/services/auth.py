import hashlib
import secrets
from datetime import datetime

from sqlalchemy import func, select
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
    existing = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if existing is not None:
        return

    db.add(User(username=username, password_hash=hash_password(settings.initial_admin_password)))
    db.commit()
