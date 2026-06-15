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

```bash
npm run test:e2e          # Run E2E tests
npm run test:e2e:ui       # Playwright UI at http://localhost:9323
npm run test:e2e:headed   # Run with visible browser
npm run test:e2e:report   # View test report at http://localhost:9324
```

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
