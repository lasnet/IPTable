from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Folder(TimestampMixin, Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), nullable=True)

    parent: Mapped["Folder | None"] = relationship(remote_side="Folder.id", back_populates="children")
    children: Mapped[list["Folder"]] = relationship(back_populates="parent")
    projects: Mapped[list["Project"]] = relationship(back_populates="folder", cascade="all, delete-orphan")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(24), unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_columns: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_columns: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    interface_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    interface_theme: Mapped[str | None] = mapped_column(String(8), nullable=True)
    project_table_page_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", name="fk_users_role_id_roles", ondelete="RESTRICT"), index=True,
    )
    role: Mapped[Role | None] = relationship(lazy="joined")
    column_preferences: Mapped[list["ProjectColumnVisibility"]] = relationship(cascade="all, delete-orphan")

    @property
    def has_admin_access(self) -> bool:
        return self.is_admin or (self.role is not None and self.role.code == "admin")

    @property
    def can_create_inventory(self) -> bool:
        return self.has_admin_access or bool(self.role.can_create if self.role else self.can_create)

    @property
    def can_edit_inventory(self) -> bool:
        return self.has_admin_access or bool(self.role.can_edit if self.role else self.can_edit)

    @property
    def can_delete_inventory(self) -> bool:
        return self.has_admin_access or bool(self.role.can_delete if self.role else self.can_delete)

    @property
    def can_manage_project_columns(self) -> bool:
        return self.has_admin_access or bool(self.role.can_manage_columns if self.role else self.can_manage_columns)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_expires", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("folder_id", "name", name="uq_project_folder_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_ping_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ping_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hidden_columns: Mapped[list[str]] = mapped_column(JSON, server_default="[]", nullable=False)

    folder: Mapped[Folder] = relationship(back_populates="projects")
    ip_addresses: Mapped[list["IPAddress"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    custom_fields: Mapped[list["CustomField"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    column_preferences: Mapped[list["ProjectColumnVisibility"]] = relationship(cascade="all, delete-orphan")
    ping_jobs: Mapped[list["PingJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ping_schedules: Mapped[list["PingSchedule"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ip_history: Mapped[list["IPAddressHistory"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectColumnVisibility(Base):
    __tablename__ = "project_column_visibility"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, index=True,
    )
    hidden_columns: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class IPAddress(TimestampMixin, Base):
    __tablename__ = "ip_addresses"
    __table_args__ = (
        UniqueConstraint("project_id", "address", name="uq_ip_project_address"),
        UniqueConstraint("project_id", "ordinal", name="uq_ip_project_ordinal"),
        Index("ix_ip_addresses_search", "address", "hostname", "os", "asset_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    os: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    asset_type: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    custom_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ping_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    edit_lock_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edit_lock_token: Mapped[str | None] = mapped_column(String(43), nullable=True)
    edit_lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="ip_addresses")
    history: Mapped[list["IPAddressHistory"]] = relationship(back_populates="ip_address", cascade="all, delete-orphan")


class IPAddressHistory(TimestampMixin, Base):
    __tablename__ = "ip_address_history"
    __table_args__ = (
        Index("ix_ip_history_project_created", "project_id", "created_at"),
        Index("ix_ip_history_ip_created", "ip_address_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address_id: Mapped[int] = mapped_column(
        ForeignKey("ip_addresses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(180), nullable=False)
    field_label: Mapped[str] = mapped_column(String(180), nullable=False)
    old_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    new_value: Mapped[str] = mapped_column(Text, default="", nullable=False)

    project: Mapped[Project] = relationship(back_populates="ip_history")
    ip_address: Mapped[IPAddress] = relationship(back_populates="history")
    user: Mapped[User | None] = relationship()


class CustomField(TimestampMixin, Base):
    __tablename__ = "custom_fields"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_custom_field_project_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(140), nullable=False)
    field_type: Mapped[str] = mapped_column(String(40), default="text", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project: Mapped[Project] = relationship(back_populates="custom_fields")

    @property
    def visibility_key(self) -> str:
        # SQLite may reuse an ID after deletion. A new field starts visible.
        return f"custom:{self.id}:{self.created_at.isoformat()}"

    @property
    def drawer_input_name(self) -> str:
        return f"custom__{self.visibility_key}:{self.field_type}"


class PingSchedule(TimestampMixin, Base):
    __tablename__ = "ping_schedules"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_ping_schedule_project"),
        UniqueConstraint("folder_id", name="uq_ping_schedule_folder"),
        Index("ix_ping_schedules_due", "enabled", "next_run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="ping_schedules")
    folder: Mapped[Folder | None] = relationship()


class PingJob(TimestampMixin, Base):
    __tablename__ = "ping_jobs"
    __table_args__ = (
        Index("ix_ping_jobs_queue", "status", "run_after", "id"),
        Index("ix_ping_jobs_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    reason: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    project: Mapped[Project] = relationship(back_populates="ping_jobs")


class LoginRateLimitEvent(TimestampMixin, Base):
    __tablename__ = "login_rate_limit_events"
    __table_args__ = (
        Index("ix_login_rate_limit_events_lookup", "identity_hash", "created_at"),
        Index("ix_login_rate_limit_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class AppConfiguration(Base):
    __tablename__ = "app_configuration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    app_name: Mapped[str] = mapped_column(String(120), nullable=False)
    interface_language: Mapped[str] = mapped_column(String(8), nullable=False)
    interface_theme: Mapped[str] = mapped_column(String(8), nullable=False, server_default="blue")
    project_table_default_page_size: Mapped[int] = mapped_column(Integer, nullable=False)
    session_idle_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    login_rate_limit_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    login_rate_limit_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    login_rate_limit_lockout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ping_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ping_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    ping_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    ping_batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    ping_batch_pause_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    ping_project_pause_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    ping_queue_poll_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    ping_running_job_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_project_addresses: Mapped[int] = mapped_column(Integer, nullable=False)
    csv_import_max_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class AppConfigurationAudit(Base):
    __tablename__ = "app_configuration_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(80), nullable=False)
    section: Mapped[str] = mapped_column(String(40), nullable=False)
    old_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    new_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class IntegrationCredential(Base):
    __tablename__ = "integration_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
