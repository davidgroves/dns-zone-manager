"""CLI for the performance harness: zone | run | report."""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2 as httpx
import yaml

from perf import loadgen, probes, provision, report, zonegen
from perf.provision import (
    DEFAULT_API_BASE,
    DEFAULT_API_KEY,
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
)
from perf.report import RunReport, compare_reports, load_report, write_report
from perf.zonegen import DEFAULT_ZONE, PRESETS, resolve_record_count

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
APP_CONTAINER = os.environ.get("PERF_APP_CONTAINER", "dns-zone-manager_devcontainer-dev-1")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _host_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "node": platform.node(),
    }


def _load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIOS_DIR / f"{name}.yaml"
    if not path.is_file():
        # Allow bare path
        alt = Path(name)
        if alt.is_file():
            path = alt
        else:
            raise FileNotFoundError(f"scenario not found: {name} (looked in {SCENARIOS_DIR})")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"scenario {path} must be a mapping")
    data.setdefault("name", path.stem)
    return data


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))


def cmd_zone(args: argparse.Namespace) -> int:
    if args.action == "create":
        count = resolve_record_count(args.preset, args.records)
        result = provision.create_zone(
            zone=args.zone,
            records=count,
            seed=args.seed,
            skip_generate=args.skip_generate,
            skip_cache_refresh=args.skip_cache_refresh,
            api_base=args.api_base,
        )
        print(yaml.safe_dump(result.to_dict(), sort_keys=False), end="")
        return 1 if result.errors else 0
    if args.action == "destroy":
        result = provision.destroy_zone(
            args.zone,
            delete_file=args.delete_file,
            api_base=args.api_base,
        )
        print(yaml.safe_dump(result.to_dict(), sort_keys=False), end="")
        return 1 if result.errors else 0
    if args.action == "generate":
        count = resolve_record_count(args.preset, args.records)
        gen = zonegen.generate_zone(
            zone=args.zone,
            records=count,
            seed=args.seed,
            output=Path(args.output) if args.output else None,
        )
        print(
            f"Wrote {gen.records} records → {gen.path} "
            f"({gen.bytes_written} bytes, {gen.elapsed_seconds:.2f}s)"
        )
        return 0
    raise SystemExit(f"unknown zone action: {args.action}")


async def _single_cold_reads(
    zone: str,
    api_base: str,
    api_key: str | None,
) -> dict[str, Any]:
    """One shot of each cold-read endpoint with latency."""
    from perf.loadgen import LatencyStats, _headers, _zone_path

    zp = quote(_zone_path(zone), safe="")
    base = api_base.rstrip("/")
    headers = _headers(api_key)
    ops = {
        "first_page": ("GET", f"{base}/v1/zones/{zp}/rrsets", {"limit": "100"}),
        "deep_page": (
            "GET",
            f"{base}/v1/zones/{zp}/rrsets",
            {"limit": "100", "after": "host0001000"},
        ),
        "search": (
            "GET",
            f"{base}/v1/zones/{zp}/search",
            {"name_pattern": "host0001", "limit": "50"},
        ),
        "zone_list": ("GET", f"{base}/v1/zones", {"limit": "100"}),
        "export": ("GET", f"{base}/v1/zones/{zp}/export", None),
    }
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=600.0, headers=headers) as client:
        for name, (method, url, params) in ops.items():
            stats = LatencyStats()
            t0 = time.perf_counter()
            err: str | None = None
            try:
                resp = await client.request(method, url, params=params)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            stats.record((time.perf_counter() - t0) * 1000.0, error=err)
            out[name] = stats.summary()
    return out


