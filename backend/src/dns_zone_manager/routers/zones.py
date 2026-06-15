"""Zone management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient, ZoneTransferError
from dns_zone_manager.dns.idn import get_idn_info
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import (
    PaginatedZoneResponse,
    ZoneListResponse,
    ZoneResponse,
)

router = APIRouter(prefix="/zones", tags=["Zones"])

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


@router.get(
    "",
    response_model=ZoneListResponse | PaginatedZoneResponse,
    summary="List Zones",
    description="List all cached zones. Supports optional pagination \
            with after/limit/offset parameters.",
)
async def list_zones(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
    after: Annotated[
        str | None,
        Query(description="Return zones after this zone name (cursor-based pagination)"),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=1000, description="Maximum number of zones to return"),
    ] = None,
    offset: Annotated[
        int | None,
        Query(ge=0, description="Start at this index (0-based, for direct page jumps)"),
    ] = None,
) -> ZoneListResponse | PaginatedZoneResponse:
    """List all cached zones with optional pagination.

    Args:
        http_request: FastAPI request
        user: Authenticated user
        zone_cache: Zone cache
        after: Return zones after this name (cursor)
        limit: Maximum zones to return
        offset: Start at this index (0-based, for direct page jumps)

    Returns:
        List of cached zones with metadata, paginated if params provided
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="list_zones")

    # Use paginated method
    cached_zones, total_count, next_cursor, has_more = zone_cache.list_zones_paginated(
        after=after, limit=limit, offset=offset
    )

    zones = []
    for cached in cached_zones:
        zone_is_idn, zone_utf8 = get_idn_info(cached.zone_name)
        zones.append(
            ZoneResponse(
                zone=cached.zone_name,
                serial=cached.serial,
                rrset_count=cached.rrset_count,
                last_refresh=cached.last_refresh,
                zone_is_idn=zone_is_idn,
                zone_utf8=zone_utf8,
            )
        )

    enrich_dns_context(http_request, zones_count=len(zones), total_zones=total_count)

    # Return paginated response if pagination was requested
    if after is not None or limit is not None or offset is not None:
        return PaginatedZoneResponse(
            zones=zones,
            total_count=total_count,
            page_size=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    # Return simple list for backward compatibility
    return ZoneListResponse(zones=zones)


@router.get(
    "/{zone}",
    response_model=ZoneResponse,
    summary="Get Zone",
    description="Get zone information.",
)
async def get_zone(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> ZoneResponse:
    """Get information about a zone.

    Args:
        http_request: FastAPI request
        zone: Zone name
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Zone information
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="get_zone", zone=zone)

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    # Get or refresh cached zone
    try:
        cached = zone_cache.get_or_refresh_zone(zone)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="AXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to transfer zone: {e}",
        )

    enrich_dns_context(
        http_request,
        serial=cached.serial,
        rrset_count=cached.rrset_count,
    )

    zone_is_idn, zone_utf8 = get_idn_info(cached.zone_name)
    return ZoneResponse(
        zone=cached.zone_name,
        serial=cached.serial,
        rrset_count=cached.rrset_count,
        last_refresh=cached.last_refresh,
        zone_is_idn=zone_is_idn,
        zone_utf8=zone_utf8,
    )


@router.post(
    "/{zone}/refresh",
    response_model=ZoneResponse,
    summary="Refresh Zone",
    description="Force refresh of zone cache from DNS server via AXFR.",
)
async def refresh_zone(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> ZoneResponse:
    """Force refresh of a zone from the DNS server.

    Args:
        http_request: FastAPI request
        zone: Zone name
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Updated zone information
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="refresh_zone", zone=zone)

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    try:
        cached = zone_cache.refresh_zone(zone)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="AXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to transfer zone: {e}",
        )

    enrich_dns_context(
        http_request,
        result="success",
        serial=cached.serial,
        rrset_count=cached.rrset_count,
    )

    zone_is_idn, zone_utf8 = get_idn_info(cached.zone_name)
    return ZoneResponse(
        zone=cached.zone_name,
        serial=cached.serial,
        rrset_count=cached.rrset_count,
        last_refresh=cached.last_refresh,
        zone_is_idn=zone_is_idn,
        zone_utf8=zone_utf8,
    )


@router.delete(
    "/{zone}/cache",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate Zone Cache",
    description="Remove a zone from the cache.",
)
async def invalidate_zone_cache(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> None:
    """Invalidate a zone's cache.

    Args:
        http_request: FastAPI request
        zone: Zone name
        user: Authenticated user
        zone_cache: Zone cache
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="invalidate_cache",
        zone=zone,
    )

    zone_cache.invalidate_zone(zone)
    enrich_dns_context(http_request, result="success")


@router.get(
    "/{zone}/export",
    summary="Export Zone",
    description="Export zone as BIND master format zone file.",
    responses={
        200: {
            "content": {"text/dns": {}},
            "description": "Zone file in BIND master format",
        }
    },
)
async def export_zone(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> Response:
    """Export a zone as a BIND master format zone file.

    Args:
        http_request: FastAPI request
        zone: Zone name
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Zone file content as text/dns with download headers
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="export_zone", zone=zone)

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    # Get or refresh cached zone
    try:
        cached = zone_cache.get_or_refresh_zone(zone)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="AXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to transfer zone: {e}",
        )

    # Export zone data to master file format with $ORIGIN header
    zone_records = cached.zone_data.to_text()
    zone_content = f"$ORIGIN {zone}\n{zone_records}"

    # Create filename from zone name (remove trailing dot)
    filename = zone.rstrip(".") + ".zone"

    enrich_dns_context(
        http_request,
        result="success",
        serial=cached.serial,
        rrset_count=cached.rrset_count,
    )

    return Response(
        content=zone_content,
        media_type="text/dns",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
