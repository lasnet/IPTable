"""Add per-user interface preferences; NULL inherits the installation default."""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0008"
down_revision = "20261106_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("interface_language", sa.String(8), nullable=True))
    op.add_column("users", sa.Column("interface_theme", sa.String(8), nullable=True))
    op.add_column("users", sa.Column("project_table_page_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "project_table_page_size")
    op.drop_column("users", "interface_theme")
    op.drop_column("users", "interface_language")
