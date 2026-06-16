"""Unit tests for authentication modules."""

from unittest.mock import patch

import pytest
from dns_zone_manager.auth.api_key import APIKeyUser, validate_api_key
from dns_zone_manager.auth.azure import AzureUser
from dns_zone_manager.auth.combined import AuthenticatedUser, get_current_user
from dns_zone_manager.auth.proxy import ProxyUser, proxy_user_from_request
from dns_zone_manager.config import ProxyAuthSettings
from fastapi import HTTPException


class _FakeRequest:
    """Minimal stand-in for a Starlette Request (only .headers is used)."""

    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def _proxy_settings(**kwargs):
    """Build a mock settings object exposing proxy_auth (and disabled others)."""
    return type(
        "MockSettings",
        (),
        {
            "proxy_auth": ProxyAuthSettings(enabled=True, **kwargs),
            "api_key": type("A", (), {"enabled": False})(),
            "azure_ad": type("Z", (), {"enabled": False})(),
        },
    )()


class TestAPIKeyUser:
    """Tests for APIKeyUser."""

    def test_api_key_user_creation(self):
        """Test creating APIKeyUser with named key."""
        user = APIKeyUser(key_name="admin")
        assert user.key_id == "admin"
        assert user.key_name == "admin"
        assert user.auth_type == "api_key"

    def test_api_key_user_repr(self):
        """Test APIKeyUser string representation."""
        user = APIKeyUser(key_name="test_key")
        assert "test_key" in repr(user)


class TestValidateAPIKey:
    """Tests for validate_api_key function.

    Uses the test config file from conftest.py which has:
    - api_key.enabled: true
    - api_key.keys: [{name: test, secret: test-api-key-12345}]
    """

    def test_validate_valid_key(self):
        """Test validating a valid API key."""
        # Uses test config: test:test-api-key-12345
        user = validate_api_key("test-api-key-12345")
        assert user is not None
        assert isinstance(user, APIKeyUser)
        assert user.key_name == "test"

    def test_validate_invalid_key(self):
        """Test validating an invalid API key."""
        with pytest.raises(HTTPException) as exc_info:
            validate_api_key("invalid-key")
        assert exc_info.value.status_code == 401

    def test_validate_no_key_returns_none(self):
        """Test that no key returns None."""
        result = validate_api_key(None)
        assert result is None

    def test_validate_disabled_returns_none(self):
        """Test that disabled API key auth returns None."""
        # Mock settings to have API key auth disabled
        from dns_zone_manager.config import APIKeySettings

        mock_settings = type(
            "MockSettings",
            (),
            {"api_key": APIKeySettings(enabled=False)},
        )()

        with patch("dns_zone_manager.auth.api_key.get_settings", return_value=mock_settings):
            result = validate_api_key("any-key")
            assert result is None


class TestAzureUser:
    """Tests for AzureUser."""

    def test_azure_user_creation(self):
        """Test creating AzureUser."""
        user = AzureUser(
            user_id="user-123",
            name="Test User",
            email="test@example.com",
            roles=["admin"],
        )
        assert user.user_id == "user-123"
        assert user.name == "Test User"
        assert user.email == "test@example.com"
        assert "admin" in user.roles

    def test_azure_user_from_claims(self):
        """Test creating AzureUser from JWT claims."""
        claims = {
            "oid": "object-id-123",
            "name": "John Doe",
            "preferred_username": "john@example.com",
            "roles": ["DNS.Admin"],
        }
        user = AzureUser.from_token_claims(claims)
        assert user.user_id == "object-id-123"
        assert user.name == "John Doe"
        assert user.email == "john@example.com"
        assert "DNS.Admin" in user.roles

    def test_azure_user_from_claims_minimal(self):
        """Test creating AzureUser from minimal claims."""
        claims = {"sub": "subject-123"}
        user = AzureUser.from_token_claims(claims)
        assert user.user_id == "subject-123"
        assert user.name is None
        assert user.roles == []


