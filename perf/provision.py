"""Provision large zones into BIND via rndc and load them into the app cache."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from perf.zonegen import (
    DEFAULT_SEED,
    DEFAULT_ZONE,
    PRESETS,
    ZoneGenResult,
    default_output_path,
    generate_zone,
)

DEFAULT_BIND_HOST = os.environ.get("BIND_HOST", "bind")
DEFAULT_BIND_PORT = int(os.environ.get("BIND_PORT", "15353"))
DEFAULT_API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_API_KEY = os.environ.get("API_KEY", "")
DEFAULT_RNDC = os.environ.get("RNDC", "rndc")
DEFAULT_RNDC_CONF = os.environ.get("RNDC_CONF", "/etc/rndc.conf")
TSIG_NAME = os.environ.get("TSIG_NAME", "dns-api-key")


@dataclass
class ProvisionResult:
    """Timing and size measurements from zone create/destroy."""

    action: str
    zone: str
    records: int | None = None
    zone_path: str | None = None
    generate_seconds: float | None = None
    generate_bytes: int | None = None
    bind_add_seconds: float | None = None
    bind_ready_seconds: float | None = None
    axfr_refresh_seconds: float | None = None
    cache_size_bytes_before: float | None = None
    cache_size_bytes_after: float | None = None
    soa_serial: int | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _api_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if DEFAULT_API_KEY:
        headers["X-API-Key"] = DEFAULT_API_KEY
    return headers


def _zone_path(zone: str) -> str:
    return zone.rstrip(".")


def _rndc_cmd(*args: str) -> list[str]:
    cmd = [DEFAULT_RNDC]
    conf = Path(DEFAULT_RNDC_CONF)
    if conf.is_file():
        cmd.extend(["-c", str(conf)])
    cmd.extend(args)
    return cmd


def run_rndc(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run rndc with the shared conf; raise on failure when check=True."""
    return subprocess.run(
        _rndc_cmd(*args),
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def dig_soa(
    zone: str,
    *,
    host: str = DEFAULT_BIND_HOST,
    port: int = DEFAULT_BIND_PORT,
    timeout: float = 2.0,
) -> int | None:
    """Return SOA serial if dig succeeds, else None."""
    try:
        proc = subprocess.run(
            [
                "dig",
                f"@{host}",
                "-p",
                str(port),
                zone.rstrip(".") + ".",
                "SOA",
                "+short",
                "+time=2",
                "+tries=1",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
    except subprocess.TimeoutExpired, FileNotFoundError:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    parts = proc.stdout.split()
    # dig +short SOA: mname rname serial refresh retry expire minimum
    if len(parts) >= 3:
        try:
            return int(parts[2])
        except ValueError:
            return None
    return None


def wait_for_soa(
    zone: str,
    *,
    timeout: float = 600.0,
    poll: float = 1.0,
) -> tuple[float, int | None]:
    """Poll until BIND answers SOA; return (elapsed, serial)."""
    started = time.perf_counter()
    deadline = started + timeout
    serial: int | None = None
    while time.perf_counter() < deadline:
        serial = dig_soa(zone)
        if serial is not None:
            return time.perf_counter() - started, serial
        time.sleep(poll)
    return time.perf_counter() - started, None


def fetch_cache_size_bytes(api_base: str = DEFAULT_API_BASE) -> float | None:
    """Parse Prometheus gauge dns_zone_manager_cache_size_bytes from /metrics."""
    try:
        with httpx.Client(timeout=30.0, headers=_api_headers()) as client:
            resp = client.get(f"{api_base.rstrip('/')}/metrics")
            resp.raise_for_status()
    except httpx.HTTPError, OSError:
        return None
    for line in resp.text.splitlines():
        if line.startswith("dns_zone_manager_cache_size_bytes") and not line.startswith("#"):
            try:
                return float(line.split()[-1])
            except ValueError:
                return None
    return None


def refresh_zone_cache(
    zone: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 600.0,
) -> float:
    """Invalidate + refresh zone cache; return elapsed seconds."""
    base = api_base.rstrip("/")
    zp = quote(_zone_path(zone), safe="")
    headers = _api_headers()
    started = time.perf_counter()
    with httpx.Client(timeout=timeout, headers=headers) as client:
        client.delete(f"{base}/v1/zones/{zp}/cache")
        resp = client.post(f"{base}/v1/zones/{zp}/refresh")
        resp.raise_for_status()
    return time.perf_counter() - started


def addzone_config(zone: str, zone_file: Path) -> str:
    """rndc addzone configuration body (trailing ';' required by BIND)."""
    return (
        f'{{ type primary; file "{zone_file}"; '
        f'allow-update {{ key "{TSIG_NAME}"; }}; '
        f'allow-transfer {{ key "{TSIG_NAME}"; }}; }};'
    )


def create_zone(
    *,
    zone: str = DEFAULT_ZONE,
    records: int = PRESETS["5m"],
    seed: int = DEFAULT_SEED,
    output: Path | None = None,
    skip_generate: bool = False,
    skip_cache_refresh: bool = False,
    api_base: str = DEFAULT_API_BASE,
) -> ProvisionResult:
    """Generate (optional), rndc addzone, wait for SOA, refresh app cache."""
    zone = zone.rstrip(".")
    result = ProvisionResult(action="create", zone=zone, records=records)
    path = output or default_output_path(zone)

    gen: ZoneGenResult | None = None
    if not skip_generate:
        if path.exists() and path.stat().st_size > 0:
            # Reuse existing file when present (idempotent create).
            result.zone_path = str(path)
            result.generate_bytes = path.stat().st_size
            result.generate_seconds = 0.0
        else:
            gen = generate_zone(zone=zone, records=records, seed=seed, output=path)
            result.zone_path = str(gen.path)
            result.generate_seconds = gen.elapsed_seconds
            result.generate_bytes = gen.bytes_written
            path = gen.path
    else:
        result.zone_path = str(path)

    if not path.is_file():
        result.errors.append(f"zone file missing: {path}")
        return result

    # BIND must see the file under /perf-zones; path should already be there.
    started = time.perf_counter()
    try:
        run_rndc("addzone", zone, addzone_config(zone, path))
        result.bind_add_seconds = time.perf_counter() - started
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        # Already exists is OK for idempotent create.
        if "already exists" not in stderr.lower():
            result.errors.append(f"rndc addzone failed: {stderr or exc}")
            return result
        result.bind_add_seconds = time.perf_counter() - started

    ready_elapsed, serial = wait_for_soa(zone, timeout=900.0)
    result.bind_ready_seconds = ready_elapsed
    result.soa_serial = serial
    if serial is None:
        result.errors.append("BIND did not answer SOA within timeout")
        return result

    if not skip_cache_refresh:
        result.cache_size_bytes_before = fetch_cache_size_bytes(api_base)
        try:
            result.axfr_refresh_seconds = refresh_zone_cache(zone, api_base=api_base)
        except httpx.HTTPError as exc:
            result.errors.append(f"cache refresh failed: {exc}")
        result.cache_size_bytes_after = fetch_cache_size_bytes(api_base)

    return result


def destroy_zone(
    zone: str = DEFAULT_ZONE,
    *,
    delete_file: bool = False,
    api_base: str = DEFAULT_API_BASE,
) -> ProvisionResult:
    """rndc delzone and optionally remove the zone file + invalidate cache."""
    zone = zone.rstrip(".")
    result = ProvisionResult(action="destroy", zone=zone)
    started = time.perf_counter()
    try:
        run_rndc("delzone", "-clean", zone)
        result.bind_add_seconds = time.perf_counter() - started
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if "not found" not in stderr.lower() and "no matching zone" not in stderr.lower():
            result.errors.append(f"rndc delzone failed: {stderr or exc}")

    # Best-effort cache invalidate.
    try:
        zp = quote(_zone_path(zone), safe="")
        with httpx.Client(timeout=30.0, headers=_api_headers()) as client:
            client.delete(f"{api_base.rstrip('/')}/v1/zones/{zp}/cache")
    except httpx.HTTPError, OSError:
        pass

    if delete_file:
        path = default_output_path(zone)
        try:
            path.unlink(missing_ok=True)
            # Journals may sit beside the file.
            for suffix in (".jnl", ".jbk", ".signed"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError as exc:
            result.errors.append(f"delete file failed: {exc}")

    return result
