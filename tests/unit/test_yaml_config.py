"""Unit tests for YAML configuration loading."""

from pathlib import Path

import pytest
import yaml
from dns_zone_manager.config import (
    APIKeyEntry,
    APIKeySettings,
    NotifySettings,
    Settings,
    TSIGKeyEntry,
    get_settings,
    load_yaml_config,
    set_config_file,
)


class TestLoadYamlConfig:
    """Tests for load_yaml_config function."""

    def test_load_valid_yaml(self, tmp_path: Path):
        """Test loading a valid YAML config file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
dns:
  server: 192.168.1.1
  port: 5353
"""
        )

        config = load_yaml_config(config_file)

        assert config["dns"]["server"] == "192.168.1.1"
        assert config["dns"]["port"] == 5353

    def test_load_empty_yaml(self, tmp_path: Path):
        """Test loading an empty YAML file returns empty dict."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        config = load_yaml_config(config_file)

        assert config == {}

    def test_load_nonexistent_file(self, tmp_path: Path):
        """Test loading a nonexistent file raises FileNotFoundError."""
        config_file = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            load_yaml_config(config_file)

    def test_load_invalid_yaml(self, tmp_path: Path):
        """Test loading invalid YAML raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content:")

        with pytest.raises(yaml.YAMLError):
            load_yaml_config(config_file)


class TestAPIKeyEntry:
    """Tests for APIKeyEntry model."""

    def test_create_api_key_entry(self):
        """Test creating an API key entry."""
        entry = APIKeyEntry(name="alice", secret="secret123")  # type: ignore

        assert entry.name == "alice"
        assert entry.secret.get_secret_value() == "secret123"

    def test_api_key_entry_validation(self):
        """Test that name and secret are required."""
        with pytest.raises(Exception):
            APIKeyEntry()  # type: ignore


class TestAPIKeySettings:
    """Tests for APIKeySettings with list format."""

    def test_keys_from_list(self):
        """Test getting keys from keys_list (YAML format)."""
        settings = APIKeySettings(
            keys_list=[
                APIKeyEntry(name="alice", secret="secret1"),  # type: ignore
                APIKeyEntry(name="bob", secret="secret2"),  # type: ignore
            ]
        )

        keys = settings.keys
        assert len(keys) == 2
        assert keys["alice"].get_secret_value() == "secret1"
        assert keys["bob"].get_secret_value() == "secret2"

    def test_keys_from_string(self):
        """Test getting keys from keys_str (env var format)."""
        settings = APIKeySettings(keys_str="alice:secret1,bob:secret2")

        keys = settings.keys
        assert len(keys) == 2
        assert keys["alice"].get_secret_value() == "secret1"
        assert keys["bob"].get_secret_value() == "secret2"

    def test_keys_merge_list_takes_precedence(self):
        """Test that keys_list (YAML) takes precedence over keys_str (env)."""
        settings = APIKeySettings(
            keys_str="alice:env_secret",
            keys_list=[
                APIKeyEntry(name="alice", secret="yaml_secret"),  # type: ignore
            ],
        )

        keys = settings.keys
        assert keys["alice"].get_secret_value() == "yaml_secret"

    def test_empty_keys(self):
        """Test empty keys returns empty dict."""
        settings = APIKeySettings()

        assert settings.keys == {}


class TestNotifySettings:
    """Tests for NotifySettings TSIG configuration."""

    def test_notify_without_tsig(self):
        """Test NOTIFY settings without TSIG configuration."""
        settings = NotifySettings()

        assert settings.require_tsig is False
        assert settings.tsig_key is None

    def test_notify_with_tsig_key_reference(self):
        """Test NOTIFY settings with TSIG key reference."""
        settings = NotifySettings(
            require_tsig=True,
            tsig_key="notify-key",
        )

        assert settings.require_tsig is True
        assert settings.tsig_key == "notify-key"


