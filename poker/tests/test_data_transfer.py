import hashlib
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase

from poker.data_transfer import (
    capture, import_records, make_bundle, require_empty_target,
    validate_bundle, verify_records,
)
from poker.models import Project, Sprint, SprintTask, Task


class DataTransferTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(pk=101, username="transfer-owner", password="secret")
        group = Group.objects.create(pk=91, name="Planners")
        group.permissions.add(Permission.objects.filter(content_type__app_label="poker").first())
        user.groups.add(group)
        project = Project.objects.create(pk=51, owner=user, name="Проект")
        task = Task.objects.create(pk=71, project=project, number="ABS-1", title="Задача", imported_estimate="12.50")
        Task.objects.filter(pk=71).update(created_at=datetime(2026, 10, 19, 12, 0, 0, 123456, tzinfo=timezone.utc))
        sprint = Sprint.objects.create(pk=81, project=project, name="2026.21.abs")
        SprintTask.objects.create(sprint=sprint, task=task, position=1)
        self.bundle = make_bundle("default")

    def test_export_keeps_ids_hashes_and_exact_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "transfer.json"
            output = StringIO()
            call_command("transfer_data", "export", file=str(path), stdout=output)
            data = json.loads(path.read_text())
            owner = next(row for row in data["records"] if row["model"] == "auth.user" and row["pk"] == 101)
            self.assertTrue(owner["fields"]["password"].startswith("pbkdf2_sha256$"))
            self.assertNotIn(owner["fields"]["password"], output.getvalue())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), output.getvalue())
            self.assertEqual(validate_bundle(data, "default"), capture("default"))
            task = next(row for row in data["records"] if row["model"] == "poker.task")
            self.assertEqual(task["fields"]["created_at"], "2026-10-19T12:00:00.123456Z")
            with self.assertRaises(CommandError):
                call_command("transfer_data", "export", file=str(path), stdout=StringIO())

    def test_tampered_file_rejected_before_loading(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "transfer.json"
            path.write_text(json.dumps(self.bundle))
            with self.assertRaisesMessage(CommandError, "--expected-sha256"):
                call_command("transfer_data", "import", file=str(path), expected_sha256="0" * 64)

    def test_schema_mismatch_rejected(self):
        self.bundle["schema"] = {}
        with self.assertRaises(CommandError):
            validate_bundle(self.bundle, "default")

    def test_field_changes_detected_even_when_counts_match(self):
        Task.objects.filter(pk=71).update(title="Изменено")
        with self.assertRaisesMessage(CommandError, "poker.task"):
            verify_records(self.bundle["records"], "default")

    def test_duplicate_ids_rejected(self):
        self.bundle["records"].append(self.bundle["records"][0])
        with self.assertRaises(CommandError):
            validate_bundle(self.bundle, "default")

    def test_nonempty_database_cannot_be_imported(self):
        with self.assertRaises(CommandError):
            require_empty_target("default")

    @skipUnless(connection.vendor == "postgresql", "Real PostgreSQL import is checked in CI")
    def test_postgresql_roundtrip_preserves_all_data_and_resets_sequences(self):
        call_command("flush", interactive=False, verbosity=0)
        records = validate_bundle(self.bundle, "default")
        import_records(records, "default")
        verify_records(records, "default")
        self.assertTrue(get_user_model().objects.get(pk=101).check_password("secret"))
        self.assertEqual(get_user_model().objects.get(pk=101).groups.get().pk, 91)
        self.assertEqual(str(Task.objects.get(pk=71).imported_estimate), "12.50")
        self.assertEqual(Task.objects.get(pk=71).created_at.microsecond, 123456)
        self.assertEqual(SprintTask.objects.get(task_id=71).sprint_id, 81)
        self.assertGreater(get_user_model().objects.create_user(username="next").pk, 101)
        self.assertGreater(Project.objects.create(owner_id=101, name="Next").pk, 51)

    @skipUnless(connection.vendor == "postgresql", "Real PostgreSQL import is checked in CI")
    def test_import_rolls_back_if_verification_fails(self):
        call_command("flush", interactive=False, verbosity=0)
        original_types = list(ContentType.objects.values_list("pk", "app_label", "model"))
        with patch("poker.data_transfer.verify_records", side_effect=CommandError("mismatch")):
            with self.assertRaises(CommandError):
                import_records(self.bundle["records"], "default")
        self.assertFalse(Project.objects.exists())
        self.assertEqual(list(ContentType.objects.values_list("pk", "app_label", "model")), original_types)