class TestAuthenticatedUser:
    """Tests for AuthenticatedUser."""

    def test_from_api_key(self):
        """Test creating AuthenticatedUser from API key user."""
        api_key_user = APIKeyUser(key_name="admin")
        auth_user = AuthenticatedUser.from_api_key(api_key_user)

        assert auth_user.user_id == "admin"
        assert auth_user.auth_type == "api_key"
        assert auth_user.name == "API Key: admin"
        assert auth_user.roles is not None
        assert "api_key_user" in auth_user.roles

    def test_from_azure(self):
        """Test creating AuthenticatedUser from Azure user."""
        azure_user = AzureUser(
            user_id="azure-123",
            name="Test User",
            email="test@example.com",
            roles=["admin"],
        )
        auth_user = AuthenticatedUser.from_azure(azure_user)

        assert auth_user.user_id == "azure-123"
        assert auth_user.auth_type == "azure_ad"
        assert auth_user.name == "Test User"
        assert auth_user.email == "test@example.com"

    def test_from_proxy(self):
        """Test creating AuthenticatedUser from a trusted-proxy user."""
        proxy_user = ProxyUser(email="alice@example.com", name="Alice")
        auth_user = AuthenticatedUser.from_proxy(proxy_user)

        assert auth_user.user_id == "alice@example.com"
        assert auth_user.auth_type == "proxy"
        assert auth_user.name == "Alice"
        assert auth_user.email == "alice@example.com"
        assert auth_user.roles is not None
        assert "proxy_user" in auth_user.roles


class TestProxyUser:
    """Tests for ProxyUser and proxy_user_from_request."""

    def test_proxy_user_defaults_name_to_email(self):
        user = ProxyUser(email="bob@example.com")
        assert user.user_id == "bob@example.com"
        assert user.email == "bob@example.com"
        assert user.name == "bob@example.com"
        assert user.auth_type == "proxy"

    def test_from_request_disabled_returns_none(self):
        mock_settings = type("M", (), {"proxy_auth": ProxyAuthSettings(enabled=False)})()
        with patch("dns_zone_manager.auth.proxy.get_settings", return_value=mock_settings):
            req = _FakeRequest({"X-Auth-Request-Email": "alice@example.com"})
            assert proxy_user_from_request(req) is None

    def test_from_request_missing_header_returns_none(self):
        with patch("dns_zone_manager.auth.proxy.get_settings", return_value=_proxy_settings()):
            assert proxy_user_from_request(_FakeRequest({})) is None

    def test_from_request_reads_identity(self):
        with patch("dns_zone_manager.auth.proxy.get_settings", return_value=_proxy_settings()):
            req = _FakeRequest(
                {
                    "X-Auth-Request-Email": "alice@example.com",
                    "X-Auth-Request-Preferred-Username": "Alice",
                }
            )
            user = proxy_user_from_request(req)
            assert user is not None
            assert user.email == "alice@example.com"
            assert user.name == "Alice"

    def test_from_request_custom_headers(self):
        settings = _proxy_settings(user_header="X-Forwarded-Email", name_header="X-Forwarded-User")
        with patch("dns_zone_manager.auth.proxy.get_settings", return_value=settings):
            req = _FakeRequest({"X-Forwarded-Email": "carol@example.com"})
            user = proxy_user_from_request(req)
            assert user is not None
            assert user.email == "carol@example.com"


class TestGetCurrentUserProxy:
    """get_current_user honors trusted-proxy header auth."""

    @pytest.mark.asyncio
    async def test_proxy_user_authenticated(self):
        settings = _proxy_settings()
        with (
            patch("dns_zone_manager.auth.combined.get_settings", return_value=settings),
            patch("dns_zone_manager.auth.proxy.get_settings", return_value=settings),
        ):
            req = _FakeRequest({"X-Auth-Request-Email": "dave@example.com"})
            user = await get_current_user(req, api_key=None, bearer_token=None)
            assert user.auth_type == "proxy"
            assert user.email == "dave@example.com"

    @pytest.mark.asyncio
    async def test_proxy_enabled_but_no_header_raises(self):
        settings = _proxy_settings()
        with (
            patch("dns_zone_manager.auth.combined.get_settings", return_value=settings),
            patch("dns_zone_manager.auth.proxy.get_settings", return_value=settings),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(_FakeRequest({}), api_key=None, bearer_token=None)
            assert exc_info.value.status_code == 401
