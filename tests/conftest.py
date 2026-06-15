"""Pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path

import pytest

# Test configuration as YAML
TEST_CONFIG_YAML = """
# Test configuration for unit tests
tsig_keys:
  - name: test-key
    secret: dGVzdC1zZWNyZXQta2V5LWZvci10ZXN0aW5n
    algorithm: hmac-sha256

dns:
  server: localhost
  port: 53
  update_tsig_key: test-key

api_key:
  enabled: true
  keys:
    - name: test
      secret: test-api-key-12345

azure_ad:
  enabled: false

cache:
  enabled: true

debug: false
"""

# Global to track the temp config file path
_test_config_path: Path | None = None


def pytest_configure(config):
    """Set up test config file before any tests are collected.

    This must happen early because some modules call get_settings() at import time.
    """
    global _test_config_path

    # Create temp config file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(TEST_CONFIG_YAML)
        _test_config_path = Path(f.name)

    # Set config file BEFORE any test modules are imported
    from dns_zone_manager.config import get_settings, set_config_file

    set_config_file(_test_config_path)
    get_settings.cache_clear()

    # Register custom markers
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests requiring Docker"
    )


def pytest_unconfigure(config):
    """Clean up test config file after all tests."""
    global _test_config_path

    from dns_zone_manager.config import get_settings, set_config_file

    set_config_file(None)
    get_settings.cache_clear()

    if _test_config_path and _test_config_path.exists():
        _test_config_path.unlink(missing_ok=True)
        _test_config_path = None


@pytest.fixture
def test_api_key() -> str:
    """Get the test API key."""
    return "test-api-key-12345"


@pytest.fixture
def auth_headers(test_api_key: str) -> dict[str, str]:
    """Get authentication headers for API requests."""
    return {"X-API-Key": test_api_key}
