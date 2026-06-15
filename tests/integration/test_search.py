"""Integration tests for search functionality."""

import re

import pytest
from fastapi.testclient import TestClient

from tests.integration.bind_container import BindContainer


@pytest.mark.integration
class TestSearchZoneCache:
    """Integration tests for search methods in ZoneCache."""

    def test_search_by_name_pattern(self, bind_server: BindContainer, dns_env: dict):
        """Test searching by name pattern."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Refresh zone first
        cache.refresh_zone("test.example.")

        # Search for names starting with "www"
        pattern = re.compile(r"^www\.", re.IGNORECASE)
        result = cache.search_zone("test.example.", name_pattern=pattern)

        assert result is not None
        assert result.zone == "test.example."
        assert result.serial >= 2024010101
        assert len(result.rrsets) >= 1
        # All results should match the pattern
        for rrset in result.rrsets:
            assert pattern.search(rrset.name)

    def test_search_by_type(self, bind_server: BindContainer, dns_env: dict):
        """Test searching by record type."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cache.refresh_zone("test.example.")

        # Search for all A records (need at least a name or value pattern)
        pattern = re.compile(r".*")  # Match any name
        result = cache.search_zone("test.example.", name_pattern=pattern, rdtype="A")

        assert result is not None
        assert len(result.rrsets) >= 1
        # All results should be A records
        for rrset in result.rrsets:
            assert rrset.rdtype == "A"

    def test_search_by_value_pattern(self, bind_server: BindContainer, dns_env: dict):
        """Test searching by value pattern."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cache.refresh_zone("test.example.")

        # Search for records with values starting with "192.0.2"
        value_pattern = re.compile(r"^192\.0\.2\.")
        result = cache.search_zone("test.example.", value_pattern=value_pattern)

        assert result is not None
        assert len(result.rrsets) >= 1
        # All results should have matching values
        for rrset in result.rrsets:
            assert any(value_pattern.search(r) for r in rrset.records)

    def test_search_combined_filters(self, bind_server: BindContainer, dns_env: dict):
        """Test searching with combined name, type, and value filters."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cache.refresh_zone("test.example.")

        # Search for A records with names containing "mail" and IP starting with 192
        name_pattern = re.compile(r"mail", re.IGNORECASE)
        value_pattern = re.compile(r"^192\.")
        result = cache.search_zone(
            "test.example.",
            name_pattern=name_pattern,
            rdtype="A",
            value_pattern=value_pattern,
        )

        assert result is not None
        # Results should match all criteria
        for rrset in result.rrsets:
            assert name_pattern.search(rrset.name)
            assert rrset.rdtype == "A"
            assert any(value_pattern.search(r) for r in rrset.records)

    def test_search_no_matches(self, bind_server: BindContainer, dns_env: dict):
        """Test searching with no matches."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        cache.refresh_zone("test.example.")

        # Search for non-existent pattern
        pattern = re.compile(r"^nonexistent-xyz-123\.")
        result = cache.search_zone("test.example.", name_pattern=pattern)

        assert result is not None
        assert result.zone == "test.example."
        assert len(result.rrsets) == 0

    def test_search_nonexistent_zone(self, bind_server: BindContainer, dns_env: dict):
        """Test searching in a non-existent zone."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        pattern = re.compile(r".*")
        result = cache.search_zone("nonexistent.zone.", name_pattern=pattern)

        assert result is None


@pytest.mark.integration
class TestSearchAPIEndpoints:
    """Integration tests for search API endpoints."""

    def test_zone_search_by_name(self, test_client: TestClient, zone_name: str):
        """Test per-zone search by name pattern."""
        # First refresh the zone to ensure it's cached
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Search for names starting with "www"
        response = test_client.get(
            f"/v1/zones/{zone_name}/search",
            params={"name_pattern": "^www\\."},
        )

        assert response.status_code == 200
        data = response.json()
        assert "zone" in data
        assert "serial" in data
        assert "results" in data
        assert "total_count" in data
        assert data["zone"] == zone_name

    def test_zone_search_by_type(self, test_client: TestClient, zone_name: str):
        """Test per-zone search filtered by type."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(
            f"/v1/zones/{zone_name}/search",
            params={"name_pattern": ".*", "type": "MX"},
        )

        assert response.status_code == 200
        data = response.json()
        # All results should be MX records
        for result in data["results"]:
            assert result["type"] == "MX"

    def test_zone_search_by_value(self, test_client: TestClient, zone_name: str):
        """Test per-zone search by value pattern."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(
            f"/v1/zones/{zone_name}/search",
            params={"value_pattern": "^192\\.0\\.2\\."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] >= 1

    def test_zone_search_requires_pattern(self, test_client: TestClient, zone_name: str):
        """Test that search requires at least one pattern."""
        response = test_client.get(f"/v1/zones/{zone_name}/search")

        assert response.status_code == 400
        assert "at least one" in response.json()["detail"].lower()

    def test_zone_search_invalid_regex(self, test_client: TestClient, zone_name: str):
        """Test error handling for invalid regex."""
        response = test_client.get(
            f"/v1/zones/{zone_name}/search",
            params={"name_pattern": "[invalid(regex"},
        )

        assert response.status_code == 400
        assert "invalid regex" in response.json()["detail"].lower()

    def test_zone_search_nonexistent_zone(self, test_client: TestClient):
        """Test search on non-existent zone."""
        response = test_client.get(
            "/v1/zones/nonexistent.zone./search",
            params={"name_pattern": ".*"},
        )

        assert response.status_code == 404

    def test_global_search(self, test_client: TestClient, zone_name: str):
        """Test global search across all zones."""
        # Ensure zone is cached
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(
            "/v1/search",
            params={"name_pattern": ".*"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total_count" in data
        # Should have at least one zone with results
        if data["total_count"] > 0:
            assert len(data["results"]) >= 1
            # Each result should have zone, serial, and rrsets
            for zone_result in data["results"]:
                assert "zone" in zone_result
                assert "serial" in zone_result
                assert "rrsets" in zone_result

    def test_global_search_by_type(self, test_client: TestClient, zone_name: str):
        """Test global search filtered by type."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(
            "/v1/search",
            params={"name_pattern": ".*", "type": "A"},
        )

        assert response.status_code == 200
        data = response.json()
        # All results should be A records
        for zone_result in data["results"]:
            for rrset in zone_result["rrsets"]:
                assert rrset["type"] == "A"

    def test_global_search_requires_pattern(self, test_client: TestClient):
        """Test that global search requires at least one pattern."""
        response = test_client.get("/v1/search")

        assert response.status_code == 400

    def test_search_unauthenticated(self, test_client_with_auth: TestClient, zone_name: str):
        """Test that search requires authentication when auth is enabled."""
        response = test_client_with_auth.get(
            f"/v1/zones/{zone_name}/search",
            params={"name_pattern": ".*"},
        )

        # Should be 401 (Unauthorized)
        assert response.status_code == 401

    def test_global_search_unauthenticated(self, test_client_with_auth: TestClient):
        """Test that global search requires authentication when auth is enabled."""
        response = test_client_with_auth.get(
            "/v1/search",
            params={"name_pattern": ".*"},
        )

        assert response.status_code == 401
