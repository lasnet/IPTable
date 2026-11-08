"""Store column visibility shared by all users of a project."""
from alembic import op
import sqlalchemy as sa

revision = "20261107_0012"
down_revision = "20261107_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("hidden_columns", sa.JSON(), server_default="[]", nullable=False))


def downgrade() -> None:
    op.drop_column("projects", "hidden_columns")
