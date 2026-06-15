"""DNS Record Management API."""

try:
    from dns_zone_manager._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"


def _check_extras_installed() -> None:
    """Warn if package installed without cli or api extras."""
    try:
        import click  # noqa: F401

        return  # At least one extra is installed
    except ImportError:
        pass

    import sys

    print(
        "dns-zone-manager: No extras installed.\n"
        "\n"
        "This package requires at least one extra to be useful:\n"
        "\n"
        "  uv pip install dns-zone-manager[cli]      # CLI client only\n"
        "  uv pip install dns-zone-manager[api]      # API server only\n"
        "  uv pip install dns-zone-manager[cli,api]  # Both\n",
        file=sys.stderr,
    )


_check_extras_installed()
