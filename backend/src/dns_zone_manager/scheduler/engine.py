"""Database engine construction for the scheduled change store.

Builds SQLAlchemy engines for either supported backend from
:class:`~dns_zone_manager.config.DatabaseSettings`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from dns_zone_manager.config import DatabaseSettings

logger = logging.getLogger(__name__)


def sqlite_path(settings: DatabaseSettings) -> Path | None:
    """The SQLite database file, or None when another backend is configured."""
    if settings.backend != "sqlite":
        return None
    return Path(settings.path)


def _prepare_sqlite(settings: DatabaseSettings) -> None:
    """Create the parent directory so a fresh SQLite file can be written."""
    path = sqlite_path(settings)
    if path is not None and path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Apply per-connection SQLite settings.

    Foreign keys and the WAL journal are connection-scoped, so they must be set
    on every pooled connection rather than once at startup.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry,
    ) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()


def _engine_kwargs(settings: DatabaseSettings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "connect_args": settings.connect_args(),
    }
    if settings.backend == "postgres":
        assert settings.postgres is not None  # guaranteed by config validation
        kwargs["pool_size"] = settings.postgres.pool_size
    return kwargs


def create_store_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create the async engine the store runs its queries on."""
    _prepare_sqlite(settings)
    engine = create_async_engine(settings.url(async_driver=True), **_engine_kwargs(settings))
    if settings.backend == "sqlite":
        _register_sqlite_pragmas(engine.sync_engine)
    return engine


def create_migration_engine(settings: DatabaseSettings) -> Engine:
    """Create the sync engine Alembic runs migrations on."""
    _prepare_sqlite(settings)
    engine = create_engine(settings.url(async_driver=False), **_engine_kwargs(settings))
    if settings.backend == "sqlite":
        _register_sqlite_pragmas(engine)
    return engine


__all__ = ["create_migration_engine", "create_store_engine", "sqlite_path"]
