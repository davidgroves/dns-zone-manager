"""RRset management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import PaginatedRRsets, ZoneCache
from dns_zone_manager.dns.client import (
    RCODE_DESCRIPTIONS,
    DNSClient,
    PrerequisiteFailedError,
    UpdateError,
)
from dns_zone_manager.dns.idn import get_idn_info, get_records_utf8_info
from dns_zone_manager.dns.types import (
    UPDATABLE_TYPES,
    is_valid_class,
    is_valid_type,
    normalize_class,
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
    AddRRsetRequest,
    DeleteRRsetRequest,
    PaginatedRRsetResponse,
    ReplaceRRsetRequest,
    RRsetResponse,
    SuccessResponse,
)

router = APIRouter(prefix="/zones/{zone}/rrsets", tags=["RRsets"])

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
    """Validate and normalize zone name.

    Args:
        zone: Zone name from path
        dns_client: DNS client for validation

    Returns:
        Normalized zone name

    Raises:
        HTTPException: If zone doesn't exist
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    if not dns_client.check_zone_exists(zone):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    return zone


def validate_record_type(rdtype: str) -> str:
    """Validate record type.

    Args:
        rdtype: Record type string

    Returns:
        Normalized record type

    Raises:
        HTTPException: If type is invalid or not updatable
    """
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
    """Validate record class.

    Args:
        rdclass: Record class string

    Returns:
        Normalized record class

    Raises:
        HTTPException: If class is invalid
    """
    if not is_valid_class(rdclass):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid record class: {rdclass}",
        )

    return normalize_class(rdclass)


def make_rrset_response(
    name: str,
    ttl: int,
    rdtype: str,
    rdclass: str,
    records: list[str],
) -> RRsetResponse:
    """Create an RRsetResponse with IDN and UTF-8 fields populated.

    Args:
        name: Fully qualified record name
        ttl: Time to live
        rdtype: Record type
        rdclass: Record class
        records: Record data values

    Returns:
        RRsetResponse with IDN (for name) and UTF-8 (for records) fields populated
    """
    name_is_idn, name_utf8 = get_idn_info(name)
    records_is_utf8, records_utf8 = get_records_utf8_info(records)

    return RRsetResponse(
        name=name,
        ttl=ttl,
        type=rdtype,
        rdclass=rdclass,
        records=records,
        name_is_idn=name_is_idn,
        name_utf8=name_utf8,
        records_is_utf8=records_is_utf8,
        records_utf8=records_utf8,
    )


