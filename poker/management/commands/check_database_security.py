from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Fail if the current PostgreSQL runtime role has administrative or DDL privileges."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("This check requires PostgreSQL.")
        with connection.cursor() as cursor:
            cursor.execute("""SELECT rolname FROM pg_roles WHERE
                (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
                AND (rolname = current_user OR pg_has_role(current_user, oid, 'MEMBER'))""")
            privileged = cursor.fetchall()
            cursor.execute("""SELECT
                has_database_privilege(current_user, current_database(), 'CREATE'),
                has_database_privilege(current_user, current_database(), 'TEMP'),
                has_schema_privilege(current_user, 'public', 'CREATE'),
                EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname='public' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))""")
            ddl = cursor.fetchone()
        if privileged or any(ddl):
            raise CommandError("Runtime role has administrative, ownership, schema CREATE or TEMP privileges.")
        self.stdout.write("DATABASE_SECURITY_OK: no administrative or DDL privileges.")
