"""ip address history

Revision ID: 20260515_0002
Revises: 20260515_0001
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260515_0002"
down_revision = "20260515_0001"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ip_address_history"):
        return

    op.create_table(
        "ip_address_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "ip_address_id",
            sa.Integer(),
            sa.ForeignKey("ip_addresses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=180), nullable=False),
        sa.Column("field_label", sa.String(length=180), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ip_address_history_project_id", "ip_address_history", ["project_id"])
    op.create_index("ix_ip_address_history_ip_address_id", "ip_address_history", ["ip_address_id"])
    op.create_index("ix_ip_address_history_address", "ip_address_history", ["address"])
    op.create_index("ix_ip_history_project_created", "ip_address_history", ["project_id", "created_at"])
    op.create_index("ix_ip_history_ip_created", "ip_address_history", ["ip_address_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ip_address_history")
