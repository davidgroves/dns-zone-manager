"""Background scheduler that claims and executes due scheduled changes."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid

from dns_zone_manager.config import RetentionSettings, SchedulerSettings
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient
from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    retention_errors_total,
    scheduled_changes_expired_total,
    scheduled_changes_pending,
)
from dns_zone_manager.scheduler.executor import execute_change
from dns_zone_manager.scheduler.retention import run_retention
from dns_zone_manager.scheduler.store import ScheduledChangeStore

logger = logging.getLogger(__name__)


def _lease_owner_id() -> str:
    """Generate a unique lease owner ID for this process."""
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


async def run_scheduler_loop(
    store: ScheduledChangeStore,
    dns_client: DNSClient,
    zone_cache: ZoneCache,
    settings: SchedulerSettings,
    retention: RetentionSettings | None = None,
) -> None:
    """Poll for due scheduled changes and execute them.

    Runs until cancelled. Safe to run in multiple workers thanks to lease
    claiming; on PostgreSQL the claim also skips rows locked by another
    instance, so several application instances can share one database.

    Retention runs only on idle ticks (no due change claimed) so a vacuum
    never delays applying a scheduled change.
    """
    owner = _lease_owner_id()
    retention_settings = retention or RetentionSettings(enabled=False)
    # Wait one full interval after startup before the first pass so a converting
    # VACUUM cannot contend with the initial wave of due changes.
    last_retention_at = time.monotonic()
    log_internal_event(
        "scheduler_started",
        logger,
        lease_owner=owner,
        poll_interval=settings.poll_interval,
        database_backend=store.backend,
        retention_enabled=retention_settings.enabled,
        retention_interval=retention_settings.interval,
    )

    try:
        while True:
            try:
                expired = await store.expire_overdue()
                for change_id in expired:
                    scheduled_changes_expired_total.inc()
                    log_internal_event(
                        "scheduled_change_expired",
                        logger,
                        change_id=change_id,
                    )

                pending = await store.count_pending()
                scheduled_changes_pending.set(pending)

                change = await store.claim_due(owner, settings.lease_ttl)
                if change is not None:
                    log_internal_event(
                        "scheduled_change_claimed",
                        logger,
                        change_id=change.id,
                        zone=change.zone,
                        name=change.name,
                        attempt=change.attempts,
                    )

                    result = await execute_change(
                        change,
                        store=store,
                        dns_client=dns_client,
                        zone_cache=zone_cache,
                        max_attempts=settings.max_attempts,
                        retry_backoff=settings.retry_backoff,
                        actor=owner,
                        trigger="scheduler",
                    )

                    if result.success:
                        log_internal_event(
                            "scheduled_change_applied",
                            logger,
                            change_id=change.id,
                            zone=change.zone,
                            new_serial=result.new_serial,
                        )
                    else:
                        log_internal_event(
                            "scheduled_change_failed",
                            logger,
                            level="WARNING",
                            change_id=change.id,
                            zone=change.zone,
                            error=result.error,
                            result_rcode=result.result_rcode,
                        )
                    # Process next change immediately if one was found
                    continue

                # Idle tick: run retention when the interval has elapsed.
                if retention_settings.enabled:
                    now_mono = time.monotonic()
                    if (now_mono - last_retention_at) >= retention_settings.interval:
                        try:
                            await run_retention(store, retention_settings)
                            last_retention_at = now_mono
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            retention_errors_total.inc()
                            log_internal_event(
                                "retention_pass_failed",
                                logger,
                                level="ERROR",
                                error=str(e),
                            )
                            # Still advance the timer so a persistent failure
                            # does not retry every poll interval.
                            last_retention_at = now_mono

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_internal_event(
                    "scheduler_tick_error",
                    logger,
                    level="ERROR",
                    error=str(e),
                )

            await asyncio.sleep(settings.poll_interval)

    except asyncio.CancelledError:
        log_internal_event("scheduler_stopped", logger, lease_owner=owner)
        raise
