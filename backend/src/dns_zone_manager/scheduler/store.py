"""SQLite persistence for scheduled DNS changes.

Stores only intent (what to do, when) — never zone state.
DNS remains the sole source of truth for what a zone contains.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Concatenate

import aiosqlite

from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    AuditEventResponse,
    ChangePrerequisite,
    ChangeStatus,
    ScheduledChangeEventResponse,
    ScheduledChangeResponse,
    ScheduledOperationResponse,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_changes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    zone TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_at TEXT,
    not_valid_after TEXT,
    auto_prerequisites INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT,
    updated_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    applied_at TEXT,
    result_rcode TEXT,
    new_serial INTEGER,
    reverted_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_scheduled_changes_status
    ON scheduled_changes(status);
CREATE INDEX IF NOT EXISTS idx_scheduled_changes_zone
    ON scheduled_changes(zone);
CREATE INDEX IF NOT EXISTS idx_scheduled_changes_due
    ON scheduled_changes(status, scheduled_at);

CREATE TABLE IF NOT EXISTS scheduled_operations (
    change_id TEXT NOT NULL REFERENCES scheduled_changes(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    action TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    rdclass TEXT NOT NULL DEFAULT 'IN',
    ttl INTEGER NOT NULL DEFAULT 3600,
    records TEXT,
    prior_ttl INTEGER,
    prior_records TEXT,
    snapshot_at TEXT,
    PRIMARY KEY (change_id, seq)
);

CREATE TABLE IF NOT EXISTS scheduled_prerequisites (
    change_id TEXT NOT NULL REFERENCES scheduled_changes(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    prereq_type TEXT NOT NULL,
    name TEXT NOT NULL,
    rdtype TEXT,
    rdclass TEXT NOT NULL DEFAULT 'IN',
    data TEXT,
    PRIMARY KEY (change_id, seq)
);

CREATE TABLE IF NOT EXISTS scheduled_change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL REFERENCES scheduled_changes(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_change
    ON scheduled_change_events(change_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_ts
    ON scheduled_change_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_event
    ON scheduled_change_events(event);
CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_actor
    ON scheduled_change_events(actor);
"""

EDITABLE_STATUSES = frozenset({"draft", "scheduled", "failed"})
PENDING_STATUSES = frozenset({"draft", "scheduled", "failed", "running"})
# Draft, scheduled, and failed can still be applied later — used for RRset conflict warnings.
CONFLICT_STATUSES = frozenset({"draft", "scheduled", "failed"})


def _serialize_operation(op: AtomicOperation | ScheduledOperationResponse) -> dict[str, Any]:
    """JSON-friendly operation snapshot for audit detail."""
    return {
        "action": op.action,
        "name": op.name,
        "type": op.type,
        "rdclass": getattr(op, "rdclass", None) or "IN",
        "ttl": getattr(op, "ttl", None) if getattr(op, "ttl", None) is not None else 3600,
        "records": list(op.records) if op.records else None,
    }


def _serialize_prerequisite(prereq: ChangePrerequisite) -> dict[str, Any]:
    """JSON-friendly prerequisite snapshot for audit detail."""
    return {
        "prereq_type": prereq.prereq_type,
        "name": prereq.name,
        "rdtype": prereq.rdtype,
        "rdclass": prereq.rdclass,
        "data": prereq.data,
    }


@dataclass
class OpSnapshot:
    """Pre-apply RRset state for one operation (by seq)."""

    seq: int
    prior_ttl: int | None
    prior_records: list[str] | None
    snapshot_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass
class ChangeCreateData:
    """Internal create payload for the store."""

    name: str
    zone: str
    operations: list[AtomicOperation]
    prerequisites: list[ChangePrerequisite] = field(default_factory=list)
    description: str | None = None
    scheduled_at: datetime | None = None
    not_valid_after: datetime | None = None
    auto_prerequisites: bool = True
    created_by: str | None = None


