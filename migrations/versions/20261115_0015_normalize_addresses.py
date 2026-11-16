"""Normalize legacy IPv4 ordinals once during upgrade."""

from ipaddress import IPv4Address, IPv4Network

from alembic import op
import sqlalchemy as sa


revision = "20261115_0015"
down_revision = "20261115_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    projects = sa.table("projects", sa.column("id", sa.Integer()), sa.column("cidr", sa.String()))
    addresses = sa.table(
        "ip_addresses", sa.column("id", sa.Integer()), sa.column("project_id", sa.Integer()),
        sa.column("ordinal", sa.Integer()), sa.column("address", sa.String()),
    )
    set_ordinal = addresses.update().where(addresses.c.id == sa.bindparam("address_id")).values(
        ordinal=sa.bindparam("new_ordinal"),
    )

    last_project_id = None
    while True:
        page = sa.select(projects).order_by(projects.c.id).limit(100)
        if last_project_id is not None:
            page = page.where(projects.c.id > last_project_id)
        batch = db.execute(page).all()
        if not batch:
            break
        last_project_id = batch[-1].id
        for project in batch:
            network = IPv4Network(project.cidr, strict=False)
            if network.prefixlen < 31:
                db.execute(addresses.delete().where(
                    addresses.c.project_id == project.id,
                    addresses.c.address.in_([str(network.network_address), str(network.broadcast_address)]),
                ))
            rows = db.execute(sa.select(addresses).where(addresses.c.project_id == project.id)).all()
            ordered = sorted(rows, key=lambda row: IPv4Address(row.address))
            changed = [
                {"address_id": row.id, "new_ordinal": ordinal}
                for ordinal, row in enumerate(ordered, start=1) if row.ordinal != ordinal
            ]
            if changed:
                first_temporary = min(0, min(row.ordinal for row in rows)) - 1
                db.execute(set_ordinal, [
                    {"address_id": row["address_id"], "new_ordinal": first_temporary - index}
                    for index, row in enumerate(changed)
                ])
                db.execute(set_ordinal, changed)


def downgrade() -> None:
    # Ordinals and reserved addresses were already normalized at runtime in prior releases.
    pass
