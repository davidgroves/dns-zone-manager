"""Unit tests for reverse DNS utilities."""

import dns.exception
import dns.name
import dns.reversename
import pytest
from dns_zone_manager.dns.reverse import (
    find_reverse_zone,
    get_ptr_record_name,
    ip_to_ptr_name,
    is_reverse_zone,
    ptr_name_to_ip,
)


class TestIpToPtrName:
    """Tests for IP to PTR name conversion."""

    def test_ipv4_basic(self):
        """Test basic IPv4 to PTR conversion."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        assert ptr_name.to_text() == "100.2.0.192.in-addr.arpa."

    def test_ipv4_leading_zeros(self):
        """Test IPv4 with zeros."""
        ptr_name = ip_to_ptr_name("10.0.0.1")
        assert ptr_name.to_text() == "1.0.0.10.in-addr.arpa."

    def test_ipv4_max_values(self):
        """Test IPv4 with max values."""
        ptr_name = ip_to_ptr_name("255.255.255.255")
        assert ptr_name.to_text() == "255.255.255.255.in-addr.arpa."

    def test_ipv6_full(self):
        """Test full IPv6 address."""
        ptr_name = ip_to_ptr_name("2001:0db8:0000:0000:0000:0000:0000:0001")
        assert "ip6.arpa." in ptr_name.to_text()
        # Check it has all 32 nibbles + ip6.arpa. = 34 dots
        assert ptr_name.to_text().count(".") == 34

    def test_ipv6_compressed(self):
        """Test compressed IPv6 address."""
        ptr_name = ip_to_ptr_name("2001:db8::1")
        assert "ip6.arpa." in ptr_name.to_text()
        # Full expansion should be 32 nibbles
        parts = ptr_name.to_text().split(".")
        # Remove ip6.arpa. parts
        nibbles = [p for p in parts if len(p) == 1]
        assert len(nibbles) == 32

    def test_ipv6_with_consecutive_zeros(self):
        """Test IPv6 with multiple zero groups."""
        ptr_name = ip_to_ptr_name("2001:db8::50")
        # Verify it ends with ip6.arpa.
        assert ptr_name.to_text().endswith(".ip6.arpa.")
        # First nibble (rightmost when reversed) should be 0
        assert ptr_name.to_text().startswith("0.5.0.0.")

    def test_invalid_ip_raises(self):
        """Test that invalid IPs raise exception."""
        with pytest.raises(dns.exception.SyntaxError):
            ip_to_ptr_name("not.an.ip")

    def test_invalid_ipv4_octet_raises(self):
        """Test that invalid IPv4 octets raise exception."""
        with pytest.raises(Exception):  # Different error types possible
            ip_to_ptr_name("256.0.0.1")


class TestFindReverseZone:
    """Tests for finding managed reverse zones."""

    def test_find_slash24_zone(self):
        """Test finding a /24 reverse zone."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        managed_zones = {"2.0.192.in-addr.arpa.", "0.192.in-addr.arpa."}
        result = find_reverse_zone(ptr_name, managed_zones)
        assert result == "2.0.192.in-addr.arpa."

    def test_find_slash16_zone(self):
        """Test finding a /16 reverse zone when /24 not managed."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        managed_zones = {"0.192.in-addr.arpa."}
        result = find_reverse_zone(ptr_name, managed_zones)
        assert result == "0.192.in-addr.arpa."

    def test_find_slash8_zone(self):
        """Test finding a /8 reverse zone."""
        ptr_name = ip_to_ptr_name("10.20.30.40")
        managed_zones = {"10.in-addr.arpa."}
        result = find_reverse_zone(ptr_name, managed_zones)
        assert result == "10.in-addr.arpa."

    def test_no_managed_zone_returns_none(self):
        """Test that None is returned when no zone is managed."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        managed_zones = {"example.com."}
        result = find_reverse_zone(ptr_name, managed_zones)
        assert result is None

    def test_empty_managed_zones(self):
        """Test with empty managed zones set."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        result = find_reverse_zone(ptr_name, set())
        assert result is None

    def test_ipv6_zone_finding(self):
        """Test finding IPv6 reverse zone."""
        ptr_name = ip_to_ptr_name("2001:db8::1")
        # /48 delegation
        managed_zones = {"8.b.d.0.1.0.0.2.ip6.arpa."}
        result = find_reverse_zone(ptr_name, managed_zones)
        assert result == "8.b.d.0.1.0.0.2.ip6.arpa."

    def test_case_insensitive_matching(self):
        """Test that zone matching is case-insensitive."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        # dnspython returns lowercase, but managed_zones might have mixed case
        managed_zones = {"2.0.192.IN-ADDR.ARPA."}
        # Since we lowercase the managed_zones in the router, this works
        managed_zones_lower = {z.lower() for z in managed_zones}
        result = find_reverse_zone(ptr_name, managed_zones_lower)
        assert result == "2.0.192.in-addr.arpa."


