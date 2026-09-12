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

### Scheduled changes (SQLite)

The scheduler stores named/timed change *intent* in SQLite (never zone state). In the
devcontainer this defaults to `.tmp/scheduler.db` (see
`.devcontainer/config.devcontainer.yaml`). For production, set
`scheduler.database_path` to a durable volume and include that file in backups.

```yaml
scheduler:
  enabled: true
  database_path: /var/lib/dns-zone-manager/scheduler.db
```

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
