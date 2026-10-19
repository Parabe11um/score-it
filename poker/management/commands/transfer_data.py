import hashlib
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from poker.data_transfer import (
    encode, import_records, inventory, make_bundle, require_empty_target,
    snapshot, validate_bundle, verify_records,
)


class Command(BaseCommand):
    help = "Перенос данных с сохранением ID: export, import (по умолчанию DRY_RUN), verify."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("export", "import", "verify"))
        parser.add_argument("--database", default="default")
        parser.add_argument("--file", required=True)
        parser.add_argument("--expected-sha256")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        action, database = options["action"], options["database"]
        path = Path(options["file"])
        if options["apply"] and action != "import":
            raise CommandError("--apply используется только с import.")
        if action == "export":
            with snapshot(database):
                bundle = make_bundle(database)
            raw = (encode(bundle) + "\n").encode("utf-8")
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise CommandError("Файл уже существует; выберите новое имя.") from exc
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            summary = inventory(bundle["records"])
        else:
            raw = path.read_bytes()
            actual_sha = hashlib.sha256(raw).hexdigest()
            if not options["expected_sha256"] or actual_sha != options["expected_sha256"]:
                raise CommandError("Укажите совпадающий --expected-sha256 из результата экспорта.")
            try:
                bundle = json.loads(raw)
                records = validate_bundle(bundle, database)
            except (ValueError, TypeError, KeyError, LookupError) as exc:
                raise CommandError("Некорректный файл переноса.") from exc
            if action == "verify":
                with snapshot(database):
                    summary = verify_records(records, database)
                self.stdout.write("VERIFY_OK")
            elif options["apply"]:
                summary = import_records(records, database)
                self.stdout.write("IMPORT_VERIFIED")
            else:
                require_empty_target(database)
                summary = inventory(records)
                self.stdout.write("DRY_RUN_OK — данные не записаны; импорт не проверен до выполнения APPLY.")
        self.stdout.write("SHA256=" + hashlib.sha256(raw).hexdigest())
        for label, details in summary.items():
            self.stdout.write(f"{label}: {details['count']}")
        self.stdout.write("TOTAL=" + str(sum(details["count"] for details in summary.values())))
