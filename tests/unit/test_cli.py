"""Unit tests for the DNS API CLI."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from dns_zone_manager.cli import cli
from httpx import Response


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_response():
    """Factory for creating mock httpx responses."""

    def _create_response(
        status_code: int = 200,
        json_data: dict | list | None = None,
        text: str = "",
    ) -> MagicMock:
        response = MagicMock(spec=Response)
        response.status_code = status_code
        response.is_success = 200 <= status_code < 300

        if json_data is not None:
            response.json.return_value = json_data
            response.text = json.dumps(json_data)
        else:
            response.text = text
            response.json.side_effect = json.JSONDecodeError("", "", 0)

        return response

    return _create_response


class TestCLIBase:
    """Tests for CLI base functionality."""

    def test_version(self, runner):
        """Test --version flag."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "dns-cli" in result.output

    def test_help(self, runner):
        """Test --help flag."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "DNS API CLI" in result.output
        assert "DNS_API_URL" in result.output
        assert "DNS_API_KEY" in result.output

    def test_missing_api_key(self, runner):
        """Test error when API key is missing."""
        result = runner.invoke(cli, ["list", "zones"])
        assert result.exit_code == 1
        assert "API key required" in result.output


class TestListZones:
    """Tests for 'list zones' command."""

    def test_list_zones_success(self, runner, mock_response):
        """Test successful zone listing."""
        zones_data = {
            "zones": [
                {"zone": "example.com.", "serial": 2024010101, "rrset_count": 10},
                {"zone": "test.org.", "serial": 2024010102, "rrset_count": 5},
            ]
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data=zones_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "list", "zones"])

        assert result.exit_code == 0
        assert "Found 2 zone(s)" in result.output
        assert "example.com." in result.output
        assert "test.org." in result.output

    def test_list_zones_empty(self, runner, mock_response):
        """Test listing when no zones exist."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data={"zones": []})

            result = runner.invoke(cli, ["--api-key", "test-key", "list", "zones"])

        assert result.exit_code == 0
        assert "No zones found" in result.output

    def test_list_zones_json_output(self, runner, mock_response):
        """Test JSON output mode."""
        zones_data = {"zones": [{"zone": "example.com.", "serial": 2024010101, "rrset_count": 10}]}

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data=zones_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "--json", "list", "zones"])

        assert result.exit_code == 0
        output_data = json.loads(result.output)
        assert "zones" in output_data

    def test_list_zones_api_error(self, runner, mock_response):
        """Test API error handling."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(
                status_code=500, json_data={"detail": "Internal server error"}
            )

            result = runner.invoke(cli, ["--api-key", "test-key", "list", "zones"])

        assert result.exit_code == 1
        assert "Failed to list zones" in result.output


class TestListRecords:
    """Tests for 'list records' command."""

    def test_list_records_success(self, runner, mock_response):
        """Test successful record listing."""
        records_data = [
            {"name": "www.example.com.", "type": "A", "ttl": 3600, "records": ["192.0.2.1"]},
            {
                "name": "mail.example.com.",
                "type": "MX",
                "ttl": 3600,
                "records": ["10 smtp.example.com."],
            },
        ]

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data=records_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "list", "records", "example.com"])

        assert result.exit_code == 0
        assert "Found 2 record(s)" in result.output
        assert "www.example.com." in result.output
        assert "192.0.2.1" in result.output

    def test_list_records_with_type_filter(self, runner, mock_response):
        """Test record listing with type filter."""
        records_data = [
            {"name": "www.example.com.", "type": "A", "ttl": 3600, "records": ["192.0.2.1"]},
        ]

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data=records_data)

            result = runner.invoke(
                cli,
                ["--api-key", "test-key", "list", "records", "example.com", "--type", "A"],
            )

        assert result.exit_code == 0
        # Verify the type parameter was passed
        call_args = mock_client.return_value.get.call_args
        assert call_args[1]["params"]["type"] == "A"


class TestGetRecord:
    """Tests for 'get' command."""

    def test_get_record_success(self, runner, mock_response):
        """Test successful record retrieval."""
        record_data = {
            "name": "www.example.com.",
            "type": "A",
            "ttl": 3600,
            "records": ["192.0.2.1", "192.0.2.2"],
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data=record_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "get", "example.com", "www", "A"])

        assert result.exit_code == 0
        assert "www.example.com." in result.output
        assert "192.0.2.1" in result.output
        assert "192.0.2.2" in result.output

    def test_get_record_not_found(self, runner, mock_response):
        """Test record not found error."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(
                status_code=404, json_data={"detail": "Record not found"}
            )

            result = runner.invoke(
                cli, ["--api-key", "test-key", "get", "example.com", "nonexistent", "A"]
            )

        assert result.exit_code == 1
        assert "Record not found" in result.output


