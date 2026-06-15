# DNS Zone Manager Example Environment

This directory contains a complete Docker Compose environment for testing the DNS Zone Manager with a real BIND server.

## Configuration Files

| File | Purpose |
|------|---------|
| `config.yaml` | Docker Compose configuration (used by docker-compose.yaml) |
| `config.dev.yaml` | Local development configuration (BIND on port 15353) |
| `config.example.yaml` | Full reference config with all options documented |

## Quick Start

```bash
cd examples

# Build and start the environment
docker compose up -d --build

# Check status
docker compose ps

# View logs
docker compose logs -f
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| dns-zone-manager | 8000 | FastAPI DNS Management API |
| dns-zone-manager | 5354 | Catalog Zone NOTIFY listener |
| bind | 15353 | BIND 9 DNS Server |
| bind | 15953 | BIND 9 RNDC control port |

## Access Points

### Web UI

The DNS Zone Editor web interface is available at:

- **Web UI**: http://localhost:8000

Sign in using one of the example API keys: `demo-api-key-12345`

### OpenAPI Documentation (Swagger UI)

Open your browser and navigate to:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### API Endpoints

- **Health Check**: http://localhost:8000/health
- **API Info**: http://localhost:8000/

## Authentication

All API endpoints (except health check) require authentication. Use the API key header:

```
X-API-Key: demo-api-key-12345
```

## Example Zones

The environment comes with three pre-configured zones:

| Zone | Description |
|------|-------------|
| `example.com` | General purpose zone with web, mail, and CDN records |
| `internal.corp` | Internal corporate zone with services and infrastructure |
| `test.local` | Test zone for experimentation |

## Catalog Zone Auto-Discovery

This example environment includes a catalog zone (`catalog.example`) configured per RFC 9432. The catalog zone automatically discovers and loads member zones.

### How It Works

1. BIND hosts a catalog zone that lists all member zones as PTR records
2. The DNS API monitors the catalog zone via AXFR and NOTIFY
3. When zones are added to or removed from the catalog, the API automatically syncs

### Check Catalog Status

```bash
curl -s http://localhost:8000/catalog/status \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### List Discovered Zones

```bash
curl -s http://localhost:8000/catalog/zones \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Force Sync

```bash
curl -s -X POST http://localhost:8000/catalog/sync \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Adding a Zone to the Catalog

To add a new zone via the catalog:

1. Create the zone in BIND (add zone file and named.conf entry)
2. Add a PTR record to the catalog zone:

```bash
curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
zone catalog.example.
update add newzone.zones.catalog.example. 3600 PTR newzone.example.
send
' | jq
```

The API will automatically discover and load the new zone.

## Testing the API

### List Zones

```bash
curl -s http://localhost:8000/zones \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Get Zone Details

```bash
curl -s http://localhost:8000/zones/example.com \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### List RRsets in a Zone

```bash
curl -s "http://localhost:8000/zones/example.com/rrsets" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Get Specific RRset

```bash
curl -s "http://localhost:8000/zones/example.com/rrsets?name=www&type=A" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Add a New Record

```bash
curl -s -X POST http://localhost:8000/zones/test.local/rrsets \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myhost",
    "ttl": 3600,
    "type": "A",
    "records": ["172.16.100.1"]
  }' | jq
```

### Delete a Record

```bash
curl -s -X DELETE http://localhost:8000/zones/test.local/rrsets \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myhost",
    "type": "A"
  }' | jq
```

### Replace a Record

```bash
curl -s -X PUT http://localhost:8000/zones/test.local/rrsets \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test1",
    "ttl": 7200,
    "type": "A",
    "records": ["172.16.200.1", "172.16.200.2"]
  }' | jq
```

## NSUPDATE Examples

The API supports executing nsupdate(1)-formatted commands directly. This is useful for batch operations or when migrating from existing nsupdate scripts.

### Basic Add with Prerequisite

Add a new A record only if it doesn't already exist:

```bash
curl -s -X POST "http://localhost:8000/nsupdate?zone=test.local" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
prereq nxdomain newhost.test.local.
update add newhost.test.local. 3600 A 172.16.50.1
send
' | jq
```

### Multiple Records in One Transaction

```bash
curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
zone test.local.
update add webserver.test.local. 300 A 172.16.60.1
update add webserver.test.local. 300 A 172.16.60.2
update add webserver.test.local. 300 AAAA 2001:db8:abcd::60
send
' | jq
```

### Delete a Record

```bash
curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
zone test.local.
prereq yxdomain test1.test.local.
update delete test1.test.local. A
send
' | jq
```

### Replace Record (Delete + Add)

```bash
curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
zone test.local.
prereq yxrrset roundrobin.test.local. A
update delete roundrobin.test.local. A
update add roundrobin.test.local. 300 A 172.16.70.1
update add roundrobin.test.local. 300 A 172.16.70.2
send
' | jq
```

### Multiple Transactions

Execute multiple independent transactions in one request:

```bash
curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  -d '
zone example.com.
update add batch1.example.com. 300 A 192.0.2.201
send

zone internal.corp.
update add batch2.internal.corp. 300 A 10.0.100.1
send
' | jq
```

### Using nsupdate Files

You can also pipe existing nsupdate files:

