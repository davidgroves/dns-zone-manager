"""Catalog zone management endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.config import get_settings
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context

router = APIRouter(prefix="/catalog", tags=["Catalog Zone"])

# These will be injected by the main app
_catalog_indexer: Any = None  # CatZoneIndex | None when available
_zone_cache: ZoneCache | None = None


def set_catalog_indexer(indexer: Any) -> None:
    """Set the catalog indexer instance."""
    global _catalog_indexer
    _catalog_indexer = indexer


def set_zone_cache(cache: ZoneCache) -> None:
    """Set the zone cache instance."""
    global _zone_cache
    _zone_cache = cache


def get_catalog_indexer() -> Any:
    """Get the catalog indexer dependency (may be None if disabled or unavailable)."""
    return _catalog_indexer


def get_zone_cache() -> ZoneCache:
    """Get the zone cache dependency."""
    if _zone_cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zone cache not initialized",
        )
    return _zone_cache


class CatalogStatusResponse(BaseModel):
    """Catalog zone status response."""

    enabled: bool
    connected: bool
    zone_name: str | None
    serial: int | None
    zones_discovered: int
    poll_interval: float | None


class CatalogZoneInfo(BaseModel):
    """Information about a zone discovered from catalog."""

    zone: str
    from_catalog: bool
    loaded: bool


class CatalogZonesResponse(BaseModel):
    """List of zones discovered from catalog."""

    zones: list[CatalogZoneInfo]
    total: int


class SyncResultResponse(BaseModel):
    """Result of catalog sync operation."""

    synced: int
    added: int
    exists: int
    failed: int
    removed: int
    details: dict[str, str]


@router.get(
    "/status",
    response_model=CatalogStatusResponse,
    summary="Get Catalog Status",
    description="Get the status of the catalog zone indexer.",
)
async def get_catalog_status(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> CatalogStatusResponse:
    """Get catalog zone indexer status.

    Args:
        http_request: FastAPI request
        user: Authenticated user

    Returns:
        Catalog zone status
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="catalog_status")

    settings = get_settings()

    if not settings.catalog.enabled:
        enrich_dns_context(http_request, catalog_enabled=False)
        return CatalogStatusResponse(
            enabled=False,
            connected=False,
            zone_name=None,
            serial=None,
            zones_discovered=0,
            poll_interval=None,
        )

    indexer = get_catalog_indexer()

    zones_discovered = 0
    serial = None
    connected = False

    if indexer:
        connected = True
        serial = indexer.current_serial
        try:
            zones_discovered = len(indexer.list_zones())
        except RuntimeError:
            # Zone not yet loaded
            pass

    enrich_dns_context(
        http_request,
        catalog_enabled=True,
        connected=connected,
        zones_discovered=zones_discovered,
        serial=serial,
    )

    return CatalogStatusResponse(
        enabled=True,
        connected=connected,
        zone_name=settings.catalog.zone_name or None,
        serial=serial,
        zones_discovered=zones_discovered,
        poll_interval=settings.catalog.poll_interval,
    )


@router.get(
    "/zones",
    response_model=CatalogZonesResponse,
    summary="List Catalog Zones",
    description="List all zones discovered from the catalog zone.",
)
async def list_catalog_zones(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> CatalogZonesResponse:
    """List zones discovered from catalog.

    Args:
        http_request: FastAPI request
        user: Authenticated user
        zone_cache: Zone cache

    Returns:
        List of catalog zones
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="list_catalog_zones")

    settings = get_settings()

    if not settings.catalog.enabled:
        enrich_dns_context(http_request, catalog_enabled=False, zones_count=0)
        return CatalogZonesResponse(zones=[], total=0)

    indexer = get_catalog_indexer()
    if not indexer:
        enrich_dns_context(http_request, connected=False, zones_count=0)
        return CatalogZonesResponse(zones=[], total=0)

    try:
        discovered_zones = indexer.list_zones()
    except RuntimeError:
        enrich_dns_context(http_request, zone_loaded=False, zones_count=0)
        return CatalogZonesResponse(zones=[], total=0)

    cached_zones = set(zone_cache.list_zones())
    catalog_zones = zone_cache.get_catalog_zones()

    zones = []
    for zone in discovered_zones:
        # Normalize zone name
        normalized = zone if zone.endswith(".") else zone + "."
        normalized = normalized.lower()

        zones.append(
            CatalogZoneInfo(
                zone=zone,
                from_catalog=normalized in catalog_zones,
                loaded=normalized in cached_zones,
            )
        )

    enrich_dns_context(http_request, zones_count=len(zones))

    return CatalogZonesResponse(zones=zones, total=len(zones))


@router.post(
    "/sync",
    response_model=SyncResultResponse,
    summary="Sync Catalog Zones",
    description="Force synchronization of zones from the catalog zone.",
)
async def sync_catalog_zones(
    http_request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> SyncResultResponse:
    """Force sync zones from catalog.

    Args:
        http_request: FastAPI request
        user: Authenticated user
        zone_cache: Zone cache

    Returns:
        Sync operation results
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="sync_catalog")

    settings = get_settings()

    if not settings.catalog.enabled:
        enrich_error_context(
            http_request,
            error_type="ConfigurationError",
            message="Catalog zone is not enabled",
            code="CATALOG_DISABLED",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Catalog zone is not enabled",
        )

    indexer = get_catalog_indexer()
    if not indexer:
        enrich_error_context(
            http_request,
            error_type="ServiceUnavailable",
            message="Catalog indexer not connected",
            code="CATALOG_NOT_CONNECTED",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Catalog indexer not connected",
        )

    try:
        discovered_zones = indexer.list_zones()
    except RuntimeError:
        enrich_error_context(
            http_request,
            error_type="ServiceUnavailable",
            message="Catalog zone not yet loaded",
            code="CATALOG_NOT_LOADED",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Catalog zone not yet loaded",
        )

    enrich_dns_context(http_request, zones_to_sync=len(discovered_zones))

    if not discovered_zones:
        enrich_dns_context(
            http_request,
            synced=0,
            added=0,
            exists=0,
            failed=0,
            removed=0,
        )
        return SyncResultResponse(
            synced=0,
            added=0,
            exists=0,
            failed=0,
            removed=0,
            details={},
        )

    results = zone_cache.sync_from_catalog(
        discovered_zones,
        remove_stale=settings.catalog.remove_stale_zones,
    )

    added = sum(1 for v in results.values() if v == "added")
    exists = sum(1 for v in results.values() if v == "exists")
    failed = sum(1 for v in results.values() if v == "failed")
    removed = sum(1 for v in results.values() if v == "removed")

    enrich_dns_context(
        http_request,
        result="success",
        synced=len(results),
        added=added,
        exists=exists,
        failed=failed,
        removed=removed,
    )

    return SyncResultResponse(
        synced=len(results),
        added=added,
        exists=exists,
        failed=failed,
        removed=removed,
        details=results,
    )
