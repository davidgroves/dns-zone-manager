"""DNS Zone Manager Server CLI.

Run the DNS Zone Manager API server with optional YAML configuration file.

Usage:
    dns-zone-manager --config config.yaml
    dns-zone-manager --config config.yaml --host 0.0.0.0 --port 8000

Install with: uv pip install dns-zone-manager[api]
"""

# Check for required dependencies before importing
try:
    import click
    import uvicorn
except ImportError:
    import sys

    print(
        "Error: Missing API server dependencies.\n"
        "Install with: uv pip install dns-zone-manager[api]",
        file=sys.stderr,
    )
    sys.exit(1)

from pathlib import Path

from dns_zone_manager import __version__


@click.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to YAML configuration file",
)
@click.option(
    "--host",
    "-h",
    default="0.0.0.0",
    help="Host to bind to (default: 0.0.0.0)",
)
@click.option(
    "--port",
    "-p",
    default=8000,
    type=int,
    help="Port to bind to (default: 8000)",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable auto-reload for development",
)
@click.option(
    "--reload-delay",
    default=0.25,
    type=float,
    help="Delay in seconds before reloading (default: 0.25)",
)
@click.option(
    "--workers",
    "-w",
    default=1,
    type=int,
    help="Number of worker processes (default: 1)",
)
@click.version_option(version=__version__, prog_name="dns-zone-manager")
def serve(
    config: Path | None,
    host: str,
    port: int,
    reload: bool,
    reload_delay: float,
    workers: int,
) -> None:
    """Run the DNS Zone Manager API server.

    Configuration is loaded from (in order of priority):
    1. YAML config file (if --config specified)
    2. Environment variables
    3. Default values

    Example:
        dns-zone-manager --config examples/config.yaml
    """
    # Set config file before importing app (which triggers settings load)
    if config:
        from dns_zone_manager.config import set_config_file

        set_config_file(config)
        click.echo(f"Loading configuration from: {config}")

    # Now we can safely get settings
    from dns_zone_manager.config import get_settings

    settings = get_settings()

    # Determine log level
    log_level = "debug" if settings.debug else settings.logging.level.lower()

    click.echo(f"Starting DNS Zone Manager on {host}:{port}")
    click.echo(f"Debug mode: {settings.debug}")
    click.echo(f"Log level: {log_level}")

    # Directories to exclude from file watching (avoids spurious reloads)
    reload_excludes = [
        ".venv",
        "node_modules",
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "*.pyc",
        ".coverage",
        "htmlcov",
        "dist",
        "build",
    ]

    uvicorn.run(
        "dns_zone_manager.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_delay=reload_delay if reload else 0,
        reload_excludes=reload_excludes if reload else None,
        workers=workers if not reload else 1,  # reload doesn't support multiple workers
        log_level=log_level,
    )


# For backwards compatibility with direct python -m invocation
main = serve

if __name__ == "__main__":
    serve()
