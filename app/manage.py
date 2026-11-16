"""Database migration and settings recovery commands."""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--runtime-role", help="Provision a dedicated role for the bundled PostgreSQL")
    sub.add_parser("restore-key", help="Install the installation key from standard input")
    sub.add_parser("reset-admin-password", help="Reset the protected administrator's password")
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
    if args.command in {"migrate", "restore-key"}:
        from app.core.installation_key import initialize_key
        key_file = os.environ.get("SECRET_KEY_FILE")
        try:
            if args.command == "restore-key":
                if not key_file:
                    parser.error("SECRET_KEY_FILE must be configured before restoring a key")
                previous_key = sys.stdin.read().removesuffix("\n")
                if not previous_key:
                    parser.error("The installation key backup is empty")
                # Respect any legacy override before writing to the volume.
                legacy_key = os.environ.get("SECRET_KEY")
                if legacy_key and legacy_key != previous_key:
                    parser.error("The backup key differs from SECRET_KEY in the environment")
                initialize_key(Path(key_file), previous_key)
                return
            if key_file:
                initialize_key(Path(key_file), os.environ.get("SECRET_KEY", ""))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        from app.core.config import get_settings
        from app.core.database import SessionLocal
        from app.services.auth import bootstrap_initial_admin
        from app.services.ping import ensure_default_project_schedules
        from app.services.settings import current, initialize
        settings = get_settings()
        with SessionLocal() as db:
            initialize(db)
            try:
                bootstrap_initial_admin(db, settings)
            except ValueError as exc:
                parser.error(str(exc))
            ensure_default_project_schedules(db, current(db, settings))
        if args.runtime_role and not os.environ.get("DATABASE_URL"):
            from app.core.database import engine
            from app.services.database_roles import provision_runtime_role
            if engine.dialect.name == "postgresql":
                try:
                    provision_runtime_role(engine, args.runtime_role, settings.secret_key)
                except Exception:
                    raise RuntimeError("Runtime database role provisioning failed; check the dedicated role name and migration permissions") from None
        return
    from app.core.database import SessionLocal
    if args.command == "reset-admin-password":
        from sqlalchemy import select
        from app.models import User
        from app.services.auth import hash_password, validate_admin_password
        from app.services.sessions import revoke_user_sessions

        if not sys.stdin.isatty():
            parser.error("Run this command in an interactive terminal")
        with SessionLocal() as db:
            admins = list(db.scalars(select(User).where(User.is_admin.is_(True)).limit(2)))
            if len(admins) != 1:
                parser.error("Expected exactly one protected installation administrator")
            password = getpass("New administrator password: ")
            try:
                validate_admin_password(password)
            except ValueError as exc:
                parser.error(str(exc))
            if password != getpass("Confirm password: "):
                parser.error("Passwords do not match")
            admin = admins[0]
            admin.password_hash = hash_password(password)
            revoke_user_sessions(db, admin.id)
            db.commit()
        print("Administrator password updated")
        return
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
