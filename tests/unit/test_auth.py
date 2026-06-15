"""Unit tests for authentication modules."""

from unittest.mock import patch

import pytest
from dns_zone_manager.auth.api_key import APIKeyUser, validate_api_key
from dns_zone_manager.auth.azure import AzureUser
from dns_zone_manager.auth.combined import AuthenticatedUser
from fastapi import HTTPException


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
