"""Search endpoints for DNS records."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import (
    PaginatedGlobalSearchResult,
    PaginatedSearchResult,
    ZoneCache,
)
from dns_zone_manager.dns.idn import get_idn_info, get_records_utf8_info
from dns_zone_manager.metrics import global_searches_total, zone_searches_total
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import (
    GlobalSearchResponse,
    PaginatedGlobalSearchResponse,
    PaginatedZoneSearchResponse,
    RRsetResponse,
    ZoneSearchResponse,
    ZoneSearchResultItem,
)

# Router for per-zone search
zone_router = APIRouter(prefix="/zones/{zone}/search", tags=["Search"])

# Router for global search
global_router = APIRouter(prefix="/search", tags=["Search"])

# Zone cache will be injected by the main app
_zone_cache: ZoneCache | None = None


def set_zone_cache(cache: ZoneCache) -> None:
    """Set the zone cache instance."""
    global _zone_cache
    _zone_cache = cache


def get_zone_cache() -> ZoneCache:
    """Get the zone cache dependency."""
    if _zone_cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zone cache not initialized",
        )
    return _zone_cache


def compile_pattern(pattern: str | None, param_name: str) -> re.Pattern[str] | None:
    """Compile a regex pattern with error handling.

    Args:
        pattern: Regex pattern string or None
        param_name: Parameter name for error messages

    Returns:
        Compiled pattern or None

    Raises:
        HTTPException: If pattern is invalid
    """
    if pattern is None:
        return None

    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid regex pattern for '{param_name}': {e}",
        )


def validate_search_params(
    name_pattern: str | None,
    value_pattern: str | None,
) -> None:
    """Validate that at least one search pattern is provided.

    Args:
        name_pattern: Name pattern
        value_pattern: Value pattern

    Raises:
        HTTPException: If no patterns provided
    """
    if name_pattern is None and value_pattern is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'name_pattern' or 'value_pattern' must be provided",
        )


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


def make_zone_search_result_item(
    zone: str,
    serial: int,
    rrsets: list[RRsetResponse],
) -> ZoneSearchResultItem:
    """Create a ZoneSearchResultItem with IDN fields populated.

    Args:
        zone: Zone name
        serial: SOA serial number
        rrsets: List of RRsetResponse objects

    Returns:
        ZoneSearchResultItem with IDN fields populated
    """
    zone_is_idn, zone_utf8 = get_idn_info(zone)

    return ZoneSearchResultItem(
        zone=zone,
        serial=serial,
        rrsets=rrsets,
        zone_is_idn=zone_is_idn,
        zone_utf8=zone_utf8,
    )


@zone_router.get(
    "",
    response_model=ZoneSearchResponse | PaginatedZoneSearchResponse,
    summary="Search RRsets in a zone",
    description=(
        "Search for RRsets in a specific zone using regex patterns. "
        "At least one of name_pattern or value_pattern must be provided. "
        "Supports optional pagination with after/limit/offset parameters."
    ),
)
async def search_zone(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    name_pattern: Annotated[
        str | None,
        Query(
            description="Regex pattern to match record names",
            examples=["^api-.*", ".*foo.*"],
        ),
    ] = None,
    type: Annotated[
        str | None,
        Query(
            description="Filter by record type",
            examples=["CNAME", "A", "MX"],
        ),
    ] = None,
    rdclass: Annotated[
        str | None,
        Query(
            alias="class",
            description="Filter by record class",
            examples=["IN", "CH", "HS"],
        ),
    ] = None,
    value_pattern: Annotated[
        str | None,
        Query(
            description="Regex pattern to match record values",
            examples=[".*foo.*", "^192\\.168\\..*"],
        ),
    ] = None,
    after: Annotated[
        str | None,
        Query(description="Return results after this record name (cursor-based pagination)"),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=1000, description="Maximum number of results to return"),
    ] = None,
    offset: Annotated[
        int | None,
        Query(ge=0, description="Start at this index (0-based, for direct page jumps)"),
    ] = None,
) -> ZoneSearchResponse | PaginatedZoneSearchResponse:
    """Search for RRsets in a specific zone.

    Args:
        http_request: FastAPI request
        zone: Zone name to search
        user: Authenticated user
        zone_cache: Zone cache
        name_pattern: Regex pattern to match record names
        type: Optional record type filter
        rdclass: Optional record class filter
        value_pattern: Regex pattern to match record values
        after: Return results after this name (cursor)
        limit: Maximum results to return
        offset: Start at this index (0-based, for direct page jumps)

    Returns:
        ZoneSearchResponse or PaginatedZoneSearchResponse
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="search_zone",
        zone=zone,
        name_pattern=name_pattern,
        rdtype=type,
        rdclass=rdclass,
        value_pattern=value_pattern,
    )

    validate_search_params(name_pattern, value_pattern)

    # Compile patterns
    name_regex = compile_pattern(name_pattern, "name_pattern")
    value_regex = compile_pattern(value_pattern, "value_pattern")

    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    # Perform search
    result = zone_cache.search_zone(
        zone, name_regex, type, rdclass, value_regex, after, limit, offset
    )

    if result is None:
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not cached",
        )

    # Increment zone search metrics
    zone_searches_total.labels(zone=zone).inc()

    # Handle paginated result
    if isinstance(result, PaginatedSearchResult):
        rrset_responses = [
            make_rrset_response(
                name=rrset.name,
                ttl=rrset.ttl,
                rdtype=rrset.rdtype,
                rdclass=rrset.rdclass,
                records=rrset.records,
            )
            for rrset in result.rrsets
        ]

        enrich_dns_context(
            http_request,
            results_count=len(rrset_responses),
            total_count=result.total_count,
            serial=result.serial,
        )

        return PaginatedZoneSearchResponse(
            zone=result.zone,
            serial=result.serial,
            results=rrset_responses,
            total_count=result.total_count,
            page_size=limit,
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    # Convert to response model (non-paginated)
    rrset_responses = [
        make_rrset_response(
            name=rrset.name,
            ttl=rrset.ttl,
            rdtype=rrset.rdtype,
            rdclass=rrset.rdclass,
            records=rrset.records,
        )
        for rrset in result.rrsets
    ]

    enrich_dns_context(
        http_request,
        results_count=len(rrset_responses),
        serial=result.serial,
    )

    return ZoneSearchResponse(
        zone=result.zone,
        serial=result.serial,
        results=rrset_responses,
        total_count=len(rrset_responses),
    )


@global_router.get(
    "",
    response_model=GlobalSearchResponse | PaginatedGlobalSearchResponse,
    summary="Search RRsets across all zones",
    description=(
        "Search for RRsets across all cached zones using regex patterns. "
        "At least one of name_pattern or value_pattern must be provided. "
        "Supports optional pagination with after/limit/offset parameters."
    ),
)
async def search_all_zones(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    name_pattern: Annotated[
        str | None,
        Query(
            description="Regex pattern to match record names",
            examples=["^api-.*", ".*foo.*"],
        ),
    ] = None,
    type: Annotated[
        str | None,
        Query(
            description="Filter by record type",
            examples=["CNAME", "A", "MX"],
        ),
    ] = None,
    rdclass: Annotated[
        str | None,
        Query(
            alias="class",
            description="Filter by record class",
            examples=["IN", "CH", "HS"],
        ),
    ] = None,
    value_pattern: Annotated[
        str | None,
        Query(
            description="Regex pattern to match record values",
            examples=[".*foo.*", "^192\\.168\\..*"],
        ),
    ] = None,
    after: Annotated[
        str | None,
        Query(description="Cursor for pagination (format: zone_name:record_name)"),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=1000, description="Maximum number of results to return"),
    ] = None,
    offset: Annotated[
        int | None,
        Query(ge=0, description="Start at this index (0-based, for direct page jumps)"),
    ] = None,
) -> GlobalSearchResponse | PaginatedGlobalSearchResponse:
    """Search for RRsets across all cached zones.

    Args:
        http_request: FastAPI request
        user: Authenticated user
        zone_cache: Zone cache
        name_pattern: Regex pattern to match record names
        type: Optional record type filter
        rdclass: Optional record class filter
        value_pattern: Regex pattern to match record values
        after: Cursor for pagination
        limit: Maximum results to return
        offset: Start at this index (0-based, for direct page jumps)

    Returns:
        GlobalSearchResponse or PaginatedGlobalSearchResponse
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="search_all_zones",
        name_pattern=name_pattern,
        rdtype=type,
        rdclass=rdclass,
        value_pattern=value_pattern,
    )

    validate_search_params(name_pattern, value_pattern)

    # Compile patterns
    name_regex = compile_pattern(name_pattern, "name_pattern")
    value_regex = compile_pattern(value_pattern, "value_pattern")

    # Perform search across all zones
    result = zone_cache.search_all_zones(
        name_regex, type, rdclass, value_regex, after, limit, offset
    )

    # Increment global search metrics
    global_searches_total.inc()

    # Handle paginated result
    if isinstance(result, PaginatedGlobalSearchResult):
        result_items: list[ZoneSearchResultItem] = []
        page_count = 0

        for zone_result in result.zone_results:
            rrset_responses = [
                make_rrset_response(
                    name=rrset.name,
                    ttl=rrset.ttl,
                    rdtype=rrset.rdtype,
                    rdclass=rrset.rdclass,
                    records=rrset.records,
                )
                for rrset in zone_result.rrsets
            ]

            result_items.append(
                make_zone_search_result_item(
                    zone=zone_result.zone,
                    serial=zone_result.serial,
                    rrsets=rrset_responses,
                )
            )
            page_count += len(rrset_responses)

        enrich_dns_context(
            http_request,
            zones_searched=len(result_items),
            results_count=page_count,
            total_count=result.total_count,
        )

        return PaginatedGlobalSearchResponse(
            results=result_items,
            total_count=result.total_count,
            page_size=limit,
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

    # Convert to response model (non-paginated)
    result_items = []
    total_count = 0

    for zone_result in result:
        rrset_responses = [
            make_rrset_response(
                name=rrset.name,
                ttl=rrset.ttl,
                rdtype=rrset.rdtype,
                rdclass=rrset.rdclass,
                records=rrset.records,
            )
            for rrset in zone_result.rrsets
        ]

        result_items.append(
            make_zone_search_result_item(
                zone=zone_result.zone,
                serial=zone_result.serial,
                rrsets=rrset_responses,
            )
        )
        total_count += len(rrset_responses)

    enrich_dns_context(
        http_request,
        zones_searched=len(result_items),
        results_count=total_count,
    )

    return GlobalSearchResponse(
        results=result_items,
        total_count=total_count,
    )