```bash
cat << 'EOF' | curl -s -X POST "http://localhost:8000/nsupdate" \
  -H "X-API-Key: demo-api-key-12345" \
  -H "Content-Type: text/plain" \
  --data-binary @- | jq
; Migration script
zone test.local.
; Add new load balancer
prereq nxdomain lb.test.local.
update add lb.test.local. 300 A 172.16.80.1
update add lb.test.local. 300 A 172.16.80.2
send
EOF
```

### Supported Commands

| Command | Description |
|---------|-------------|
| `zone <name>` | Set target zone |
| `prereq nxdomain <name>` | Name must not exist |
| `prereq yxdomain <name>` | Name must exist |
| `prereq nxrrset <name> [class] <type>` | RRset must not exist |
| `prereq yxrrset <name> [class] <type> [data]` | RRset must exist |
| `update add <name> <ttl> [class] <type> <data>` | Add record |
| `update delete <name> [class] [type] [data]` | Delete record(s) |
| `send` | Execute transaction |

**Notes:**
- Lines starting with `;` or `#` are comments
- `server` and `key` commands are ignored (API uses its own configuration)
- Class defaults to `IN`

## Search Examples

### Search by Name Pattern (Regex)

Find all records starting with "www":

```bash
curl -s "http://localhost:8000/zones/example.com/search?name_pattern=^www" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

Find all records containing "foo":

```bash
curl -s "http://localhost:8000/zones/test.local/search?name_pattern=.*foo.*" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Search by Record Type

Find all MX records:

```bash
curl -s "http://localhost:8000/zones/example.com/search?name_pattern=.*&type=MX" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

Find all CNAME records:

```bash
curl -s "http://localhost:8000/zones/example.com/search?name_pattern=.*&type=CNAME" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Search by Value Pattern

Find all records pointing to IPs starting with "192.0.2":

```bash
curl -s "http://localhost:8000/zones/example.com/search?value_pattern=^192\.0\.2\." \
  -H "X-API-Key: demo-api-key-12345" | jq
```

Find CNAMEs pointing to CDN:

```bash
curl -s "http://localhost:8000/zones/example.com/search?type=CNAME&value_pattern=.*cdn.*" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

### Global Search (All Zones)

Search across all cached zones:

```bash
curl -s "http://localhost:8000/search?name_pattern=.*api.*" \
  -H "X-API-Key: demo-api-key-12345" | jq
```

## Direct DNS Queries

You can also query the BIND server directly:

```bash
# Query A record
dig @localhost -p 15353 www.example.com A +short

# Query MX record
dig @localhost -p 15353 example.com MX +short

# Query all records for a name
dig @localhost -p 15353 example.com ANY

# Zone transfer (AXFR)
dig @localhost -p 15353 example.com AXFR
```

## Troubleshooting

### Architecture Issues (Apple Silicon / ARM)

If you're on Apple Silicon (M1/M2/M3) and see errors about architecture mismatch like "trying to get linux/amd64 instead of linux/arm64", try these fixes:

```bash
# Clear any cached images of the wrong architecture
docker image rm internetsystemsconsortium/bind9:9.20

# Pull fresh with explicit platform
docker pull --platform linux/arm64 internetsystemsconsortium/bind9:9.20

# Then start the environment
docker compose up -d --build
```

Alternatively, you can set the default platform for your shell session:

```bash
export DOCKER_DEFAULT_PLATFORM=linux/arm64
docker compose up -d --build
```

### Check Service Health

```bash
# API health
curl -s http://localhost:8000/health | jq

# BIND status
docker compose exec bind named -V
```

### View Logs

```bash
# All services
docker compose logs -f

# Just the API
docker compose logs -f dns-zone-manager

# Just BIND
docker compose logs -f bind
```

### Restart Services

```bash
# Restart everything
docker compose restart

# Restart just the API
docker compose restart dns-zone-manager
```

### Reset Environment

```bash
# Stop and remove containers
docker compose down

# Remove volumes and start fresh
docker compose down -v
docker compose up -d
```

## Configuration

### API Keys

API keys use a `name:secret` format where the name is used for audit logging:

| Name | Secret | Usage |
|------|--------|-------|
| `admin` | `demo-api-key-12345` | Full access key |
| `readonly` | `another-key-67890` | Secondary key |

When making API requests, use the **secret** value in the `X-API-Key` header:

```bash
curl -H "X-API-Key: demo-api-key-12345" http://localhost:8000/zones
```

The **name** (e.g., "admin") appears in audit logs to identify who made changes.

To modify keys, edit the `API_KEYS` environment variable in `docker-compose.yaml`:

```
API_KEYS=admin:your-secret-key,ci-bot:another-key
```

### TSIG Key

The TSIG key for DDNS authentication is pre-configured. If you need to regenerate it:

```bash
# Generate a new key
docker compose exec bind tsig-keygen -a hmac-sha256 dns-api-key

# Update both named.conf and docker-compose.yaml with the new secret
```

### Adding New Zones

1. Create a new zone file in `bind/zones/`
2. Add the zone configuration to `bind/named.conf`
3. Restart the bind container: `docker compose restart bind`

## Cleanup

```bash
# Stop and remove containers
docker compose down

# Also remove volumes
docker compose down -v
```

