"""Integration tests for API endpoints with real BIND server."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestHealthEndpoint:
    """Tests for health endpoint."""

    def test_health_check(self, test_client: TestClient):
        """Test health check endpoint."""
        response = test_client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "dns_server" in data
        assert "dns_connected" in data

    def test_root_endpoint(self, test_client: TestClient):
        """Test root endpoint."""
        response = test_client.get("/")
        assert response.status_code == 200

        data = response.json()
        assert "name" in data
        assert "version" in data
        assert data["docs"] == "/docs"


@pytest.mark.integration
class TestAuthenticationEndpoints:
    """Tests for authentication - uses auth-enabled client."""

    def test_unauthenticated_request_rejected(self, test_client_with_auth: TestClient):
        """Test that unauthenticated requests are rejected."""
        response = test_client_with_auth.get("/v1/zones")
        assert response.status_code == 401

    def test_invalid_api_key_rejected(self, test_client_with_auth: TestClient):
        """Test that invalid API key is rejected."""
        response = test_client_with_auth.get(
            "/v1/zones",
            headers={"X-API-Key": "invalid-key"},
        )
        assert response.status_code == 401

    def test_valid_api_key_accepted(self, test_client_with_auth: TestClient, auth_headers: dict):
        """Test that valid API key is accepted."""
        response = test_client_with_auth.get("/v1/zones", headers=auth_headers)
        assert response.status_code == 200


@pytest.mark.integration
class TestZoneEndpoints:
    """Tests for zone endpoints."""

    def test_list_zones_empty_initially(self, test_client: TestClient):
        """Test listing zones when cache is empty."""
        response = test_client.get("/v1/zones")
        assert response.status_code == 200

        data = response.json()
        assert "zones" in data
        # May be empty if no zones loaded yet

    def test_get_zone(self, test_client: TestClient, zone_name: str):
        """Test getting zone information."""
        response = test_client.get(f"/v1/zones/{zone_name}")
        assert response.status_code == 200

        data = response.json()
        assert data["zone"] == zone_name
        assert "serial" in data
        assert "rrset_count" in data

    def test_get_nonexistent_zone(self, test_client: TestClient):
        """Test getting non-existent zone."""
        response = test_client.get("/v1/zones/nonexistent.zone.")
        assert response.status_code == 404

    def test_refresh_zone(self, test_client: TestClient, zone_name: str):
        """Test refreshing zone cache."""
        response = test_client.post(f"/v1/zones/{zone_name}/refresh")
        assert response.status_code == 200

        data = response.json()
        assert data["zone"] == zone_name
        assert "last_refresh" in data

    def test_invalidate_zone_cache(self, test_client: TestClient, zone_name: str):
        """Test invalidating zone cache."""
        # First refresh to ensure zone is cached
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Then invalidate
        response = test_client.delete(f"/v1/zones/{zone_name}/cache")
        assert response.status_code == 204


@pytest.mark.integration
class TestRRsetListAndGet:
    """Tests for listing and getting RRsets."""

    def test_list_rrsets(self, test_client: TestClient, zone_name: str):
        """Test listing all RRsets in a zone."""
        # Refresh zone first
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/rrsets")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Check structure
        for rrset in data:
            assert "name" in rrset
            assert "type" in rrset
            assert "ttl" in rrset
            assert "records" in rrset

    def test_list_rrsets_filter_by_name(self, test_client: TestClient, zone_name: str):
        """Test listing RRsets filtered by name."""
        response = test_client.get(
            f"/v1/zones/{zone_name}/rrsets",
            params={"name": "www"},
        )
        assert response.status_code == 200

        data = response.json()
        for rrset in data:
            assert "www" in rrset["name"]

    def test_list_rrsets_filter_by_type(self, test_client: TestClient, zone_name: str):
        """Test listing RRsets filtered by type."""
        response = test_client.get(
            f"/v1/zones/{zone_name}/rrsets",
            params={"type": "A"},
        )
        assert response.status_code == 200

        data = response.json()
        for rrset in data:
            assert rrset["type"] == "A"

    def test_get_specific_rrset(self, test_client: TestClient, zone_name: str):
        """Test getting a specific RRset."""
        response = test_client.get(f"/v1/zones/{zone_name}/rrsets/www/A")
        assert response.status_code == 200

        data = response.json()
        assert "www" in data["name"]
        assert data["type"] == "A"
        assert "192.0.2.10" in data["records"]

    def test_get_nonexistent_rrset(self, test_client: TestClient, zone_name: str):
        """Test getting non-existent RRset."""
        response = test_client.get(f"/v1/zones/{zone_name}/rrsets/nonexistent/A")
        assert response.status_code == 404


@pytest.mark.integration
class TestAddRRset:
    """Tests for adding RRsets."""

    def test_add_a_record(self, test_client: TestClient, zone_name: str):
        """Test adding an A record."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-add",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.111"],
            },
        )
        assert response.status_code == 201

        data = response.json()
        assert data["success"]
        assert data["rrset"]["type"] == "A"
        assert "192.0.2.111" in data["rrset"]["records"]

    def test_add_aaaa_record(self, test_client: TestClient, zone_name: str):
        """Test adding an AAAA record."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-ipv6",
                "ttl": 3600,
                "type": "AAAA",
                "records": ["2001:db8::100"],
            },
        )
        assert response.status_code == 201

    def test_add_txt_record(self, test_client: TestClient, zone_name: str):
        """Test adding a TXT record."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-txt",
                "ttl": 3600,
                "type": "TXT",
                "records": ['"test=value"'],
            },
        )
        assert response.status_code == 201

    def test_add_mx_record(self, test_client: TestClient, zone_name: str):
        """Test adding an MX record."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-mx",
                "ttl": 3600,
                "type": "MX",
                "records": ["10 mail.test.example."],
            },
        )
        assert response.status_code == 201

    def test_add_cname_record(self, test_client: TestClient, zone_name: str):
        """Test adding a CNAME record."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-cname",
                "ttl": 3600,
                "type": "CNAME",
                "records": ["www.test.example."],
            },
        )
        assert response.status_code == 201

    def test_add_multiple_records(self, test_client: TestClient, zone_name: str):
        """Test adding multiple records in one RRset."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-multi",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.201", "192.0.2.202", "192.0.2.203"],
            },
        )
        assert response.status_code == 201

        data = response.json()
        assert len(data["rrset"]["records"]) == 3

    def test_add_duplicate_fails(self, test_client: TestClient, zone_name: str):
        """Test that adding duplicate RRset fails."""
        # www already exists in the zone
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "www",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.222"],
            },
        )
        assert response.status_code == 409

    def test_add_invalid_type_fails(self, test_client: TestClient, zone_name: str):
        """Test that adding invalid record type fails."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "test",
                "ttl": 3600,
                "type": "INVALID",
                "records": ["test"],
            },
        )
        assert response.status_code == 400  # Invalid record type


