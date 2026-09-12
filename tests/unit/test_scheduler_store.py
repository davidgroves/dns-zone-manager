"""Unit tests for ScheduledChangeStore (SQLite)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import ChangePrerequisite
from dns_zone_manager.scheduler.store import (
    ChangeCreateData,
    ChangeUpdateData,
    ScheduledChangeStore,
)


@pytest.fixture
async def store(tmp_path: Path):
    s = ScheduledChangeStore(tmp_path / "test.db", default_expiry_window=3600)
    await s.open()
    yield s
    await s.close()


def _op(name: str = "www", action: str = "add") -> AtomicOperation:
    return AtomicOperation(
        action=action,  # type: ignore[arg-type]
        name=name,
        type="A",
        ttl=3600,
        records=["192.0.2.1"] if action != "delete" else None,
    )


@pytest.mark.asyncio
async def test_create_draft(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="Draft change",
            zone="example.com.",
            operations=[_op()],
            created_by="alice",
        )
    )
    assert change.status == "draft"
    assert change.scheduled_at is None
    assert len(change.operations) == 1
    assert change.created_by == "alice"
    assert any(e.event == "created" for e in change.events)


@pytest.mark.asyncio
async def test_create_scheduled_sets_expiry(store: ScheduledChangeStore):
    when = datetime.now(UTC) + timedelta(hours=1)
    change = await store.create(
        ChangeCreateData(
            name="Timed",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=when,
        )
    )
    assert change.status == "scheduled"
    assert change.not_valid_after is not None
    assert change.not_valid_after == when + timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_create_with_prerequisites(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="With prereq",
            zone="example.com.",
            operations=[_op()],
            prerequisites=[
                ChangePrerequisite(prereq_type="nxrrset", name="www", rdtype="A"),
            ],
        )
    )
    assert len(change.prerequisites) == 1
    assert change.prerequisites[0].prereq_type == "nxrrset"


@pytest.mark.asyncio
async def test_list_filter_by_status_and_zone(store: ScheduledChangeStore):
    await store.create(ChangeCreateData(name="A", zone="example.com.", operations=[_op()]))
    await store.create(
        ChangeCreateData(
            name="B",
            zone="other.com.",
            operations=[_op()],
            scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    drafts = await store.list_changes(status="draft")
    assert len(drafts) == 1
    assert drafts[0].name == "A"

    example = await store.list_changes(zone="example.com")
    assert len(example) == 1


@pytest.mark.asyncio
async def test_list_filter_by_multiple_statuses(store: ScheduledChangeStore):
    await store.create(ChangeCreateData(name="Draft", zone="example.com.", operations=[_op("a")]))
    await store.create(
        ChangeCreateData(
            name="Scheduled",
            zone="example.com.",
            operations=[_op("b")],
            scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    failed = await store.create(
        ChangeCreateData(name="Failed", zone="example.com.", operations=[_op("c")])
    )
    applied = await store.create(
        ChangeCreateData(name="Applied", zone="example.com.", operations=[_op("d")])
    )

    import aiosqlite

    async with aiosqlite.connect(store.database_path) as db:
        await db.execute(
            "UPDATE scheduled_changes SET status = 'failed' WHERE id = ?",
            (failed.id,),
        )
        await db.execute(
            "UPDATE scheduled_changes SET status = 'applied' WHERE id = ?",
            (applied.id,),
        )
        await db.commit()

    active = await store.list_changes(statuses=["draft", "scheduled", "failed"])
    names = {c.name for c in active}
    assert names == {"Draft", "Scheduled", "Failed"}
    assert "Applied" not in names


@pytest.mark.asyncio
async def test_update_and_cancel(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(name="Edit me", zone="example.com.", operations=[_op()])
    )
    updated = await store.update(
        change.id,
        ChangeUpdateData(
            name="Edited",
            scheduled_at=datetime.now(UTC) + timedelta(hours=2),
            actor="bob",
        ),
    )
    assert updated.name == "Edited"
    assert updated.status == "scheduled"

    cancelled = await store.cancel(updated.id, actor="bob")
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_update_emits_rich_field_diffs(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="Original",
            description="notes",
            zone="example.com.",
            operations=[_op("www")],
            created_by="alice",
        )
    )
    created_ev = next(e for e in change.events if e.event == "created")
    assert created_ev.detail is not None
    assert created_ev.detail["zone"] == "example.com."
    assert created_ev.detail["operations_count"] == 1
    assert created_ev.detail["status"] == "draft"

    updated = await store.update(
        change.id,
        ChangeUpdateData(
            name="Renamed",
            operations=[_op("www"), _op("api")],
            actor="bob",
        ),
    )
    updated_ev = next(e for e in updated.events if e.event == "updated")
    assert updated_ev.detail is not None
    changes = updated_ev.detail["changes"]
    assert changes["name"] == {"from": "Original", "to": "Renamed"}
    assert changes["operations"]["from_count"] == 1
    assert changes["operations"]["to_count"] == 2

    cancelled = await store.cancel(updated.id, actor="bob")
    cancelled_ev = next(e for e in cancelled.events if e.event == "cancelled")
    assert cancelled_ev.detail == {"previous_status": "draft"}


@pytest.mark.asyncio
async def test_list_events_filters_and_pagination(store: ScheduledChangeStore):
    a = await store.create(
        ChangeCreateData(
            name="Alpha change",
            zone="alpha.example.",
            operations=[_op("a")],
            created_by="alice",
        )
    )
    b = await store.create(
        ChangeCreateData(
            name="Beta change",
            zone="beta.example.",
            operations=[_op("b")],
            created_by="bob",
        )
    )
    await store.update(a.id, ChangeUpdateData(name="Alpha renamed", actor="carol"))

    by_zone, total = await store.list_events(zone="alpha.example.")
    assert total >= 2
    assert all(e.zone == "alpha.example." for e in by_zone)
    assert all(e.change_id == a.id for e in by_zone)

    by_event, _ = await store.list_events(events=["updated"])
    assert all(e.event == "updated" for e in by_event)
    assert any(e.change_id == a.id for e in by_event)

    by_q, _ = await store.list_events(q="Beta")
    assert any(e.change_id == b.id for e in by_q)

    page1, total_all = await store.list_events(limit=1, offset=0)
    assert len(page1) == 1
    page2, _ = await store.list_events(limit=1, offset=1)
    assert len(page2) == 1
    assert page1[0].id != page2[0].id
    assert total_all >= 3


@pytest.mark.asyncio
async def test_concurrent_readers_and_writers_share_the_connection(
    store: ScheduledChangeStore,
):
    """The scheduler loop and API requests share one connection.

    Without serialised transactions, an interleaved commit fails with
    "cannot commit transaction - SQL statements in progress".
    """
    past = datetime.now(UTC) - timedelta(seconds=5)
    changes = [
        await store.create(
            ChangeCreateData(
                name=f"Concurrent {i}",
                zone="example.com.",
                operations=[_op(f"host{i}")],
                scheduled_at=past,
                not_valid_after=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        for i in range(40)
    ]

    async def claim() -> None:
        for _ in range(40):
            await store.claim_due("worker", lease_ttl=60)

    async def write() -> None:
        for change in changes:
            await store.add_event(change.id, "poked", actor="tester")

    async def read() -> None:
        for _ in range(20):
            await store.list_changes()
            await store.count_pending()

    async def expire() -> None:
        for _ in range(40):
            await store.expire_overdue()

    results = await asyncio.gather(
        claim(),
        write(),
        read(),
        expire(),
        write(),
        claim(),
        return_exceptions=True,
    )
    assert [r for r in results if isinstance(r, BaseException)] == []


@pytest.mark.asyncio
async def test_update_clears_schedule_back_to_draft(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="Timed",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=datetime.now(UTC) + timedelta(hours=2),
            not_valid_after=datetime.now(UTC) + timedelta(hours=3),
        )
    )
    assert change.status == "scheduled"

    updated = await store.update(
        change.id,
        ChangeUpdateData(clear_scheduled_at=True, clear_not_valid_after=True),
    )
    assert updated.status == "draft"
    assert updated.scheduled_at is None
    assert updated.not_valid_after is None


@pytest.mark.asyncio
async def test_update_replaces_operations_and_prerequisites(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="Replace me",
            zone="example.com.",
            operations=[_op("www"), _op("old")],
            prerequisites=[ChangePrerequisite(prereq_type="nxdomain", name="www")],
        )
    )
    assert len(change.operations) == 2

    updated = await store.update(
        change.id,
        ChangeUpdateData(
            operations=[_op("www")],
            prerequisites=[
                ChangePrerequisite(prereq_type="yxrrset", name="www", rdtype="A"),
            ],
        ),
    )
    assert [op.name for op in updated.operations] == ["www"]
    assert len(updated.prerequisites) == 1
    assert updated.prerequisites[0].prereq_type == "yxrrset"


@pytest.mark.asyncio
async def test_update_rejects_empty_operations(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(name="Keep ops", zone="example.com.", operations=[_op()])
    )
    with pytest.raises(ValueError, match="at least one operation"):
        await store.update(change.id, ChangeUpdateData(operations=[]))

    # The original operations survive the rejected update.
    reloaded = await store.get(change.id)
    assert reloaded is not None
    assert len(reloaded.operations) == 1


@pytest.mark.asyncio
async def test_update_clears_description(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(
            name="Described",
            zone="example.com.",
            operations=[_op()],
            description="some notes",
        )
    )
    assert change.description == "some notes"

    updated = await store.update(change.id, ChangeUpdateData(clear_description=True))
    assert updated.description is None


@pytest.mark.asyncio
async def test_update_rejects_non_editable_status(store: ScheduledChangeStore):
    change = await store.create(
        ChangeCreateData(name="Done", zone="example.com.", operations=[_op()])
    )
    await store.cancel(change.id, actor="bob")

    with pytest.raises(ValueError, match="Cannot edit change in status"):
        await store.update(change.id, ChangeUpdateData(name="Nope"))


@pytest.mark.asyncio
async def test_claim_due_and_lease(store: ScheduledChangeStore):
    past = datetime.now(UTC) - timedelta(seconds=5)
    change = await store.create(
        ChangeCreateData(
            name="Due",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=past,
            not_valid_after=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    claimed = await store.claim_due("worker-1", lease_ttl=120)
    assert claimed is not None
    assert claimed.id == change.id
    assert claimed.status == "running"
    assert claimed.attempts == 1

    # Second claim should get nothing (lease held)
    claimed2 = await store.claim_due("worker-2", lease_ttl=120)
    assert claimed2 is None


@pytest.mark.asyncio
async def test_mark_applied(store: ScheduledChangeStore):
    past = datetime.now(UTC) - timedelta(seconds=5)
    change = await store.create(
        ChangeCreateData(
            name="Apply",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=past,
            not_valid_after=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await store.claim_due("worker-1", lease_ttl=120)
    applied = await store.mark_applied(
        change.id, result_rcode="NOERROR", new_serial=42, actor="worker-1"
    )
    assert applied.status == "applied"
    assert applied.new_serial == 42
    assert applied.result_rcode == "NOERROR"


@pytest.mark.asyncio
async def test_mark_failed_retries_then_fails(store: ScheduledChangeStore):
    past = datetime.now(UTC) - timedelta(seconds=5)
    change = await store.create(
        ChangeCreateData(
            name="Retry",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=past,
            not_valid_after=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    claimed = await store.claim_due("worker-1", lease_ttl=120)
    assert claimed is not None

    # First failure — should go back to scheduled for retry
    failed = await store.mark_failed(
        change.id,
        "prereq failed",
        max_attempts=3,
        retry_backoff=1,
        result_rcode="YXRRSET",
    )
    assert failed.status == "scheduled"
    assert failed.next_attempt_at is not None

    # Exhaust attempts
    for _ in range(2):
        claimed = await store.claim_due("worker-1", lease_ttl=120)
        if claimed is None:
            # next_attempt_at may be in the future; force it
            import aiosqlite

            async with aiosqlite.connect(store.database_path) as db:
                await db.execute(
                    "UPDATE scheduled_changes "
                    "SET next_attempt_at = NULL, lease_expires_at = NULL "
                    "WHERE id = ?",
                    (change.id,),
                )
                await db.commit()
            claimed = await store.claim_due("worker-1", lease_ttl=120)
        assert claimed is not None
        failed = await store.mark_failed(
            change.id, "still failing", max_attempts=3, retry_backoff=1
        )

    assert failed.status == "failed"


@pytest.mark.asyncio
async def test_expire_overdue(store: ScheduledChangeStore):
    past = datetime.now(UTC) - timedelta(hours=2)
    change = await store.create(
        ChangeCreateData(
            name="Expired",
            zone="example.com.",
            operations=[_op()],
            scheduled_at=past,
            not_valid_after=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    expired_ids = await store.expire_overdue()
    assert change.id in expired_ids
    got = await store.get(change.id)
    assert got is not None
    assert got.status == "expired"


@pytest.mark.asyncio
async def test_find_conflicts(store: ScheduledChangeStore):
    await store.create(
        ChangeCreateData(
            name="First",
            zone="example.com.",
            operations=[_op("www")],
        )
    )
    second = await store.create(
        ChangeCreateData(
            name="Second",
            zone="example.com.",
            operations=[_op("www")],
        )
    )
    conflicts = await store.find_conflicts("example.com.", [_op("www")], exclude_id=second.id)
    assert len(conflicts) == 1
    assert conflicts[0][1] == "First"


@pytest.mark.asyncio
async def test_find_conflicts_ignores_applied_keeps_failed(store: ScheduledChangeStore):
    applied = await store.create(
        ChangeCreateData(
            name="Already applied",
            zone="example.com.",
            operations=[_op("www")],
        )
    )
    failed = await store.create(
        ChangeCreateData(
            name="Already failed",
            zone="example.com.",
            operations=[_op("www")],
        )
    )
    draft = await store.create(
        ChangeCreateData(
            name="Still draft",
            zone="example.com.",
            operations=[_op("www")],
        )
    )
    scheduled = await store.create(
        ChangeCreateData(
            name="Still scheduled",
            zone="example.com.",
            operations=[_op("www")],
            scheduled_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )

    import aiosqlite

    async with aiosqlite.connect(store.database_path) as db:
        await db.execute(
            "UPDATE scheduled_changes SET status = 'applied' WHERE id = ?",
            (applied.id,),
        )
        await db.execute(
            "UPDATE scheduled_changes SET status = 'failed' WHERE id = ?",
            (failed.id,),
        )
        await db.commit()

    conflicts = await store.find_conflicts("example.com.", [_op("www")], exclude_id=None)
    names = {c[1] for c in conflicts}
    assert names == {"Still draft", "Still scheduled", "Already failed"}
    assert draft.id in {c[0] for c in conflicts}
    assert scheduled.id in {c[0] for c in conflicts}
    assert failed.id in {c[0] for c in conflicts}
    assert applied.id not in {c[0] for c in conflicts}
