"""API endpoints for named, optionally timed scheduled DNS changes."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.config import get_settings
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient
from dns_zone_manager.dns.types import UPDATABLE_TYPES, is_valid_class, is_valid_type
from dns_zone_manager.dns.update_builder import (
    UpdateBuildError,
    apply_cache_updates,
    build_update,
    preview_auto_prerequisites,
    preview_explicit_prerequisites,
)
from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import (
    ddns_updates_failed,
    ddns_updates_successful,
    rrset_adds_total,
    rrset_deletes_total,
    scheduled_changes_created_total,
    scheduled_changes_pending,
    scheduled_changes_reverted_total,
)
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    ApplyResponse,
    AuditEventListResponse,
    ChangeStatus,
    ConflictWarning,
    PreviewResponse,
    RevertPreviewResponse,
    RevertResponse,
    ScheduledChangeCreate,
    ScheduledChangeEventResponse,
    ScheduledChangeListResponse,
    ScheduledChangeResponse,
    ScheduledChangeUpdate,
)
from dns_zone_manager.scheduler.executor import execute_change
from dns_zone_manager.scheduler.revert import (
    REVERT_WARNING,
    build_revert_operations,
    can_revert,
    forward_ops_to_atomic,
    revert_ops_to_atomic,
)
from dns_zone_manager.scheduler.store import (
    ChangeCreateData,
    ChangeUpdateData,
    ScheduledChangeStore,
)

router = APIRouter(prefix="/scheduled-changes", tags=["Scheduled Changes"])
logger = logging.getLogger(__name__)

_dns_client: DNSClient | None = None
_zone_cache: ZoneCache | None = None
_store: ScheduledChangeStore | None = None


def set_dns_client(client: DNSClient) -> None:
    """Set the DNS client instance."""
    global _dns_client
    _dns_client = client


def set_zone_cache(cache: ZoneCache) -> None:
    """Set the zone cache instance."""
    global _zone_cache
    _zone_cache = cache


def set_store(store: ScheduledChangeStore) -> None:
    """Set the scheduled change store instance."""
    global _store
    _store = store


def get_dns_client() -> DNSClient:
    """Get the DNS client dependency."""
    if _dns_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DNS client not initialized",
        )
    return _dns_client


def get_zone_cache() -> ZoneCache:
    """Get the zone cache dependency."""
    if _zone_cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zone cache not initialized",
        )
    return _zone_cache


def get_store() -> ScheduledChangeStore:
    """Get the scheduled change store dependency."""
    if _store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduled change store not initialized",
        )
    return _store


def _validate_operations(operations: list[AtomicOperation]) -> None:
    """Validate operations for type/class/records."""
    for i, op in enumerate(operations):
        rdtype = op.type.upper()
        if not is_valid_type(rdtype):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {i}: unknown record type: {rdtype}",
            )
        if rdtype not in UPDATABLE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {i}: record type '{rdtype}' cannot be modified via API",
            )
        if not is_valid_class(op.rdclass):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {i}: invalid record class: {op.rdclass}",
            )
        if op.action in ("add", "replace") and (not op.records or len(op.records) == 0):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {i}: '{op.action}' requires records",
            )


def _normalize_zone(zone: str, dns_client: DNSClient) -> str:
    if not zone.endswith("."):
        zone = zone + "."
    if not dns_client.check_zone_exists(zone):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )
    return zone


@router.post(
    "",
    response_model=ScheduledChangeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create scheduled change",
)
async def create_scheduled_change(
    http_request: Request,
    body: ScheduledChangeCreate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> ScheduledChangeResponse:
    """Create a named change (draft if no schedule, else scheduled)."""
    enrich_user_context(http_request, user)
    zone = _normalize_zone(body.zone, dns_client)
    _validate_operations(body.operations)

    enrich_dns_context(
        http_request,
        operation="scheduled_change_create",
        zone=zone,
        operations_count=len(body.operations),
    )

    change = await store.create(
        ChangeCreateData(
            name=body.name,
            description=body.description,
            zone=zone,
            operations=body.operations,
            prerequisites=body.prerequisites,
            scheduled_at=body.scheduled_at,
            not_valid_after=body.not_valid_after,
            auto_prerequisites=body.auto_prerequisites,
            created_by=user.user_id,
        )
    )
    scheduled_changes_created_total.inc()
    scheduled_changes_pending.set(await store.count_pending())
    return change


@router.get(
    "",
    response_model=ScheduledChangeListResponse,
    summary="List scheduled changes",
)
async def list_scheduled_changes(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
    status_filter: Annotated[
        list[ChangeStatus] | None,
        Query(
            alias="status",
            description="Filter by status; repeat the param for multiple values",
        ),
    ] = None,
    zone: Annotated[str | None, Query(description="Filter by zone")] = None,
) -> ScheduledChangeListResponse:
    """List scheduled changes, optionally filtered."""
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="scheduled_change_list", zone=zone)

    changes = await store.list_changes(
        statuses=status_filter or None,
        zone=zone,
        include_events=False,
    )
    return ScheduledChangeListResponse(changes=changes, total=len(changes))


@router.get(
    "/events",
    response_model=AuditEventListResponse,
    summary="List scheduled-change audit events",
)
async def list_audit_events(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
    event: Annotated[
        list[str] | None,
        Query(description="Filter by event type; repeat for multiple"),
    ] = None,
    actor: Annotated[str | None, Query(description="Substring match on actor")] = None,
    zone: Annotated[str | None, Query(description="Exact zone filter")] = None,
    change_id: Annotated[str | None, Query(description="Exact change id")] = None,
    q: Annotated[
        str | None,
        Query(description="Free-text search over event, actor, detail, name, zone"),
    ] = None,
    since: Annotated[
        datetime | None,
        Query(description="Include events at/after this UTC time"),
    ] = None,
    until: Annotated[
        datetime | None,
        Query(description="Include events at/before this UTC time"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Offset for pagination")] = 0,
) -> AuditEventListResponse:
    """List audit events across all scheduled changes."""
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="scheduled_change_audit_list", zone=zone)

    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)

    events, total = await store.list_events(
        events=event or None,
        actor=actor,
        zone=zone,
        change_id=change_id,
        q=q,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AuditEventListResponse(events=events, total=total)


@router.get(
    "/{change_id}",
    response_model=ScheduledChangeResponse,
    summary="Get scheduled change",
)
async def get_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> ScheduledChangeResponse:
    """Get a scheduled change with operations, prerequisites, and events."""
    enrich_user_context(http_request, user)
    change = await store.get(change_id, include_events=True)
    if change is None:
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Change {change_id} not found",
            code="CHANGE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )
    enrich_dns_context(
        http_request,
        operation="scheduled_change_get",
        zone=change.zone,
        change_id=change_id,
    )
    return change


@router.patch(
    "/{change_id}",
    response_model=ScheduledChangeResponse,
    summary="Update scheduled change",
)
async def update_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    body: ScheduledChangeUpdate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> ScheduledChangeResponse:
    """Edit a draft, scheduled, or failed change."""
    enrich_user_context(http_request, user)
    if body.operations is not None:
        _validate_operations(body.operations)

    # Detect explicit null clearing via model_fields_set is hard with PATCH;
    # scheduled_at: null means clear schedule (become draft).
    raw = body.model_dump(exclude_unset=True)
    clear_scheduled = "scheduled_at" in raw and raw["scheduled_at"] is None
    clear_expiry = "not_valid_after" in raw and raw["not_valid_after"] is None
    clear_description = "description" in raw and raw["description"] is None

    try:
        change = await store.update(
            change_id,
            ChangeUpdateData(
                name=body.name,
                description=body.description,
                clear_description=clear_description,
                operations=body.operations,
                prerequisites=body.prerequisites,
                scheduled_at=body.scheduled_at if not clear_scheduled else None,
                clear_scheduled_at=clear_scheduled,
                not_valid_after=body.not_valid_after if not clear_expiry else None,
                clear_not_valid_after=clear_expiry,
                auto_prerequisites=body.auto_prerequisites,
                actor=user.user_id,
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    enrich_dns_context(
        http_request,
        operation="scheduled_change_update",
        zone=change.zone,
        change_id=change_id,
    )
    return change


@router.delete(
    "/{change_id}",
    response_model=ScheduledChangeResponse,
    summary="Cancel scheduled change",
)
async def cancel_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> ScheduledChangeResponse:
    """Cancel a pending change."""
    enrich_user_context(http_request, user)
    try:
        change = await store.cancel(change_id, actor=user.user_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    enrich_dns_context(
        http_request,
        operation="scheduled_change_cancel",
        zone=change.zone,
        change_id=change_id,
    )
    scheduled_changes_pending.set(await store.count_pending())
    return change


@router.post(
    "/{change_id}/apply",
    response_model=ApplyResponse,
    summary="Apply scheduled change now",
)
async def apply_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> ApplyResponse:
    """Apply a change immediately, ignoring its schedule."""
    enrich_user_context(http_request, user)
    settings = get_settings()

    try:
        change = await store.mark_running(
            change_id,
            lease_owner=user.user_id,
            lease_ttl=settings.scheduler.lease_ttl,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    enrich_dns_context(
        http_request,
        operation="scheduled_change_apply",
        zone=change.zone,
        change_id=change_id,
    )

    result = await execute_change(
        change,
        store=store,
        dns_client=dns_client,
        zone_cache=zone_cache,
        max_attempts=settings.scheduler.max_attempts,
        retry_backoff=settings.scheduler.retry_backoff,
        actor=user.user_id,
        trigger="apply_now",
    )

    updated = await store.get(change_id, include_events=False)
    scheduled_changes_pending.set(await store.count_pending())

    if not result.success:
        enrich_error_context(
            http_request,
            error_type="ApplyFailed",
            message=result.message,
            code="APPLY_FAILED",
            details={"rcode": result.result_rcode},
        )

    return ApplyResponse(
        success=result.success,
        change_id=change_id,
        zone=change.zone,
        status=updated.status if updated else "failed",
        message=result.message,
        result_rcode=result.result_rcode,
        new_serial=result.new_serial,
    )


@router.post(
    "/{change_id}/preview",
    response_model=PreviewResponse,
    summary="Preview scheduled change",
)
async def preview_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> PreviewResponse:
    """Dry-run: evaluate prerequisites and report conflicts without sending."""
    enrich_user_context(http_request, user)
    change = await store.get(change_id, include_events=False)
    if change is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )

    enrich_dns_context(
        http_request,
        operation="scheduled_change_preview",
        zone=change.zone,
        change_id=change_id,
    )

    # Ensure zone is in cache for preview
    if zone_cache.get_zone(change.zone) is None:
        try:
            zone_cache.refresh_zone(change.zone)
        except Exception:
            pass

    results = preview_explicit_prerequisites(change.zone, change.prerequisites, zone_cache)
    atomic_ops = forward_ops_to_atomic(change.operations)
    if change.auto_prerequisites:
        results.extend(preview_auto_prerequisites(change.zone, atomic_ops, zone_cache))

    conflict_rows = await store.find_conflicts(change.zone, atomic_ops, exclude_id=change.id)
    conflicts = [
        ConflictWarning(
            other_change_id=oid,
            other_change_name=oname,
            name=name,
            type=rdtype,
            rdclass=rdclass,
        )
        for oid, oname, name, rdtype, rdclass in conflict_rows
    ]

    all_passed = all(r.passed for r in results)
    message = (
        "All prerequisites currently pass" if all_passed else "One or more prerequisites would fail"
    )
    if conflicts:
        message += f"; {len(conflicts)} conflict warning(s)"

    return PreviewResponse(
        change_id=change.id,
        zone=change.zone,
        prerequisites=results,
        all_prerequisites_passed=all_passed,
        conflicts=conflicts,
        operations_count=len(change.operations),
        message=message,
    )


@router.get(
    "/{change_id}/events",
    response_model=list[ScheduledChangeEventResponse],
    summary="Get scheduled change audit events",
)
async def get_scheduled_change_events(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> list[ScheduledChangeEventResponse]:
    """Return the audit trail for a change."""
    enrich_user_context(http_request, user)
    change = await store.get(change_id, include_events=False)
    if change is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )
    enrich_dns_context(
        http_request,
        operation="scheduled_change_events",
        zone=change.zone,
        change_id=change_id,
    )
    return await store.get_events(change_id)


@router.get(
    "/{change_id}/revert-preview",
    response_model=RevertPreviewResponse,
    summary="Preview revert of an applied change",
)
async def revert_preview(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> RevertPreviewResponse:
    """Return the inverse ADD/DELETE operations a revert would perform."""
    enrich_user_context(http_request, user)
    change = await store.get(change_id, include_events=False)
    if change is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )

    enrich_dns_context(
        http_request,
        operation="scheduled_change_revert_preview",
        zone=change.zone,
        change_id=change_id,
    )

    if not can_revert(change):
        enrich_error_context(
            http_request,
            error_type="ConflictError",
            message="Change cannot be reverted",
            code="CANNOT_REVERT",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Change cannot be reverted: it must be applied and must have "
                "pre-apply snapshots (changes applied before revert support cannot be reverted)"
            ),
        )

    operations = build_revert_operations(change)
    log_internal_event(
        "scheduled_change_revert_preview",
        logger,
        change_id=change_id,
        zone=change.zone,
        operations_count=len(operations),
    )
    return RevertPreviewResponse(
        change_id=change.id,
        zone=change.zone,
        operations=operations,
        warning=REVERT_WARNING,
        message=(
            f"Revert will perform {len(operations)} operation(s) to undo this change"
            if operations
            else "No DNS operations are needed to revert this change"
        ),
        can_revert=True,
    )


@router.post(
    "/{change_id}/revert",
    response_model=RevertResponse,
    summary="Revert an applied scheduled change",
)
async def revert_scheduled_change(
    http_request: Request,
    change_id: Annotated[str, Path(description="Change ID")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
) -> RevertResponse:
    """Undo an applied change by sending the inverse ADD/DELETE UPDATE."""
    enrich_user_context(http_request, user)
    change = await store.get(change_id, include_events=True)
    if change is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Change '{change_id}' not found",
        )

    enrich_dns_context(
        http_request,
        operation="scheduled_change_revert",
        zone=change.zone,
        change_id=change_id,
    )

    if not can_revert(change):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Change cannot be reverted: it must be applied and must have pre-apply snapshots"
            ),
        )

    try:
        revert_ops = build_revert_operations(change)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    zone = change.zone
    if not zone.endswith("."):
        zone = zone + "."

    if not revert_ops:
        updated = await store.mark_reverted(
            change_id,
            result_rcode="NOERROR",
            new_serial=None,
            actor=user.user_id,
            operations_count=0,
        )
        scheduled_changes_reverted_total.inc()
        log_internal_event(
            "scheduled_change_reverted",
            logger,
            change_id=change_id,
            zone=zone,
            operations_count=0,
        )
        return RevertResponse(
            success=True,
            change_id=change_id,
            zone=zone,
            status=updated.status,
            message="Change marked reverted (no DNS operations required)",
            result_rcode="NOERROR",
            operations=[],
        )

    try:
        try:
            zone_cache.refresh_zone(zone)
        except Exception:
            pass

        built = build_update(
            zone,
            revert_ops_to_atomic(revert_ops),
            dns_client,
            zone_cache,
            prerequisites=[],
            # Auto-prereqs cannot be used: a replace invert is DELETE+ADD in one
            # UPDATE, and NXRRSET for the ADD would fail because prereqs are
            # checked before any update section runs.
            auto_prerequisites=False,
            validate_cache_state=False,
        )
        dns_client._send_update(built.update, zone)
        apply_cache_updates(zone, built.cache_updates, zone_cache)
        for cu in built.cache_updates:
            if cu.action == "add":
                rrset_adds_total.labels(zone=zone).inc()
            elif cu.action == "delete":
                rrset_deletes_total.labels(zone=zone).inc()
        ddns_updates_successful.inc()

        new_serial = None
        cached = zone_cache.get_zone(zone)
        if cached is not None:
            new_serial = cached.serial

        updated = await store.mark_reverted(
            change_id,
            result_rcode="NOERROR",
            new_serial=new_serial,
            actor=user.user_id,
            operations_count=len(revert_ops),
        )
        scheduled_changes_reverted_total.inc()
        scheduled_changes_pending.set(await store.count_pending())
        log_internal_event(
            "scheduled_change_reverted",
            logger,
            change_id=change_id,
            zone=zone,
            operations_count=len(revert_ops),
            new_serial=new_serial,
        )
        return RevertResponse(
            success=True,
            change_id=change_id,
            zone=zone,
            status=updated.status,
            message="Change reverted successfully",
            result_rcode="NOERROR",
            new_serial=new_serial,
            operations=revert_ops,
        )
    except UpdateBuildError as e:
        ddns_updates_failed.labels(reason="build_error").inc()
        enrich_error_context(
            http_request,
            error_type="UpdateBuildError",
            message=e.message,
            code=e.code or "BUILD_ERROR",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.message) from e
    except Exception as e:
        ddns_updates_failed.labels(reason="update_error").inc()
        enrich_error_context(
            http_request,
            error_type="RevertFailed",
            message=str(e),
            code="REVERT_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Revert failed: {e}",
        ) from e