@pytest.mark.integration
class TestDeleteRRset:
    """Tests for deleting RRsets."""

    def test_delete_entire_rrset(self, test_client: TestClient, zone_name: str):
        """Test deleting an entire RRset."""
        # First create a record to delete
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-delete",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.150"],
            },
        )

        # Delete it
        response = test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-delete",
                "type": "A",
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"]

        # Verify it's gone
        get_response = test_client.get(f"/v1/zones/{zone_name}/rrsets/api-test-delete/A")
        assert get_response.status_code == 404

    def test_delete_specific_record(self, test_client: TestClient, zone_name: str):
        """Test deleting a specific record from an RRset."""
        # First create records
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-partial-delete",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.160", "192.0.2.161"],
            },
        )

        # Delete one record
        response = test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-partial-delete",
                "type": "A",
                "records": ["192.0.2.160"],
            },
        )
        assert response.status_code == 200

        # Verify only one remains
        get_response = test_client.get(f"/v1/zones/{zone_name}/rrsets/api-test-partial-delete/A")
        assert get_response.status_code == 200
        data = get_response.json()
        assert "192.0.2.161" in data["records"]
        assert "192.0.2.160" not in data["records"]

    def test_delete_nonexistent_fails(self, test_client: TestClient, zone_name: str):
        """Test that deleting non-existent RRset fails."""
        response = test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "nonexistent-record",
                "type": "A",
            },
        )
        assert response.status_code == 404


@pytest.mark.integration
class TestReplaceRRset:
    """Tests for replacing RRsets."""

    def test_replace_rrset(self, test_client: TestClient, zone_name: str):
        """Test replacing an RRset."""
        # First create a record
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-replace",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.170"],
            },
        )

        # Replace it
        response = test_client.put(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-replace",
                "ttl": 7200,
                "type": "A",
                "records": ["192.0.2.171", "192.0.2.172"],
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"]
        assert data["rrset"]["ttl"] == 7200
        assert "192.0.2.171" in data["rrset"]["records"]
        assert "192.0.2.172" in data["rrset"]["records"]

    def test_replace_nonexistent_fails(self, test_client: TestClient, zone_name: str):
        """Test that replacing non-existent RRset fails."""
        response = test_client.put(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "nonexistent-replace",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.180"],
            },
        )
        assert response.status_code == 404


