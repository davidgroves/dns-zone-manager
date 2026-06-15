"""Unit tests for the DNS client helpers."""

import socket

import dns.message
import dns.query
import dns.rcode
import pytest
from dns.exception import Timeout
from dns_zone_manager.dns.client import DNSClient, resolve_server_address


class TestResolveServerAddress:
    """Tests for resolve_server_address (hostname -> IP resolution)."""

    def test_ipv4_literal_passthrough(self):
        """An IPv4 literal is returned unchanged (no resolution attempted)."""
        assert resolve_server_address("172.28.0.10") == "172.28.0.10"

    def test_ipv6_literal_passthrough(self):
        """An IPv6 literal is returned unchanged."""
        assert resolve_server_address("2001:db8::1") == "2001:db8::1"

    def test_hostname_resolves_to_ipv4(self, monkeypatch):
        """A hostname is resolved to an IPv4 address via the system resolver."""

        def fake_getaddrinfo(host, port):
            assert host == "bind"
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.5.0.7", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        # IPv4 is preferred even when an IPv6 result is returned first.
        assert resolve_server_address("bind") == "10.5.0.7"

    def test_hostname_falls_back_to_first_result(self, monkeypatch):
        """When no IPv4 result exists, the first returned address is used."""

        def fake_getaddrinfo(host, port):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:db8::99", 0, 0, 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert resolve_server_address("ipv6-only-host") == "2001:db8::99"

    def test_unresolvable_hostname_raises_value_error(self, monkeypatch):
        """A hostname that cannot be resolved raises a clear ValueError."""

        def fake_getaddrinfo(host, port):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="Could not resolve DNS server hostname"):
            resolve_server_address("does-not-exist.invalid")


class TestCheckServerResponding:
    """Tests for DNSClient.check_server_responding (any response = connected)."""

    def _make_client(self) -> DNSClient:
        # Build a bare client without running __init__ (which needs settings).
        client = DNSClient.__new__(DNSClient)
        client.server = "192.0.2.1"
        client.port = 53
        client.timeout = 1.0
        return client

    def test_any_response_counts_as_connected(self, monkeypatch):
        """A returned message (even REFUSED) means the server is responding."""
        client = self._make_client()

        def fake_udp(request, where, port=53, timeout=None):
            response = dns.message.make_response(request)
            response.set_rcode(dns.rcode.REFUSED)
            return response

        monkeypatch.setattr(dns.query, "udp", fake_udp)
        assert client.check_server_responding() is True

    def test_timeout_counts_as_not_responding(self, monkeypatch):
        """A timeout (no response at all) means the server is not responding."""
        client = self._make_client()

        def fake_udp(request, where, port=53, timeout=None):
            raise Timeout("timed out")

        monkeypatch.setattr(dns.query, "udp", fake_udp)
        assert client.check_server_responding() is False

    def test_transport_error_counts_as_not_responding(self, monkeypatch):
        """A transport/OS error means the server is not responding."""
        client = self._make_client()

        def fake_udp(request, where, port=53, timeout=None):
            raise OSError("connection refused")

        monkeypatch.setattr(dns.query, "udp", fake_udp)
        assert client.check_server_responding() is False
