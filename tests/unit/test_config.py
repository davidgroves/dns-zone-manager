"""Unit tests for configuration module."""

import os
from unittest.mock import patch

from dns_zone_manager.config import (
    APIKeySettings,
    AzureADSettings,
    CacheSettings,
    DNSSettings,
    TSIGKeyEntry,
)


class TestDNSSettings:
    """Tests for DNSSettings."""

    def test_dns_settings_from_env(self):
        """Test loading DNS settings from environment."""
        with patch.dict(
            os.environ,
            {
                "DNS_SERVER": "ns1.test.com",
                "DNS_PORT": "5353",
                "DNS_TIMEOUT": "15.0",
            },
            clear=True,
        ):
            settings = DNSSettings()
            assert settings.server == "ns1.test.com"
            assert settings.port == 5353
            assert settings.timeout == 15.0

    def test_dns_settings_defaults(self):
        """Test DNS settings defaults."""
        with patch.dict(os.environ, {"DNS_SERVER": "test.local"}, clear=True):
            settings = DNSSettings()
            assert settings.port == 53
            assert settings.timeout == 10.0
            assert settings.axfr_timeout == 60.0

    def test_dns_settings_tsig_key_references(self):
        """Test DNS settings TSIG key reference fields."""
        with patch.dict(os.environ, {"DNS_SERVER": "test.local"}, clear=True):
            settings = DNSSettings(update_tsig_key="my-update-key", axfr_tsig_key="my-axfr-key")
            assert settings.update_tsig_key == "my-update-key"
            assert settings.axfr_tsig_key == "my-axfr-key"
            assert settings.effective_axfr_tsig_key == "my-axfr-key"

    def test_dns_settings_axfr_key_fallback(self):
        """Test that AXFR key falls back to update key."""
        with patch.dict(os.environ, {"DNS_SERVER": "test.local"}, clear=True):
            settings = DNSSettings(update_tsig_key="my-key")
            assert settings.axfr_tsig_key is None
            assert settings.effective_axfr_tsig_key == "my-key"


class TestTSIGKeyEntry:
    """Tests for TSIGKeyEntry."""

    def test_create_tsig_key_entry(self):
        """Test creating a TSIG key entry."""
        entry = TSIGKeyEntry(
            name="my-key",
            secret="c2VjcmV0",  # type: ignore
            algorithm="hmac-sha512",
        )
        assert entry.name == "my-key"
        assert entry.secret.get_secret_value() == "c2VjcmV0"
        assert entry.algorithm == "hmac-sha512"

    def test_tsig_key_entry_default_algorithm(self):
        """Test TSIG key entry default algorithm."""
        entry = TSIGKeyEntry(name="test", secret="dGVzdA==")  # type: ignore
        assert entry.algorithm == "hmac-sha256"


class TestAzureADSettings:
    """Tests for AzureADSettings."""

    def test_azure_ad_disabled_by_default(self):
        """Test Azure AD is disabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            settings = AzureADSettings()
            assert not settings.enabled

    def test_azure_ad_settings_from_env(self):
        """Test loading Azure AD settings from environment."""
        with patch.dict(
            os.environ,
            {
                "AZURE_AD_AUTH_ENABLED": "true",
                "AZURE_AD_TENANT_ID": "test-tenant",
                "AZURE_AD_CLIENT_ID": "test-client",
            },
            clear=True,
        ):
            settings = AzureADSettings()
            assert settings.enabled
            assert settings.tenant_id == "test-tenant"
            assert settings.client_id == "test-client"


class TestAPIKeySettings:
    """Tests for APIKeySettings."""

    def test_api_key_enabled_by_default(self):
        """Test API key auth is enabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            settings = APIKeySettings()
            assert settings.enabled

    def test_api_key_settings_from_env(self):
        """Test loading API key settings from environment with name:secret format."""
        with patch.dict(
            os.environ,
            {
                "API_KEY_AUTH_ENABLED": "true",
                "API_KEYS": "admin:key1,reader:key2,ci-bot:key3",
            },
            clear=True,
        ):
            settings = APIKeySettings()
            assert settings.enabled
            keys = settings.keys
            assert len(keys) == 3
            assert "admin" in keys
            assert "reader" in keys
            assert "ci-bot" in keys
            assert keys["admin"].get_secret_value() == "key1"
            assert keys["reader"].get_secret_value() == "key2"
            assert keys["ci-bot"].get_secret_value() == "key3"

    def test_api_key_header_default(self):
        """Test default API key header name."""
        with patch.dict(os.environ, {}, clear=True):
            settings = APIKeySettings()
            assert settings.header_name == "X-API-Key"

    def test_api_key_empty_string(self):
        """Test empty API_KEYS string results in empty dict."""
        with patch.dict(os.environ, {"API_KEYS": ""}, clear=True):
            settings = APIKeySettings()
            assert settings.keys == {}

    def test_api_key_single_key(self):
        """Test single API key with name:secret format."""
        with patch.dict(os.environ, {"API_KEYS": "admin:single-key"}, clear=True):
            settings = APIKeySettings()
            keys = settings.keys
            assert len(keys) == 1
            assert "admin" in keys
            assert keys["admin"].get_secret_value() == "single-key"

    def test_api_key_invalid_format_ignored(self):
        """Test API keys without colon are silently ignored."""
        with patch.dict(os.environ, {"API_KEYS": "invalid-no-colon"}, clear=True):
            settings = APIKeySettings()
            assert settings.keys == {}


class TestCacheSettings:
    """Tests for CacheSettings."""

    def test_cache_enabled_by_default(self):
        """Test cache is enabled by default."""
        with patch.dict(os.environ, {}, clear=True):
            settings = CacheSettings()
            assert settings.enabled

    def test_cache_settings_from_env(self):
        """Test loading cache settings from environment."""
        with patch.dict(
            os.environ,
            {
                "CACHE_ENABLED": "false",
                "CACHE_MIN_REFRESH_INTERVAL": "120",
                "CACHE_MAX_REFRESH_INTERVAL": "3600",
            },
            clear=True,
        ):
            settings = CacheSettings()
            assert not settings.enabled
            assert settings.min_refresh_interval == 120
            assert settings.max_refresh_interval == 3600
