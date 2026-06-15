"""Integration tests for zone history and rollback endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestZoneHistoryEndpoint:
    """Tests for zone history endpoint."""

    def test_get_zone_history(self, test_client: TestClient, zone_name: str):
        """Test getting zone history."""
        # First refresh the zone to ensure it's loaded
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/history")
        assert response.status_code == 200

        data = response.json()
        assert data["zone"] == zone_name
        assert "current_serial" in data
        assert "history" in data
        assert "available_from_serial" in data
        assert "is_full_axfr" in data
        assert isinstance(data["history"], list)

    def test_get_zone_history_nonexistent_zone(self, test_client: TestClient):
        """Test getting history for non-existent zone returns 404."""
        response = test_client.get("/v1/zones/nonexistent.zone./history")
        assert response.status_code == 404

    def test_get_zone_history_with_from_serial(self, test_client: TestClient, zone_name: str):
        """Test getting zone history with from_serial parameter."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/history?from_serial=1")
        assert response.status_code == 200

        data = response.json()
        assert data["zone"] == zone_name

    def test_zone_history_has_batches_after_changes(self, test_client: TestClient, zone_name: str):
        """Test that zone history includes changes after DDNS updates.

        Note: This test may show is_full_axfr=True if BIND's journal doesn't
        have incremental history (e.g., if ixfr-from-differences is not enabled
        or if the journal was just created).
        """
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Get initial serial
        zone_response = test_client.get(f"/v1/zones/{zone_name}")
        initial_serial = zone_response.json()["serial"]

        # Make a change
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "history-test-record",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.200"],
            },
        )

        # Get history
        history_response = test_client.get(f"/v1/zones/{zone_name}/history")
        assert history_response.status_code == 200

        data = history_response.json()
        assert data["current_serial"] > initial_serial

        # Cleanup
        test_client.request(
            "DELETE",
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "history-test-record",
                "type": "A",
            },
        )


@pytest.mark.integration
class TestRollbackPreviewEndpoint:
    """Tests for rollback preview endpoint."""

    def test_rollback_preview(self, test_client: TestClient, zone_name: str):
        """Test rollback preview endpoint."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Get current serial
        zone_response = test_client.get(f"/v1/zones/{zone_name}")
        current_serial = zone_response.json()["serial"]

        # Try to preview rollback to serial 1 (likely unavailable, but tests endpoint)
        response = test_client.get(
            f"/v1/zones/{zone_name}/history/rollback/preview?target_serial=1"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["zone"] == zone_name
        assert data["current_serial"] == current_serial
        assert data["target_serial"] == 1
        assert "changes" in data
        assert "change_count" in data
        assert "can_rollback" in data
        # May or may not be able to rollback depending on history availability

    def test_rollback_preview_nonexistent_zone(self, test_client: TestClient):
        """Test rollback preview for non-existent zone returns 404."""
        response = test_client.get(
            "/v1/zones/nonexistent.zone./history/rollback/preview?target_serial=1"
        )
        assert response.status_code == 404

    def test_rollback_preview_future_serial(self, test_client: TestClient, zone_name: str):
        """Test rollback preview with future serial."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Get current serial
        zone_response = test_client.get(f"/v1/zones/{zone_name}")
        current_serial = zone_response.json()["serial"]

        # Try to preview rollback to a future serial (should fail)
        future_serial = current_serial + 100
        response = test_client.get(
            f"/v1/zones/{zone_name}/history/rollback/preview?target_serial={future_serial}"
        )
        assert response.status_code == 200

        data = response.json()
        assert data["can_rollback"] is False
        assert "warning" in data
        assert data["warning"] is not None


