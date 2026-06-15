"""Unit tests for DNS record types."""

from dns_zone_manager.dns.types import (
    DNS_RECORD_TYPES,
    PROTECTED_TYPES,
    UPDATABLE_TYPES,
    RRType,
    get_supported_types,
    get_type_code,
    get_type_name,
    is_valid_type,
)


class TestRRType:
    """Tests for the RRType enum."""

    def test_common_types_have_correct_values(self):
        """Test that common record types have the correct IANA values."""
        assert RRType.A == 1
        assert RRType.NS == 2
        assert RRType.CNAME == 5
        assert RRType.SOA == 6
        assert RRType.PTR == 12
        assert RRType.MX == 15
        assert RRType.TXT == 16
        assert RRType.AAAA == 28
        assert RRType.SRV == 33
        assert RRType.DS == 43
        assert RRType.DNSKEY == 48
        assert RRType.TLSA == 52
        assert RRType.CAA == 257

    def test_dnssec_types(self):
        """Test DNSSEC-related record types."""
        assert RRType.RRSIG == 46
        assert RRType.NSEC == 47
        assert RRType.NSEC3 == 50
        assert RRType.NSEC3PARAM == 51

    def test_modern_types(self):
        """Test modern record types."""
        assert RRType.SVCB == 64
        assert RRType.HTTPS == 65


class TestDNSRecordTypes:
    """Tests for DNS_RECORD_TYPES mapping."""

    def test_all_common_types_defined(self):
        """Test that all common record types are defined."""
        common_types = [
            "A",
            "AAAA",
            "NS",
            "CNAME",
            "SOA",
            "PTR",
            "MX",
            "TXT",
            "SRV",
            "CAA",
            "DS",
            "DNSKEY",
            "TLSA",
            "SSHFP",
        ]
        for rtype in common_types:
            assert rtype in DNS_RECORD_TYPES, f"{rtype} should be in DNS_RECORD_TYPES"

    def test_type_info_has_required_fields(self):
        """Test that RecordTypeInfo has all required fields."""
        for name, info in DNS_RECORD_TYPES.items():
            assert info.type_code > 0 or info.type_code == 0, f"{name} should have valid type_code"
            assert info.name == name, f"{name} name should match key"
            assert info.description, f"{name} should have description"

    def test_a_record_info(self):
        """Test A record type info."""
        info = DNS_RECORD_TYPES["A"]
        assert info.type_code == 1
        assert info.name == "A"
        assert "IPv4" in info.description or "address" in info.description.lower()
        assert info.rfc == "RFC1035"
        assert not info.obsolete

    def test_obsolete_types_marked(self):
        """Test that obsolete types are properly marked."""
        obsolete_types = ["MD", "MF", "SIG", "KEY", "NXT", "A6", "MAILA", "SPF"]
        for rtype in obsolete_types:
            if rtype in DNS_RECORD_TYPES:
                assert DNS_RECORD_TYPES[rtype].obsolete, f"{rtype} should be marked obsolete"


class TestTypeHelperFunctions:
    """Tests for type helper functions."""

    def test_get_type_code_valid(self):
        """Test get_type_code with valid types."""
        assert get_type_code("A") == 1
        assert get_type_code("AAAA") == 28
        assert get_type_code("mx") == 15  # Case insensitive
        assert get_type_code("TxT") == 16  # Mixed case

    def test_get_type_code_invalid(self):
        """Test get_type_code with invalid types."""
        assert get_type_code("INVALID") is None
        assert get_type_code("") is None
        assert get_type_code("FAKE123") is None

    def test_get_type_name_valid(self):
        """Test get_type_name with valid codes."""
        assert get_type_name(1) == "A"
        assert get_type_name(28) == "AAAA"
        assert get_type_name(15) == "MX"

    def test_get_type_name_invalid(self):
        """Test get_type_name with invalid codes."""
        assert get_type_name(99999) is None
        assert get_type_name(-1) is None

    def test_is_valid_type(self):
        """Test is_valid_type function."""
        assert is_valid_type("A")
        assert is_valid_type("aaaa")  # Case insensitive
        assert is_valid_type("MX")
        assert not is_valid_type("INVALID")
        assert not is_valid_type("")

    def test_get_supported_types(self):
        """Test get_supported_types returns all types."""
        types = get_supported_types()
        assert isinstance(types, list)
        assert len(types) > 50  # We have many types defined
        assert "A" in types
        assert "AAAA" in types
        assert "MX" in types


class TestProtectedAndUpdatableTypes:
    """Tests for protected and updatable type sets."""

    def test_protected_types_not_updatable(self):
        """Test that protected types are not in updatable types."""
        for ptype in PROTECTED_TYPES:
            assert ptype not in UPDATABLE_TYPES, f"{ptype} should not be updatable"

    def test_meta_types_are_protected(self):
        """Test that meta/special types are protected."""
        meta_types = ["OPT", "TSIG", "TKEY", "AXFR", "IXFR", "ANY"]
        for mtype in meta_types:
            assert mtype in PROTECTED_TYPES, f"{mtype} should be protected"

    def test_common_types_are_updatable(self):
        """Test that common data types are updatable."""
        updatable = ["A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA", "NS", "PTR"]
        for utype in updatable:
            assert utype in UPDATABLE_TYPES, f"{utype} should be updatable"

    def test_soa_is_updatable(self):
        """Test that SOA records can be updated (for serial bumps)."""
        assert "SOA" in UPDATABLE_TYPES
