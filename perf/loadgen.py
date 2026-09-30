"""Async load generator: API / DDNS writers, readers, WebSocket subscribers."""

from __future__ import annotations

import asyncio
import math
import os
import statistics
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, TextIO
from urllib.parse import quote

import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.tsig
import dns.tsigkeyring
import dns.update
import httpx2 as httpx

from perf.provision import (
    DEFAULT_API_BASE,
    DEFAULT_API_KEY,
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
)
from perf.report import percentile

try:
    from dns_zone_manager.dns.client import resolve_server_address as _resolve_bind
except ImportError:  # pragma: no cover - harness may run without package install

    def _resolve_bind(server: str) -> str:
        return server


def render_progress_bar(fraction: float, width: int = 28) -> str:
    """ASCII progress bar; ``fraction`` clamped to [0, 1]."""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "#" * filled + "-" * (width - filled)


@dataclass
class LoadProgress:
    """TTY progress for a multi-step load ramp (no-op when stderr is not a TTY)."""

    step_index: int
    step_total: int
    label: str
    duration: float
    target_rps: float
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    enabled: bool = field(default_factory=lambda: sys.stderr.isatty())
    _started: float = field(default_factory=time.perf_counter, init=False)
    _last_len: int = field(default=0, init=False)

    def _write_line(self, text: str, *, final: bool = False) -> None:
        if not self.enabled:
            return
        pad = max(0, self._last_len - len(text))
        end = "\n" if final else "\r"
        self.stream.write(f"\r{text}{' ' * pad}{end}")
        self.stream.flush()
        self._last_len = 0 if final else len(text)

    def tick(self, *, count: int = 0, errors: int = 0) -> None:
        elapsed = time.perf_counter() - self._started
        frac = elapsed / self.duration if self.duration > 0 else 1.0
        live_rps = count / elapsed if elapsed > 0 else 0.0
        prefix = f"[{self.step_index}/{self.step_total}] {self.label}"
        bar = render_progress_bar(frac)
        pct = min(100, int(frac * 100))
        line = (
            f"{prefix}  [{bar}] {pct:3d}%  "
            f"{elapsed:5.1f}/{self.duration:.0f}s  "
            f"~{live_rps:6.1f}/s (target {self.target_rps:.0f})  "
            f"n={count} err={errors}"
        )
        self._write_line(line)

    def finish(self, result: StepResult) -> None:
        writers = result.writers or {}
        line = (
            f"[{self.step_index}/{self.step_total}] {self.label}  done  "
            f"achieved={result.achieved_rps:.1f}/s  "
            f"n={writers.get('count', 0)}  err={writers.get('errors', 0)}  "
            f"p99={writers.get('p99_ms', 0):.1f}ms"
        )
        self._write_line(line, final=True)


TSIG_NAME = os.environ.get("TSIG_NAME", "dns-api-key")
TSIG_SECRET = os.environ.get("TSIG_SECRET", "K8vC2mP9nQ4rT6wX1yB3fG5hJ7kL0mN2pR4sU6vW8xY=")
TSIG_ALG = os.environ.get("TSIG_ALG", "hmac-sha256")


@dataclass
class LatencyStats:
    """Aggregate latency statistics in milliseconds."""

    count: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    error_kinds: Counter[str] = field(default_factory=Counter)

    def record(self, latency_ms: float, error: str | None = None) -> None:
        self.count += 1
        self.latencies_ms.append(latency_ms)
        if error:
            self.errors += 1
            self.error_kinds[error] += 1

    def summary(self) -> dict[str, Any]:
        xs = self.latencies_ms
        if not xs:
            return {
                "count": 0,
                "errors": self.errors,
                "error_kinds": dict(self.error_kinds),
            }
        return {
            "count": self.count,
            "errors": self.errors,
            "error_kinds": dict(self.error_kinds),
            "p50_ms": percentile(xs, 50),
            "p95_ms": percentile(xs, 95),
            "p99_ms": percentile(xs, 99),
            "max_ms": max(xs),
            "mean_ms": statistics.fmean(xs),
            "min_ms": min(xs),
        }


@dataclass
class StepResult:
    """Result of one rate step."""

    label: str
    target_rps: float
    duration_seconds: float
    achieved_rps: float
    writers: dict[str, Any]
    readers: dict[str, Any] = field(default_factory=dict)
    websocket: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _zone_path(zone: str) -> str:
    return zone.rstrip(".")


