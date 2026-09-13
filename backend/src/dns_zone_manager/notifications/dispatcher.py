"""Queue-backed webhook dispatcher.

DNS writes are committed synchronously, so ``emit()`` only enqueues an event
and never performs I/O. A single background task drains the queue, auto-records
manual changes in the scheduler store, and POSTs to each matching target. A
slow or dead webhook endpoint therefore cannot delay a DNS update.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from dns_zone_manager.config import WebhookSettings, WebhookTarget
from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    webhook_autorecorded_changes_total,
    webhook_deliveries_total,
    webhook_delivery_duration_seconds,
    webhook_queue_depth,
    webhook_queue_dropped_total,
)
from dns_zone_manager.notifications.auth import build_auth_headers, redact
from dns_zone_manager.notifications.events import DnsChangeEvent
from dns_zone_manager.notifications.formatters import format_payload

if TYPE_CHECKING:
    from dns_zone_manager.scheduler.store import ScheduledChangeStore

logger = logging.getLogger(__name__)

# HTTP status codes worth retrying; everything else in 4xx is a client error
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


class WebhookDispatcher:
    """Delivers DNS change events to configured webhook targets."""

    def __init__(
        self,
        settings: WebhookSettings,
        *,
        store: ScheduledChangeStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a dispatcher.

        Args:
            settings: Webhook configuration
            store: Scheduled change store, used to auto-record manual changes
            transport: Optional httpx transport override (used by tests)
        """
        self.settings = settings
        self.store = store
        self._transport = transport
        self._queue: asyncio.Queue[DnsChangeEvent] = asyncio.Queue(
            maxsize=max(1, settings.queue_size)
        )
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[str, httpx.AsyncClient] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the background delivery worker."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="webhook-dispatcher")
        log_internal_event(
            "webhook_dispatcher_started",
            logger,
            targets=[t.name for t in self.settings.targets],
            queue_size=self.settings.queue_size,
        )

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Drain outstanding events, then stop the worker and close clients."""
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
        except TimeoutError:
            log_internal_event(
                "webhook_dispatcher_drain_timeout",
                logger,
                level="WARNING",
                pending=self._queue.qsize(),
            )

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._loop = None

        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        log_internal_event("webhook_dispatcher_stopped", logger)

    # -- emission ----------------------------------------------------------

    def emit(self, event: DnsChangeEvent) -> None:
        """Enqueue an event for delivery. Never blocks and never raises.

        Safe to call from synchronous DNS code both on the event loop and from
        a worker thread.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is not None:
            self._enqueue(event)
            return

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, event)
            return

        # No worker running (CLI or unit-test use): nothing to deliver to.
        log_internal_event(
            "webhook_event_discarded",
            logger,
            level="DEBUG",
            reason="dispatcher_not_running",
            zone=event.zone,
        )

    def _enqueue(self, event: DnsChangeEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            webhook_queue_dropped_total.inc()
            log_internal_event(
                "webhook_event_dropped",
                logger,
                level="WARNING",
                reason="queue_full",
                zone=event.zone,
                queue_size=self.settings.queue_size,
            )
            return
        webhook_queue_depth.set(self._queue.qsize())

    # -- worker ------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Webhook handling failed: %s", e)
            finally:
                self._queue.task_done()
                webhook_queue_depth.set(self._queue.qsize())

    async def _handle(self, event: DnsChangeEvent) -> None:
        """Auto-record the change if needed, then deliver it to every target."""
        if event.autorecord:
            event = await self._autorecord(event)

        targets = [
            target
            for target in self.settings.targets
            if target.matches_event(event.event, list(self.settings.events))
            and target.matches_zone(event.zone)
        ]
        for target in targets:
            await self._deliver(target, event)

    async def _autorecord(self, event: DnsChangeEvent) -> DnsChangeEvent:
        """Persist a manual change in the scheduler store so its link resolves.

        On failure the change ID is cleared so the notification falls back to a
        zone link rather than pointing at a change that does not exist.
        """
        if self.store is None or not self.settings.autorecord_manual_changes:
            webhook_autorecorded_changes_total.labels(outcome="skipped").inc()
            return replace(event, change_id=None, autorecord=False)

        try:
            await self.store.record_external_change(
                change_id=event.change_id,
                name=event.default_name(),
                zone=event.zone,
                operations=[op.to_dict() for op in event.operations],
                status="applied" if event.succeeded else "failed",
                actor=event.actor,
                trigger=event.trigger,
                result_rcode=event.rcode,
                error=event.error,
                occurred_at=event.timestamp,
            )
        except Exception as e:
            webhook_autorecorded_changes_total.labels(outcome="failed").inc()
            log_internal_event(
                "webhook_autorecord_failed",
                logger,
                level="WARNING",
                zone=event.zone,
                change_id=event.change_id,
                error=str(e),
            )
            return replace(event, change_id=None, autorecord=False)

        webhook_autorecorded_changes_total.labels(outcome="recorded").inc()
        log_internal_event(
            "webhook_autorecorded_change",
            logger,
            zone=event.zone,
            change_id=event.change_id,
            trigger=event.trigger,
            actor=event.actor,
        )
        return replace(event, autorecord=False)

    # -- delivery ----------------------------------------------------------

    def _client_for(self, target: WebhookTarget) -> httpx.AsyncClient:
        client = self._clients.get(target.name)
        if client is None:
            client = httpx.AsyncClient(
                timeout=target.timeout or self.settings.timeout,
                verify=target.verify_tls,
                transport=self._transport,
            )
            self._clients[target.name] = client
        return client

    async def _deliver(self, target: WebhookTarget, event: DnsChangeEvent) -> None:
        """POST one event to one target, retrying transient failures."""
        try:
            payload: dict[str, Any] = format_payload(target.type, event, self.settings.base_url)
        except Exception as e:
            webhook_deliveries_total.labels(target=target.name, outcome="format_error").inc()
            logger.warning("Webhook payload formatting failed for %s: %s", target.name, e)
            return

        # Serialise once: the HMAC must cover the exact bytes transmitted, and
        # retries must reuse the same body and timestamp to stay valid.
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = datetime.now(UTC).isoformat()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "dns-zone-manager-webhook",
            **target.headers,
            **build_auth_headers(target, body, timestamp),
        }

        client = self._client_for(target)
        url = target.url.get_secret_value()
        attempts = max(1, self.settings.max_retries)

        for attempt in range(1, attempts + 1):
            try:
                with webhook_delivery_duration_seconds.labels(target=target.name).time():
                    response = await client.post(url, content=body, headers=headers)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                outcome = "transport_error"
                detail = redact(str(e) or type(e).__name__, target)
            else:
                if response.status_code < 400:
                    webhook_deliveries_total.labels(target=target.name, outcome="success").inc()
                    log_internal_event(
                        "webhook_delivered",
                        logger,
                        target=target.name,
                        target_type=target.type,
                        status=response.status_code,
                        attempt=attempt,
                        event=event.event,
                        zone=event.zone,
                    )
                    return

                outcome = "http_error"
                detail = redact(response.text[:500], target)
                retryable = (
                    response.status_code >= 500 or response.status_code in _RETRYABLE_STATUSES
                )
                if not retryable:
                    webhook_deliveries_total.labels(target=target.name, outcome=outcome).inc()
                    log_internal_event(
                        "webhook_delivery_failed",
                        logger,
                        level="WARNING",
                        target=target.name,
                        status=response.status_code,
                        attempt=attempt,
                        retryable=False,
                        zone=event.zone,
                        error=detail,
                    )
                    return

            log_internal_event(
                "webhook_delivery_failed",
                logger,
                level="WARNING",
                target=target.name,
                outcome=outcome,
                attempt=attempt,
                max_attempts=attempts,
                retryable=True,
                zone=event.zone,
                error=detail,
            )

            if attempt < attempts:
                await asyncio.sleep(self.settings.retry_backoff * (2 ** (attempt - 1)))

        webhook_deliveries_total.labels(target=target.name, outcome="exhausted").inc()
        log_internal_event(
            "webhook_delivery_exhausted",
            logger,
            level="ERROR",
            target=target.name,
            attempts=attempts,
            zone=event.zone,
            event=event.event,
        )
