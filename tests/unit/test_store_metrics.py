"""Unit tests for scheduled change store instrumentation."""

from pathlib import Path

import pytest
from dns_zone_manager.metrics import store_errors_total, store_operation_duration_seconds
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.scheduler.store import (
    ChangeCreateData,
    ChangeUpdateData,
    ScheduledChangeStore,
)


@pytest.fixture
async def store(tmp_path: Path):
    s = ScheduledChangeStore(tmp_path / "metrics.db")
    await s.open()
    yield s
    await s.close()


def _sample(metric, suffix: str, operation: str) -> float:
    """Read one sample value from a metric via the public collect() API."""
    labels = {"operation": operation, "backend": "sqlite"}
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith(suffix) and sample.labels == labels:
                return sample.value
    return 0.0


def _error_count(operation: str) -> float:
    return _sample(store_errors_total, "_total", operation)


def _duration_count(operation: str) -> float:
    return _sample(store_operation_duration_seconds, "_count", operation)


async def test_successful_operation_is_timed(store: ScheduledChangeStore):
    before = _duration_count("create")
    await store.create(
        ChangeCreateData(
            name="timed",
            zone="example.com.",
            operations=[AtomicOperation(action="add", name="a", type="A", records=["10.0.0.1"])],
        )
    )
    assert _duration_count("create") == before + 1


async def test_nested_calls_are_counted_once(store: ScheduledChangeStore):
    """create() calls get() internally; only the outer transaction is recorded."""
    before = _duration_count("get")
    await store.create(
        ChangeCreateData(
            name="nested",
            zone="example.com.",
            operations=[AtomicOperation(action="add", name="b", type="A", records=["10.0.0.2"])],
        )
    )
    assert _duration_count("get") == before


async def test_business_errors_are_not_counted_as_store_errors(store: ScheduledChangeStore):
    """A missing change is expected control flow, not a database fault."""
    before = _error_count("update")
    with pytest.raises(KeyError):
        await store.update("no-such-change", ChangeUpdateData(name="x"))
    assert _error_count("update") == before


async def test_database_errors_are_counted(store: ScheduledChangeStore):
    """A real database failure increments the error counter."""
    before = _error_count("create")
    change = await store.create(
        ChangeCreateData(
            name="dup",
            zone="example.com.",
            operations=[AtomicOperation(action="add", name="c", type="A", records=["10.0.0.3"])],
        )
    )
    # Reusing the primary key violates the constraint inside the transaction.
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError subclass
        await store.record_external_change(
            name="dup again",
            zone="example.com.",
            operations=[],
            change_id=change.id,
        )
    assert _error_count("create") + _error_count("record_external_change") == before + 1
