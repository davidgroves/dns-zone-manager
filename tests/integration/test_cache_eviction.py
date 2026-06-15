"""Integration tests for LRU cache eviction with real BIND server."""

import logging
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.integration.bind_container import BindContainer


class MultiZoneBindContainer(BindContainer):
    """BIND container with multiple zones for cache eviction testing.

    Creates multiple zones so we can fill up a small cache and trigger evictions.
    """

    def __init__(
        self,
        image: str = "internetsystemsconsortium/bind9:9.20",
        zone_count: int = 5,
        tsig_key_name: str = "update-key",
        **kwargs,
    ):
        """Initialize BIND container with multiple zones.

        Args:
            image: Docker image to use
            zone_count: Number of test zones to create
            tsig_key_name: Name of the TSIG key
            **kwargs: Additional container arguments
        """
        # Use first zone name for parent class
        super().__init__(
            image=image,
            zone_name="zone1.test",
            tsig_key_name=tsig_key_name,
            **kwargs,
        )
        self.zone_count = zone_count
        self.zone_names = [f"zone{i}.test." for i in range(1, zone_count + 1)]

    def _create_named_conf(self) -> str:
        """Create named.conf content with multiple zones."""
        import textwrap

        # Build zone configurations
        zone_configs = []
        for zone_name in self.zone_names:
            zone_base = zone_name.rstrip(".")
            zone_configs.append(f"""
            zone "{zone_name}" {{
                type primary;
                file "/var/lib/bind/db.{zone_base}";
                allow-update {{ key "{self.tsig_key_name}"; }};
                allow-transfer {{ key "{self.tsig_key_name}"; }};
            }};
            """)

        return textwrap.dedent(f"""\
            options {{
                directory "/var/cache/bind";
                listen-on port 53 {{ any; }};
                listen-on-v6 port 53 {{ any; }};
                allow-query {{ any; }};
                allow-transfer {{ key "{self.tsig_key_name}"; }};
                allow-recursion {{ none; }};
                recursion no;
                dnssec-validation no;
            }};

            key "{self.tsig_key_name}" {{
                algorithm hmac-sha256;
                secret "{self._tsig_secret_b64}";
            }};

            {"".join(zone_configs)}

            logging {{
                channel default_log {{
                    stderr;
                    severity info;
                    print-time yes;
                    print-category yes;
                    print-severity yes;
                }};
                category default {{ default_log; }};
                category update {{ default_log; }};
                category xfer-in {{ default_log; }};
                category xfer-out {{ default_log; }};
            }};
        """)

    def _create_zone_file_for(self, zone_name: str, record_count: int = 50) -> str:
        """Create zone file content for a specific zone.

        Args:
            zone_name: Zone name
            record_count: Number of A records to create (more records = larger zone)
        """
        import textwrap

        zone_base = zone_name.rstrip(".")

        # Start with basic zone records
        records = textwrap.dedent(f"""\
            $TTL 3600
            $ORIGIN {zone_name}
            @       IN      SOA     ns1.{zone_base}. admin.{zone_base}. (
                                    2024010101 ; serial
                                    3600       ; refresh
                                    600        ; retry
                                    604800     ; expire
                                    300        ; minimum
                                    )
            @       IN      NS      ns1.{zone_base}.
            ns1     IN      A       192.0.2.1
        """)

        # Add many records to make the zone larger
        for i in range(record_count):
            records += f"host{i:04d}     IN      A       192.0.2.{(i % 254) + 1}\n"

        return records

    def _write_config_files(self) -> None:
        """Write configuration files to temp directory."""
        from pathlib import Path

        config_path = Path(self._config_dir)

        # Create directories
        (config_path / "bind").mkdir(exist_ok=True)
        (config_path / "zones").mkdir(exist_ok=True)

        # Write named.conf
        (config_path / "bind" / "named.conf").write_text(self._create_named_conf())

        # Write zone files for all zones
        for zone_name in self.zone_names:
            zone_base = zone_name.rstrip(".")
            zone_file = config_path / "zones" / f"db.{zone_base}"
            zone_file.write_text(self._create_zone_file_for(zone_name))
            os.chmod(zone_file, 0o666)

        # Make files and directories accessible
        os.chmod(config_path / "bind" / "named.conf", 0o644)
        os.chmod(config_path / "zones", 0o777)


@pytest.fixture(scope="module")
def multi_zone_bind_server() -> Generator[MultiZoneBindContainer]:
    """Start a BIND container with multiple zones for eviction testing.

    This fixture is module-scoped so it's shared across all tests in this file.
    """
    container = MultiZoneBindContainer(
        zone_count=5,
        tsig_key_name="eviction-test-key",
    )
    try:
        container.start()
        yield container
    finally:
        container.stop()


