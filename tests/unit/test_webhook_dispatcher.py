"""Unit tests for webhook delivery, auth headers, retries, and auto-recording."""

import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import pytest
from dns_zone_manager.config import WebhookAuth, WebhookSettings, WebhookTarget
from dns_zone_manager.notifications.auth import build_auth_headers, redact
from dns_zone_manager.notifications.dispatcher import WebhookDispatcher
from dns_zone_manager.notifications.events import (
    EVENT_CHANGE_APPLIED,
    EVENT_CHANGE_FAILED,
    ChangeOperation,
    DnsChangeEvent,
)

ZONE = "test.example."
HOOK_URL = "https://hooks.example.com/T000/B000/tokenvalue"


def _event(**overrides) -> DnsChangeEvent:
    defaults = {
        "event": EVENT_CHANGE_APPLIED,
        "zone": ZONE,
        "operations": [
            ChangeOperation(
                action="add",
                name="www.test.example.",
                rdtype="A",
                ttl=300,
                records=["10.0.0.1"],
            )
        ],
        "timestamp": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "actor": "alice@example.com",
        "change_id": "abc-123",
        "rcode": "NOERROR",
    }
    return DnsChangeEvent(**{**defaults, **overrides})


def _settings(**overrides) -> WebhookSettings:
    defaults = {
        "enabled": True,
        "base_url": "https://dns.example.com",
        "retry_backoff": 0.0,
        "targets": [WebhookTarget(name="slack", type="slack", url=HOOK_URL)],
    }
    return WebhookSettings(**{**defaults, **overrides})


class _Recorder:
    """Captures requests and replays a scripted sequence of responses."""

    def __init__(self, statuses: list[int] | None = None, raise_times: int = 0):
        self.requests: list[httpx.Request] = []
        self.statuses = statuses or []
        self.raise_times = raise_times
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.calls += 1
        if self.calls <= self.raise_times:
            raise httpx.ConnectError(f"connection refused to {request.url}")
        index = min(self.calls - 1, len(self.statuses) - 1) if self.statuses else 0
        status = self.statuses[index] if self.statuses else 200
        return httpx.Response(status, text="ok" if status < 400 else "rejected")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def _deliver(dispatcher: WebhookDispatcher, event: DnsChangeEvent) -> None:
    """Run one event through the worker without starting a background task."""
    await dispatcher._handle(event)


