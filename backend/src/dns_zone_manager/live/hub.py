"""In-process pub/sub hub for live zone change WebSocket subscribers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    zone_ws_broadcasts_total,
    zone_ws_rejected_total,
    zone_ws_send_errors_total,
    zone_ws_slow_client_drops_total,
    zone_ws_subscribers,
)

logger = logging.getLogger(__name__)


class ConnectionLimitError(Exception):
    """Raised when a WebSocket subscription would exceed configured caps."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def normalize_zone_name(zone: str) -> str:
    """Normalize a zone name to lowercase with a trailing dot."""
    zone = zone.strip().lower()
    if zone and not zone.endswith("."):
        zone = zone + "."
    return zone


class ZoneChangeHub:
    """Fan-out applied zone changes to per-zone and all-zones WebSocket clients."""

    def __init__(
        self,
        max_connections: int = 500,
        max_connections_per_ip: int = 50,
        send_timeout_seconds: float = 5.0,
    ) -> None:
        self._by_zone: dict[str, set[WebSocket]] = {}
        self._all: set[WebSocket] = set()
        self._client_ips: dict[WebSocket, str | None] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.max_connections = max_connections
        self.max_connections_per_ip = max_connections_per_ip
        self.send_timeout_seconds = send_timeout_seconds

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running event loop so sync DNS code can schedule broadcasts."""
        self._loop = loop

    def unbind_loop(self) -> None:
        """Clear the bound loop (on shutdown)."""
        self._loop = None

    def _total_subscribers(self) -> int:
        return sum(len(s) for s in self._by_zone.values()) + len(self._all)

    def _ip_count(self, client_ip: str | None) -> int:
        if not client_ip:
            return 0
        return sum(1 for ip in self._client_ips.values() if ip == client_ip)

    def _client_ip(self, websocket: WebSocket) -> str | None:
        if websocket.client is None:
            return None
        return websocket.client.host

    def _check_limits(self, websocket: WebSocket) -> str | None:
        """Return a rejection reason, or None if the socket may subscribe."""
        client_ip = self._client_ip(websocket)
        if self._total_subscribers() >= self.max_connections:
            return "max_connections"
        if client_ip and self._ip_count(client_ip) >= self.max_connections_per_ip:
            return "max_per_ip"
        return None

    async def subscribe(self, zone: str, websocket: WebSocket) -> None:
        """Subscribe a socket to a single zone."""
        zone = normalize_zone_name(zone)
        async with self._lock:
            reason = self._check_limits(websocket)
            if reason:
                zone_ws_rejected_total.labels(reason=reason).inc()
                raise ConnectionLimitError(reason)
            self._by_zone.setdefault(zone, set()).add(websocket)
            self._client_ips[websocket] = self._client_ip(websocket)
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
            self._client_ips.pop(websocket, None)
            self._refresh_subscriber_gauges()

    async def subscribe_all(self, websocket: WebSocket) -> None:
        """Subscribe a socket to every zone change."""
        async with self._lock:
            reason = self._check_limits(websocket)
            if reason:
                zone_ws_rejected_total.labels(reason=reason).inc()
                raise ConnectionLimitError(reason)
            self._all.add(websocket)
            self._client_ips[websocket] = self._client_ip(websocket)
            self._refresh_subscriber_gauges()
        log_internal_event("zone_ws_subscribed", logger, zone="*", scope="all")

    async def unsubscribe_all(self, websocket: WebSocket) -> None:
        """Remove a socket from the all-zones feed."""
        async with self._lock:
            self._all.discard(websocket)
            self._client_ips.pop(websocket, None)
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
                await asyncio.wait_for(
                    ws.send_json(payload),
                    timeout=self.send_timeout_seconds,
                )
            except TimeoutError:
                zone_ws_slow_client_drops_total.inc()
                zone_ws_send_errors_total.inc()
                dead.append(ws)
            except Exception:
                zone_ws_send_errors_total.inc()
                dead.append(ws)

        if not dead:
            return

        async with self._lock:
            for ws in dead:
                self._all.discard(ws)
                self._client_ips.pop(ws, None)
                for z, socks in list(self._by_zone.items()):
                    socks.discard(ws)
                    if not socks:
                        del self._by_zone[z]
            self._refresh_subscriber_gauges()

    def _refresh_subscriber_gauges(self) -> None:
        zone_count = sum(len(s) for s in self._by_zone.values())
        zone_ws_subscribers.labels(scope="zone").set(zone_count)
        zone_ws_subscribers.labels(scope="all").set(len(self._all))
