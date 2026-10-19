"""Database configuration shared by Passenger and container deployments."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def database_config(environ, base_dir):
    engine = environ.get("DB_ENGINE", "sqlite").strip()
    if engine == "sqlite":
        path = Path(environ.get("DATABASE_PATH", base_dir / "data" / "db.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path,
            "OPTIONS": {"timeout": 20},
        }
    if engine != "postgresql":
        raise ImproperlyConfigured("DB_ENGINE must be sqlite or postgresql")
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DB_HOST")
    if any(not environ.get(key) for key in required):
        raise ImproperlyConfigured("PostgreSQL requires " + ", ".join(required))
    options = {"connect_timeout": 10}
    if environ.get("DB_SSLMODE"):
        options["sslmode"] = environ["DB_SSLMODE"]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": environ["POSTGRES_DB"],
        "USER": environ["POSTGRES_USER"],
        "PASSWORD": environ["POSTGRES_PASSWORD"],
        "HOST": environ["DB_HOST"],
        "PORT": environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
