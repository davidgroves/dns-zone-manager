"""Integration tests for the atomic update endpoint and dual-writer races."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestAtomicUpdateEndpoint:
    """POST /v1/zones/{zone}/atomic — multi-op DDNS in one transaction."""

    def test_atomic_multi_op_success(self, test_client: TestClient, zone_name: str) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host_a = "atomic-ok-a"
        host_b = "atomic-ok-b"

        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {
                        "action": "add",
                        "name": host_a,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.70"],
                    },
                    {
                        "action": "add",
                        "name": host_b,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.71"],
                    },
                ]
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["operations_count"] == 2
        assert body["zone"].endswith(".")

        for name, ip in ((host_a, "192.0.2.70"), (host_b, "192.0.2.71")):
            got = test_client.get(f"/v1/zones/{zone_name}/rrsets/{name}/A")
            assert got.status_code == 200, got.text
            assert ip in got.json()["records"]

        for name in (host_a, host_b):
            test_client.request(
                "DELETE",
                f"/v1/zones/{zone_name}/rrsets",
                json={"name": name, "type": "A"},
            )

    def test_atomic_prereq_failure_leaves_dns_unchanged(
        self, test_client: TestClient, zone_name: str
    ) -> None:
        """If any op fails prereqs, none of the ops in the transaction apply."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        existing = "atomic-prereq-exists"
        new_host = "atomic-prereq-new"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": existing,
                "ttl": 300,
                "type": "A",
                "records": ["192.0.2.72"],
            },
        )
        assert seed.status_code == 201, seed.text

        # Second op tries to add a name that already exists → whole txn fails
        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {
                        "action": "add",
                        "name": new_host,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.73"],
                    },
                    {
                        "action": "add",
                        "name": existing,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.74"],
                    },
                ]
            },
        )
        assert response.status_code == 409, response.text

        # New host must not have been created
        missing = test_client.get(f"/v1/zones/{zone_name}/rrsets/{new_host}/A")
        assert missing.status_code == 404

        # Existing record unchanged
        still = test_client.get(f"/v1/zones/{zone_name}/rrsets/{existing}/A")
        assert still.status_code == 200
        assert still.json()["records"] == ["192.0.2.72"]

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": existing, "type": "A"},
        )

    def test_atomic_replace_and_add(self, test_client: TestClient, zone_name: str) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        replace_name = "atomic-replace"
        add_name = "atomic-add-with-replace"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": replace_name,
                "ttl": 300,
                "type": "A",
                "records": ["192.0.2.75"],
            },
        )
        assert seed.status_code == 201, seed.text

        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {
                        "action": "replace",
                        "name": replace_name,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.76"],
                    },
                    {
                        "action": "add",
                        "name": add_name,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.77"],
                    },
                ]
            },
        )
        assert response.status_code == 200, response.text

        replaced = test_client.get(f"/v1/zones/{zone_name}/rrsets/{replace_name}/A")
        assert replaced.json()["records"] == ["192.0.2.76"]
        added = test_client.get(f"/v1/zones/{zone_name}/rrsets/{add_name}/A")
        assert added.json()["records"] == ["192.0.2.77"]

        for name in (replace_name, add_name):
            test_client.request(
                "DELETE",
                f"/v1/zones/{zone_name}/rrsets",
                json={"name": name, "type": "A"},
            )


@pytest.mark.integration
class TestDualClientRace:
    """Two writers race: external DDNS vs API with stale cache → 409."""

    def test_stale_cache_replace_conflicts(
        self, test_client: TestClient, zone_name: str, dns_env: dict
    ) -> None:
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.client import DNSClient

        test_client.post(f"/v1/zones/{zone_name}/refresh")
        name = "dual-client-race"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": name,
                "ttl": 300,
                "type": "A",
                "records": ["192.0.2.80"],
            },
        )
        assert seed.status_code == 201, seed.text

        # External writer updates BIND out from under the API cache
        client = DNSClient(get_settings())
        client.replace_rrset(
            zone="test.example.",
            name=name,
            ttl=300,
            rdtype="A",
            new_records=["192.0.2.90"],
            prereq_records=["192.0.2.80"],
        )

        # API still believes the RRset is 192.0.2.80 → YXRRSET fails
        response = test_client.put(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": name,
                "ttl": 300,
                "type": "A",
                "records": ["192.0.2.81"],
            },
        )
        assert response.status_code == 409, response.text

        test_client.post(f"/v1/zones/{zone_name}/refresh")
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": name, "type": "A"},
        )
