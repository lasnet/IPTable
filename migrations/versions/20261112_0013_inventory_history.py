"""Keep inventory history independently of folders, subnets and addresses."""

from alembic import op
import sqlalchemy as sa


revision = "20261112_0013"
down_revision = "20261107_0012"
branch_labels = None
depends_on = None

IP_COLUMNS = (
    "project_id", "ip_address_id", "address", "username", "field_name", "field_label", "old_value", "new_value", "created_at",
)


def upgrade() -> None:
    history = op.create_table(
        "inventory_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("folder_id", sa.Integer()),
        sa.Column("project_id", sa.Integer()),
        sa.Column("ip_address_id", sa.Integer()),
        sa.Column("folder_name", sa.String(120), nullable=False),
        sa.Column("subnet_name", sa.String(160), nullable=False),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("field_name", sa.String(180), nullable=False),
        sa.Column("field_label", sa.String(180), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=False),
        sa.Column("new_value", sa.Text(), nullable=False),
    )
    previous = sa.table("ip_address_history", *(sa.column(name) for name in ("id", "user_id", *IP_COLUMNS)))
    projects = sa.table("projects", sa.column("id"), sa.column("folder_id"), sa.column("name"), sa.column("cidr"))
    folders = sa.table("folders", sa.column("id"), sa.column("name"))
    users = sa.table("users", sa.column("id"))
    entries = sa.select(
        *(previous.c[name] for name in IP_COLUMNS),
        users.c.id,
        sa.literal("ip_updated"),
        projects.c.folder_id,
        sa.func.coalesce(folders.c.name, ""),
        sa.func.coalesce(projects.c.name, ""),
        sa.func.coalesce(projects.c.cidr, ""),
        sa.literal(""),
    ).select_from(
        previous.outerjoin(projects, previous.c.project_id == projects.c.id)
        .outerjoin(folders, projects.c.folder_id == folders.c.id)
        .outerjoin(users, previous.c.user_id == users.c.id)
    ).order_by(previous.c.id)
    op.execute(history.insert().from_select(
        [*IP_COLUMNS, "user_id", "action", "folder_id", "folder_name", "subnet_name", "cidr", "source_filename"], entries,
    ))
    op.create_index("ix_history_created", "inventory_history", ["created_at", "id"])
    for scope in ("folder", "project", "user"):
        op.create_index(f"ix_history_{scope}_created", "inventory_history", [f"{scope}_id", "created_at", "id"])
    op.drop_table("ip_address_history")


def downgrade() -> None:
    previous = op.create_table(
        "ip_address_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ip_address_id", sa.Integer(), sa.ForeignKey("ip_addresses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("username", sa.String(80), nullable=False, server_default=""),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("field_name", sa.String(180), nullable=False),
        sa.Column("field_label", sa.String(180), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    history = sa.table("inventory_history", *(sa.column(name) for name in ("id", "user_id", "action", *IP_COLUMNS)))
    addresses = sa.table("ip_addresses", sa.column("id"), sa.column("project_id"), sa.column("address"))
    projects = sa.table("projects", sa.column("id"))
    entries = sa.select(*(history.c[name] for name in IP_COLUMNS), history.c.user_id, history.c.created_at).select_from(
        history.join(addresses, sa.and_(
            history.c.ip_address_id == addresses.c.id,
            history.c.project_id == addresses.c.project_id,
            history.c.address == addresses.c.address,
        )).join(projects, history.c.project_id == projects.c.id)
    ).where(history.c.action == "ip_updated").order_by(history.c.id)
    op.execute(previous.insert().from_select([*IP_COLUMNS, "user_id", "updated_at"], entries))
    for column in ("project_id", "ip_address_id", "address"):
        op.create_index(f"ix_ip_address_history_{column}", "ip_address_history", [column])
    op.create_index("ix_ip_history_project_created", "ip_address_history", ["project_id", "created_at"])
    op.create_index("ix_ip_history_ip_created", "ip_address_history", ["ip_address_id", "created_at"])
    op.drop_table("inventory_history")
