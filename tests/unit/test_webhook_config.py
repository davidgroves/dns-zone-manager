"""Unit tests for webhook configuration parsing and filtering."""

from pathlib import Path

import pytest
from dns_zone_manager.config import (
    Settings,
    WebhookAuth,
    WebhookSettings,
    WebhookTarget,
)
from pydantic import ValidationError

BASE_YAML = """
tsig_keys:
  - name: test-key
    secret: c2VjcmV0
dns:
  server: 127.0.0.1
  update_tsig_key: test-key
"""


def _write(tmp_path: Path, extra: str) -> Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(BASE_YAML + extra)
    return config_file


class TestWebhookSettingsYaml:
    """Loading the webhooks section from a YAML config file."""

    def test_defaults_to_disabled(self, tmp_path: Path):
        settings = Settings.load(_write(tmp_path, ""))

        assert settings.webhooks.enabled is False
        assert settings.webhooks.targets == []
        assert settings.webhooks.events == ["change_applied", "change_failed"]

    def test_loads_slack_and_teams_targets(self, tmp_path: Path):
        settings = Settings.load(
            _write(
                tmp_path,
                """
webhooks:
  enabled: true
  base_url: https://dns.example.com/
  timeout: 2.5
  max_retries: 5
  targets:
    - name: netops-teams
      type: teams
      url: https://example.com/teams-hook
    - name: netops-slack
      type: slack
      url: https://example.com/slack-hook
      zones: ["example.com.", "*.internal."]
      events: [change_applied]
""",
            )
        )

        webhooks = settings.webhooks
        assert webhooks.enabled is True
        # Trailing slash stripped so links do not end up with a double slash
        assert webhooks.base_url == "https://dns.example.com"
        assert webhooks.timeout == 2.5
        assert webhooks.max_retries == 5
        assert [t.name for t in webhooks.targets] == ["netops-teams", "netops-slack"]

        teams, slack = webhooks.targets
        assert teams.type == "teams"
        assert teams.url.get_secret_value() == "https://example.com/teams-hook"
        assert teams.auth.type == "none"
        assert slack.events == ["change_applied"]

    def test_url_is_secret(self, tmp_path: Path):
        settings = Settings.load(
            _write(
                tmp_path,
                """
webhooks:
  enabled: true
  base_url: https://dns.example.com
  targets:
    - name: slack
      type: slack
      url: https://hooks.example.com/T000/B000/supersecret
""",
            )
        )

        target = settings.webhooks.targets[0]
        # The URL is itself the Slack/Teams credential, so it must not leak
        assert "supersecret" not in repr(target)
        assert "supersecret" not in str(target.url)

    def test_enabled_without_base_url_is_rejected(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="base_url is required"):
            Settings.load(
                _write(
                    tmp_path,
                    """
webhooks:
  enabled: true
""",
                )
            )

    def test_duplicate_target_names_rejected(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="duplicate webhook target names"):
            Settings.load(
                _write(
                    tmp_path,
                    """
webhooks:
  enabled: true
  base_url: https://dns.example.com
  targets:
    - name: dup
      url: https://a.example.com/hook
    - name: dup
      url: https://b.example.com/hook
""",
                )
            )

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WEBHOOK_ENABLED", "true")
        monkeypatch.setenv("WEBHOOK_BASE_URL", "https://env.example.com")
        monkeypatch.setenv("WEBHOOK_MAX_RETRIES", "7")

        webhooks = WebhookSettings()

        assert webhooks.enabled is True
        assert webhooks.base_url == "https://env.example.com"
        assert webhooks.max_retries == 7


class TestWebhookTargetValidation:
    """URL scheme and auth credential validation."""

    def test_http_url_rejected_by_default(self):
        with pytest.raises(ValidationError, match="insecure http"):
            WebhookTarget(name="t", url="http://plain.example.com/hook")

    def test_http_url_allowed_when_explicitly_permitted(self):
        target = WebhookTarget(
            name="t",
            url="http://plain.example.com/hook",
            allow_insecure=True,
        )
        assert target.url.get_secret_value().startswith("http://")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValidationError, match="must be http or https"):
            WebhookTarget(name="t", url="ftp://files.example.com/hook")

    @pytest.mark.parametrize("auth_type", ["bearer", "header", "hmac"])
    def test_secret_required(self, auth_type: str):
        with pytest.raises(ValidationError, match="requires 'secret'"):
            WebhookAuth(type=auth_type)

    def test_basic_requires_username_and_password(self):
        with pytest.raises(ValidationError, match="requires 'username' and 'password'"):
            WebhookAuth(type="basic", username="bob")

    def test_default_header_names_per_auth_type(self):
        assert WebhookAuth(type="hmac", secret="s").effective_header == "X-DNS-Signature"
        assert WebhookAuth(type="header", secret="s").effective_header == "X-API-Key"
        assert (
            WebhookAuth(type="header", secret="s", header="X-Custom").effective_header == "X-Custom"
        )


class TestTargetFiltering:
    """Per-target event and zone filters."""

    def test_no_zone_filter_matches_everything(self):
        target = WebhookTarget(name="t", url="https://a.example.com/h")
        assert target.matches_zone("anything.test.") is True

    @pytest.mark.parametrize(
        ("zone", "expected"),
        [
            ("example.com.", True),
            ("sub.internal.", True),
            ("deep.sub.internal.", True),
            ("other.com.", False),
            ("internal.", False),
        ],
    )
    def test_zone_patterns(self, zone: str, expected: bool):
        target = WebhookTarget(
            name="t",
            url="https://a.example.com/h",
            zones=["example.com.", "*.internal."],
        )
        assert target.matches_zone(zone) is expected

    def test_zone_match_normalizes_trailing_dot(self):
        target = WebhookTarget(
            name="t",
            url="https://a.example.com/h",
            zones=["example.com"],
        )
        assert target.matches_zone("example.com") is True
        assert target.matches_zone("example.com.") is True

    def test_event_filter_falls_back_to_global_default(self):
        target = WebhookTarget(name="t", url="https://a.example.com/h")
        assert target.matches_event("change_applied", ["change_applied"]) is True
        assert target.matches_event("change_failed", ["change_applied"]) is False

    def test_per_target_events_override_global(self):
        target = WebhookTarget(
            name="t",
            url="https://a.example.com/h",
            events=["change_failed"],
        )
        assert target.matches_event("change_failed", ["change_applied"]) is True
        assert target.matches_event("change_applied", ["change_applied"]) is False
