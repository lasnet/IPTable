"""Shared, revisioned runtime configuration."""
import hashlib
import secrets
from datetime import datetime, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import AppConfiguration, AppConfigurationAudit, IntegrationCredential, PingJob

GROUPS = {
    "interface": ("app_name", "interface_language", "project_table_default_page_size"),
    "security": ("session_idle_timeout_seconds", "login_rate_limit_attempts",
                 "login_rate_limit_window_seconds", "login_rate_limit_lockout_seconds"),
    "ping": ("ping_interval_seconds", "ping_timeout_seconds", "ping_concurrency", "ping_batch_size",
             "ping_batch_pause_seconds", "ping_project_pause_seconds", "ping_queue_poll_seconds",
             "ping_running_job_timeout_seconds"),
    "limits": ("max_project_addresses", "csv_import_max_bytes"),
}
RUNTIME_FIELDS = tuple(name for names in GROUPS.values() for name in names)


class RuntimeValues(BaseModel):
    app_name: str
    interface_language: str
    project_table_default_page_size: int = Field(ge=10, le=250)
    session_idle_timeout_seconds: int = Field(ge=60)
    login_rate_limit_attempts: int = Field(ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(ge=60)
    login_rate_limit_lockout_seconds: int = Field(ge=60)
    ping_interval_seconds: int = Field(ge=60)
    ping_timeout_seconds: int = Field(ge=1)
    ping_concurrency: int = Field(ge=1, le=128)
    ping_batch_size: int = Field(ge=1, le=512)
    ping_batch_pause_seconds: float = Field(ge=0, allow_inf_nan=False)
    ping_project_pause_seconds: float = Field(ge=0, allow_inf_nan=False)
    ping_queue_poll_seconds: float = Field(ge=1, allow_inf_nan=False)
    ping_running_job_timeout_seconds: int = Field(ge=300)
    max_project_addresses: int = Field(ge=1)
    csv_import_max_bytes: int = Field(ge=1024)


class RevisionConflict(ValueError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def initialize(db: Session, legacy: Settings | None = None) -> None:
    row = db.get(AppConfiguration, 1)
    if row is None:
        legacy = legacy or Settings()
        values = {name: getattr(legacy, name) for name in RUNTIME_FIELDS}
        values["project_table_default_page_size"] = min((25, 50, 100, 250),
            key=lambda size: abs(size - values["project_table_default_page_size"]))
        db.add(AppConfiguration(id=1, revision=1, config_schema_version=1, **values))
        db.add(IntegrationCredential(id=1, token_hash=token_hash(legacy.integration_api_token)
                                     if legacy.integration_api_token.strip() else None, version=1))
        db.add(AppConfigurationAudit(actor_name="system:env-import", section="initialization",
                                     old_revision=0, new_revision=1, changes={}))
        db.commit()
    else:
        legacy = None  # Never reimport changed env after initialization.
    # Existing running jobs retain the timeout in force when upgraded.
    row = db.get(AppConfiguration, 1)
    for job in db.scalars(select(PingJob).where(PingJob.status == "running",
                            PingJob.started_at.is_not(None), PingJob.lease_expires_at.is_(None))):
        job.lease_expires_at = job.started_at + timedelta(seconds=row.ping_running_job_timeout_seconds)
    db.commit()


def current(db: Session, base: Settings | None = None) -> Settings:
    row = db.get(AppConfiguration, 1)
    if row is None:
        raise RuntimeError("Application configuration is not initialized. Run app.manage migrate")
    base = base or get_settings()
    return base.model_copy(update={name: getattr(row, name) for name in RUNTIME_FIELDS})


def save_group(db: Session, group: str, values: dict, expected_revision: int,
               *, actor_id: int | None, actor_name: str) -> int:
    if group not in GROUPS or set(values) != set(GROUPS[group]):
        raise ValueError("Invalid settings group")
    row = db.get(AppConfiguration, 1)
    if row is None:
        raise RuntimeError("Application configuration is not initialized")
    if row.revision != expected_revision:
        raise RevisionConflict("Settings changed in another session")
    old = {key: getattr(row, key) for key in GROUPS[group]}
    proposed = old | values
    for key in proposed:
        if key in {"app_name", "interface_language"}:
            continue
        try:
            proposed[key] = float(proposed[key]) if key in {
                "ping_batch_pause_seconds", "ping_project_pause_seconds", "ping_queue_poll_seconds"
            } else int(proposed[key])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid value for {key}") from exc
    if group == "interface":
        proposed["app_name"] = str(proposed["app_name"]).strip()
        proposed["interface_language"] = str(proposed["interface_language"]).upper()
        if not proposed["app_name"] or len(proposed["app_name"]) > 120 or any(ord(c) < 32 for c in proposed["app_name"]):
            raise ValueError("Application name must contain 1–120 printable characters")
        if proposed["interface_language"] not in {"RU", "EN"} or proposed["project_table_default_page_size"] not in {25, 50, 100, 250}:
            raise ValueError("Invalid interface setting")
    if group == "limits" and proposed["csv_import_max_bytes"] > max(2_097_152, row.csv_import_max_bytes):
        raise ValueError("Import file limit cannot exceed 2 MiB through the web interface")
    try:
        validated = RuntimeValues.model_validate({name: getattr(row, name) for name in RUNTIME_FIELDS} | proposed)
    except ValidationError as exc:
        raise ValueError("Invalid setting value") from exc
    changed = {key: [old[key], getattr(validated, key)] for key in old if old[key] != getattr(validated, key)}
    if not changed:
        return row.revision
    new_revision = row.revision + 1
    result = db.execute(update(AppConfiguration).where(AppConfiguration.id == 1,
        AppConfiguration.revision == expected_revision).values(
        **{key: pair[1] for key, pair in changed.items()}, revision=new_revision,
        updated_at=datetime.utcnow(), updated_by_user_id=actor_id))
    if result.rowcount != 1:
        db.rollback()
        raise RevisionConflict("Settings changed in another session")
    db.add(AppConfigurationAudit(actor_id=actor_id, actor_name=actor_name[:80], section=group,
                                 old_revision=expected_revision, new_revision=new_revision, changes=changed))
    db.commit()
    return new_revision


def replace_token(db: Session, expected_version: int, actor_id: int, *, disable: bool = False,
                  actor_name: str = "admin") -> str | None:
    previous = db.get(IntegrationCredential, 1)
    if previous is None:
        raise RuntimeError("Integration credential is not initialized")
    event = "token_disabled" if disable else "token_replaced" if previous.token_hash else "token_created"
    token = None if disable else secrets.token_urlsafe(32)
    result = db.execute(update(IntegrationCredential).where(IntegrationCredential.id == 1,
        IntegrationCredential.version == expected_version).values(
        token_hash=token_hash(token) if token else None, version=expected_version + 1,
        updated_at=datetime.utcnow()))
    if result.rowcount != 1:
        db.rollback()
        raise RevisionConflict("Token changed in another session")
    db.add(AppConfigurationAudit(actor_id=actor_id, actor_name=actor_name[:80], section=event,
                                 old_revision=expected_version, new_revision=expected_version + 1, changes={}))
    db.commit()
    return token
