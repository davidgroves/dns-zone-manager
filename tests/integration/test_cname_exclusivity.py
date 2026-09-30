"""Integration tests for CNAME vs other-type exclusivity."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestCnameExclusivityRest:
    """REST add rejects CNAME/other-type conflicts (prereq + cache)."""

    def test_add_cname_while_a_exists(self, test_client: TestClient, zone_name: str) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "cname-excl-rest-a"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "ttl": 300, "type": "A", "records": ["192.0.2.80"]},
        )
        assert seed.status_code == 201, seed.text

        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": host,
                "ttl": 300,
                "type": "CNAME",
                "records": [f"target.{zone_name}"],
            },
        )
        assert response.status_code == 409, response.text
        assert "CNAME" in response.json()["detail"]

        still = test_client.get(f"/v1/zones/{zone_name}/rrsets/{host}/A")
        assert still.status_code == 200
        assert "192.0.2.80" in still.json()["records"]

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "type": "A"},
        )

    def test_add_a_while_cname_exists(self, test_client: TestClient, zone_name: str) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "cname-excl-rest-b"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": host,
                "ttl": 300,
                "type": "CNAME",
                "records": [f"target.{zone_name}"],
            },
        )
        assert seed.status_code == 201, seed.text

        response = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "ttl": 300, "type": "A", "records": ["192.0.2.81"]},
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "CNAME" in detail

        still = test_client.get(f"/v1/zones/{zone_name}/rrsets/{host}/CNAME")
        assert still.status_code == 200

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "type": "CNAME"},
        )


@pytest.mark.integration
class TestCnameExclusivityAtomic:
    """Atomic multi-op CNAME exclusivity."""

    def test_atomic_add_cname_with_existing_a_rejected(
        self, test_client: TestClient, zone_name: str
    ) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "cname-excl-atomic-a"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "ttl": 300, "type": "A", "records": ["192.0.2.82"]},
        )
        assert seed.status_code == 201, seed.text

        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {
                        "action": "add",
                        "name": host,
                        "type": "CNAME",
                        "ttl": 300,
                        "records": [f"target.{zone_name}"],
                    }
                ]
            },
        )
        assert response.status_code == 409, response.text
        assert "CNAME" in response.json()["detail"]

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "type": "A"},
        )

    def test_atomic_delete_a_then_add_cname(self, test_client: TestClient, zone_name: str) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "cname-excl-atomic-b"
        target = f"target.{zone_name}"

        seed = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "ttl": 300, "type": "A", "records": ["192.0.2.83"]},
        )
        assert seed.status_code == 201, seed.text

        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {"action": "delete", "name": host, "type": "A"},
                    {
                        "action": "add",
                        "name": host,
                        "type": "CNAME",
                        "ttl": 300,
                        "records": [target],
                    },
                ]
            },
        )
        assert response.status_code == 200, response.text

        got = test_client.get(f"/v1/zones/{zone_name}/rrsets/{host}/CNAME")
        assert got.status_code == 200
        assert target in got.json()["records"]

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": host, "type": "CNAME"},
        )

    def test_atomic_add_a_and_cname_same_txn_rejected(
        self, test_client: TestClient, zone_name: str
    ) -> None:
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "cname-excl-atomic-c"

        response = test_client.post(
            f"/v1/zones/{zone_name}/atomic",
            json={
                "operations": [
                    {
                        "action": "add",
                        "name": host,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.84"],
                    },
                    {
                        "action": "add",
                        "name": host,
                        "type": "CNAME",
                        "ttl": 300,
                        "records": [f"target.{zone_name}"],
                    },
                ]
            },
        )
        assert response.status_code == 409, response.text

        # Neither type should exist
        assert test_client.get(f"/v1/zones/{zone_name}/rrsets/{host}/A").status_code == 404
        assert test_client.get(f"/v1/zones/{zone_name}/rrsets/{host}/CNAME").status_code == 404
