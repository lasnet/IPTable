"""initial schema

Revision ID: 20260515_0001
Revises:
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260515_0001"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table: str, name: str) -> bool:
    if not _table_exists(inspector, table):
        return False
    return name in {column["name"] for column in inspector.get_columns(table)}


def _add_column_if_missing(inspector: sa.Inspector, table: str, column: sa.Column) -> None:
    if not _column_exists(inspector, table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    bool_false = sa.text("false") if bind.dialect.name == "postgresql" else sa.text("0")
    bool_true = sa.text("true") if bind.dialect.name == "postgresql" else sa.text("1")

    if not _table_exists(inspector, "folders"):
        op.create_table(
            "folders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("folders.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name"),
        )
        op.create_index("ix_folders_name", "folders", ["name"])

    if not _table_exists(inspector, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=80), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("first_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("last_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=bool_false),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=bool_true),
            sa.Column("can_create", sa.Boolean(), nullable=False, server_default=bool_false),
            sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=bool_false),
            sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=bool_false),
            sa.Column("can_manage_columns", sa.Boolean(), nullable=False, server_default=bool_false),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("username"),
        )
        op.create_index("ix_users_username", "users", ["username"])
    else:
        _add_column_if_missing(inspector, "users", sa.Column("first_name", sa.String(length=120), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "users", sa.Column("last_name", sa.String(length=120), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "users", sa.Column("description", sa.Text(), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=bool_false))
        _add_column_if_missing(inspector, "users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=bool_true))
        _add_column_if_missing(inspector, "users", sa.Column("can_create", sa.Boolean(), nullable=False, server_default=bool_false))
        _add_column_if_missing(inspector, "users", sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=bool_false))
        _add_column_if_missing(inspector, "users", sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=bool_false))
        _add_column_if_missing(inspector, "users", sa.Column("can_manage_columns", sa.Boolean(), nullable=False, server_default=bool_false))
        _add_column_if_missing(inspector, "users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    if not _table_exists(inspector, "projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("folder_id", sa.Integer(), sa.ForeignKey("folders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("cidr", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_ping_started_at", sa.DateTime(), nullable=True),
            sa.Column("last_ping_finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("folder_id", "name", name="uq_project_folder_name"),
        )
        op.create_index("ix_projects_folder_id", "projects", ["folder_id"])
        op.create_index("ix_projects_name", "projects", ["name"])
    else:
        _add_column_if_missing(inspector, "projects", sa.Column("description", sa.Text(), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "projects", sa.Column("last_ping_started_at", sa.DateTime(), nullable=True))
        _add_column_if_missing(inspector, "projects", sa.Column("last_ping_finished_at", sa.DateTime(), nullable=True))

    if not _table_exists(inspector, "ip_addresses"):
        op.create_table(
            "ip_addresses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("address", sa.String(length=64), nullable=False),
            sa.Column("hostname", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("os", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("asset_type", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("custom_values", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("is_reachable", sa.Boolean(), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("ping_latency_ms", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "address", name="uq_ip_project_address"),
            sa.UniqueConstraint("project_id", "ordinal", name="uq_ip_project_ordinal"),
        )
        op.create_index("ix_ip_addresses_address", "ip_addresses", ["address"])
        op.create_index("ix_ip_addresses_project_id", "ip_addresses", ["project_id"])
        op.create_index("ix_ip_addresses_search", "ip_addresses", ["address", "hostname", "os", "asset_type"])
    else:
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("hostname", sa.String(length=255), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("os", sa.String(length=120), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("asset_type", sa.String(length=120), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("comment", sa.Text(), nullable=False, server_default=""))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("custom_values", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("is_reachable", sa.Boolean(), nullable=True))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("last_checked_at", sa.DateTime(), nullable=True))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
        _add_column_if_missing(inspector, "ip_addresses", sa.Column("ping_latency_ms", sa.Float(), nullable=True))

    if not _table_exists(inspector, "custom_fields"):
        op.create_table(
            "custom_fields",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("key", sa.String(length=140), nullable=False),
            sa.Column("field_type", sa.String(length=40), nullable=False, server_default="text"),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", "key", name="uq_custom_field_project_key"),
        )
        op.create_index("ix_custom_fields_project_id", "custom_fields", ["project_id"])
    else:
        _add_column_if_missing(inspector, "custom_fields", sa.Column("field_type", sa.String(length=40), nullable=False, server_default="text"))
        _add_column_if_missing(inspector, "custom_fields", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "ping_schedules"):
        op.create_table(
            "ping_schedules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
            sa.Column("folder_id", sa.Integer(), sa.ForeignKey("folders.id", ondelete="CASCADE"), nullable=True),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=bool_true),
            sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="3600"),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("project_id", name="uq_ping_schedule_project"),
            sa.UniqueConstraint("folder_id", name="uq_ping_schedule_folder"),
        )
        op.create_index("ix_ping_schedules_due", "ping_schedules", ["enabled", "next_run_at"])

    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "ping_jobs"):
        op.create_table(
            "ping_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("reason", sa.String(length=40), nullable=False, server_default="manual"),
            sa.Column("run_after", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ping_jobs_project_id", "ping_jobs", ["project_id"])
        op.create_index("ix_ping_jobs_queue", "ping_jobs", ["status", "run_after", "id"])
        op.create_index("ix_ping_jobs_project_status", "ping_jobs", ["project_id", "status"])


def downgrade() -> None:
    op.drop_table("ping_jobs")
    op.drop_table("ping_schedules")
