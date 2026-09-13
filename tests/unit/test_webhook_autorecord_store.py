"""Unit tests for recording externally-executed changes in the scheduler store."""

from pathlib import Path

import pytest
from dns_zone_manager.scheduler.store import ScheduledChangeStore

OPERATIONS = [
    {
        "action": "add",
        "name": "www.test.example.",
        "type": "A",
        "rdclass": "IN",
        "ttl": 300,
        "records": ["10.0.0.1"],
    }
]


@pytest.fixture
async def store(tmp_path: Path):
    store = ScheduledChangeStore(tmp_path / "scheduler.db")
    await store.open()
    yield store
    await store.close()


class TestRecordExternalChange:
    """record_external_change() persists already-applied writes."""

    @pytest.mark.asyncio
    async def test_records_applied_change_with_source_manual(self, store):
        change = await store.record_external_change(
            change_id="abc-123",
            name="add A www.test.example.",
            zone="test.example.",
            operations=OPERATIONS,
            actor="alice@example.com",
            result_rcode="NOERROR",
        )

        assert change.id == "abc-123"
        assert change.status == "applied"
        assert change.source == "manual"
        assert change.created_by == "alice@example.com"
        assert change.applied_at is not None
        assert change.result_rcode == "NOERROR"
        assert change.scheduled_at is None
        assert len(change.operations) == 1
        assert change.operations[0].records == ["10.0.0.1"]

    @pytest.mark.asyncio
    async def test_generates_id_when_not_supplied(self, store):
        change = await store.record_external_change(
            name="manual edit",
            zone="test.example.",
            operations=OPERATIONS,
        )

        assert change.id

    @pytest.mark.asyncio
    async def test_normalizes_zone_trailing_dot(self, store):
        change = await store.record_external_change(
            name="manual edit",
            zone="test.example",
            operations=OPERATIONS,
        )

        assert change.zone == "test.example."

    @pytest.mark.asyncio
    async def test_records_audit_trail_with_trigger(self, store):
        change = await store.record_external_change(
            change_id="abc-123",
            name="manual edit",
            zone="test.example.",
            operations=OPERATIONS,
            actor="alice@example.com",
        )

        events = {e.event: e for e in change.events}
        assert set(events) == {"created", "applied"}
        assert events["applied"].actor == "alice@example.com"
        assert events["applied"].detail is not None
        assert events["applied"].detail["trigger"] == "manual"
        assert events["applied"].detail["source"] == "manual"

    @pytest.mark.asyncio
    async def test_failed_change_keeps_error_and_no_applied_at(self, store):
        change = await store.record_external_change(
            name="manual edit",
            zone="test.example.",
            operations=OPERATIONS,
            status="failed",
            error="Prerequisite failed: NXRRSET",
            result_rcode="NXRRSET",
        )

        assert change.status == "failed"
        assert change.applied_at is None
        assert change.last_error == "Prerequisite failed: NXRRSET"
        assert {e.event for e in change.events} == {"created", "failed"}

    @pytest.mark.asyncio
    async def test_missing_ttl_defaults(self, store):
        change = await store.record_external_change(
            name="manual delete",
            zone="test.example.",
            operations=[
                {
                    "action": "delete",
                    "name": "old.test.example.",
                    "type": "TXT",
                    "ttl": None,
                    "records": [],
                }
            ],
        )

        assert change.operations[0].ttl == 3600
        assert change.operations[0].records is None

    @pytest.mark.asyncio
    async def test_long_name_is_truncated_to_column_limit(self, store):
        change = await store.record_external_change(
            name="x" * 500,
            zone="test.example.",
            operations=OPERATIONS,
        )

        assert len(change.name) == 200

    @pytest.mark.asyncio
    async def test_autorecorded_change_cannot_be_reverted(self, store):
        from dns_zone_manager.scheduler.revert import can_revert

        change = await store.record_external_change(
            name="manual edit",
            zone="test.example.",
            operations=OPERATIONS,
        )

        # No pre-apply snapshots were captured, so there is nothing to restore
        assert can_revert(change) is False


class TestSourceFilter:
    """Listing changes by origin."""

    @pytest.mark.asyncio
    async def test_filters_by_source(self, store):
        from dns_zone_manager.models.requests import AtomicOperation
        from dns_zone_manager.scheduler.store import ChangeCreateData

        await store.create(
            ChangeCreateData(
                name="scheduled one",
                zone="test.example.",
                operations=[
                    AtomicOperation(action="add", name="a", type="A", records=["10.0.0.1"])
                ],
            )
        )
        await store.record_external_change(
            name="manual one",
            zone="test.example.",
            operations=OPERATIONS,
        )

        manual = await store.list_changes(source="manual")
        scheduler = await store.list_changes(source="scheduler")
        everything = await store.list_changes()

        assert [c.name for c in manual] == ["manual one"]
        assert [c.name for c in scheduler] == ["scheduled one"]
        assert len(everything) == 2

    @pytest.mark.asyncio
    async def test_scheduler_created_changes_default_to_scheduler_source(self, store):
        from dns_zone_manager.models.requests import AtomicOperation
        from dns_zone_manager.scheduler.store import ChangeCreateData

        change = await store.create(
            ChangeCreateData(
                name="scheduled one",
                zone="test.example.",
                operations=[
                    AtomicOperation(action="add", name="a", type="A", records=["10.0.0.1"])
                ],
            )
        )

        assert change.source == "scheduler"

    @pytest.mark.asyncio
    async def test_source_combines_with_status_filter(self, store):
        await store.record_external_change(
            name="manual applied",
            zone="test.example.",
            operations=OPERATIONS,
        )
        await store.record_external_change(
            name="manual failed",
            zone="test.example.",
            operations=OPERATIONS,
            status="failed",
        )

        applied = await store.list_changes(source="manual", statuses=["applied"])

        assert [c.name for c in applied] == ["manual applied"]
