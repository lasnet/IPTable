from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base


def _sqlite_connect_args(database_url: str) -> dict[str, bool]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return

    raw_path = database_url.removeprefix(prefix)
    if raw_path in {":memory:", ""}:
        return

    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args=_sqlite_connect_args(settings.database_url),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _ensure_users_columns() -> None:
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    bool_default = "FALSE" if engine.dialect.name == "postgresql" else "0"
    columns = {
        "first_name": "VARCHAR(120) NOT NULL DEFAULT ''",
        "last_name": "VARCHAR(120) NOT NULL DEFAULT ''",
        "description": "TEXT NOT NULL DEFAULT ''",
        "is_admin": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "can_create": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "can_edit": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "can_delete": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
        "can_manage_columns": f"BOOLEAN NOT NULL DEFAULT {bool_default}",
    }

    with engine.begin() as connection:
        for name, definition in columns.items():
            if name not in existing_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_users_columns()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
