"""Environment probes: Prometheus /metrics, docker stats, SOA serial lag."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from perf.provision import (
    DEFAULT_API_BASE,
    DEFAULT_API_KEY,
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    dig_soa,
)

_METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)"
)


@dataclass
class MetricsSnapshot:
    """Parsed subset of /metrics values (counters/gauges of interest)."""

    timestamp: float
    values: dict[str, float] = field(default_factory=dict)

    def get(self, name: str, default: float = 0.0) -> float:
        return self.values.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


INTERESTING_PREFIXES = (
    "dns_zone_manager_ddns_updates_",
    "dns_zone_manager_rrset_",
    "dns_zone_manager_zone_transfers",
    "dns_zone_manager_cache_size_bytes",
    "dns_zone_manager_cache_evictions_total",
    "dns_zone_manager_notifies_",
    "dns_zone_manager_zone_ws_",
    "dns_zone_manager_webhook_",
)


def _api_headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key if api_key is not None else DEFAULT_API_KEY
    return {"X-API-Key": key} if key else {}


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse Prometheus exposition format into name{labels}=value keys."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _METRIC_LINE.match(line)
        if not match:
            continue
        name = match.group("name")
        labels = match.group("labels") or ""
        value = float(match.group("value"))
        key = f"{name}{{{labels}}}" if labels else name
        out[key] = value
        # Also store bare name when unlabeled / first occurrence.
        if name not in out or not labels:
            out[name] = value
    return out


def snapshot_metrics(
    api_base: str = DEFAULT_API_BASE,
    *,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> MetricsSnapshot:
    """Fetch /metrics and keep interesting series."""
    with httpx.Client(timeout=timeout, headers=_api_headers(api_key)) as client:
        resp = client.get(f"{api_base.rstrip('/')}/metrics")
        resp.raise_for_status()
    all_values = parse_prometheus_text(resp.text)
    filtered = {
        k: v
        for k, v in all_values.items()
        if any(k.startswith(p) or k.split("{", 1)[0].startswith(p) for p in INTERESTING_PREFIXES)
    }
    return MetricsSnapshot(timestamp=time.time(), values=filtered)


def metrics_delta(before: MetricsSnapshot, after: MetricsSnapshot) -> dict[str, float]:
    """Subtract matching keys (after - before).

    Skip Prometheus ``*_created`` series (process-start timestamps, not counters).
    """
    keys = set(before.values) | set(after.values)
    out: dict[str, float] = {}
    for k in sorted(keys):
        base = k.split("{", 1)[0]
        if base.endswith("_created"):
            continue
        out[k] = after.values.get(k, 0.0) - before.values.get(k, 0.0)
    return out


def docker_stats(
    containers: list[str] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Return CPU/mem for named containers, or None if docker unavailable.

    ``containers`` defaults to common compose names for this project.
    """
    if containers is None:
        containers = [
            "dns-zone-manager_devcontainer-dev-1",
            "dns-zone-manager_devcontainer-bind-1",
            "dns-zone-manager",
            "dns-bind",
        ]
    try:
        proc = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
                *containers,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        return None
    if proc.returncode != 0:
        # Some names may not exist; retry without explicit list (all containers).
        try:
            proc = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError, subprocess.TimeoutExpired, OSError:
            return None
        if proc.returncode != 0:
            return None

    wanted = {c.lower() for c in containers}
    result: dict[str, dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, cpu, mem_usage, mem_perc = parts[0], parts[1], parts[2], parts[3]
        if (
            wanted
            and name.lower() not in wanted
            and not any(w in name.lower() for w in ("bind", "dns-zone", "dev-1"))
        ):
            continue
        result[name] = {
            "cpu_perc": cpu.strip(),
            "mem_usage": mem_usage.strip(),
            "mem_perc": mem_perc.strip(),
        }
    return result or None


def app_zone_serial(
    zone: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
) -> int | None:
    """Return SOA serial as reported by GET /v1/zones/{zone}."""
    zp = quote(zone.rstrip("."), safe="")
    try:
        with httpx.Client(timeout=30.0, headers=_api_headers(api_key)) as client:
            resp = client.get(f"{api_base.rstrip('/')}/v1/zones/{zp}")
            if resp.status_code != 200:
                return None
            data = resp.json()
    except httpx.HTTPError, OSError, ValueError:
        return None
    serial = data.get("serial")
    if serial is None and isinstance(data.get("soa"), dict):
        serial = data["soa"].get("serial")
    try:
        return int(serial) if serial is not None else None
    except TypeError, ValueError:
        return None


def measure_serial_lag(
    zone: str,
    *,
    bind_host: str = DEFAULT_BIND_HOST,
    bind_port: int = DEFAULT_BIND_PORT,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
    timeout: float = 30.0,
    poll: float = 0.05,
) -> dict[str, Any]:
    """Wait until app serial catches BIND serial; return lag metrics."""
    bind_serial = dig_soa(zone, host=bind_host, port=bind_port)
    started = time.perf_counter()
    deadline = started + timeout
    app_serial: int | None = None
    while time.perf_counter() < deadline:
        app_serial = app_zone_serial(zone, api_base=api_base, api_key=api_key)
        if bind_serial is not None and app_serial is not None and app_serial >= bind_serial:
            break
        time.sleep(poll)
    elapsed = time.perf_counter() - started
    caught_up = bind_serial is not None and app_serial is not None and app_serial >= bind_serial
    return {
        "bind_serial": bind_serial,
        "app_serial": app_serial,
        "lag_seconds": elapsed if caught_up else None,
        "timed_out": not caught_up,
        "timeout_seconds": timeout,
    }


def collect_environment_probe(
    zone: str | None = None,
    *,
    api_base: str = DEFAULT_API_BASE,
) -> dict[str, Any]:
    """One-shot probe used at scenario start/end."""
    probe: dict[str, Any] = {
        "api_base": api_base,
        "bind_host": DEFAULT_BIND_HOST,
        "bind_port": DEFAULT_BIND_PORT,
        "docker_stats": docker_stats(),
        "metrics": None,
        "serial_lag": None,
        "host": {
            "cwd": os.getcwd(),
        },
    }
    try:
        probe["metrics"] = snapshot_metrics(api_base).to_dict()
    except (httpx.HTTPError, OSError) as exc:
        probe["metrics_error"] = str(exc)
    if zone:
        probe["serial_lag"] = measure_serial_lag(zone, api_base=api_base)
    return probe
