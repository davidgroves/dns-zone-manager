# Performance Testing

Opt-in harness for measuring DNS Zone Manager under large zones and high
write rates. **Not run by CI or pre-commit.**

## Quick start (devcontainer)

Requires the shared `perf-zones` volume on both `dev` and `bind` (see
`.devcontainer/docker-compose.yml`) and `allow-new-zones yes` in BIND.
After pulling those compose/named.conf changes, recreate BIND (and rebuild
the devcontainer if `/perf-zones` is missing inside `dev`):

```bash
docker compose -p dns-zone-manager_devcontainer \
  -f .devcontainer/docker-compose.yml up -d --force-recreate bind
```

```bash
# 1. Create a 5M-record zone in BIND and load it into the app cache
./perf.sh zone create --preset 5m

# Quicker smoke sizes:
./perf.sh zone create --preset 100k
./perf.sh zone create --preset 1m

# 2. Run scenarios (Markdown + JSON land in perf/results/)
./perf.sh run large-zone-load --preset 100k   # provision + cold reads
./perf.sh run rapid-api-writes                # ramp 50→500/s via REST
./perf.sh run rapid-ddns-writes               # ramp via direct DDNS
./perf.sh run mixed-read-write
./perf.sh run websocket-fanout
./perf.sh run cold-start
./perf.sh run soak                            # 30 min @ 100/s

./perf.sh run all                             # every scenario sequentially
./perf.sh list
./perf.sh report                              # re-render latest.md
./perf.sh report --compare perf/results/A.json perf/results/B.json

# 3. Tear down
./perf.sh zone destroy --delete-file
```

VS Code / Cursor tasks (not part of **Run All**):

- **PERF: Create 5M zone**
- **PERF: Run all**
- **PERF: Destroy zone**

Grafana: open the **DNS Zone Manager — Performance** dashboard on
http://localhost:3000 while a run is in progress.

## Examples compose (non-devcontainer)

```bash
cd examples
docker compose up -d
docker compose --profile perf run --rm perf-runner ./perf.sh zone create --preset 100k
docker compose --profile perf run --rm perf-runner ./perf.sh run rapid-api-writes
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_BASE` | `http://127.0.0.1:8000` | FastAPI base URL |
| `API_KEY` | (empty) | Optional `X-API-Key` |
| `BIND_HOST` / `BIND_PORT` | `bind` / `15353` | DNS + dig targets |
| `TSIG_NAME` / `TSIG_SECRET` / `TSIG_ALG` | demo key | DDNS TSIG |
| `RNDC` / `RNDC_CONF` | `rndc` / `/etc/rndc.conf` | Zone add/del |
| `PERF_APP_CONTAINER` | `dns-zone-manager_devcontainer-dev-1` | cold-start restart |

## Scenarios

| Name | What it measures |
| --- | --- |
| `large-zone-load` | Generate + `rndc addzone`, BIND SOA ready time, AXFR into cache, cold read latencies |
| `rapid-api-writes` | REST write ramp 50 → 500/s |
| `rapid-ddns-writes` | Direct DDNS ramp + NOTIFY/IXFR serial lag |
| `mixed-read-write` | 100 writes/s + concurrent readers on the large zone |
| `websocket-fanout` | 100 then 500 WS subscribers during 100 writes/s |
| `cold-start` | Time to `/health` and first list after container restart |
| `soak` | 30 minutes at 100 writes/s (memory / journal growth) |

### Suggested additional scenarios (not automated yet)

- **Many-zones breadth** — 1000 zones × 5k records (catalog pressure, zone list cost).
- **Atomic batch-size sweep** — `/v1/zones/{zone}/atomic` with growing update batches.
- **Scheduler throughput** — flood scheduled changes; measure claim/apply rate vs `poll_interval`.
- **IXFR→AXFR fallback storm** — force large deltas so BIND returns full AXFR under load.
- **UI timing** — Playwright records-page timings against the 5M zone.

## Methodology

1. Record host specs (fill the baseline table below).
2. Prefer `--preset 100k` while iterating; use `5m` for published numbers.
3. Each scenario writes `perf/results/<timestamp>-<scenario>.{json,md}` and
   updates `perf/results/latest.{json,md}`.
4. Compare two runs with `./perf.sh report --compare A.json B.json`.
5. Watch the Performance Grafana dashboard for live rates and cache size.

### Host spec template

| Field | Value |
| --- | --- |
| CPU | |
| Memory | |
| Disk | |
| OS / Docker | |
| BIND image | `internetsystemsconsortium/bind9:9.20` |
| App commit | |
| Zone size | e.g. 5_000_000 records |
| Date | |

### Baseline results

Fill after your first serious run (leave blank until measured):

| Scenario | Key metric | Result |
| --- | --- | --- |
| large-zone-load | BIND ready (s) | |
| large-zone-load | AXFR refresh (s) | |
| large-zone-load | cache_size_bytes | |
| large-zone-load | first_page p99 (ms) | |
| rapid-api-writes | achieved rps @ target 100 | |
| rapid-api-writes | achieved rps @ target 500 | |
| rapid-api-writes | p99 ms @ 100/s | |
| rapid-ddns-writes | serial lag p99 (s) | |
| websocket-fanout | messages @ 100 subs | |
| soak | cache_size_bytes Δ / 30m | |

## Known hotspots (context for reading numbers)

These are **current** implementation characteristics the harness is meant to
surface; fixing them is a separate follow-up.

1. **Per-update TCP** — each DDNS update opens a new TCP connection to BIND
   (`dns.query.tcp`); no connection reuse.
2. **DDNS TCP offloaded from the event loop** — ``dns.query.tcp`` runs in a
   dedicated thread pool; response handling / webhooks stay on the loop.
   NOTIFY-triggered refreshes also use ``asyncio.to_thread``. A single-worker
   API still tops out near ~200 writes/s from per-request Python work on the
   loop (BIND itself sustains 250+/s via direct DDNS). Per-update TCP (no
   connection reuse) remains.
3. **Full-sort pagination** — `GET .../rrsets?limit=N` still builds and sorts
   the entire zone before slicing; list/search cost grows with zone size.
4. **`rrset_count` walks** — zone list responses walk every RRset for counts.
5. **WebSocket fan-out** — broadcasts are sequential per subscriber; slow
   clients are dropped after `send_timeout`.
6. **Scheduler** — claims one due change at a time; not a high-rate write path.

## Layout

```
perf/
  zonegen.py      # streaming deterministic zone generator
  provision.py    # rndc addzone/delzone + cache refresh timing
  loadgen.py      # asyncio API/DDNS writers, readers, WS subscribers
  probes.py       # /metrics, docker stats, SOA serial lag
  report.py       # JSON → Markdown, compare
  cli.py / __main__.py
  scenarios/*.yaml
  results/        # gitignored outputs
perf.sh
```
