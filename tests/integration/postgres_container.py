"""PostgreSQL container for integration tests.

Mirrors ``bind_container.py``: a thin ``DockerContainer`` subclass that knows how
to wait for readiness and hand back connection settings.
"""

import socket
import time
from collections.abc import Generator

from dns_zone_manager.config import DatabaseSettings, PostgresSettings
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs


class PostgresContainer(DockerContainer):
    """PostgreSQL container for scheduled change store tests.

    The database starts empty on purpose: the store creates its own schema, so
    tests exercise the same path as pointing the application at a blank
    database.
    """

    POSTGRES_PORT = 5432

    def __init__(
        self,
        image: str = "postgres:17",
        database: str = "dns_zone_manager_test",
        user: str = "dns_zone_manager",
        password: str = "integration-test-password",
        **kwargs,
    ):
        """Initialize the PostgreSQL container.

        Args:
            image: Docker image to use. Keep aligned with the version in
                examples/docker-compose.yaml and .devcontainer/docker-compose.yml.
            database: Database name to create
            user: Database user to create (owns the database)
            password: Password for that user
            **kwargs: Additional container arguments
        """
        super().__init__(image, **kwargs)

        self.database = database
        self.user = user
        self.password = password

        self.with_exposed_ports(self.POSTGRES_PORT)
        self.with_env("TZ", "UTC")
        self.with_env("POSTGRES_DB", database)
        self.with_env("POSTGRES_USER", user)
        self.with_env("POSTGRES_PASSWORD", password)
        # Data lives in the container's own filesystem, which is discarded when
        # the container stops. No host mount, so no path translation is needed.

    def start(self) -> "PostgresContainer":
        """Start the container and wait until it accepts connections."""
        super().start()
        # The entrypoint starts the server twice: once on a unix socket for
        # initdb, then for real. Log lines therefore appear before TCP is
        # listening, so readiness is confirmed with an actual connection.
        wait_for_logs(self, r"database system is ready to accept connections", timeout=90)
        self._wait_for_connection()
        return self

    def _wait_for_connection(self, timeout: float = 60.0) -> None:
        """Poll until a real connection succeeds."""
        import psycopg

        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(
                    host=self.get_postgres_host(),
                    port=self.get_postgres_port(),
                    dbname=self.database,
                    user=self.user,
                    password=self.password,
                    connect_timeout=3,
                ) as connection:
                    connection.execute("SELECT 1")
                return
            except Exception as e:  # not up yet
                last_error = e
                time.sleep(0.5)
        raise TimeoutError(f"PostgreSQL did not become ready in {timeout}s: {last_error}")

    def get_postgres_host(self) -> str:
        """Get the reachable host address for the database."""
        host = self.get_container_host_ip()
        if host == "localhost":
            return "127.0.0.1"
        if host and not host[0].isdigit():
            try:
                return socket.gethostbyname(host)
            except socket.gaierror:
                pass
        return host

    def get_postgres_port(self) -> int:
        """Get the mapped host port for the database."""
        return int(self.get_exposed_port(self.POSTGRES_PORT))  # type: ignore[arg-type]

    def get_database_settings(self, **overrides) -> DatabaseSettings:
        """Build DatabaseSettings pointing at this container."""
        return DatabaseSettings(
            backend="postgres",
            postgres=PostgresSettings(
                host=self.get_postgres_host(),
                port=self.get_postgres_port(),
                database=self.database,
                user=self.user,
                password=self.password,
                sslmode="disable",
            ),
            **overrides,
        )

    def get_config_yaml_section(self) -> str:
        """Build the YAML 'database' section for this container."""
        return f"""
database:
  backend: postgres
  auto_migrate: true
  postgres:
    host: {self.get_postgres_host()}
    port: {self.get_postgres_port()}
    database: {self.database}
    user: {self.user}
    password: {self.password}
    sslmode: disable
"""


def postgres_container(
    database: str = "dns_zone_manager_test",
) -> Generator[PostgresContainer]:
    """Context manager for a PostgreSQL container.

    Args:
        database: Database name to create

    Yields:
        Running PostgresContainer instance
    """
    container = PostgresContainer(database=database)
    try:
        container.start()
        yield container
    finally:
        container.stop()
