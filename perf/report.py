"""JSON/Markdown reporting and run comparison for the perf harness."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100). Empty list -> 0.0."""
    if not values:
        return 0.0
    if pct <= 0:
        return min(values)
    if pct >= 100:
        return max(values)
    ordered = sorted(values)
    # Nearest-rank: index = ceil(p/100 * n) - 1
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


@dataclass
class RunReport:
    """One scenario run ready to serialize."""

    scenario: str
    started_at: str
    finished_at: str
    host: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    provision: dict[str, Any] | None = None
    probes: dict[str, Any] = field(default_factory=dict)
    metrics_delta: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_report(report: RunReport) -> tuple[Path, Path]:
    """Write JSON + Markdown; update latest.md. Return (json_path, md_path)."""
    ensure_results_dir()
    slug = f"{timestamp_slug()}-{report.scenario}-{uuid.uuid4().hex[:8]}"
    json_path = RESULTS_DIR / f"{slug}.json"
    md_path = RESULTS_DIR / f"{slug}.md"
    data = report.to_dict()
    json_path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    md = render_markdown(data)
    md_path.write_text(md, encoding="utf-8")
    (RESULTS_DIR / "latest.md").write_text(md, encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return json_path, md_path


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}"
    except TypeError, ValueError:
        return str(value)


def _fmt_rps(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}"
    except TypeError, ValueError:
        return str(value)


def render_markdown(data: dict[str, Any]) -> str:
    """Render a run report dict as Markdown."""
    lines: list[str] = []
    scenario = data.get("scenario", "unknown")
    lines.append(f"# Perf: {scenario}")
    lines.append("")
    lines.append(f"- Started: `{data.get('started_at', '')}`")
    lines.append(f"- Finished: `{data.get('finished_at', '')}`")
    if data.get("config"):
        lines.append(f"- Config: `{json.dumps(data['config'], sort_keys=True)}`")
    lines.append("")

    provision = data.get("provision")
    if provision:
        lines.append("## Provision")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for key in (
            "zone",
            "records",
            "generate_seconds",
            "generate_bytes",
            "bind_add_seconds",
            "bind_ready_seconds",
            "axfr_refresh_seconds",
            "cache_size_bytes_before",
            "cache_size_bytes_after",
            "soa_serial",
        ):
            if provision.get(key) is not None:
                lines.append(f"| {key} | {provision[key]} |")
        if provision.get("errors"):
            lines.append("")
            lines.append("Errors: " + "; ".join(provision["errors"]))
        lines.append("")

    steps = data.get("steps") or []
    if steps:
        lines.append("## Load steps")
        lines.append("")
        header = (
            "| Step | Target rps | Achieved rps | Count | Errors "
            "| p50 ms | p95 ms | p99 ms | max ms |"
        )
        lines.append(header)
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for step in steps:
            writers = step.get("writers") or {}
            row = (
                f"| {step.get('label', '')} "
                f"| {_fmt_rps(step.get('target_rps'))} "
                f"| {_fmt_rps(step.get('achieved_rps'))} "
                f"| {writers.get('count', 0)} "
                f"| {writers.get('errors', 0)} "
                f"| {_fmt_ms(writers.get('p50_ms'))} "
                f"| {_fmt_ms(writers.get('p95_ms'))} "
                f"| {_fmt_ms(writers.get('p99_ms'))} "
                f"| {_fmt_ms(writers.get('max_ms'))} |"
            )
            lines.append(row)
        lines.append("")

        # Reader / websocket details when present
        for step in steps:
            readers = step.get("readers") or {}
            if readers:
                lines.append(f"### Readers ({step.get('label', '')})")
                lines.append("")
                lines.append("| Op | Count | Errors | p50 ms | p95 ms | p99 ms |")
                lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
                for op, summary in readers.items():
                    lines.append(
                        f"| {op} | {summary.get('count', 0)} | {summary.get('errors', 0)} | "
                        f"{_fmt_ms(summary.get('p50_ms'))} | {_fmt_ms(summary.get('p95_ms'))} | "
                        f"{_fmt_ms(summary.get('p99_ms'))} |"
                    )
                lines.append("")
            ws = step.get("websocket") or {}
            if ws:
                lines.append(f"### WebSocket ({step.get('label', '')})")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(ws, indent=2))
                lines.append("```")
                lines.append("")

    delta = data.get("metrics_delta") or {}
    if delta:
        lines.append("## Metrics delta")
        lines.append("")
        lines.append("| Metric | Delta |")
        lines.append("| --- | ---: |")
        # Show only non-zero deltas to keep the table readable.
        shown = 0
        for key, value in delta.items():
            if abs(value) < 1e-9:
                continue
            lines.append(f"| `{key}` | {value:g} |")
            shown += 1
            if shown >= 40:
                lines.append("| … | (truncated) |")
                break
        lines.append("")

    probes = data.get("probes") or {}
    if probes.get("after") or probes.get("before"):
        lines.append("## Probes")
        lines.append("")
        for label in ("before", "after"):
            block = probes.get(label)
            if not block:
                continue
            lines.append(f"### {label}")
            if block.get("docker_stats"):
                lines.append("")
                lines.append("Docker stats:")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(block["docker_stats"], indent=2))
                lines.append("```")
            if block.get("serial_lag"):
                lines.append("")
                lines.append(f"Serial lag: `{block['serial_lag']}`")
            lines.append("")

    if data.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for note in data["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    if data.get("errors"):
        lines.append("## Errors")
        lines.append("")
        for err in data["errors"]:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines) + "\n"


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_reports(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Markdown table comparing achieved rps and p99 between two runs."""
    lines = [
        f"# Compare: {a.get('scenario')} vs {b.get('scenario')}",
        "",
        f"- A: `{a.get('started_at')}`",
        f"- B: `{b.get('started_at')}`",
        "",
        "| Step | A rps | B rps | Δ rps | A p99 | B p99 | Δ p99 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    steps_a = {s.get("label"): s for s in a.get("steps") or []}
    steps_b = {s.get("label"): s for s in b.get("steps") or []}
    labels = list(dict.fromkeys([*steps_a.keys(), *steps_b.keys()]))
    for label in labels:
        sa, sb = steps_a.get(label, {}), steps_b.get(label, {})
        ra = float(sa.get("achieved_rps") or 0)
        rb = float(sb.get("achieved_rps") or 0)
        pa = float((sa.get("writers") or {}).get("p99_ms") or 0)
        pb = float((sb.get("writers") or {}).get("p99_ms") or 0)
        lines.append(
            f"| {label} | {ra:.1f} | {rb:.1f} | {rb - ra:+.1f} | "
            f"{pa:.1f} | {pb:.1f} | {pb - pa:+.1f} |"
        )
    lines.append("")
    return "\n".join(lines)
