# DNS Zone Editor Demo Guide

This guide covers the key features to demonstrate in a DNS Zone Editor demo.

## Prerequisites

Start the development environment before the demo using the devcontainer:

1. Open the project in VS Code or Cursor
2. Click "Reopen in Container" when prompted
3. Run the "Start All (Devcontainer)" task (`Ctrl+Shift+P` → "Tasks: Run Build Task")

This starts:
- **BIND DNS server** at hostname `bind:15353`
- **FastAPI backend** on http://localhost:8000
- **Vite frontend** on http://localhost:5173

Login with API key: `demo-api-key-12345`

---

## Demo Checklist

Use this checklist during your demo:

- [ ] **Login** with API key
- [ ] **Browse zones** - show sidebar, filtering, pagination
- [ ] **Show catalog badge** on zones from catalog
- [ ] **Add a record** - show type dropdown, examples
- [ ] **Edit a record** - change TTL or values
- [ ] **Delete a record** - show confirmation
- [ ] **Search** - within zone and globally
- [ ] **Filter by type** - use dropdown without search text
- [ ] **Reverse PTR** - create from an A record
- [ ] **Atomic mode** - queue multiple changes, submit together
- [ ] **History** - view changes, explain IXFR limitations
- [ ] **OpenAPI docs** - show Swagger UI
- [ ] **Health endpoint** - show cache stats
- [ ] **Metrics endpoint** - show Prometheus metrics
- [ ] **Logs** - show WIDE events in FastAPI console
- [ ] **IDN domains** - show punycode decoding and UTF-8 display

---

## 1. Catalog Zone Integration

The application supports **RFC 9432 Catalog Zones** for automatic zone discovery.

### What is a Catalog Zone?

A catalog zone is a special DNS zone that lists other zones. When configured:
- The API automatically discovers zones from the catalog
- New zones added to the catalog appear automatically in the UI
- Removed zones are automatically cleaned up

### Demonstrating Catalog Zones

1. **Show the catalog status indicator** in the sidebar:
   - Look for "Catalog: X zones" with a green/yellow status dot
   - Click the refresh button to manually sync

2. **Explain the configuration** (from `examples/config.example.yaml`):
   ```yaml
   catalog:
     enabled: true
     zone_name: catalog.example.
     poll_interval: 300
     notify_udp_port: 5354
   ```

3. **Show zones with CAT badge** - these came from the catalog automatically

### How It Works

```
┌─────────────────┐      ┌──────────────┐      ┌────────────────┐
│  BIND Server    │      │  Catalog     │      │  DNS API       │
│                 │ ──── │  Indexer     │ ──── │  Zone Cache    │
│  catalog.zone   │      │  (watches)   │      │  (auto-loads)  │
└─────────────────┘      └──────────────┘      └────────────────┘
```

---

## 2. Adding Records

### Basic Record Creation

1. **Select a zone** (e.g., `example.com`)
2. Click **"Add Record"** button
3. Fill in the form:
   - **Name**: `www` (relative) or `www.example.com.` (FQDN)
   - **Type**: Select from common types or "Custom type..." for any DNS type
   - **TTL**: e.g., `3600`
   - **Records**: One per line (e.g., `192.0.2.100`)
4. Click **Create**

### Key Points to Highlight

- **Type dropdown** includes all common types (A, AAAA, CNAME, MX, TXT, etc.)
- **Custom type** option allows ANY DNS type (TYPE65, DNAME, etc.)
- **Multiple values** supported - just enter one per line
- **Type-specific examples** shown below the records field
- **Immediate effect** - changes are live in DNS instantly

### Behind the Scenes

The backend sends a DDNS UPDATE (RFC 2136) with TSIG authentication after getting a message like:
```
POST /zones/example.com/rrsets
{
  "name": "www",
  "type": "A",
  "ttl": 3600,
  "records": ["192.0.2.100"]
}
```

---

## 3. Deleting Records

1. **Find the record** in the zone view
2. Click the **trash icon** on the right
3. **Confirm deletion** in the modal

