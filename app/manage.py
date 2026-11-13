"""Database migration and settings recovery commands."""
import argparse
import json
import os

from alembic import command
from alembic.config import Config


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--runtime-role", help="Provision a dedicated role for the bundled PostgreSQL")
    settings = sub.add_parser("settings")
    actions = settings.add_subparsers(dest="action", required=True)
    actions.add_parser("show")
    set_command = actions.add_parser("set")
    set_command.add_argument("--group", required=True, choices=("interface", "ping", "security", "limits"))
    set_command.add_argument("--expected-revision", required=True, type=int)
    set_command.add_argument("values", nargs="+", help="KEY=VALUE")
    rollback = actions.add_parser("rollback")
    rollback.add_argument("--revision", required=True, type=int)
    rollback.add_argument("--expected-revision", required=True, type=int)
    args = parser.parse_args()
    if args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        from app.core.database import SessionLocal
        from app.services.settings import initialize
        with SessionLocal() as db:
            initialize(db)
        if args.runtime_role and not os.environ.get("DATABASE_URL"):
            from app.core.config import get_settings
            from app.core.database import engine
            from app.services.database_roles import provision_runtime_role
            if engine.dialect.name == "postgresql":
                try:
                    provision_runtime_role(engine, args.runtime_role, get_settings().secret_key)
                except Exception:
                    raise RuntimeError("Runtime database role provisioning failed; check the dedicated role name and migration permissions") from None
        return
    from app.core.database import SessionLocal
    from app.models import AppConfiguration, AppConfigurationAudit
    from app.services.settings import GROUPS, save_group
    with SessionLocal() as db:
        row = db.get(AppConfiguration, 1)
        if row is None:
            parser.error("Configuration is not initialized")
        if args.action == "show":
            print(json.dumps({"revision": row.revision, **{
                key: getattr(row, key) for keys in GROUPS.values() for key in keys}}, ensure_ascii=False, indent=2))
        elif args.action == "set":
            values = dict(item.split("=", 1) for item in args.values)
            if set(values) != set(GROUPS[args.group]):
                parser.error("Supply exactly the fields in the selected group")
            print(save_group(db, args.group, values, args.expected_revision,
                             actor_id=None, actor_name="system:cli"))
        else:
            audit = db.query(AppConfigurationAudit).filter(
                AppConfigurationAudit.new_revision > args.revision,
                AppConfigurationAudit.new_revision <= args.expected_revision,
            ).order_by(AppConfigurationAudit.new_revision.desc()).all()
            if row.revision != args.expected_revision or not audit:
                parser.error("Revision mismatch or no changes to revert")
            # Reverse one section at a time so the same validation and audit path applies.
            for entry in audit:
                if entry.section not in GROUPS or not entry.changes:
                    continue
                values = {key: getattr(row, key) for key in GROUPS[entry.section]}
                values.update({key: pair[0] for key, pair in entry.changes.items()})
                save_group(db, entry.section, values, row.revision,
                           actor_id=None, actor_name="system:cli")
                db.refresh(row)
            print(row.revision)


if __name__ == "__main__":
    main()
