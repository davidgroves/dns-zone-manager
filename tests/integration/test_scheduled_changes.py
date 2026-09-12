"""Integration tests for scheduled change API endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestScheduledChangesAPI:
    def test_create_list_get_cancel(self, test_client: TestClient, zone_name: str):
        # Ensure zone is cached
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Add sched-www",
                "description": "Integration test change",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "sched-www",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.50"],
                    }
                ],
                "prerequisites": [
                    {
                        "prereq_type": "nxrrset",
                        "name": "sched-www",
                        "rdtype": "A",
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        data = create.json()
        assert data["status"] == "draft"
        assert data["name"] == "Add sched-www"
        assert len(data["operations"]) == 1
        assert len(data["prerequisites"]) == 1
        change_id = data["id"]

        listed = test_client.get("/v1/scheduled-changes")
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1

        got = test_client.get(f"/v1/scheduled-changes/{change_id}")
        assert got.status_code == 200
        assert got.json()["id"] == change_id

        cancelled = test_client.delete(f"/v1/scheduled-changes/{change_id}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

    def test_create_scheduled_with_time(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        resp = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Future change",
                "zone": zone_name,
                "scheduled_at": when,
                "operations": [
                    {
                        "action": "add",
                        "name": "future-host",
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.51"],
                    }
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "scheduled"
        assert data["scheduled_at"] is not None
        assert data["not_valid_after"] is not None

        # Cancel so it doesn't fire later
        test_client.delete(f"/v1/scheduled-changes/{data['id']}")

    def test_preview(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Preview me",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "preview-host",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.52"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201
        change_id = create.json()["id"]

        preview = test_client.post(f"/v1/scheduled-changes/{change_id}/preview")
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["change_id"] == change_id
        assert data["operations_count"] == 1
        assert data["all_prerequisites_passed"] is True

        test_client.delete(f"/v1/scheduled-changes/{change_id}")

    def test_apply_now(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"applynow-{datetime.now(UTC).strftime('%H%M%S%f')}"
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Apply now test",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.53"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        change_id = create.json()["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200, apply.text
        data = apply.json()
        assert data["success"] is True
        assert data["status"] == "applied"

        # The record is now live in the zone
        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets", params={"name": unique, "type": "A"}
        )
        assert rrsets.status_code == 200, rrsets.text
        assert [r["records"] for r in rrsets.json()] == [["192.0.2.53"]]

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )

    def test_revert_add_only_change(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"revert-add-{datetime.now(UTC).strftime('%H%M%S%f')}"
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Revert ADD",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.70"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        change_id = create.json()["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200, apply.text
        assert apply.json()["status"] == "applied"

        got = test_client.get(f"/v1/scheduled-changes/{change_id}")
        assert got.status_code == 200
        op = got.json()["operations"][0]
        assert op["snapshot_at"] is not None
        assert op["prior_records"] is None

        preview = test_client.get(f"/v1/scheduled-changes/{change_id}/revert-preview")
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["can_revert"] is True
        assert body["warning"]
        assert len(body["operations"]) == 1
        assert body["operations"][0]["action"] == "delete"
        assert body["operations"][0]["records"] == ["192.0.2.70"]

        revert = test_client.post(f"/v1/scheduled-changes/{change_id}/revert")
        assert revert.status_code == 200, revert.text
        assert revert.json()["success"] is True
        assert revert.json()["status"] == "reverted"

        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets", params={"name": unique, "type": "A"}
        )
        assert rrsets.status_code == 200
        assert rrsets.json() == [] or all(
            unique not in (r.get("name") or "") for r in rrsets.json()
        )

        # Double-revert must fail
        again = test_client.post(f"/v1/scheduled-changes/{change_id}/revert")
        assert again.status_code == 409

    def test_revert_delete_restores_prior_records(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"revert-del-{datetime.now(UTC).strftime('%H%M%S%f')}"

        # Seed a record, then schedule a delete of it
        add = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": unique,
                "type": "A",
                "ttl": 300,
                "records": ["192.0.2.71"],
            },
        )
        assert add.status_code in (200, 201), add.text

        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Revert DELETE",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "delete",
                        "name": unique,
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.71"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        change_id = create.json()["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200, apply.text

        got = test_client.get(f"/v1/scheduled-changes/{change_id}")
        op = got.json()["operations"][0]
        assert op["snapshot_at"] is not None
        assert op["prior_records"] == ["192.0.2.71"]
        assert op["prior_ttl"] == 300

        # Record should be gone
        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets", params={"name": unique, "type": "A"}
        )
        assert rrsets.json() == [] or not any(
            "192.0.2.71" in (r.get("records") or []) for r in rrsets.json()
        )

        preview = test_client.get(f"/v1/scheduled-changes/{change_id}/revert-preview")
        assert preview.status_code == 200
        assert preview.json()["operations"][0]["action"] == "add"
        assert preview.json()["operations"][0]["records"] == ["192.0.2.71"]

        revert = test_client.post(f"/v1/scheduled-changes/{change_id}/revert")
        assert revert.status_code == 200, revert.text
        assert revert.json()["status"] == "reverted"

        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets", params={"name": unique, "type": "A"}
        )
        assert rrsets.status_code == 200
        assert any(r.get("records") == ["192.0.2.71"] for r in rrsets.json()), rrsets.text

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )

    def test_revert_replace_restores_prior(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"revert-rep-{datetime.now(UTC).strftime('%H%M%S%f')}"

        add = test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": unique,
                "type": "A",
                "ttl": 600,
                "records": ["192.0.2.72"],
            },
        )
        assert add.status_code in (200, 201), add.text

        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Revert REPLACE",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "replace",
                        "name": unique,
                        "type": "A",
                        "ttl": 600,
                        "records": ["192.0.2.73"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201
        change_id = create.json()["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200, apply.text

        got = test_client.get(f"/v1/scheduled-changes/{change_id}")
        op = got.json()["operations"][0]
        assert op["prior_records"] == ["192.0.2.72"]

        preview = test_client.get(f"/v1/scheduled-changes/{change_id}/revert-preview")
        assert preview.status_code == 200
        actions = [o["action"] for o in preview.json()["operations"]]
        assert actions == ["delete", "add"]

        revert = test_client.post(f"/v1/scheduled-changes/{change_id}/revert")
        assert revert.status_code == 200, revert.text
        assert revert.json()["status"] == "reverted"

        rrsets = test_client.get(
            f"/v1/zones/{zone_name}/rrsets", params={"name": unique, "type": "A"}
        )
        assert any(r.get("records") == ["192.0.2.72"] for r in rrsets.json()), rrsets.text

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )

    def test_revert_preview_409_without_snapshots(self, test_client: TestClient, zone_name: str):
        """Pre-feature applied changes (no snapshot_at) cannot be reverted."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"revert-old-{datetime.now(UTC).strftime('%H%M%S%f')}"
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "No snapshot",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.74"],
                    }
                ],
            },
        )
        change_id = create.json()["id"]
        apply = test_client.post(f"/v1/scheduled-changes/{change_id}/apply")
        assert apply.status_code == 200

        # Clear snapshots to simulate a pre-feature applied change
        import sqlite3

        from dns_zone_manager.routers import scheduled as scheduled_router

        store = scheduled_router._store
        assert store is not None
        with sqlite3.connect(store.database_path) as db:
            db.execute(
                """
                UPDATE scheduled_operations
                SET snapshot_at = NULL, prior_ttl = NULL, prior_records = NULL
                WHERE change_id = ?
                """,
                (change_id,),
            )
            db.commit()

        preview = test_client.get(f"/v1/scheduled-changes/{change_id}/revert-preview")
        assert preview.status_code == 409

        revert = test_client.post(f"/v1/scheduled-changes/{change_id}/revert")
        assert revert.status_code == 409

        # Cleanup DNS
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )

    def test_patch_and_events(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Patch me",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "patch-host",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.54"],
                    }
                ],
            },
        )
        change_id = create.json()["id"]

        patched = test_client.patch(
            f"/v1/scheduled-changes/{change_id}",
            json={"name": "Patched name"},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Patched name"

        events = test_client.get(f"/v1/scheduled-changes/{change_id}/events")
        assert events.status_code == 200
        event_names = [e["event"] for e in events.json()]
        assert "created" in event_names
        assert "updated" in event_names

        updated = next(e for e in events.json() if e["event"] == "updated")
        assert updated["detail"]["changes"]["name"]["from"] == "Patch me"
        assert updated["detail"]["changes"]["name"]["to"] == "Patched name"

        created = next(e for e in events.json() if e["event"] == "created")
        assert created["detail"]["zone"] == zone_name
        assert created["detail"]["operations_count"] == 1

        # Cross-change audit list joins change metadata
        listed = test_client.get(
            "/v1/scheduled-changes/events",
            params={"change_id": change_id, "q": "Patched"},
        )
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] >= 1
        assert any(e["change_id"] == change_id for e in body["events"])
        assert any(e["change_name"] == "Patched name" for e in body["events"])
        assert any(e["event"] == "updated" for e in body["events"])

        by_event = test_client.get(
            "/v1/scheduled-changes/events",
            params=[("event", "created"), ("event", "updated"), ("zone", zone_name)],
        )
        assert by_event.status_code == 200
        assert all(e["zone"] == zone_name for e in by_event.json()["events"])
        assert {e["event"] for e in by_event.json()["events"]} <= {"created", "updated"}

        test_client.delete(f"/v1/scheduled-changes/{change_id}")

    def test_patch_full_edit_round_trip(self, test_client: TestClient, zone_name: str):
        """Edit every field the UI exposes, then revert the change to a draft."""
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Full edit",
                "description": "original notes",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "edit-one",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.61"],
                    },
                    {
                        "action": "add",
                        "name": "edit-two",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.62"],
                    },
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201
        change_id = create.json()["id"]

        scheduled_at = datetime.now(UTC) + timedelta(hours=3)
        not_valid_after = scheduled_at + timedelta(hours=2)
        patched = test_client.patch(
            f"/v1/scheduled-changes/{change_id}",
            json={
                "name": "Full edit v2",
                "description": None,
                "operations": [
                    {
                        "action": "delete",
                        "name": "edit-one",
                        "type": "A",
                        "ttl": 3600,
                        "records": None,
                    }
                ],
                "prerequisites": [{"prereq_type": "yxrrset", "name": "edit-one", "rdtype": "A"}],
                "scheduled_at": scheduled_at.isoformat(),
                "not_valid_after": not_valid_after.isoformat(),
                "auto_prerequisites": False,
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["name"] == "Full edit v2"
        assert body["description"] is None
        assert body["status"] == "scheduled"
        assert body["auto_prerequisites"] is False
        assert len(body["operations"]) == 1
        assert body["operations"][0]["action"] == "delete"
        assert len(body["prerequisites"]) == 1
        assert body["prerequisites"][0]["prereq_type"] == "yxrrset"

        # Clearing the schedule sends it back to a draft.
        reverted = test_client.patch(
            f"/v1/scheduled-changes/{change_id}",
            json={"scheduled_at": None, "not_valid_after": None},
        )
        assert reverted.status_code == 200
        assert reverted.json()["status"] == "draft"
        assert reverted.json()["scheduled_at"] is None

        test_client.delete(f"/v1/scheduled-changes/{change_id}")

    def test_patch_rejects_empty_operations(self, test_client: TestClient, zone_name: str):
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Keep operations",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "keep-host",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.63"],
                    }
                ],
            },
        )
        change_id = create.json()["id"]

        response = test_client.patch(
            f"/v1/scheduled-changes/{change_id}",
            json={"operations": []},
        )
        assert response.status_code == 422

        unchanged = test_client.get(f"/v1/scheduled-changes/{change_id}")
        assert len(unchanged.json()["operations"]) == 1

        test_client.delete(f"/v1/scheduled-changes/{change_id}")

    def test_patch_rejects_cancelled_change(self, test_client: TestClient, zone_name: str):
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Cancelled then edited",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": "cancelled-host",
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.64"],
                    }
                ],
            },
        )
        change_id = create.json()["id"]
        assert test_client.delete(f"/v1/scheduled-changes/{change_id}").status_code == 200

        response = test_client.patch(
            f"/v1/scheduled-changes/{change_id}",
            json={"name": "Too late"},
        )
        assert response.status_code == 409
