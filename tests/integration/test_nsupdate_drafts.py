"""Integration tests for POST /v1/nsupdate/drafts."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestNSUpdateDrafts:
    """Save nsupdate text as draft scheduled changes."""

    def test_create_draft_from_nsupdate(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = f"nsupdate-draft-{id(self) % 100000}"
        text = f"""
zone {zone_name}
prereq nxdomain {host}.{zone_name}
update add {host}.{zone_name} 300 A 10.20.30.40
send
"""
        response = test_client.post(
            "/v1/nsupdate/drafts",
            content=text,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 1
        assert len(data["created"]) == 1
        change = data["created"][0]
        assert change["status"] == "draft"
        assert change["zone"] == zone_name if zone_name.endswith(".") else zone_name + "."
        assert change["auto_prerequisites"] is False
        assert len(change["operations"]) == 1
        assert change["operations"][0]["action"] == "add"
        assert change["operations"][0]["records"] == ["10.20.30.40"]
        assert len(change["prerequisites"]) == 1

        listed = test_client.get("/v1/scheduled-changes", params={"status": "draft"})
        assert listed.status_code == 200
        ids = [c["id"] for c in listed.json()["changes"]]
        assert change["id"] in ids

        test_client.delete(f"/v1/scheduled-changes/{change['id']}")

    def test_multi_send_creates_multiple_drafts(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        text = f"""
zone {zone_name}
update add multi-a.{zone_name} 300 A 10.20.30.41
send
update add multi-b.{zone_name} 300 A 10.20.30.42
send
"""
        response = test_client.post(
            "/v1/nsupdate/drafts",
            content=text,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert all(c["status"] == "draft" for c in data["created"])
        for c in data["created"]:
            test_client.delete(f"/v1/scheduled-changes/{c['id']}")

    def test_apply_now_after_draft(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        host = "nsupdate-apply-me"
        text = f"""
zone {zone_name}
update add {host}.{zone_name} 300 A 10.20.30.55
send
"""
        create = test_client.post(
            "/v1/nsupdate/drafts",
            content=text,
            headers={"Content-Type": "text/plain"},
        )
        assert create.status_code == 200
        change_id = create.json()["created"][0]["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200, apply.text
        assert apply.json()["status"] == "applied"

        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets",
            params={"name": f"{host}.{zone_name}", "type": "A"},
        )
        assert rrsets.status_code == 200
        records = rrsets.json()
        assert any("10.20.30.55" in str(r.get("records", [])) for r in records)

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": f"{host}.{zone_name}", "type": "A"},
        )

    def test_delete_without_type_rejected(self, test_client: TestClient, zone_name: str):
        text = f"""
zone {zone_name}
update delete gone.{zone_name}
send
"""
        response = test_client.post(
            "/v1/nsupdate/drafts",
            content=text,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 400
        assert "type" in response.json()["detail"].lower()

    def test_empty_body_rejected(self, test_client: TestClient):
        response = test_client.post(
            "/v1/nsupdate/drafts",
            content="   \n",
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 400
