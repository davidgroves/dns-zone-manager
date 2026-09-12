"""Atomic update endpoint for multi-operation DNS updates."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import (
    RCODE_DESCRIPTIONS,
    DNSClient,
    PrerequisiteFailedError,
    UpdateError,
)
from dns_zone_manager.dns.types import (
    UPDATABLE_TYPES,
    is_valid_class,
    is_valid_type,
    normalize_class,
)
from dns_zone_manager.dns.update_builder import (
    UpdateBuildError,
    apply_cache_updates,
    build_update,
)
from dns_zone_manager.metrics import (
    ddns_updates_failed,
    ddns_updates_successful,
    rrset_adds_total,
    rrset_deletes_total,
    rrset_replaces_total,
)
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import (
    AtomicOperation,
    AtomicUpdateRequest,
    AtomicUpdateResponse,
)

router = APIRouter(prefix="/zones/{zone}/atomic", tags=["Atomic Updates"])

# These will be injected by the main app
_dns_client: DNSClient | None = None
_zone_cache: ZoneCache | None = None


def set_dns_client(client: DNSClient) -> None:
    """Set the DNS client instance."""
    global _dns_client
    _dns_client = client


def set_zone_cache(cache: ZoneCache) -> None:
    """Set the zone cache instance."""
    global _zone_cache
    _zone_cache = cache


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


def validate_zone(zone: str, dns_client: DNSClient) -> str:
    """Validate and normalize zone name."""
    if not zone.endswith("."):
        zone = zone + "."

    if not dns_client.check_zone_exists(zone):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    return zone


def validate_record_type(rdtype: str) -> str:
    """Validate record type."""
    rdtype = rdtype.upper()

    if not is_valid_type(rdtype):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown record type: {rdtype}",
        )

    if rdtype not in UPDATABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Record type '{rdtype}' cannot be modified via API",
        )

    return rdtype


def validate_record_class(rdclass: str) -> str:
    """Validate record class."""
    if not is_valid_class(rdclass):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid record class: {rdclass}",
        )

    return normalize_class(rdclass)


def validate_operation(op: AtomicOperation, index: int) -> None:
    """Validate a single atomic operation.

    Args:
        op: The operation to validate
        index: Operation index for error messages

    Raises:
        HTTPException: If validation fails
    """
    if op.action in ("add", "replace"):
        if not op.records or len(op.records) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {index}: '{op.action}' requires records",
            )


@router.post(
    "",
    response_model=AtomicUpdateResponse,
    summary="Atomic Update",
    description="""
Execute multiple DNS operations atomically within a single zone.

All operations are combined into a single DNS UPDATE transaction.
Either all operations succeed, or none are applied.

**Supported actions:**
- `add` - Add a new RRset (fails if exists)
- `delete` - Delete an RRset or specific records
- `replace` - Replace an existing RRset with new values

**Notes:**
- All operations must be for the same zone (specified in path)
- Operations are executed in order within a single transaction
- If any operation fails, the entire transaction is rolled back
""",
    responses={
        200: {"description": "All operations completed successfully"},
        400: {"description": "Invalid operation or validation error"},
        404: {"description": "Zone not found"},
        409: {"description": "Prerequisite failed (state changed)"},
        500: {"description": "DNS update failed"},
    },
)
async def atomic_update(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    request: AtomicUpdateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> AtomicUpdateResponse:
    """Execute multiple DNS operations atomically.

    All operations are combined into a single DNS UPDATE message and
    executed as one transaction. Either all succeed or all fail.

    Args:
        http_request: FastAPI request
        zone: Zone name
        request: Atomic update request with operations list
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        AtomicUpdateResponse with success status and message
    """
    zone = validate_zone(zone, dns_client)

    # Enrich wide event with user context
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="atomic_update",
        zone=zone,
        operations_count=len(request.operations),
    )

    # Validate all operations first
    operations_summary = []
    for i, op in enumerate(request.operations):
        rdtype = validate_record_type(op.type)
        rdclass = validate_record_class(op.rdclass)
        validate_operation(op, i)
        operations_summary.append(
            {
                "action": op.action,
                "name": op.name,
                "type": rdtype,
                "rdclass": rdclass,
                "records": op.records,
            }
        )

    enrich_dns_context(http_request, operations=operations_summary)

    try:
        built = build_update(
            zone,
            request.operations,
            dns_client,
            zone_cache,
            auto_prerequisites=True,
            validate_cache_state=True,
        )
    except UpdateBuildError as e:
        status_code = status.HTTP_400_BAD_REQUEST
        if e.code == "RRSET_EXISTS":
            status_code = status.HTTP_409_CONFLICT
        elif e.code == "RRSET_NOT_FOUND":
            status_code = status.HTTP_404_NOT_FOUND
        enrich_error_context(
            http_request,
            error_type="UpdateBuildError",
            message=e.message,
            code=e.code,
        )
        raise HTTPException(status_code=status_code, detail=e.message)

    # Send the combined update
    try:
        dns_client._send_update(built.update, zone)

        apply_cache_updates(zone, built.cache_updates, zone_cache)
        for cu in built.cache_updates:
            if cu.action == "add":
                rrset_adds_total.labels(zone=zone).inc()
            elif cu.action == "delete":
                rrset_deletes_total.labels(zone=zone).inc()
            elif cu.action == "replace":
                rrset_replaces_total.labels(zone=zone).inc()

        ddns_updates_successful.inc()

        enrich_dns_context(http_request, result="success")

        return AtomicUpdateResponse(
            success=True,
            zone=zone,
            operations_count=len(request.operations),
            message="All operations completed successfully",
        )

    except PrerequisiteFailedError as e:
        enrich_error_context(
            http_request,
            error_type="PrerequisiteFailedError",
            message=str(e),
            code="PREREQ_FAILED",
            details={"rcode": e.rcode_text},
        )
        ddns_updates_failed.labels(reason="prereq_failed").inc()
        # Refresh cache since state is inconsistent
        try:
            zone_cache.refresh_zone(zone)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"DNS state has changed, refresh and retry: {e}",
                "rcode": e.rcode_text,
                "rcode_description": RCODE_DESCRIPTIONS.get(e.rcode_text or ""),
            },
        )

    except UpdateError as e:
        enrich_error_context(
            http_request,
            error_type="UpdateError",
            message=str(e),
            code="DDNS_FAILED",
        )
        ddns_updates_failed.labels(reason="update_error").inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DNS update failed: {e}",
        )
