"""database-backed login rate limit

Revision ID: 20260528_0003
Revises: 20260515_0002
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260528_0003"
down_revision = "20260515_0002"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "login_rate_limit_events"):
        op.create_table(
            "login_rate_limit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("identity_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_login_rate_limit_events_identity_hash", "login_rate_limit_events", ["identity_hash"])
        op.create_index(
            "ix_login_rate_limit_events_lookup",
            "login_rate_limit_events",
            ["identity_hash", "created_at"],
        )
        op.create_index("ix_login_rate_limit_events_created", "login_rate_limit_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "login_rate_limit_events"):
        op.drop_table("login_rate_limit_events")
