"""CI-only end-to-end transfer; refuses to touch a non-CI PostgreSQL database."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent.parent


def run(environment, *arguments):
    subprocess.run([sys.executable, "manage.py", *arguments], cwd=ROOT, env=environment, check=True)


def main():
    target = dict(os.environ)
    if target.get("POSTGRES_DB") != "score_it_ci" or target.get("DB_ENGINE") != "postgresql":
        raise SystemExit("This check requires the disposable score_it_ci PostgreSQL database.")
    with TemporaryDirectory() as directory:
        source = dict(target, DB_ENGINE="sqlite", DATABASE_PATH=str(Path(directory) / "source.sqlite3"))
        run(source, "migrate", "--noinput", "--verbosity", "0")
        run(source, "shell", "-c", """
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from poker.models import Project, Task, Sprint, SprintTask
from datetime import datetime, timezone
owner = get_user_model().objects.create_user(pk=101, username='transfer-owner', password='ci-test-password')
group = Group.objects.create(pk=91, name='Планировщики')
group.permissions.add(Permission.objects.filter(content_type__app_label='poker').first())
owner.groups.add(group)
project = Project.objects.create(pk=51, owner=owner, name='Проект')
task = Task.objects.create(pk=71, project=project, number='ABS-1', title='Задача', imported_estimate='12.50')
Task.objects.filter(pk=71).update(created_at=datetime(2026, 10, 19, 12, 0, 0, 123456, tzinfo=timezone.utc))
sprint = Sprint.objects.create(pk=81, project=project, name='2026.21.abs')
SprintTask.objects.create(sprint=sprint, task=task, position=1)
""")
        path = Path(directory) / "transfer.json"
        run(source, "transfer_data", "export", "--file", str(path))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        run(target, "migrate", "--noinput", "--verbosity", "0")
        common = ("--file", str(path), "--expected-sha256", sha)
        run(target, "transfer_data", "import", *common)
        run(target, "transfer_data", "import", *common, "--apply")
        run(target, "transfer_data", "verify", *common)
        run(target, "shell", "-c", """
from django.contrib.auth import get_user_model
from poker.models import Project, Task
assert get_user_model().objects.get(pk=101).check_password('ci-test-password')
assert get_user_model().objects.get(pk=101).groups.get().pk == 91
assert Task.objects.get(pk=71).created_at.microsecond == 123456
assert get_user_model().objects.create_user(username='after-transfer').pk > 101
assert Project.objects.create(owner_id=101, name='После переноса').pk > 51
print('SQLITE_TO_POSTGRES_OK')
""")


if __name__ == "__main__":
    main()
