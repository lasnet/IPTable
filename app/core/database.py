from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


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


REQUIRED_TABLES = {
    "alembic_version",
    "folders",
    "users",
    "projects",
    "ip_addresses",
    "ip_address_history",
    "custom_fields",
    "login_rate_limit_events",
    "ping_schedules",
    "ping_jobs",
}


def init_db() -> None:
    existing_tables = set(inspect(engine).get_table_names())
    missing_tables = sorted(REQUIRED_TABLES - existing_tables)
    if missing_tables:
        missing = ", ".join(missing_tables)
        raise RuntimeError(f"Database schema is not initialized. Run `alembic upgrade head`. Missing: {missing}")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
