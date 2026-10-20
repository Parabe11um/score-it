from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase

from config.database import database_config


class DatabaseConfigurationTests(SimpleTestCase):
    def test_sqlite_default_is_retained(self):
        with TemporaryDirectory() as directory:
            config = database_config({}, Path(directory))
            self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
            self.assertEqual(config["NAME"], Path(directory) / "data/db.sqlite3")

    def test_postgresql_does_not_create_sqlite_directory(self):
        with TemporaryDirectory() as directory:
            config = database_config({
                "DB_ENGINE": "postgresql", "POSTGRES_DB": "score_it",
                "POSTGRES_USER": "score_it", "POSTGRES_PASSWORD": "secret",
                "DB_HOST": "db", "DB_SSLMODE": "require",
            }, Path(directory))
            self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
            self.assertEqual(config["OPTIONS"]["sslmode"], "require")
            self.assertFalse((Path(directory) / "data").exists())

    def test_production_container_rejects_sqlite_before_creating_files(self):
        for engine in (None, "sqlite", "postgress"):
            with self.subTest(engine=engine), TemporaryDirectory() as directory:
                environ = {"SCORE_IT_REQUIRE_POSTGRESQL": "1"}
                if engine is not None:
                    environ["DB_ENGINE"] = engine
                with self.assertRaisesRegex(ImproperlyConfigured, "requires DB_ENGINE=postgresql"):
                    database_config(environ, Path(directory))
                self.assertFalse((Path(directory) / "data").exists())

    def test_production_container_accepts_explicit_postgresql_runtime_role(self):
        config = database_config({
            "SCORE_IT_REQUIRE_POSTGRESQL": "1", "DB_ENGINE": "postgresql",
            "POSTGRES_DB": "score_it", "DB_HOST": "db",
            "DB_USER": "score_it_app", "DB_PASSWORD": "fixture-password",
        }, Path("/tmp"))
        self.assertEqual(config["USER"], "score_it_app")

    def test_incomplete_postgresql_configuration_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            database_config({"DB_ENGINE": "postgresql"}, Path("/tmp"))
        with self.assertRaises(ImproperlyConfigured):
            database_config({"DB_ENGINE": "postgress"}, Path("/tmp"))

    def test_explicit_runtime_credentials_do_not_fall_back_to_administrator(self):
        environ = {"DB_ENGINE": "postgresql", "POSTGRES_DB": "score_it", "DB_HOST": "db",
                   "POSTGRES_USER": "admin", "POSTGRES_PASSWORD": "admin-password",
                   "DB_USER": "score_it_app", "DB_PASSWORD": "app-password"}
        self.assertEqual(database_config(environ, Path("/tmp"))["USER"], "score_it_app")
        environ["DB_PASSWORD"] = ""
        with self.assertRaises(ImproperlyConfigured):
            database_config(environ, Path("/tmp"))


class HealthTests(TestCase):
    def test_health_is_available_without_authentication(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