async def _cold_start(
    zone: str,
    api_base: str,
    api_key: str | None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {"restarted": False}
    if cfg.get("restart"):
        try:
            subprocess.run(
                ["docker", "restart", APP_CONTAINER],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            result["restarted"] = True
            result["container"] = APP_CONTAINER
        except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
            result["restart_error"] = str(exc)
            result["note"] = (
                "Could not docker-restart; restart the app manually and re-run, "
                "or set PERF_APP_CONTAINER."
            )

    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    health_timeout = float(cfg.get("health_timeout", 120))
    list_timeout = float(cfg.get("list_timeout", 600))
    base = api_base.rstrip("/")
    t0 = time.perf_counter()
    deadline = t0 + health_timeout
    health_ok = False
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        while time.perf_counter() < deadline:
            try:
                r = await client.get(f"{base}/health")
                if r.status_code == 200:
                    health_ok = True
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    result["health_ok"] = health_ok
    result["health_seconds"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    list_ok = False
    zp = quote(zone.rstrip("."), safe="")
    async with httpx.AsyncClient(timeout=list_timeout, headers=headers) as client:
        try:
            r = await client.get(
                f"{base}/v1/zones/{zp}/rrsets",
                params={"limit": 100},
            )
            list_ok = r.status_code == 200
        except httpx.HTTPError as exc:
            result["list_error"] = str(exc)
    result["list_ok"] = list_ok
    result["first_list_seconds"] = time.perf_counter() - t1
    return result


async def _run_scenario(
    scenario: dict[str, Any],
    *,
    api_base: str,
    api_key: str | None,
    bind_host: str,
    bind_port: int,
    records_override: int | None,
    preset_override: str | None,
) -> RunReport:
    name = scenario["name"]
    zone = scenario.get("zone", DEFAULT_ZONE)
    started = _now_iso()
    errors: list[str] = []
    notes = list(scenario.get("notes") or [])
    provision_data: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []
    metrics_delta: dict[str, float] = {}
    probe_before: dict[str, Any] | None = None
    probe_after: dict[str, Any] | None = None

    # Provision
    if scenario.get("provision"):
        count = resolve_record_count(
            preset_override or scenario.get("preset"),
            records_override if records_override is not None else scenario.get("records"),
        )
        prov = provision.create_zone(
            zone=zone,
            records=count,
            seed=int(scenario.get("seed", 42)),
            skip_generate=bool(scenario.get("skip_generate", False)),
            skip_cache_refresh=bool(scenario.get("skip_cache_refresh", False)),
            api_base=api_base,
        )
        provision_data = prov.to_dict()
        errors.extend(prov.errors)

    if scenario.get("require_zone") and not scenario.get("provision"):
        serial = provision.dig_soa(zone, host=bind_host, port=bind_port)
        if serial is None:
            errors.append(
                f"Zone {zone} not loaded in BIND; run: ./perf.sh zone create --zone {zone}"
            )

    probe_before = probes.collect_environment_probe(zone, api_base=api_base)
    try:
        metrics_before = probes.snapshot_metrics(api_base, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        metrics_before = None
        errors.append(f"metrics before failed: {exc}")

    # Cold reads
    cold_read_results: dict[str, Any] | None = None
    if scenario.get("cold_reads") and not errors:
        try:
            cold_read_results = await _single_cold_reads(zone, api_base, api_key)
            steps.append(
                {
                    "label": "cold-reads",
                    "target_rps": 0,
                    "duration_seconds": 0,
                    "achieved_rps": 0,
                    "writers": {},
                    "readers": cold_read_results,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cold reads failed: {exc}")

    # Cold start
    if scenario.get("cold_start") and not (
        scenario.get("require_zone") and errors and "not loaded" in str(errors)
    ):
        try:
            cs = await _cold_start(zone, api_base, api_key, scenario["cold_start"])
            steps.append(
                {
                    "label": "cold-start",
                    "target_rps": 0,
                    "duration_seconds": 0,
                    "achieved_rps": 0,
                    "writers": cs,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cold start failed: {exc}")

    # Load
    load_cfg = scenario.get("load")
    if load_cfg and not any("not loaded" in e for e in errors):
        try:
            ramp_steps = load_cfg.get("steps") or [
                {
                    "rps": load_cfg.get("rps", 100),
                    "duration": load_cfg.get("duration", 60),
                }
            ]
            results = await loadgen.run_ramp(
                zone=zone,
                mode=load_cfg.get("mode", "api"),
                steps=ramp_steps,
                concurrency=load_cfg.get("concurrency", 16),
                pool_size=load_cfg.get("pool_size", 1000),
                readers=load_cfg.get("readers", False),
                reader_rps=load_cfg.get("reader_rps", 5),
                websocket_count=load_cfg.get("websocket_count", 0),
                api_base=api_base,
                api_key=api_key,
                bind_host=bind_host,
                bind_port=bind_port,
            )
            steps.extend(r.to_dict() for r in results)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"load failed: {exc}")

    if scenario.get("measure_serial_lag"):
        try:
            lag = probes.measure_serial_lag(
                zone,
                bind_host=bind_host,
                bind_port=bind_port,
                api_base=api_base,
                api_key=api_key,
            )
            notes.append(f"serial_lag={lag}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"serial lag failed: {exc}")

    probe_after = probes.collect_environment_probe(zone, api_base=api_base)
    try:
        metrics_after = probes.snapshot_metrics(api_base, api_key=api_key)
        if metrics_before is not None:
            metrics_delta = probes.metrics_delta(metrics_before, metrics_after)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"metrics after failed: {exc}")

    return RunReport(
        scenario=name,
        started_at=started,
        finished_at=_now_iso(),
        host=_host_info(),
        config={
            "zone": zone,
            "api_base": api_base,
            "bind": f"{bind_host}:{bind_port}",
            "scenario_file": scenario.get("name"),
        },
        steps=steps,
        provision=provision_data,
        probes={"before": probe_before, "after": probe_after},
        metrics_delta=metrics_delta,
        notes=notes,
        errors=errors,
    )


def cmd_run(args: argparse.Namespace) -> int:
    names = list_scenarios() if args.scenario == "all" else [args.scenario]
    exit_code = 0
    for name in names:
        try:
            scenario = _load_scenario(name)
        except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"==> Running scenario: {scenario['name']}", file=sys.stderr)
        report_obj = asyncio.run(
            _run_scenario(
                scenario,
                api_base=args.api_base,
                api_key=args.api_key if args.api_key is not None else DEFAULT_API_KEY or None,
                bind_host=args.bind_host,
                bind_port=args.bind_port,
                records_override=args.records,
                preset_override=args.preset,
            )
        )
        json_path, md_path = write_report(report_obj)
        print(f"Wrote {json_path}", file=sys.stderr)
        print(f"Wrote {md_path}", file=sys.stderr)
        print(md_path.read_text(encoding="utf-8"))
        if report_obj.errors:
            exit_code = 1
    return exit_code


def cmd_report(args: argparse.Namespace) -> int:
    if args.compare:
        if len(args.compare) != 2:
            print("ERROR: --compare needs exactly two report JSON paths", file=sys.stderr)
            return 2
        a = load_report(Path(args.compare[0]))
        b = load_report(Path(args.compare[1]))
        print(compare_reports(a, b))
        return 0
    path = Path(args.path) if args.path else report.RESULTS_DIR / "latest.json"
    if not path.is_file():
        print(f"ERROR: report not found: {path}", file=sys.stderr)
        return 1
    data = load_report(path)
    print(report.render_markdown(data))
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    for name in list_scenarios():
        print(name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perf",
        description="DNS Zone Manager performance harness (opt-in, not part of CI)",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help=f"API base URL (default: {DEFAULT_API_BASE})",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (default: $API_KEY)",
    )
    parser.add_argument("--bind-host", default=DEFAULT_BIND_HOST)
    parser.add_argument("--bind-port", type=int, default=DEFAULT_BIND_PORT)

    sub = parser.add_subparsers(dest="command", required=True)

    zone_p = sub.add_parser("zone", help="Generate / provision / destroy a large zone")
    zone_sub = zone_p.add_subparsers(dest="action", required=True)

    for action in ("create", "generate"):
        p = zone_sub.add_parser(action)
        p.add_argument("--zone", default=DEFAULT_ZONE)
        p.add_argument("--records", type=int, default=None)
        p.add_argument("--preset", choices=sorted(PRESETS), default=None)
        p.add_argument("--seed", type=int, default=42)
        if action == "generate":
            p.add_argument("--output", default=None)
        if action == "create":
            p.add_argument("--skip-generate", action="store_true")
            p.add_argument("--skip-cache-refresh", action="store_true")

    destroy_p = zone_sub.add_parser("destroy")
    destroy_p.add_argument("--zone", default=DEFAULT_ZONE)
    destroy_p.add_argument(
        "--delete-file",
        action="store_true",
        help="Also delete the zone file under /perf-zones or .tmp/perf",
    )

    run_p = sub.add_parser("run", help="Run a named scenario (or 'all')")
    run_p.add_argument("scenario", help="Scenario name, path, or 'all'")
    run_p.add_argument("--records", type=int, default=None, help="Override provision size")
    run_p.add_argument("--preset", choices=sorted(PRESETS), default=None)

    report_p = sub.add_parser("report", help="Render or compare saved reports")
    report_p.add_argument("path", nargs="?", default=None, help="JSON report path")
    report_p.add_argument(
        "--compare",
        nargs=2,
        metavar=("A", "B"),
        help="Compare two JSON reports",
    )

    sub.add_parser("list", help="List available scenarios")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "zone":
        return cmd_zone(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "list":
        return cmd_list(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
