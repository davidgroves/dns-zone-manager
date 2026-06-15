"""BIND 9.20 container for integration tests."""

import base64
import json
import os
import secrets
import subprocess
import tempfile
import textwrap
from collections.abc import Generator
from pathlib import Path

from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs


def _get_host_path(container_path: str) -> str:
    """Translate a container path to the corresponding host path.

    When running in a devcontainer with Docker-in-Docker (socket mounted from host),
    paths created inside the devcontainer need to be translated to host paths
    for Docker volume mounts to work.

    Args:
        container_path: Path inside the current container

    Returns:
        Host path (translated if in DinD, otherwise unchanged)
    """
    # Check for explicit env var first
    workspace_host = os.environ.get("WORKSPACE_HOST_PATH")
    if workspace_host and container_path.startswith("/workspace"):
        return container_path.replace("/workspace", workspace_host, 1)

    # Try to auto-detect by introspecting our own container's mounts
    # This works when the Docker socket is mounted from the host
    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        return container_path

    try:
        result = subprocess.run(
            ["docker", "inspect", hostname, "--format", "{{json .Mounts}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            mounts = json.loads(result.stdout)
            for mount in mounts:
                dest = mount.get("Destination", "")
                source = mount.get("Source", "")
                if dest and source and container_path.startswith(dest):
                    # Translate the path
                    return container_path.replace(dest, source, 1)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return container_path


class BindContainer(DockerContainer):
    """BIND 9.20 container for DNS integration tests.

    Provides a fully configured BIND server with:
    - TSIG key for authenticated DDNS updates
    - AXFR zone transfer support
    - A test zone (test.example.)
    """

    DNS_PORT = 53
    RNDC_PORT = 953

    def __init__(
        self,
        image: str = "internetsystemsconsortium/bind9:9.20",
        zone_name: str = "test.example",
        tsig_key_name: str = "update-key",
        **kwargs,
    ):
        """Initialize BIND container.

        Args:
            image: Docker image to use
            zone_name: Name of the test zone
            tsig_key_name: Name of the TSIG key
            **kwargs: Additional container arguments
        """
        super().__init__(image, **kwargs)

        self.zone_name = zone_name if zone_name.endswith(".") else zone_name + "."
        self.tsig_key_name = tsig_key_name

        # Generate a random TSIG secret
        self._tsig_secret_raw = secrets.token_bytes(32)
        self._tsig_secret_b64 = base64.b64encode(self._tsig_secret_raw).decode()

        # Create temp directory for config files
        # When running in a devcontainer with Docker socket mounted from host,
        # temp files must be in a path accessible to the Docker daemon.
        # /workspace is mounted from the host, so use it as the temp base.
        workspace_tmp = Path("/workspace/.tmp")
        if Path("/workspace").exists():
            # Create .tmp directory if it doesn't exist (e.g., after fresh clone)
            workspace_tmp.mkdir(exist_ok=True)
            self._config_dir = tempfile.mkdtemp(prefix="bind_test_", dir=workspace_tmp)
        else:
            # Fall back to system temp for non-devcontainer environments
            self._config_dir = tempfile.mkdtemp(prefix="bind_test_")

        # Set up container - expose both TCP and UDP
        self.with_exposed_ports("53/tcp", "53/udp")
        self.with_env("TZ", "UTC")
        self.with_env("BIND9_USER", "root")

    @property
    def tsig_secret(self) -> str:
        """Get the base64-encoded TSIG secret."""
        return self._tsig_secret_b64

    @property
    def tsig_algorithm(self) -> str:
        """Get the TSIG algorithm."""
        return "hmac-sha256"

    def _create_named_conf(self) -> str:
        """Create named.conf content."""
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

            zone "{self.zone_name}" {{
                type primary;
                file "/var/lib/bind/db.{self.zone_name.rstrip(".")}";
                allow-update {{ key "{self.tsig_key_name}"; }};
                allow-transfer {{ key "{self.tsig_key_name}"; }};
            }};

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

    def _create_zone_file(self) -> str:
        """Create initial zone file content."""
        zone_base = self.zone_name.rstrip(".")
        return textwrap.dedent(f"""\
            $TTL 3600
            $ORIGIN {self.zone_name}
            @       IN      SOA     ns1.{zone_base}. admin.{zone_base}. (
                                    2024010101 ; serial
                                    3600       ; refresh
                                    600        ; retry
                                    604800     ; expire
                                    300        ; minimum
                                    )
            @       IN      NS      ns1.{zone_base}.
            ns1     IN      A       192.0.2.1
            www     IN      A       192.0.2.10
            mail    IN      A       192.0.2.20
            @       IN      MX      10 mail.{zone_base}.
            @       IN      TXT     "v=spf1 mx -all"
        """)

    def _write_config_files(self) -> None:
        """Write configuration files to temp directory."""
        config_path = Path(self._config_dir)

        # Create directories
        (config_path / "bind").mkdir(exist_ok=True)
        (config_path / "zones").mkdir(exist_ok=True)

        # Write named.conf
        (config_path / "bind" / "named.conf").write_text(self._create_named_conf())

        # Write zone file
        zone_base = self.zone_name.rstrip(".")
        (config_path / "zones" / f"db.{zone_base}").write_text(self._create_zone_file())

        # Make files and directories accessible for dynamic updates
        # The zones directory needs to be writable for journal files
        os.chmod(config_path / "bind" / "named.conf", 0o644)
        os.chmod(config_path / "zones", 0o777)  # Directory must be writable
        os.chmod(config_path / "zones" / f"db.{zone_base}", 0o666)  # Zone file must be writable

    def start(self) -> "BindContainer":
        """Start the BIND container.

        Returns:
            Self for chaining
        """
        # Write config files
        self._write_config_files()

        config_path = Path(self._config_dir)

        # Mount config files individually
        # Translate paths for Docker-in-Docker scenarios where the Docker daemon
        # runs on the host but we're inside a devcontainer
        named_conf_host = _get_host_path(str(config_path / "bind" / "named.conf"))
        zones_dir_host = _get_host_path(str(config_path / "zones"))

        self.with_volume_mapping(
            named_conf_host,
            "/etc/bind/named.conf",
            mode="ro",
        )
        self.with_volume_mapping(
            zones_dir_host,
            "/var/lib/bind",
            mode="rw",
        )

        # Start container
        super().start()

        # Wait for BIND to be ready - look for "running" in logs
        wait_for_logs(self, "running", timeout=60)

        return self

    def get_dns_host(self) -> str:
        """Get the DNS server host address as an IP."""
        import socket

        host = self.get_container_host_ip()
        # dnspython requires an IP address, not a hostname
        if host == "localhost":
            return "127.0.0.1"
        # Resolve host.docker.internal and other hostnames to IP
        if host and not host[0].isdigit():
            try:
                return socket.gethostbyname(host)
            except socket.gaierror:
                # If resolution fails, return as-is and let caller handle it
                pass
        return host

    def get_dns_port(self) -> int:
        """Get the mapped DNS TCP port (used for AXFR and DDNS)."""
        # Docker maps TCP and UDP to different host ports - must specify protocol
        # Note: testcontainers type hints say int but accepts str at runtime
        return int(self.get_exposed_port("53/tcp"))  # type: ignore[arg-type]

    def get_dns_udp_port(self) -> int:
        """Get the mapped DNS UDP port (used for queries)."""
        # Docker maps TCP and UDP to different host ports - must specify protocol
        return int(self.get_exposed_port("53/udp"))  # type: ignore[arg-type]

    def get_connection_config(self) -> dict:
        """Get configuration for connecting to this BIND instance.

        Returns:
            Dict with DNS_SERVER, DNS_PORT, DNS_UDP_PORT, TSIG_KEY_NAME, TSIG_KEY_SECRET
        """
        return {
            "DNS_SERVER": self.get_dns_host(),
            "DNS_PORT": str(self.get_dns_port()),  # TCP port for AXFR/DDNS
            "DNS_UDP_PORT": str(self.get_dns_udp_port()),  # UDP port for queries
            "TSIG_KEY_NAME": self.tsig_key_name,
            "TSIG_KEY_SECRET": self.tsig_secret,
            "TSIG_KEY_ALGORITHM": self.tsig_algorithm,
        }


def bind_container(
    zone_name: str = "test.example",
    tsig_key_name: str = "update-key",
) -> Generator[BindContainer]:
    """Context manager for BIND container.

    Args:
        zone_name: Name of the test zone
        tsig_key_name: Name of the TSIG key

    Yields:
        Running BindContainer instance
    """
    container = BindContainer(
        zone_name=zone_name,
        tsig_key_name=tsig_key_name,
    )
    try:
        container.start()
        yield container
    finally:
        container.stop()
