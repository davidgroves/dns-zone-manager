# Development Guide

This document covers setting up a development environment for the DNS Zone Manager project.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- VS Code or Cursor with the "Dev Containers" extension

## Getting Started (Devcontainer)

All development is done inside a devcontainer for a fully containerized environment — no local Python/Node setup required.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it
2. Install the "Dev Containers" extension in VS Code/Cursor
3. Open the project and click "Reopen in Container" when prompted

The devcontainer includes Python 3.13, Node 22, Playwright, and uv. BIND runs alongside in the same Docker network.

### Starting Services

Use the VS Code/Cursor task runner:

**Cmd/Ctrl+Shift+P → Tasks: Run Build Task**

Or run manually in separate terminals:

```bash
# Terminal 1: Start FastAPI backend
uv run dns-zone-manager --config .devcontainer/config.devcontainer.yaml --reload

# Terminal 2: Start Vite frontend
npm run dev
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |

Login with API key: `demo-api-key-12345`

## TypeScript Frontend

The web UI is a standalone SPA built with TypeScript and [Alpine.js](https://alpinejs.dev/). Source files are in `frontend/`.

### Development

During development, Vite serves the frontend with hot module replacement:

```bash
npm run dev          # Start Vite dev server at http://localhost:5173
```

The Vite dev server proxies API requests to the FastAPI backend at `http://localhost:8000`.

### Production Build

For production, the frontend builds to `dist/` as a standalone SPA:

```bash
npm run build        # Build to dist/ directory
```

In Docker, the frontend is served by nginx which proxies API requests to FastAPI (see `Dockerfile.frontend` and `examples/nginx.conf`).

### Configuration

- `tsconfig.json` — TypeScript (strict mode, ES2022)
- `vite.config.ts` — Vite bundler config
- `package.json` — npm scripts and dependencies

### Scheduled changes (SQLite or PostgreSQL)

The scheduler stores named/timed change *intent* and the audit log in a database
(never zone state). Choose the backend with `database.backend`; the schema is
created on first start either way, so an empty database is all that is needed.

```yaml
# A local file — no external service
database:
  backend: sqlite
  path: /var/lib/dns-zone-manager/scheduler.db

scheduler:
  enabled: true
```

```yaml
# An external server — durable, and shareable by several instances
database:
  backend: postgres
  postgres:
    host: postgres
    port: 5432
    database: dns_zone_manager
    user: dns_zone_manager
    password: change-me
    sslmode: require
```

The devcontainer uses PostgreSQL, running as a sibling container reachable at
hostname `postgres` (see `.devcontainer/config.devcontainer.yaml`). Its data
lives in tmpfs, so every restart starts from an empty database and re-exercises
schema creation. Inspect it with:

```bash
psql postgresql://dns_zone_manager:devpassword@postgres:5432/dns_zone_manager
# From the host instead: localhost:15432
```

To develop against SQLite, replace the `database` section with the SQLite form
above pointing at `.tmp/scheduler.db`.

#### Backups

This database is the only persistent state in the system, and it cannot be
rebuilt from DNS. Zone data does not need backing up (DNS is the source of
truth), but pending changes and the audit trail do.

```bash
# PostgreSQL
pg_dump --format=custom --file=scheduler-$(date +%F).dump \
  postgresql://dns_zone_manager@db:5432/dns_zone_manager
pg_restore --clean --if-exists --dbname=postgresql://... scheduler-2026-01-01.dump

# SQLite (safe while running, unlike a plain file copy)
sqlite3 /var/lib/dns-zone-manager/scheduler.db ".backup scheduler-$(date +%F).db"
```

#### Schema migrations

Alembic owns the schema and ships inside the package. The application runs
`alembic upgrade head` at startup unless `database.auto_migrate` is false. To
work with migrations directly, point Alembic at your config file:

```bash
export DNS_ZONE_MANAGER_CONFIG_FILE=.devcontainer/config.devcontainer.yaml

uv run alembic current                          # Which revision is applied
uv run alembic check                            # Does the schema match the models?
uv run alembic upgrade head                     # Apply pending migrations
uv run alembic downgrade -1                     # Roll back one revision
uv run alembic revision --autogenerate -m "add x"   # Generate a new revision
```

After changing `backend/src/dns_zone_manager/scheduler/schema.py`, generate a
revision and review it — autogenerate does not always get types or indexes
right. `alembic check` fails when the models and migrations have drifted apart.

