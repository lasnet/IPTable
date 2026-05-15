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

    @property
    def can_create_inventory(self) -> bool:
        return bool(self.is_admin or self.can_create)

    @property
    def can_edit_inventory(self) -> bool:
        return bool(self.is_admin or self.can_edit)

    @property
    def can_delete_inventory(self) -> bool:
        return bool(self.is_admin or self.can_delete)

    @property
    def can_manage_project_columns(self) -> bool:
        return bool(self.is_admin or self.can_manage_columns)


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

    folder: Mapped[Folder] = relationship(back_populates="projects")
    ip_addresses: Mapped[list["IPAddress"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    custom_fields: Mapped[list["CustomField"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ping_jobs: Mapped[list["PingJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ping_schedules: Mapped[list["PingSchedule"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    ip_history: Mapped[list["IPAddressHistory"]] = relationship(back_populates="project", cascade="all, delete-orphan")


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
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    project: Mapped[Project] = relationship(back_populates="ping_jobs")