class TestTSIGKeyEntry:
    """Tests for TSIGKeyEntry model."""

    def test_create_tsig_key_entry(self):
        """Test creating a TSIG key entry."""
        entry = TSIGKeyEntry(
            name="my-key",
            secret="dGVzdHNlY3JldA==",  # type: ignore
            algorithm="hmac-sha384",
        )

        assert entry.name == "my-key"
        assert entry.secret.get_secret_value() == "dGVzdHNlY3JldA=="
        assert entry.algorithm == "hmac-sha384"

    def test_tsig_key_entry_default_algorithm(self):
        """Test TSIG key entry default algorithm."""
        entry = TSIGKeyEntry(name="key", secret="secret")  # type: ignore

        assert entry.algorithm == "hmac-sha256"


class TestSettingsLoad:
    """Tests for Settings.load with YAML config."""

    def test_load_with_yaml_file(self, tmp_path: Path, monkeypatch):
        """Test loading settings from YAML file."""
        # Set required env vars that YAML will override
        monkeypatch.setenv("DNS_SERVER", "fallback-server")
        monkeypatch.setenv("TSIG_KEY_SECRET", "fallback-secret")

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: test-key
    secret: dGVzdHNlY3JldA==
    algorithm: hmac-sha256

dns:
  server: 10.0.0.1
  port: 5353
  timeout: 5.0
  update_tsig_key: test-key

api_key:
  enabled: true
  keys:
    - name: test-user
      secret: test-secret

debug: true
"""
        )

        settings = Settings.load(config_file)

        # YAML values should override env vars
        assert settings.dns.server == "10.0.0.1"
        assert settings.dns.port == 5353
        assert settings.dns.timeout == 5.0
        assert settings.dns.update_tsig_key == "test-key"
        assert len(settings.tsig_keys) == 1
        assert settings.tsig_keys[0].name == "test-key"
        assert settings.tsig_keys[0].secret.get_secret_value() == "dGVzdHNlY3JldA=="
        assert settings.api_key.enabled is True
        assert "test-user" in settings.api_key.keys
        assert settings.debug is True

    def test_yaml_overrides_env(self, tmp_path: Path, monkeypatch):
        """Test that YAML values override env vars."""
        monkeypatch.setenv("DNS_SERVER", "env-server")
        monkeypatch.setenv("DNS_PORT", "53")
        monkeypatch.setenv("TSIG_KEY_SECRET", "c2VjcmV0")

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
dns:
  server: yaml-server
  port: 5353
"""
        )

        settings = Settings.load(config_file)

        # YAML values should override env vars
        assert settings.dns.server == "yaml-server"
        assert settings.dns.port == 5353


class TestSetConfigFile:
    """Tests for set_config_file and get_settings."""

    def test_set_config_file_clears_cache(self, tmp_path: Path):
        """Test that set_config_file clears the settings cache."""
        # Create first config file
        config_file1 = tmp_path / "config1.yaml"
        config_file1.write_text(
            """
dns:
  server: server-one
"""
        )

        # Create second config file
        config_file2 = tmp_path / "config2.yaml"
        config_file2.write_text(
            """
dns:
  server: server-two
"""
        )

        # First load
        set_config_file(config_file1)
        settings1 = get_settings()
        assert settings1.dns.server == "server-one"

        # Set different config file - this should clear the cache
        set_config_file(config_file2)

        # Second load - should use new config
        settings2 = get_settings()
        assert settings2.dns.server == "server-two"

        # Clean up
        set_config_file(None)
        get_settings.cache_clear()

    def test_get_settings_requires_config_file(self):
        """Test that get_settings raises error without config file."""
        set_config_file(None)
        get_settings.cache_clear()

        with pytest.raises(RuntimeError, match="No config file set"):
            get_settings()

        # Clean up
        set_config_file(None)
        get_settings.cache_clear()