### Demonstrating Delete Safety

- The delete confirmation shows the record details
- Uses DDNS prerequisites (`YXRRSET`) to ensure the record still has the expected values
- If someone else modified it, you get a conflict error

---

## 4. Reverse PTR Records

The application can automatically create PTR records for forward A/AAAA records.

### Demo Steps (using example.com)

1. **Select example.com zone**
2. Find an **A or AAAA record** (look for a host with IP addresses)
3. Click the **reverse arrow icon** (↔) next to the record
4. The **Reverse PTR modal** opens showing:
   - Each IP address and its computed PTR name
   - Which reverse zones are managed by this system
   - Any existing PTR records
5. **Select IPs** to create PTRs for
6. Choose mode if PTRs exist:
   - **Replace**: Overwrite existing PTR
   - **Add round-robin**: Add to existing PTRs
7. Click **Create PTRs**

### What Gets Created

For IP `192.0.2.100` pointing to `www.example.com.`:
- PTR name: `100.2.0.192.in-addr.arpa.`
- PTR value: `www.example.com.`

### Requirements

You must have the reverse zone (e.g., `2.0.192.in-addr.arpa.`) loaded in the system.

---

## 5. Atomic Operations

The **Atomic mode** allows queuing multiple changes and applying them as a single DNS transaction.

### Demo Steps

1. Click the **Atomic button** (atom icon) in the header - it turns green when active
2. **Make changes** - they queue instead of applying immediately:
   - Add a record → "Add queued (1 pending)"
   - Edit another → "Replace queued (2 pending)"
   - Delete one → "Delete queued (3 pending)"
3. Click the **queue indicator** showing "3 pending"
4. Review all changes in the **Atomic Changes modal**
5. Click **Submit All** - all changes apply as one transaction

### Why Atomic Mode?

- **All-or-nothing**: If any operation fails, none apply
- **Consistent state**: No partial updates visible to DNS clients
- **Complex changes**: Rename records by deleting old + adding new atomically

### Behind the Scenes

Uses the `/zones/{zone}/atomic` endpoint:
```json
POST /zones/example.com/atomic
{
  "operations": [
    {"action": "add", "name": "new", "type": "A", ...},
    {"action": "delete", "name": "old", "type": "A", ...}
  ]
}
```

---

## 6. Zone History (IXFR)

View the change history of a zone using **Incremental Zone Transfer (IXFR)**.

### Demo Steps

1. Select a zone
2. Click **"History"** button
3. The modal shows:
   - **Current serial** and **available history from** serial
   - **Change batches** grouped by serial transitions
4. Click a batch to expand and see:
   - Records added (green `+`)
   - Records deleted (red `-`)
5. **Rollback**: Click "Rollback to X" to preview reverting changes

### Limitations (Important to Explain)

⚠️ **IXFR limitations**:

1. **Server must keep journal files** - BIND needs `ixfr-from-differences yes`
2. **Journal can be lost** - Restarting BIND or running `rndc sync -clean` clears it
3. **Limited history depth** - Only changes since the journal started
4. **Server may respond with AXFR** - If history isn't available, you see `is_full_axfr: true`

---

## 7. Cache Size Feature

The application maintains an in-memory cache of zone data with configurable size limits.

### Show in /health Endpoint

Visit http://localhost:8000/health to see:
```json
{
  "status": "healthy",
  "cache": {
    "zones_cached": 15,
    "total_records": 1250,
    "size_bytes": 524288,
    "max_size_bytes": 1073741824
  }
}
```

### Configuration

```bash
# Maximum cache size (default 1GB, 0 = unlimited)
CACHE_MAX_SIZE_BYTES=1073741824
```

### LRU Eviction

When cache exceeds max size:
- Least-recently-used zones are evicted
- Tracked via Prometheus: `dns_zone_manager_cache_evictions_total`

---

## 8. Backend OpenAPI Documentation

The FastAPI backend provides interactive API documentation.

### URLs to Demo

