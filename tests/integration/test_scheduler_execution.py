"""Integration tests for scheduler execution timing and expiry."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestSchedulerExecution:
    def test_due_change_is_applied_by_scheduler(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"sched-due-{datetime.now(UTC).strftime('%H%M%S%f')}"
        # Schedule 2 seconds in the past so the next poll picks it up
        when = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Scheduler due test",
                "zone": zone_name,
                "scheduled_at": when,
                "not_valid_after": expiry,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.60"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        change_id = create.json()["id"]
        # The change is already due, so the scheduler may have claimed it before
        # the create response was serialised — it must not be left as a draft.
        assert create.json()["status"] in ("scheduled", "running", "applied")

        # Wait for scheduler poll (poll_interval=1 in test config)
        applied = False
        for _ in range(20):
            time.sleep(0.5)
            got = test_client.get(f"/v1/scheduled-changes/{change_id}")
            assert got.status_code == 200
            if got.json()["status"] == "applied":
                applied = True
                break
            if got.json()["status"] in ("failed", "expired"):
                pytest.fail(f"Change ended in {got.json()['status']}: {got.json()}")

        assert applied, "Scheduler did not apply the due change in time"

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )

    def test_expired_change_does_not_apply(self, test_client: TestClient, zone_name: str):
        test_client.post(f"/v1/zones/{zone_name}/refresh")
        unique = f"sched-exp-{datetime.now(UTC).strftime('%H%M%S%f')}"
        when = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        expiry = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Should expire",
                "zone": zone_name,
                "scheduled_at": when,
                "not_valid_after": expiry,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.61"],
                    }
                ],
            },
        )
        assert create.status_code == 201
        change_id = create.json()["id"]

        expired = False
        for _ in range(15):
            time.sleep(0.5)
            got = test_client.get(f"/v1/scheduled-changes/{change_id}")
            if got.json()["status"] == "expired":
                expired = True
                break
            if got.json()["status"] == "applied":
                pytest.fail("Expired change was incorrectly applied")

        assert expired, "Change was not marked expired"
