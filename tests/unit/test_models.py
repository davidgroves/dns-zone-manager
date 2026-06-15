"""Unit tests for Pydantic models."""

import pytest
from dns_zone_manager.models.records import (
    AAAARecord,
    ARecord,
    CAARecord,
    CNAMERecord,
    MXRecord,
    NSRecord,
    PTRRecord,
    RRsetData,
    SOARecord,
    SRVRecord,
    SSHFPRecord,
    TLSARecord,
    TXTRecord,
    parse_record_data,
)
from dns_zone_manager.models.requests import (
    AddRRsetRequest,
    DeleteRRsetRequest,
    ReplaceRRsetRequest,
    RRsetResponse,
    SuccessResponse,
    ZoneResponse,
)
from pydantic import ValidationError


class TestRRsetData:
    """Tests for RRsetData model."""

    def test_valid_a_record(self):
        """Test creating valid A record RRset."""
        rrset = RRsetData(
            name="www",
            ttl=3600,
            type="A",
            records=["192.0.2.1", "192.0.2.2"],
        )
        assert rrset.name == "www"
        assert rrset.ttl == 3600
        assert rrset.record_type == "A"
        assert len(rrset.records) == 2

    def test_type_normalized_to_uppercase(self):
        """Test that record type is normalized to uppercase."""
        rrset = RRsetData(name="www", type="aaaa", records=["::1"])
        assert rrset.record_type == "AAAA"

    def test_invalid_record_type(self):
        """Test that invalid record type raises error."""
        with pytest.raises(ValidationError) as exc_info:
            RRsetData(name="www", type="INVALID", records=["test"])
        assert "Unknown record type" in str(exc_info.value)

    def test_protected_type_rejected(self):
        """Test that protected types are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RRsetData(name="www", type="AXFR", records=["test"])
        assert "cannot be modified" in str(exc_info.value)

    def test_empty_name_rejected(self):
        """Test that empty name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RRsetData(name="", type="A", records=["192.0.2.1"])
        assert "cannot be empty" in str(exc_info.value)

    def test_name_too_long_rejected(self):
        """Test that overly long name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RRsetData(name="x" * 300, type="A", records=["192.0.2.1"])
        assert "too long" in str(exc_info.value)

    def test_empty_records_rejected(self):
        """Test that empty records list is rejected."""
        with pytest.raises(ValidationError):
            RRsetData(name="www", type="A", records=[])

    def test_default_ttl(self):
        """Test default TTL value."""
        rrset = RRsetData(name="www", type="A", records=["192.0.2.1"])
        assert rrset.ttl == 3600

    def test_ttl_bounds(self):
        """Test TTL boundary values."""
        # Minimum TTL
        rrset = RRsetData(name="www", type="A", ttl=0, records=["192.0.2.1"])
        assert rrset.ttl == 0

        # Maximum TTL
        rrset = RRsetData(name="www", type="A", ttl=2147483647, records=["192.0.2.1"])
        assert rrset.ttl == 2147483647

        # TTL too high
        with pytest.raises(ValidationError):
            RRsetData(name="www", type="A", ttl=2147483648, records=["192.0.2.1"])


class TestTypeSpecificRecords:
    """Tests for type-specific record models."""

    def test_a_record(self):
        """Test A record model."""
        record = ARecord(address="192.0.2.1")  # type: ignore[arg-type]
        assert record.to_string() == "192.0.2.1"

    def test_a_record_invalid_ip(self):
        """Test A record with invalid IP."""
        with pytest.raises(ValidationError):
            ARecord(address="not-an-ip")  # type: ignore[arg-type]

    def test_aaaa_record(self):
        """Test AAAA record model."""
        record = AAAARecord(address="2001:db8::1")  # type: ignore[arg-type]
        assert record.to_string() == "2001:db8::1"

    def test_mx_record(self):
        """Test MX record model."""
        record = MXRecord(priority=10, exchange="mail.example.com.")
        assert record.to_string() == "10 mail.example.com."

    def test_mx_record_priority_bounds(self):
        """Test MX record priority bounds."""
        # Valid bounds
        MXRecord(priority=0, exchange="mail.example.com.")
        MXRecord(priority=65535, exchange="mail.example.com.")

        # Invalid
        with pytest.raises(ValidationError):
            MXRecord(priority=-1, exchange="mail.example.com.")
        with pytest.raises(ValidationError):
            MXRecord(priority=65536, exchange="mail.example.com.")

    def test_srv_record(self):
        """Test SRV record model."""
        record = SRVRecord(priority=10, weight=20, port=443, target="server.example.com.")
        assert record.to_string() == "10 20 443 server.example.com."

    def test_txt_record(self):
        """Test TXT record model."""
        record = TXTRecord(text="v=spf1 include:example.com ~all")
        assert "v=spf1" in record.to_string()

    def test_txt_record_with_spaces(self):
        """Test TXT record quoting with spaces."""
        record = TXTRecord(text="hello world")
        assert record.to_string() == '"hello world"'

    def test_caa_record(self):
        """Test CAA record model."""
        record = CAARecord(flags=0, tag="issue", value="letsencrypt.org")
        assert record.to_string() == '0 issue "letsencrypt.org"'

    def test_caa_record_invalid_tag(self):
        """Test CAA record with invalid tag."""
        with pytest.raises(ValidationError):
            CAARecord(flags=0, tag="invalid", value="example.com")  # type: ignore[arg-type]

    def test_soa_record(self):
        """Test SOA record model."""
        record = SOARecord(
            mname="ns1.example.com.",
            rname="admin.example.com.",
            serial=2024010101,
            refresh=3600,
            retry=600,
            expire=604800,
            minimum=300,
        )
        result = record.to_string()
        assert "ns1.example.com." in result
        assert "2024010101" in result

    def test_ns_record(self):
        """Test NS record model."""
        record = NSRecord(nsdname="ns1.example.com.")
        assert record.to_string() == "ns1.example.com."

    def test_cname_record(self):
        """Test CNAME record model."""
        record = CNAMERecord(cname="target.example.com.")
        assert record.to_string() == "target.example.com."

    def test_ptr_record(self):
        """Test PTR record model."""
        record = PTRRecord(ptrdname="host.example.com.")
        assert record.to_string() == "host.example.com."

    def test_sshfp_record(self):
        """Test SSHFP record model."""
        record = SSHFPRecord(algorithm=1, fp_type=1, fingerprint="abc123")
        assert record.to_string() == "1 1 abc123"

    def test_tlsa_record(self):
        """Test TLSA record model."""
        record = TLSARecord(
            usage=3,
            selector=1,
            matching_type=1,
            certificate_data="abc123def456",
        )
        assert record.to_string() == "3 1 1 abc123def456"


class TestParseRecordData:
    """Tests for parse_record_data function."""

    def test_parse_string_data(self):
        """Test parsing string record data."""
        result = parse_record_data("A", "192.0.2.1")
        assert result == "192.0.2.1"

    def test_parse_dict_a_record(self):
        """Test parsing dict A record data."""
        result = parse_record_data("A", {"address": "192.0.2.1"})
        assert result == "192.0.2.1"

    def test_parse_dict_mx_record(self):
        """Test parsing dict MX record data."""
        result = parse_record_data("MX", {"priority": 10, "exchange": "mail.example.com."})
        assert result == "10 mail.example.com."

    def test_parse_unknown_type_requires_string(self):
        """Test that unknown types require string format."""
        with pytest.raises(ValueError) as exc_info:
            parse_record_data("UNKNOWNTYPE", {"foo": "bar"})
        assert "string format" in str(exc_info.value).lower()


class TestRequestModels:
    """Tests for API request models."""

    def test_add_rrset_request(self):
        """Test AddRRsetRequest model."""
        request = AddRRsetRequest(
            name="www",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        assert request.name == "www"
        assert request.type == "A"

    def test_delete_rrset_request_full(self):
        """Test DeleteRRsetRequest with specific records."""
        request = DeleteRRsetRequest(
            name="www",
            type="A",
            records=["192.0.2.1"],
        )
        assert request.records == ["192.0.2.1"]

    def test_delete_rrset_request_all(self):
        """Test DeleteRRsetRequest for entire RRset."""
        request = DeleteRRsetRequest(
            name="www",
            type="A",
        )
        assert request.records is None

    def test_replace_rrset_request(self):
        """Test ReplaceRRsetRequest model."""
        request = ReplaceRRsetRequest(
            name="www",
            ttl=7200,
            type="A",
            records=["192.0.2.10"],
        )
        assert request.ttl == 7200


class TestResponseModels:
    """Tests for API response models."""

    def test_rrset_response(self):
        """Test RRsetResponse model."""
        response = RRsetResponse(
            name="www.example.com.",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        assert response.name == "www.example.com."

    def test_success_response(self):
        """Test SuccessResponse model."""
        response = SuccessResponse(
            success=True,
            message="Operation completed",
        )
        assert response.success
        assert response.rrset is None

    def test_success_response_with_rrset(self):
        """Test SuccessResponse with RRset."""
        rrset = RRsetResponse(
            name="www.example.com.",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        response = SuccessResponse(
            success=True,
            message="Created",
            rrset=rrset,
        )
        assert response.rrset is not None
        assert response.rrset.type == "A"

    def test_zone_response(self):
        """Test ZoneResponse model."""
        from datetime import UTC, datetime

        response = ZoneResponse(
            zone="example.com.",
            serial=2024010101,
            rrset_count=42,
            last_refresh=datetime.now(UTC),
        )
        assert response.zone == "example.com."
        assert response.serial == 2024010101


class TestClassSupport:
    """Tests for rdclass support in models."""

    def test_add_rrset_request_default_class(self):
        """Test AddRRsetRequest defaults to IN class."""
        request = AddRRsetRequest(
            name="www",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        assert request.rdclass == "IN"

    def test_add_rrset_request_with_class(self):
        """Test AddRRsetRequest with explicit class."""
        request = AddRRsetRequest(
            name="version",
            ttl=3600,
            type="TXT",
            rdclass="CH",
            records=['"BIND 9.20"'],
        )
        assert request.rdclass == "CH"

    def test_delete_rrset_request_default_class(self):
        """Test DeleteRRsetRequest defaults to IN class."""
        request = DeleteRRsetRequest(
            name="www",
            type="A",
        )
        assert request.rdclass == "IN"

    def test_delete_rrset_request_with_class(self):
        """Test DeleteRRsetRequest with explicit class."""
        request = DeleteRRsetRequest(
            name="version",
            type="TXT",
            rdclass="CH",
        )
        assert request.rdclass == "CH"

    def test_replace_rrset_request_default_class(self):
        """Test ReplaceRRsetRequest defaults to IN class."""
        request = ReplaceRRsetRequest(
            name="www",
            ttl=7200,
            type="A",
            records=["192.0.2.10"],
        )
        assert request.rdclass == "IN"

    def test_replace_rrset_request_with_class(self):
        """Test ReplaceRRsetRequest with explicit class."""
        request = ReplaceRRsetRequest(
            name="version",
            ttl=3600,
            type="TXT",
            rdclass="HS",
            records=['"new version"'],
        )
        assert request.rdclass == "HS"

    def test_rrset_response_default_class(self):
        """Test RRsetResponse defaults to IN class."""
        response = RRsetResponse(
            name="www.example.com.",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        assert response.rdclass == "IN"

    def test_rrset_response_with_class(self):
        """Test RRsetResponse with explicit class."""
        response = RRsetResponse(
            name="version.bind.",
            ttl=0,
            type="TXT",
            rdclass="CH",
            records=['"BIND 9.20"'],
        )
        assert response.rdclass == "CH"

    def test_rrset_data_default_class(self):
        """Test RRsetData defaults to IN class."""
        rrset = RRsetData(
            name="www",
            ttl=3600,
            type="A",
            records=["192.0.2.1"],
        )
        assert rrset.rdclass == "IN"

    def test_rrset_data_with_class(self):
        """Test RRsetData with explicit class."""
        rrset = RRsetData(
            name="version",
            ttl=3600,
            type="TXT",
            rdclass="CH",
            records=['"BIND 9.20"'],
        )
        assert rrset.rdclass == "CH"