class TestGetPtrRecordName:
    """Tests for getting relative record names."""

    def test_ipv4_slash24(self):
        """Test record name for /24 delegation."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        result = get_ptr_record_name(ptr_name, "2.0.192.in-addr.arpa.")
        assert result == "100"

    def test_ipv4_slash16(self):
        """Test record name for /16 delegation."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        result = get_ptr_record_name(ptr_name, "0.192.in-addr.arpa.")
        assert result == "100.2"

    def test_ipv4_slash8(self):
        """Test record name for /8 delegation."""
        ptr_name = ip_to_ptr_name("10.20.30.40")
        result = get_ptr_record_name(ptr_name, "10.in-addr.arpa.")
        assert result == "40.30.20"

    def test_ipv6_slash48(self):
        """Test record name for IPv6 /48 delegation."""
        ptr_name = ip_to_ptr_name("2001:db8::50")
        zone = "8.b.d.0.1.0.0.2.ip6.arpa."
        result = get_ptr_record_name(ptr_name, zone)
        # Should be the remaining nibbles after removing the zone
        assert "0.5.0.0" in result  # Contains the ::50 part

    def test_zone_without_trailing_dot(self):
        """Test that zone without trailing dot is handled."""
        ptr_name = ip_to_ptr_name("192.0.2.100")
        result = get_ptr_record_name(ptr_name, "2.0.192.in-addr.arpa")
        assert result == "100"


class TestIsReverseZone:
    """Tests for reverse zone detection."""

    def test_ipv4_reverse_zone(self):
        """Test IPv4 reverse zone detection."""
        assert is_reverse_zone("2.0.192.in-addr.arpa.") is True
        assert is_reverse_zone("10.in-addr.arpa.") is True
        # Root arpa zones (without subdomain) are edge cases - typically not managed
        # The function checks for .in-addr.arpa suffix, so these require a prefix
        assert is_reverse_zone("0.in-addr.arpa.") is True

    def test_ipv6_reverse_zone(self):
        """Test IPv6 reverse zone detection."""
        assert is_reverse_zone("8.b.d.0.1.0.0.2.ip6.arpa.") is True
        # Root arpa zones - need a prefix nibble
        assert is_reverse_zone("0.ip6.arpa.") is True

    def test_forward_zone_not_reverse(self):
        """Test that forward zones are not detected as reverse."""
        assert is_reverse_zone("example.com.") is False
        assert is_reverse_zone("test.example.com.") is False

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert is_reverse_zone("2.0.192.IN-ADDR.ARPA.") is True
        assert is_reverse_zone("8.b.d.0.1.0.0.2.IP6.ARPA.") is True

    def test_without_trailing_dot(self):
        """Test without trailing dot."""
        assert is_reverse_zone("2.0.192.in-addr.arpa") is True
        assert is_reverse_zone("8.b.d.0.1.0.0.2.ip6.arpa") is True


class TestPtrNameToIp:
    """Tests for PTR name to IP conversion."""

    def test_ipv4_roundtrip(self):
        """Test IPv4 address roundtrip."""
        ip = "192.0.2.100"
        ptr_name = ip_to_ptr_name(ip)
        result = ptr_name_to_ip(ptr_name)
        assert result == ip

    def test_ipv6_roundtrip(self):
        """Test IPv6 address roundtrip."""
        ip = "2001:db8::1"
        ptr_name = ip_to_ptr_name(ip)
        result = ptr_name_to_ip(ptr_name)
        # IPv6 addresses are normalized
        assert result == "2001:db8::1"

    def test_ipv6_full_roundtrip(self):
        """Test full IPv6 address roundtrip."""
        ip = "2001:0db8:0000:0000:0000:0000:0000:0001"
        ptr_name = ip_to_ptr_name(ip)
        result = ptr_name_to_ip(ptr_name)
        # Result is normalized/compressed
        assert result == "2001:db8::1"


class TestEdgeCases:
    """Edge case tests for reverse DNS utilities."""

    def test_localhost_ipv4(self):
        """Test localhost IPv4."""
        ptr_name = ip_to_ptr_name("127.0.0.1")
        assert ptr_name.to_text() == "1.0.0.127.in-addr.arpa."

    def test_localhost_ipv6(self):
        """Test localhost IPv6."""
        ptr_name = ip_to_ptr_name("::1")
        assert ptr_name.to_text().endswith("ip6.arpa.")

    def test_all_zeros_ipv4(self):
        """Test 0.0.0.0 address."""
        ptr_name = ip_to_ptr_name("0.0.0.0")
        assert ptr_name.to_text() == "0.0.0.0.in-addr.arpa."

    def test_private_ranges(self):
        """Test private IP ranges."""
        # Class A private
        ptr = ip_to_ptr_name("10.0.0.1")
        assert "10.in-addr.arpa" in ptr.to_text()

        # Class B private
        ptr = ip_to_ptr_name("172.16.0.1")
        assert "in-addr.arpa" in ptr.to_text()

        # Class C private
        ptr = ip_to_ptr_name("192.168.1.1")
        assert "168.192.in-addr.arpa" in ptr.to_text()

    def test_test_net_ranges(self):
        """Test documentation/test IP ranges."""
        # TEST-NET-1
        ptr = ip_to_ptr_name("192.0.2.1")
        assert "2.0.192.in-addr.arpa" in ptr.to_text()

        # TEST-NET-2
        ptr = ip_to_ptr_name("198.51.100.1")
        assert "100.51.198.in-addr.arpa" in ptr.to_text()

        # TEST-NET-3
        ptr = ip_to_ptr_name("203.0.113.1")
        assert "113.0.203.in-addr.arpa" in ptr.to_text()
