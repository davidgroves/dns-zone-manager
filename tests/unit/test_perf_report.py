"""Unit tests for perf.report helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perf.report import (
    RunReport,
    compare_reports,
    percentile,
    render_markdown,
    write_report,
)


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([10.0], 50) == 10.0
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(xs, 50) == 5.0
    assert percentile(xs, 100) == 10.0
    assert percentile(xs, 0) == 1.0
    assert percentile(xs, 90) == 9.0


def test_render_markdown_contains_tables() -> None:
    data = {
        "scenario": "rapid-api-writes",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "config": {"zone": "perf5m.test"},
        "steps": [
            {
                "label": "api@100",
                "target_rps": 100,
                "achieved_rps": 95.5,
                "duration_seconds": 60,
                "writers": {
                    "count": 5700,
                    "errors": 2,
                    "p50_ms": 12.0,
                    "p95_ms": 40.0,
                    "p99_ms": 80.0,
                    "max_ms": 200.0,
                },
            }
        ],
        "metrics_delta": {"dns_zone_manager_rrset_replaces_total": 5700.0},
        "notes": ["hello"],
        "errors": [],
    }
    md = render_markdown(data)
    assert "# Perf: rapid-api-writes" in md
    assert "api@100" in md
    assert "p99 ms" in md
    assert "dns_zone_manager_rrset_replaces_total" in md
    assert "hello" in md


def test_write_report_and_compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import perf.report as report_mod

    monkeypatch.setattr(report_mod, "RESULTS_DIR", tmp_path)
    a = RunReport(
        scenario="rapid-api-writes",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        steps=[
            {
                "label": "api@100",
                "target_rps": 100,
                "achieved_rps": 90.0,
                "writers": {"p99_ms": 50.0, "count": 100},
            }
        ],
    )
    b = RunReport(
        scenario="rapid-api-writes",
        started_at="2026-01-02T00:00:00+00:00",
        finished_at="2026-01-02T00:01:00+00:00",
        steps=[
            {
                "label": "api@100",
                "target_rps": 100,
                "achieved_rps": 110.0,
                "writers": {"p99_ms": 40.0, "count": 120},
            }
        ],
    )
    ja, ma = write_report(a)
    jb, _mb = write_report(b)
    assert ja.is_file() and ma.is_file()
    assert (tmp_path / "latest.md").is_file()
    assert json.loads(ja.read_text())["scenario"] == "rapid-api-writes"
    cmp_md = compare_reports(
        json.loads(ja.read_text()),
        json.loads(jb.read_text()),
    )
    assert "Δ rps" in cmp_md
    assert "20.0" in cmp_md
