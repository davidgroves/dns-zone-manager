"""Unit tests for the live zone-change WebSocket hub."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from dns_zone_manager.live.hub import ZoneChangeHub, normalize_zone_name


def test_normalize_zone_name() -> None:
    assert normalize_zone_name("Example.COM") == "example.com."
    assert normalize_zone_name("example.com.") == "example.com."


@pytest.mark.asyncio
async def test_hub_broadcasts_to_zone_and_all_subscribers() -> None:
    hub = ZoneChangeHub()
    hub.bind_loop(asyncio.get_running_loop())

    zone_ws = MagicMock()
    zone_ws.client_state = MagicMock()
    # Starlette WebSocketState.CONNECTED value is typically an enum; treat as connected
    from starlette.websockets import WebSocketState

    zone_ws.client_state = WebSocketState.CONNECTED
    zone_ws.send_json = AsyncMock()

    all_ws = MagicMock()
    all_ws.client_state = WebSocketState.CONNECTED
    all_ws.send_json = AsyncMock()

    other_ws = MagicMock()
    other_ws.client_state = WebSocketState.CONNECTED
    other_ws.send_json = AsyncMock()

    await hub.subscribe("example.com.", zone_ws)
    await hub.subscribe_all(all_ws)
    await hub.subscribe("other.com.", other_ws)

    payload: dict[str, Any] = {
        "type": "zone_change",
        "event": "change_applied",
        "operations": [{"action": "replace", "name": "www.example.com.", "type": "A"}],
    }
    hub.broadcast("example.com", payload)
    await asyncio.sleep(0.05)

    assert zone_ws.send_json.await_count == 1
    assert all_ws.send_json.await_count == 1
    assert other_ws.send_json.await_count == 0
    sent = zone_ws.send_json.await_args.args[0]
    assert sent["zone"] == "example.com."
    assert sent["type"] == "zone_change"

    await hub.unsubscribe("example.com.", zone_ws)
    await hub.unsubscribe_all(all_ws)
    await hub.unsubscribe("other.com.", other_ws)
