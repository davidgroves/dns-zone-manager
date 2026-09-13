"""Integration tests for UI theme endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.bind_container import BindContainer
from tests.integration.conftest import (
    _cleanup_config_file,
    _make_config_yaml,
    _set_config_file,
)


@pytest.fixture
def theme_client(bind_server: BindContainer, tmp_path: Path):
    """TestClient with a themed config including a local logo."""
    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    config_yaml = _make_config_yaml(
        bind_server,
        auth_enabled=False,
        extra_yaml=f"""
theme:
  app_name: Themed Zones
  default_mode: light
  allow_mode_toggle: true
  logo:
    path: {logo}
    alt: Themed Home
  dark:
    accent_primary: "#112233"
  light:
    bg_primary: "#fefefe"
""",
    )
    config_path = _set_config_file(config_yaml)

    from dns_zone_manager.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    _cleanup_config_file(config_path)


@pytest.fixture
def unthemed_client(bind_server: BindContainer):
    """TestClient with default theme (no logo)."""
    config_yaml = _make_config_yaml(bind_server, auth_enabled=False)
    config_path = _set_config_file(config_yaml)

    from dns_zone_manager.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    _cleanup_config_file(config_path)


@pytest.mark.integration
class TestUiConfigTheme:
    def test_ui_config_includes_theme_block(self, theme_client: TestClient):
        response = theme_client.get("/ui/config")
        assert response.status_code == 200
        data = response.json()
        assert "theme" in data
        theme = data["theme"]
        assert theme["appName"] == "Themed Zones"
        assert theme["defaultMode"] == "light"
        assert theme["allowModeToggle"] is True
        assert theme["logo"] == {"url": "/ui/logo", "alt": "Themed Home"}
        assert theme["dark"] == {"--accent-primary": "#112233"}
        assert theme["light"] == {"--bg-primary": "#fefefe"}

    def test_ui_config_theme_defaults(self, unthemed_client: TestClient):
        response = unthemed_client.get("/ui/config")
        assert response.status_code == 200
        theme = response.json()["theme"]
        assert theme["appName"] == "DNS Zone Manager"
        assert theme["defaultMode"] == "dark"
        assert theme["logo"] is None
        assert theme["light"] == {}
        assert theme["dark"] == {}


@pytest.mark.integration
class TestUiLogo:
    def test_serves_local_logo(self, theme_client: TestClient):
        response = theme_client.get("/ui/logo")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in response.content
        assert "Cache-Control" in response.headers

    def test_404_when_unconfigured(self, unthemed_client: TestClient):
        response = unthemed_client.get("/ui/logo")
        assert response.status_code == 404
