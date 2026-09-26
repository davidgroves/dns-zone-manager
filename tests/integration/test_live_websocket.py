"""Integration tests for live zone-change WebSockets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestLiveZoneWebSocket:
    """Live WS receives applied changes for a subscribed zone."""

    def test_zone_ws_receives_replace(self, test_client: TestClient, zone_name: str) -> None:
        # Seed a record via API
        name = "live-ws-test"
        add = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": name,
                "ttl": 60,
                "type": "A",
                "rdclass": "IN",
                "records": ["192.0.2.10"],
            },
        )
        assert add.status_code == 201, add.text

        with test_client.websocket_connect(f"/v1/zones/{zone_name}/ws") as ws:
            subscribed = ws.receive_json()
            assert subscribed["type"] == "subscribed"
            assert subscribed["zone"].endswith(".")

            replace = test_client.put(
                f"/v1/zones/{zone_name}/rrsets",
                json={
                    "name": name,
                    "ttl": 60,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.99"],
                },
            )
            assert replace.status_code == 200, replace.text

            message = ws.receive_json()
            assert message["type"] == "zone_change"
            assert message["event"] == "change_applied"
            assert any(
                op.get("action") == "replace" and name in op.get("name", "")
                for op in message.get("operations", [])
            )

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": name, "type": "A", "rdclass": "IN"},
        )

    def test_all_zones_ws_receives_change(self, test_client: TestClient, zone_name: str) -> None:
        name = "live-ws-all"
        add = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": name,
                "ttl": 60,
                "type": "A",
                "rdclass": "IN",
                "records": ["192.0.2.11"],
            },
        )
        assert add.status_code == 201, add.text

        with test_client.websocket_connect("/v1/ws") as ws:
            subscribed = ws.receive_json()
            assert subscribed == {"type": "subscribed", "zone": "*"}

            replace = test_client.put(
                f"/v1/zones/{zone_name}/rrsets",
                json={
                    "name": name,
                    "ttl": 60,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.12"],
                },
            )
            assert replace.status_code == 200, replace.text

            message = ws.receive_json()
            assert message["type"] == "zone_change"
            assert zone_name.rstrip(".") in message["zone"]

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": name, "type": "A", "rdclass": "IN"},
        )


@pytest.mark.integration
class TestLiveZoneWebSocketAuth:
    """API key auth via query param for browser-style WS clients."""

    def test_api_key_query_required(
        self, test_client_with_auth: TestClient, zone_name: str
    ) -> None:
        with pytest.raises(Exception):
            with test_client_with_auth.websocket_connect(f"/v1/zones/{zone_name}/ws"):
                pass

        with test_client_with_auth.websocket_connect(
            f"/v1/zones/{zone_name}/ws?api_key=integration-test-key-12345"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"

    def test_invalid_api_key_rejected(
        self, test_client_with_auth: TestClient, zone_name: str
    ) -> None:
        with pytest.raises(Exception):
            with test_client_with_auth.websocket_connect(
                f"/v1/zones/{zone_name}/ws?api_key=wrong-key"
            ):
                pass

    def test_api_key_header_accepted(
        self, test_client_with_auth: TestClient, zone_name: str
    ) -> None:
        with test_client_with_auth.websocket_connect(
            f"/v1/zones/{zone_name}/ws",
            headers={"x-api-key": "integration-test-key-12345"},
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
