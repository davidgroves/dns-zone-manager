"""Integration tests for outbound DNS change webhooks.

These run the real delivery path: the app POSTs to a local HTTP sink over a
real socket, so payload shape, auth headers, and auto-recording are all
exercised end to end against BIND.
"""

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.bind_container import BindContainer
from tests.integration.conftest import (
    _cleanup_config_file,
    _make_config_yaml,
    _set_config_file,
)

pytestmark = pytest.mark.integration

SIGNING_SECRET = "integration-signing-secret"
API_KEY = "integration-test-key-12345"
AUTH_HEADERS = {"X-API-Key": API_KEY}
BASE_URL = "https://dns.example.com"


class _WebhookSink:
    """A local HTTP server that records the webhook requests it receives."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with sink._lock:
                    sink.received.append(
                        {
                            "path": self.path,
                            "headers": dict(self.headers),
                            "body": body,
                            "json": json.loads(body) if body else None,
                        }
                    )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args: Any) -> None:
                """Silence the default stderr access log."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/hook"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.received)

    def wait_for(self, count: int = 1, timeout: float = 15.0) -> list[dict[str, Any]]:
        """Block until at least ``count`` requests arrive, then return them."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.snapshot()
            if len(current) >= count:
                return current
            time.sleep(0.05)
        raise AssertionError(f"Expected {count} webhook request(s), got {len(self.snapshot())}")

    def clear(self) -> None:
        with self._lock:
            self.received.clear()


@pytest.fixture
def webhook_sink() -> Generator[_WebhookSink]:
    sink = _WebhookSink()
    sink.start()
    try:
        yield sink
    finally:
        sink.stop()


@pytest.fixture
def webhook_client(
    bind_server: BindContainer,
    webhook_sink: _WebhookSink,
    tmp_path: Path,
) -> Generator[TestClient]:
    """Test client with API key auth and a generic HMAC-signed webhook target."""
    extra_yaml = f"""
webhooks:
  enabled: true
  base_url: {BASE_URL}
  timeout: 5.0
  max_retries: 2
  retry_backoff: 0.0
  targets:
    - name: sink
      type: generic
      url: {webhook_sink.url}
      allow_insecure: true
      auth:
        type: hmac
        secret: {SIGNING_SECRET}