API: `POST/GET/PATCH/DELETE /v1/scheduled-changes`, plus `/apply`, `/preview`,
`/revert-preview`, and `/revert`. Applied changes with pre-apply snapshots can be
reverted immediately (status becomes `reverted`).
From the UI: enable Atomic mode, queue changes, then **Save as Change**, or open
the **Scheduled** view to preview / apply / cancel / revert.

## Docker Compose (Production Testing)

The `examples/` directory contains a Docker Compose configuration for testing production builds:

```bash
cd examples
docker compose up -d --build   # Start all services
docker compose logs -f         # View logs
docker compose down -v         # Stop and clean up
```

| Service | URL |
|---------|-----|
| Web UI | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| BIND DNS | localhost:15353 |

## Running Tests

Backend tests use [pytest](https://pytest.org/). Integration tests require Docker (they spin up BIND via testcontainers).

```bash
uv run pytest                        # All tests
uv run pytest tests/unit/            # Unit tests only (no Docker)
uv run pytest -m "not integration"   # Skip integration tests
uv run pytest --cov=dns_zone_manager # With coverage
```

### Frontend Tests

```bash
npm run test              # Run unit tests
npm run test:watch        # Watch mode
npm run test:coverage     # With coverage
```

### E2E Tests (Playwright)

E2E projects cover Chromium, Firefox, WebKit (Desktop Safari), and an iPad
viewport (also WebKit). Browsers and their OS dependencies are installed in
the image; `postCreateCommand` runs `npx playwright install --with-deps` so a
rebuild or Playwright version bump picks up any missing browser.

```bash
npm run test:e2e          # Run E2E tests
npm run test:e2e:ui       # Playwright UI at http://localhost:9323
npm run test:e2e:headed   # Run with visible browser
npm run test:e2e:report   # View test report at http://localhost:9324

# Single browser / device project
npx playwright test --project=webkit
npx playwright test --project=ipad
```

## CI/CD (GitHub Actions)

Workflows live in `.github/workflows/`.

### On every push and pull request (`ci.yml`)

Four parallel jobs:

1. **Pre-commit checks** — `uv run pre-commit run --all-files` (ruff, ruff-format, ty, tsc)
2. **Backend unit tests** — `pytest -m "not integration"`
3. **Backend integration tests** — `pytest tests/integration/` (BIND via testcontainers; needs Docker on the runner)
4. **Frontend unit tests** — `npm run test` (vitest)

Playwright E2E is **not** run in CI. Run it locally with a live stack:

```bash
./tests.sh --all          # unit + integration + Playwright (needs servers)
npm run test:e2e          # Playwright only (needs backend + BIND + vite)
```

### On a version tag (`release.yml`)

Pushing a tag matching `v*` (e.g. `v0.4.0`):

1. Runs the full CI suite (via `workflow_call`)
2. Builds and pushes container images to GHCR:
   - `ghcr.io/davidgroves/dns-zone-manager/backend:<tag>` (+ `latest`)
   - `ghcr.io/davidgroves/dns-zone-manager/frontend:<tag>` (+ `latest`)
3. Builds the Python sdist/wheel (`uv build`)
4. Creates a GitHub Release with generated notes and the dist files attached

```bash
git tag v0.4.0
git push origin v0.4.0
```

#### One-time: make GHCR packages public

The first release creates the packages as private. In the GitHub repo → **Packages**, open each of `backend` and `frontend`, then **Package settings** → **Change visibility** → Public.

### Optional: branch protection

In GitHub repo settings → Branches, require pull requests and the CI status checks before merging to `main`.

## Dependency Updates (Renovate)

Renovate (GitHub App) opens daily dependency update PRs for Python (`uv`) and npm packages. Configuration lives in `renovate.json5`. PRs require manual review and merge (`automerge: false`).

## Code Quality

### Pre-commit Hooks

Configured hooks run automatically on commit:
- **ruff check** — Linting with auto-fix
- **ruff format** — Code formatting
- **ty** — Type checking

```bash
uv run pre-commit run --all-files  # Run manually
```

### Manual Commands

```bash
# Linting
uv run ruff check backend tests
uv run ruff check --fix backend tests
uv run ruff format backend tests

# Type checking
uv run ty check backend
```