@dataclass
class ChangeUpdateData:
    """Internal update payload for the store."""

    name: str | None = None
    description: str | None = None
    clear_description: bool = False
    operations: list[AtomicOperation] | None = None
    prerequisites: list[ChangePrerequisite] | None = None
    scheduled_at: datetime | None = None
    clear_scheduled_at: bool = False
    not_valid_after: datetime | None = None
    clear_not_valid_after: bool = False
    auto_prerequisites: bool | None = None
    actor: str | None = None


class _TransactionLock:
    """Serialises logical transactions over the store's shared connection.

    The scheduler loop and API requests share one aiosqlite connection, so
    without this an interleaved commit could publish another task's
    half-written change. Store methods call one another, so the lock is
    re-entrant for the task that already holds it.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if self._depth and self._owner is task:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return

        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._lock.release()


def _transactional[**P, R](
    method: Callable[Concatenate[ScheduledChangeStore, P], Awaitable[R]],
) -> Callable[Concatenate[ScheduledChangeStore, P], Awaitable[R]]:
    """Run a store method as a single serialised transaction."""

    @functools.wraps(method)
    async def wrapper(self: ScheduledChangeStore, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._tx():
            return await method(self, *args, **kwargs)

    return wrapper


class ScheduledChangeStore:
    """Async SQLite store for scheduled changes."""

    def __init__(self, database_path: str | Path, default_expiry_window: int = 3600) -> None:
        self.database_path = Path(database_path)
        self.default_expiry_window = default_expiry_window
        self._db: aiosqlite.Connection | None = None
        self._tx = _TransactionLock()

    async def open(self) -> None:
        """Open the database connection and ensure schema exists."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.database_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._ensure_schema_version()
        await self._db.commit()
        logger.info("Scheduled change store opened at %s", self.database_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ScheduledChangeStore is not open")
        return self._db

    async def _ensure_schema_version(self) -> None:
        db = self._conn()
        cursor = await db.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            return

        current = int(row["version"])
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            logger.warning(
                "Schema version newer than code: db=%s code=%s",
                current,
                SCHEMA_VERSION,
            )
            return

        if current < 2:
            await self._migrate_to_v2(db)
            current = 2
        if current < 3:
            await self._migrate_to_v3(db)
            current = 3

        await db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        logger.info("Migrated scheduled-change schema to version %s", SCHEMA_VERSION)

    async def _migrate_to_v2(self, db: aiosqlite.Connection) -> None:
        """Add revert snapshot columns (idempotent via PRAGMA table_info)."""
        cursor = await db.execute("PRAGMA table_info(scheduled_changes)")
        change_cols = {r["name"] for r in await cursor.fetchall()}
        await cursor.close()
        if "reverted_at" not in change_cols:
            await db.execute("ALTER TABLE scheduled_changes ADD COLUMN reverted_at TEXT")

        cursor = await db.execute("PRAGMA table_info(scheduled_operations)")
        op_cols = {r["name"] for r in await cursor.fetchall()}
        await cursor.close()
        if "prior_ttl" not in op_cols:
            await db.execute("ALTER TABLE scheduled_operations ADD COLUMN prior_ttl INTEGER")
        if "prior_records" not in op_cols:
            await db.execute("ALTER TABLE scheduled_operations ADD COLUMN prior_records TEXT")
        if "snapshot_at" not in op_cols:
            await db.execute("ALTER TABLE scheduled_operations ADD COLUMN snapshot_at TEXT")

    async def _migrate_to_v3(self, db: aiosqlite.Connection) -> None:
        """Add indexes for cross-change audit log queries."""
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_ts "
            "ON scheduled_change_events(ts DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_event "
            "ON scheduled_change_events(event)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scheduled_change_events_actor "
            "ON scheduled_change_events(actor)"
        )

    @_transactional
    async def add_event(
        self,
        change_id: str,
        event: str,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event for a change."""
        db = self._conn()
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(_utcnow()),
                event,
                actor,
                json.dumps(detail) if detail is not None else None,
            ),
        )
        await db.commit()

    @_transactional
    async def create(self, data: ChangeCreateData) -> ScheduledChangeResponse:
        """Create a new scheduled change."""
        now = _utcnow()
        change_id = str(uuid.uuid4())
        status: ChangeStatus = "scheduled" if data.scheduled_at else "draft"

        not_valid_after = data.not_valid_after
        if not_valid_after is None and data.scheduled_at is not None:
            not_valid_after = data.scheduled_at + timedelta(seconds=self.default_expiry_window)

        db = self._conn()
        await db.execute(
            """
            INSERT INTO scheduled_changes (
                id, name, description, zone, status, scheduled_at, not_valid_after,
                auto_prerequisites, created_at, created_by, updated_at, attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                change_id,
                data.name,
                data.description,
                data.zone,
                status,
                _to_iso(data.scheduled_at),
                _to_iso(not_valid_after),
                1 if data.auto_prerequisites else 0,
                _to_iso(now),
                data.created_by,
                _to_iso(now),
            ),
        )
        await self._insert_operations(change_id, data.operations)
        await self._insert_prerequisites(change_id, data.prerequisites)
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "created",
                data.created_by,
                json.dumps(
                    {
                        "status": status,
                        "zone": data.zone,
                        "operations_count": len(data.operations),
                        "prerequisites_count": len(data.prerequisites),
                        "scheduled_at": _to_iso(data.scheduled_at) if data.scheduled_at else None,
                        "auto_prerequisites": data.auto_prerequisites,
                    }
                ),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    async def _insert_operations(self, change_id: str, operations: list[AtomicOperation]) -> None:
        db = self._conn()
        for seq, op in enumerate(operations):
            await db.execute(
                """
                INSERT INTO scheduled_operations
                    (change_id, seq, action, name, type, rdclass, ttl, records,
                     prior_ttl, prior_records, snapshot_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    change_id,
                    seq,
                    op.action,
                    op.name,
                    op.type,
                    op.rdclass,
                    op.ttl,
                    json.dumps(op.records) if op.records is not None else None,
                ),
            )

    async def _insert_prerequisites(
        self, change_id: str, prerequisites: list[ChangePrerequisite]
    ) -> None:
        db = self._conn()
        for seq, prereq in enumerate(prerequisites):
            await db.execute(
                """
                INSERT INTO scheduled_prerequisites
                    (change_id, seq, prereq_type, name, rdtype, rdclass, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change_id,
                    seq,
                    prereq.prereq_type,
                    prereq.name,
                    prereq.rdtype,
                    prereq.rdclass,
                    prereq.data,
                ),
            )

    @_transactional
    async def get(
        self, change_id: str, *, include_events: bool = True
    ) -> ScheduledChangeResponse | None:
        """Fetch a change by ID."""
        db = self._conn()
        cursor = await db.execute(
            "SELECT * FROM scheduled_changes WHERE id = ?",
            (change_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return await self._row_to_response(row, include_events=include_events)

    @_transactional
    async def list_changes(
        self,
        *,
        status: ChangeStatus | None = None,
        statuses: list[ChangeStatus] | None = None,
        zone: str | None = None,
        include_events: bool = False,
    ) -> list[ScheduledChangeResponse]:
        """List changes, optionally filtered by status and/or zone.

        Prefer ``statuses`` for multi-value filters. ``status`` remains for
        single-value callers.
        """
        db = self._conn()
        clauses: list[str] = []
        params: list[Any] = []
        effective = list(statuses) if statuses else ([status] if status is not None else [])
        if effective:
            placeholders = ", ".join("?" for _ in effective)
            clauses.append(f"status IN ({placeholders})")
            params.extend(effective)
        if zone is not None:
            if not zone.endswith("."):
                zone = zone + "."
            clauses.append("zone = ?")
            params.append(zone)

        sql = "SELECT * FROM scheduled_changes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(scheduled_at, created_at) DESC"

        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        results: list[ScheduledChangeResponse] = []
        for row in rows:
            results.append(await self._row_to_response(row, include_events=include_events))
        return results

    @_transactional
    async def update(self, change_id: str, data: ChangeUpdateData) -> ScheduledChangeResponse:
        """Update an editable change."""
        existing = await self.get(change_id, include_events=False)
        if existing is None:
            raise KeyError(f"Change {change_id} not found")
        if existing.status not in EDITABLE_STATUSES:
            raise ValueError(f"Cannot edit change in status '{existing.status}'")
        if data.operations is not None and len(data.operations) == 0:
            raise ValueError("A change must have at least one operation")

        now = _utcnow()
        fields: list[str] = ["updated_at = ?"]
        params: list[Any] = [_to_iso(now)]
        changes: dict[str, Any] = {}

        if data.name is not None and data.name != existing.name:
            fields.append("name = ?")
            params.append(data.name)
            changes["name"] = {"from": existing.name, "to": data.name}
        elif data.name is not None:
            fields.append("name = ?")
            params.append(data.name)

        new_description = existing.description
        if data.clear_description:
            new_description = None
            fields.append("description = ?")
            params.append(None)
        elif data.description is not None:
            new_description = data.description
            fields.append("description = ?")
            params.append(data.description)
        if new_description != existing.description:
            changes["description"] = {
                "from": existing.description,
                "to": new_description,
            }

        new_auto = existing.auto_prerequisites
        if data.auto_prerequisites is not None:
            new_auto = data.auto_prerequisites
            fields.append("auto_prerequisites = ?")
            params.append(1 if data.auto_prerequisites else 0)
            if new_auto != existing.auto_prerequisites:
                changes["auto_prerequisites"] = {
                    "from": existing.auto_prerequisites,
                    "to": new_auto,
                }

        scheduled_at = existing.scheduled_at
        if data.clear_scheduled_at:
            scheduled_at = None
            fields.append("scheduled_at = ?")
            params.append(None)
        elif data.scheduled_at is not None:
            scheduled_at = data.scheduled_at
            fields.append("scheduled_at = ?")
            params.append(_to_iso(data.scheduled_at))
        if scheduled_at != existing.scheduled_at:
            changes["scheduled_at"] = {
                "from": _to_iso(existing.scheduled_at),
                "to": _to_iso(scheduled_at),
            }

        not_valid_after = existing.not_valid_after
        if data.clear_not_valid_after:
            not_valid_after = None
            fields.append("not_valid_after = ?")
            params.append(None)
        elif data.not_valid_after is not None:
            not_valid_after = data.not_valid_after
            fields.append("not_valid_after = ?")
            params.append(_to_iso(data.not_valid_after))
        elif data.scheduled_at is not None and existing.not_valid_after is None:
            # Auto-set expiry when scheduling without an explicit window
            not_valid_after = data.scheduled_at + timedelta(seconds=self.default_expiry_window)
            fields.append("not_valid_after = ?")
            params.append(_to_iso(not_valid_after))
        if not_valid_after != existing.not_valid_after:
            changes["not_valid_after"] = {
                "from": _to_iso(existing.not_valid_after),
                "to": _to_iso(not_valid_after),
            }

        # Recompute status from schedule presence (unless failed stays failed until rescheduled)
        new_status: ChangeStatus
        if scheduled_at is not None:
            new_status = "scheduled"
        else:
            new_status = "draft"
        # Allow re-scheduling a failed change
        if existing.status == "failed" and scheduled_at is not None:
            new_status = "scheduled"
            fields.append("last_error = ?")
            params.append(None)
            fields.append("next_attempt_at = ?")
            params.append(None)

        fields.append("status = ?")
        params.append(new_status)
        params.append(change_id)
        if new_status != existing.status:
            changes["status"] = {"from": existing.status, "to": new_status}

        if data.operations is not None:
            before_ops = [_serialize_operation(op) for op in existing.operations]
            after_ops = [_serialize_operation(op) for op in data.operations]
            if before_ops != after_ops:
                changes["operations"] = {
                    "from_count": len(before_ops),
                    "to_count": len(after_ops),
                    "from": before_ops,
                    "to": after_ops,
                }

        if data.prerequisites is not None:
            before_prereqs = [_serialize_prerequisite(p) for p in existing.prerequisites]
            after_prereqs = [_serialize_prerequisite(p) for p in data.prerequisites]
            if before_prereqs != after_prereqs:
                changes["prerequisites"] = {
                    "from_count": len(before_prereqs),
                    "to_count": len(after_prereqs),
                    "from": before_prereqs,
                    "to": after_prereqs,
                }

        db = self._conn()
        await db.execute(
            f"UPDATE scheduled_changes SET {', '.join(fields)} WHERE id = ?",
            params,
        )

        if data.operations is not None:
            await db.execute(
                "DELETE FROM scheduled_operations WHERE change_id = ?",
                (change_id,),
            )
            await self._insert_operations(change_id, data.operations)

        if data.prerequisites is not None:
            await db.execute(
                "DELETE FROM scheduled_prerequisites WHERE change_id = ?",
                (change_id,),
            )
            await self._insert_prerequisites(change_id, data.prerequisites)

        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "updated",
                data.actor,
                json.dumps({"status": new_status, "changes": changes}),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def cancel(self, change_id: str, actor: str | None = None) -> ScheduledChangeResponse:
        """Cancel a pending change."""
        existing = await self.get(change_id, include_events=False)
        if existing is None:
            raise KeyError(f"Change {change_id} not found")
        if existing.status not in {"draft", "scheduled", "failed"}:
            raise ValueError(f"Cannot cancel change in status '{existing.status}'")

        now = _utcnow()
        db = self._conn()
        await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'cancelled', updated_at = ?, lease_owner = NULL, lease_expires_at = NULL
            WHERE id = ?
            """,
            (_to_iso(now), change_id),
        )
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "cancelled",
                actor,
                json.dumps({"previous_status": existing.status}),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def claim_due(self, lease_owner: str, lease_ttl: int) -> ScheduledChangeResponse | None:
        """Atomically claim the next due scheduled change."""
        now = _utcnow()
        lease_expires = now + timedelta(seconds=lease_ttl)
        db = self._conn()

        # Expire overdue changes first
        await self.expire_overdue(now=now)

        cursor = await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'running',
                lease_owner = ?,
                lease_expires_at = ?,
                attempts = attempts + 1,
                updated_at = ?
            WHERE id = (
                SELECT id FROM scheduled_changes
                WHERE status = 'scheduled'
                  AND scheduled_at IS NOT NULL
                  AND scheduled_at <= ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY scheduled_at
                LIMIT 1
            )
            RETURNING id
            """,
            (
                lease_owner,
                _to_iso(lease_expires),
                _to_iso(now),
                _to_iso(now),
                _to_iso(now),
                _to_iso(now),
            ),
        )
        # The RETURNING cursor must be closed before committing, otherwise
        # SQLite reports "SQL statements in progress".
        row = await cursor.fetchone()
        await cursor.close()
        await db.commit()
        if row is None:
            return None

        change = await self.get(row["id"])
        if change is not None:
            await self.add_event(
                change.id,
                "claimed",
                actor=lease_owner,
                detail={"attempt": change.attempts},
            )
        return change

    @_transactional
    async def mark_applied(
        self,
        change_id: str,
        *,
        result_rcode: str | None = None,
        new_serial: int | None = None,
        actor: str | None = None,
        trigger: str = "scheduler",
    ) -> ScheduledChangeResponse:
        """Mark a change as successfully applied."""
        now = _utcnow()
        db = self._conn()
        await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'applied',
                applied_at = ?,
                result_rcode = ?,
                new_serial = ?,
                last_error = NULL,
                next_attempt_at = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (_to_iso(now), result_rcode, new_serial, _to_iso(now), change_id),
        )
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "applied",
                actor,
                json.dumps(
                    {
                        "trigger": trigger,
                        "result_rcode": result_rcode,
                        "new_serial": new_serial,
                    }
                ),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def save_operation_snapshots(self, change_id: str, snapshots: list[OpSnapshot]) -> None:
        """Persist pre-apply RRset snapshots for delete/replace operations."""
        db = self._conn()
        for snap in snapshots:
            await db.execute(
                """
                UPDATE scheduled_operations
                SET prior_ttl = ?,
                    prior_records = ?,
                    snapshot_at = ?
                WHERE change_id = ? AND seq = ?
                """,
                (
                    snap.prior_ttl,
                    json.dumps(snap.prior_records) if snap.prior_records is not None else None,
                    _to_iso(snap.snapshot_at),
                    change_id,
                    snap.seq,
                ),
            )
        await db.commit()

    @_transactional
    async def mark_reverted(
        self,
        change_id: str,
        *,
        result_rcode: str | None = None,
        new_serial: int | None = None,
        actor: str | None = None,
        operations_count: int = 0,
    ) -> ScheduledChangeResponse:
        """Mark an applied change as successfully reverted."""
        existing = await self.get(change_id, include_events=False)
        if existing is None:
            raise KeyError(f"Change {change_id} not found")
        if existing.status != "applied":
            raise ValueError(f"Cannot revert change in status '{existing.status}'")

        now = _utcnow()
        db = self._conn()
        await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'reverted',
                reverted_at = ?,
                result_rcode = ?,
                new_serial = ?,
                last_error = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (_to_iso(now), result_rcode, new_serial, _to_iso(now), change_id),
        )
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "reverted",
                actor,
                json.dumps(
                    {
                        "result_rcode": result_rcode,
                        "new_serial": new_serial,
                        "operations_count": operations_count,
                    }
                ),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def mark_failed(
        self,
        change_id: str,
        error: str,
        *,
        max_attempts: int,
        retry_backoff: int,
        actor: str | None = None,
        result_rcode: str | None = None,
    ) -> ScheduledChangeResponse:
        """Record a failure; reschedule for retry or leave as failed."""
        existing = await self.get(change_id, include_events=False)
        if existing is None:
            raise KeyError(f"Change {change_id} not found")

        now = _utcnow()
        can_retry = existing.attempts < max_attempts and existing.scheduled_at is not None
        # Also allow retry for apply-now on draft that we temporarily put in running
        if existing.attempts < max_attempts and existing.status == "running":
            # If it was scheduled (has scheduled_at) or was apply-now, decide based on attempts
            can_retry = existing.attempts < max_attempts

        if can_retry and existing.scheduled_at is not None:
            next_status: ChangeStatus = "scheduled"
            next_attempt = now + timedelta(seconds=retry_backoff * existing.attempts)
        else:
            next_status = "failed"
            next_attempt = None

        db = self._conn()
        await db.execute(
            """
            UPDATE scheduled_changes
            SET status = ?,
                last_error = ?,
                result_rcode = ?,
                next_attempt_at = ?,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                error,
                result_rcode,
                _to_iso(next_attempt),
                _to_iso(now),
                change_id,
            ),
        )
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "failed",
                actor,
                json.dumps(
                    {
                        "error": error,
                        "result_rcode": result_rcode,
                        "next_status": next_status,
                        "next_attempt_at": _to_iso(next_attempt),
                    }
                ),
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def mark_running(
        self, change_id: str, lease_owner: str, lease_ttl: int
    ) -> ScheduledChangeResponse:
        """Mark a change as running for apply-now."""
        existing = await self.get(change_id, include_events=False)
        if existing is None:
            raise KeyError(f"Change {change_id} not found")
        if existing.status not in {"draft", "scheduled", "failed"}:
            raise ValueError(f"Cannot apply change in status '{existing.status}'")

        now = _utcnow()
        lease_expires = now + timedelta(seconds=lease_ttl)
        db = self._conn()
        await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'running',
                lease_owner = ?,
                lease_expires_at = ?,
                attempts = attempts + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (lease_owner, _to_iso(lease_expires), _to_iso(now), change_id),
        )
        await db.execute(
            """
            INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                change_id,
                _to_iso(now),
                "apply_now",
                lease_owner,
                None,
            ),
        )
        await db.commit()
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def expire_overdue(self, now: datetime | None = None) -> list[str]:
        """Mark scheduled changes past not_valid_after as expired."""
        now = now or _utcnow()
        db = self._conn()
        cursor = await db.execute(
            """
            UPDATE scheduled_changes
            SET status = 'expired', updated_at = ?, lease_owner = NULL, lease_expires_at = NULL
            WHERE status = 'scheduled'
              AND not_valid_after IS NOT NULL
              AND not_valid_after < ?
            RETURNING id
            """,
            (_to_iso(now), _to_iso(now)),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        expired_ids = [row["id"] for row in rows]
        for change_id in expired_ids:
            await db.execute(
                """
                INSERT INTO scheduled_change_events (change_id, ts, event, actor, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (change_id, _to_iso(now), "expired", "scheduler", None),
            )
        await db.commit()
        return expired_ids

    @_transactional
    async def find_conflicts(
        self, zone: str, operations: list[AtomicOperation], exclude_id: str | None = None
    ) -> list[tuple[str, str, str, str, str]]:
        """Find draft/scheduled/failed changes that touch the same name/type/class.

        Applied, cancelled, expired, and other terminal statuses are ignored.
        Failed is included because those changes are often fixed and reapplied.

        Returns list of (other_id, other_name, name, type, rdclass).
        """
        if not zone.endswith("."):
            zone = zone + "."

        pending = await self.list_changes(zone=zone, include_events=False)
        targets = {
            (op.name.rstrip(".").lower(), op.type.upper(), op.rdclass.upper()) for op in operations
        }
        conflicts: list[tuple[str, str, str, str, str]] = []
        for other in pending:
            if other.status not in CONFLICT_STATUSES:
                continue
            if exclude_id and other.id == exclude_id:
                continue
            for op in other.operations:
                key = (op.name.rstrip(".").lower(), op.type.upper(), op.rdclass.upper())
                if key in targets:
                    conflicts.append((other.id, other.name, op.name, op.type, op.rdclass))
        return conflicts

    @_transactional
    async def count_pending(self) -> int:
        """Count changes waiting to run."""
        db = self._conn()
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt FROM scheduled_changes
            WHERE status IN ('draft', 'scheduled', 'failed', 'running')
            """
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["cnt"]) if row else 0

    @_transactional
    async def list_events(
        self,
        *,
        events: list[str] | None = None,
        actor: str | None = None,
        zone: str | None = None,
        change_id: str | None = None,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEventResponse], int]:
        """List audit events across all changes with filters and pagination."""
        db = self._conn()
        clauses: list[str] = []
        params: list[Any] = []

        if events:
            placeholders = ", ".join("?" for _ in events)
            clauses.append(f"e.event IN ({placeholders})")
            params.extend(events)
        if actor:
            clauses.append("e.actor LIKE ?")
            params.append(f"%{actor}%")
        if zone is not None:
            if not zone.endswith("."):
                zone = zone + "."
            clauses.append("c.zone = ?")
            params.append(zone)
        if change_id:
            clauses.append("e.change_id = ?")
            params.append(change_id)
        if since is not None:
            clauses.append("e.ts >= ?")
            params.append(_to_iso(since))
        if until is not None:
            clauses.append("e.ts <= ?")
            params.append(_to_iso(until))
        if q:
            like = f"%{q}%"
            clauses.append(
                "(e.event LIKE ? OR IFNULL(e.actor, '') LIKE ? OR IFNULL(e.detail, '') LIKE ?"
                " OR c.name LIKE ? OR c.zone LIKE ?)"
            )
            params.extend([like, like, like, like, like])

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        count_sql = f"""
            SELECT COUNT(*) AS cnt
            FROM scheduled_change_events e
            JOIN scheduled_changes c ON c.id = e.change_id
            {where}
        """
        cursor = await db.execute(count_sql, params)
        count_row = await cursor.fetchone()
        await cursor.close()
        total = int(count_row["cnt"]) if count_row else 0

        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        list_sql = f"""
            SELECT e.id, e.ts, e.event, e.actor, e.detail,
                   e.change_id, c.name AS change_name, c.zone, c.status AS change_status
            FROM scheduled_change_events e
            JOIN scheduled_changes c ON c.id = e.change_id
            {where}
            ORDER BY e.ts DESC, e.id DESC
            LIMIT ? OFFSET ?
        """
        cursor = await db.execute(list_sql, [*params, limit, offset])
        rows = await cursor.fetchall()
        await cursor.close()

        results: list[AuditEventResponse] = []
        for row in rows:
            detail = json.loads(row["detail"]) if row["detail"] else None
            results.append(
                AuditEventResponse(
                    id=row["id"],
                    ts=_from_iso(row["ts"]),  # type: ignore[arg-type]
                    event=row["event"],
                    actor=row["actor"],
                    detail=detail,
                    change_id=row["change_id"],
                    change_name=row["change_name"],
                    zone=row["zone"],
                    change_status=row["change_status"],
                )
            )
        return results, total

    @_transactional
    async def get_events(self, change_id: str) -> list[ScheduledChangeEventResponse]:
        """Return audit events for a change."""
        db = self._conn()
        cursor = await db.execute(
            """
            SELECT * FROM scheduled_change_events
            WHERE change_id = ?
            ORDER BY id ASC
            """,
            (change_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._event_from_row(row) for row in rows]

    async def _row_to_response(
        self, row: aiosqlite.Row, *, include_events: bool
    ) -> ScheduledChangeResponse:
        change_id = row["id"]
        operations = await self._load_operations(change_id)
        prerequisites = await self._load_prerequisites(change_id)
        events: list[ScheduledChangeEventResponse] = []
        if include_events:
            events = await self.get_events(change_id)

        return ScheduledChangeResponse(
            id=change_id,
            name=row["name"],
            description=row["description"],
            zone=row["zone"],
            status=row["status"],
            scheduled_at=_from_iso(row["scheduled_at"]),
            not_valid_after=_from_iso(row["not_valid_after"]),
            auto_prerequisites=bool(row["auto_prerequisites"]),
            created_at=_from_iso(row["created_at"]),  # type: ignore[arg-type]
            created_by=row["created_by"],
            updated_at=_from_iso(row["updated_at"]),  # type: ignore[arg-type]
            attempts=row["attempts"],
            next_attempt_at=_from_iso(row["next_attempt_at"]),
            last_error=row["last_error"],
            applied_at=_from_iso(row["applied_at"]),
            result_rcode=row["result_rcode"],
            new_serial=row["new_serial"],
            reverted_at=_from_iso(self._row_get(row, "reverted_at")),
            operations=operations,
            prerequisites=prerequisites,
            events=events,
        )

    @staticmethod
    def _row_get(row: aiosqlite.Row, key: str) -> Any:
        """Read a column that may be missing on pre-migration rows."""
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    async def _load_operations(self, change_id: str) -> list[ScheduledOperationResponse]:
        db = self._conn()
        cursor = await db.execute(
            """
            SELECT * FROM scheduled_operations
            WHERE change_id = ?
            ORDER BY seq ASC
            """,
            (change_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        ops: list[ScheduledOperationResponse] = []
        for row in rows:
            records = json.loads(row["records"]) if row["records"] else None
            prior_raw = self._row_get(row, "prior_records")
            prior_records = json.loads(prior_raw) if prior_raw else None
            ops.append(
                ScheduledOperationResponse(
                    action=row["action"],
                    name=row["name"],
                    type=row["type"],
                    rdclass=row["rdclass"],
                    ttl=row["ttl"],
                    records=records,
                    prior_ttl=self._row_get(row, "prior_ttl"),
                    prior_records=prior_records,
                    snapshot_at=_from_iso(self._row_get(row, "snapshot_at")),
                )
            )
        return ops

    async def _load_prerequisites(self, change_id: str) -> list[ChangePrerequisite]:
        db = self._conn()
        cursor = await db.execute(
            """
            SELECT * FROM scheduled_prerequisites
            WHERE change_id = ?
            ORDER BY seq ASC
            """,
            (change_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            ChangePrerequisite(
                prereq_type=row["prereq_type"],
                name=row["name"],
                rdtype=row["rdtype"],
                rdclass=row["rdclass"],
                data=row["data"],
            )
            for row in rows
        ]

    @staticmethod
    def _event_from_row(row: aiosqlite.Row) -> ScheduledChangeEventResponse:
        detail = json.loads(row["detail"]) if row["detail"] else None
        return ScheduledChangeEventResponse(
            id=row["id"],
            change_id=row["change_id"],
            ts=_from_iso(row["ts"]),  # type: ignore[arg-type]
            event=row["event"],
            actor=row["actor"],
            detail=detail,
        )
