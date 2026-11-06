"""Store the installation's interface theme."""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0007"
down_revision = "20261103_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_configuration",
        sa.Column("interface_theme", sa.String(8), server_default="blue", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("app_configuration", "interface_theme")
