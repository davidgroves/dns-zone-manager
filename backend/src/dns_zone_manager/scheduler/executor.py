"""Execute a scheduled change against DNS."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient, PrerequisiteFailedError, UpdateError
from dns_zone_manager.dns.update_builder import (
    UpdateBuildError,
    apply_cache_updates,
    build_update,
)
from dns_zone_manager.metrics import (
    ddns_updates_failed,
    ddns_updates_successful,
    rrset_adds_total,
    rrset_deletes_total,
    rrset_replaces_total,
    scheduled_change_lateness_seconds,
    scheduled_changes_applied_total,
    scheduled_changes_failed_total,
)
from dns_zone_manager.models.scheduled import ScheduledChangeResponse
from dns_zone_manager.scheduler.revert import capture_prior_state, forward_ops_to_atomic
from dns_zone_manager.scheduler.store import ScheduledChangeStore

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of executing a scheduled change."""

    success: bool
    message: str
    result_rcode: str | None = None
    new_serial: int | None = None
    error: str | None = None


async def execute_change(
    change: ScheduledChangeResponse,
    *,
    store: ScheduledChangeStore,
    dns_client: DNSClient,
    zone_cache: ZoneCache,
    max_attempts: int,
    retry_backoff: int,
    actor: str | None = None,
    trigger: str = "scheduler",
) -> ExecutionResult:
    """Build and send the DDNS UPDATE for a claimed scheduled change.

    Updates the store with applied/failed status and refreshes the zone cache.
    Captures prior RRset state for delete/replace ops before the UPDATE so the
    change can later be reverted.
    """
    zone = change.zone
    if not zone.endswith("."):
        zone = zone + "."

    try:
        # Refresh cache so auto-prereqs and snapshots reflect current DNS state
        try:
            zone_cache.refresh_zone(zone)
        except Exception as e:
            logger.warning("Cache refresh before scheduled change failed: %s", e)

        snapshots = capture_prior_state(zone, change.operations, zone_cache)
        await store.save_operation_snapshots(change.id, snapshots)

        built = build_update(
            zone,
            forward_ops_to_atomic(change.operations),
            dns_client,
            zone_cache,
            prerequisites=change.prerequisites,
            auto_prerequisites=change.auto_prerequisites,
            validate_cache_state=False,
        )

        dns_client._send_update(built.update, zone)

        apply_cache_updates(zone, built.cache_updates, zone_cache)
        for cu in built.cache_updates:
            if cu.action == "add":
                rrset_adds_total.labels(zone=zone).inc()
            elif cu.action == "delete":
                rrset_deletes_total.labels(zone=zone).inc()
            elif cu.action == "replace":
                rrset_replaces_total.labels(zone=zone).inc()

        ddns_updates_successful.inc()

        new_serial = None
        cached = zone_cache.get_zone(zone)
        if cached is not None:
            new_serial = cached.serial

        if change.scheduled_at is not None and change.applied_at is None:
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            lateness = max(0.0, (now - change.scheduled_at).total_seconds())
            scheduled_change_lateness_seconds.observe(lateness)

        await store.mark_applied(
            change.id,
            result_rcode="NOERROR",
            new_serial=new_serial,
            actor=actor,
            trigger=trigger,
        )
        scheduled_changes_applied_total.labels(trigger=trigger).inc()

        return ExecutionResult(
            success=True,
            message="Change applied successfully",
            result_rcode="NOERROR",
            new_serial=new_serial,
        )

    except UpdateBuildError as e:
        ddns_updates_failed.labels(reason="build_error").inc()
        scheduled_changes_failed_total.labels(reason="build_error").inc()
        await store.mark_failed(
            change.id,
            e.message,
            max_attempts=max_attempts,
            retry_backoff=retry_backoff,
            actor=actor,
        )
        return ExecutionResult(success=False, message=e.message, error=e.message)

    except PrerequisiteFailedError as e:
        ddns_updates_failed.labels(reason="prereq_failed").inc()
        scheduled_changes_failed_total.labels(reason="prereq_failed").inc()
        try:
            zone_cache.refresh_zone(zone)
        except Exception:
            pass
        error = str(e)
        await store.mark_failed(
            change.id,
            error,
            max_attempts=max_attempts,
            retry_backoff=retry_backoff,
            actor=actor,
            result_rcode=e.rcode_text,
        )
        return ExecutionResult(
            success=False,
            message=error,
            result_rcode=e.rcode_text,
            error=error,
        )

    except UpdateError as e:
        ddns_updates_failed.labels(reason="update_error").inc()
        scheduled_changes_failed_total.labels(reason="update_error").inc()
        error = str(e)
        await store.mark_failed(
            change.id,
            error,
            max_attempts=max_attempts,
            retry_backoff=retry_backoff,
            actor=actor,
        )
        return ExecutionResult(success=False, message=error, error=error)

    except Exception as e:
        ddns_updates_failed.labels(reason="unexpected").inc()
        scheduled_changes_failed_total.labels(reason="unexpected").inc()
        error = f"Unexpected error: {e}"
        await store.mark_failed(
            change.id,
            error,
            max_attempts=max_attempts,
            retry_backoff=retry_backoff,
            actor=actor,
        )
        return ExecutionResult(success=False, message=error, error=error)
