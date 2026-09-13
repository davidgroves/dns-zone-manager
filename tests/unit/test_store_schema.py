"""Unit tests for the store schema, cross-dialect types, and migrations."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dns_zone_manager.config import DatabaseSettings
from dns_zone_manager.scheduler.engine import create_migration_engine, create_store_engine
from dns_zone_manager.scheduler.migrate import (
    current_revision,
    schema_exists,
    upgrade_to_head,
    verify_schema,
)
from dns_zone_manager.scheduler.schema import (
    UtcDateTime,
    metadata,
    scheduled_change_events,
    scheduled_changes,
)
from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable


def _sqlite_settings(tmp_path: Path, **overrides) -> DatabaseSettings:
    return DatabaseSettings(backend="sqlite", path=str(tmp_path / "store.db"), **overrides)


class TestCrossDialectTypes:
    """The same metadata must produce sensible DDL on both backends."""

    def test_postgres_uses_native_types(self):
        ddl = str(CreateTable(scheduled_changes).compile(dialect=postgresql.dialect()))
        assert "created_at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
        assert "auto_prerequisites BOOLEAN" in ddl
        assert "new_serial BIGINT" in ddl

    def test_postgres_uses_jsonb_and_identity(self):
        events_ddl = str(CreateTable(scheduled_change_events).compile(dialect=postgresql.dialect()))
        assert "detail JSONB" in events_ddl
        assert "BIGSERIAL" in events_ddl

    def test_sqlite_stores_timestamps_as_text(self):
        ddl = str(CreateTable(scheduled_changes).compile(dialect=sqlite_dialect.dialect()))
        assert "created_at TEXT NOT NULL" in ddl
        assert "scheduled_at TEXT" in ddl

    def test_sqlite_events_key_is_an_integer_rowid_alias(self):
        """SQLite only autoincrements INTEGER primary keys, not BIGINT."""
        ddl = str(CreateTable(scheduled_change_events).compile(dialect=sqlite_dialect.dialect()))
        assert "id INTEGER NOT NULL" in ddl
        assert "BIGINT" not in ddl

    def test_foreign_keys_cascade_on_both_dialects(self):
        for dialect in (postgresql.dialect(), sqlite_dialect.dialect()):
            ddl = str(CreateTable(scheduled_change_events).compile(dialect=dialect))
            assert "ON DELETE CASCADE" in ddl

    def test_every_table_is_defined_once(self):
        assert {t.name for t in metadata.sorted_tables} == {
            "scheduled_changes",
            "scheduled_operations",
            "scheduled_prerequisites",
            "scheduled_change_events",
        }


class TestUtcDateTime:
    """Timestamps must survive a round trip as timezone-aware UTC."""

    @pytest.mark.parametrize(
        "value",
        [
            datetime(2030, 6, 1, 12, 30, tzinfo=UTC),
            datetime(2030, 6, 1, 12, 30, 45, 123456, tzinfo=UTC),
            datetime(2030, 6, 1, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ],
    )
    async def test_round_trip_preserves_the_instant(self, tmp_path: Path, value: datetime):
        settings = _sqlite_settings(tmp_path)
        await _create_schema(settings)
        engine = create_store_engine(settings)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    insert(scheduled_changes).values(
                        id="ts-1",
                        name="n",
                        zone="z.",
                        status="draft",
                        created_at=value,
                        updated_at=value,
                        scheduled_at=value,
                    )
                )
            async with engine.connect() as connection:
                result = await connection.execute(
                    select(scheduled_changes.c.scheduled_at).where(scheduled_changes.c.id == "ts-1")
                )
                stored = result.scalar_one()
        finally:
            await engine.dispose()

        assert stored.tzinfo is not None
        assert stored == value
        assert stored.utcoffset() == timedelta(0)

    async def test_naive_datetimes_are_treated_as_utc(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path)
        await _create_schema(settings)
        naive = datetime(2030, 6, 1, 12, 0)
        engine = create_store_engine(settings)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    insert(scheduled_changes).values(
                        id="ts-2",
                        name="n",
                        zone="z.",
                        status="draft",
                        created_at=naive,
                        updated_at=naive,
                    )
                )
            async with engine.connect() as connection:
                stored = (
                    await connection.execute(
                        select(scheduled_changes.c.created_at).where(
                            scheduled_changes.c.id == "ts-2"
                        )
                    )
                ).scalar_one()
        finally:
            await engine.dispose()
        assert stored == naive.replace(tzinfo=UTC)

    async def test_sqlite_text_form_sorts_chronologically(self, tmp_path: Path):
        """Ordering relies on ISO-8601 strings comparing like timestamps."""
        settings = _sqlite_settings(tmp_path)
        await _create_schema(settings)
        base = datetime(2030, 1, 1, tzinfo=UTC)
        engine = create_store_engine(settings)
        try:
            async with engine.begin() as connection:
                for i in (2, 0, 1):
                    await connection.execute(
                        insert(scheduled_changes).values(
                            id=f"sort-{i}",
                            name="n",
                            zone="z.",
                            status="draft",
                            created_at=base + timedelta(days=i),
                            updated_at=base,
                        )
                    )
        finally:
            await engine.dispose()

        with sqlite3.connect(settings.path) as db:
            raw = [r[0] for r in db.execute("SELECT created_at FROM scheduled_changes")]
            ordered = [
                r[0] for r in db.execute("SELECT id FROM scheduled_changes ORDER BY created_at ASC")
            ]
        assert all(value.endswith("+00:00") for value in raw)
        assert ordered == ["sort-0", "sort-1", "sort-2"]

    def test_rejects_non_datetime_values(self):
        with pytest.raises(TypeError, match="requires a datetime"):
            UtcDateTime().process_bind_param("2030-01-01", sqlite_dialect.dialect())

    def test_none_passes_through(self):
        assert UtcDateTime().process_bind_param(None, sqlite_dialect.dialect()) is None
        assert UtcDateTime().process_result_value(None, sqlite_dialect.dialect()) is None


class TestMigrations:
    async def test_empty_database_is_brought_to_head(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path)
        engine = create_migration_engine(settings)
        try:
            assert not schema_exists(engine)
        finally:
            engine.dispose()

        before, after = upgrade_to_head(settings)
        assert before is None
        assert after is not None

        engine = create_migration_engine(settings)
        try:
            assert schema_exists(engine)
            assert current_revision(engine) == after
        finally:
            engine.dispose()

    async def test_expected_tables_and_indexes_are_created(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path)
        upgrade_to_head(settings)
        with sqlite3.connect(settings.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            indexes = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
        assert {
            "alembic_version",
            "scheduled_changes",
            "scheduled_operations",
            "scheduled_prerequisites",
            "scheduled_change_events",
        } <= tables
        assert {
            "idx_scheduled_changes_status",
            "idx_scheduled_changes_zone",
            "idx_scheduled_changes_due",
            "idx_scheduled_changes_source",
            "idx_scheduled_change_events_change",
            "idx_scheduled_change_events_ts",
            "idx_scheduled_change_events_event",
            "idx_scheduled_change_events_actor",
        } <= indexes

    def test_upgrade_is_idempotent(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path)
        _, first = upgrade_to_head(settings)
        before, after = upgrade_to_head(settings)
        assert before == after == first

    def test_verify_schema_rejects_an_empty_database(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path, auto_migrate=False)
        with pytest.raises(RuntimeError, match="auto_migrate is disabled"):
            verify_schema(settings)

    def test_verify_schema_accepts_a_migrated_database(self, tmp_path: Path):
        settings = _sqlite_settings(tmp_path, auto_migrate=False)
        upgrade_to_head(settings)
        verify_schema(settings)

    def test_parent_directory_is_created_for_sqlite(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested"
        settings = DatabaseSettings(backend="sqlite", path=str(nested / "store.db"))
        upgrade_to_head(settings)
        assert (nested / "store.db").exists()


class TestSqlitePragmas:
    async def test_foreign_keys_are_enforced(self, tmp_path: Path):
        """Cascade deletes depend on the per-connection foreign_keys pragma."""
        settings = _sqlite_settings(tmp_path)
        await _create_schema(settings)
        engine = create_store_engine(settings)
        try:
            async with engine.begin() as connection:
                result = await connection.exec_driver_sql("PRAGMA foreign_keys")
                assert result.scalar() == 1
        finally:
            await engine.dispose()


async def _create_schema(settings: DatabaseSettings) -> None:
    upgrade_to_head(settings)
