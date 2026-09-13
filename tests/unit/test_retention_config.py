"""Unit tests for retention configuration parsing."""

from pathlib import Path

import pytest
from dns_zone_manager.config import RetentionSettings, Settings
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


class TestRetentionSettingsYaml:
    def test_defaults(self, tmp_path: Path):
        settings = Settings.load(_write(tmp_path, ""))

        retention = settings.retention
        assert retention.enabled is True
        assert retention.interval == 3600.0
        assert retention.max_age_days == 0
        assert retention.max_database_mb == 2048
        assert retention.trim_percent == 10
        assert retention.max_trim_passes == 10
        assert retention.statuses == [
            "applied",
            "failed",
            "cancelled",
            "expired",
            "reverted",
        ]
        assert retention.vacuum == "incremental"
        assert retention.dry_run is False

    def test_loads_custom_values(self, tmp_path: Path):
        settings = Settings.load(
            _write(
                tmp_path,
                """
retention:
  enabled: true
  interval: 120
  max_age_days: 90
  max_database_mb: 512
  trim_percent: 25
  max_trim_passes: 3
  statuses: [applied, expired, reverted]
  vacuum: full
  dry_run: true
""",
            )
        )

        retention = settings.retention
        assert retention.interval == 120
        assert retention.max_age_days == 90
        assert retention.max_database_mb == 512
        assert retention.trim_percent == 25
        assert retention.max_trim_passes == 3
        assert retention.statuses == ["applied", "expired", "reverted"]
        assert retention.vacuum == "full"
        assert retention.dry_run is True

    def test_rejects_active_statuses(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="active statuses"):
            Settings.load(
                _write(
                    tmp_path,
                    """
retention:
  statuses: [applied, draft, scheduled]
""",
                )
            )

    def test_rejects_running_status(self, tmp_path: Path):
        with pytest.raises(ValidationError, match="active statuses"):
            RetentionSettings(statuses=["applied", "running"])

    def test_trim_percent_bounds(self, tmp_path: Path):
        with pytest.raises(ValidationError):
            RetentionSettings(trim_percent=0)
        with pytest.raises(ValidationError):
            RetentionSettings(trim_percent=101)

    def test_zero_disables_policies(self, tmp_path: Path):
        settings = Settings.load(
            _write(
                tmp_path,
                """
retention:
  max_age_days: 0
  max_database_mb: 0
""",
            )
        )
        assert settings.retention.max_age_days == 0
        assert settings.retention.max_database_mb == 0


class TestRetentionSettingsEnv:
    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RETENTION_ENABLED", "false")
        monkeypatch.setenv("RETENTION_MAX_DATABASE_MB", "1024")
        monkeypatch.setenv("RETENTION_TRIM_PERCENT", "15")
        monkeypatch.setenv("RETENTION_MAX_AGE_DAYS", "30")

        settings = RetentionSettings()
        assert settings.enabled is False
        assert settings.max_database_mb == 1024
        assert settings.trim_percent == 15
        assert settings.max_age_days == 30
