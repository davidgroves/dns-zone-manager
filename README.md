# DNS Zone Manager

[![CI](https://github.com/davidgroves/dns-zone-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/davidgroves/dns-zone-manager/actions/workflows/ci.yml)

A FastAPI-based DNS record management API that communicates with BIND (or another standards compliant DNS server) via DDNS updates (with TSIG authentication) and AXFR zone transfers.

## Features

- **Full DNS Record Type Support**: Supports all IANA-registered record types for the IN class
- **DDNS Updates**: RFC 2136 compliant dynamic updates with TSIG authentication
- **AXFR Zone Transfers**: Full zone synchronization for caching
- **Consistency Guarantees**: PREREQ-based consistency checks ensure updates are atomic, and supports multi-user and multi-update-method situations.
- **Dual Authentication**: Supports both Azure AD and API key authentication
- **CLI Tool**: Optional command-line interface for scripting and automation
- **YAML Configuration**: Configure via YAML file or environment variables
- **Change Webhooks**: Notify Slack, Microsoft Teams, or any JSON endpoint when DNS changes

## Container images

Published to GitHub Container Registry on each `v*` tag:

```bash
docker pull ghcr.io/davidgroves/dns-zone-manager/backend:latest
docker pull ghcr.io/davidgroves/dns-zone-manager/frontend:latest
```

Pin to a release with the tag (e.g. `:v0.3.0`). See [DEVELOPMENT.md](DEVELOPMENT.md) for the CI/CD pipeline.

## Development

For development, use the devcontainer which provides all required tools:

1. Open in VS Code or Cursor
2. Click "Reopen in Container" when prompted
3. Run "Start All (Devcontainer)" task

See [DEVELOPMENT.md](DEVELOPMENT.md) for full setup instructions.

## Installation (Production)

```bash
# Using uv
uv sync

# Or using pip
pip install -e .

# With CLI support
pip install -e ".[cli]"
```

## Configuration

DNS Zone Manager can be configured via YAML config file, environment variables, or both. When both are used, YAML config takes priority.

### YAML Configuration (Recommended)

Copy `examples/config.example.yaml` to `config.yaml` and customize:

```yaml
dns:
  server: ns1.example.com
  port: 53

tsig:
  name: update-key
  secret: base64-encoded-secret
  algorithm: hmac-sha256

api_key:
  enabled: true
  keys:
    - name: admin
      secret: secret123
    - name: bot
      secret: secret456

cache:
  enabled: true

# Where scheduled changes and the audit log are stored.
# backend: sqlite (default, a local file) or postgres (external server).
# The schema is created automatically, so an empty database is enough.
database:
  backend: sqlite
  path: /var/lib/dns-zone-manager/scheduler.db
  # backend: postgres
  # postgres:
  #   host: postgres
  #   port: 5432
  #   database: dns_zone_manager
  #   user: dns_zone_manager
  #   password: change-me
  #   sslmode: require

# Scheduled changes (intent store — not zone state)
scheduler:
  enabled: true
  poll_interval: 10
  max_attempts: 3
  retry_backoff: 60
  lease_ttl: 120
  default_expiry_window: 3600

# Purge completed changes / audit events by age and/or database size
retention:
  enabled: true
  interval: 3600
  max_age_days: 0          # 0 disables age-based purge
  max_database_mb: 2048    # trim oldest 10% when over this size (0 disables)
  trim_percent: 10
  max_trim_passes: 10
  statuses: [applied, failed, cancelled, expired, reverted]
  vacuum: incremental      # incremental | full | off

# Change notifications to Slack / Teams / any JSON endpoint
webhooks:
  enabled: true
  base_url: https://dns.example.com
  targets:
    - name: slack-dns
      type: slack
      url: https://hooks.slack.com/services/T000/B000/XXXXXXXX

logging:
  format: json
  level: INFO
```

### Frontend Theme / Branding

Optional `theme:` section controls the web UI name, logo, and colour palettes.
Settings are exposed to the SPA via `GET /ui/config` (and `GET /ui/logo` when
using a local logo file). Colour tokens are partial overrides — unset tokens
keep the built-in light/dark defaults. Values must be `#rgb` / `#rrggbb` /
`#rrggbbaa` or a CSS named colour.

```yaml
theme:
  app_name: Acme DNS          # falls back to top-level app_name
  default_mode: dark          # dark | light | auto
  allow_mode_toggle: true
  logo:
    # url: https://cdn.example.com/logo.svg
    path: /etc/dns-zone-manager/logo.svg
    alt: Acme DNS
  dark:
    accent_primary: "#58a6ff"
  light:
    accent_primary: "#0969da"
```

See `examples/config.example.yaml` for the full token list.

### Change Webhooks

When enabled, every committed DNS change posts a notification reporting **who**
made the change, whether it was **manual or scheduled**, and a **link to the
change** in the Scheduled Changes view.

```yaml
webhooks:
  enabled: true
  # Public URL of this application; used to build the change link
  base_url: https://dns.example.com
  # change_applied, change_failed, or both
  events: [change_applied, change_failed]
  targets:
    - name: slack-dns
      type: slack
      url: https://hooks.slack.com/services/T000/B000/XXXXXXXX

    - name: teams-dns
      type: teams
      url: https://prod-00.westeurope.logic.azure.com:443/workflows/.../invoke
      # Optional per-target filters
      zones: ["*.prod.example.com"]
      events: [change_failed]

    - name: audit-sink
      type: generic
      url: https://audit.example.com/dns-events
      auth:
        type: hmac
        secret: a-long-random-string
```

Target types are `slack` (Block Kit message), `teams` (Adaptive Card via a
Power Automate workflow URL), and `generic` (the raw JSON event).

**Authentication.** Slack and Teams URLs are themselves the credential, so
they need no `auth` block and are never written to logs. For `generic`
targets, `auth.type` may be:

| Type | Sends |
|------|-------|
| `none` | nothing (default) |
| `bearer` | `Authorization: Bearer <secret>` |
| `basic` | `Authorization: Basic <base64 of username:password>` |
| `header` | `<header>: <secret>`, default header `X-API-Key` |
| `hmac` | `X-DNS-Signature: sha256=<hex>` over `"<timestamp>.<body>"`, plus `X-DNS-Timestamp` |

HMAC is the strongest option: the receiver recomputes the digest over the
timestamp and raw body, which authenticates the sender and detects tampering.
The timestamp is generated once per event, so a retried delivery keeps a valid
signature and receivers can reject stale timestamps to prevent replay.

Requests must use `https://` unless the target sets `allow_insecure: true`.
Set `verify_tls` to a CA bundle path for endpoints using a private
certificate authority.

**Links to manual changes.** Scheduled changes already have a record to link
to. So that manual record edits do too, `autorecord_manual_changes` (on by
default) records each manual change in the scheduler store as an
already-applied change, visible under the **Manual** source filter in the
Scheduled Changes view. This requires `scheduler.enabled`. Auto-recorded
changes cannot be reverted, since no pre-apply snapshot exists.

Delivery happens on a background worker, so a slow or unreachable endpoint
never delays or fails a DNS write. Failures are retried with exponential
backoff and surfaced in the `webhook_deliveries_total` and
`webhook_queue_dropped_total` metrics.

### Environment Variables

You can also configure via environment variables (useful for Docker/Kubernetes):

```bash
# DNS Server Configuration (required)
DNS_SERVER=ns1.example.com
DNS_PORT=53

# TSIG Key for DDNS Updates (required)
TSIG_KEY_NAME=update-key
TSIG_KEY_SECRET=base64-encoded-secret
TSIG_KEY_ALGORITHM=hmac-sha256

# API Key Authentication
API_KEY_AUTH_ENABLED=true
API_KEYS=admin:secret123,bot:secret456

# Azure AD Authentication (optional)
AZURE_AD_AUTH_ENABLED=false
AZURE_AD_TENANT_ID=your-tenant-id
AZURE_AD_CLIENT_ID=your-client-id

# Cache Settings
CACHE_ENABLED=true

# Scheduler (scheduled DNS changes)
SCHEDULER_ENABLED=true
SCHEDULER_DATABASE_PATH=/var/lib/dns-zone-manager/scheduler.db

# Retention (purge completed changes / audit events)
RETENTION_ENABLED=true
RETENTION_MAX_AGE_DAYS=0
RETENTION_MAX_DATABASE_MB=2048
RETENTION_TRIM_PERCENT=10

# Change webhooks (targets themselves must be configured via YAML)
WEBHOOK_ENABLED=true
WEBHOOK_BASE_URL=https://dns.example.com

# Frontend theme / branding
THEME_DEFAULT_MODE=dark
THEME_ALLOW_MODE_TOGGLE=true
THEME_APP_NAME=Acme DNS

# Storage for scheduled changes
DATABASE_BACKEND=postgres
POSTGRES_HOST=postgres
POSTGRES_DATABASE=dns_zone_manager
POSTGRES_USER=dns_zone_manager
POSTGRES_PASSWORD=change-me

# Logging
LOG_FORMAT=json
LOG_LEVEL=INFO
```

Scheduled changes store **intent** (what to apply later), not zone state. After a change is `applied`, you can `POST /v1/scheduled-changes/{id}/revert` to undo it immediately if a pre-apply snapshot was captured; status becomes `reverted`. Changes applied before snapshot support cannot be reverted.

### Storage Backends

| | SQLite | PostgreSQL |
|---|---|---|
| Setup | None; a local file | An external server |
| Best for | Single instance, evaluation | Several instances, existing database operations |
| Concurrent claiming | One instance | Safe across instances (`FOR UPDATE SKIP LOCKED`) |
| Backups | Copy the file | `pg_dump` |

Either way the schema is created on first start, so pointing at an empty database is all that is required. `examples/docker-compose.yaml` runs a PostgreSQL container configured this way.

See `examples/config.example.yaml` for all available options.

## Running the Server

```bash
# With YAML config file (recommended)
dns-zone-manager --config config.yaml

# Development with auto-reload
dns-zone-manager --config config.yaml --reload

# With environment variables only
dns-zone-manager

# Full options
dns-zone-manager --config config.yaml --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Add RRset
```http
POST /zones/{zone}/rrsets
Content-Type: application/json

{
    "name": "www",
    "ttl": 3600,
    "type": "A",
    "records": ["192.0.2.1", "192.0.2.2"]
}
```

### Delete RRset
```http
DELETE /zones/{zone}/rrsets
Content-Type: application/json

{
    "name": "www",
    "type": "A",
    "records": ["192.0.2.1"]
}
```

### Replace RRset
```http
PUT /zones/{zone}/rrsets
Content-Type: application/json

{
    "name": "www",
    "ttl": 3600,
    "type": "A",
    "records": ["192.0.2.10"]
}
```

## Error Responses

- **409 Conflict**: PREREQ failed (DNS state changed externally)
- **404 Not Found**: Zone or record not found
- **400 Bad Request**: Invalid record data
- **401/403**: Authentication/authorization failures