| URL | Description |
|-----|-------------|
| http://localhost:8000/docs | **Swagger UI** - Interactive API explorer |
| http://localhost:8000/redoc | **ReDoc** - Alternative documentation |
| http://localhost:8000/openapi.json | Raw OpenAPI spec |

### Swagger UI Features

1. **Try it out** - Execute API calls directly
2. **Authentication** - Click "Authorize" and enter API key
3. **Request/Response schemas** - Full type documentation
4. **Example values** - Pre-filled request bodies

---

## 9. WIDE Event Logging

The backend uses the **"Wide Event" pattern** for structured logging.

### What is a Wide Event?

Instead of many small log lines, each HTTP request produces **one comprehensive JSON event** containing all context:

```json
{
  "request_id": "req_abc123def456",
  "timestamp": "2024-01-24T12:00:00Z",
  "service": "dns-zone-manager",
  "request": {
    "method": "POST",
    "path": "/zones/example.com/rrsets",
    "client_ip": "192.168.1.100"
  },
  "user": {
    "id": "alice",
    "auth_type": "api_key"
  },
  "dns": {
    "operation": "add_rrset",
    "zone": "example.com.",
    "name": "www",
    "rdtype": "A",
    "ttl": 3600
  },
  "response": {
    "status_code": 201,
    "duration_ms": 45.2
  }
}
```

### Configuration

```bash
LOG_FORMAT=json          # 'json' for production, 'text' for development
LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
LOG_SAMPLE_RATE=0.1      # Sample 10% of successful GETs
LOG_SLOW_THRESHOLD_MS=1000  # Always log slow requests
```

### Demo: Watch the Logs

In the tmux session, switch to window 1 (FastAPI) to see logs as you interact with the UI.

---

## 10. Prometheus Metrics

The application exposes Prometheus metrics for monitoring.

### Metrics Endpoint

Visit http://localhost:8000/metrics to see:

```prometheus
# Authentication
dns_zone_manager_logins_total{auth_type="api_key"} 5
dns_zone_manager_logouts_total 2

# Operations
dns_zone_manager_rrset_adds_total{zone="example.com."} 10
dns_zone_manager_rrset_deletes_total{zone="example.com."} 3
dns_zone_manager_rrset_replaces_total{zone="example.com."} 7

# DDNS outcomes
dns_zone_manager_ddns_updates_successful_total 18
dns_zone_manager_ddns_updates_failed_total{reason="prereq_failed"} 2

# Zone transfers
dns_zone_manager_zone_transfers_total{method="axfr",zone="example.com."} 5
dns_zone_manager_zone_transfers_total{method="ixfr",zone="example.com."} 12

# Cache
dns_zone_manager_cache_size_bytes 524288
dns_zone_manager_cache_evictions_total 0

# Search
dns_zone_manager_zone_searches_total{zone="example.com."} 15
dns_zone_manager_global_searches_total 3
```

### Key Metrics to Highlight

| Metric | Purpose |
|--------|---------|
| `dns_zone_manager_ddns_updates_*` | Track DNS update success/failure |
| `dns_zone_manager_cache_size_bytes` | Monitor memory usage |
| `dns_zone_manager_zone_transfers_total` | Track AXFR/IXFR frequency |
| `dns_zone_manager_logins_total` | Usage tracking |

---

## 11. Authentication Methods

The application supports two authentication methods.

### API Key Authentication

Simple header-based authentication:

```bash
curl -H "X-API-Key: demo-api-key-12345" http://localhost:8000/zones
```

Configuration:
```bash
API_KEY_AUTH_ENABLED=true
API_KEYS=alice:secret-key-1,bob:secret-key-2
```

- **Named keys**: Each key has an identity for audit logs
- **Multiple keys**: Support different users/services

### Azure AD Authentication (Optional)

OAuth2/OIDC authentication with Azure Active Directory:

1. User clicks "Sign in with Azure AD"
2. Redirects to Microsoft login
3. Returns with token
4. Backend validates token against Azure AD

