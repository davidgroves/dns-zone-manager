"""Persistence for scheduled DNS changes.

Stores only intent (what to do, when) — never zone state.
DNS remains the sole source of truth for what a zone contains.

Runs against either SQLite or PostgreSQL. The schema is defined once in
``schema.py`` and created or upgraded by Alembic (see ``migrate.py``), so this
module contains no DDL and no dialect-specific SQL beyond the row-locking used
to claim due changes.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Concatenate

from sqlalchemy import Text, cast, delete, func, insert, or_, select, update
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from dns_zone_manager.config import DatabaseSettings
from dns_zone_manager.metrics import store_errors_total, store_operation_duration_seconds
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    AuditEventResponse,
    ChangePrerequisite,
    ChangeStatus,
    ScheduledChangeEventResponse,
    ScheduledChangeResponse,
    ScheduledOperationResponse,
)
from dns_zone_manager.scheduler.engine import create_store_engine, sqlite_path
from dns_zone_manager.scheduler.migrate import upgrade_to_head, verify_schema
from dns_zone_manager.scheduler.schema import (
    scheduled_change_events,
    scheduled_changes,
    scheduled_operations,
    scheduled_prerequisites,
)

logger = logging.getLogger(__name__)

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
    """Render a timestamp for storage inside JSON audit detail."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


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
    """Serialises logical transactions over the store's connection.

    The scheduler loop and API requests share the store, so without this an
    interleaved commit could publish another task's half-written change. Store
    methods call one another, so the lock is re-entrant for the task that
    already holds it; only the outermost entry owns the transaction.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[bool]:
        """Yield True when this is the outermost entry for the current task."""
        task = asyncio.current_task()
        if self._depth and self._owner is task:
            self._depth += 1
            try:
                yield False
            finally:
                self._depth -= 1
            return

        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        try:
            yield True
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._lock.release()


def _transactional[**P, R](
    method: Callable[Concatenate[ScheduledChangeStore, P], Awaitable[R]],
) -> Callable[Concatenate[ScheduledChangeStore, P], Awaitable[R]]:
    """Run a store method as a single serialised transaction.

    The outermost decorated call opens the transaction and commits it, so a
    method that fails partway through leaves nothing behind.
    """

    operation = getattr(method, "__name__", "unknown")

    @functools.wraps(method)
    async def wrapper(self: ScheduledChangeStore, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._transaction(operation):
            return await method(self, *args, **kwargs)

    return wrapper


class ScheduledChangeStore:
    """Async store for scheduled changes, backed by SQLite or PostgreSQL."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        default_expiry_window: int = 3600,
        *,
        settings: DatabaseSettings | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        """Create a store.

        Args:
            database_path: SQLite database file. Shorthand for a SQLite
                ``settings``, and the only form used by tests and older callers.
            default_expiry_window: Seconds after ``scheduled_at`` before a
                change expires when no explicit window is given.
            settings: Full database configuration, for PostgreSQL or for a
                SQLite database with non-default options.
            engine: Pre-built engine to use instead of creating one. The caller
                keeps ownership and is responsible for disposing it.
        """
        if settings is None:
            if database_path is None:
                raise ValueError("ScheduledChangeStore requires either database_path or settings")
            settings = DatabaseSettings(backend="sqlite", path=str(database_path))
        self.settings = settings
        self.default_expiry_window = default_expiry_window
        # Retained for SQLite callers that inspect the file directly.
        self.database_path = sqlite_path(settings)
        self._engine = engine
        self._owns_engine = engine is None
        self._tx = _TransactionLock()
        self._connection: AsyncConnection | None = None

    @property
    def backend(self) -> str:
        """The configured backend name, for logs and metrics."""
        return self.settings.backend

    async def open(self) -> None:
        """Connect and make sure the schema is present."""
        if self._engine is None:
            self._engine = create_store_engine(self.settings)

        if self.settings.auto_migrate:
            before, after = await asyncio.to_thread(upgrade_to_head, self.settings)
            if before != after:
                logger.info(
                    "Scheduled change schema migrated from %s to %s",
                    before or "empty",
                    after,
                )
        else:
            await asyncio.to_thread(verify_schema, self.settings)

        logger.info(
            "Scheduled change store opened (backend=%s target=%s)",
            self.settings.backend,
            self.settings.redacted_url(),
        )

    async def close(self) -> None:
        """Release the engine if this store created it."""
        if self._engine is not None and self._owns_engine:
            await self._engine.dispose()
        self._engine = None

    async def ping(self) -> bool:
        """Check the database answers, for health reporting."""
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(select(1))
            return True
        except Exception:
            return False

    def _require_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("ScheduledChangeStore is not open")
        return self._engine

    def _conn(self) -> AsyncConnection:
        if self._connection is None:
            raise RuntimeError("No active store transaction")
        return self._connection

    @asynccontextmanager
    async def _transaction(self, operation: str) -> AsyncIterator[AsyncConnection]:
        """Open (or join) the transaction for one logical store operation."""
        async with self._tx() as outermost:
            if not outermost:
                yield self._conn()
                return

            engine = self._require_engine()
            started = time.perf_counter()
            try:
                async with engine.connect() as connection, connection.begin():
                    self._connection = connection
                    try:
                        yield connection
                    finally:
                        self._connection = None
            except SQLAlchemyError:
                # Only database failures count here. Business errors such as a
                # missing change id are expected control flow, not store faults.
                store_errors_total.labels(operation=operation, backend=self.settings.backend).inc()
                raise
            finally:
                store_operation_duration_seconds.labels(
                    operation=operation, backend=self.settings.backend
                ).observe(time.perf_counter() - started)

    @property
    def _is_postgres(self) -> bool:
        return self.settings.backend == "postgres"

    @_transactional
    async def add_event(
        self,
        change_id: str,
        event: str,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event for a change."""
        await self._conn().execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=_utcnow(),
                event=event,
                actor=actor,
                detail=detail,
            )
        )

    @_transactional
    async def create(self, data: ChangeCreateData) -> ScheduledChangeResponse:
        """Create a new scheduled change."""
        now = _utcnow()
        change_id = str(uuid.uuid4())
        status: ChangeStatus = "scheduled" if data.scheduled_at else "draft"

        not_valid_after = data.not_valid_after
        if not_valid_after is None and data.scheduled_at is not None:
            not_valid_after = data.scheduled_at + timedelta(seconds=self.default_expiry_window)

        conn = self._conn()
        await conn.execute(
            insert(scheduled_changes).values(
                id=change_id,
                name=data.name,
                description=data.description,
                zone=data.zone,
                status=status,
                scheduled_at=data.scheduled_at,
                not_valid_after=not_valid_after,
                auto_prerequisites=data.auto_prerequisites,
                created_at=now,
                created_by=data.created_by,
                updated_at=now,
                attempts=0,
                source="scheduler",
            )
        )
        await self._insert_operations(change_id, data.operations)
        await self._insert_prerequisites(change_id, data.prerequisites)
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="created",
                actor=data.created_by,
                detail={
                    "status": status,
                    "zone": data.zone,
                    "operations_count": len(data.operations),
                    "prerequisites_count": len(data.prerequisites),
                    "scheduled_at": _to_iso(data.scheduled_at) if data.scheduled_at else None,
                    "auto_prerequisites": data.auto_prerequisites,
                },
            )
        )
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def record_external_change(
        self,
        *,
        name: str,
        zone: str,
        operations: list[dict[str, Any]],
        change_id: str | None = None,
        status: ChangeStatus = "applied",
        actor: str | None = None,
        trigger: str = "manual",
        result_rcode: str | None = None,
        error: str | None = None,
        occurred_at: datetime | None = None,
        source: str = "manual",
    ) -> ScheduledChangeResponse:
        """Record a change that was already executed outside the scheduler.

        Direct writes (RRset edits, atomic updates, nsupdate, rollbacks) never
        pass through the scheduler, so they have no change record to link to.
        Recording them here gives every change a stable ID and audit trail.

        Unlike ``create()``, this inserts a terminal status directly and never
        schedules anything for execution.
        """
        now = occurred_at or _utcnow()
        change_id = change_id or str(uuid.uuid4())
        if not zone.endswith("."):
            zone = zone + "."

        parsed_ops = [
            AtomicOperation(
                action=op["action"],
                name=op["name"],
                type=op["type"],
                rdclass=op.get("rdclass") or "IN",
                ttl=op["ttl"] if op.get("ttl") is not None else 3600,
                records=op.get("records") or None,
            )
            for op in operations
        ]

        conn = self._conn()
        await conn.execute(
            insert(scheduled_changes).values(
                id=change_id,
                name=name[:200],
                description=None,
                zone=zone,
                status=status,
                scheduled_at=None,
                not_valid_after=None,
                auto_prerequisites=False,
                created_at=now,
                created_by=actor,
                updated_at=now,
                attempts=0,
                applied_at=now if status == "applied" else None,
                result_rcode=result_rcode,
                last_error=error,
                source=source,
            )
        )
        await self._insert_operations(change_id, parsed_ops)

        detail = {
            "trigger": trigger,
            "source": source,
            "zone": zone,
            "operations_count": len(parsed_ops),
            "result_rcode": result_rcode,
            "error": error,
        }
        await conn.execute(
            insert(scheduled_change_events),
            [
                {
                    "change_id": change_id,
                    "ts": now,
                    "event": event,
                    "actor": actor,
                    "detail": detail,
                }
                for event in ("created", "applied" if status == "applied" else "failed")
            ],
        )
        return await self.get(change_id)  # type: ignore[return-value]

    async def _insert_operations(self, change_id: str, operations: list[AtomicOperation]) -> None:
        if not operations:
            return
        await self._conn().execute(
            insert(scheduled_operations),
            [
                {
                    "change_id": change_id,
                    "seq": seq,
                    "action": op.action,
                    "name": op.name,
                    "type": op.type,
                    "rdclass": op.rdclass,
                    "ttl": op.ttl,
                    "records": list(op.records) if op.records is not None else None,
                    "prior_ttl": None,
                    "prior_records": None,
                    "snapshot_at": None,
                }
                for seq, op in enumerate(operations)
            ],
        )

    async def _insert_prerequisites(
        self, change_id: str, prerequisites: list[ChangePrerequisite]
    ) -> None:
        if not prerequisites:
            return
        await self._conn().execute(
            insert(scheduled_prerequisites),
            [
                {
                    "change_id": change_id,
                    "seq": seq,
                    "prereq_type": prereq.prereq_type,
                    "name": prereq.name,
                    "rdtype": prereq.rdtype,
                    "rdclass": prereq.rdclass,
                    "data": prereq.data,
                }
                for seq, prereq in enumerate(prerequisites)
            ],
        )

    @_transactional
    async def get(
        self, change_id: str, *, include_events: bool = True
    ) -> ScheduledChangeResponse | None:
        """Fetch a change by ID."""
        result = await self._conn().execute(
            select(scheduled_changes).where(scheduled_changes.c.id == change_id)
        )
        row = result.mappings().first()
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
        source: str | None = None,
        include_events: bool = False,
    ) -> list[ScheduledChangeResponse]:
        """List changes, optionally filtered by status, zone, and/or source.

        Prefer ``statuses`` for multi-value filters. ``status`` remains for
        single-value callers.
        """
        clauses: list[ColumnElement[bool]] = []
        effective = list(statuses) if statuses else ([status] if status is not None else [])
        if effective:
            clauses.append(scheduled_changes.c.status.in_(effective))
        if zone is not None:
            if not zone.endswith("."):
                zone = zone + "."
            clauses.append(scheduled_changes.c.zone == zone)
        if source is not None:
            clauses.append(func.coalesce(scheduled_changes.c.source, "scheduler") == source)

        result = await self._conn().execute(
            select(scheduled_changes)
            .where(*clauses)
            .order_by(
                func.coalesce(
                    scheduled_changes.c.scheduled_at, scheduled_changes.c.created_at
                ).desc()
            )
        )
        return [
            await self._row_to_response(row, include_events=include_events)
            for row in result.mappings().all()
        ]

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
        values: dict[str, Any] = {"updated_at": now}
        changes: dict[str, Any] = {}

        if data.name is not None:
            values["name"] = data.name
            if data.name != existing.name:
                changes["name"] = {"from": existing.name, "to": data.name}

        new_description = existing.description
        if data.clear_description:
            new_description = None
            values["description"] = None
        elif data.description is not None:
            new_description = data.description
            values["description"] = data.description
        if new_description != existing.description:
            changes["description"] = {
                "from": existing.description,
                "to": new_description,
            }

        new_auto = existing.auto_prerequisites
        if data.auto_prerequisites is not None:
            new_auto = data.auto_prerequisites
            values["auto_prerequisites"] = data.auto_prerequisites
            if new_auto != existing.auto_prerequisites:
                changes["auto_prerequisites"] = {
                    "from": existing.auto_prerequisites,
                    "to": new_auto,
                }

        scheduled_at = existing.scheduled_at
        if data.clear_scheduled_at:
            scheduled_at = None
            values["scheduled_at"] = None
        elif data.scheduled_at is not None:
            scheduled_at = data.scheduled_at
            values["scheduled_at"] = data.scheduled_at
        if scheduled_at != existing.scheduled_at:
            changes["scheduled_at"] = {
                "from": _to_iso(existing.scheduled_at),
                "to": _to_iso(scheduled_at),
            }

        not_valid_after = existing.not_valid_after
        if data.clear_not_valid_after:
            not_valid_after = None
            values["not_valid_after"] = None
        elif data.not_valid_after is not None:
            not_valid_after = data.not_valid_after
            values["not_valid_after"] = data.not_valid_after
        elif data.scheduled_at is not None and existing.not_valid_after is None:
            # Auto-set expiry when scheduling without an explicit window
            not_valid_after = data.scheduled_at + timedelta(seconds=self.default_expiry_window)
            values["not_valid_after"] = not_valid_after
        if not_valid_after != existing.not_valid_after:
            changes["not_valid_after"] = {
                "from": _to_iso(existing.not_valid_after),
                "to": _to_iso(not_valid_after),
            }

        # Recompute status from schedule presence (unless failed stays failed until rescheduled)
        new_status: ChangeStatus = "scheduled" if scheduled_at is not None else "draft"
        # Allow re-scheduling a failed change
        if existing.status == "failed" and scheduled_at is not None:
            new_status = "scheduled"
            values["last_error"] = None
            values["next_attempt_at"] = None

        values["status"] = new_status
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

        conn = self._conn()
        await conn.execute(
            update(scheduled_changes).where(scheduled_changes.c.id == change_id).values(**values)
        )

        if data.operations is not None:
            await conn.execute(
                delete(scheduled_operations).where(scheduled_operations.c.change_id == change_id)
            )
            await self._insert_operations(change_id, data.operations)

        if data.prerequisites is not None:
            await conn.execute(
                delete(scheduled_prerequisites).where(
                    scheduled_prerequisites.c.change_id == change_id
                )
            )
            await self._insert_prerequisites(change_id, data.prerequisites)

        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="updated",
                actor=data.actor,
                detail={"status": new_status, "changes": changes},
            )
        )
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
        conn = self._conn()
        await conn.execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                status="cancelled",
                updated_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="cancelled",
                actor=actor,
                detail={"previous_status": existing.status},
            )
        )
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def claim_due(self, lease_owner: str, lease_ttl: int) -> ScheduledChangeResponse | None:
        """Atomically claim the next due scheduled change."""
        now = _utcnow()
        lease_expires = now + timedelta(seconds=lease_ttl)

        # Expire overdue changes first
        await self.expire_overdue(now=now)

        due = (
            select(scheduled_changes.c.id)
            .where(
                scheduled_changes.c.status == "scheduled",
                scheduled_changes.c.scheduled_at.is_not(None),
                scheduled_changes.c.scheduled_at <= now,
                or_(
                    scheduled_changes.c.next_attempt_at.is_(None),
                    scheduled_changes.c.next_attempt_at <= now,
                ),
                or_(
                    scheduled_changes.c.lease_expires_at.is_(None),
                    scheduled_changes.c.lease_expires_at < now,
                ),
            )
            .order_by(scheduled_changes.c.scheduled_at)
            .limit(1)
        )
        if self._is_postgres:
            # Multiple application instances can share one database, so the
            # candidate row is locked and rows already claimed are skipped.
            due = due.with_for_update(skip_locked=True)

        result = await self._conn().execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == due.scalar_subquery())
            .values(
                status="running",
                lease_owner=lease_owner,
                lease_expires_at=lease_expires,
                attempts=scheduled_changes.c.attempts + 1,
                updated_at=now,
            )
            .returning(scheduled_changes.c.id)
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            return None

        change = await self.get(claimed_id)
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
        conn = self._conn()
        await conn.execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                status="applied",
                applied_at=now,
                result_rcode=result_rcode,
                new_serial=new_serial,
                last_error=None,
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="applied",
                actor=actor,
                detail={
                    "trigger": trigger,
                    "result_rcode": result_rcode,
                    "new_serial": new_serial,
                },
            )
        )
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def save_operation_snapshots(self, change_id: str, snapshots: list[OpSnapshot]) -> None:
        """Persist pre-apply RRset snapshots for delete/replace operations."""
        conn = self._conn()
        for snap in snapshots:
            await conn.execute(
                update(scheduled_operations)
                .where(
                    scheduled_operations.c.change_id == change_id,
                    scheduled_operations.c.seq == snap.seq,
                )
                .values(
                    prior_ttl=snap.prior_ttl,
                    prior_records=snap.prior_records,
                    snapshot_at=snap.snapshot_at,
                )
            )

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
        conn = self._conn()
        await conn.execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                status="reverted",
                reverted_at=now,
                result_rcode=result_rcode,
                new_serial=new_serial,
                last_error=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="reverted",
                actor=actor,
                detail={
                    "result_rcode": result_rcode,
                    "new_serial": new_serial,
                    "operations_count": operations_count,
                },
            )
        )
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

        conn = self._conn()
        await conn.execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                status=next_status,
                last_error=error,
                result_rcode=result_rcode,
                next_attempt_at=next_attempt,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="failed",
                actor=actor,
                detail={
                    "error": error,
                    "result_rcode": result_rcode,
                    "next_status": next_status,
                    "next_attempt_at": _to_iso(next_attempt),
                },
            )
        )
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
        conn = self._conn()
        await conn.execute(
            update(scheduled_changes)
            .where(scheduled_changes.c.id == change_id)
            .values(
                status="running",
                lease_owner=lease_owner,
                lease_expires_at=lease_expires,
                attempts=scheduled_changes.c.attempts + 1,
                updated_at=now,
            )
        )
        await conn.execute(
            insert(scheduled_change_events).values(
                change_id=change_id,
                ts=now,
                event="apply_now",
                actor=lease_owner,
                detail=None,
            )
        )
        return await self.get(change_id)  # type: ignore[return-value]

    @_transactional
    async def expire_overdue(self, now: datetime | None = None) -> list[str]:
        """Mark scheduled changes past not_valid_after as expired."""
        now = now or _utcnow()
        conn = self._conn()
        result = await conn.execute(
            update(scheduled_changes)
            .where(
                scheduled_changes.c.status == "scheduled",
                scheduled_changes.c.not_valid_after.is_not(None),
                scheduled_changes.c.not_valid_after < now,
            )
            .values(
                status="expired",
                updated_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(scheduled_changes.c.id)
        )
        expired_ids = list(result.scalars().all())
        if expired_ids:
            await conn.execute(
                insert(scheduled_change_events),
                [
                    {
                        "change_id": change_id,
                        "ts": now,
                        "event": "expired",
                        "actor": "scheduler",
                        "detail": None,
                    }
                    for change_id in expired_ids
                ],
            )
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
        result = await self._conn().execute(
            select(func.count())
            .select_from(scheduled_changes)
            .where(scheduled_changes.c.status.in_(sorted(PENDING_STATUSES)))
        )
        return int(result.scalar() or 0)

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
        clauses: list[ColumnElement[bool]] = []

        if events:
            clauses.append(scheduled_change_events.c.event.in_(events))
        if actor:
            clauses.append(scheduled_change_events.c.actor.ilike(f"%{actor}%"))
        if zone is not None:
            if not zone.endswith("."):
                zone = zone + "."
            clauses.append(scheduled_changes.c.zone == zone)
        if change_id:
            clauses.append(scheduled_change_events.c.change_id == change_id)
        if since is not None:
            clauses.append(scheduled_change_events.c.ts >= since)
        if until is not None:
            clauses.append(scheduled_change_events.c.ts <= until)
        if q:
            like = f"%{q}%"
            clauses.append(
                or_(
                    scheduled_change_events.c.event.ilike(like),
                    func.coalesce(scheduled_change_events.c.actor, "").ilike(like),
                    # detail is JSON, so search its serialised form
                    func.coalesce(cast(scheduled_change_events.c.detail, Text), "").ilike(like),
                    scheduled_changes.c.name.ilike(like),
                    scheduled_changes.c.zone.ilike(like),
                )
            )

        joined = scheduled_change_events.join(
            scheduled_changes, scheduled_changes.c.id == scheduled_change_events.c.change_id
        )
        conn = self._conn()
        count_result = await conn.execute(select(func.count()).select_from(joined).where(*clauses))
        total = int(count_result.scalar() or 0)

        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        result = await conn.execute(
            select(
                scheduled_change_events.c.id,
                scheduled_change_events.c.ts,
                scheduled_change_events.c.event,
                scheduled_change_events.c.actor,
                scheduled_change_events.c.detail,
                scheduled_change_events.c.change_id,
                scheduled_changes.c.name.label("change_name"),
                scheduled_changes.c.zone,
                scheduled_changes.c.status.label("change_status"),
            )
            .select_from(joined)
            .where(*clauses)
            .order_by(
                scheduled_change_events.c.ts.desc(),
                scheduled_change_events.c.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        results = [
            AuditEventResponse(
                id=row["id"],
                ts=row["ts"],
                event=row["event"],
                actor=row["actor"],
                detail=row["detail"],
                change_id=row["change_id"],
                change_name=row["change_name"],
                zone=row["zone"],
                change_status=row["change_status"],
            )
            for row in result.mappings().all()
        ]
        return results, total

    @_transactional
    async def get_events(self, change_id: str) -> list[ScheduledChangeEventResponse]:
        """Return audit events for a change."""
        result = await self._conn().execute(
            select(scheduled_change_events)
            .where(scheduled_change_events.c.change_id == change_id)
            .order_by(scheduled_change_events.c.id.asc())
        )
        return [self._event_from_row(row) for row in result.mappings().all()]

    async def _row_to_response(
        self, row: RowMapping, *, include_events: bool
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
            scheduled_at=row["scheduled_at"],
            not_valid_after=row["not_valid_after"],
            auto_prerequisites=bool(row["auto_prerequisites"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            last_error=row["last_error"],
            applied_at=row["applied_at"],
            result_rcode=row["result_rcode"],
            new_serial=row["new_serial"],
            reverted_at=row["reverted_at"],
            source=row["source"] or "scheduler",
            operations=operations,
            prerequisites=prerequisites,
            events=events,
        )

    async def _load_operations(self, change_id: str) -> list[ScheduledOperationResponse]:
        result = await self._conn().execute(
            select(scheduled_operations)
            .where(scheduled_operations.c.change_id == change_id)
            .order_by(scheduled_operations.c.seq.asc())
        )
        return [
            ScheduledOperationResponse(
                action=row["action"],
                name=row["name"],
                type=row["type"],
                rdclass=row["rdclass"],
                ttl=row["ttl"],
                records=row["records"],
                prior_ttl=row["prior_ttl"],
                prior_records=row["prior_records"],
                snapshot_at=row["snapshot_at"],
            )
            for row in result.mappings().all()
        ]

    async def _load_prerequisites(self, change_id: str) -> list[ChangePrerequisite]:
        result = await self._conn().execute(
            select(scheduled_prerequisites)
            .where(scheduled_prerequisites.c.change_id == change_id)
            .order_by(scheduled_prerequisites.c.seq.asc())
        )
        return [
            ChangePrerequisite(
                prereq_type=row["prereq_type"],
                name=row["name"],
                rdtype=row["rdtype"],
                rdclass=row["rdclass"],
                data=row["data"],
            )
            for row in result.mappings().all()
        ]

    @staticmethod
    def _event_from_row(row: RowMapping) -> ScheduledChangeEventResponse:
        return ScheduledChangeEventResponse(
            id=row["id"],
            change_id=row["change_id"],
            ts=row["ts"],
            event=row["event"],
            actor=row["actor"],
            detail=row["detail"],
        )
