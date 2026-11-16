import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AppConfiguration, Role, User

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

    return user


def validate_admin_password(password: str) -> None:
    if len(password.strip()) < 12 or password.strip().startswith("replace-with-"):
        raise ValueError("INITIAL_ADMIN_PASSWORD must be at least 12 characters and must be changed from its placeholder")


def bootstrap_initial_admin(db: Session, settings: Settings) -> None:
    db.execute(select(AppConfiguration.id).where(AppConfiguration.id == 1).with_for_update())
    if db.scalar(select(User.id).where(User.is_admin.is_(True)).limit(1)) is not None:
        return
    password = settings.initial_admin_password
    if settings.app_env == "production":
        validate_admin_password(password)
    elif not password:
        return

    username = settings.initial_admin_username.strip()
    if len(username) < 3:
        raise ValueError("INITIAL_ADMIN_USERNAME must have at least 3 characters")
    if db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is not None:
        raise ValueError("INITIAL_ADMIN_USERNAME already belongs to an existing user. Choose a different login")

    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            role=db.scalar(select(Role).where(Role.code == "admin")),
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
    role: Role,
    is_active: bool = True,
) -> User:
    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        description=description.strip(),
        is_active=is_active,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
