"""Integration smoke for perf.loadgen (keeps the harness from rotting).

Runs a few seconds of low-rate API writes against the testcontainers BIND +
ASGI app. No large zone, no rate assertions.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest

from perf.loadgen import run_load_step


@pytest.mark.integration
@pytest.mark.asyncio
async def test_loadgen_api_smoke(test_client, zone_name) -> None:
    """Drive ~5 writes/s for 3s via the API against the integration zone."""
    app = test_client.app
    transport = httpx.ASGITransport(app=app)

    class ASGIAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            kwargs["base_url"] = "http://testserver"
            super().__init__(*args, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("perf.loadgen.httpx.AsyncClient", ASGIAsyncClient)
    try:
        result = await run_load_step(
            zone=zone_name,
            mode="api",
            target_rps=5.0,
            duration=3.0,
            concurrency=4,
            pool_size=20,
            readers=False,
            websocket_count=0,
            api_base="http://testserver",
            api_key=None,
            label="smoke@5",
        )
    finally:
        monkey.undo()

    assert result.writers["count"] >= 5
    assert result.writers["errors"] < result.writers["count"]
