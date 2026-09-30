"""Unit tests for loadgen progress helpers."""

from __future__ import annotations

import io

from perf.loadgen import LoadProgress, StepResult, render_progress_bar


def test_render_progress_bar_bounds() -> None:
    assert render_progress_bar(0, width=10) == "----------"
    assert render_progress_bar(1, width=10) == "##########"
    assert render_progress_bar(0.5, width=10) == "#####-----"
    assert render_progress_bar(-1, width=4) == "----"
    assert render_progress_bar(2, width=4) == "####"


def test_load_progress_writes_and_finishes() -> None:
    buf = io.StringIO()
    progress = LoadProgress(
        step_index=1,
        step_total=4,
        label="api@50",
        duration=60.0,
        target_rps=50.0,
        stream=buf,
        enabled=True,
    )
    progress.tick(count=10, errors=1)
    assert "\r" in buf.getvalue()
    assert "api@50" in buf.getvalue()
    assert "[1/4]" in buf.getvalue()
    result = StepResult(
        label="api@50",
        target_rps=50.0,
        duration_seconds=60.0,
        achieved_rps=48.5,
        writers={"count": 100, "errors": 1, "p99_ms": 12.5},
    )
    progress.finish(result)
    assert "done" in buf.getvalue()
    assert "48.5/s" in buf.getvalue()
    assert buf.getvalue().endswith("\n")