@pytest.fixture
def small_cache_dns_env(
    multi_zone_bind_server: MultiZoneBindContainer,
) -> Generator[dict[str, str]]:
    """Set up config with very small cache size to trigger evictions.

    Args:
        multi_zone_bind_server: Running BIND container with multiple zones

    Yields:
        Dict with config info (for backwards compatibility)
    """
    from dns_zone_manager.config import get_settings, set_config_file

    config = multi_zone_bind_server.get_connection_config()

    # Get connection details
    tcp_port = config["DNS_PORT"]
    udp_port = config["DNS_UDP_PORT"]
    server = config["DNS_SERVER"]
    tsig_name = config["TSIG_KEY_NAME"]
    tsig_secret = config["TSIG_KEY_SECRET"]
    tsig_algo = config["TSIG_KEY_ALGORITHM"]

    # Create config YAML with small cache size
    # 30KB fits ~2 zones with 50 records each (~12KB per zone)
    config_yaml = f"""
# Cache eviction test configuration
tsig_keys:
  - name: {tsig_name}
    secret: {tsig_secret}
    algorithm: {tsig_algo}

dns:
  server: {server}
  port: {udp_port}
  tcp_port: {tcp_port}
  update_tsig_key: {tsig_name}

api_key:
  enabled: false

azure_ad:
  enabled: false

cache:
  enabled: true
  max_size_bytes: 30000

catalog:
  enabled: false

notify:
  enabled: false

debug: false
"""

    # Write temp config file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_yaml)
        config_path = Path(f.name)

    # Set config file
    set_config_file(config_path)
    get_settings.cache_clear()

    yield {}

    # Cleanup
    config_path.unlink(missing_ok=True)
    get_settings.cache_clear()


