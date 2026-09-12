"""Capture pre-apply snapshots and build inverse operations for revert."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    RevertOperation,
    ScheduledChangeResponse,
    ScheduledOperationResponse,
)
from dns_zone_manager.scheduler.store import OpSnapshot

REVERT_WARNING = (
    "This reverts this scheduled change only. Other DNS changes may have been "
    "made since it was applied; the zone may not return to its earlier overall state."
)


@dataclass
class CaptureResult:
    """Snapshots keyed by operation sequence number."""

    snapshots: list[OpSnapshot]


def capture_prior_state(
    zone: str,
    operations: list[ScheduledOperationResponse] | list[AtomicOperation],
    zone_cache: ZoneCache,
    *,
    now: datetime | None = None,
) -> list[OpSnapshot]:
    """Snapshot current RRset state for delete/replace ops before apply.

    ADD operations do not need a prior snapshot (revert is a value-delete of
    the records that were added). For delete/replace, records the ttl and
    rdata present in the cache (or nulls if the RRset is absent).
    """
    if not zone.endswith("."):
        zone = zone + "."
    snapshot_at = now or datetime.now(UTC)
    snapshots: list[OpSnapshot] = []

    for seq, op in enumerate(operations):
        action = op.action
        if action not in ("delete", "replace"):
            # Still mark ADD ops as snapshotted so can_revert knows apply ran
            # under the new schema. prior_* stay null.
            snapshots.append(
                OpSnapshot(
                    seq=seq,
                    prior_ttl=None,
                    prior_records=None,
                    snapshot_at=snapshot_at,
                )
            )
            continue

        rdclass = getattr(op, "rdclass", "IN") or "IN"
        existing = zone_cache.get_rrset(zone, op.name, op.type, rdclass)
        if existing is None:
            snapshots.append(
                OpSnapshot(
                    seq=seq,
                    prior_ttl=None,
                    prior_records=None,
                    snapshot_at=snapshot_at,
                )
            )
        else:
            snapshots.append(
                OpSnapshot(
                    seq=seq,
                    prior_ttl=existing.ttl,
                    prior_records=list(existing.records),
                    snapshot_at=snapshot_at,
                )
            )
    return snapshots


def can_revert(change: ScheduledChangeResponse) -> bool:
    """Return True if this applied change has enough snapshot data to revert."""
    if change.status != "applied":
        return False
    if not change.operations:
        return False
    for op in change.operations:
        if op.action in ("delete", "replace") and op.snapshot_at is None:
            return False
        # ADD-only changes also need snapshot_at so we know apply ran post-feature
        if op.action == "add" and op.snapshot_at is None:
            return False
    return True


def build_revert_operations(change: ScheduledChangeResponse) -> list[RevertOperation]:
    """Build inverse ADD/DELETE operations for an applied change.

    Raises ValueError when the change cannot be reverted.
    """
    if not can_revert(change):
        raise ValueError(
            "Change cannot be reverted: it is not applied or is missing "
            "pre-apply snapshots (applied before revert support)"
        )

    revert_ops: list[RevertOperation] = []
    for op in change.operations:
        if op.action == "add":
            if not op.records:
                continue
            revert_ops.append(
                RevertOperation(
                    action="delete",
                    name=op.name,
                    type=op.type,
                    rdclass=op.rdclass,
                    ttl=op.ttl,
                    records=list(op.records),
                )
            )
        elif op.action == "delete":
            if op.prior_records is None:
                # Snapshot said the RRset was already absent — nothing to restore
                continue
            revert_ops.append(
                RevertOperation(
                    action="add",
                    name=op.name,
                    type=op.type,
                    rdclass=op.rdclass,
                    ttl=op.prior_ttl if op.prior_ttl is not None else op.ttl,
                    records=list(op.prior_records),
                )
            )
        elif op.action == "replace":
            if op.records:
                revert_ops.append(
                    RevertOperation(
                        action="delete",
                        name=op.name,
                        type=op.type,
                        rdclass=op.rdclass,
                        ttl=op.ttl,
                        records=list(op.records),
                    )
                )
            if op.prior_records is not None:
                revert_ops.append(
                    RevertOperation(
                        action="add",
                        name=op.name,
                        type=op.type,
                        rdclass=op.rdclass,
                        ttl=op.prior_ttl if op.prior_ttl is not None else op.ttl,
                        records=list(op.prior_records),
                    )
                )
    return revert_ops


def revert_ops_to_atomic(operations: list[RevertOperation]) -> list[AtomicOperation]:
    """Convert revert preview ops into AtomicOperation for build_update."""
    return [
        AtomicOperation(
            action=op.action,
            name=op.name,
            type=op.type,
            rdclass=op.rdclass,
            ttl=op.ttl,
            records=op.records,
        )
        for op in operations
    ]


def forward_ops_to_atomic(
    operations: list[ScheduledOperationResponse],
) -> list[AtomicOperation]:
    """Strip snapshot fields for DDNS construction."""
    return [op.to_atomic() for op in operations]
