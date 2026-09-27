"""Live zone-change WebSocket endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Path, WebSocket, WebSocketDisconnect

from dns_zone_manager.live.auth import authenticate_websocket
from dns_zone_manager.live.hub import ConnectionLimitError, ZoneChangeHub, normalize_zone_name
from dns_zone_manager.logging import log_internal_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Live"])

_hub: ZoneChangeHub | None = None


def set_zone_change_hub(hub: ZoneChangeHub | None) -> None:
    """Inject the zone change hub."""
    global _hub
    _hub = hub


def get_zone_change_hub() -> ZoneChangeHub:
    """Return the hub or raise if not initialized."""
    if _hub is None:
        raise RuntimeError("Zone change hub not initialized")
    return _hub


async def _reject_limit(websocket: WebSocket, reason: str) -> None:
    await websocket.close(code=1013, reason=f"Connection limit: {reason}")
    log_internal_event(
        "zone_ws_rejected",
        logger,
        level="WARNING",
        reason=reason,
    )


@router.websocket("/ws")
async def subscribe_all_zones(websocket: WebSocket) -> None:
    """Subscribe to applied changes for every zone."""
    await authenticate_websocket(websocket)
    await websocket.accept()
    hub = get_zone_change_hub()
    try:
        await hub.subscribe_all(websocket)
    except ConnectionLimitError as e:
        await _reject_limit(websocket, e.reason)
        return
    await websocket.send_json({"type": "subscribed", "zone": "*"})
    try:
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe_all(websocket)
        log_internal_event("zone_ws_disconnected", logger, zone="*", scope="all")


@router.websocket("/zones/{zone}/ws")
async def subscribe_zone(
    websocket: WebSocket,
    zone: str = Path(description="Zone name"),
) -> None:
    """Subscribe to applied changes for a single zone."""
    await authenticate_websocket(websocket)
    zone = normalize_zone_name(zone)
    await websocket.accept()
    hub = get_zone_change_hub()
    try:
        await hub.subscribe(zone, websocket)
    except ConnectionLimitError as e:
        await _reject_limit(websocket, e.reason)
        return
    await websocket.send_json({"type": "subscribed", "zone": zone})
    try:
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(zone, websocket)
        log_internal_event("zone_ws_disconnected", logger, zone=zone, scope="zone")
