"""Alembic environment for the scheduled change store.

Runs both ways:

- Programmatically at application startup, where the caller passes an engine in
  ``config.attributes["engine"]``.
- From the developer CLI (``uv run alembic ...``), where the engine is built
  from the active application config file.
"""

from __future__ import annotations

from alembic import context
from dns_zone_manager.scheduler.schema import metadata
from sqlalchemy import Engine

config = context.config
target_metadata = metadata


def _get_engine() -> Engine:
    """Return the caller's engine, or build one from the application config."""
    engine = config.attributes.get("engine")
    if engine is not None:
        return engine

    from dns_zone_manager.config import get_settings
    from dns_zone_manager.scheduler.engine import create_migration_engine

    return create_migration_engine(get_settings().database)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it against a database."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        url = _get_engine().url.render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    engine = _get_engine()
    owns_engine = config.attributes.get("engine") is None
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                # SQLite cannot ALTER most things in place; batch mode rewrites
                # the table instead.
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if owns_engine:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
