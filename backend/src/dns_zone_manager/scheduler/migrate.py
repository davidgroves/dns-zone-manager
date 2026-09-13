"""Schema creation and migration for the scheduled change store.

Alembic owns the schema. An empty database is brought up to the current
revision at startup, which is what makes pointing the application at a blank
PostgreSQL database work.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from dns_zone_manager.config import DatabaseSettings
from dns_zone_manager.scheduler.engine import create_migration_engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary but fixed key so concurrently starting instances serialise their
# DDL instead of racing. PostgreSQL only; SQLite databases are not shared.
_ADVISORY_LOCK_KEY = 0x646E735A4D475221


def _alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # env.py uses this instead of building its own engine.
    config.attributes["engine"] = engine
    return config


def current_revision(engine: Engine) -> str | None:
    """The revision the database is currently at, or None if unmanaged."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def schema_exists(engine: Engine) -> bool:
    """Whether the store's tables are present."""
    return inspect(engine).has_table("scheduled_changes")


def upgrade_to_head(settings: DatabaseSettings) -> tuple[str | None, str | None]:
    """Create or upgrade the schema to the latest revision.

    Returns the revision before and after, so callers can log whether anything
    actually changed.

    This is synchronous and blocking; async callers should run it in a thread.
    """
    engine = create_migration_engine(settings)
    try:
        if settings.backend == "postgres":
            with engine.connect() as lock_connection:
                lock_connection.execute(
                    text("SELECT pg_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                )
                lock_connection.commit()
                try:
                    return _run_upgrade(engine)
                finally:
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                    )
                    lock_connection.commit()
        return _run_upgrade(engine)
    finally:
        engine.dispose()


def _run_upgrade(engine: Engine) -> tuple[str | None, str | None]:
    before = current_revision(engine)
    command.upgrade(_alembic_config(engine), "head")
    return before, current_revision(engine)


def verify_schema(settings: DatabaseSettings) -> None:
    """Check the schema is present, for when auto_migrate is disabled.

    This is synchronous and blocking; async callers should run it in a thread.
    """
    engine = create_migration_engine(settings)
    try:
        if not schema_exists(engine):
            raise RuntimeError(
                f"No scheduled change schema found in {settings.redacted_url()} and "
                "database.auto_migrate is disabled. Enable it, or run "
                "'alembic upgrade head' against the database first."
            )
    finally:
        engine.dispose()


__all__ = [
    "current_revision",
    "schema_exists",
    "upgrade_to_head",
    "verify_schema",
]
