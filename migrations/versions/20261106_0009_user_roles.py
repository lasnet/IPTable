"""Add reusable roles and preserve each account's effective permissions."""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0009"
down_revision = "20261106_0008"
branch_labels = None
depends_on = None

PERMISSIONS = ("can_create", "can_edit", "can_delete", "can_manage_columns")
ROLE_FK = "fk_users_role_id_roles"


def upgrade() -> None:
    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(24), unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(300), unique=True, nullable=False),
        *(sa.Column(key, sa.Boolean(), nullable=False) for key in PERMISSIONS),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(ROLE_FK, "roles", ["role_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_users_role_id", ["role_id"])

    users = sa.table(
        "users", sa.column("id", sa.Integer()), sa.column("role_id", sa.Integer()),
        sa.column("is_admin", sa.Boolean()), *(sa.column(key, sa.Boolean()) for key in PERMISSIONS),
    )
    connection = op.get_bind()

    def add_role(name, flags, code=None):
        result = connection.execute(roles.insert().values(
            name=name, name_key=name.casefold(), code=code, **dict(zip(PERMISSIONS, flags)),
        ))
        return result.inserted_primary_key[0]

    admin_id = add_role("Administrator", (True,) * 4, "admin")
    by_permissions = {
        (False,) * 4: add_role("User", (False,) * 4, "user"),
        (True,) * 4: add_role("Editor", (True,) * 4, "editor"),
    }
    for flags in connection.execute(sa.select(*(users.c[key] for key in PERMISSIONS))
                                    .where(users.c.is_admin.is_(False)).distinct()).all():
        if flags not in by_permissions:
            code = "".join("1" if flag else "0" for flag in flags)
            by_permissions[flags] = add_role(f"Legacy access {code}", flags)
    connection.execute(users.update().where(users.c.is_admin.is_(True)).values(role_id=admin_id))
    for flags, role_id in by_permissions.items():
        condition = [users.c[key].is_(flag) for key, flag in zip(PERMISSIONS, flags)]
        connection.execute(users.update().where(users.c.is_admin.is_(False), *condition).values(role_id=role_id))


def downgrade() -> None:
    roles = sa.table("roles", sa.column("id"), sa.column("code"), *(sa.column(key) for key in PERMISSIONS))
    users = sa.table("users", sa.column("role_id"), sa.column("is_admin"), *(sa.column(key) for key in PERMISSIONS))
    connection = op.get_bind()
    for role in connection.execute(sa.select(roles)).mappings().all():
        connection.execute(users.update().where(users.c.role_id == role["id"]).values(
            is_admin=role["code"] == "admin", **{key: role[key] for key in PERMISSIONS},
        ))
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_role_id")
        batch.drop_constraint(ROLE_FK, type_="foreignkey")
        batch.drop_column("role_id")
    op.drop_table("roles")