class TestAuthHeaders:
    """Per-target outbound authentication."""

    def _target(self, auth: WebhookAuth) -> WebhookTarget:
        return WebhookTarget(name="t", type="generic", url=HOOK_URL, auth=auth)

    def test_none_adds_no_headers(self):
        headers = build_auth_headers(self._target(WebhookAuth()), b"{}", "ts")
        assert headers == {}

    def test_bearer(self):
        target = self._target(WebhookAuth(type="bearer", secret="tok"))
        assert build_auth_headers(target, b"{}", "ts") == {"Authorization": "Bearer tok"}

    def test_basic(self):
        target = self._target(WebhookAuth(type="basic", username="bob", password="hunter2"))
        headers = build_auth_headers(target, b"{}", "ts")
        # base64("bob:hunter2")
        assert headers == {"Authorization": "Basic Ym9iOmh1bnRlcjI="}

    def test_static_header(self):
        target = self._target(WebhookAuth(type="header", secret="k3y"))
        assert build_auth_headers(target, b"{}", "ts") == {"X-API-Key": "k3y"}

    def test_custom_header_name(self):
        target = self._target(WebhookAuth(type="header", secret="k3y", header="X-Token"))
        assert build_auth_headers(target, b"{}", "ts") == {"X-Token": "k3y"}

    def test_hmac_signs_timestamp_and_body(self):
        body = b'{"hello":"world"}'
        target = self._target(WebhookAuth(type="hmac", secret="shh"))

        headers = build_auth_headers(target, body, "2026-01-02T03:04:05+00:00")

        expected = hmac.new(
            b"shh",
            b"2026-01-02T03:04:05+00:00." + body,
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-DNS-Signature"] == f"sha256={expected}"
        assert headers["X-DNS-Timestamp"] == "2026-01-02T03:04:05+00:00"

    def test_hmac_signature_changes_when_body_is_tampered(self):
        target = self._target(WebhookAuth(type="hmac", secret="shh"))
        a = build_auth_headers(target, b'{"a":1}', "ts")["X-DNS-Signature"]
        b = build_auth_headers(target, b'{"a":2}', "ts")["X-DNS-Signature"]
        assert a != b

    def test_hmac_sha512(self):
        target = self._target(WebhookAuth(type="hmac", secret="shh", algorithm="sha512"))
        assert build_auth_headers(target, b"{}", "ts")["X-DNS-Signature"].startswith("sha512=")


class TestRedaction:
    """Secrets must never reach the logs."""

    def test_redacts_url_and_secret(self):
        target = WebhookTarget(
            name="t",
            type="generic",
            url=HOOK_URL,
            auth=WebhookAuth(type="bearer", secret="topsecret"),
        )
        message = f"failed POST to {HOOK_URL} with token topsecret"

        cleaned = redact(message, target)

        assert HOOK_URL not in cleaned
        assert "topsecret" not in cleaned
        assert cleaned.count("***") == 2


class TestDelivery:
    """Happy-path delivery and payload contents."""

    @pytest.mark.asyncio
    async def test_posts_payload_with_actor_trigger_and_link(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(targets=[WebhookTarget(name="gen", type="generic", url=HOOK_URL)]),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event())

        assert len(recorder.requests) == 1
        request = recorder.requests[0]
        assert str(request.url) == HOOK_URL
        assert request.headers["content-type"] == "application/json"

        payload = json.loads(request.content)
        assert payload["actor"]["id"] == "alice@example.com"
        assert payload["trigger"] == "manual"
        assert payload["change"]["link"] == (
            "https://dns.example.com/?view=scheduled&change=abc-123"
        )

    @pytest.mark.asyncio
    async def test_delivers_to_every_matching_target(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(
                targets=[
                    WebhookTarget(name="slack", type="slack", url=HOOK_URL),
                    WebhookTarget(name="teams", type="teams", url=HOOK_URL),
                ]
            ),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event())

        assert len(recorder.requests) == 2

    @pytest.mark.asyncio
    async def test_zone_filter_skips_non_matching_target(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(
                targets=[
                    WebhookTarget(
                        name="other",
                        type="generic",
                        url=HOOK_URL,
                        zones=["other.example."],
                    )
                ]
            ),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event())

        assert recorder.requests == []

    @pytest.mark.asyncio
    async def test_event_filter_skips_non_matching_target(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(events=["change_applied"]),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event(event=EVENT_CHANGE_FAILED))

        assert recorder.requests == []

    @pytest.mark.asyncio
    async def test_failure_events_are_delivered_when_enabled(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await _deliver(
            dispatcher,
            _event(event=EVENT_CHANGE_FAILED, rcode="NXRRSET", error="prereq failed"),
        )

        assert len(recorder.requests) == 1


class TestRetries:
    """Transient failures retry; client errors do not."""

    @pytest.mark.asyncio
    async def test_retries_server_error_then_succeeds(self):
        recorder = _Recorder([500, 200])
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await _deliver(dispatcher, _event())

        assert recorder.calls == 2

    @pytest.mark.asyncio
    async def test_retries_transport_error(self):
        recorder = _Recorder([200], raise_times=1)
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await _deliver(dispatcher, _event())

        assert recorder.calls == 2

    @pytest.mark.asyncio
    async def test_client_error_is_not_retried(self):
        recorder = _Recorder([403])
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await _deliver(dispatcher, _event())

        assert recorder.calls == 1

    @pytest.mark.asyncio
    async def test_429_is_retried(self):
        recorder = _Recorder([429, 200])
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await _deliver(dispatcher, _event())

        assert recorder.calls == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        recorder = _Recorder([500])
        dispatcher = WebhookDispatcher(
            _settings(max_retries=3),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event())

        assert recorder.calls == 3

    @pytest.mark.asyncio
    async def test_retries_reuse_the_signed_body_and_timestamp(self):
        recorder = _Recorder([500, 200])
        dispatcher = WebhookDispatcher(
            _settings(
                targets=[
                    WebhookTarget(
                        name="gen",
                        type="generic",
                        url=HOOK_URL,
                        auth=WebhookAuth(type="hmac", secret="shh"),
                    )
                ]
            ),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event())

        first, second = recorder.requests
        # A regenerated timestamp would invalidate the signature on retry
        assert first.content == second.content
        assert first.headers["X-DNS-Signature"] == second.headers["X-DNS-Signature"]
        assert first.headers["X-DNS-Timestamp"] == second.headers["X-DNS-Timestamp"]


class TestQueue:
    """emit() must never block or raise on the DNS write path."""

    @pytest.mark.asyncio
    async def test_emit_enqueues(self):
        dispatcher = WebhookDispatcher(_settings(), transport=_Recorder([200]).transport)

        dispatcher.emit(_event())

        assert dispatcher._queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_full_queue_drops_without_raising(self):
        dispatcher = WebhookDispatcher(
            _settings(queue_size=1),
            transport=_Recorder([200]).transport,
        )

        dispatcher.emit(_event())
        dispatcher.emit(_event())

        assert dispatcher._queue.qsize() == 1

    def test_emit_without_running_loop_is_a_noop(self):
        dispatcher = WebhookDispatcher(_settings(), transport=_Recorder([200]).transport)

        # No exception: a CLI or test-time DNS write simply has nowhere to send
        dispatcher.emit(_event())

    @pytest.mark.asyncio
    async def test_start_and_stop_drains_pending_events(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(_settings(), transport=recorder.transport)

        await dispatcher.start()
        dispatcher.emit(_event())
        await dispatcher.stop()

        assert len(recorder.requests) == 1

    @pytest.mark.asyncio
    async def test_formatting_failure_does_not_kill_the_worker(self):
        recorder = _Recorder([200])
        settings = _settings()
        # Bypass validation to simulate an unknown target type reaching delivery
        object.__setattr__(settings.targets[0], "type", "bogus")
        dispatcher = WebhookDispatcher(settings, transport=recorder.transport)

        await _deliver(dispatcher, _event())

        assert recorder.requests == []


class _FakeStore:
    """Minimal stand-in for ScheduledChangeStore."""

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    async def record_external_change(self, **kwargs):
        if self.fail:
            raise RuntimeError("database is locked")
        self.calls.append(kwargs)
        return None


class TestAutorecord:
    """Manual changes are persisted so their notification link resolves."""

    @pytest.mark.asyncio
    async def test_records_manual_change_before_delivery(self):
        recorder = _Recorder([200])
        store = _FakeStore()
        dispatcher = WebhookDispatcher(
            _settings(targets=[WebhookTarget(name="gen", type="generic", url=HOOK_URL)]),
            store=store,
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event(autorecord=True))

        assert len(store.calls) == 1
        call = store.calls[0]
        assert call["change_id"] == "abc-123"
        assert call["zone"] == ZONE
        assert call["status"] == "applied"
        assert call["actor"] == "alice@example.com"
        assert call["trigger"] == "manual"
        assert call["operations"][0]["name"] == "www.test.example."

        payload = json.loads(recorder.requests[0].content)
        assert payload["change"]["link"].endswith("change=abc-123")

    @pytest.mark.asyncio
    async def test_failed_change_is_recorded_as_failed(self):
        store = _FakeStore()
        dispatcher = WebhookDispatcher(
            _settings(), store=store, transport=_Recorder([200]).transport
        )

        await _deliver(
            dispatcher,
            _event(autorecord=True, event=EVENT_CHANGE_FAILED, error="boom"),
        )

        assert store.calls[0]["status"] == "failed"
        assert store.calls[0]["error"] == "boom"

    @pytest.mark.asyncio
    async def test_scheduler_change_is_not_recorded_again(self):
        store = _FakeStore()
        dispatcher = WebhookDispatcher(
            _settings(), store=store, transport=_Recorder([200]).transport
        )

        await _deliver(dispatcher, _event(autorecord=False, trigger="scheduler"))

        assert store.calls == []

    @pytest.mark.asyncio
    async def test_link_falls_back_to_zone_when_recording_fails(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(targets=[WebhookTarget(name="gen", type="generic", url=HOOK_URL)]),
            store=_FakeStore(fail=True),
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event(autorecord=True))

        # Better to link to the zone than to a change record that never landed
        payload = json.loads(recorder.requests[0].content)
        assert payload["change"]["id"] is None
        assert payload["change"]["link"] == f"https://dns.example.com/?zone={ZONE}"

    @pytest.mark.asyncio
    async def test_no_store_means_no_change_link(self):
        recorder = _Recorder([200])
        dispatcher = WebhookDispatcher(
            _settings(targets=[WebhookTarget(name="gen", type="generic", url=HOOK_URL)]),
            store=None,
            transport=recorder.transport,
        )

        await _deliver(dispatcher, _event(autorecord=True))

        payload = json.loads(recorder.requests[0].content)
        assert payload["change"]["id"] is None

    @pytest.mark.asyncio
    async def test_autorecord_can_be_disabled(self):
        store = _FakeStore()
        dispatcher = WebhookDispatcher(
            _settings(autorecord_manual_changes=False),
            store=store,
            transport=_Recorder([200]).transport,
        )

        await _deliver(dispatcher, _event(autorecord=True))

        assert store.calls == []
