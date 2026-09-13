"""Integration tests for the PostgreSQL storage backend.

Covers the parts that cannot be verified against SQLite: native column types,
row-level locking when several application instances share one database, and
creating the schema in an empty database.
"""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from dns_zone_manager.config import DatabaseSettings
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.scheduler.engine import create_store_engine
from dns_zone_manager.scheduler.migrate import current_revision, schema_exists
from dns_zone_manager.scheduler.schema import scheduled_changes
from dns_zone_manager.scheduler.store import (
    ChangeCreateData,
    ChangeUpdateData,
    OpSnapshot,
    ScheduledChangeStore,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.integration.bind_container import BindContainer
from tests.integration.conftest import (
    _cleanup_config_file,
    _make_config_yaml,
    _set_config_file,
)
from tests.integration.postgres_container import PostgresContainer

pytestmark = pytest.mark.integration


def _connect(settings: DatabaseSettings) -> psycopg.Connection:
    assert settings.postgres is not None
    return psycopg.connect(
        host=settings.postgres.host,
        port=settings.postgres.port,
        dbname=settings.postgres.database,
        user=settings.postgres.user,
        password=settings.postgres.password.get_secret_value(),
        autocommit=True,
    )


@pytest.fixture
def empty_database(postgres_server: PostgresContainer) -> DatabaseSettings:
    """Database settings pointing at a freshly emptied database."""
    settings = postgres_server.get_database_settings()
    with _connect(settings) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    return settings


@pytest.fixture
async def store(empty_database: DatabaseSettings):
    """An open store on an empty database."""
    s = ScheduledChangeStore(settings=empty_database, default_expiry_window=3600)
    await s.open()
    yield s
    await s.close()


def _op(name: str = "www", records: list[str] | None = None) -> AtomicOperation:
    return AtomicOperation(
        action="add",
        name=name,
        type="A",
        ttl=300,
        records=records or ["192.0.2.1"],
    )


class TestSchemaBootstrap:
    async def test_creates_schema_in_empty_database(self, empty_database: DatabaseSettings):
        """Pointing the store at a blank database builds the schema."""
        engine = create_store_engine(empty_database)
        try:
            assert not await asyncio.to_thread(_sync_schema_exists, empty_database)
        finally:
            await engine.dispose()

        store = ScheduledChangeStore(settings=empty_database)
        await store.open()
        try:
            assert await asyncio.to_thread(_sync_schema_exists, empty_database)
            assert await asyncio.to_thread(_sync_revision, empty_database) is not None

            with _connect(empty_database) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                }
            assert {
                "alembic_version",
                "scheduled_changes",
                "scheduled_operations",
                "scheduled_prerequisites",
                "scheduled_change_events",
            } <= tables
        finally:
            await store.close()

    async def test_opening_twice_is_idempotent(self, empty_database: DatabaseSettings):
        """A second start against a migrated database changes nothing."""
        first = ScheduledChangeStore(settings=empty_database)
        await first.open()
        revision = await asyncio.to_thread(_sync_revision, empty_database)
        await first.close()

        second = ScheduledChangeStore(settings=empty_database)
        await second.open()
        try:
            assert await asyncio.to_thread(_sync_revision, empty_database) == revision
        finally:
            await second.close()

    async def test_auto_migrate_disabled_reports_missing_schema(
        self, postgres_server: PostgresContainer
    ):
        """With auto_migrate off, an empty database fails with a clear error."""
        settings = postgres_server.get_database_settings(auto_migrate=False)
        with _connect(settings) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")

        store = ScheduledChangeStore(settings=settings)
        with pytest.raises(RuntimeError, match="auto_migrate is disabled"):
            await store.open()
        await store.close()

    async def test_native_column_types(self, store: ScheduledChangeStore):
        """Timestamps, booleans, and JSON use real PostgreSQL types."""
        with _connect(store.settings) as connection:
            types = dict(
                connection.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'scheduled_changes'"
                ).fetchall()
            )
        assert types["created_at"] == "timestamp with time zone"
        assert types["auto_prerequisites"] == "boolean"
        assert types["new_serial"] == "bigint"

        with _connect(store.settings) as connection:
            op_types = dict(
                connection.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'scheduled_operations'"
                ).fetchall()
            )
        assert op_types["records"] == "jsonb"
        assert op_types["prior_records"] == "jsonb"


