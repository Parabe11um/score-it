"""Reject SQLite in lower layers, stale package metadata and changed app runtime contracts."""
import copy
from pathlib import Path
import tempfile
import unittest

from check_sqlite_payload import STATUS, check_archive, check_app_metadata
from runtime_policy import APP_HEALTHCHECK
from test_caddy_payload import layer_bytes, saved_image

IMAGE = "score-it:sqlite-payload-test"
EMPTY_STATUS = b"Package: libc6\nStatus: install ok installed\n\n"
SQLITE_STATUS = b"Package: libsqlite3-0\nStatus: install ok installed\n\n"

class SQLitePayloadTests(unittest.TestCase):
    def verify(self, layers, oci=False, postgres=False):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.tar"
            saved_image(path, layers, oci=oci, image=IMAGE)
            check_archive(path, IMAGE, postgres=postgres)

    def test_accepts_non_sqlite_libraries_and_django_backend_source(self):
        files = {
            STATUS: EMPTY_STATUS,
            "usr/lib/x86_64-linux-gnu/libxml2.so.2.9.14": b"unrelated",
            "usr/local/lib/python3.12/site-packages/django/db/backends/sqlite3/base.py": b"source",
            "app/poker/tests/test_sqlite_transfer.py": b"source",
        }
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.verify([layer_bytes(files, link=False)], oci=oci)

    def test_rejects_deleted_sqlite_binary_in_old_layers(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                with self.assertRaisesRegex(RuntimeError, "SQLite retained"):
                    self.verify([
                        layer_bytes({"usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6": b"old"}, link=False),
                        layer_bytes({"usr/lib/x86_64-linux-gnu/.wh.libsqlite3.so.0.8.6": b"",
                                     STATUS: EMPTY_STATUS}, link=False),
                    ], oci=oci)

    def test_rejects_python_sqlite_modules_and_links(self):
        for name in (
            "usr/local/lib/python3.12/lib-dynload/_sqlite3.cpython-312-x86_64-linux-gnu.so",
            "usr/local/lib/python3.12/sqlite3/dbapi2.py",
            "usr/bin/sqlite3",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "SQLite retained"):
                self.verify([layer_bytes({STATUS: EMPTY_STATUS, name: b"old"}, link=False)])

    def test_rejects_historical_installed_package_record(self):
        with self.assertRaisesRegex(RuntimeError, "SQLite package retained"):
            self.verify([layer_bytes({STATUS: SQLITE_STATUS}, link=False),
                         layer_bytes({STATUS: EMPTY_STATUS}, link=False)])

    def test_requires_package_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "inventory is missing"):
            self.verify([layer_bytes({"app/manage.py": b"source"}, link=False)])

    def test_rejects_postgres_gpg_even_after_removal(self):
        with self.assertRaisesRegex(RuntimeError, "gpg retained"):
            self.verify([
                layer_bytes({"usr/bin/gpg": b"old"}, link=False),
                layer_bytes({"usr/bin/.wh.gpg": b"", STATUS: EMPTY_STATUS}, link=False),
            ], postgres=True)

    def test_rejects_postgres_gpg_package_metadata(self):
        for package in ("gpg", "gnupg", "gpg-wks-client", "gpg-wks-server"):
            with self.subTest(package=package), self.assertRaisesRegex(RuntimeError, "gpg package"):
                self.verify([layer_bytes({
                    STATUS: f"Package: {package}\nStatus: install ok installed\n\n".encode(),
                }, link=False)], postgres=True)

    def test_preserves_python_environment_and_nonroot_app_contract(self):
        source = {"Env": ["PATH=/usr/local/bin:/usr/bin", "PYTHON_VERSION=3.12.15", "LANG=C.UTF-8"]}
        target = {
            "Env": source["Env"] + ["PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1",
                                    "SCORE_IT_REQUIRE_POSTGRESQL=1", "DB_ENGINE=postgresql"],
            "User": "10001:10001", "WorkingDir": "/app",
            "Cmd": ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"],
            "ExposedPorts": {"8000/tcp": {}}, "Healthcheck":copy.deepcopy(APP_HEALTHCHECK),
        }
        check_app_metadata(source, target)
        for key, value in (("Healthcheck",None),("User", "0:0"), ("WorkingDir", "/"), ("Entrypoint", ["sh"]),
                           ("Env", target["Env"] + ["OPENSSL_CONF=/tmp/profile.cnf"])):
            with self.subTest(key=key):
                changed = copy.deepcopy(target)
                changed[key] = value
                with self.assertRaises(RuntimeError):
                    check_app_metadata(source, changed)

if __name__ == "__main__":
    unittest.main()

