"""Retention maintenance for completed scheduled changes and audit events.

Deletes only terminal rows (and their cascaded children). Changes with a
future ``scheduled_at`` or ``next_attempt_at`` are never touched. Age and size
policies can run independently; size trim repeats until the database is under
the configured limit or ``max_trim_passes`` is exhausted.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from dns_zone_manager.config import RetentionSettings
from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    retention_changes_purged_total,
    retention_database_bytes,
    retention_duration_seconds,
    retention_events_purged_total,
    retention_last_success_timestamp_seconds,
)
from dns_zone_manager.scheduler.store import PurgeBatchResult, ScheduledChangeStore

logger = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    """Summary of one retention maintenance pass."""

    purged_by_age: int = 0
    purged_by_size: int = 0
    events_purged: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    trim_passes: int = 0
    eligible_remaining: int = 0
    duration_ms: float = 0.0
    dry_run: bool = False
    skipped: bool = False
    over_limit_no_candidates: bool = False
    by_status: dict[str, int] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _record_purge(
    batch: PurgeBatchResult,
    reason: str,
    totals: dict[str, int],
    *,
    dry_run: bool,
) -> None:
    for status, count in batch.by_status.items():
        totals[status] = totals.get(status, 0) + count
        if not dry_run and count:
            retention_changes_purged_total.labels(reason=reason, status=status).inc(count)
    if not dry_run and batch.events_deleted:
        retention_events_purged_total.labels(reason=reason).inc(batch.events_deleted)


async def run_retention(
    store: ScheduledChangeStore,
    settings: RetentionSettings,
    *,
    now: datetime | None = None,
) -> RetentionResult:
    """Run one retention pass against the scheduled-change store."""
    if not settings.enabled:
        return RetentionResult(skipped=True)

    now = now or _utcnow()
    started = time.perf_counter()
    result = RetentionResult(dry_run=settings.dry_run)
    statuses = list(settings.statuses)
    by_status: dict[str, int] = {}

    result.bytes_before = await store.database_size_bytes()
    retention_database_bytes.set(result.bytes_before)

    # --- Age-based purge -------------------------------------------------
    if settings.max_age_days > 0:
        cutoff = now - timedelta(days=settings.max_age_days)
        while True:
            batch = await store.purge_by_age(
                cutoff,
                statuses,
                now=now,
                # dry_run cannot page: the same candidates would be returned forever
                batch_size=1_000_000 if settings.dry_run else 500,
                dry_run=settings.dry_run,
            )
            if not batch.changes_deleted:
                break
            result.purged_by_age += batch.changes_deleted
            result.events_purged += batch.events_deleted
            _record_purge(batch, "age", by_status, dry_run=settings.dry_run)
            if settings.dry_run:
                break

        if result.purged_by_age and not settings.dry_run and settings.vacuum != "off":
            await store.reclaim_space(settings.vacuum)

    # --- Size-based trim -------------------------------------------------
    if settings.max_database_mb > 0:
        limit_bytes = settings.max_database_mb * 1024 * 1024
        size = await store.database_size_bytes()
        while size > limit_bytes and result.trim_passes < settings.max_trim_passes:
            eligible = await store.count_purgeable(statuses, now=now)
            if eligible <= 0:
                result.over_limit_no_candidates = True
                log_internal_event(
                    "retention_over_limit_no_candidates",
                    logger,
                    level="WARNING",
                    database_bytes=size,
                    limit_bytes=limit_bytes,
                    vacuum=settings.vacuum,
                )
                break

            to_delete = max(1, math.ceil(eligible * settings.trim_percent / 100))
            batch = await store.purge_oldest(
                to_delete,
                statuses,
                now=now,
                dry_run=settings.dry_run,
            )
            if not batch.changes_deleted:
                break

            result.trim_passes += 1
            result.purged_by_size += batch.changes_deleted
            result.events_purged += batch.events_deleted
            _record_purge(batch, "size", by_status, dry_run=settings.dry_run)

            if settings.dry_run:
                break

            if settings.vacuum == "off":
                log_internal_event(
                    "retention_vacuum_disabled",
                    logger,
                    level="WARNING",
                    message="Size cap cannot shrink the database file while vacuum is off",
                    database_bytes=size,
                    limit_bytes=limit_bytes,
                )
                # Without reclaim, further passes cannot reduce measured size.
                break

            await store.reclaim_space(settings.vacuum)
            size = await store.database_size_bytes()

    result.bytes_after = await store.database_size_bytes()
    retention_database_bytes.set(result.bytes_after)
    result.eligible_remaining = await store.count_purgeable(statuses, now=now)
    result.by_status = by_status
    result.duration_ms = (time.perf_counter() - started) * 1000.0

    retention_duration_seconds.observe(result.duration_ms / 1000.0)
    if not settings.dry_run:
        retention_last_success_timestamp_seconds.set(now.timestamp())

    log_internal_event(
        "retention_pass_completed",
        logger,
        purged_by_age=result.purged_by_age,
        purged_by_size=result.purged_by_size,
        events_purged=result.events_purged,
        bytes_before=result.bytes_before,
        bytes_after=result.bytes_after,
        trim_passes=result.trim_passes,
        eligible_remaining=result.eligible_remaining,
        duration_ms=round(result.duration_ms, 2),
        dry_run=result.dry_run,
        over_limit_no_candidates=result.over_limit_no_candidates,
    )
    return result


__all__ = ["RetentionResult", "run_retention"]
