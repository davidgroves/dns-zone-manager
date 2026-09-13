"""Unit tests for the database configuration section."""

from pathlib import Path

import pytest
from dns_zone_manager.config import (
    DatabaseSettings,
    PostgresSettings,
    Settings,
)
from pydantic import ValidationError


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
dns:
  server: 127.0.0.1
{body}
"""
    )
    return path


class TestDefaults:
    def test_defaults_to_sqlite(self, tmp_path: Path):
        settings = Settings.load(_write_config(tmp_path, ""))
        assert settings.database.backend == "sqlite"
        assert settings.database.auto_migrate is True
        assert settings.database.postgres is None

    def test_sqlite_path_falls_back_to_scheduler_database_path(self, tmp_path: Path):
        """Configs predating the database section keep working."""
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
scheduler:
  enabled: true
  database_path: /var/lib/dns/legacy.db
""",
            )
        )
        assert settings.database.backend == "sqlite"
        assert settings.database.path == "/var/lib/dns/legacy.db"
        assert settings.database.url() == "sqlite+aiosqlite:////var/lib/dns/legacy.db"

    def test_explicit_database_path_wins(self, tmp_path: Path):
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
scheduler:
  database_path: /var/lib/dns/legacy.db
database:
  backend: sqlite
  path: /srv/explicit.db
""",
            )
        )
        assert settings.database.path == "/srv/explicit.db"

    def test_sqlite_urls_use_the_right_drivers(self):
        settings = DatabaseSettings(backend="sqlite", path=".tmp/scheduler.db")
        assert settings.url(async_driver=True) == "sqlite+aiosqlite:///.tmp/scheduler.db"
        assert settings.url(async_driver=False) == "sqlite+pysqlite:///.tmp/scheduler.db"
        assert settings.connect_args() == {}


class TestPostgresConfig:
    def test_loads_from_yaml(self, tmp_path: Path):
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
database:
  backend: postgres
  auto_migrate: false
  postgres:
    host: db.example.com
    port: 6432
    database: dns
    user: dnsuser
    password: s3cret
    sslmode: require
    schema: dns_schema
    statement_timeout_ms: 5000
""",
            )
        )
        pg = settings.database.postgres
        assert pg is not None
        assert settings.database.auto_migrate is False
        assert pg.host == "db.example.com"
        assert pg.port == 6432
        assert pg.db_schema == "dns_schema"
        assert pg.password.get_secret_value() == "s3cret"

    def test_url_and_connect_args(self):
        settings = DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(
                host="db.example.com",
                port=6432,
                database="dns",
                user="dnsuser",
                password="s3cret",
                sslmode="require",
                db_schema="dns_schema",
                statement_timeout_ms=5000,
            ),
        )
        assert settings.url() == "postgresql+psycopg://dnsuser:s3cret@db.example.com:6432/dns"
        args = settings.connect_args()
        assert args["sslmode"] == "require"
        assert args["connect_timeout"] == 10
        assert args["options"] == "-c search_path=dns_schema -c statement_timeout=5000"

    def test_special_characters_in_credentials_are_quoted(self):
        settings = DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(
                host="db",
                database="dns",
                user="user@corp",
                password="p@ss/word:1",
            ),
        )
        assert "p%40ss%2Fword%3A1" in settings.url()
        assert "user%40corp" in settings.url()

    def test_password_is_redacted_for_logging(self):
        settings = DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(host="db", database="dns", user="dnsuser", password="s3cret"),
        )
        assert settings.redacted_url() == "postgresql+psycopg://dnsuser:***@db:5432/dns"
        assert "s3cret" not in settings.redacted_url()

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@host:5432/db",
            "postgres://u:p@host:5432/db",
            "postgresql+psycopg://u:p@host:5432/db",
        ],
    )
    def test_dsn_is_normalised_to_the_psycopg_driver(self, dsn: str):
        settings = DatabaseSettings(backend="postgres", postgres=PostgresSettings(dsn=dsn))
        assert settings.url() == "postgresql+psycopg://u:p@host:5432/db"
        assert settings.redacted_url() == "postgresql+psycopg://u:***@host:5432/db"

    def test_dsn_does_not_duplicate_libpq_parameters(self):
        """sslmode in both the DSN and connect_args would be an error."""
        settings = DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(dsn="postgresql://u:p@host/db?sslmode=verify-full"),
        )
        args = settings.connect_args()
        assert "sslmode" not in args
        assert "connect_timeout" not in args
        assert "options" in args

    def test_statement_timeout_can_be_disabled(self):
        settings = DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(host="db", database="dns", user="u", statement_timeout_ms=0),
        )
        assert settings.connect_args()["options"] == "-c search_path=public"


class TestValidation:
    def test_postgres_backend_requires_a_postgres_section(self):
        with pytest.raises(ValidationError, match="no database.postgres section"):
            DatabaseSettings(backend="postgres")

    @pytest.mark.parametrize(
        ("kwargs", "missing"),
        [
            ({"database": "dns", "user": "u"}, "host"),
            ({"host": "db", "user": "u"}, "database"),
            ({"host": "db", "database": "dns"}, "user"),
        ],
    )
    def test_missing_connection_fields_are_reported(self, kwargs: dict, missing: str):
        with pytest.raises(ValidationError, match=missing):
            PostgresSettings(**kwargs)

    def test_dsn_satisfies_the_connection_requirements(self):
        pg = PostgresSettings(dsn="postgresql://u:p@host/db")
        assert pg.host == ""

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValidationError):
            DatabaseSettings(backend="mysql")


class TestEnvironmentVariables:
    def test_postgres_fields_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        monkeypatch.setenv("POSTGRES_DATABASE", "env-db")
        monkeypatch.setenv("POSTGRES_USER", "env-user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "env-pass")
        pg = PostgresSettings()
        assert pg.host == "env-host"
        assert pg.database == "env-db"
        assert pg.password.get_secret_value() == "env-pass"

    def test_backend_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_BACKEND", "postgres")
        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        monkeypatch.setenv("POSTGRES_DATABASE", "env-db")
        monkeypatch.setenv("POSTGRES_USER", "env-user")
        settings = DatabaseSettings(postgres=PostgresSettings())
        assert settings.backend == "postgres"

    def test_yaml_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("POSTGRES_HOST", "env-host")
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
database:
  backend: postgres
  postgres:
    host: yaml-host
    database: dns
    user: u
""",
            )
        )
        assert settings.database.postgres is not None
        assert settings.database.postgres.host == "yaml-host"
