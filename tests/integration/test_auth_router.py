"""Integration tests for auth tracking endpoints and validate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestAuthRouter:
    """POST /v1/auth/login|logout and GET /v1/auth/validate."""

    def test_login_logout_unauthenticated_ok(self, test_client: TestClient) -> None:
        """Login/logout are metrics hooks — no auth required by design."""
        login = test_client.post("/v1/auth/login", json={"auth_type": "api_key"})
        assert login.status_code == 200, login.text
        assert login.json()["status"] == "ok"

        logout = test_client.post("/v1/auth/logout")
        assert logout.status_code == 200, logout.text
        assert logout.json()["status"] == "ok"

    def test_validate_anonymous_when_auth_disabled(self, test_client: TestClient) -> None:
        response = test_client.get("/v1/auth/validate")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["valid"] is True
        assert data["auth_type"] == "none"
        assert data["user_id"] == "anonymous"

    def test_validate_requires_key_when_auth_enabled(
        self, test_client_with_auth: TestClient, auth_headers: dict[str, str]
    ) -> None:
        denied = test_client_with_auth.get("/v1/auth/validate")
        assert denied.status_code == 401

        ok = test_client_with_auth.get("/v1/auth/validate", headers=auth_headers)
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["valid"] is True
        assert data["auth_type"] == "api_key"
        assert data["user_id"] == "test"

    def test_login_invalid_auth_type(self, test_client: TestClient) -> None:
        response = test_client.post("/v1/auth/login", json={"auth_type": "bogus"})
        assert response.status_code == 422

    def test_login_rejects_azure_ad_auth_type(self, test_client: TestClient) -> None:
        response = test_client.post("/v1/auth/login", json={"auth_type": "azure_ad"})
        assert response.status_code == 422


@pytest.mark.integration
class TestProxyAuth:
    """Trusted reverse-proxy identity headers."""

    def test_validate_with_proxy_header(self, test_client_with_proxy: TestClient) -> None:
        denied = test_client_with_proxy.get("/v1/auth/validate")
        assert denied.status_code == 401

        ok = test_client_with_proxy.get(
            "/v1/auth/validate",
            headers={
                "X-Auth-Request-Email": "alice@example.com",
                "X-Auth-Request-Preferred-Username": "Alice",
            },
        )
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["valid"] is True
        assert data["auth_type"] == "proxy"
        assert data["user_id"] == "alice@example.com"
        assert data["name"] == "Alice"

    def test_zones_list_with_proxy_header(self, test_client_with_proxy: TestClient) -> None:
        response = test_client_with_proxy.get(
            "/v1/zones",
            headers={"X-Auth-Request-Email": "bob@example.com"},
        )
        assert response.status_code == 200, response.text

    def test_ui_config_exposes_proxy_user(self, test_client_with_proxy: TestClient) -> None:
        response = test_client_with_proxy.get(
            "/ui/config",
            headers={
                "X-Auth-Request-Email": "carol@example.com",
                "X-Auth-Request-Preferred-Username": "Carol",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["proxyAuthEnabled"] is True
        assert data["apiKeyEnabled"] is False
        assert "azureEnabled" not in data
        assert data["user"]["email"] == "carol@example.com"
        assert data["user"]["name"] == "Carol"
