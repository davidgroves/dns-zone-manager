"""Unit tests for the theme configuration section."""

from pathlib import Path

import pytest
from dns_zone_manager.config import Settings, ThemePalette, ThemeSettings
from pydantic import ValidationError


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
dns:
  server: 127.0.0.1
{body}
"""
    )
    return path


class TestThemeDefaults:
    def test_defaults_with_no_theme_section(self, tmp_path: Path):
        settings = Settings.load(_write_config(tmp_path, ""))
        assert settings.theme.default_mode == "dark"
        assert settings.theme.allow_mode_toggle is True
        assert settings.theme.app_name is None
        assert settings.theme.logo is None
        assert settings.theme.light.as_css_vars() == {}
        assert settings.theme.dark.as_css_vars() == {}

    def test_to_ui_dict_falls_back_to_app_name(self, tmp_path: Path):
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
app_name: Corp DNS
""",
            )
        )
        ui = settings.theme.to_ui_dict(settings.app_name)
        assert ui["appName"] == "Corp DNS"
        assert ui["defaultMode"] == "dark"
        assert ui["allowModeToggle"] is True
        assert ui["logo"] is None
        assert ui["light"] == {}
        assert ui["dark"] == {}


class TestThemePalette:
    def test_partial_overrides(self, tmp_path: Path):
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
theme:
  app_name: Acme Zones
  default_mode: light
  dark:
    accent_primary: "#ff0000"
  light:
    bg_primary: white
    text_primary: "#111111"
""",
            )
        )
        ui = settings.theme.to_ui_dict(settings.app_name)
        assert ui["appName"] == "Acme Zones"
        assert ui["defaultMode"] == "light"
        assert ui["dark"] == {"--accent-primary": "#ff0000"}
        assert ui["light"] == {"--bg-primary": "white", "--text-primary": "#111111"}
        assert settings.theme.override_count() == 3

    def test_rejects_non_colour_string(self):
        with pytest.raises(ValidationError, match="invalid CSS colour"):
            ThemePalette(accent_primary="red; } body { color: blue")

    def test_rejects_url_injection(self):
        with pytest.raises(ValidationError, match="invalid CSS colour"):
            ThemePalette(bg_primary="url(javascript:alert(1))")

    def test_accepts_hex_and_named(self):
        palette = ThemePalette(
            accent_primary="#abc",
            accent_success="#aabbcc",
            accent_danger="#aabbccdd",
            bg_primary="Transparent",
        )
        assert palette.as_css_vars()["--accent-primary"] == "#abc"
        assert palette.as_css_vars()["--bg-primary"] == "Transparent"


class TestThemeLogo:
    def test_url_and_path_mutually_exclusive(self, tmp_path: Path):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"png")
        with pytest.raises(ValidationError, match="mutually exclusive"):
            Settings.load(
                _write_config(
                    tmp_path,
                    f"""
theme:
  logo:
    url: https://example.com/logo.png
    path: {logo}
""",
                )
            )

    def test_missing_logo_file_raises(self, tmp_path: Path):
        missing = tmp_path / "missing.png"
        with pytest.raises(ValidationError, match="does not exist"):
            Settings.load(
                _write_config(
                    tmp_path,
                    f"""
theme:
  logo:
    path: {missing}
""",
                )
            )

    def test_rejects_unsupported_suffix(self, tmp_path: Path):
        logo = tmp_path / "logo.gif"
        logo.write_bytes(b"gif")
        with pytest.raises(ValidationError, match="must end in"):
            Settings.load(
                _write_config(
                    tmp_path,
                    f"""
theme:
  logo:
    path: {logo}
""",
                )
            )

    def test_local_logo_resolves_to_ui_logo(self, tmp_path: Path):
        logo = tmp_path / "brand.svg"
        logo.write_text("<svg></svg>")
        settings = Settings.load(
            _write_config(
                tmp_path,
                f"""
theme:
  logo:
    path: {logo}
    alt: Brand
""",
            )
        )
        ui = settings.theme.to_ui_dict(settings.app_name)
        assert ui["logo"] == {"url": "/ui/logo", "alt": "Brand"}
        assert settings.theme.logo is not None
        assert settings.theme.logo.media_type() == "image/svg+xml"

    def test_url_logo(self, tmp_path: Path):
        settings = Settings.load(
            _write_config(
                tmp_path,
                """
theme:
  logo:
    url: /static/logo.png
    alt: Home
""",
            )
        )
        ui = settings.theme.to_ui_dict(settings.app_name)
        assert ui["logo"] == {"url": "/static/logo.png", "alt": "Home"}


class TestThemeSettingsStandalone:
    def test_env_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("THEME_DEFAULT_MODE", "light")
        monkeypatch.setenv("THEME_ALLOW_MODE_TOGGLE", "false")
        settings = ThemeSettings()
        assert settings.default_mode == "light"
        assert settings.allow_mode_toggle is False
