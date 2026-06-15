"""Integration tests for the NSUPDATE endpoint."""

from collections.abc import AsyncGenerator

import pytest
from dns_zone_manager.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def nsupdate_app(dns_env: dict):
    """Create FastAPI app with DNS environment (auth disabled)."""
    app = create_app()
    return app


@pytest.fixture
def nsupdate_app_with_auth(dns_env_with_auth: dict):
    """Create FastAPI app with DNS environment and auth enabled."""
    app = create_app()
    return app


@pytest.fixture
async def nsupdate_client(nsupdate_app) -> AsyncGenerator[AsyncClient]:
    """Create async HTTP client for NSUPDATE tests (no auth required)."""
    async with AsyncClient(
        transport=ASGITransport(app=nsupdate_app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
async def nsupdate_client_with_auth(nsupdate_app_with_auth) -> AsyncGenerator[AsyncClient]:
    """Create async HTTP client for NSUPDATE tests (auth required)."""
    async with AsyncClient(
        transport=ASGITransport(app=nsupdate_app_with_auth),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.integration
class TestNSUpdateEndpoint:
    """Test the /nsupdate endpoint."""

    async def test_nsupdate_add_record(self, nsupdate_client: AsyncClient, bind_server):
        """Test adding a record via nsupdate."""
        zone = bind_server.zone_name
        nsupdate_text = f"""
zone {zone}
prereq nxdomain nsupdate-test.{zone}
update add nsupdate-test.{zone} 300 A 10.20.30.40
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_success"] == 1
        assert data["total_failed"] == 0
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["success"] is True

    async def test_nsupdate_with_zone_param(self, nsupdate_client: AsyncClient, bind_server):
        """Test using zone query parameter."""
        zone = bind_server.zone_name
        nsupdate_text = """
prereq nxdomain param-test.test.example.
update add param-test.test.example. 300 A 10.20.30.41
send
"""
        response = await nsupdate_client.post(
            f"/v1/nsupdate?zone={zone}",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_success"] == 1

    async def test_nsupdate_delete_record(self, nsupdate_client: AsyncClient, bind_server):
        """Test deleting a record via nsupdate."""
        zone = bind_server.zone_name

        # First add a record
        add_text = f"""
zone {zone}
update add delete-test.{zone} 300 A 10.20.30.42
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=add_text,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200

        # Now delete it
        delete_text = f"""
zone {zone}
prereq yxdomain delete-test.{zone}
update delete delete-test.{zone} A
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=delete_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_success"] == 1

    async def test_nsupdate_prereq_failure(self, nsupdate_client: AsyncClient, bind_server):
        """Test that prerequisite failures are reported."""
        zone = bind_server.zone_name

        # Try to add with prereq yxdomain for non-existent name
        nsupdate_text = f"""
zone {zone}
prereq yxdomain definitely-not-exists.{zone}
update add definitely-not-exists.{zone} 300 A 10.20.30.43
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_success"] == 0
        assert data["total_failed"] == 1
        assert data["transactions"][0]["success"] is False
        assert "Prerequisite" in data["transactions"][0]["message"]

    async def test_nsupdate_multiple_transactions(self, nsupdate_client: AsyncClient, bind_server):
        """Test multiple transactions in one request."""
        zone = bind_server.zone_name

        nsupdate_text = f"""
zone {zone}
update add multi-a.{zone} 300 A 10.20.30.44
send

update add multi-b.{zone} 300 A 10.20.30.45
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["transactions"]) == 2
        assert data["total_success"] == 2

    async def test_nsupdate_parse_error(self, nsupdate_client: AsyncClient, bind_server):
        """Test parse error handling."""
        nsupdate_text = """
zone test.example.
invalidcommand foo bar
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 400
        assert "Parse error" in response.json()["detail"]

    async def test_nsupdate_empty_body(self, nsupdate_client: AsyncClient, bind_server):
        """Test empty body handling."""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content="",
            headers={"Content-Type": "text/plain"},
        )

        # FastAPI returns 422 for validation errors (empty body fails Body() validation)
        assert response.status_code in (400, 422)

    async def test_nsupdate_no_zone(self, nsupdate_client: AsyncClient, bind_server):
        """Test error when no zone specified."""
        nsupdate_text = """
update add www 300 A 1.2.3.4
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 400
        assert "zone" in response.json()["detail"].lower()

    async def test_nsupdate_requires_auth(
        self, nsupdate_client_with_auth: AsyncClient, bind_server
    ):
        """Test that authentication is required when auth is enabled."""
        nsupdate_text = """
zone test.example.
update add www 300 A 1.2.3.4
send
"""
        response = await nsupdate_client_with_auth.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 401

    async def test_nsupdate_comments_ignored(self, nsupdate_client: AsyncClient, bind_server):
        """Test that comments are properly ignored."""
        zone = bind_server.zone_name

        nsupdate_text = f"""
; This is a comment
# This is also a comment
zone {zone}
; Another comment
update add comment-test.{zone} 300 A 10.20.30.46
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_success"] == 1

    async def test_nsupdate_server_key_ignored(self, nsupdate_client: AsyncClient, bind_server):
        """Test that server and key commands are ignored."""
        zone = bind_server.zone_name

        nsupdate_text = f"""
server wrong.server.com 5353
key wrong-key secret123
zone {zone}
update add ignored-cmds.{zone} 300 A 10.20.30.47
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        # Should succeed using API's configured server/key, not the ignored ones
        assert data["total_success"] == 1

    async def test_nsupdate_operations_in_response(self, nsupdate_client: AsyncClient, bind_server):
        """Test that operations are included in the response."""
        zone = bind_server.zone_name

        nsupdate_text = f"""
zone {zone}
prereq nxdomain ops-test.{zone}
update add ops-test.{zone} 300 A 10.20.30.48
send
"""
        response = await nsupdate_client.post(
            "/v1/nsupdate",
            content=nsupdate_text,
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 200
        data = response.json()
        ops = data["transactions"][0]["operations"]
        assert len(ops) == 2  # prereq + add

        # Check prereq
        assert ops[0]["action"] == "prereq_nxdomain"
        assert "ops-test" in ops[0]["name"]

        # Check add
        assert ops[1]["action"] == "add"
        assert ops[1]["ttl"] == 300
        assert ops[1]["rdtype"] == "A"
        assert ops[1]["data"] == "10.20.30.48"
