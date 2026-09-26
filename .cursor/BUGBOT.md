# Bugbot review guidance

DNS Zone Manager project context for automated PR reviews.

## Architecture

- DNS (BIND via DDNS/AXFR) is the source of truth for zone contents. Never treat
  the scheduler database as authoritative zone state.
- The scheduler store (SQLite or PostgreSQL) holds only change *intent* and the
  audit log — what to apply, when, and history. Schema is owned by Alembic under
  `backend/src/dns_zone_manager/scheduler/`.
- The in-memory zone cache is ephemeral and read-only; invalidate/refresh after writes.

## Conventions

- Zone names always end with a trailing dot (e.g. `example.com.`, not `example.com`).
- Backend lives under `backend/src/dns_zone_manager/` (not `backend/dns_zone_manager/`).
- Frontend is Alpine.js templates in `frontend/index.html` plus TypeScript modules —
  not React/Vue.

## Testing expectations

When a PR changes behaviour, prefer accompanying tests in the matching layer:

- Backend unit: `tests/unit/`
- Backend integration (Docker/BIND): `tests/integration/`
- Frontend unit: `frontend/__tests__/` (Vitest)
- Frontend E2E: `frontend/__tests__/e2e/` (Playwright)

Flag behavioural changes that touch API routes, the scheduler store, or DNS write
paths without any new or updated tests.

## Security and ops

- Do not introduce secrets into committed YAML, Docker Compose, or examples beyond
  clearly labelled demo values.
- Prefer structured (WIDE) logging and Prometheus metrics for new operational paths.
- DDNS prerequisites: add uses NXRRSET; delete/replace use YXRRSET.

## Out of scope for noise

- Pure dependency bumps with no application code changes are usually low value for
  deep review (Renovate PRs are also blocked from auto-review in Bugbot settings).
- Typo-only edits in docs need at most a light pass.
