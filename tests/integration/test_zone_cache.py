"""Integration tests for zone cache with real BIND server."""

from typing import cast

import pytest
from dns_zone_manager.dns.cache import RRsetInfo

from tests.integration.bind_container import BindContainer


@pytest.mark.integration
class TestZoneCacheWithBind:
    """Integration tests for ZoneCache against real BIND 9.20."""

    def test_refresh_zone(self, bind_server: BindContainer, dns_env: dict):
        """Test refreshing zone from BIND server."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Refresh zone
        cached = cache.refresh_zone("test.example.")

        assert cached is not None
        assert cached.zone_name == "test.example."
        assert cached.serial >= 2024010101  # Serial should be at least initial value
        assert cached.rrset_count > 0

    def test_get_rrset_from_cache(self, bind_server: BindContainer, dns_env: dict):
        """Test getting RRset from cache."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Get zone (auto-refreshes)
        rrset = cache.get_rrset("test.example.", "www", "A")

        assert rrset is not None
        assert rrset.rdtype == "A"
        assert "192.0.2.10" in rrset.records

    def test_get_all_rrsets(self, bind_server: BindContainer, dns_env: dict):
        """Test getting all RRsets from cached zone."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cached = cache.get_or_refresh_zone("test.example.")
        rrsets_result = cached.get_all_rrsets()
        assert isinstance(rrsets_result, list)
        rrsets = cast(list[RRsetInfo], rrsets_result)

        assert len(rrsets) > 0

        # Should have SOA, NS, A, MX, TXT records
        types = {r.rdtype for r in rrsets}
        assert "SOA" in types
        assert "NS" in types
        assert "A" in types

    def test_cache_update_after_add(self, bind_server: BindContainer, dns_env: dict):
        """Test cache updates after adding records."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Ensure zone is cached
        cache.refresh_zone("test.example.")

        # Add record via client
        client.add_rrset(
            zone="test.example.",
            name="cache-test-add",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.250"],
            prereq_not_exists=True,
        )

        # Update cache
        cache.update_cache_after_add(
            zone="test.example.",
            name="cache-test-add",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.250"],
        )

        # Verify cache was updated
        rrset = cache.get_rrset("test.example.", "cache-test-add", "A")
        assert rrset is not None
        assert "192.0.2.250" in rrset.records

    def test_cache_update_after_delete(self, bind_server: BindContainer, dns_env: dict):
        """Test cache updates after deleting records."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Add record first
        client.add_rrset(
            zone="test.example.",
            name="cache-test-delete",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.251"],
            prereq_not_exists=True,
        )

        # Refresh cache to include new record
        cache.refresh_zone("test.example.")

        # Delete via client
        client.delete_rrset(
            zone="test.example.",
            name="cache-test-delete",
            rdtype="A",
            prereq_records=["192.0.2.251"],
        )

        # Update cache
        cache.update_cache_after_delete(
            zone="test.example.",
            name="cache-test-delete",
            rdtype="A",
        )

        # Verify cache was updated
        rrset = cache.get_rrset("test.example.", "cache-test-delete", "A")
        assert rrset is None

    def test_cache_update_after_replace(self, bind_server: BindContainer, dns_env: dict):
        """Test cache updates after replacing records."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Add initial record
        client.add_rrset(
            zone="test.example.",
            name="cache-test-replace",
            ttl=3600,
            rdtype="A",
            records=["192.0.2.252"],
            prereq_not_exists=True,
        )

        # Refresh cache
        cache.refresh_zone("test.example.")

        # Replace via client
        client.replace_rrset(
            zone="test.example.",
            name="cache-test-replace",
            ttl=7200,
            rdtype="A",
            new_records=["192.0.2.253"],
            prereq_records=["192.0.2.252"],
        )

        # Update cache
        cache.update_cache_after_replace(
            zone="test.example.",
            name="cache-test-replace",
            ttl=7200,
            rdtype="A",
            records=["192.0.2.253"],
        )

        # Verify cache was updated
        rrset = cache.get_rrset("test.example.", "cache-test-replace", "A")
        assert rrset is not None
        assert "192.0.2.253" in rrset.records
        assert "192.0.2.252" not in rrset.records
        assert rrset.ttl == 7200

    def test_list_zones(self, bind_server: BindContainer, dns_env: dict):
        """Test listing cached zones."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Initially empty
        assert len(cache.list_zones()) == 0

        # After refresh
        cache.refresh_zone("test.example.")
        zones = cache.list_zones()
        assert "test.example." in zones

    def test_invalidate_zone(self, bind_server: BindContainer, dns_env: dict):
        """Test invalidating zone cache."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Cache zone
        cache.refresh_zone("test.example.")
        assert "test.example." in cache.list_zones()

        # Invalidate
        cache.invalidate_zone("test.example.")
        assert "test.example." not in cache.list_zones()

    def test_invalidate_all(self, bind_server: BindContainer, dns_env: dict):
        """Test invalidating all cached zones."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Cache zone
        cache.refresh_zone("test.example.")

        # Invalidate all
        cache.invalidate_all()
        assert len(cache.list_zones()) == 0

    def test_get_rrsets_by_name(self, bind_server: BindContainer, dns_env: dict):
        """Test getting all RRsets for a specific name."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cached = cache.get_or_refresh_zone("test.example.")

        # Zone apex should have SOA, NS, MX, TXT
        rrsets = cached.get_rrsets_by_name("@")
        assert len(rrsets) > 0

        types = {r.rdtype for r in rrsets}
        assert "SOA" in types