class TestStoreOperations:
    async def test_create_get_and_round_trip_types(self, store: ScheduledChangeStore):
        change = await store.create(
            ChangeCreateData(
                name="pg change",
                zone="test.example.",
                operations=[_op()],
                description="hello",
                created_by="alice",
            )
        )
        assert change.status == "draft"
        assert change.auto_prerequisites is True
        assert change.source == "scheduler"
        assert change.created_at.tzinfo is not None
        assert change.operations[0].records == ["192.0.2.1"]
        assert [e.event for e in change.events] == ["created"]

        fetched = await store.get(change.id)
        assert fetched is not None
        assert fetched.created_at == change.created_at
        assert fetched.description == "hello"

    async def test_list_filters(self, store: ScheduledChangeStore):
        await store.create(ChangeCreateData(name="a", zone="test.example.", operations=[_op("a")]))
        await store.create(ChangeCreateData(name="b", zone="other.example.", operations=[_op("b")]))
        await store.record_external_change(
            name="manual", zone="test.example.", operations=[_op("m").model_dump()]
        )

        assert len(await store.list_changes(zone="test.example.")) == 2
        assert len(await store.list_changes(status="draft")) == 2
        assert len(await store.list_changes(source="manual")) == 1
        assert len(await store.list_changes(source="scheduler")) == 2

    async def test_update_and_cancel(self, store: ScheduledChangeStore):
        change = await store.create(
            ChangeCreateData(
                name="before",
                zone="test.example.",
                operations=[_op()],
                description="old",
            )
        )
        when = datetime.now(UTC) + timedelta(hours=1)
        updated = await store.update(
            change.id,
            ChangeUpdateData(
                name="after",
                clear_description=True,
                scheduled_at=when,
                actor="bob",
            ),
        )
        assert updated.name == "after"
        assert updated.description is None
        assert updated.status == "scheduled"
        assert updated.not_valid_after is not None

        detail = next(e.detail for e in updated.events if e.event == "updated")
        assert detail["changes"]["name"] == {"from": "before", "to": "after"}

        cancelled = await store.cancel(change.id, actor="bob")
        assert cancelled.status == "cancelled"

    async def test_claim_apply_and_lateness(self, store: ScheduledChangeStore):
        due = datetime.now(UTC) - timedelta(seconds=5)
        change = await store.create(
            ChangeCreateData(name="due", zone="test.example.", operations=[_op()], scheduled_at=due)
        )

        claimed = await store.claim_due("worker-1", 60)
        assert claimed is not None
        assert claimed.id == change.id
        assert claimed.status == "running"
        assert claimed.attempts == 1

        assert await store.claim_due("worker-1", 60) is None

        applied = await store.mark_applied(
            change.id, result_rcode="NOERROR", new_serial=99, actor="worker-1"
        )
        assert applied.status == "applied"
        assert applied.new_serial == 99
        assert applied.applied_at is not None

    async def test_mark_failed_retries_then_gives_up(self, store: ScheduledChangeStore):
        due = datetime.now(UTC) - timedelta(seconds=5)
        change = await store.create(
            ChangeCreateData(
                name="fail", zone="test.example.", operations=[_op()], scheduled_at=due
            )
        )
        await store.claim_due("worker-1", 60)

        retried = await store.mark_failed(change.id, "boom", max_attempts=3, retry_backoff=1)
        assert retried.status == "scheduled"
        assert retried.next_attempt_at is not None
        assert retried.last_error == "boom"

        # Clear the retry delay so the next attempt is claimable immediately.
        await _clear_retry_delay(store.settings, change.id)
        claimed = await store.claim_due("worker-1", 60)
        assert claimed is not None
        assert claimed.attempts == 2

        exhausted = await store.mark_failed(
            change.id, "boom again", max_attempts=1, retry_backoff=1
        )
        assert exhausted.status == "failed"
        assert exhausted.next_attempt_at is None
        assert exhausted.last_error == "boom again"

    async def test_snapshots_and_revert(self, store: ScheduledChangeStore):
        change = await store.create(
            ChangeCreateData(
                name="revert me",
                zone="test.example.",
                operations=[AtomicOperation(action="delete", name="old", type="A", ttl=300)],
                scheduled_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        await store.save_operation_snapshots(
            change.id,
            [
                OpSnapshot(
                    seq=0,
                    prior_ttl=300,
                    prior_records=["192.0.2.9"],
                    snapshot_at=datetime.now(UTC),
                )
            ],
        )
        reloaded = await store.get(change.id)
        assert reloaded is not None
        assert reloaded.operations[0].prior_records == ["192.0.2.9"]
        assert reloaded.operations[0].prior_ttl == 300
        assert reloaded.operations[0].snapshot_at is not None

        await store.claim_due("worker-1", 60)
        await store.mark_applied(change.id, result_rcode="NOERROR")
        reverted = await store.mark_reverted(
            change.id, result_rcode="NOERROR", actor="alice", operations_count=1
        )
        assert reverted.status == "reverted"
        assert reverted.reverted_at is not None

    async def test_expire_overdue(self, store: ScheduledChangeStore):
        stale = await store.create(
            ChangeCreateData(
                name="stale",
                zone="test.example.",
                operations=[_op()],
                scheduled_at=datetime.now(UTC) - timedelta(hours=2),
                not_valid_after=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        expired = await store.expire_overdue()
        assert stale.id in expired
        after = await store.get(stale.id)
        assert after is not None
        assert after.status == "expired"
        assert any(e.event == "expired" for e in after.events)

    async def test_find_conflicts_and_count_pending(self, store: ScheduledChangeStore):
        await store.create(
            ChangeCreateData(name="first", zone="test.example.", operations=[_op("dup")])
        )
        conflicts = await store.find_conflicts("test.example.", [_op("dup")])
        assert len(conflicts) == 1
        assert conflicts[0][1] == "first"
        assert await store.count_pending() == 1


class TestAuditLog:
    async def test_search_is_case_insensitive_across_json_detail(self, store: ScheduledChangeStore):
        """ILIKE and the JSON cast keep search behaving like it does on SQLite."""
        change = await store.create(
            ChangeCreateData(
                name="Findable Change",
                zone="test.example.",
                operations=[_op()],
                created_by="Alice",
            )
        )
        await store.update(change.id, ChangeUpdateData(name="Renamed Thing", actor="Bob"))

        _, by_name = await store.list_events(q="findable")
        assert by_name >= 1
        _, by_upper = await store.list_events(q="FINDABLE")
        assert by_upper == by_name

        # "renamed thing" only appears inside the updated event's JSON detail
        events, total = await store.list_events(q="renamed thing")
        assert total >= 1
        assert any(e.event == "updated" for e in events)

        _, by_actor = await store.list_events(actor="bob")
        assert by_actor >= 1

    async def test_pagination_and_ordering(self, store: ScheduledChangeStore):
        for i in range(5):
            await store.create(
                ChangeCreateData(
                    name=f"change {i}", zone="test.example.", operations=[_op(f"h{i}")]
                )
            )
        first_page, total = await store.list_events(limit=2, offset=0)
        second_page, _ = await store.list_events(limit=2, offset=2)
        assert total == 5
        assert len(first_page) == 2
        assert len(second_page) == 2
        assert {e.id for e in first_page}.isdisjoint({e.id for e in second_page})
        # Newest first
        assert first_page[0].ts >= first_page[1].ts


class TestConcurrentInstances:
    async def test_claim_skips_rows_locked_by_another_instance(self, store: ScheduledChangeStore):
        """A second instance neither blocks nor double-claims a locked change.

        Without FOR UPDATE SKIP LOCKED the competing claim would wait for the
        holder's transaction instead of returning promptly.
        """
        due = datetime.now(UTC) - timedelta(seconds=5)
        change = await store.create(
            ChangeCreateData(
                name="contended", zone="test.example.", operations=[_op()], scheduled_at=due
            )
        )

        # A second store stands in for another application instance; it has its
        # own engine and its own transaction lock.
        other = ScheduledChangeStore(settings=store.settings)
        await other.open()
        try:
            engine = create_store_engine(store.settings)
            try:
                async with engine.connect() as connection, connection.begin():
                    # Lock the row the way a concurrent claim would.
                    locked = await connection.execute(
                        select(scheduled_changes.c.id)
                        .where(scheduled_changes.c.id == change.id)
                        .with_for_update(skip_locked=True)
                    )
                    assert locked.scalar_one_or_none() == change.id

                    claimed = await asyncio.wait_for(other.claim_due("worker-2", 60), timeout=15)
                    assert claimed is None
            finally:
                await engine.dispose()

            # Once the lock is released the change is claimable again.
            claimed = await asyncio.wait_for(other.claim_due("worker-3", 60), timeout=15)
            assert claimed is not None
            assert claimed.id == change.id
            assert claimed.status == "running"
        finally:
            await other.close()


class TestApiEndToEnd:
    @pytest.fixture
    def postgres_client(
        self,
        bind_server: BindContainer,
        empty_database: DatabaseSettings,
        postgres_server: PostgresContainer,
    ) -> Generator[TestClient]:
        """A TestClient backed by PostgreSQL instead of SQLite."""
        config_yaml = _make_config_yaml(
            bind_server,
            auth_enabled=False,
            extra_yaml=postgres_server.get_config_yaml_section(),
        )
        config_path = _set_config_file(config_yaml)
        from dns_zone_manager.main import create_app

        with TestClient(create_app()) as client:
            yield client
        _cleanup_config_file(config_path)

    def test_scheduled_change_lifecycle_through_api(
        self, postgres_client: TestClient, zone_name: str
    ):
        postgres_client.post(f"/v1/zones/{zone_name}/refresh")

        health = postgres_client.get("/health")
        assert health.status_code == 200
        assert health.json()["database"] == {"backend": "postgres", "connected": True}

        create = postgres_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Add pg-www",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "pg-www",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.77"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        change_id = create.json()["id"]

        applied = postgres_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "applied"

        rrset = postgres_client.get(f"/v1/zones/{zone_name}/rrsets/pg-www/A")
        assert rrset.status_code == 200
        assert "192.0.2.77" in rrset.json()["records"]

        audit = postgres_client.get("/v1/scheduled-changes/events", params={"q": "pg-www"})
        assert audit.status_code == 200, audit.text
        body = audit.json()
        assert body["total"] >= 1
        assert {"created", "applied"} <= {e["event"] for e in body["events"]}


async def _clear_retry_delay(settings: DatabaseSettings, change_id: str) -> None:
    """Make a change due again by dropping its retry delay and lease."""
    engine = create_store_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                update(scheduled_changes)
                .where(scheduled_changes.c.id == change_id)
                .values(next_attempt_at=None, lease_expires_at=None)
            )
    finally:
        await engine.dispose()


def _sync_schema_exists(settings: DatabaseSettings) -> bool:
    from dns_zone_manager.scheduler.engine import create_migration_engine

    engine = create_migration_engine(settings)
    try:
        return schema_exists(engine)
    finally:
        engine.dispose()


def _sync_revision(settings: DatabaseSettings) -> str | None:
    from dns_zone_manager.scheduler.engine import create_migration_engine

    engine = create_migration_engine(settings)
    try:
        return current_revision(engine)
    finally:
        engine.dispose()
