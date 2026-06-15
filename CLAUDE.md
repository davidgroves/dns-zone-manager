# LLM Context: DNS Zone Editor

This file provides context for AI assistants working on this codebase.

## Critical: Things to do when making changes.

- Run all the tests in pre-commit after any changes.
- Run all the tests with `./tests.sh` after any changes.
- Fix any errors those things show.

## Critical: Testing.

- When adding a feature, add tests for it.
- Remember all 4 classes of tests.
 - Back end pytest unit tests.
 - Back end pytest integration tests.
 - Front end unit tests.
 - Front end playwright UI tests.

## Critical: Logging and Instrumentation.

- When making changes, consider adding logging with the WIDE logs pattern.
- When making changes, consider adding prometheus metrics if appropriate.

## Critical: File Paths

**Backend source is at `backend/src/dns_zone_manager/`** — NOT `backend/dns_zone_manager/`.

```
backend/src/dns_zone_manager/ # Python backend (FastAPI)
├── routers/                  # API endpoints
├── dns/                      # DNS client, cache, types
├── auth/                     # API key + Azure AD auth
├── models/                   # Pydantic models
└── main.py                   # App factory

frontend/                     # TypeScript SPA (Alpine.js)
├── modules/                  # Feature modules (zones.ts, records.ts, etc.)
├── api/client.ts             # API client
├── types/index.ts            # TypeScript types
└── index.html                # Main HTML with Alpine templates

tests/
├── unit/                     # No Docker required
└── integration/              # Requires Docker (BIND container auto-starts)
```

## Commands

### Backend

```bash
# Run all tests
uv run pytest

# Unit tests only (fast, no Docker)
uv run pytest tests/unit/

# Integration tests (needs Docker running)
uv run pytest tests/integration/

# Skip integration tests
uv run pytest -m "not integration"

# Single test file
uv run pytest tests/integration/test_zone_export.py -v

# Linting and formatting
uv run ruff check backend/src tests
uv run ruff check --fix backend/src tests
uv run ruff format backend/src tests

# Type checking
uv run ty check backend/src

# Import check (verify module loads)
uv run python -c "from dns_zone_manager.routers.zones import router; print('OK')"
```

### Frontend

```bash
# Type check
npx tsc --noEmit

# Build production
npm run build

# Dev server (needs backend running)
npm run dev
```

### Full Development Environment

Use the devcontainer:

```bash
# VS Code/Cursor: Open folder → "Reopen in Container"
# Then use Tasks: "Start All (Devcontainer)"

# CLI/Headless:
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . bash
# Inside: uv run dns-zone-manager --config .devcontainer/config.devcontainer.yaml --reload
```

Config file: `.devcontainer/config.devcontainer.yaml` (DNS server = `bind`).

## Architecture Patterns

### Backend

1. **Dependency injection via module globals**: Routers use `set_dns_client()` / `set_zone_cache()` called from `main.py`

2. **Zone name normalization**: Always ends with `.` — normalize at start of every endpoint:
   ```python
   if not zone.endswith("."):
       zone = zone + "."
   ```

3. **Cache is read-only copy**: DNS server is source of truth. Cache populated via AXFR, invalidated on writes.

4. **DDNS prerequisites**: Add uses `NXRRSET` (must not exist), Delete/Replace use `YXRRSET` (must exist with values)

5. **Error enrichment**: Use `enrich_error_context()` before raising HTTPException for structured logging

### Frontend

1. **Alpine.js with `this` context**: Methods must use `this` (the Alpine proxy) for reactivity:
   ```typescript
   async loadZones(this: ZoneMethodContext) {
     this.loadingZones = true;  // Triggers reactivity
     // ...
   }
   ```

2. **API client pattern**: All requests go through `api()` from `frontend/api/client.ts`

3. **State in `frontend/state/index.ts`**: Central state object, modules add methods

4. **Toast notifications**: Use `this.toast('message', 'success'|'error'|'warning')`

## Common Tasks

### Adding a new API endpoint

1. Add route in `backend/src/dns_zone_manager/routers/{router}.py`
2. Add Pydantic models in `backend/src/dns_zone_manager/models/` if needed
3. Write tests in `tests/integration/test_{feature}.py`
4. Verify: `uv run python -c "from dns_zone_manager.routers.{router} import {function}"`

### Adding frontend functionality

1. Add method to appropriate module in `frontend/modules/`
2. Add to method context type if accessing state
3. Add UI in `frontend/index.html` using Alpine directives
4. Type check: `npx tsc --noEmit`

### Adding integration tests

1. Use fixtures from `tests/integration/conftest.py`:
   - `test_client` — TestClient with auth disabled
   - `zone_name` — Returns `"test.example."`
   - `dns_env` — Environment variables for DNS connection

2. Mark with `@pytest.mark.integration`

3. Tests auto-start BIND container via testcontainers

## Key Files

| File | Purpose |
|------|---------|
| `backend/src/dns_zone_manager/main.py` | App factory, dependency wiring |
| `backend/src/dns_zone_manager/dns/client.py` | DDNS updates, AXFR transfers |
| `backend/src/dns_zone_manager/dns/cache.py` | Zone cache, RRset operations |
| `frontend/modules/zones.ts` | Zone CRUD, export, pagination |
| `frontend/modules/records.ts` | Record CRUD operations |
| `frontend/index.html` | All UI templates |
| `tests/integration/conftest.py` | Test fixtures, BIND container setup |

## Gotchas

1. **Integration tests need Docker** — They spin up BIND via testcontainers. Skip with `-m "not integration"`

2. **Zone names always end with `.`** — `test.example.` not `test.example`

3. **Frontend uses Alpine.js NOT React/Vue** — Templates are in HTML with `x-` directives

4. **No database** — DNS server is the source of truth. Cache is ephemeral.

5. **TSIG authentication** — All DNS operations use shared secret. Configured via YAML or env vars.

6. **named-checkzone tests skip gracefully** — Export validation tests auto-skip if tool not installed

## Configuration

Configuration via YAML file (recommended) or environment variables. See `examples/config.example.yaml` for all options.

```bash
# With YAML config (recommended)
dns-zone-manager --config config.yaml

# Or with environment variables
DNS_SERVER=127.0.0.1 DNS_PORT=15353 ... dns-zone-manager
```

Key config sections (YAML):
```yaml
# Define TSIG keys (referenced by name from other sections)
tsig_keys:
  - name: dns-api-key
    secret: base64secret
    algorithm: hmac-sha256

dns:
  server: 127.0.0.1
  port: 15353
  update_tsig_key: dns-api-key  # References tsig_keys entry

api_key:
  enabled: true
  keys:
    - name: admin
      secret: secret-key-here

notify:
  require_tsig: false
  # tsig_key: notify-key  # Can use different key for NOTIFY validation
```

## Related Documentation

- `ARCHITECTURE.md` — System design, data flow diagrams
- `DEVELOPMENT.md` — Human-readable setup guide
- `README.md` — Project overview, configuration reference