@pytest.mark.integration
class TestCacheEviction:
    """Integration tests for LRU cache eviction."""

    def test_cache_eviction_on_size_limit(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        small_cache_dns_env: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ):
        """Test that zones are evicted when cache size limit is exceeded.

        This test:
        1. Loads multiple zones into a small cache
        2. Verifies that eviction log events are emitted
        3. Verifies that the cache size stays within limits
        """
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        # Set up logging capture
        caplog.set_level(logging.DEBUG, logger="dns_zone_manager.dns.cache")

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Verify small cache is configured (30KB fits ~2 zones)
        assert settings.cache.max_size_bytes == 30000

        # Load zones one by one - this should trigger evictions
        zones_loaded = []
        for zone_name in multi_zone_bind_server.zone_names[:4]:
            cache.refresh_zone(zone_name)
            zones_loaded.append(zone_name)

        # Check that eviction events were logged
        eviction_logs = [record for record in caplog.records if "cache_evicted" in str(record.msg)]

        # We should have at least one eviction since we loaded 4 zones into a tiny cache
        assert len(eviction_logs) > 0, (
            f"Expected eviction logs but found none. "
            f"Loaded {len(zones_loaded)} zones. "
            f"Current cache size: {cache._current_size_bytes} bytes. "
            f"Max size: {settings.cache.max_size_bytes} bytes."
        )

        # Verify cache size is within limits
        assert (
            cache._current_size_bytes <= settings.cache.max_size_bytes
        ), f"Cache size {cache._current_size_bytes} exceeds max {settings.cache.max_size_bytes}"

    def test_lru_eviction_order(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        small_cache_dns_env: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ):
        """Test that least recently used zones are evicted first.

        With 30KB cache and ~12KB per zone, cache holds 2 zones.
        This test:
        1. Loads zone1, zone2 (both fit in cache)
        2. Accesses zone1 (making it most recently used)
        3. Loads zone3 (should evict zone2, not zone1)
        4. Verifies zone2 was evicted (LRU), not zone1 (recently accessed)
        """
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        caplog.set_level(logging.DEBUG, logger="dns_zone_manager.dns.cache")

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        zone_names = multi_zone_bind_server.zone_names

        # Load zone1 and zone2 (both fit in 30KB cache)
        cache.refresh_zone(zone_names[0])  # zone1
        cache.refresh_zone(zone_names[1])  # zone2

        # Verify both zones are in cache
        assert zone_names[0] in cache.list_zones()
        assert zone_names[1] in cache.list_zones()

        # Access zone1 to make it most recently used (moves to end of LRU)
        cache.get_zone(zone_names[0])

        # Clear log to see only new evictions
        caplog.clear()

        # Load zone3 - should trigger eviction of zone2 (LRU), not zone1
        cache.refresh_zone(zone_names[2])

        # Check eviction logs to see which zone was evicted
        eviction_logs = [record for record in caplog.records if "cache_evicted" in str(record.msg)]

        # Get evicted zone names from logs
        evicted_zones = []
        for record in eviction_logs:
            msg = record.msg if isinstance(record.msg, dict) else {}
            if "zone" in msg:
                evicted_zones.append(msg["zone"])

        # zone2 should be evicted (it's LRU since zone1 was accessed)
        assert (
            zone_names[1] in evicted_zones
        ), f"Expected zone2 ({zone_names[1]}) to be evicted as LRU. Evicted zones: {evicted_zones}"

        # zone1 should NOT be evicted since we just accessed it
        assert zone_names[0] not in evicted_zones, (
            f"zone1 ({zone_names[0]}) was evicted despite being recently accessed. "
            f"Evicted zones: {evicted_zones}"
        )

        # Verify final cache state: zone1 and zone3 should be in cache
        final_zones = cache.list_zones()
        assert zone_names[0] in final_zones, "zone1 should still be in cache"
        assert zone_names[2] in final_zones, "zone3 should be in cache"
        assert zone_names[1] not in final_zones, "zone2 should have been evicted"

    def test_cache_size_tracking(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        small_cache_dns_env: dict[str, str],
    ):
        """Test that cache size is tracked correctly."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Initially empty
        assert cache._current_size_bytes == 0

        # Load a zone
        zone_name = multi_zone_bind_server.zone_names[0]
        cached = cache.refresh_zone(zone_name)
        expected_size = cached.estimate_size_bytes()

        assert cache._current_size_bytes == expected_size
        assert cache._current_size_bytes > 0

        # Invalidate zone
        cache.invalidate_zone(zone_name)
        assert cache._current_size_bytes == 0

    def test_invalidate_all_resets_size(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        small_cache_dns_env: dict[str, str],
    ):
        """Test that invalidate_all resets cache size to zero."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        # Load some zones
        for zone_name in multi_zone_bind_server.zone_names[:2]:
            cache.refresh_zone(zone_name)

        assert cache._current_size_bytes > 0

        # Invalidate all
        cache.invalidate_all()

        assert cache._current_size_bytes == 0
        assert len(cache.list_zones()) == 0

    def test_unlimited_cache_no_eviction(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test that setting max_size_bytes=0 disables eviction."""
        from dns_zone_manager.config import get_settings, set_config_file

        # Get connection details from container
        config = multi_zone_bind_server.get_connection_config()
        tcp_port = config["DNS_PORT"]
        udp_port = config["DNS_UDP_PORT"]
        server = config["DNS_SERVER"]
        tsig_name = config["TSIG_KEY_NAME"]
        tsig_secret = config["TSIG_KEY_SECRET"]
        tsig_algo = config["TSIG_KEY_ALGORITHM"]

        # Create config YAML with unlimited cache (max_size_bytes: 0)
        config_yaml = f"""
# Unlimited cache test configuration
tsig_keys:
  - name: {tsig_name}
    secret: {tsig_secret}
    algorithm: {tsig_algo}

dns:
  server: {server}
  port: {udp_port}
  tcp_port: {tcp_port}
  update_tsig_key: {tsig_name}

api_key:
  enabled: false

azure_ad:
  enabled: false

cache:
  enabled: true
  max_size_bytes: 0

catalog:
  enabled: false

notify:
  enabled: false

debug: false
"""

        # Write temp config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_yaml)
            config_path = Path(f.name)

        # Set config file
        set_config_file(config_path)
        get_settings.cache_clear()

        try:
            caplog.set_level(logging.DEBUG, logger="dns_zone_manager.dns.cache")

            settings = get_settings()
            from dns_zone_manager.dns.cache import ZoneCache
            from dns_zone_manager.dns.client import DNSClient

            client = DNSClient(settings)
            cache = ZoneCache(settings, client)

            # Load all zones - should NOT trigger any evictions
            for zone_name in multi_zone_bind_server.zone_names:
                cache.refresh_zone(zone_name)

            # Check no eviction events
            eviction_logs = [
                record for record in caplog.records if "cache_evicted" in str(record.msg)
            ]

            assert (
                len(eviction_logs) == 0
            ), f"Expected no evictions with unlimited cache, but found {len(eviction_logs)}"

            # All zones should be cached
            assert len(cache.list_zones()) == len(multi_zone_bind_server.zone_names)

        finally:
            # Cleanup
            config_path.unlink(missing_ok=True)
            get_settings.cache_clear()

    def test_zone_size_estimation(
        self,
        multi_zone_bind_server: MultiZoneBindContainer,
        small_cache_dns_env: dict[str, str],
    ):
        """Test that zone size estimation produces reasonable values."""
        from dns_zone_manager.config import get_settings
        from dns_zone_manager.dns.cache import ZoneCache
        from dns_zone_manager.dns.client import DNSClient

        settings = get_settings()
        client = DNSClient(settings)
        cache = ZoneCache(settings, client)

        zone_name = multi_zone_bind_server.zone_names[0]
        cached = cache.refresh_zone(zone_name)

        estimated_size = cached.estimate_size_bytes()

        # Size should be reasonable - our test zones have ~50 records
        # Each record adds ~100+ bytes (name overhead + data + overhead)
        # So ~50 records should be at least 5KB
        assert estimated_size > 5000, f"Zone size {estimated_size} seems too small"
        assert estimated_size < 1_000_000, f"Zone size {estimated_size} seems too large"

        # RRset count should correlate with size
        # More records = larger size
        rrset_count = cached.rrset_count
        assert rrset_count > 0
        # Rough sanity check: at least ~100 bytes per rrset on average
        bytes_per_rrset = estimated_size / rrset_count
        assert bytes_per_rrset > 50, f"Bytes per rrset {bytes_per_rrset} seems too low"
