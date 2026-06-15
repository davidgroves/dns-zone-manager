"""Integration tests for DNS client with real BIND server."""

import pytest

from tests.integration.bind_container import BindContainer


@pytest.mark.integration
class TestDNSClientWithBind:
    """Integration tests for DNSClient against real BIND 9.20."""

    def test_check_zone_exists(self, bind_server: BindContainer, dns_env: dict):
        """Test checking if a zone exists."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # Test zone should exist
        assert client.check_zone_exists("test.example.")

        # Non-existent zone should not exist
        assert not client.check_zone_exists("nonexistent.test.")

    def test_get_zone_serial(self, bind_server: BindContainer, dns_env: dict):
        """Test getting zone serial number."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        serial = client.get_zone_serial("test.example.")
        assert serial is not None
        assert serial >= 2024010101  # Serial should be at least initial value

    def test_get_rrset(self, bind_server: BindContainer, dns_env: dict):
        """Test querying for an RRset."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # Query existing record
        rrset = client.get_rrset("test.example.", "www", "A")
        assert rrset is not None
        assert rrset.rdtype == "A"
        assert "192.0.2.10" in rrset.records

    def test_get_rrset_not_found(self, bind_server: BindContainer, dns_env: dict):
        """Test querying for non-existent RRset."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        rrset = client.get_rrset("test.example.", "nonexistent", "A")
        assert rrset is None

    def test_axfr_zone_transfer(self, bind_server: BindContainer, dns_env: dict):
        """Test AXFR zone transfer."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        zone = client.perform_axfr("test.example.")
        assert zone is not None

        # Check zone has expected records
        assert len(zone.nodes) > 0

    def test_add_rrset(self, bind_server: BindContainer, dns_env: dict):
        """Test adding a new RRset via DDNS."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # Add a new A record
        client.add_rrset(
            zone="test.example.",
            name="newhost",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.100"],
            prereq_not_exists=True,
        )

        # Verify it was added
        rrset = client.get_rrset("test.example.", "newhost", "A")
        assert rrset is not None
        assert "192.0.2.100" in rrset.records

    def test_add_rrset_prereq_fails(self, bind_server: BindContainer, dns_env: dict):
        """Test that adding existing RRset fails with prereq."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient, PrerequisiteFailedError

        settings = get_settings()
        client = DNSClient(settings)

        # www already exists
        with pytest.raises(PrerequisiteFailedError):
            client.add_rrset(
                zone="test.example.",
                name="www",
                ttl=3600,
                rdtype="A",
                records=["192.0.2.200"],
                prereq_not_exists=True,
            )

    def test_delete_rrset(self, bind_server: BindContainer, dns_env: dict):
        """Test deleting an RRset via DDNS."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # First add a record to delete
        client.add_rrset(
            zone="test.example.",
            name="todelete",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.200"],
            prereq_not_exists=True,
        )

        # Verify it exists
        rrset = client.get_rrset("test.example.", "todelete", "A")
        assert rrset is not None

        # Delete it
        client.delete_rrset(
            zone="test.example.",
            name="todelete",
            rdtype="A",
            prereq_records=["192.0.2.200"],
        )

        # Verify it's gone
        rrset = client.get_rrset("test.example.", "todelete", "A")
        assert rrset is None

    def test_replace_rrset(self, bind_server: BindContainer, dns_env: dict):
        """Test replacing an RRset via DDNS."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # First add a record to replace
        client.add_rrset(
            zone="test.example.",
            name="toreplace",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.50"],
            prereq_not_exists=True,
        )

        # Replace it
        client.replace_rrset(
            zone="test.example.",
            name="toreplace",
            ttl=7200,
            rdtype="A",
            new_records=["192.0.2.51", "192.0.2.52"],
            prereq_records=["192.0.2.50"],
        )

        # Verify replacement
        rrset = client.get_rrset("test.example.", "toreplace", "A")
        assert rrset is not None
        assert "192.0.2.51" in rrset.records
        assert "192.0.2.52" in rrset.records
        assert "192.0.2.50" not in rrset.records

    def test_add_txt_record(self, bind_server: BindContainer, dns_env: dict):
        """Test adding a TXT record."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="_dmarc",
            ttl=3600,
            rdtype="TXT",
            records=['"v=DMARC1; p=reject"'],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "_dmarc", "TXT")
        assert rrset is not None
        assert "DMARC1" in rrset.records[0]

    def test_add_mx_record(self, bind_server: BindContainer, dns_env: dict):
        """Test adding an MX record."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="subdomain",
            ttl=3600,
            rdtype="MX",
            records=["10 mail.test.example."],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "subdomain", "MX")
        assert rrset is not None
        assert "10 mail.test.example." in rrset.records

    def test_add_aaaa_record(self, bind_server: BindContainer, dns_env: dict):
        """Test adding an AAAA record."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="ipv6host",
            ttl=3600,
            rdtype="AAAA",
            records=["2001:db8::1"],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "ipv6host", "AAAA")
        assert rrset is not None
        assert "2001:db8::1" in rrset.records

    def test_add_srv_record(self, bind_server: BindContainer, dns_env: dict):
        """Test adding an SRV record."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="_http._tcp",
            ttl=3600,
            rdtype="SRV",
            records=["10 20 80 www.test.example."],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "_http._tcp", "SRV")
        assert rrset is not None

    def test_add_caa_record(self, bind_server: BindContainer, dns_env: dict):
        """Test adding a CAA record."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="@",
            ttl=3600,
            rdtype="CAA",
            records=['0 issue "letsencrypt.org"'],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "@", "CAA")
        assert rrset is not None
        assert "letsencrypt.org" in rrset.records[0]


