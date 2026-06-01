"""drop legacy ip address tags column

Revision ID: 20260601_0004
Revises: 20260528_0003
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_0004"
down_revision = "20260528_0003"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table: str, name: str) -> bool:
    if not _table_exists(inspector, table):
        return False
    return name in {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ip_addresses") and _column_exists(inspector, "ip_addresses", "tags"):
        op.drop_column("ip_addresses", "tags")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ip_addresses") and not _column_exists(inspector, "ip_addresses", "tags"):
        op.add_column(
            "ip_addresses",
            sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
