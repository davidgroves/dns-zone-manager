"""Integration test fixtures."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.bind_container import BindContainer


@pytest.fixture(scope="session")
def bind_server() -> Generator[BindContainer]:
    """Start a BIND 9.20 container for the test session.

    This fixture starts once per test session and is shared across all
    integration tests.
    """
    container = BindContainer(
        zone_name="test.example",
        tsig_key_name="integration-test-key",
    )
    try:
        container.start()
        yield container
    finally:
        container.stop()


def _make_config_yaml(
    bind_server: BindContainer,
    auth_enabled: bool = False,
    scheduler_db: str | None = None,
) -> str:
    """Create YAML config for the test.

    Args:
        bind_server: Running BIND container
        auth_enabled: Whether to enable API key authentication
        scheduler_db: Optional path for the scheduler SQLite database

    Returns:
        YAML configuration string
    """
    config = bind_server.get_connection_config()

    # Get ports - TCP for AXFR/DDNS, UDP for queries
    tcp_port = config["DNS_PORT"]
    udp_port = config["DNS_UDP_PORT"]
    server = config["DNS_SERVER"]
    tsig_name = config["TSIG_KEY_NAME"]
    tsig_secret = config["TSIG_KEY_SECRET"]
    tsig_algo = config["TSIG_KEY_ALGORITHM"]

    api_key_section = ""
    if auth_enabled:
        api_key_section = """
api_key:
  enabled: true
  keys:
    - name: test
      secret: integration-test-key-12345
"""
    else:
        api_key_section = """
api_key:
  enabled: false
"""

    if scheduler_db is None:
        scheduler_db = tempfile.mktemp(suffix="-scheduler.db")

    return f"""
# Integration test configuration
tsig_keys:
  - name: {tsig_name}
    secret: {tsig_secret}
    algorithm: {tsig_algo}

dns:
  server: {server}
  port: {udp_port}
  tcp_port: {tcp_port}
  update_tsig_key: {tsig_name}

{api_key_section}

azure_ad:
  enabled: false

cache:
  enabled: true

catalog:
  enabled: false

notify:
  enabled: false

scheduler:
  enabled: true
  database_path: {scheduler_db}
  poll_interval: 1
  max_attempts: 3
  retry_backoff: 1
  lease_ttl: 30
  default_expiry_window: 3600

debug: false
"""


def _set_config_file(config_yaml: str) -> Path:
    """Write config to temp file and set it as the active config.

    Returns:
        Path to the temp config file
    """
    from dns_zone_manager.config import get_settings, set_config_file

    # Create temp config file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_yaml)
        config_path = Path(f.name)

    # Set config file and clear cache
    set_config_file(config_path)
    get_settings.cache_clear()

    return config_path


def _cleanup_config_file(config_path: Path) -> None:
    """Clean up temp config file and restore previous config."""
    from dns_zone_manager.config import get_settings

    # Note: We don't restore the previous config file here because
    # the main conftest.py will handle that
    config_path.unlink(missing_ok=True)
    get_settings.cache_clear()


@pytest.fixture
def dns_config(bind_server: BindContainer) -> Generator[Path]:
    """Set up config file for DNS connection WITHOUT authentication.

    This is the default fixture for most integration tests - auth is disabled
    so tests don't need to pass auth headers.

    Args:
        bind_server: Running BIND container

    Yields:
        Path to the config file
    """
    config_yaml = _make_config_yaml(bind_server, auth_enabled=False)
    config_path = _set_config_file(config_yaml)

    yield config_path

    _cleanup_config_file(config_path)


@pytest.fixture
def dns_config_with_auth(bind_server: BindContainer) -> Generator[Path]:
    """Set up config file for DNS connection WITH authentication.

    Use this fixture for tests that specifically test authentication behavior.

    Args:
        bind_server: Running BIND container

    Yields:
        Path to the config file
    """
    config_yaml = _make_config_yaml(bind_server, auth_enabled=True)
    config_path = _set_config_file(config_yaml)

    yield config_path

    _cleanup_config_file(config_path)


@pytest.fixture
def test_client(dns_config: Path) -> Generator[TestClient]:
    """Create a test client WITHOUT authentication required.

    Args:
        dns_config: Path to config file (auth disabled)

    Yields:
        FastAPI TestClient
    """
    # Import app after config is set up
    from dns_zone_manager.main import create_app

    app = create_app()

    with TestClient(app) as client:
        yield client


@pytest.fixture
def test_client_with_auth(dns_config_with_auth: Path) -> Generator[TestClient]:
    """Create a test client WITH authentication required.

    Use this fixture for tests that specifically test authentication behavior.

    Args:
        dns_config_with_auth: Path to config file (auth enabled)

    Yields:
        FastAPI TestClient
    """
    from dns_zone_manager.main import create_app

    app = create_app()

    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Get authentication headers for API requests."""
    return {"X-API-Key": "integration-test-key-12345"}


@pytest.fixture
def zone_name() -> str:
    """Get the test zone name."""
    return "test.example."


# Backwards compatibility aliases for old fixture names
@pytest.fixture
def dns_env(dns_config: Path) -> Generator[dict[str, str]]:
    """Backwards compatibility alias for dns_config.

    Returns an empty dict since config is now file-based.
    """
    yield {}


@pytest.fixture
def dns_env_with_auth(dns_config_with_auth: Path) -> Generator[dict[str, str]]:
    """Backwards compatibility alias for dns_config_with_auth.

    Returns a dict with the API key info for reference.
    """
    yield {"API_KEYS": "test:integration-test-key-12345"}