def _headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key if api_key is not None else DEFAULT_API_KEY
    h = {"Content-Type": "application/json"}
    if key:
        h["X-API-Key"] = key
    return h


def _tsig_keyring() -> dns.tsigkeyring.KeyRing:
    return dns.tsigkeyring.from_text({TSIG_NAME: TSIG_SECRET})


def _tsig_algorithm() -> Any:
    mapping = {
        "hmac-sha256": dns.tsig.HMAC_SHA256,
        "hmac-sha1": dns.tsig.HMAC_SHA1,
        "hmac-md5": dns.tsig.HMAC_MD5,
        "hmac-sha384": dns.tsig.HMAC_SHA384,
        "hmac-sha512": dns.tsig.HMAC_SHA512,
    }
    return mapping.get(TSIG_ALG.lower().replace("_", "-"), dns.tsig.HMAC_SHA256)


async def _token_bucket(
    rate: float,
    duration: float,
    on_token: Callable[[int], Awaitable[None]],
) -> int:
    """Fire ``on_token(seq)`` at approximately ``rate`` tokens/sec for duration.

    Returns number of tokens issued.
    """
    if rate <= 0:
        await asyncio.sleep(duration)
        return 0
    interval = 1.0 / rate
    started = time.perf_counter()
    deadline = started + duration
    seq = 0
    next_at = started
    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        if now < next_at:
            await asyncio.sleep(min(next_at - now, deadline - now))
            continue
        seq += 1
        await on_token(seq)
        next_at += interval
        # Catch up if we fell behind: don't try to issue a burst of backlog.
        if next_at < time.perf_counter() - interval:
            next_at = time.perf_counter()
    return seq


def _write_name(seq: int, pool_size: int) -> str:
    return f"perfchg-{(seq % pool_size) + 1:06d}"


def _write_addr(seq: int) -> str:
    # TEST-NET-3 documentation range.
    return f"203.0.113.{(seq % 200) + 1}"


