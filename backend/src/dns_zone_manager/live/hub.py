"""In-process pub/sub hub for live zone change WebSocket subscribers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    zone_ws_broadcasts_total,
    zone_ws_send_errors_total,
    zone_ws_subscribers,
)

logger = logging.getLogger(__name__)


def normalize_zone_name(zone: str) -> str:
    """Normalize a zone name to lowercase with a trailing dot."""
    zone = zone.strip().lower()
    if zone and not zone.endswith("."):
        zone = zone + "."
    return zone


class ZoneChangeHub:
    """Fan-out applied zone changes to per-zone and all-zones WebSocket clients."""

    def __init__(self) -> None:
        self._by_zone: dict[str, set[WebSocket]] = {}
        self._all: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running event loop so sync DNS code can schedule broadcasts."""
        self._loop = loop

    def unbind_loop(self) -> None:
        """Clear the bound loop (on shutdown)."""
        self._loop = None

    async def subscribe(self, zone: str, websocket: WebSocket) -> None:
        """Subscribe a socket to a single zone."""
        zone = normalize_zone_name(zone)
        async with self._lock:
            self._by_zone.setdefault(zone, set()).add(websocket)
            self._refresh_subscriber_gauges()
        log_internal_event("zone_ws_subscribed", logger, zone=zone, scope="zone")

    async def unsubscribe(self, zone: str, websocket: WebSocket) -> None:
        """Remove a socket from a zone subscription."""
        zone = normalize_zone_name(zone)
        async with self._lock:
            sockets = self._by_zone.get(zone)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    del self._by_zone[zone]
            self._refresh_subscriber_gauges()

    async def subscribe_all(self, websocket: WebSocket) -> None:
        """Subscribe a socket to every zone change."""
        async with self._lock:
            self._all.add(websocket)
            self._refresh_subscriber_gauges()
        log_internal_event("zone_ws_subscribed", logger, zone="*", scope="all")

    async def unsubscribe_all(self, websocket: WebSocket) -> None:
        """Remove a socket from the all-zones feed."""
        async with self._lock:
            self._all.discard(websocket)
            self._refresh_subscriber_gauges()

    def broadcast(self, zone: str, payload: dict[str, Any]) -> None:
        """Schedule a broadcast. Safe from sync DNS code; never raises."""
        try:
            zone = normalize_zone_name(zone)
            payload = {**payload, "zone": zone}
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is not None:
                running.create_task(self._broadcast(zone, payload))
                return

            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._broadcast(zone, payload), self._loop)
                return
        except Exception as e:
            log_internal_event(
                "zone_ws_broadcast_schedule_failed",
                logger,
                level="WARNING",
                zone=zone,
                error=str(e),
            )

    async def _broadcast(self, zone: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = set(self._by_zone.get(zone, set()))
            targets |= set(self._all)

        if not targets:
            return

        zone_ws_broadcasts_total.labels(scope="event").inc()
        dead: list[WebSocket] = []
        for ws in targets:
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                zone_ws_send_errors_total.inc()
                dead.append(ws)

        if not dead:
            return

        async with self._lock:
            for ws in dead:
                self._all.discard(ws)
                for z, socks in list(self._by_zone.items()):
                    socks.discard(ws)
                    if not socks:
                        del self._by_zone[z]
            self._refresh_subscriber_gauges()

    def _refresh_subscriber_gauges(self) -> None:
        zone_count = sum(len(s) for s in self._by_zone.values())
        zone_ws_subscribers.labels(scope="zone").set(zone_count)
        zone_ws_subscribers.labels(scope="all").set(len(self._all))