@router.post(
    "",
    response_model=SuccessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add RRset",
    description="Add a new RRset to the zone. Fails if the RRset already exists.",
)
async def add_rrset(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    request: AddRRsetRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> SuccessResponse:
    """Add a new RRset to a zone.

    The operation will fail with 409 Conflict if:
    - The RRset already exists (unless prereq check is disabled)
    - The DNS server state has changed since the last cache refresh

    Args:
        http_request: FastAPI request
        zone: Zone name
        request: Add request with name, TTL, type, class, and records
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Success response with the created RRset
    """
    zone = validate_zone(zone, dns_client)
    rdtype = validate_record_type(request.type)
    rdclass = validate_record_class(request.rdclass)

    # Enrich wide event with user and DNS context
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="add_rrset",
        zone=zone,
        name=request.name,
        rdtype=rdtype,
        rdclass=rdclass,
        ttl=request.ttl,
        records=request.records,
        prereq_check=True,
    )

    # Check cache for existing RRset
    cached_rrset = zone_cache.get_rrset(zone, request.name, rdtype, rdclass)
    if cached_rrset is not None:
        enrich_error_context(
            http_request,
            error_type="ConflictError",
            message=f"RRset {request.name} {rdclass} {rdtype} already exists",
            code="RRSET_EXISTS",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RRset {request.name} {rdclass} {rdtype} already exists in zone {zone}",
        )

    try:
        # Perform DDNS update with prerequisite check
        dns_client.add_rrset(
            zone=zone,
            name=request.name,
            ttl=request.ttl,
            rdtype=rdtype,
            records=request.records,
            rdclass=rdclass,
            prereq_not_exists=True,
        )

        # Update cache
        fqdn = str(dns_client.normalize_name(request.name, zone))
        zone_cache.update_cache_after_add(
            zone=zone,
            name=request.name,
            ttl=request.ttl,
            rdtype=rdtype,
            records=request.records,
            rdclass=rdclass,
        )

        # Update DNS context with success
        enrich_dns_context(http_request, result="success", fqdn=fqdn)

        # Increment metrics for successful add
        rrset_adds_total.labels(zone=zone).inc()
        ddns_updates_successful.inc()

        return SuccessResponse(
            success=True,
            message=f"RRset {request.name} {rdclass} {rdtype} added successfully",
            rrset=make_rrset_response(
                name=fqdn,
                ttl=request.ttl,
                rdtype=rdtype,
                rdclass=rdclass,
                records=request.records,
            ),
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
                "message": f"RRset already exists or DNS state has changed: {e}",
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


@router.delete(
    "",
    response_model=SuccessResponse,
    summary="Delete RRset",
    description="Delete an RRset or specific records from the zone.",
)
async def delete_rrset(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    request: DeleteRRsetRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> SuccessResponse:
    """Delete an RRset or specific records from a zone.

    If 'records' is provided, only those specific records are deleted.
    If 'records' is null/omitted, the entire RRset is deleted.

    Args:
        http_request: FastAPI request
        zone: Zone name
        request: Delete request with name, type, class, and optional records
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Success response
    """
    zone = validate_zone(zone, dns_client)
    rdtype = validate_record_type(request.type)
    rdclass = validate_record_class(request.rdclass)

    records_to_delete = request.records if request.records else "ALL"

    # Enrich wide event with user and DNS context
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="delete_rrset",
        zone=zone,
        name=request.name,
        rdtype=rdtype,
        rdclass=rdclass,
        records_to_delete=records_to_delete,
        prereq_check=True,
    )

    # Get current cached state for prerequisite
    cached_rrset = zone_cache.get_rrset(zone, request.name, rdtype, rdclass)
    if cached_rrset is None:
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"RRset {request.name} {rdclass} {rdtype} not found",
            code="RRSET_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RRset {request.name} {rdclass} {rdtype} not found in zone {zone}",
        )

    # If specific records requested, verify they exist
    if request.records:
        missing = set(request.records) - set(cached_rrset.records)
        if missing:
            enrich_error_context(
                http_request,
                error_type="NotFoundError",
                message=f"Records not found: {list(missing)}",
                code="RECORDS_NOT_FOUND",
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Records not found: {list(missing)}",
            )

    try:
        # Perform DDNS update with prerequisite
        dns_client.delete_rrset(
            zone=zone,
            name=request.name,
            rdtype=rdtype,
            records=request.records,
            rdclass=rdclass,
            prereq_records=cached_rrset.records,
        )

        # Update cache
        zone_cache.update_cache_after_delete(
            zone=zone,
            name=request.name,
            rdtype=rdtype,
            records=request.records,
            rdclass=rdclass,
        )

        # Update DNS context with success
        enrich_dns_context(http_request, result="success")

        # Increment metrics for successful delete
        rrset_deletes_total.labels(zone=zone).inc()
        ddns_updates_successful.inc()

        return SuccessResponse(
            success=True,
            message=f"RRset {request.name} {rdclass} {rdtype} deleted successfully",
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


@router.put(
    "",
    response_model=SuccessResponse,
    summary="Replace RRset",
    description="Replace an entire RRset with new values.",
)
async def replace_rrset(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    request: ReplaceRRsetRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> SuccessResponse:
    """Replace an entire RRset with new values.

    All existing records are removed and replaced with the provided records.

    Args:
        http_request: FastAPI request
        zone: Zone name
        request: Replace request with name, TTL, type, class, and new records
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Success response with the updated RRset
    """
    zone = validate_zone(zone, dns_client)
    rdtype = validate_record_type(request.type)
    rdclass = validate_record_class(request.rdclass)

    # Enrich wide event with user and DNS context
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="replace_rrset",
        zone=zone,
        name=request.name,
        rdtype=rdtype,
        rdclass=rdclass,
        ttl=request.ttl,
        records=request.records,
        prereq_check=True,
    )

    # Get current cached state for prerequisite
    cached_rrset = zone_cache.get_rrset(zone, request.name, rdtype, rdclass)
    if cached_rrset is None:
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"RRset {request.name} {rdclass} {rdtype} not found",
            code="RRSET_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"RRset {request.name} {rdclass} {rdtype} not found in zone {zone}. "
                "Use POST to create."
            ),
        )

    # Record original records for context
    enrich_dns_context(http_request, original_records=cached_rrset.records)

    try:
        # Perform DDNS update with prerequisite
        dns_client.replace_rrset(
            zone=zone,
            name=request.name,
            ttl=request.ttl,
            rdtype=rdtype,
            new_records=request.records,
            rdclass=rdclass,
            prereq_records=cached_rrset.records,
        )

        # Update cache
        fqdn = str(dns_client.normalize_name(request.name, zone))
        zone_cache.update_cache_after_replace(
            zone=zone,
            name=request.name,
            ttl=request.ttl,
            rdtype=rdtype,
            records=request.records,
            rdclass=rdclass,
        )

        # Update DNS context with success
        enrich_dns_context(http_request, result="success", fqdn=fqdn)

        # Increment metrics for successful replace
        rrset_replaces_total.labels(zone=zone).inc()
        ddns_updates_successful.inc()

        return SuccessResponse(
            success=True,
            message=f"RRset {request.name} {rdclass} {rdtype} replaced successfully",
            rrset=make_rrset_response(
                name=fqdn,
                ttl=request.ttl,
                rdtype=rdtype,
                rdclass=rdclass,
                records=request.records,
            ),
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


@router.get(
    "",
    response_model=list[RRsetResponse] | PaginatedRRsetResponse,
    summary="List RRsets",
    description=(
        "List all RRsets in the zone. Supports optional filtering by name, type, or class. "
        "Supports cursor-based pagination with 'after' and 'limit' parameters, "
        "or direct page jumps with 'offset' parameter. "
        "If no pagination params provided, returns all records for backward compatibility."
    ),
)
async def list_rrsets(
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    name: Annotated[str | None, Query(description="Filter by record name")] = None,
    record_type: Annotated[
        str | None, Query(alias="type", description="Filter by record type")
    ] = None,
    record_class: Annotated[
        str | None, Query(alias="class", description="Filter by record class")
    ] = None,
    after: Annotated[
        str | None,
        Query(description="Return records after this name (cursor for pagination)"),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=10000, description="Maximum number of records to return"),
    ] = None,
    offset: Annotated[
        int | None,
        Query(ge=0, description="Start at this index (0-based, for direct page jumps)"),
    ] = None,
) -> list[RRsetResponse] | PaginatedRRsetResponse:
    """List RRsets in a zone with optional pagination.

    Args:
        zone: Zone name
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache
        name: Optional name filter (disables pagination)
        record_type: Optional type filter
        record_class: Optional class filter
        after: Cursor for pagination - return records after this name
        limit: Maximum number of records to return
        offset: Start at this index (0-based, for direct page jumps)

    Returns:
        If pagination params (after/limit/offset) provided: PaginatedRRsetResponse
        Otherwise: List of RRsets (backward compatible)
    """
    zone = validate_zone(zone, dns_client)

    # Get zone from cache (refresh if needed)
    cached_zone = zone_cache.get_or_refresh_zone(zone)

    # Normalize class filter if provided
    rdclass_filter = None
    if record_class is not None:
        rdclass_filter = normalize_class(record_class)

    if name is not None:
        # Filter by name (and optionally class) - pagination not supported for name filter
        rrsets = cached_zone.get_rrsets_by_name(name, rdclass_filter)
        # Filter by type if specified
        if record_type is not None:
            record_type = record_type.upper()
            rrsets = [r for r in rrsets if r.rdtype == record_type]
        return [
            make_rrset_response(
                name=r.name,
                ttl=r.ttl,
                rdtype=r.rdtype,
                rdclass=r.rdclass,
                records=r.records,
            )
            for r in rrsets
        ]

    # Get all RRsets with optional pagination
    result = cached_zone.get_all_rrsets(
        rdclass=rdclass_filter, after_name=after, limit=limit, offset=offset
    )

    # Handle paginated result
    if isinstance(result, PaginatedRRsets):
        rrsets = result.rrsets
        # Filter by type if specified
        if record_type is not None:
            record_type = record_type.upper()
            rrsets = [r for r in rrsets if r.rdtype == record_type]

        return PaginatedRRsetResponse(
            rrsets=[
                make_rrset_response(
                    name=r.name,
                    ttl=r.ttl,
                    rdtype=r.rdtype,
                    rdclass=r.rdclass,
                    records=r.records,
                )
                for r in rrsets
            ],
            total_count=result.total_count,
            page_size=limit,
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    # Non-paginated result (simple list)
    rrsets = result
    # Filter by type if specified
    if record_type is not None:
        record_type = record_type.upper()
        rrsets = [r for r in rrsets if r.rdtype == record_type]

    return [
        make_rrset_response(
            name=r.name,
            ttl=r.ttl,
            rdtype=r.rdtype,
            rdclass=r.rdclass,
            records=r.records,
        )
        for r in rrsets
    ]


@router.get(
    "/{name}/{record_type}",
    response_model=RRsetResponse,
    summary="Get RRset",
    description="Get a specific RRset by name and type.",
)
async def get_rrset(
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    name: Annotated[str, Path(description="Record name")],
    record_type: Annotated[str, Path(description="Record type (e.g., A, AAAA, MX)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    record_class: Annotated[
        str, Query(alias="class", description="Record class (default: IN)")
    ] = "IN",
) -> RRsetResponse:
    """Get a specific RRset.

    Args:
        zone: Zone name
        name: Record name
        record_type: Record type
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache
        record_class: Record class (default: IN)

    Returns:
        RRset details
    """
    zone = validate_zone(zone, dns_client)
    rdtype = record_type.upper()
    rdclass = validate_record_class(record_class)

    if not is_valid_type(rdtype):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown record type: {rdtype}",
        )

    rrset = zone_cache.get_rrset(zone, name, rdtype, rdclass)
    if rrset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RRset {name} {rdclass} {rdtype} not found in zone {zone}",
        )

    return make_rrset_response(
        name=rrset.name,
        ttl=rrset.ttl,
        rdtype=rrset.rdtype,
        rdclass=rrset.rdclass,
        records=rrset.records,
    )
