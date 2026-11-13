"""Provision the bundled database runtime role using the migration connection."""
from psycopg import sql
from sqlalchemy import text

from app.core.config import runtime_database_password, runtime_database_role_name


def validate_runtime_role(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        privileged = connection.execute(text(
            "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )).scalar_one()
    if privileged:
        raise RuntimeError("Use a non-administrator DATABASE_URL for web/worker and a separate MIGRATION_DATABASE_URL")


def provision_runtime_role(engine, role: str, secret_key: str) -> None:
    if not role or len(role.encode()) > 63:
        raise ValueError("POSTGRES_APP_USER must contain 1–63 bytes")
    with engine.begin() as connection:
        cursor = connection.connection.driver_connection.cursor()
        database, owner = cursor.execute("SELECT current_database(), current_user").fetchone()
        if role == "auto":
            role = runtime_database_role_name(owner, database)
        password = runtime_database_password(secret_key, role)
        if role == owner or role.startswith("pg_"):
            raise ValueError("POSTGRES_APP_USER must be a dedicated non-administrator role")
        marker = "IPtable runtime role for database " + database
        cursor.execute("SELECT pg_advisory_xact_lock(4804684, 2)")
        existing = cursor.execute(
            "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s", (role,),
        ).fetchone()
        if existing is not None and existing[0] != marker:
            raise ValueError("POSTGRES_APP_USER already exists and is not managed by this installation")
        operation = sql.SQL("CREATE ROLE") if existing is None else sql.SQL("ALTER ROLE")
        cursor.execute(sql.SQL("{} {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                               "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                                   operation, sql.Identifier(role), sql.Literal(password)))
        cursor.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(role), sql.Literal(marker)))
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT "
                               "SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(role)))
        cursor.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT "
                               "USAGE, SELECT ON SEQUENCES TO {}").format(sql.Identifier(role)))
        cursor.close()