@pytest.mark.integration
class TestConcurrencyAndConsistency:
    """Tests for concurrency and consistency guarantees."""

    def test_prereq_catches_concurrent_modification(
        self, test_client: TestClient, zone_name: str, dns_env: dict
    ):
        """Test that prerequisite checks catch concurrent modifications."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)

        # Create initial record via API
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-concurrent",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.190"],
            },
        )

        # Modify directly via DNS client (simulating concurrent modification)
        client.replace_rrset(
            zone="test.example.",
            name="api-test-concurrent",
            ttl=3600,
            rdtype="A",
            new_records=["192.0.2.191"],
            prereq_records=["192.0.2.190"],
        )

        # Try to replace via API with stale cache - should fail
        response = test_client.put(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "api-test-concurrent",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.192"],
            },
        )
        assert response.status_code == 409  # Conflict due to prereq failure


@pytest.mark.integration
class TestPagination:
    """Tests for pagination of RRset listings."""

    def test_list_rrsets_without_pagination_returns_all(
        self, test_client: TestClient, zone_name: str
    ):
        """Test that listing without pagination params returns all records."""
        response = test_client.get(f"/v1/zones/{zone_name}/rrsets")
        assert response.status_code == 200

        data = response.json()
        # Should be a simple list, not paginated response
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_rrsets_with_limit_returns_paginated(
        self, test_client: TestClient, zone_name: str
    ):
        """Test that listing with limit param returns paginated response."""
        response = test_client.get(f"/v1/zones/{zone_name}/rrsets?limit=2")
        assert response.status_code == 200

        data = response.json()
        # Should be paginated response
        assert "rrsets" in data
        assert "total_count" in data
        assert "has_more" in data
        assert "next_cursor" in data
        assert len(data["rrsets"]) <= 2

    def test_list_rrsets_pagination_cursor(self, test_client: TestClient, zone_name: str):
        """Test cursor-based pagination works correctly."""
        # Get first page with limit
        response1 = test_client.get(f"/v1/zones/{zone_name}/rrsets?limit=2")
        assert response1.status_code == 200
        data1 = response1.json()

        # If there are more records, use cursor to get next page
        if data1["has_more"] and data1["next_cursor"]:
            cursor = data1["next_cursor"]
            response2 = test_client.get(f"/v1/zones/{zone_name}/rrsets?limit=2&after={cursor}")
            assert response2.status_code == 200
            data2 = response2.json()

            # The cursor points to the first record of the next page
            # Cursor format is "name:type", so extract the name portion
            assert len(data2["rrsets"]) > 0
            cursor_name = cursor.rsplit(":", 1)[0] if ":" in cursor else cursor
            assert data2["rrsets"][0]["name"].lower() == cursor_name.lower()

    def test_list_rrsets_pagination_no_records_skipped(
        self, test_client: TestClient, zone_name: str
    ):
        """Test that navigating through all pages returns all records without skipping any.

        This is a regression test for a bug where clicking Next would show 0 records
        because the cursor comparison used > instead of >=.
        """
        # Get all records without pagination first
        response_all = test_client.get(f"/v1/zones/{zone_name}/rrsets")
        assert response_all.status_code == 200
        all_records = response_all.json()
        all_names = {r["name"] for r in all_records}
        total_expected = len(all_records)

        if total_expected < 3:
            pytest.skip("Need at least 3 records to test pagination properly")

        # Now paginate through with a small page size
        page_size = 2
        collected_names: set[str] = set()
        cursor: str | None = None
        pages_visited = 0
        max_pages = total_expected + 1  # Safety limit

        while pages_visited < max_pages:
            url = f"/v1/zones/{zone_name}/rrsets?limit={page_size}"
            if cursor:
                url += f"&after={cursor}"

            response = test_client.get(url)
            assert response.status_code == 200
            data = response.json()

            # Collect records from this page
            for rrset in data["rrsets"]:
                collected_names.add(rrset["name"])

            pages_visited += 1

            # Move to next page
            if data["has_more"] and data["next_cursor"]:
                cursor = data["next_cursor"]
            else:
                break

        # Verify we collected all records
        assert collected_names == all_names, (
            f"Pagination missed records! "
            f"Expected {len(all_names)} records, got {len(collected_names)}. "
            f"Missing: {all_names - collected_names}"
        )

    def test_list_rrsets_pagination_total_count(self, test_client: TestClient, zone_name: str):
        """Test that total_count reflects all records, not just current page."""
        # Get all records first
        response_all = test_client.get(f"/v1/zones/{zone_name}/rrsets")
        total_all = len(response_all.json())

        # Get paginated response
        response_paged = test_client.get(f"/v1/zones/{zone_name}/rrsets?limit=1")
        data_paged = response_paged.json()

        # total_count should match total number of records
        assert data_paged["total_count"] == total_all

    def test_list_rrsets_pagination_with_type_filter(self, test_client: TestClient, zone_name: str):
        """Test pagination works with type filter."""
        # Get only A records with limit
        response = test_client.get(f"/v1/zones/{zone_name}/rrsets?type=A&limit=1")
        assert response.status_code == 200

        data = response.json()
        assert "rrsets" in data
        # All returned records should be A type
        for rrset in data["rrsets"]:
            assert rrset["type"] == "A"

    def test_list_rrsets_cursor_past_end(self, test_client: TestClient, zone_name: str):
        """Test that cursor past all records returns empty page."""
        response = test_client.get(
            f"/v1/zones/{zone_name}/rrsets?limit=10&after=zzzzzzzzz.{zone_name}"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["rrsets"] == []
        assert data["has_more"] is False
        assert data["next_cursor"] is None


@pytest.mark.integration
class TestZonePagination:
    """Tests for pagination of zone listings."""

    def test_list_zones_without_pagination_returns_all(self, test_client: TestClient):
        """Test that listing without pagination params returns simple list."""
        response = test_client.get("/v1/zones")
        assert response.status_code == 200

        data = response.json()
        # Should be simple ZoneListResponse, not paginated
        assert "zones" in data
        # Should NOT have pagination fields
        assert "total_count" not in data
        assert "has_more" not in data

    def test_list_zones_with_limit_returns_paginated(self, test_client: TestClient, zone_name: str):
        """Test that listing with limit param returns paginated response."""
        # First ensure we have at least one zone
        test_client.get(f"/v1/zones/{zone_name}")

        response = test_client.get("/v1/zones?limit=10")
        assert response.status_code == 200

        data = response.json()
        # Should be paginated response
        assert "zones" in data
        assert "total_count" in data
        assert "has_more" in data
        assert "next_cursor" in data
        assert isinstance(data["zones"], list)

    def test_list_zones_pagination_cursor(self, test_client: TestClient, zone_name: str):
        """Test cursor-based pagination works correctly."""
        # First ensure we have at least one zone
        test_client.get(f"/v1/zones/{zone_name}")

        # Get first page with limit=1
        response1 = test_client.get("/v1/zones?limit=1")
        assert response1.status_code == 200
        data1 = response1.json()

        assert len(data1["zones"]) <= 1
        assert "total_count" in data1

        # If there are more zones, use cursor to get next page
        if data1["has_more"] and data1["next_cursor"]:
            cursor = data1["next_cursor"]
            response2 = test_client.get(f"/v1/zones?limit=1&after={cursor}")
            assert response2.status_code == 200
            data2 = response2.json()

            # The cursor points to the first zone of the next page
            # So the first zone should match the cursor exactly
            assert len(data2["zones"]) > 0
            assert data2["zones"][0]["zone"].lower() == cursor.lower()

    def test_list_zones_pagination_total_count(self, test_client: TestClient, zone_name: str):
        """Test that total_count reflects all zones, not just current page."""
        # First ensure we have at least one zone
        test_client.get(f"/v1/zones/{zone_name}")

        # Get all zones first
        response_all = test_client.get("/v1/zones")
        total_all = len(response_all.json()["zones"])

        # Get paginated response
        response_paged = test_client.get("/v1/zones?limit=1")
        data_paged = response_paged.json()

        # total_count should match total number of zones
        assert data_paged["total_count"] == total_all

    def test_list_zones_cursor_past_end(self, test_client: TestClient):
        """Test that cursor past all zones returns empty page."""
        response = test_client.get("/v1/zones?limit=10&after=zzzzzzzzz.test.")
        assert response.status_code == 200

        data = response.json()
        assert data["zones"] == []
        assert data["has_more"] is False
        assert data["next_cursor"] is None
