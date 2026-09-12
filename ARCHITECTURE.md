# Architecture

This document describes the system architecture of the DNS Record Management API, with a focus on the design decisions that make DNS itself the single source of truth.

## Table of Contents

- [Overview](#overview)
- [Core Design Principle: DNS as the Source of Truth](#core-design-principle-dns-as-the-source-of-truth)
- [Why Not a Database?](#why-not-a-database)
- [System Components](#system-components)
- [Data Flow](#data-flow)
- [Consistency Model](#consistency-model)
- [Deployment Topology](#deployment-topology)
- [Security Model](#security-model)

## Overview

This system provides a RESTful API and web interface for managing DNS records. It communicates directly with a DNS server (typically BIND) using standard DNS protocols:

- **AXFR** (RFC 5936) — Full zone transfers for reading zone data
- **DDNS** (RFC 2136) — Dynamic DNS updates for modifications
- **TSIG** (RFC 2845) — Transaction signatures for authentication

```mermaid
flowchart LR
    subgraph Clients
        UI[Web UI]
        CLI[CLI Tools]
        Auto[Automation]
    end

    subgraph "DNS Management API"
        API[FastAPI Backend]
        Cache[Zone Cache]
    end

    subgraph "DNS Infrastructure"
        HP[Hidden Primary]
        S1[Secondary 1]
        S2[Secondary 2]
    end

    UI --> API
    CLI --> API
    Auto --> API
    
    API <-->|DDNS + TSIG| HP
    API <-->|AXFR + TSIG| HP
    
    HP -->|Zone Transfer| S1
    HP -->|Zone Transfer| S2
```

## Core Design Principle: DNS as the Source of Truth

**The DNS server is the authoritative source of all zone and record data.** This is a deliberate architectural decision with significant benefits.

### The Hidden Primary Pattern

In a typical production DNS deployment, the architecture looks like this:

```mermaid
flowchart TB
    subgraph "Management Layer"
        API[DNS Management API]
    end
    
    subgraph "Hidden Primary"
        HP[Primary DNS Server<br/>Not publicly accessible]
    end
    
    subgraph "Public DNS"
        S1[Secondary NS1<br/>Public]
        S2[Secondary NS2<br/>Public]
        S3[Secondary NS3<br/>Public]
    end
    
    subgraph "Internet"
        R1[Resolvers]
        R2[End Users]
    end
    
    API <-->|DDNS/AXFR<br/>Private Network| HP
    HP -->|AXFR/IXFR<br/>Zone Transfers| S1
    HP -->|AXFR/IXFR<br/>Zone Transfers| S2
    HP -->|AXFR/IXFR<br/>Zone Transfers| S3
    
    R1 --> S1
    R1 --> S2
    R2 --> S3
```

The **hidden primary** is a DNS server that:
- Is not listed in NS records
- Is not publicly accessible
- Receives all updates via DDNS
- Propagates changes to public secondaries via zone transfer

This API is designed to work with this pattern. It sends updates to the hidden primary, which then propagates to the public-facing servers.

### What This Means in Practice

1. **No separate database** — Zone data is stored only in DNS
2. **No synchronization problems** — There's nothing to sync
3. **Standard DNS replication** — Changes propagate via normal zone transfers
4. **Any DNS client works** — The data is accessible via standard DNS queries

## Why Not a Database?

Many DNS management systems take a different approach: storing zone data in a database, then generating zone files or pushing updates to DNS servers. This can create significant problems.

### The Dual Source of Truth Problem

```mermaid
flowchart TB
    subgraph "Database-Backed Approach (Problematic)"
        DB[(Database)]
        Sync[Sync Process]
        DNS1[DNS Server]
    end
    
    DB -->|Generate zones| Sync
    Sync -->|Push| DNS1
    
    DB -.-|"What if these<br/>get out of sync?"| DNS1
```

When you have a database AND DNS servers containing zone data:

| Problem | Description |
|---------|-------------|
| **Sync failures** | Network issues, crashes, or bugs can cause database and DNS to diverge |
| **Split-brain** | Which source is correct when they disagree? |
| **Manual changes** | Direct DNS edits bypass the database entirely |
| **Complexity** | Need sync jobs, reconciliation, conflict resolution |
| **Latency** | Changes must propagate through multiple systems |

### The DNS-Native Approach (This System)

```mermaid
flowchart TB
    subgraph "DNS-Native Approach (This System)"
        API2[Management API]
        Cache2[Read Cache<br/>Ephemeral]
        DNS2[DNS Server<br/>Source of Truth]
    end
    
    API2 <-->|DDNS updates| DNS2
    API2 <-->|AXFR reads| DNS2
    DNS2 -->|Populates| Cache2
    
    DNS2 -.-|"Single source<br/>of truth"| DNS2
```

Benefits of treating DNS as the source of truth:

| Benefit | Description |
|---------|-------------|
| **Single source of truth** | DNS server is always authoritative |
| **No sync needed** | The API reads from and writes to the same place |
| **Atomic updates** | DDNS updates are processed atomically by the DNS server |
| **Standard protocols** | Works with any RFC-compliant DNS server |
| **Operational simplicity** | No database to manage, back up, or scale for *zone state* |
| **Direct queries work** | `dig` and other tools see the same data as the API |

### Intent Versus State: Scheduled Changes

Zone **state** (what records exist now) still lives only in DNS. Named, time-scheduled changes are **intent** (what to do later) — they have no representation in a zone until they execute. For that reason the scheduler persists intent in a local SQLite file:

| Stored in SQLite | Still only in DNS |
|------------------|-------------------|
| Change name, schedule, expiry | Current RRsets |
| Operations and prerequisites | Serial / SOA |
| Audit trail of apply/fail | Authoritative answers |

SQLite is never used as a dual source of truth for zone contents. After a change applies, the authoritative result is whatever the DNS server accepted via DDNS; the cache is refreshed from DNS as usual.

Applied changes can later be **reverted** if a pre-apply snapshot was captured. At apply time the executor records prior RRset state (`prior_ttl` / `prior_records` / `snapshot_at`) for each operation. Revert builds inverse ADD/DELETE operations and sends them immediately; status becomes terminal `reverted`. Changes applied before snapshot support (missing `snapshot_at`) cannot be reverted.

### The Cache is Not a Source of Truth

This system maintains an in-memory cache of zone data for performance. This cache is:

- **Ephemeral** — Lost on restart, rebuilt from DNS
- **Read-only copy** — Never authoritative
- **Automatically refreshed** — Based on SOA refresh timers and notify messages.
- **Invalidated on writes** — Updated optimistically, refreshed if inconsistent

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Cache
    participant DNS as DNS Server

    Note over Cache,DNS: Cache is populated via AXFR
    
    Client->>API: GET /zones/example.com/rrsets
    API->>Cache: Read from cache
    Cache-->>API: Return cached data
    API-->>Client: Zone records
    
    Note over Client,DNS: Writes go directly to DNS
    
    Client->>API: POST /zones/example.com/rrsets
    API->>DNS: DDNS Update + TSIG
    DNS-->>API: Success
    API->>Cache: Optimistic update
    API-->>Client: Success
    
    Note over Cache,DNS: Cache refresh on SOA timer
    
    Cache->>DNS: AXFR (periodic)
    DNS-->>Cache: Full zone data
```

## System Components

### Backend (FastAPI)

The Python backend provides:

- **REST API** — CRUD operations for DNS records
- **DDNS Client** — Sends RFC 2136 updates with TSIG authentication
- **Zone Cache** — In-memory cache populated via AXFR
- **Catalog Zone Support** — Auto-discovery of zones via RFC 9432

```mermaid
flowchart TB
    subgraph "FastAPI Application"
        subgraph "Routers"
            R1[zones]
            R2[rrsets]
            R3[search]
            R4[atomic]
            R5[nsupdate]
            R6[reverse]
        end
        
        subgraph "Core Services"
            DC[DNS Client]
            ZC[Zone Cache]
            CI[Catalog Indexer]
        end
        
        subgraph "Auth"
            AK[API Key Auth]
            AZ[Azure AD Auth]
        end
    end
    
    R1 & R2 & R3 & R4 & R5 & R6 --> DC
    R1 & R2 & R3 --> ZC
    CI --> ZC
    
    AK & AZ --> R1 & R2 & R3 & R4 & R5 & R6
```

### Frontend (TypeScript/Alpine.js)

A standalone single-page application that:

- Communicates with the backend via REST API
- Provides zone browsing and record management
- Supports search across all zones
- Handles authentication (API key or Azure AD)

### DNS Server (BIND or compatible)

Any DNS server supporting:

- AXFR zone transfers (RFC 5936)
- DDNS updates (RFC 2136)
- TSIG authentication (RFC 2845)
- Optionally: Catalog zones (RFC 9432)

## Data Flow

### Reading Zone Data

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Cache
    participant DNS

    Client->>API: GET /zones/example.com/rrsets
    
    alt Zone not cached
        API->>DNS: AXFR example.com (TCP)
        DNS-->>API: Zone data
        API->>Cache: Store zone
    end
    
    API->>Cache: Query records
    Cache-->>API: RRsets
    API-->>Client: JSON response
```

### Writing Records

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Cache
    participant DNS

    Client->>API: POST /zones/example.com/rrsets
    
    Note over API,DNS: Build DDNS UPDATE message
    
    API->>Cache: Check prerequisites
    Cache-->>API: Current state
    
    API->>DNS: DDNS UPDATE + TSIG
    Note over DNS: Atomic operation
    
    alt Success
        DNS-->>API: NOERROR
        API->>Cache: Optimistic update
        API-->>Client: 201 Created
    else Prerequisite failed
        DNS-->>API: NXRRSET/YXRRSET
        API->>DNS: AXFR (refresh cache)
        API-->>Client: 409 Conflict
    end
```

### Atomic Multi-Operation Updates

Multiple operations can be combined into a single DNS transaction:

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant DNS

    Client->>API: POST /zones/example.com/atomic
    Note over Client,API: {operations: [add, delete, replace]}
    
    Note over API: Build single UPDATE message
    API->>DNS: Combined DDNS UPDATE
    
    Note over DNS: All-or-nothing execution
    
    alt All succeed
        DNS-->>API: NOERROR
        API-->>Client: Success (all operations)
    else Any fails
        DNS-->>API: Error rcode
        API-->>Client: 409 Conflict (no operations applied)
    end
```

The same builder is used for **scheduled changes** (`POST /v1/scheduled-changes`). A named change can be applied immediately or when a background scheduler claims it after `scheduled_at`. Explicit DNS prerequisites (NXDOMAIN / YXDOMAIN / NXRRSET / YXRRSET) and auto-derived prerequisites are attached to the same UPDATE message so the check and write stay atomic.

Statuses include `draft`, `scheduled`, `running`, `applied`, `failed`, `cancelled`, `expired`, and `reverted`. `POST /v1/scheduled-changes/{id}/revert` undoes an `applied` change using snapshots taken at apply time; `GET .../revert-preview` shows the inverse operations and a warning that only that change is undone.

## Consistency Model

### Prerequisites Ensure Consistency

DDNS updates include prerequisite conditions that must be met for the update to proceed:

| Operation | Prerequisite | Meaning |
|-----------|-------------|---------|
| Add | `NXRRSET` | RRset must not exist |
| Delete | `YXRRSET` | RRset must exist with expected values |
| Replace | `YXRRSET` | RRset must exist with expected values |

```mermaid
flowchart TB
    subgraph "Add Operation"
        A1[Check cache: RRset doesn't exist]
        A2[Prereq: NXRRSET]
        A3[Update: ADD records]
        A4{DNS accepts?}
        A5[Success]
        A6[Conflict: Already exists]
        
        A1 --> A2 --> A3 --> A4
        A4 -->|Yes| A5
        A4 -->|No| A6
    end
```

### Handling Concurrent Modifications

When the prerequisite fails, it means the DNS state has changed since the cache was last refreshed (possibly by another client or direct DNS edit):

```mermaid
sequenceDiagram
    participant Client1
    participant Client2
    participant API
    participant DNS

    Note over DNS: www.example.com A → 1.2.3.4
    
    Client1->>API: Replace www A → 5.6.7.8
    Client2->>API: Replace www A → 9.10.11.12
    
    Note over API: Both see cached value 1.2.3.4
    
    API->>DNS: UPDATE (prereq: 1.2.3.4) → 5.6.7.8
    DNS-->>API: NOERROR
    
    Note over DNS: www.example.com A → 5.6.7.8
    
    API->>DNS: UPDATE (prereq: 1.2.3.4) → 9.10.11.12
    DNS-->>API: NXRRSET (prereq failed!)
    
    API-->>Client1: Success
    API-->>Client2: 409 Conflict - refresh and retry
```

This optimistic concurrency control ensures no updates are lost due to race conditions.

## Deployment Topology

### Development Environment

```mermaid
flowchart LR
    subgraph "Developer Machine"
        Vite[Vite Dev Server<br/>:5173]
        FastAPI[FastAPI<br/>:8000]
    end
    
    subgraph "Docker"
        BIND[BIND DNS<br/>:15353]
    end
    
    Browser --> Vite
    Vite -->|Proxy /api| FastAPI
    FastAPI <--> BIND
```

### Production Environment

```mermaid
flowchart TB
    subgraph "Internet"
        Users[Users]
    end
    
    subgraph "Load Balancer"
        LB[Nginx/HAProxy]
    end
    
    subgraph "Application Tier"
        FE[Nginx<br/>Frontend + Proxy]
        API1[FastAPI Instance]
        API2[FastAPI Instance]
    end
    
    subgraph "DNS Infrastructure"
        HP[Hidden Primary<br/>BIND]
        NS1[Public NS1]
        NS2[Public NS2]
    end
    
    Users --> LB
    LB --> FE
    FE -->|Static files| FE
    FE -->|/api proxy| API1 & API2
    
    API1 & API2 <-->|DDNS/AXFR| HP
    HP -->|Zone Transfer| NS1 & NS2
```

## Security Model

### Authentication Layers

```mermaid
flowchart TB
    subgraph "Client"
        U[User/Service]
    end
    
    subgraph "API Authentication"
        AK[API Key<br/>X-API-Key header]
        AZ[Azure AD<br/>Bearer token]
    end
    
    subgraph "DNS Authentication"
        TSIG[TSIG Key<br/>Shared secret]
    end
    
    subgraph "DNS Server"
        BIND[Hidden Primary]
    end
    
    U -->|API Key or| AK
    U -->|Azure AD| AZ
    
    AK & AZ -->|Authorized request| TSIG
    TSIG -->|Signed DNS messages| BIND
```

### TSIG Authentication

All DNS operations are authenticated using TSIG (Transaction SIGnature):

- Shared secret between API and DNS server
- HMAC signature on every DDNS update and AXFR request
- Configured via environment variables (key name, secret, algorithm)

### Network Security Recommendations

1. **Hidden primary should be on a private network** — Not accessible from the internet
2. **TLS termination at load balancer** — Encrypt client traffic. This system doesn't do TLS.

---

## Further Reading

- [RFC 2136 - Dynamic Updates in DNS](https://datatracker.ietf.org/doc/html/rfc2136)
- [RFC 2845 - Secret Key Transaction Authentication (TSIG)](https://datatracker.ietf.org/doc/html/rfc2845)
- [RFC 5936 - DNS Zone Transfer Protocol (AXFR)](https://datatracker.ietf.org/doc/html/rfc5936)
- [RFC 9432 - DNS Catalog Zones](https://datatracker.ietf.org/doc/html/rfc9432)
- [DEVELOPMENT.md](./DEVELOPMENT.md) - Development setup guide
- [README.md](./README.md) - Project overview and configuration