"""
    config_yaml = _make_config_yaml(
        bind_server,
        auth_enabled=True,
        scheduler_db=str(tmp_path / "scheduler.db"),
        extra_yaml=extra_yaml,
    )
    config_path = _set_config_file(config_yaml)

    from dns_zone_manager.main import create_app

    app = create_app()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        _cleanup_config_file(config_path)


def _verify_signature(request: dict[str, Any]) -> bool:
    """Recompute the HMAC the receiver would check."""
    signature = request["headers"]["X-DNS-Signature"]
    timestamp = request["headers"]["X-DNS-Timestamp"]
    expected = hmac.new(
        SIGNING_SECRET.encode(),
        timestamp.encode() + b"." + request["body"],
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


@pytest.mark.integration
class TestManualChangeWebhook:
    """A direct RRset write notifies, and is recorded so its link resolves."""

    def test_add_rrset_sends_signed_notification(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        response = webhook_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-manual",
                "type": "A",
                "ttl": 300,
                "records": ["10.10.0.1"],
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 201

        request = webhook_sink.wait_for(1)[0]

        assert request["path"] == "/hook"
        assert _verify_signature(request), "receiver could not verify the HMAC"

        payload = request["json"]
        assert payload["event"] == "change_applied"
        assert payload["zone"] == zone_name
        # Who made the change: the API key name is the actor
        assert payload["actor"]["id"] == "test"
        assert payload["actor"]["auth_type"] == "api_key"
        # Manual vs scheduled
        assert payload["trigger"] == "manual"
        # A link to the change in the scheduler
        change_id = payload["change"]["id"]
        assert change_id
        assert payload["change"]["link"] == (f"{BASE_URL}/?view=scheduled&change={change_id}")
        assert payload["result"]["success"] is True
        assert payload["result"]["rcode"] == "NOERROR"

        operations = payload["operations"]
        assert len(operations) == 1
        assert operations[0]["action"] == "add"
        assert operations[0]["type"] == "A"
        assert operations[0]["records"] == ["10.10.0.1"]

    def test_linked_change_is_retrievable_from_the_scheduler(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        webhook_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-linkable",
                "type": "A",
                "ttl": 300,
                "records": ["10.10.0.2"],
            },
            headers=AUTH_HEADERS,
        )
        payload = webhook_sink.wait_for(1)[0]["json"]
        change_id = payload["change"]["id"]

        # The link in the notification must resolve to a real change
        detail = webhook_client.get(f"/v1/scheduled-changes/{change_id}", headers=AUTH_HEADERS)
        assert detail.status_code == 200
        change = detail.json()
        assert change["status"] == "applied"
        assert change["source"] == "manual"
        assert change["created_by"] == "test"
        assert change["operations"][0]["records"] == ["10.10.0.2"]

        events = {e["event"] for e in change["events"]}
        assert {"created", "applied"} <= events

    def test_manual_changes_are_filterable_by_source(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        webhook_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-filter",
                "type": "A",
                "ttl": 300,
                "records": ["10.10.0.3"],
            },
            headers=AUTH_HEADERS,
        )
        webhook_sink.wait_for(1)

        manual = webhook_client.get("/v1/scheduled-changes?source=manual", headers=AUTH_HEADERS)
        assert manual.status_code == 200
        manual_changes = manual.json()["changes"]
        assert manual_changes
        assert all(c["source"] == "manual" for c in manual_changes)

        scheduler_only = webhook_client.get(
            "/v1/scheduled-changes?source=scheduler", headers=AUTH_HEADERS
        )
        assert all(c["source"] == "scheduler" for c in scheduler_only.json()["changes"])

    def test_delete_and_replace_are_reported_with_their_action(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        webhook_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-actions",
                "type": "A",
                "ttl": 300,
                "records": ["10.10.0.4"],
            },
            headers=AUTH_HEADERS,
        )
        webhook_sink.wait_for(1)
        webhook_sink.clear()

        replaced = webhook_client.put(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-actions",
                "type": "A",
                "ttl": 600,
                "records": ["10.10.0.5"],
            },
            headers=AUTH_HEADERS,
        )
        assert replaced.status_code == 200
        payload = webhook_sink.wait_for(1)[0]["json"]
        assert payload["operations"][0]["action"] == "replace"
        assert payload["operations"][0]["records"] == ["10.10.0.5"]

        webhook_sink.clear()
        deleted = webhook_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": "webhook-actions", "type": "A"},
            headers=AUTH_HEADERS,
        )
        assert deleted.status_code == 200
        payload = webhook_sink.wait_for(1)[0]["json"]
        assert payload["operations"][0]["action"] == "delete"


@pytest.mark.integration
class TestFailedChangeWebhook:
    """Failures notify too, so a rejected change is not silent."""

    def test_server_side_prerequisite_failure_sends_change_failed(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        # nsupdate sends prerequisites straight to the server rather than
        # pre-checking the cache, so this fails at BIND with NXRRSET.
        nsupdate_text = (
            f"zone {zone_name}\n"
            f"prereq yxrrset does-not-exist.{zone_name} A\n"
            f"update add webhook-failure.{zone_name} 300 A 10.10.0.6\n"
            "send\n"
        )

        response = webhook_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={**AUTH_HEADERS, "Content-Type": "text/plain"},
        )
        assert response.status_code == 200
        assert response.json()["total_failed"] == 1

        payload = webhook_sink.wait_for(1)[0]["json"]
        assert payload["event"] == "change_failed"
        assert payload["result"]["success"] is False
        assert payload["result"]["rcode"] == "NXRRSET"
        assert payload["result"]["error"]
        assert payload["trigger"] == "manual"
        assert payload["actor"]["id"] == "test"

    def test_failed_change_is_recorded_as_failed(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        nsupdate_text = (
            f"zone {zone_name}\n"
            f"prereq yxrrset also-missing.{zone_name} A\n"
            f"update add webhook-failure2.{zone_name} 300 A 10.10.0.9\n"
            "send\n"
        )
        webhook_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={**AUTH_HEADERS, "Content-Type": "text/plain"},
        )

        change_id = webhook_sink.wait_for(1)[0]["json"]["change"]["id"]

        change = webhook_client.get(
            f"/v1/scheduled-changes/{change_id}", headers=AUTH_HEADERS
        ).json()
        assert change["status"] == "failed"
        assert change["source"] == "manual"
        assert change["last_error"]


@pytest.mark.integration
class TestScheduledChangeWebhook:
    """Scheduler-driven writes link to their real change, not a new record."""

    def test_apply_now_reports_scheduled_trigger_and_change_id(
        self,
        webhook_client: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        created = webhook_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Webhook scheduled change",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "webhook-scheduled",
                        "type": "A",
                        "ttl": 300,
                        "records": ["10.10.0.7"],
                    }
                ],
            },
            headers=AUTH_HEADERS,
        )
        assert created.status_code in (200, 201)
        change_id = created.json()["id"]

        applied = webhook_client.post(
            f"/v1/scheduled-changes/{change_id}/apply", headers=AUTH_HEADERS
        )
        assert applied.status_code == 200

        payload = webhook_sink.wait_for(1)[0]["json"]

        assert payload["trigger"] == "apply_now"
        assert payload["change"]["id"] == change_id
        assert payload["change"]["name"] == "Webhook scheduled change"
        assert payload["change"]["link"] == (f"{BASE_URL}/?view=scheduled&change={change_id}")

        # It keeps its scheduler origin rather than being recorded as manual
        detail = webhook_client.get(
            f"/v1/scheduled-changes/{change_id}", headers=AUTH_HEADERS
        ).json()
        assert detail["source"] == "scheduler"

        manual_ids = {
            c["id"]
            for c in webhook_client.get(
                "/v1/scheduled-changes?source=manual", headers=AUTH_HEADERS
            ).json()["changes"]
        }
        assert change_id not in manual_ids


@pytest.mark.integration
class TestWebhooksDisabled:
    """Nothing is sent, and no manual records accumulate, when disabled."""

    def test_no_delivery_and_no_autorecord(
        self,
        test_client_with_auth: TestClient,
        webhook_sink: _WebhookSink,
        zone_name: str,
    ):
        response = test_client_with_auth.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "webhook-disabled",
                "type": "A",
                "ttl": 300,
                "records": ["10.10.0.8"],
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 201

        time.sleep(0.5)
        assert webhook_sink.snapshot() == []

        manual = test_client_with_auth.get(
            "/v1/scheduled-changes?source=manual", headers=AUTH_HEADERS
        )
        assert manual.json()["changes"] == []
