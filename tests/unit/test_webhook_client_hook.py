"""Unit tests for change event emission from the DNS client write path."""

from unittest.mock import MagicMock, patch

import dns.rcode
import dns.rdata
import dns.rdatatype
import dns.update
import pytest
from dns.exception import DNSException
from dns_zone_manager.config import (
    DNSSettings,
    Settings,
    TSIGKeyEntry,
)
from dns_zone_manager.dns.client import DNSClient, PrerequisiteFailedError, UpdateError
from dns_zone_manager.notifications.context import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULER,
    ChangeContext,
    change_context,
    set_change_context,
)
from dns_zone_manager.notifications.events import (
    EVENT_CHANGE_APPLIED,
    EVENT_CHANGE_FAILED,
    DnsChangeEvent,
)

ZONE = "test.example."


class _CapturingDispatcher:
    def __init__(self):
        self.events: list[DnsChangeEvent] = []

    def emit(self, event: DnsChangeEvent) -> None:
        self.events.append(event)


def _settings() -> Settings:
    return Settings(
        tsig_keys=[TSIGKeyEntry(name="test-key", secret="c2VjcmV0")],
        dns=DNSSettings(server="127.0.0.1", update_tsig_key="test-key"),
    )


def _client(dispatcher) -> DNSClient:
    return DNSClient(_settings(), dispatcher)


def _update() -> dns.update.Update:
    update = dns.update.Update(ZONE)
    update.add("www.test.example.", 300, dns.rdata.from_text("IN", "A", "10.0.0.1"))
    return update


def _response(rcode: int) -> MagicMock:
    response = MagicMock()
    response.rcode.return_value = rcode
    return response


@pytest.fixture(autouse=True)
def _clear_context():
    set_change_context(None)
    yield
    set_change_context(None)


class TestSuccessEmission:
    """A committed update emits change_applied."""

    def test_emits_applied_event_with_operations(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client._send_update(_update(), ZONE)

        assert len(dispatcher.events) == 1
        event = dispatcher.events[0]
        assert event.event == EVENT_CHANGE_APPLIED
        assert event.zone == ZONE
        assert event.rcode == "NOERROR"
        assert event.server == "127.0.0.1"
        assert [op.action for op in event.operations] == ["add"]
        assert event.operations[0].records == ["10.0.0.1"]

    def test_uses_actor_from_change_context(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)
        set_change_context(
            ChangeContext(
                actor="alice@example.com",
                actor_name="Alice Example",
                auth_type="azure_ad",
                request_id="req_123",
            )
        )

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.actor == "alice@example.com"
        assert event.actor_name == "Alice Example"
        assert event.auth_type == "azure_ad"
        assert event.request_id == "req_123"

    def test_manual_write_mints_change_id_for_autorecording(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.trigger == TRIGGER_MANUAL
        assert event.change_id is not None
        assert event.autorecord is True

    def test_scheduler_write_reuses_existing_change_id(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with (
            patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)),
            change_context(
                trigger=TRIGGER_SCHEDULER,
                change_id="existing-id",
                change_name="Nightly rotation",
                actor="host:abcd1234",
            ),
        ):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.trigger == TRIGGER_SCHEDULER
        assert event.change_id == "existing-id"
        assert event.change_name == "Nightly rotation"
        assert event.autorecord is False


class TestFailureEmission:
    """Failed updates emit change_failed and still raise."""

    def test_prerequisite_failure(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with (
            patch("dns.query.tcp", return_value=_response(dns.rcode.NXRRSET)),
            pytest.raises(PrerequisiteFailedError),
        ):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.event == EVENT_CHANGE_FAILED
        assert event.rcode == "NXRRSET"
        assert event.error is not None

    def test_server_failure_rcode(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with (
            patch("dns.query.tcp", return_value=_response(dns.rcode.SERVFAIL)),
            pytest.raises(UpdateError),
        ):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.event == EVENT_CHANGE_FAILED
        assert event.rcode == "SERVFAIL"

    def test_transport_exception(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with (
            patch("dns.query.tcp", side_effect=DNSException("timed out")),
            pytest.raises(UpdateError),
        ):
            client._send_update(_update(), ZONE)

        event = dispatcher.events[0]
        assert event.event == EVENT_CHANGE_FAILED
        assert event.rcode is None
        assert "timed out" in (event.error or "")


class TestEmissionSafety:
    """Notification problems must never break a DNS write."""

    def test_no_dispatcher_is_fine(self):
        client = _client(None)

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client._send_update(_update(), ZONE)

    def test_dispatcher_exception_does_not_fail_the_update(self):
        dispatcher = MagicMock()
        dispatcher.emit.side_effect = RuntimeError("dispatcher exploded")
        client = _client(dispatcher)

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client._send_update(_update(), ZONE)

        assert dispatcher.emit.called

    def test_high_level_helpers_emit_once(self):
        dispatcher = _CapturingDispatcher()
        client = _client(dispatcher)

        with patch("dns.query.tcp", return_value=_response(dns.rcode.NOERROR)):
            client.add_rrset(ZONE, "www", 300, "A", ["10.0.0.1"])

        assert len(dispatcher.events) == 1
        assert dispatcher.events[0].operations[0].action == "add"