@pytest.mark.integration
class TestRollbackEndpoint:
    """Tests for rollback endpoint."""

    def test_rollback_future_serial_fails(self, test_client: TestClient, zone_name: str):
        """Test that rollback to future serial fails."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Get current serial
        zone_response = test_client.get(f"/v1/zones/{zone_name}")
        current_serial = zone_response.json()["serial"]

        # Try to rollback to a future serial (should fail)
        future_serial = current_serial + 100
        response = test_client.post(
            f"/v1/zones/{zone_name}/history/rollback",
            json={"target_serial": future_serial},
        )
        assert response.status_code == 400

    def test_rollback_nonexistent_zone(self, test_client: TestClient):
        """Test rollback for non-existent zone returns 404."""
        response = test_client.post(
            "/v1/zones/nonexistent.zone./history/rollback",
            json={"target_serial": 1},
        )
        assert response.status_code == 404

    def test_rollback_invalid_request(self, test_client: TestClient, zone_name: str):
        """Test rollback with invalid request returns 422."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/history/rollback",
            json={},  # Missing target_serial
        )
        assert response.status_code == 422

    def test_rollback_requires_positive_serial(self, test_client: TestClient, zone_name: str):
        """Test that rollback requires positive serial number."""
        response = test_client.post(
            f"/v1/zones/{zone_name}/history/rollback",
            json={"target_serial": 0},
        )
        assert response.status_code == 422  # Validation error

    def test_rollback_with_available_history(self, test_client: TestClient, zone_name: str):
        """Test rollback when history is available.

        This test creates a record, notes the serial, then makes another change,
        and attempts to rollback. The success depends on BIND's journal having
        the incremental history.
        """
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        # Create a test record
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "rollback-test-1",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.201"],
            },
        )

        # Get the serial after first change
        zone_response = test_client.get(f"/v1/zones/{zone_name}")
        serial_after_first_change = zone_response.json()["serial"]

        # Create another test record
        test_client.post(
            f"/v1/zones/{zone_name}/rrsets",
            json={
                "name": "rollback-test-2",
                "ttl": 3600,
                "type": "A",
                "records": ["192.0.2.202"],
            },
        )

        # Try to rollback to serial before second change
        # This may fail if BIND doesn't have incremental history
        response = test_client.post(
            f"/v1/zones/{zone_name}/history/rollback",
            json={"target_serial": serial_after_first_change},
        )

        # Could be 200 (success), 400 (history not available), or 409 (conflict)
        assert response.status_code in [200, 400, 409]

        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert "new_serial" in data
            assert "changes_applied" in data

        # Cleanup - delete any remaining test records
        for name in ["rollback-test-1", "rollback-test-2"]:
            test_client.request(
                "DELETE",
                f"/v1/zones/{zone_name}/rrsets",
                json={"name": name, "type": "A"},
            )


@pytest.mark.integration
class TestHistoryResponse:
    """Tests for history response structure."""

    def test_history_response_structure(self, test_client: TestClient, zone_name: str):
        """Test the structure of history response."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/history")
        assert response.status_code == 200

        data = response.json()

        # Check required fields
        assert "zone" in data
        assert "current_serial" in data
        assert "history" in data
        assert "available_from_serial" in data
        assert "is_full_axfr" in data

        # Check types
        assert isinstance(data["zone"], str)
        assert isinstance(data["current_serial"], int)
        assert isinstance(data["history"], list)
        assert isinstance(data["available_from_serial"], int)
        assert isinstance(data["is_full_axfr"], bool)

        # If there are history batches, check their structure
        if data["history"]:
            batch = data["history"][0]
            assert "from_serial" in batch
            assert "to_serial" in batch
            assert "changes" in batch
            assert isinstance(batch["from_serial"], int)
            assert isinstance(batch["to_serial"], int)
            assert isinstance(batch["changes"], list)

            # If there are changes, check their structure
            if batch["changes"]:
                change = batch["changes"][0]
                assert "action" in change
                assert change["action"] in ["add", "delete"]
                assert "name" in change
                assert "ttl" in change
                assert "type" in change
                assert "rdclass" in change
                assert "records" in change
                assert isinstance(change["records"], list)

    def test_rollback_preview_response_structure(self, test_client: TestClient, zone_name: str):
        """Test the structure of rollback preview response."""
        # First refresh the zone
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(
            f"/v1/zones/{zone_name}/history/rollback/preview?target_serial=1"
        )
        assert response.status_code == 200

        data = response.json()

        # Check required fields
        assert "zone" in data
        assert "current_serial" in data
        assert "target_serial" in data
        assert "changes" in data
        assert "change_count" in data
        assert "can_rollback" in data

        # Check types
        assert isinstance(data["zone"], str)
        assert isinstance(data["current_serial"], int)
        assert isinstance(data["target_serial"], int)
        assert isinstance(data["changes"], list)
        assert isinstance(data["change_count"], int)
        assert isinstance(data["can_rollback"], bool)

        # warning can be None or string
        if data["warning"] is not None:
            assert isinstance(data["warning"], str)
