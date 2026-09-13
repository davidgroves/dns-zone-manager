"""Unit tests for scheduled-change revert helpers."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from dns_zone_manager.dns.client import RRsetInfo
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    ScheduledChangeResponse,
    ScheduledOperationResponse,
)
from dns_zone_manager.scheduler.revert import (
    build_revert_operations,
    can_revert,
    capture_prior_state,
)
from dns_zone_manager.scheduler.store import (
    ChangeCreateData,
    OpSnapshot,
    ScheduledChangeStore,
)


def _op_response(
    action: str,
    name: str = "www",
    *,
    records: list[str] | None = None,
    prior_ttl: int | None = None,
    prior_records: list[str] | None = None,
    snapshot_at: datetime | None = None,
    ttl: int = 3600,
) -> ScheduledOperationResponse:
    if records is None and action != "delete":
        records = ["192.0.2.1"]
    return ScheduledOperationResponse(
        action=action,  # type: ignore[arg-type]
        name=name,
        type="A",
        rdclass="IN",
        ttl=ttl,
        records=records,
        prior_ttl=prior_ttl,
        prior_records=prior_records,
        snapshot_at=snapshot_at,
    )


def _change(
    *ops: ScheduledOperationResponse,
    status: str = "applied",
) -> ScheduledChangeResponse:
    now = datetime.now(UTC)
    return ScheduledChangeResponse(
        id="chg-1",
        name="Test",
        description=None,
        zone="example.com.",
        status=status,  # type: ignore[arg-type]
        scheduled_at=None,
        not_valid_after=None,
        auto_prerequisites=True,
        created_at=now,
        created_by=None,
        updated_at=now,
        attempts=1,
        next_attempt_at=None,
        last_error=None,
        applied_at=now if status == "applied" else None,
        result_rcode="NOERROR" if status == "applied" else None,
        new_serial=None,
        reverted_at=None,
        operations=list(ops),
        prerequisites=[],
    )


class TestCapturePriorState:
    def test_add_gets_snapshot_marker_without_priors(self):
        cache = MagicMock()
        ops = [AtomicOperation(action="add", name="www", type="A", ttl=3600, records=["192.0.2.1"])]
        snaps = capture_prior_state("example.com.", ops, cache)
        assert len(snaps) == 1
        assert snaps[0].seq == 0
        assert snaps[0].prior_ttl is None
        assert snaps[0].prior_records is None
        assert snaps[0].snapshot_at is not None
        cache.get_rrset.assert_not_called()

    def test_delete_captures_existing_rrset(self):
        cache = MagicMock()
        cache.get_rrset.return_value = RRsetInfo(
            name="www.example.com.",
            ttl=300,
            rdtype="A",
            rdclass="IN",
            records=["192.0.2.9"],
        )
        ops = [AtomicOperation(action="delete", name="www", type="A", ttl=3600)]
        snaps = capture_prior_state("example.com.", ops, cache)
        assert snaps[0].prior_ttl == 300
        assert snaps[0].prior_records == ["192.0.2.9"]
        assert snaps[0].snapshot_at is not None

    def test_replace_captures_absent_as_nulls(self):
        cache = MagicMock()
        cache.get_rrset.return_value = None
        ops = [
            AtomicOperation(
                action="replace",
                name="www",
                type="A",
                ttl=600,
                records=["192.0.2.2"],
            )
        ]
        snaps = capture_prior_state("example.com.", ops, cache)
        assert snaps[0].prior_ttl is None
        assert snaps[0].prior_records is None
        assert snaps[0].snapshot_at is not None


class TestCanRevert:
    def test_requires_applied_status(self):
        snap = datetime.now(UTC)
        chg = _change(_op_response("add", snapshot_at=snap), status="draft")
        assert can_revert(chg) is False

    def test_add_only_with_snapshot(self):
        snap = datetime.now(UTC)
        assert can_revert(_change(_op_response("add", snapshot_at=snap))) is True

    def test_missing_snapshot_blocks_revert(self):
        assert can_revert(_change(_op_response("add", snapshot_at=None))) is False
        assert (
            can_revert(
                _change(
                    _op_response(
                        "delete",
                        records=None,
                        prior_records=["192.0.2.1"],
                        prior_ttl=3600,
                        snapshot_at=None,
                    )
                )
            )
            is False
        )

    def test_delete_with_snapshot_ok(self):
        snap = datetime.now(UTC)
        assert (
            can_revert(
                _change(
                    _op_response(
                        "delete",
                        records=None,
                        prior_ttl=3600,
                        prior_records=["192.0.2.1"],
                        snapshot_at=snap,
                    )
                )
            )
            is True
        )


class TestBuildRevertOperations:
    def test_add_becomes_delete(self):
        snap = datetime.now(UTC)
        ops = build_revert_operations(
            _change(_op_response("add", records=["192.0.2.1"], snapshot_at=snap))
        )
        assert len(ops) == 1
        assert ops[0].action == "delete"
        assert ops[0].records == ["192.0.2.1"]

    def test_delete_becomes_add_of_prior(self):
        snap = datetime.now(UTC)
        ops = build_revert_operations(
            _change(
                _op_response(
                    "delete",
                    records=None,
                    prior_ttl=300,
                    prior_records=["192.0.2.9", "192.0.2.10"],
                    snapshot_at=snap,
                )
            )
        )
        assert len(ops) == 1
        assert ops[0].action == "add"
        assert ops[0].ttl == 300
        assert ops[0].records == ["192.0.2.9", "192.0.2.10"]

    def test_whole_rrset_delete_with_null_prior_is_noop(self):
        snap = datetime.now(UTC)
        ops = build_revert_operations(
            _change(
                _op_response(
                    "delete",
                    records=None,
                    prior_ttl=None,
                    prior_records=None,
                    snapshot_at=snap,
                )
            )
        )
        assert ops == []

    def test_replace_deletes_new_then_adds_prior(self):
        snap = datetime.now(UTC)
        ops = build_revert_operations(
            _change(
                _op_response(
                    "replace",
                    records=["192.0.2.2"],
                    prior_ttl=600,
                    prior_records=["192.0.2.1"],
                    snapshot_at=snap,
                )
            )
        )
        assert [o.action for o in ops] == ["delete", "add"]
        assert ops[0].records == ["192.0.2.2"]
        assert ops[1].records == ["192.0.2.1"]
        assert ops[1].ttl == 600

    def test_raises_when_not_revertible(self):
        with pytest.raises(ValueError, match="cannot be reverted"):
            build_revert_operations(_change(_op_response("add", snapshot_at=None)))


@pytest.mark.asyncio
async def test_save_snapshots_and_mark_reverted(tmp_path: Path):
    store = ScheduledChangeStore(tmp_path / "snap.db")
    await store.open()
    try:
        change = await store.create(
            ChangeCreateData(
                name="Snap me",
                zone="example.com.",
                operations=[AtomicOperation(action="delete", name="www", type="A", ttl=3600)],
            )
        )
        now = datetime.now(UTC)
        await store.save_operation_snapshots(
            change.id,
            [
                OpSnapshot(
                    seq=0,
                    prior_ttl=300,
                    prior_records=["192.0.2.1"],
                    snapshot_at=now,
                )
            ],
        )
        # Pretend it was applied so mark_reverted is allowed
        async with aiosqlite.connect(store.database_path) as db:
            await db.execute(
                "UPDATE scheduled_changes SET status = 'applied' WHERE id = ?",
                (change.id,),
            )
            await db.commit()

        reloaded = await store.get(change.id)
        assert reloaded is not None
        assert reloaded.operations[0].prior_ttl == 300
        assert reloaded.operations[0].prior_records == ["192.0.2.1"]
        assert reloaded.operations[0].snapshot_at is not None

        reverted = await store.mark_reverted(
            change.id, result_rcode="NOERROR", actor="alice", operations_count=1
        )
        assert reverted.status == "reverted"
        assert reverted.reverted_at is not None
        assert any(e.event == "reverted" for e in reverted.events)
    finally:
        await store.close()