Configuration:
```bash
AZURE_AD_AUTH_ENABLED=true
AZURE_AD_TENANT_ID=your-tenant-id
AZURE_AD_CLIENT_ID=your-app-client-id
```

### Demo: Show Both Options

On the login screen, you'll see:
- Azure AD button (if enabled)
- API Key input field

---

## 12. IDN (Internationalized Domain Names)

The application fully supports **Internationalized Domain Names (IDN)** with automatic punycode decoding and UTF-8 display.

### What is IDN?

IDN allows domain names in non-ASCII scripts (Chinese, Arabic, Cyrillic, etc.). These are stored as **punycode** (e.g., `xn--0zwm56d`) but displayed as UTF-8 (e.g., `测试`).

### Demo Zones

The development environment includes two IDN test zones:

1. **`idn.test`** - Contains IDN subdomains in multiple scripts
2. **`xn--l3caihh2b1dya9nsa`** (โดเมนทดสอบ) - A Thai IDN zone name

### Demo Steps

#### 1. Show IDN Zone Names

1. Look in the **sidebar zone list** for `xn--l3caihh2b1dya9nsa`
2. Notice the **🌐 globe badge** next to it
3. **Hover** over the zone name to see the UTF-8 tooltip: `โดเมนทดสอบ` (Thai: "test domain")
4. **Click the 🌐 badge** to copy the UTF-8 name to clipboard

#### 2. Show IDN Record Names

1. **Select the `idn.test` zone**
2. Scroll through records to see punycode names with 🌐 badges:
   - `xn--0zwm56d` → 测试 (Chinese: "test")
   - `xn--kgbechtv` → إختبار (Arabic: "test")
   - `xn--e1afmkfd` → тест (Russian: "test")
   - `xn--zckzah` → テスト (Japanese: "test")
   - `xn--jxalpdlp` → δοκιμή (Greek: "test")

3. **Hover** over any punycode name to see the UTF-8 version
4. **Click the 🌐 badge** to copy

#### 3. Show UTF-8 in Record Data

1. Look at **TXT records** in the `idn.test` zone
2. Some contain escaped UTF-8 sequences (shown as `\231\189\145` etc.)
3. These also show a 🌐 badge
4. **Hover** to see the decoded UTF-8: `"Chinese: 网站 (website)"`

### Visual Indicators

| Indicator | Meaning |
|-----------|---------|
| 🌐 badge on zone name | Zone name is IDN (punycode) |
| 🌐 badge on record name | Record FQDN contains IDN labels |
| 🌐 badge on record data | Record data contains UTF-8 sequences |
| Dotted underline | Hover to see UTF-8 tooltip |

### Behind the Scenes

The API automatically detects and decodes:

1. **Punycode labels** (`xn--*`) in zone names and record names
   - Uses IDNA2008 standard via the `idna` library
   - Returns both punycode and UTF-8 in API responses

2. **Escaped UTF-8** (`\DDD` sequences) in record data
   - BIND escapes non-ASCII bytes as decimal: `\231\189\145`
   - API decodes these to UTF-8: `网`

### API Response Example

```json
{
  "zone": "xn--l3caihh2b1dya9nsa.",
  "zone_is_idn": true,
  "zone_utf8": "โดเมนทดสอบ.",
  "rrsets": [
    {
      "name": "xn--l3cfk7dp.xn--l3caihh2b1dya9nsa.",
      "name_is_idn": true,
      "name_utf8": "ทดสอบ.โดเมนทดสอบ.",
      "type": "TXT",
      "records": ["\"Thai: \\224\\184\\151\\224\\184\\148\\224\\184\\170\\224\\184\\173\\224\\184\\154\""],
      "records_is_utf8": true,
      "records_utf8": ["\"Thai: ทดสอบ\""]
    }
  ]
}
```

### Supported Scripts

The development environment includes examples in:

