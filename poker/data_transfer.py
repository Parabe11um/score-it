"""Portable, ID-preserving database transfer for a stopped score-it instance.

The bundle contains password hashes and session keys. Never log its contents.
"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json

from django.apps import apps
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.migrations.executor import MigrationExecutor


class TransferJSONEncoder(DjangoJSONEncoder):
    def default(self, value):
        # Django's default JSON encoder truncates datetime microseconds to
        # milliseconds. A database transfer must retain the complete value.
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        return super().default(value)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def models_to_transfer():
    return sorted(
        (model for model in apps.get_models() if model._meta.managed and not model._meta.proxy),
        key=lambda model: model._meta.label_lower,
    )


def schema():
    result = {}
    for model in models_to_transfer():
        fields = []
        for field in model._meta.local_fields + model._meta.local_many_to_many:
            related = field.remote_field.model if field.is_relation else None
            fields.append({
                "name": field.name,
                "type": field.get_internal_type(),
                "null": field.null,
                "max_length": field.max_length,
                "max_digits": getattr(field, "max_digits", None),
                "decimal_places": getattr(field, "decimal_places", None),
                "related": related._meta.label_lower if related else None,
            })
        result[model._meta.label_lower] = fields
    return result


def check_schema(database):
    connection = connections[database]
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise CommandError("Есть неприменённые миграции. Сначала выполните migrate.")
    expected = {"django_migrations"}
    for model in apps.get_models(include_auto_created=True):
        if model._meta.managed and not model._meta.proxy:
            expected.add(model._meta.db_table)
    extra = set(connection.introspection.table_names()) - expected
    if extra:
        raise CommandError("Найдены неизвестные таблицы; перенос остановлен: " + ", ".join(sorted(extra)))
    return [list(node) for node in sorted(executor.loader.graph.nodes)]


@contextmanager
def snapshot(database):
    connection = connections[database]
    previous = None
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA query_only")
            previous = cursor.fetchone()[0]
            cursor.execute("PRAGMA query_only=ON")
    try:
        with transaction.atomic(using=database):
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            yield
    finally:
        if previous is not None:
            with connection.cursor() as cursor:
                cursor.execute(f"PRAGMA query_only={int(previous)}")


def canonical_records(records):
    result = []
    seen = set()
    for record in records:
        label = record["model"]
        model = apps.get_model(label)
        record = {"model": label, "pk": record["pk"], "fields": dict(record["fields"])}
        key = (label, str(record["pk"]))
        if record["pk"] is None or key in seen:
            raise CommandError("В файле отсутствует ID или повторяется объект.")
        seen.add(key)
        for field in model._meta.local_many_to_many:
            if field.name in record["fields"]:
                record["fields"][field.name] = sorted(record["fields"][field.name], key=encode)
        result.append(record)
    return sorted(result, key=lambda item: (item["model"], str(item["pk"])))


def capture(database):
    records = []
    for model in models_to_transfer():
        # JSON normalizes dates, UUID and Decimal values identically on both engines.
        records.extend(json.loads(serializers.serialize(
            "json", model._base_manager.using(database).order_by(model._meta.pk.name),
            use_natural_foreign_keys=False, use_natural_primary_keys=False,
            cls=TransferJSONEncoder,
        )))
    return canonical_records(records)


def inventory(records):
    result = {model._meta.label_lower: [] for model in models_to_transfer()}
    for record in records:
        result[record["model"]].append(record)
    return {label: {"count": len(items), "sha256": digest(items)} for label, items in result.items()}


def make_bundle(database):
    return {
        "format": "score-it-transfer-v1",
        "source_engine": connections[database].vendor,
        "migrations": check_schema(database),
        "schema": schema(),
        "records": capture(database),
    }


def validate_bundle(bundle, database):
    if bundle.get("format") != "score-it-transfer-v1":
        raise CommandError("Неизвестный формат переноса.")
    if bundle.get("schema") != schema() or bundle.get("migrations") != check_schema(database):
        raise CommandError("Версии моделей или миграций источника и назначения различаются.")
    records = canonical_records(bundle["records"])
    allowed = set(schema())
    for record in records:
        if record["model"] not in allowed:
            raise CommandError("В файле неизвестная модель.")
        model = apps.get_model(record["model"])
        fields = {field.name for field in model._meta.local_fields if field.serialize}
        fields.update(
            field.name for field in model._meta.local_many_to_many
            if field.serialize and field.remote_field.through._meta.auto_created
        )
        if set(record["fields"]) != fields:
            raise CommandError("Неполный набор полей: " + record["model"])
    return records


def require_empty_target(database):
    if connections[database].vendor != "postgresql":
        raise CommandError("Импорт разрешён только в PostgreSQL.")
    for model in apps.get_models(include_auto_created=True):
        if not model._meta.managed or model._meta.proxy:
            continue
        # Only the metadata generated by migrate is allowed before a first import.
        if model._meta.label_lower in {"contenttypes.contenttype", "auth.permission"}:
            continue
        if model._base_manager.using(database).exists():
            raise CommandError("База назначения не пуста: " + model._meta.label_lower)


def verify_records(records, database):
    expected, actual = inventory(records), inventory(capture(database))
    different = [label for label in expected if expected[label] != actual[label]]
    if different:
        raise CommandError("Не совпадают данные: " + ", ".join(different))
    return actual


def import_records(records, database):
    connection = connections[database]
    if connection.vendor != "postgresql":
        raise CommandError("Импорт разрешён только в PostgreSQL.")
    with transaction.atomic(using=database):
        # Block concurrent writes, then recheck emptiness under the same lock.
        with connection.cursor() as cursor:
            tables = ", ".join(connection.ops.quote_name(name) for name in sorted(connection.introspection.table_names()))
            cursor.execute(f"LOCK TABLE {tables} IN EXCLUSIVE MODE")
        require_empty_target(database)
        apps.get_model("auth.Permission").objects.using(database).all().delete()
        apps.get_model("contenttypes.ContentType").objects.using(database).all().delete()
        for obj in serializers.deserialize("json", encode(records), using=database):
            # raw save preserves original timestamps; ID-based references include
            # permissions, content types, admin log, users and all voting tokens.
            obj.save(using=database)
        connection.check_constraints()
        result = verify_records(records, database)
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(), models_to_transfer()):
                cursor.execute(statement)
    apps.get_model("contenttypes.ContentType").objects.clear_cache()
    return result
