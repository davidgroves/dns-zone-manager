"""Unit tests for scheduled-change retention purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from dns_zone_manager.config import RetentionSettings
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.scheduler.retention import run_retention
from dns_zone_manager.scheduler.schema import scheduled_changes
from dns_zone_manager.scheduler.store import ChangeCreateData, ScheduledChangeStore
from sqlalchemy import text, update


@pytest.fixture
async def store(tmp_path: Path):
    s = ScheduledChangeStore(tmp_path / "retention.db", default_expiry_window=3600)
    await s.open()
    yield s
    await s.close()


def _op(name: str = "www") -> AtomicOperation:
    return AtomicOperation(
        action="add",
        name=name,
        type="A",
        ttl=3600,
        records=["192.0.2.1"],
    )


async def _create_applied(
    store: ScheduledChangeStore,
    name: str,
    *,
    completed_at: datetime | None = None,
    host: str | None = None,
) -> str:
    change = await store.create(
        ChangeCreateData(
            name=name,
            zone="example.com.",
            operations=[_op(host or name)],
            created_by="tester",
        )
    )
    await store.mark_applied(change.id, result_rcode="NOERROR", new_serial=100)
    if completed_at is not None:
        await _backdate(store, change.id, completed_at)
    return change.id


async def _backdate(store: ScheduledChangeStore, change_id: str, when: datetime) -> None:
    async with store._transaction("test_backdate"):
        await store._conn().execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                applied_at=when,
                updated_at=when,
                created_at=when,
            )
        )


@pytest.mark.asyncio
async def test_age_purge_removes_old_keeps_recent(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    old_id = await _create_applied(store, "old", completed_at=now - timedelta(days=40), host="old")
    recent_id = await _create_applied(
        store, "recent", completed_at=now - timedelta(days=5), host="recent"
    )

    result = await run_retention(
        store,
        RetentionSettings(
            max_age_days=30,
            max_database_mb=0,
            vacuum="off",
        ),
        now=now,
    )

    assert result.purged_by_age == 1
    assert await store.get(old_id) is None
    assert await store.get(recent_id) is not None


@pytest.mark.asyncio
async def test_future_scheduled_at_never_purged(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    future = now + timedelta(days=7)
    change = await store.create(
        ChangeCreateData(
            name="future-applied",
            zone="example.com.",
            operations=[_op("future")],
            scheduled_at=future,
        )
    )
    # Force terminal status while leaving scheduled_at in the future.
    async with store._transaction("force_status"):
        await store._conn().execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change.id)
            .values(
                status="applied",
                applied_at=now - timedelta(days=100),
                updated_at=now - timedelta(days=100),
                created_at=now - timedelta(days=100),
            )
        )

    result = await run_retention(
        store,
        RetentionSettings(max_age_days=1, max_database_mb=0, vacuum="off"),
        now=now,
    )
    assert result.purged_by_age == 0
    assert await store.get(change.id) is not None


@pytest.mark.asyncio
async def test_failed_with_future_retry_never_purged(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    change = await store.create(
        ChangeCreateData(
            name="retrying",
            zone="example.com.",
            operations=[_op("retry")],
            scheduled_at=now - timedelta(hours=1),
        )
    )
    await store.mark_running(change.id, "worker", 60)
    failed = await store.mark_failed(
        change.id,
        error="temporary",
        max_attempts=5,
        retry_backoff=3600,
    )
    assert failed.status == "scheduled"
    assert failed.next_attempt_at is not None
    assert failed.next_attempt_at > now

    # Force status to failed while keeping the future next_attempt_at.
    async with store._transaction("force_failed"):
        await store._conn().execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change.id)
            .values(
                status="failed",
                updated_at=now - timedelta(days=100),
                created_at=now - timedelta(days=100),
            )
        )

    result = await run_retention(
        store,
        RetentionSettings(max_age_days=1, max_database_mb=0, vacuum="off"),
        now=now,
    )
    assert result.purged_by_age == 0
    assert await store.get(change.id) is not None


@pytest.mark.asyncio
async def test_active_statuses_untouched(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    draft = await store.create(
        ChangeCreateData(name="draft", zone="example.com.", operations=[_op("d")])
    )
    scheduled = await store.create(
        ChangeCreateData(
            name="scheduled",
            zone="example.com.",
            operations=[_op("s")],
            scheduled_at=now - timedelta(days=10),
        )
    )
    # Backdate so they would match an age cut if status were ignored.
    async with store._transaction("backdate_active"):
        await store._conn().execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id.in_([draft.id, scheduled.id]))
            .values(
                updated_at=now - timedelta(days=100),
                created_at=now - timedelta(days=100),
            )
        )

    result = await run_retention(
        store,
        RetentionSettings(max_age_days=1, max_database_mb=0, vacuum="off"),
        now=now,
    )
    assert result.purged_by_age == 0
    assert await store.get(draft.id) is not None
    assert await store.get(scheduled.id) is not None


@pytest.mark.asyncio
async def test_cascade_deletes_audit_events(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    change_id = await _create_applied(
        store, "cascade", completed_at=now - timedelta(days=60), host="cascade"
    )
    events_before = await store.get_events(change_id)
    assert len(events_before) >= 1

    await run_retention(
        store,
        RetentionSettings(max_age_days=30, max_database_mb=0, vacuum="off"),
        now=now,
    )

    assert await store.get(change_id) is None
    listed, total = await store.list_events(change_id=change_id)
    assert total == 0
    assert listed == []


@pytest.mark.asyncio
async def test_size_trim_repeats_with_reclaim_stub(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    for i in range(10):
        await _create_applied(
            store,
            f"loop-{i}",
            completed_at=now - timedelta(days=i + 1),
            host=f"loop{i}",
        )

    # Sequence: before, each loop check, after reclaim, final bytes_after/eligible path.
    # Start over limit; after two trims drop under limit.
    size_values = iter([3_000_000, 3_000_000, 2_500_000, 2_500_000, 800_000, 800_000, 800_000])

    async def fake_size() -> int:
        try:
            return next(size_values)
        except StopIteration:
            return 800_000

    store.database_size_bytes = fake_size  # type: ignore[method-assign]
    store.reclaim_space = AsyncMock()  # type: ignore[method-assign]

    result = await run_retention(
        store,
        RetentionSettings(
            max_age_days=0,
            max_database_mb=1,
            trim_percent=20,
            max_trim_passes=10,
            vacuum="incremental",
        ),
        now=now,
    )

    assert result.purged_by_size >= 2
    assert result.trim_passes >= 2
    assert store.reclaim_space.await_count >= 1
    remaining = await store.list_changes(include_events=False)
    assert len(remaining) < 10


@pytest.mark.asyncio
async def test_max_trim_passes_bounds_loop(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    for i in range(20):
        await _create_applied(
            store,
            f"bound-{i}",
            completed_at=now - timedelta(days=i + 1),
            host=f"bound{i}",
        )

    async def always_over() -> int:
        return 10_000_000

    store.database_size_bytes = always_over  # type: ignore[method-assign]
    store.reclaim_space = AsyncMock()  # type: ignore[method-assign]

    result = await run_retention(
        store,
        RetentionSettings(
            max_age_days=0,
            max_database_mb=1,
            trim_percent=10,
            max_trim_passes=3,
            vacuum="incremental",
        ),
        now=now,
    )

    assert result.trim_passes == 3


@pytest.mark.asyncio
async def test_dry_run_deletes_nothing(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    change_id = await _create_applied(
        store, "dry", completed_at=now - timedelta(days=60), host="dry"
    )

    result = await run_retention(
        store,
        RetentionSettings(
            max_age_days=30,
            max_database_mb=0,
            dry_run=True,
            vacuum="off",
        ),
        now=now,
    )

    assert result.dry_run is True
    assert result.purged_by_age == 1
    assert await store.get(change_id) is not None
    events = await store.get_events(change_id)
    assert len(events) >= 1


@pytest.mark.asyncio
async def test_disabled_is_noop(store: ScheduledChangeStore):
    now = datetime.now(UTC)
    change_id = await _create_applied(
        store, "keep", completed_at=now - timedelta(days=60), host="keep"
    )

    result = await run_retention(
        store,
        RetentionSettings(enabled=False, max_age_days=1),
        now=now,
    )
    assert result.skipped is True
    assert await store.get(change_id) is not None


@pytest.mark.asyncio
async def test_database_size_bytes_counts_sqlite_file(store: ScheduledChangeStore):
    size = await store.database_size_bytes()
    assert size > 0
    assert store.database_path is not None
    assert size >= store.database_path.stat().st_size


@pytest.mark.asyncio
async def test_reclaim_incremental_on_fresh_db(store: ScheduledChangeStore):
    # Fresh DBs get auto_vacuum=INCREMENTAL from the connect pragma.
    await store.reclaim_space("incremental")
    async with store._require_engine().connect() as conn:
        result = await conn.execute(text("PRAGMA auto_vacuum"))
        assert int(result.scalar() or 0) == 2