@pytest.mark.integration
class TestDNSClientClassSupport:
    """Tests for DNS class support in DNSClient."""

    def test_get_rrset_returns_class(self, bind_server: BindContainer, dns_env: dict):
        """Test that get_rrset returns rdclass field."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # Get an existing A record (www exists in test zone)
        rrset = client.get_rrset("test.example.", "www", "A")
        assert rrset is not None
        assert rrset.rdclass == "IN"

    def test_add_rrset_with_default_class(self, bind_server: BindContainer, dns_env: dict):
        """Test add_rrset uses IN class by default."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="classtest-default",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.100"],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "classtest-default", "A")
        assert rrset is not None
        assert rrset.rdclass == "IN"

    def test_add_rrset_with_explicit_in_class(self, bind_server: BindContainer, dns_env: dict):
        """Test add_rrset with explicit IN class."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        client.add_rrset(
            zone="test.example.",
            name="classtest-in",
            ttl=3600,
            rdtype="A",
            rdclass="IN",
            records=["192.0.2.101"],
            prereq_not_exists=True,
        )

        rrset = client.get_rrset("test.example.", "classtest-in", "A")
        assert rrset is not None
        assert rrset.rdclass == "IN"

    def test_delete_rrset_with_class(self, bind_server: BindContainer, dns_env: dict):
        """Test delete_rrset works with class parameter."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # First add a record
        client.add_rrset(
            zone="test.example.",
            name="classtest-delete",
            ttl=3600,
            rdtype="A",
            rdclass="IN",
            records=["192.0.2.102"],
            prereq_not_exists=True,
        )

        # Verify it exists
        rrset = client.get_rrset("test.example.", "classtest-delete", "A")
        assert rrset is not None

        # Delete it with explicit class
        client.delete_rrset(
            zone="test.example.",
            name="classtest-delete",
            rdtype="A",
            rdclass="IN",
        )

        # Verify it's gone
        rrset = client.get_rrset("test.example.", "classtest-delete", "A")
        assert rrset is None

    def test_replace_rrset_with_class(self, bind_server: BindContainer, dns_env: dict):
        """Test replace_rrset works with class parameter."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # First add a record
        client.add_rrset(
            zone="test.example.",
            name="classtest-replace",
            ttl=3600,
            rdtype="A",
            rdclass="IN",
            records=["192.0.2.103"],
            prereq_not_exists=True,
        )

        # Replace it with new value
        client.replace_rrset(
            zone="test.example.",
            name="classtest-replace",
            ttl=7200,
            rdtype="A",
            rdclass="IN",
            new_records=["192.0.2.104"],
        )

        # Verify it was replaced
        rrset = client.get_rrset("test.example.", "classtest-replace", "A")
        assert rrset is not None
        assert rrset.records == ["192.0.2.104"]
        assert rrset.ttl == 7200
