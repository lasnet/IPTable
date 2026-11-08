"""Add expiring edit locks to IP rows."""
from alembic import op
import sqlalchemy as sa

revision = "20261107_0010"
down_revision = "20261106_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ip_addresses", sa.Column("edit_lock_user_id", sa.Integer()))
    op.add_column("ip_addresses", sa.Column("edit_lock_token", sa.String(43)))
    op.add_column("ip_addresses", sa.Column("edit_lock_expires_at", sa.DateTime()))


def downgrade() -> None:
    for column in ("edit_lock_expires_at", "edit_lock_token", "edit_lock_user_id"):
        op.drop_column("ip_addresses", column)
