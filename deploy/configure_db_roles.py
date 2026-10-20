"""Provision a dedicated score-it DB. Dry run unless --apply is explicit.

Existing application tables retain their data and move to the migration role.
Never print SQL, passwords, connection strings or database exception messages.
"""

import argparse
import os
import re

import psycopg
from psycopg import sql


class ConfigurationError(Exception):
    pass


def configure(environ, apply=False):
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DB_USER", "DB_PASSWORD",
                "DB_MIGRATION_USER", "DB_MIGRATION_PASSWORD", "DB_HOST")
    if any(not environ.get(key) for key in required):
        raise ConfigurationError("Fill all database role settings before running this command.")
    database, admin, runtime, migration = (environ[k] for k in
        ("POSTGRES_DB", "POSTGRES_USER", "DB_USER", "DB_MIGRATION_USER"))
    for name in (runtime, migration):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
            raise ConfigurationError("New role names must use lower-case ASCII identifiers.")
    if len({admin, runtime, migration}) != 3 or {runtime, migration} & {"postgres", "public"}:
        raise ConfigurationError("Admin, migration and runtime roles must be distinct.")
    if len({environ[k] for k in ("POSTGRES_PASSWORD", "DB_PASSWORD", "DB_MIGRATION_PASSWORD")}) != 3:
        raise ConfigurationError("Use a different password for each database role.")

    with psycopg.connect(host=environ["DB_HOST"], port=environ.get("DB_PORT", "5432"),
                        dbname=database, user=admin, password=environ["POSTGRES_PASSWORD"],
                        connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cursor.fetchone()[0]:
                raise ConfigurationError("This one-time command requires the dedicated DB administrator.")
            cursor.execute("SELECT pg_advisory_xact_lock(726173, 1001)")
            cursor.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('public', 'information_schema') AND nspname NOT LIKE 'pg_%'")
            if cursor.fetchone():
                raise ConfigurationError("Unexpected schema: review this database manually.")
            cursor.execute("""SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')""")
            relations = cursor.fetchall()
            if any(not name.startswith(("auth_", "django_", "poker_", "axes_"))
                   or kind not in ("r", "p", "S") or owner not in (admin, migration)
                   for name, kind, owner in relations):
                raise ConfigurationError("Unexpected objects/owners: this command is for a dedicated score-it DB.")
            cursor.execute("""SELECT 1 FROM pg_auth_members m JOIN pg_roles r
                ON r.oid IN (m.roleid, m.member) WHERE r.rolname IN (%s, %s) LIMIT 1""", (runtime, migration))
            if cursor.fetchone():
                raise ConfigurationError("Application roles have memberships; review and remove them manually.")
            # Never strip privileges from an unrelated pre-existing administrator.
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname IN (%s, %s) AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)", (runtime, migration))
            if cursor.fetchone():
                raise ConfigurationError("A selected new role is already privileged; choose a dedicated role name.")
            print(f"DATABASE={database} RUNTIME={runtime} MIGRATIONS={migration} OBJECTS={len(relations)}")
            if not apply:
                print("DRY_RUN: no changes. Stop web, then rerun with --apply.")
                return

            for name, password_key in ((runtime, "DB_PASSWORD"), (migration, "DB_MIGRATION_PASSWORD")):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,))
                if not cursor.fetchone():
                    cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(name)))
                encrypted = connection.pgconn.encrypt_password(
                    environ[password_key].encode(), name.encode(), b"scram-sha-256").decode()
                cursor.execute(sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(name), sql.Literal(encrypted)))
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}, {}").format(sql.Identifier(database), sql.Identifier(runtime), sql.Identifier(migration)))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(sql.Identifier(database), sql.Identifier(runtime), sql.Identifier(migration)))
            cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(migration)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(runtime)))
            # Grant nothing through PUBLIC; only the migration role owns DDL objects.
            cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
            cursor.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
            for name, kind, _owner in sorted(relations, key=lambda relation: relation[1] == "S"):
                if kind == "S":
                    cursor.execute("""SELECT 1 FROM pg_depend d
                        WHERE d.objid=%s::regclass AND d.deptype IN ('a', 'i') LIMIT 1""", ("public." + name,))
                    if cursor.fetchone():
                        # Owned sequences followed their table's ownership above.
                        continue
                cursor.execute(sql.SQL("ALTER {} public.{} OWNER TO {}").format(
                    sql.SQL("SEQUENCE" if kind == "S" else "TABLE"), sql.Identifier(name), sql.Identifier(migration)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(migration), sql.Identifier(runtime)))
            cursor.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(sql.Identifier(migration), sql.Identifier(runtime)))
        print("ROLES_CONFIGURED: restart web with DB_USER; run migrate using the migrate service.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        configure(os.environ, args.apply)
    except (ConfigurationError, psycopg.Error) as exc:
        message = str(exc) if isinstance(exc, ConfigurationError) else "Database operation failed; no SQL or credentials are printed."
        parser.exit(1, message + "\n")
