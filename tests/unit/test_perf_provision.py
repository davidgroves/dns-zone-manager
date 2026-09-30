"""Unit tests for perf.provision helpers (no Docker)."""

from __future__ import annotations

from pathlib import Path

from perf.provision import addzone_config


def test_addzone_config_trailing_semicolon() -> None:
    """BIND requires a trailing ';' after the addzone config block."""
    cfg = addzone_config("perf5m.test", Path("/perf-zones/db.perf5m.test"))
    assert cfg.endswith("};")
    assert 'file "/perf-zones/db.perf5m.test"' in cfg
    assert 'key "dns-api-key"' in cfg
