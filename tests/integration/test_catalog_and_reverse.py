"""Integration tests for catalog status and reverse-PTR HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestCatalogEndpoints:
    """Catalog router — disabled by default in integration fixtures."""

    def test_catalog_status_disabled(self, test_client: TestClient) -> None:
        response = test_client.get("/v1/catalog/status")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["enabled"] is False
        assert data["connected"] is False
        assert data["zones_discovered"] == 0
        assert data["zone_name"] is None

    def test_catalog_zones_when_disabled(self, test_client: TestClient) -> None:
        response = test_client.get("/v1/catalog/zones")
        # Disabled catalog should still respond with empty list or 200
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 0
        assert data["zones"] == []

    def test_catalog_sync_when_disabled(self, test_client: TestClient) -> None:
        response = test_client.post("/v1/catalog/sync")
        assert response.status_code == 400, response.text
        assert "not enabled" in response.json()["detail"].lower()


@pytest.mark.integration
class TestReversePtrEndpoints:
    """Reverse PTR check/create against BIND (with reverse zone when present)."""

    def test_check_unmanaged_reverse_zone(self, test_client: TestClient) -> None:
        """IPs outside managed reverse zones report can_create=false."""
        response = test_client.post(
            "/v1/reverse-ptr/check",
            json={
                "source_name": "www.test.example.",
                "source_type": "A",
                "records": ["203.0.113.10"],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["ip"] == "203.0.113.10"
        assert result["zone_managed"] is False
        assert result["can_create"] is False
        assert "in-addr.arpa" in result["ptr_fqdn"]

    def test_check_invalid_ip(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/v1/reverse-ptr/check",
            json={
                "source_name": "www.test.example.",
                "source_type": "A",
                "records": ["not-an-ip"],
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["can_create"] is False
        assert result["error"]

    def test_check_and_create_managed_reverse(
        self, test_client: TestClient, zone_name: str
    ) -> None:
        """When BIND has 2.0.192.in-addr.arpa, create a PTR for 192.0.2.x."""
        refresh = test_client.post("/v1/zones/2.0.192.in-addr.arpa./refresh")
        if refresh.status_code != 200:
            pytest.skip("Reverse zone not available in BIND test container")

        check = test_client.post(
            "/v1/reverse-ptr/check",
            json={
                "source_name": "ptr-src.test.example.",
                "source_type": "A",
                "records": ["192.0.2.99"],
            },
        )
        assert check.status_code == 200, check.text
        result = check.json()["results"][0]
        if not result["zone_managed"]:
            pytest.skip("Reverse zone not managed by this test BIND")

        assert result["can_create"] is True
        assert result["reverse_zone"] is not None

        create = test_client.post(
            "/v1/reverse-ptr",
            json={
                "ptr_target": "ptr-src.test.example.",
                "ttl": 300,
                "ips": ["192.0.2.99"],
                "mode": "replace",
            },
        )
        assert create.status_code == 200, create.text
        created = create.json()
        assert created["created_count"] + created["skipped_count"] >= 1
        assert any(r["status"] in ("created", "replaced", "skipped") for r in created["results"])

        # Cleanup PTR if created
        if result.get("record_name") and result.get("reverse_zone"):
            test_client.request(
                "DELETE",
                f"/v1/zones/{result['reverse_zone']}/rrsets",
                json={"name": result["record_name"], "type": "PTR"},
            )