class TestTSIGKeyReferences:
    """Tests for TSIG key reference system."""

    def test_get_tsig_key_by_name(self, tmp_path: Path, monkeypatch):
        """Test looking up TSIG key by name."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: update-key
    secret: dXBkYXRlc2VjcmV0
    algorithm: hmac-sha256
  - name: notify-key
    secret: bm90aWZ5c2VjcmV0
    algorithm: hmac-sha384

dns:
  server: 10.0.0.1
  update_tsig_key: update-key

notify:
  require_tsig: true
  tsig_key: notify-key
"""
        )

        settings = Settings.load(config_file)

        # Test get_tsig_key
        update_key = settings.get_tsig_key("update-key")
        assert update_key is not None
        assert update_key.name == "update-key"
        assert update_key.secret.get_secret_value() == "dXBkYXRlc2VjcmV0"

        notify_key = settings.get_tsig_key("notify-key")
        assert notify_key is not None
        assert notify_key.name == "notify-key"
        assert notify_key.algorithm == "hmac-sha384"

        # Test get_update_tsig_key
        assert settings.get_update_tsig_key() == update_key

        # Test get_notify_tsig_key
        assert settings.get_notify_tsig_key() == notify_key

    def test_notify_key_falls_back_to_update_key(self, tmp_path: Path, monkeypatch):
        """Test that notify key falls back to update key when not specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: shared-key
    secret: c2hhcmVkc2VjcmV0

dns:
  server: 10.0.0.1
  update_tsig_key: shared-key

notify:
  require_tsig: true
  # tsig_key not specified - should fall back to update key
"""
        )

        settings = Settings.load(config_file)

        # notify.tsig_key is not set, should fall back to update key
        assert settings.notify.tsig_key is None
        notify_key = settings.get_notify_tsig_key()
        assert notify_key is not None
        assert notify_key.name == "shared-key"

    def test_get_nonexistent_key_returns_none(self, tmp_path: Path, monkeypatch):
        """Test that looking up nonexistent key returns None."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: existing-key
    secret: c2VjcmV0

dns:
  server: 10.0.0.1
  update_tsig_key: existing-key
"""
        )

        settings = Settings.load(config_file)

        assert settings.get_tsig_key("nonexistent") is None

    def test_invalid_key_reference_raises_error(self, tmp_path: Path, monkeypatch):
        """Test that referencing a non-existent key raises validation error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: real-key
    secret: c2VjcmV0

dns:
  server: 10.0.0.1
  update_tsig_key: real-key
  axfr_tsig_key: nonexistent-key  # This key doesn't exist
"""
        )

        with pytest.raises(ValueError, match="axfr_tsig_key references unknown key"):
            Settings.load(config_file)

    def test_multiple_tsig_keys(self, tmp_path: Path, monkeypatch):
        """Test configuration with multiple TSIG keys for different purposes."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
tsig_keys:
  - name: update-key
    secret: dXBkYXRlLXNlY3JldA==
    algorithm: hmac-sha256
  - name: axfr-key
    secret: YXhmci1zZWNyZXQ=
    algorithm: hmac-sha384
  - name: notify-key
    secret: bm90aWZ5LXNlY3JldA==
    algorithm: hmac-sha512

dns:
  server: 10.0.0.1
  update_tsig_key: update-key
  axfr_tsig_key: axfr-key

notify:
  require_tsig: true
  tsig_key: notify-key
"""
        )

        settings = Settings.load(config_file)

        # Verify all three keys are distinct
        update_key = settings.get_update_tsig_key()
        axfr_key = settings.get_axfr_tsig_key()
        notify_key = settings.get_notify_tsig_key()

        assert update_key is not None
        assert axfr_key is not None
        assert notify_key is not None

        assert update_key.name == "update-key"
        assert axfr_key.name == "axfr-key"
        assert notify_key.name == "notify-key"

        assert update_key.algorithm == "hmac-sha256"
        assert axfr_key.algorithm == "hmac-sha384"
        assert notify_key.algorithm == "hmac-sha512"