async def _api_replace(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    zone: str,
    name: str,
    addr: str,
    ttl: int = 60,
) -> None:
    zp = quote(_zone_path(zone), safe="")
    body = {
        "name": name,
        "ttl": ttl,
        "type": "A",
        "rdclass": "IN",
        "records": [addr],
    }
    url = f"{api_base.rstrip('/')}/v1/zones/{zp}/rrsets"
    resp = await client.put(url, json=body)
    if resp.status_code == 404:
        # First touch: add then we're good for subsequent replaces.
        resp = await client.post(url, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")


def _ddns_replace_sync(
    *,
    bind_host: str,
    bind_port: int,
    zone: str,
    name: str,
    addr: str,
    ttl: int = 60,
) -> None:
    zone_name = dns.name.from_text(zone if zone.endswith(".") else zone + ".")
    update = dns.update.Update(
        zone_name,
        keyring=_tsig_keyring(),
        keyname=dns.name.from_text(TSIG_NAME),
        keyalgorithm=_tsig_algorithm(),
    )
    fqdn = f"{name}.{_zone_path(zone)}."
    update.delete(fqdn, "A")
    update.add(fqdn, ttl, "A", addr)
    # dnspython requires an IP address, not a Docker service hostname.
    where = _resolve_bind(bind_host)
    response = dns.query.tcp(update, where, port=bind_port, timeout=10)
    rcode = response.rcode()
    if rcode != dns.rcode.NOERROR:
        raise RuntimeError(f"DDNS rcode={dns.rcode.to_text(rcode)}")


async def run_writers(
    *,
    mode: str,
    zone: str,
    target_rps: float,
    duration: float,
    concurrency: int = 16,
    pool_size: int = 1000,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
    bind_host: str = DEFAULT_BIND_HOST,
    bind_port: int = DEFAULT_BIND_PORT,
    stats: LatencyStats | None = None,
) -> tuple[LatencyStats, float, int]:
    """Run writers; return (stats, wall_seconds, issued_tokens)."""
    stats = stats if stats is not None else LatencyStats()
    sem = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    headers = _headers(api_key)

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:

        async def one(seq: int) -> None:
            name = _write_name(seq, pool_size)
            addr = _write_addr(seq)
            use_api = mode == "api" or (mode == "mixed" and seq % 2 == 0)
            t0 = time.perf_counter()
            err: str | None = None
            async with sem:
                try:
                    if use_api:
                        await _api_replace(
                            client,
                            api_base=api_base,
                            zone=zone,
                            name=name,
                            addr=addr,
                        )
                    else:
                        await asyncio.to_thread(
                            _ddns_replace_sync,
                            bind_host=bind_host,
                            bind_port=bind_port,
                            zone=zone,
                            name=name,
                            addr=addr,
                        )
                except Exception as exc:  # noqa: BLE001 — record and continue
                    err = type(exc).__name__
            stats.record((time.perf_counter() - t0) * 1000.0, error=err)

        # Pipeline: token bucket schedules tasks; concurrency capped by semaphore.
        pending: set[asyncio.Task[None]] = set()

        async def on_token(seq: int) -> None:
            task = asyncio.create_task(one(seq))
            pending.add(task)
            task.add_done_callback(pending.discard)

        issued = await _token_bucket(target_rps, duration, on_token)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    wall = time.perf_counter() - started
    return stats, wall, issued


async def run_readers(
    *,
    zone: str,
    duration: float,
    ops_per_sec: float = 5.0,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
    deep_after: str | None = None,
) -> dict[str, Any]:
    """Issue a mix of read endpoints for ``duration`` seconds."""
    kinds = ("first_page", "deep_page", "search", "zone_list", "export")
    stats_by_kind: dict[str, LatencyStats] = {k: LatencyStats() for k in kinds}
    zp = quote(_zone_path(zone), safe="")
    base = api_base.rstrip("/")
    headers = _headers(api_key)
    after = deep_after or "host0001000"
    stop_at = time.perf_counter() + duration
    seq = 0

    async with httpx.AsyncClient(timeout=120.0, headers=headers) as client:

        async def do_one(kind: str) -> None:
            t0 = time.perf_counter()
            err: str | None = None
            try:
                if kind == "first_page":
                    r = await client.get(
                        f"{base}/v1/zones/{zp}/rrsets",
                        params={"limit": 100},
                    )
                elif kind == "deep_page":
                    r = await client.get(
                        f"{base}/v1/zones/{zp}/rrsets",
                        params={"limit": 100, "after": after},
                    )
                elif kind == "search":
                    r = await client.get(
                        f"{base}/v1/zones/{zp}/search",
                        params={"name_pattern": "host0001", "limit": 50},
                    )
                elif kind == "zone_list":
                    r = await client.get(f"{base}/v1/zones", params={"limit": 100})
                else:
                    r = await client.get(f"{base}/v1/zones/{zp}/export")
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                err = type(exc).__name__
            stats_by_kind[kind].record((time.perf_counter() - t0) * 1000.0, error=err)

        interval = 1.0 / ops_per_sec if ops_per_sec > 0 else duration
        while time.perf_counter() < stop_at:
            kind = kinds[seq % len(kinds)]
            seq += 1
            await do_one(kind)
            remaining = stop_at - time.perf_counter()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, remaining))

    return {k: v.summary() for k, v in stats_by_kind.items()}


