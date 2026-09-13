"""Integration tests for scheduled-change retention purge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import dns_zone_manager.main as app_main
import pytest
from dns_zone_manager.config import RetentionSettings, get_settings
from dns_zone_manager.scheduler.retention import run_retention
from dns_zone_manager.scheduler.schema import scheduled_changes
from dns_zone_manager.scheduler.store import ScheduledChangeStore
from fastapi.testclient import TestClient
from sqlalchemy import update


@pytest.mark.integration
class TestRetentionPurge:
    def test_age_purge_removes_applied_and_events_keeps_future(
        self, test_client: TestClient, zone_name: str
    ):
        store = app_main.scheduled_store
        assert store is not None
        assert store.database_path is not None
        db_path = store.database_path

        test_client.post(f"/v1/zones/{zone_name}/refresh")

        unique = f"ret-old-{datetime.now(UTC).strftime('%H%M%S%f')}"
        create = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Retention old applied",
                "zone": zone_name,
                "operations": [
                    {
                        "action": "add",
                        "name": unique,
                        "type": "A",
                        "ttl": 3600,
                        "records": ["192.0.2.90"],
                    }
                ],
                "auto_prerequisites": True,
            },
        )
        assert create.status_code == 201, create.text
        old_id = create.json()["id"]

        apply = test_client.post(f"/v1/scheduled-changes/{old_id}/apply")
        assert apply.status_code == 200, apply.text
        assert apply.json()["success"] is True

        when = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        future = test_client.post(
            "/v1/scheduled-changes",
            json={
                "name": "Retention future",
                "zone": zone_name,
                "scheduled_at": when,
                "operations": [
                    {
                        "action": "add",
                        "name": f"{unique}-future",
                        "type": "A",
                        "ttl": 300,
                        "records": ["192.0.2.91"],
                    }
                ],
            },
        )
        assert future.status_code == 201, future.text
        future_id = future.json()["id"]

        events = test_client.get("/v1/scheduled-changes/events", params={"change_id": old_id})
        assert events.status_code == 200, events.text
        assert events.json()["total"] >= 1

        old_ts = datetime.now(UTC) - timedelta(days=60)

        async def _backdate_and_purge() -> None:
            # Separate store on the same SQLite file — avoids sharing the
            # TestClient lifespan event loop.
            helper = ScheduledChangeStore(db_path, default_expiry_window=3600)
            await helper.open()
            try:
                async with helper._transaction("integration_backdate"):
                    await helper._conn().execute(
                        update(scheduled_changes)
                        .where(scheduled_changes.c.id == old_id)
                        .values(
                            applied_at=old_ts,
                            updated_at=old_ts,
                            created_at=old_ts,
                        )
                    )
                await run_retention(
                    helper,
                    RetentionSettings(
                        max_age_days=30,
                        max_database_mb=0,
                        vacuum="off",
                    ),
                )
            finally:
                await helper.close()

        asyncio.run(_backdate_and_purge())

        gone = test_client.get(f"/v1/scheduled-changes/{old_id}")
        assert gone.status_code == 404, gone.text

        events_after = test_client.get("/v1/scheduled-changes/events", params={"change_id": old_id})
        assert events_after.status_code == 200, events_after.text
        assert events_after.json()["total"] == 0

        still = test_client.get(f"/v1/scheduled-changes/{future_id}")
        assert still.status_code == 200, still.text
        assert still.json()["status"] == "scheduled"

        # Defaults still load with retention enabled in the running app.
        assert get_settings().retention.enabled is True

        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={"name": unique, "type": "A"},
        )
        test_client.delete(f"/v1/scheduled-changes/{future_id}")
