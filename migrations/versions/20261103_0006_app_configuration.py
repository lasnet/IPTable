"""Persist application settings and pin ping job leases."""
from alembic import op
import sqlalchemy as sa

revision = "20261103_0006"
down_revision = "20261101_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ping_jobs", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.create_table(
        "app_configuration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config_schema_version", sa.Integer(), nullable=False),
        sa.Column("initialized_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("app_name", sa.String(120), nullable=False),
        sa.Column("interface_language", sa.String(8), nullable=False),
        *[sa.Column(name, sa.Float() if name in {
            "ping_batch_pause_seconds", "ping_project_pause_seconds", "ping_queue_poll_seconds"
        } else sa.Integer(), nullable=False) for name in (
            "project_table_default_page_size", "session_idle_timeout_seconds",
            "login_rate_limit_attempts", "login_rate_limit_window_seconds", "login_rate_limit_lockout_seconds",
            "ping_interval_seconds", "ping_timeout_seconds", "ping_concurrency", "ping_batch_size",
            "ping_batch_pause_seconds", "ping_project_pause_seconds", "ping_queue_poll_seconds",
            "ping_running_job_timeout_seconds", "max_project_addresses", "csv_import_max_bytes",
        )],
    )
    op.create_table(
        "app_configuration_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_name", sa.String(80), nullable=False),
        sa.Column("section", sa.String(40), nullable=False),
        sa.Column("old_revision", sa.Integer(), nullable=False),
        sa.Column("new_revision", sa.Integer(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
    )
    op.create_table(
        "integration_credential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("integration_credential")
    op.drop_table("app_configuration_audit")
    op.drop_table("app_configuration")
    op.drop_column("ping_jobs", "lease_expires_at")