async def run_websocket_subscribers(
    *,
    zone: str,
    count: int,
    duration: float,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Connect ``count`` WS clients and count messages for ``duration``."""
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        # httpx doesn't do WS; fall back to a minimal stdlib-free note.
        return {
            "enabled": False,
            "error": "websockets package not installed; skip WS scenario or "
            "use uvicorn's dependency (websockets comes with uvicorn[standard])",
        }

    base = api_base.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    zp = quote(_zone_path(zone), safe="")
    url = f"{base}/v1/zones/{zp}/ws"
    if api_key:
        url = f"{url}?api_key={quote(api_key)}"

    messages = 0
    errors = 0
    connected = 0
    lock = asyncio.Lock()

    async def subscriber() -> None:
        nonlocal messages, errors, connected
        try:
            async with connect(url, open_timeout=10, close_timeout=5) as ws:
                async with lock:
                    connected += 1
                deadline = time.perf_counter() + duration
                while time.perf_counter() < deadline:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=1.0)
                        async with lock:
                            messages += 1
                    except TimeoutError:
                        continue
        except Exception:  # noqa: BLE001
            async with lock:
                errors += 1

    tasks = [asyncio.create_task(subscriber()) for _ in range(max(0, count))]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return {
        "enabled": True,
        "requested": count,
        "connected": connected,
        "messages": messages,
        "errors": errors,
        "duration_seconds": duration,
    }


async def run_load_step(
    *,
    zone: str,
    mode: str = "api",
    target_rps: float = 100.0,
    duration: float = 60.0,
    concurrency: int = 16,
    pool_size: int = 1000,
    readers: bool = False,
    reader_rps: float = 5.0,
    websocket_count: int = 0,
    api_base: str = DEFAULT_API_BASE,
    api_key: str | None = None,
    bind_host: str = DEFAULT_BIND_HOST,
    bind_port: int = DEFAULT_BIND_PORT,
    label: str | None = None,
    progress: LoadProgress | None = None,
) -> StepResult:
    """Run one coordinated load step."""
    label = label or f"{mode}@{target_rps}/s"
    writer_stats = LatencyStats()

    async def writers_task() -> tuple[LatencyStats, float, int]:
        return await run_writers(
            mode=mode,
            zone=zone,
            target_rps=target_rps,
            duration=duration,
            concurrency=concurrency,
            pool_size=pool_size,
            api_base=api_base,
            api_key=api_key,
            bind_host=bind_host,
            bind_port=bind_port,
            stats=writer_stats,
        )

    tasks: list[Awaitable[Any]] = [writers_task()]
    if readers:
        tasks.append(
            run_readers(
                zone=zone,
                duration=duration,
                ops_per_sec=reader_rps,
                api_base=api_base,
                api_key=api_key,
            )
        )
    if websocket_count > 0:
        tasks.append(
            run_websocket_subscribers(
                zone=zone,
                count=websocket_count,
                duration=duration,
                api_base=api_base,
                api_key=api_key,
            )
        )

    async def progress_ticker() -> None:
        if progress is None:
            return
        while True:
            progress.tick(count=writer_stats.count, errors=writer_stats.errors)
            await asyncio.sleep(0.25)

    ticker: asyncio.Task[None] | None = None
    if progress is not None and progress.enabled:
        ticker = asyncio.create_task(progress_ticker())

    try:
        results = await asyncio.gather(*tasks)
    finally:
        if ticker is not None:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

    writer_stats_done, wall, issued = results[0]
    reader_summary: dict[str, Any] = {}
    ws_summary: dict[str, Any] = {}
    idx = 1
    if readers:
        reader_summary = results[idx]
        idx += 1
    if websocket_count > 0:
        ws_summary = results[idx]

    achieved = writer_stats_done.count / wall if wall > 0 else 0.0
    result = StepResult(
        label=label,
        target_rps=target_rps,
        duration_seconds=duration,
        achieved_rps=achieved,
        writers={
            **writer_stats_done.summary(),
            "issued_tokens": issued,
            "wall_seconds": wall,
            "mode": mode,
        },
        readers=reader_summary,
        websocket=ws_summary,
    )
    if progress is not None:
        progress.finish(result)
    return result


async def run_ramp(
    *,
    zone: str,
    mode: str,
    steps: list[dict[str, Any]],
    show_progress: bool | None = None,
    **kwargs: Any,
) -> list[StepResult]:
    """Run multiple rate steps sequentially.

    Each step dict: ``{rps, duration, concurrency?, label?}``.
    """
    if show_progress is None:
        show_progress = sys.stderr.isatty()
    out: list[StepResult] = []
    total = len(steps)
    for i, step in enumerate(steps, start=1):
        label = step.get("label") or f"{mode}@{step['rps']}/s"
        duration = float(step.get("duration", 60))
        target_rps = float(step["rps"])
        progress = None
        if show_progress:
            progress = LoadProgress(
                step_index=i,
                step_total=total,
                label=str(label),
                duration=duration,
                target_rps=target_rps,
                enabled=True,
            )
        result = await run_load_step(
            zone=zone,
            mode=mode,
            target_rps=target_rps,
            duration=duration,
            concurrency=int(step.get("concurrency", kwargs.get("concurrency", 16))),
            pool_size=int(step.get("pool_size", kwargs.get("pool_size", 1000))),
            readers=bool(step.get("readers", kwargs.get("readers", False))),
            reader_rps=float(step.get("reader_rps", kwargs.get("reader_rps", 5))),
            websocket_count=int(step.get("websocket_count", kwargs.get("websocket_count", 0))),
            api_base=kwargs.get("api_base", DEFAULT_API_BASE),
            api_key=kwargs.get("api_key"),
            bind_host=kwargs.get("bind_host", DEFAULT_BIND_HOST),
            bind_port=kwargs.get("bind_port", DEFAULT_BIND_PORT),
            label=step.get("label"),
            progress=progress,
        )
        out.append(result)
    return out


def estimate_needed_samples(p99_target_confidence: float = 0.01) -> int:
    """Rough helper for docs/tests — not used in load path."""
    # Rule of thumb: ~100/p for stable p99.
    return max(100, int(math.ceil(100 / max(p99_target_confidence, 0.01))))
