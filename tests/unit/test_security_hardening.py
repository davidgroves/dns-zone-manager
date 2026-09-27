"""Unit tests for security hardening (CORS, Origin, redaction, names, search)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import dns.name
import pytest
from dns_zone_manager.config import CacheSettings, LiveSettings, ServerSettings
from dns_zone_manager.dns.names import (
    InvalidZoneNameError,
    normalize_zone_name,
    require_name_in_zone,
    sanitize_zone_filename,
)
from dns_zone_manager.live.hub import ConnectionLimitError, ZoneChangeHub
from dns_zone_manager.middleware import origin_is_allowed, redact_query_params
from dns_zone_manager.routers.search import compile_pattern
from fastapi import HTTPException
from starlette.websockets import WebSocketState


def test_redact_query_params_masks_secrets() -> None:
    assert redact_query_params({"api_key": "secret", "q": "www"}) == {
        "api_key": "[REDACTED]",
        "q": "www",
    }
    assert redact_query_params(None) is None
    assert redact_query_params({"token": "x", "Ticket": "y"}) == {
        "token": "[REDACTED]",
        "Ticket": "[REDACTED]",
    }


def test_origin_is_allowed_same_host_and_allowlist() -> None:
    assert origin_is_allowed("https://dns.example.com", "dns.example.com", [])
    assert origin_is_allowed(
        "https://other.example.com",
        "dns.example.com",
        ["https://other.example.com"],
    )
    assert not origin_is_allowed(
        "https://evil.example",
        "dns.example.com",
        ["https://other.example.com"],
    )
    assert origin_is_allowed("", "dns.example.com", [])


def test_server_cors_origins_default_empty() -> None:
    assert ServerSettings().cors_origins == []
    assert LiveSettings().max_connections == 500
    assert LiveSettings().send_timeout_seconds == 5.0


def test_cache_effective_max_zone_size() -> None:
    # Explicit 0 means "fall back to max_size_bytes"
    assert (
        CacheSettings(max_size_bytes=1000, max_zone_size_bytes=0).effective_max_zone_size_bytes()
        == 1000
    )
    assert (
        CacheSettings(max_size_bytes=1000, max_zone_size_bytes=200).effective_max_zone_size_bytes()
        == 200
    )
    # Defaults allow large zones (25 GiB)
    defaults = CacheSettings()
    assert defaults.max_zone_size_bytes == 26_843_545_600
    assert defaults.max_size_bytes == 26_843_545_600


def test_normalize_zone_name_rejects_control_chars() -> None:
    assert normalize_zone_name("Example.COM") == "example.com."
    with pytest.raises(InvalidZoneNameError):
        normalize_zone_name("bad\nname.com")
    with pytest.raises(InvalidZoneNameError):
        normalize_zone_name("")


def test_sanitize_zone_filename() -> None:
    assert sanitize_zone_filename('evil".com.') == "evil_.com.zone"
    assert sanitize_zone_filename("example.com.") == "example.com.zone"


def test_require_name_in_zone() -> None:
    require_name_in_zone(dns.name.from_text("www.example.com."), "example.com.")
    with pytest.raises(InvalidZoneNameError):
        require_name_in_zone(dns.name.from_text("www.other.com."), "example.com.")


def test_compile_pattern_rejects_nested_quantifiers_and_long_patterns() -> None:
    assert compile_pattern("^www\\.", "name_pattern") is not None
    with pytest.raises(HTTPException) as nested:
        compile_pattern("(a+)+", "name_pattern")
    assert nested.value.status_code == 400
    with pytest.raises(HTTPException) as long:
        compile_pattern("a" * 300, "name_pattern")
    assert long.value.status_code == 400


@pytest.mark.asyncio
async def test_hub_rejects_over_max_connections() -> None:
    hub = ZoneChangeHub(max_connections=1, max_connections_per_ip=10)

    def _ws(ip: str = "1.2.3.4") -> MagicMock:
        ws = MagicMock()
        ws.client = MagicMock()
        ws.client.host = ip
        ws.client_state = WebSocketState.CONNECTED
        ws.send_json = AsyncMock()
        return ws

    first = _ws()
    await hub.subscribe("example.com.", first)
    with pytest.raises(ConnectionLimitError) as exc:
        await hub.subscribe_all(_ws("9.9.9.9"))
    assert exc.value.reason == "max_connections"
    await hub.unsubscribe("example.com.", first)


@pytest.mark.asyncio
async def test_hub_rejects_over_max_per_ip() -> None:
    hub = ZoneChangeHub(max_connections=50, max_connections_per_ip=1)

    def _ws() -> MagicMock:
        ws = MagicMock()
        ws.client = MagicMock()
        ws.client.host = "10.0.0.1"
        ws.client_state = WebSocketState.CONNECTED
        ws.send_json = AsyncMock()
        return ws

    first = _ws()
    await hub.subscribe("example.com.", first)
    with pytest.raises(ConnectionLimitError) as exc:
        await hub.subscribe("example.com.", _ws())
    assert exc.value.reason == "max_per_ip"
    await hub.unsubscribe("example.com.", first)


@pytest.mark.asyncio
async def test_notify_coalesces_bursts() -> None:
    import dns.message
    import dns.opcode
    import dns.rdatatype
    from dns_zone_manager.config import NotifySettings
    from dns_zone_manager.dns.notify import NotifyListener

    calls: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_notify(zone: str) -> None:
        calls.append(zone)
        started.set()
        await release.wait()

    listener = NotifyListener(
        settings=NotifySettings(enabled=True, require_tsig=False, refresh_cooldown_seconds=0),
        on_notify=on_notify,
    )

    def wire(zone: str = "test.example.") -> bytes:
        msg = dns.message.make_query(zone, dns.rdatatype.SOA)
        msg.set_opcode(dns.opcode.NOTIFY)
        return msg.to_wire()

    await listener.handle_notify(wire(), ("127.0.0.1", 1), "udp")
    await started.wait()
    # Burst while in-flight — should coalesce to one follow-up
    await listener.handle_notify(wire(), ("127.0.0.1", 1), "udp")
    await listener.handle_notify(wire(), ("127.0.0.1", 1), "udp")
    release.set()
    # Drain follow-up
    for _ in range(20):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 2