| Script | Example | Punycode |
|--------|---------|----------|
| Chinese (Han) | 测试, 中文, 网站 | xn--0zwm56d, xn--fiq228c, xn--5tzm5g |
| Arabic | إختبار, مصر | xn--kgbechtv, xn--wgbh1c |
| Russian (Cyrillic) | сайт, рф | xn--80aswg, xn--p1ai |
| Japanese (Katakana) | テスト, 日本 | xn--zckzah, xn--wgv71a |
| Greek | δοκιμή, ελ | xn--jxalpdlp, xn--qxam |
| Thai | โดเมนทดสอบ, ทดสอบ | xn--l3caihh2b1dya9nsa, xn--l3cfk7dp |

### Searching IDN

When searching, you can use either:
- **Punycode**: Search for `xn--0zwm56d`
- **The pattern will match** the punycode stored in DNS

Note: Direct UTF-8 search is not yet supported (would require encoding to punycode first).

---

## Testing

### Test Structure

The project has four types of tests:

| Type | Location | Framework | Requirements |
|------|----------|-----------|--------------|
| Backend Unit | `tests/unit/` | pytest | None |
| Backend Integration | `tests/integration/` | pytest | Docker (auto-starts BIND) |
| Frontend Unit | `frontend/__tests__/` | vitest | None |
| Frontend E2E | `frontend/__tests__/e2e/` | Playwright | Devcontainer with servers running |

### Running Tests

```bash
# All backend tests
uv run pytest

# Unit tests only (fast, no Docker)
uv run pytest tests/unit/

# Skip integration tests
uv run pytest -m "not integration"

# Frontend unit tests
npm run test

# E2E tests (requires dev servers running)
npm run test:e2e
```

### Playwright E2E Tests

End-to-end tests verify the full stack using a real browser.

#### Running E2E Tests

```bash
# Headless (CI mode)
npm run test:e2e

# With visible browser
npm run test:e2e:headed

# Interactive UI mode
npm run test:e2e:ui
```

#### Interactive Playwright UI

The **Playwright UI** (`npm run test:e2e:ui`) is powerful for development:

1. **Run it**: `npm run test:e2e:ui`
2. **Browser opens** with the Playwright Test interface
3. Features:
   - **Test explorer**: Click tests to run them individually
   - **Watch mode**: Tests re-run when you save files
   - **Time-travel debugging**: Step through test execution
   - **DOM snapshots**: See the page at each step
   - **Trace viewer**: Network requests, console logs, screenshots

#### Writing E2E Tests

Tests are in `frontend/__tests__/e2e/*.spec.ts`:

```typescript
import { expect, test } from '@playwright/test';

test('should filter by record type', async ({ page }) => {
  // Login
  await page.goto('/');
  await page.fill('input[placeholder="Enter your API key"]', 'demo-api-key-12345');
  await page.click('button:has-text("Sign in")');
  
  // Select zone
  await page.click('.zone-item:has-text("example.com")');
  await expect(page.locator('.card table')).toBeVisible();
  
  // Filter by type
  await page.selectOption('.header-actions select', 'A');
  
  // Verify results
  const types = page.locator('.record-type');
  for (const type of await types.all()) {
    expect(await type.textContent()).toBe('A');
  }
});
```

#### Debugging Failed Tests

When a test fails:
1. **Screenshot** saved in `test-results/`
2. **Trace file** (on retry) - view with `npx playwright show-trace <path>`
3. **Error context** in markdown file

To debug interactively:
```bash
# Run specific test with debug
npx playwright test --debug "test name"
```

---

---

## Troubleshooting

### Common Issues

| Problem | Solution |
|---------|----------|
| "Zone not found" | Zone not in catalog or not manually added |
| "Prerequisite failed" | Record was modified externally - refresh and retry |
| No history available | BIND journal cleared or `ixfr-from-differences` not enabled |
| Type filter shows no results | Fixed in search.ts - now sends wildcard pattern |

### Useful Commands (in devcontainer)

```bash
# Query DNS directly
dig @bind -p 15353 www.example.com A

# Zone transfer
dig @bind -p 15353 example.com AXFR -k /etc/dns-api-key.conf

# Check BIND status
rndc status

# View API health
curl http://localhost:8000/health | jq
```

