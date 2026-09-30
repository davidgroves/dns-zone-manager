"""Unit tests for perf.zonegen (fast; no Docker)."""

from __future__ import annotations

from pathlib import Path

import dns.zone
import pytest

from perf.zonegen import (
    PRESETS,
    generate_zone,
    iter_zone_lines,
    resolve_record_count,
)


def test_resolve_record_count_presets() -> None:
    assert resolve_record_count("100k", None) == 100_000
    assert resolve_record_count("1m", None) == 1_000_000
    assert resolve_record_count("5m", None) == 5_000_000
    assert resolve_record_count(None, 1234) == 1234
    assert resolve_record_count(None, None) == PRESETS["5m"]
    with pytest.raises(ValueError):
        resolve_record_count("nope", None)


def test_iter_zone_lines_count_and_determinism() -> None:
    a = list(iter_zone_lines("perf.test", records=50, seed=7))
    b = list(iter_zone_lines("perf.test", records=50, seed=7))
    c = list(iter_zone_lines("perf.test", records=50, seed=8))
    assert a == b
    assert a != c
    # Header + 50 records; each generated RR is one line.
    generated = [ln for ln in a if ln.startswith("host") or ln.startswith("_http")]
    assert len(generated) == 50
    assert any("host0000050" in ln for ln in generated)


def test_generate_zone_parses_and_streams(tmp_path: Path) -> None:
    out = tmp_path / "db.perf.test"
    # Guard against accidental full in-memory buffering: generate to disk and
    # parse with dnspython.
    result = generate_zone(
        zone="perf.test",
        records=200,
        seed=42,
        output=out,
    )
    assert result.path == out
    assert result.records == 200
    assert result.bytes_written > 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "200 generated records below" in text
    zone = dns.zone.from_text(text, origin="perf.test.", relativize=False)
    names = {str(n) for n in zone.nodes}
    assert any("host00001" in n for n in names)
    assert (
        zone.get_rdataset("@", "SOA") is not None
        or zone.get_rdataset("perf.test.", "SOA") is not None
    )


def test_generate_zone_rejects_zero() -> None:
    with pytest.raises(ValueError):
        list(iter_zone_lines("x.test", records=0))
