"""Database configuration for local development and PostgreSQL deployments."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def database_config(environ, base_dir):
    engine = environ.get("DB_ENGINE", "sqlite").strip()
    if environ.get("SCORE_IT_REQUIRE_POSTGRESQL") == "1" and engine != "postgresql":
        raise ImproperlyConfigured(
            "Production container requires DB_ENGINE=postgresql. "
            "SQLite is supported by source installations only."
        )
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
    # Explicit DB_* credentials are runtime-only. POSTGRES_* remains supported for
    # existing non-Compose installations; production Compose never passes it to web.
    user_key = "DB_USER" if "DB_USER" in environ or "DB_PASSWORD" in environ else "POSTGRES_USER"
    password_key = "DB_PASSWORD" if user_key == "DB_USER" else "POSTGRES_PASSWORD"
    required = ("POSTGRES_DB", user_key, password_key, "DB_HOST")
    if any(not environ.get(key) for key in required):
        raise ImproperlyConfigured("PostgreSQL requires " + ", ".join(required))
    options = {"connect_timeout": 10}
    if environ.get("DB_SSLMODE"):
        options["sslmode"] = environ["DB_SSLMODE"]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": environ["POSTGRES_DB"],
        "USER": environ[user_key],
        "PASSWORD": environ[password_key],
        "HOST": environ["DB_HOST"],
        "PORT": environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