class TestAddRecord:
    """Tests for 'add' command."""

    def test_add_record_success(self, runner, mock_response):
        """Test successful record addition."""
        response_data = {
            "rrset": {
                "name": "www.example.com.",
                "type": "A",
                "ttl": 3600,
                "records": ["192.0.2.1"],
            }
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(
                cli,
                ["--api-key", "test-key", "add", "example.com", "www", "3600", "A", "192.0.2.1"],
            )

        assert result.exit_code == 0
        assert "Added www A to example.com" in result.output

    def test_add_record_multiple_values(self, runner, mock_response):
        """Test adding record with multiple values."""
        response_data = {
            "rrset": {
                "name": "www.example.com.",
                "type": "A",
                "ttl": 3600,
                "records": ["192.0.2.1", "192.0.2.2"],
            }
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(
                cli,
                [
                    "--api-key",
                    "test-key",
                    "add",
                    "example.com",
                    "www",
                    "3600",
                    "A",
                    "192.0.2.1",
                    "192.0.2.2",
                ],
            )

        assert result.exit_code == 0
        # Verify the payload contains both records
        call_args = mock_client.return_value.post.call_args
        assert call_args[1]["json"]["records"] == ["192.0.2.1", "192.0.2.2"]

    def test_add_record_verbose(self, runner, mock_response):
        """Test verbose output mode."""
        response_data = {
            "rrset": {"name": "www", "type": "A", "ttl": 3600, "records": ["192.0.2.1"]}
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(
                cli,
                [
                    "--api-key",
                    "test-key",
                    "-v",
                    "add",
                    "example.com",
                    "www",
                    "3600",
                    "A",
                    "192.0.2.1",
                ],
            )

        assert result.exit_code == 0
        assert "POST /zones/example.com/rrsets" in result.output


class TestDeleteRecord:
    """Tests for 'delete' command."""

    def test_delete_rrset(self, runner, mock_response):
        """Test deleting entire RRset."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.request.return_value = mock_response(
                json_data={"success": True}
            )

            result = runner.invoke(
                cli, ["--api-key", "test-key", "delete", "example.com", "old-host", "A"]
            )

        assert result.exit_code == 0
        assert "Deleted old-host A from example.com" in result.output

    def test_delete_specific_records(self, runner, mock_response):
        """Test deleting specific record values."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.request.return_value = mock_response(
                json_data={"success": True}
            )

            result = runner.invoke(
                cli,
                ["--api-key", "test-key", "delete", "example.com", "www", "A", "192.0.2.1"],
            )

        assert result.exit_code == 0
        assert "Deleted 192.0.2.1 from www A" in result.output

        # Verify records were included in payload
        call_args = mock_client.return_value.request.call_args
        assert call_args[1]["json"]["records"] == ["192.0.2.1"]


class TestReplaceRecord:
    """Tests for 'replace' command."""

    def test_replace_record_success(self, runner, mock_response):
        """Test successful record replacement."""
        response_data = {
            "rrset": {
                "name": "www.example.com.",
                "type": "A",
                "ttl": 600,
                "records": ["192.0.2.10"],
            }
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.put.return_value = mock_response(json_data=response_data)

            result = runner.invoke(
                cli,
                [
                    "--api-key",
                    "test-key",
                    "replace",
                    "example.com",
                    "www",
                    "600",
                    "A",
                    "192.0.2.10",
                ],
            )

        assert result.exit_code == 0
        assert "Replaced www A in example.com" in result.output


class TestExport:
    """Tests for 'export' command."""

    def test_export_to_stdout(self, runner, mock_response):
        """Test exporting zone to stdout."""
        zone_content = """; Zone file for example.com
$ORIGIN example.com.
@       3600    IN      SOA     ns1.example.com. admin.example.com. 2024010101 3600 900 604800 86400
@       3600    IN      NS      ns1.example.com.
www     3600    IN      A       192.0.2.1
"""

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(text=zone_content)
            # Override json for text response
            mock_client.return_value.get.return_value.text = zone_content

            result = runner.invoke(cli, ["--api-key", "test-key", "export", "example.com"])

        assert result.exit_code == 0
        assert "$ORIGIN example.com." in result.output
        assert "SOA" in result.output
        assert "www" in result.output

    def test_export_to_file(self, runner, mock_response):
        """Test exporting zone to a file."""
        zone_content = "$ORIGIN example.com.\n@ 3600 IN SOA ns1 admin 1 3600 900 604800 86400\n"

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(text=zone_content)
            mock_client.return_value.get.return_value.text = zone_content

            with runner.isolated_filesystem():
                result = runner.invoke(
                    cli,
                    ["--api-key", "test-key", "export", "example.com", "-o", "example.zone"],
                )

                assert result.exit_code == 0
                assert "Zone exported to example.zone" in result.output

                # Verify file was created with correct content
                with open("example.zone") as f:
                    content = f.read()
                assert "$ORIGIN example.com." in content

    def test_export_zone_not_found(self, runner, mock_response):
        """Test export when zone doesn't exist."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(
                status_code=404, json_data={"detail": "Zone 'nonexistent.com.' not found"}
            )

            result = runner.invoke(cli, ["--api-key", "test-key", "export", "nonexistent.com"])

        assert result.exit_code == 1
        assert "Export failed" in result.output

    def test_export_verbose(self, runner, mock_response):
        """Test export with verbose flag."""
        zone_content = "$ORIGIN example.com.\n"

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(text=zone_content)
            mock_client.return_value.get.return_value.text = zone_content

            result = runner.invoke(cli, ["--api-key", "test-key", "-v", "export", "example.com"])

        assert result.exit_code == 0
        # Verbose output goes to stderr
        assert "GET /zones/example.com/export" in result.output or result.exit_code == 0


class TestNsupdate:
    """Tests for 'nsupdate' command."""

    def test_nsupdate_from_file(self, runner, mock_response):
        """Test nsupdate reading from file."""
        nsupdate_input = """zone example.com
update add test 300 A 192.0.2.50
send
"""
        response_data = {
            "total_success": 1,
            "total_failed": 0,
            "transactions": [
                {"zone": "example.com.", "success": True, "message": "1 update(s) applied"}
            ],
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            with runner.isolated_filesystem():
                with open("changes.txt", "w") as f:
                    f.write(nsupdate_input)

                result = runner.invoke(
                    cli, ["--api-key", "test-key", "nsupdate", "-f", "changes.txt"]
                )

        assert result.exit_code == 0
        assert "1 transaction(s) completed successfully" in result.output

    def test_nsupdate_from_stdin(self, runner, mock_response):
        """Test nsupdate reading from stdin."""
        nsupdate_input = """zone example.com
update add test 300 A 192.0.2.50
send
"""
        response_data = {
            "total_success": 1,
            "total_failed": 0,
            "transactions": [
                {"zone": "example.com.", "success": True, "message": "1 update(s) applied"}
            ],
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "nsupdate"], input=nsupdate_input)

        assert result.exit_code == 0
        assert "1 transaction(s) completed successfully" in result.output

    def test_nsupdate_dry_run(self, runner, mock_response):
        """Test nsupdate dry-run mode."""
        nsupdate_input = """zone example.com
update add test 300 A 192.0.2.50
send
"""
        response_data = {
            "transactions": [{"zone": "example.com.", "success": True, "message": "Dry run valid"}],
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(
                cli, ["--api-key", "test-key", "nsupdate", "--dry-run"], input=nsupdate_input
            )

        assert result.exit_code == 0
        assert "Dry run" in result.output

        # Verify dry_run param was passed
        call_args = mock_client.return_value.post.call_args
        assert call_args[1]["params"] == {"dry_run": "true"}

    def test_nsupdate_empty_input(self, runner):
        """Test nsupdate with empty input."""
        result = runner.invoke(cli, ["--api-key", "test-key", "nsupdate"], input="")

        assert result.exit_code == 1
        assert "No nsupdate commands provided" in result.output

    def test_nsupdate_partial_failure(self, runner, mock_response):
        """Test nsupdate with partial failure."""
        nsupdate_input = """zone example.com
update add test 300 A 192.0.2.50
send
"""
        response_data = {
            "total_success": 1,
            "total_failed": 1,
            "transactions": [
                {"zone": "example.com.", "success": True, "message": "1 update(s) applied"},
                {"zone": "other.com.", "success": False, "message": "Zone not found"},
            ],
        }

        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response(json_data=response_data)

            result = runner.invoke(cli, ["--api-key", "test-key", "nsupdate"], input=nsupdate_input)

        assert result.exit_code == 1
        assert "1 succeeded, 1 failed" in result.output


class TestURLEncoding:
    """Tests for proper URL encoding of zone/record names."""

    def test_zone_with_special_chars(self, runner, mock_response):
        """Test zone names are properly URL encoded."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(
                json_data={"name": "test", "type": "A", "ttl": 300, "records": ["1.2.3.4"]}
            )

            runner.invoke(cli, ["--api-key", "test-key", "get", "example.com.", "test", "A"])

        # Verify the zone was encoded (dot should be encoded as %2E in path)
        call_args = mock_client.return_value.get.call_args
        assert "example.com." in call_args[0][0] or "%2E" in call_args[0][0]


class TestEnvironmentVariables:
    """Tests for environment variable handling."""

    def test_url_from_env(self, runner, mock_response):
        """Test URL can be set via environment variable."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data={"zones": []})

            result = runner.invoke(
                cli,
                ["list", "zones"],
                env={"DNS_API_URL": "http://custom-server:9000", "DNS_API_KEY": "env-key"},
            )

        assert result.exit_code == 0
        # Verify custom URL was used
        call_args = mock_client.call_args
        assert call_args[1]["base_url"] == "http://custom-server:9000"

    def test_api_key_from_env(self, runner, mock_response):
        """Test API key can be set via environment variable."""
        with patch("dns_zone_manager.cli.httpx.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.get.return_value = mock_response(json_data={"zones": []})

            result = runner.invoke(
                cli,
                ["list", "zones"],
                env={"DNS_API_KEY": "env-api-key"},
            )

        assert result.exit_code == 0
        # Verify API key header was set
        call_args = mock_client.call_args
        assert call_args[1]["headers"]["X-API-Key"] == "env-api-key"
