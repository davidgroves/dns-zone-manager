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

# Scheduled changes (intent store — not zone state)
scheduler:
  enabled: true
  database_path: /var/lib/dns-zone-manager/scheduler.db
  poll_interval: 10
  max_attempts: 3
  retry_backoff: 60
  lease_ttl: 120
  default_expiry_window: 3600

logging:
  format: json
  level: INFO
```

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

# Logging
LOG_FORMAT=json
LOG_LEVEL=INFO
```

Scheduled changes store **intent** (what to apply later) in SQLite, not zone state. After a change is `applied`, you can `POST /v1/scheduled-changes/{id}/revert` to undo it immediately if a pre-apply snapshot was captured; status becomes `reverted`. Changes applied before snapshot support cannot be reverted.

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