"""Integration tests for zone export functionality."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def has_named_checkzone() -> bool:
    """Check if named-checkzone is available on the system."""
    return shutil.which("named-checkzone") is not None


@pytest.mark.integration
class TestZoneExport:
    """Tests for zone export endpoint."""

    def test_export_zone_returns_zone_content(self, test_client: TestClient, zone_name: str):
        """Test that export endpoint returns zone file content."""
        # First refresh the zone to ensure it's in cache
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/export")
        assert response.status_code == 200

        content = response.text
        # Zone file should start with $ORIGIN directive
        assert content.startswith("$ORIGIN ")
        assert zone_name in content.split("\n")[0]
        # Zone file should contain SOA record
        assert "SOA" in content
        # Zone file should contain NS records
        assert "NS" in content
        # Zone name should appear in the content
        assert "test.example" in content.lower() or "test.example." in content.lower()

    def test_export_zone_content_type(self, test_client: TestClient, zone_name: str):
        """Test that export endpoint returns correct content type."""
        # First refresh the zone to ensure it's in cache
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/export")
        assert response.status_code == 200
        assert "text/dns" in response.headers.get("content-type", "")

    def test_export_zone_content_disposition(self, test_client: TestClient, zone_name: str):
        """Test that export endpoint returns correct Content-Disposition header."""
        # First refresh the zone to ensure it's in cache
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/export")
        assert response.status_code == 200

        content_disposition = response.headers.get("content-disposition", "")
        assert "attachment" in content_disposition
        assert "test.example.zone" in content_disposition

    def test_export_nonexistent_zone_returns_404(self, test_client: TestClient):
        """Test that exporting non-existent zone returns 404."""
        response = test_client.get("/v1/zones/nonexistent.zone./export")
        assert response.status_code == 404

    def test_export_zone_contains_records(self, test_client: TestClient, zone_name: str):
        """Test that exported zone contains expected records."""
        # First refresh the zone to ensure it's in cache
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/export")
        assert response.status_code == 200

        content = response.text

        # Zone should contain A records (www record exists in test zone)
        assert " A " in content or "\tA\t" in content
        # Zone should contain TTL values
        assert "3600" in content or "IN" in content

    @pytest.mark.skipif(not has_named_checkzone(), reason="named-checkzone not available")
    def test_export_zone_validates_with_named_checkzone(
        self, test_client: TestClient, zone_name: str
    ):
        """Test that exported zone file is valid according to named-checkzone.

        This test is skipped if named-checkzone is not installed.
        Install with: apt install bind9-utils (Debian/Ubuntu) or brew install bind (macOS)
        """
        # First refresh the zone to ensure it's in cache
        test_client.post(f"/v1/zones/{zone_name}/refresh")

        response = test_client.get(f"/v1/zones/{zone_name}/export")
        assert response.status_code == 200

        content = response.text

        # Write zone content to a temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".zone", delete=False) as zone_file:
            zone_file.write(content)
            zone_file_path = Path(zone_file.name)

        try:
            # Run named-checkzone to validate the zone file
            # named-checkzone <zone_name> <zone_file>
            result = subprocess.run(
                ["named-checkzone", zone_name, str(zone_file_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # named-checkzone returns 0 on success
            assert result.returncode == 0, (
                f"named-checkzone failed with exit code {result.returncode}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}\n"
                f"Zone content:\n{content}"
            )
        finally:
            # Clean up temporary file
            zone_file_path.unlink(missing_ok=True)

    @pytest.mark.skipif(not has_named_checkzone(), reason="named-checkzone not available")
    def test_export_multiple_zones_validate(self, test_client: TestClient):
        """Test exporting and validating multiple zones.

        This test is skipped if named-checkzone is not installed.
        """
        # Get list of available zones
        response = test_client.get("/v1/zones")
        assert response.status_code == 200

        zones_data = response.json()
        zones = zones_data.get("zones", [])

        # Test at least 2 zones if available
        zones_to_test = zones[:2] if len(zones) >= 2 else zones

        for zone_info in zones_to_test:
            zone_name = zone_info["zone"]

            # Export the zone
            export_response = test_client.get(f"/v1/zones/{zone_name}/export")
            assert export_response.status_code == 200

            content = export_response.text

            # Write and validate
            with tempfile.NamedTemporaryFile(mode="w", suffix=".zone", delete=False) as zone_file:
                zone_file.write(content)
                zone_file_path = Path(zone_file.name)

            try:
                result = subprocess.run(
                    ["named-checkzone", zone_name, str(zone_file_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                assert result.returncode == 0, (
                    f"named-checkzone failed for {zone_name}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )
            finally:
                zone_file_path.unlink(missing_ok=True)
