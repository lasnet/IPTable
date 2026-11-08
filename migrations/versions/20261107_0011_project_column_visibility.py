"""Store personal column visibility per project."""
from alembic import op
import sqlalchemy as sa

revision = "20261107_0011"
down_revision = "20261107_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_column_visibility",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("hidden_columns", sa.JSON(), nullable=False),
    )
    op.create_index("ix_project_column_visibility_project_id", "project_column_visibility", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_column_visibility")
