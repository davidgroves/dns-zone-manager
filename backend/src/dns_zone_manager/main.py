"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, status

# Optional catalog_zone_indexer support (external package, install separately)
try:
    from catalog_zone_indexer import (  # type: ignore[import-not-found]
        CatZoneIndex,
        CatZoneIndexConfig,
    )

    CATALOG_AVAILABLE = True
except ImportError:
    CATALOG_AVAILABLE = False
    if TYPE_CHECKING:
        from catalog_zone_indexer import (  # type: ignore[import-not-found]
            CatZoneIndex,
            CatZoneIndexConfig,
        )
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from dns_zone_manager import __version__
from dns_zone_manager.config import TSIGKeyEntry, get_settings
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient, DNSClientError, ZoneTransferError
from dns_zone_manager.dns.notify import NotifyListener
from dns_zone_manager.logging import configure_logging, log_internal_event
from dns_zone_manager.middleware import WideEventMiddleware, enrich_error_context
from dns_zone_manager.models.requests import ErrorResponse
from dns_zone_manager.routers import (
    atomic,
    auth,
    catalog,
    history,
    nsupdate,
    reverse,
    rrsets,
    search,
    zones,
)

# Get settings early for logging configuration
_settings = get_settings()
configure_logging(
    log_format=_settings.logging.format,
    log_level="DEBUG" if _settings.debug else _settings.logging.level,
)
logger = logging.getLogger(__name__)

# Global instances
dns_client: DNSClient | None = None
zone_cache: ZoneCache | None = None
catalog_indexer: Any = None  # CatZoneIndex | None when catalog_zone_indexer is available
notify_listener: NotifyListener | None = None


async def _sync_catalog_zones() -> None:
    """Background task to sync zones from catalog."""
    if not catalog_indexer or not zone_cache:
        return

    settings = get_settings()

    while True:
        try:
            # Get zones from catalog
            discovered_zones = catalog_indexer.list_zones()
            if discovered_zones:
                log_internal_event(
                    "catalog_discovered",
                    logger,
                    zones_count=len(discovered_zones),
                )
                if settings.catalog.auto_load_zones:
                    results = zone_cache.sync_from_catalog(
                        discovered_zones,
                        remove_stale=settings.catalog.remove_stale_zones,
                    )
                    added = sum(1 for v in results.values() if v == "added")
                    if added:
                        log_internal_event(
                            "catalog_zones_loaded",
                            logger,
                            zones_added=added,
                        )
        except RuntimeError:
            # Zone not yet loaded
            pass
        except Exception as e:
            log_internal_event(
                "catalog_sync_error",
                logger,
                level="ERROR",
                error=str(e),
            )

        # Wait before next sync check
        await asyncio.sleep(10)


async def _background_zone_refresh() -> None:
    """Background task to refresh zones based on their SOA refresh timers.

    Each zone is refreshed according to its SOA refresh value, bounded by
    the configured min/max refresh intervals.
    """
    if not zone_cache:
        return

    from datetime import UTC, datetime

    settings = get_settings()
    min_interval = settings.cache.min_refresh_interval
    max_interval = settings.cache.max_refresh_interval

    log_internal_event(
        "zone_refresh_task_started",
        logger,
        min_refresh_interval=min_interval,
        max_refresh_interval=max_interval,
    )

    while True:
        try:
            # Get zones that need refreshing
            zones_to_refresh = zone_cache.get_zones_needing_refresh()

            for zone_name in zones_to_refresh:
                try:
                    log_internal_event(
                        "zone_auto_refresh_start",
                        logger,
                        zone=zone_name,
                    )
                    cached = zone_cache.refresh_zone(zone_name)
                    log_internal_event(
                        "zone_auto_refresh_complete",
                        logger,
                        zone=zone_name,
                        serial=cached.serial,
                        soa_refresh=cached.soa_refresh,
                        effective_refresh=cached.get_effective_refresh_interval(
                            min_interval, max_interval
                        ),
                    )
                except ZoneTransferError as e:
                    log_internal_event(
                        "zone_auto_refresh_failed",
                        logger,
                        level="WARNING",
                        zone=zone_name,
                        error=str(e),
                    )
                except Exception as e:
                    log_internal_event(
                        "zone_auto_refresh_error",
                        logger,
                        level="ERROR",
                        zone=zone_name,
                        error=str(e),
                    )

            # Calculate sleep time until next zone needs refresh
            next_refresh = zone_cache.get_next_refresh_time()
            if next_refresh:
                now = datetime.now(UTC)
                sleep_seconds = max(1.0, (next_refresh - now).total_seconds())
                # Cap sleep time to avoid sleeping too long
                sleep_seconds = min(sleep_seconds, float(max_interval))
            else:
                # No zones cached, check again later
                sleep_seconds = float(min_interval)

            await asyncio.sleep(sleep_seconds)

        except asyncio.CancelledError:
            log_internal_event("zone_refresh_task_stopped", logger)
            raise
        except Exception as e:
            log_internal_event(
                "zone_refresh_task_error",
                logger,
                level="ERROR",
                error=str(e),
            )
            # Wait a bit before retrying on error
            await asyncio.sleep(float(min_interval))


async def _handle_zone_notify(zone_name: str) -> None:
    """Handle a NOTIFY message for a zone by triggering refresh.

    This callback is invoked by the NotifyListener when a NOTIFY
    message is received. It refreshes the zone using IXFR when possible.

    Args:
        zone_name: The zone that received the NOTIFY
    """
    if not zone_cache:
        return

    # Normalize zone name
    if not zone_name.endswith("."):
        zone_name = zone_name + "."
    zone_name = zone_name.lower()

    # Check if zone is in our cache
    cached = zone_cache.get_zone(zone_name)
    if cached is None:
        log_internal_event(
            "notify_zone_not_cached",
            logger,
            level="DEBUG",
            zone=zone_name,
        )
        return

    # Trigger refresh (will use IXFR if possible)
    try:
        log_internal_event(
            "notify_triggered_refresh",
            logger,
            zone=zone_name,
            current_serial=cached.serial,
        )
        zone_cache.refresh_zone(zone_name)
    except ZoneTransferError as e:
        log_internal_event(
            "notify_refresh_failed",
            logger,
            level="WARNING",
            zone=zone_name,
            error=str(e),
        )
    except Exception as e:
        log_internal_event(
            "notify_refresh_error",
            logger,
            level="ERROR",
            zone=zone_name,
            error=str(e),
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan manager.

    Initializes DNS client and zone cache on startup,
    cleans up on shutdown.
    """
    global dns_client, zone_cache, catalog_indexer, notify_listener

    settings = get_settings()

    # Emit startup event with full configuration context
    log_internal_event(
        "application_startup",
        logger,
        dns_server=settings.dns.server,
        dns_port=settings.dns.port,
        tsig_key=settings.dns.update_tsig_key,
        azure_ad_enabled=settings.azure_ad.enabled,
        api_key_enabled=settings.api_key.enabled,
        cache_enabled=settings.cache.enabled,
        catalog_enabled=settings.catalog.enabled,
        notify_enabled=settings.notify.enabled,
        notify_prefer_ixfr=settings.notify.prefer_ixfr,
        log_format=settings.logging.format,
        log_level=settings.logging.level,
    )

    # Initialize DNS client
    try:
        dns_client = DNSClient(settings)
        log_internal_event("dns_client_initialized", logger)
    except Exception as e:
        log_internal_event(
            "dns_client_init_failed",
            logger,
            level="ERROR",
            error=str(e),
        )
        raise

    # Initialize zone cache
    zone_cache = ZoneCache(settings, dns_client)
    log_internal_event("zone_cache_initialized", logger)

    # Inject dependencies into routers
    rrsets.set_dns_client(dns_client)
    rrsets.set_zone_cache(zone_cache)
    zones.set_dns_client(dns_client)
    zones.set_zone_cache(zone_cache)
    reverse.set_dns_client(dns_client)
    reverse.set_zone_cache(zone_cache)
    search.set_zone_cache(zone_cache)
    nsupdate.set_dns_client(dns_client)
    atomic.set_dns_client(dns_client)
    atomic.set_zone_cache(zone_cache)
    history.set_dns_client(dns_client)
    history.set_zone_cache(zone_cache)

    # Initialize NOTIFY listener if enabled
    if settings.notify.enabled:
        # Get TSIG key for NOTIFY validation if required
        tsig_for_notify: TSIGKeyEntry | None = None
        if settings.notify.require_tsig:
            tsig_for_notify = settings.get_notify_tsig_key()
            if not tsig_for_notify:
                log_internal_event(
                    "notify_tsig_key_not_found",
                    logger,
                    level="WARNING",
                    configured_key=settings.notify.tsig_key or settings.dns.update_tsig_key,
                )

        notify_listener = NotifyListener(
            settings=settings.notify,
            tsig_key=tsig_for_notify,
            on_notify=_handle_zone_notify,
        )
        try:
            await notify_listener.start()
            log_internal_event(
                "notify_listener_started",
                logger,
                bind_address=settings.notify.bind_address,
                udp_port=settings.notify.udp_port,
                tcp_port=settings.notify.tcp_port,
                require_tsig=settings.notify.require_tsig,
                prefer_ixfr=settings.notify.prefer_ixfr,
            )
        except Exception as e:
            log_internal_event(
                "notify_listener_start_failed",
                logger,
                level="ERROR",
                error=str(e),
            )
            notify_listener = None

    # Initialize catalog zone indexer if enabled
    sync_task: asyncio.Task[None] | None = None
    if settings.catalog.enabled and settings.catalog.zone_name and CATALOG_AVAILABLE:
        # Get TSIG key for AXFR (catalog zone transfer uses AXFR key)
        axfr_tsig = settings.get_axfr_tsig_key()
        log_internal_event(
            "catalog_zone_config",
            logger,
            zone_name=settings.catalog.zone_name,
            tsig_key_name=axfr_tsig.name if axfr_tsig else None,
        )

        # Build TSIG key tuple if configured
        tsig_key: tuple[str, str, str] | None = None
        if axfr_tsig:
            tsig_key = (
                axfr_tsig.name,
                axfr_tsig.secret.get_secret_value(),
                axfr_tsig.algorithm,
            )

        catalog_config = CatZoneIndexConfig(
            # Use the resolved server IP (dnspython/the indexer require an IP,
            # not a hostname like a Docker service name).
            master_server=dns_client.server,
            zone_name=settings.catalog.zone_name,
            tsig_key=tsig_key,
            master_port_tcp=settings.dns.effective_tcp_port,
            master_port_udp=settings.dns.port,
            poll_interval=settings.catalog.poll_interval,
            notify_bind_address=settings.catalog.notify_bind_address,
            notify_udp_port=settings.catalog.notify_udp_port,
            notify_tcp_port=settings.catalog.notify_tcp_port,
        )

        catalog_indexer = CatZoneIndex(catalog_config)
        catalog.set_catalog_indexer(catalog_indexer)
        catalog.set_zone_cache(zone_cache)

        try:
            await catalog_indexer.start()
            log_internal_event("catalog_indexer_started", logger)

            # Start background sync task
            sync_task = asyncio.create_task(_sync_catalog_zones())

            # Initial sync
            await asyncio.sleep(2)  # Wait for initial AXFR
            try:
                discovered_zones = catalog_indexer.list_zones()
                if discovered_zones and settings.catalog.auto_load_zones:
                    log_internal_event(
                        "catalog_initial_sync",
                        logger,
                        zones_count=len(discovered_zones),
                    )
                    zone_cache.sync_from_catalog(
                        discovered_zones,
                        remove_stale=settings.catalog.remove_stale_zones,
                    )
            except RuntimeError:
                log_internal_event(
                    "catalog_zone_pending",
                    logger,
                    level="WARNING",
                    message="Catalog zone not yet loaded, will sync later",
                )

        except Exception as e:
            log_internal_event(
                "catalog_indexer_failed",
                logger,
                level="ERROR",
                error=str(e),
            )
            catalog_indexer = None
    else:
        # Log if catalog is configured but module not available
        if settings.catalog.enabled and settings.catalog.zone_name and not CATALOG_AVAILABLE:
            log_internal_event(
                "catalog_module_not_installed",
                logger,
                level="WARNING",
                message="Catalog enabled but catalog_zone_indexer not installed",
            )
        catalog.set_catalog_indexer(None)
        catalog.set_zone_cache(zone_cache)

    # Start background zone refresh task (based on SOA refresh timers)
    refresh_task: asyncio.Task[None] | None = None
    if settings.cache.enabled:
        refresh_task = asyncio.create_task(_background_zone_refresh())

    yield

    # Cleanup
    log_internal_event("application_shutdown", logger)

    # Stop refresh task
    if refresh_task:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

    # Stop sync task
    if sync_task:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

    # Stop catalog indexer
    if catalog_indexer:
        await catalog_indexer.stop()
        log_internal_event("catalog_indexer_stopped", logger)

    # Stop NOTIFY listener
    if notify_listener:
        await notify_listener.stop()
        log_internal_event("notify_listener_stopped", logger)

    if zone_cache:
        zone_cache.invalidate_all()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="""
DNS Record Management API

A REST API for managing DNS records using DDNS updates with TSIG authentication.
Supports AXFR zone transfers for caching and uses prerequisites to ensure
consistency with the authoritative DNS server.

## Features

- **Full DNS Record Type Support**: All IANA-registered record types
- **DDNS Updates**: RFC 2136 compliant with TSIG authentication
- **Consistency Guarantees**: PREREQ-based atomic updates
- **Zone Caching**: AXFR-based caching with automatic refresh

## Authentication

This API supports two authentication methods:
- **Azure AD**: Bearer token authentication
- **API Key**: X-API-Key header authentication

At least one authentication method must be configured and used.
        """,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add wide event logging middleware
    app.add_middleware(
        WideEventMiddleware,  # type: ignore[arg-type]
        sample_rate=settings.logging.sample_rate,
        slow_threshold_ms=float(settings.logging.slow_threshold_ms),
    )

    # Register routers with /v1 prefix
    app.include_router(zones.router, prefix="/v1")
    app.include_router(rrsets.router, prefix="/v1")
    app.include_router(atomic.router, prefix="/v1")
    app.include_router(search.zone_router, prefix="/v1")
    app.include_router(search.global_router, prefix="/v1")
    app.include_router(nsupdate.router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(reverse.router, prefix="/v1")
    app.include_router(history.router, prefix="/v1")

    # Exception handlers
    @app.exception_handler(DNSClientError)
    async def dns_client_error_handler(
        request: Request,
        exc: DNSClientError,
    ) -> JSONResponse:
        """Handle DNS client errors."""
        # Enrich wide event with error context
        enrich_error_context(
            request,
            error_type="DNSClientError",
            message=str(exc),
            code="DNS_ERROR",
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(
                error="DNSError",
                message=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Handle unexpected exceptions."""
        # Enrich wide event with error context
        enrich_error_context(
            request,
            error_type=type(exc).__name__,
            message=str(exc),
            code="INTERNAL_ERROR",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="InternalError",
                message="An unexpected error occurred",
            ).model_dump(),
        )

    # Health check endpoint
    @app.get(
        "/health",
        tags=["Health"],
        summary="Health Check",
        description="Check if the API is healthy and can connect to the DNS server.",
    )
    async def health_check() -> dict:
        """Health check endpoint.

        Returns:
            Health status and DNS server connectivity
        """
        settings = get_settings()
        dns_ok = False

        if dns_client:
            try:
                # Any DNS response (even REFUSED for an unserved zone) means
                # the server is reachable and answering.
                dns_ok = dns_client.check_server_responding()
            except Exception:
                dns_ok = False

        catalog_status = None
        if settings.catalog.enabled:
            zones_discovered = 0
            if catalog_indexer:
                try:
                    zones_discovered = len(catalog_indexer.list_zones())
                except RuntimeError:
                    pass  # Zone not yet loaded
            catalog_status = {
                "enabled": True,
                "zone_name": settings.catalog.zone_name,
                "connected": catalog_indexer is not None,
                "zones_discovered": zones_discovered,
            }

        notify_status = None
        if settings.notify.enabled:
            notify_status = {
                "enabled": True,
                "listening": notify_listener is not None,
                "udp_port": settings.notify.udp_port,
                "tcp_port": settings.notify.tcp_port,
                "prefer_ixfr": settings.notify.prefer_ixfr,
                "require_tsig": settings.notify.require_tsig,
            }

        return {
            "status": "healthy" if dns_ok else "degraded",
            "dns_server": settings.dns.server,
            "dns_connected": dns_ok,
            "cache_enabled": settings.cache.enabled,
            "cached_zones": len(zone_cache.list_zones()) if zone_cache else 0,
            "catalog": catalog_status,
            "notify": notify_status,
        }

    # Prometheus metrics endpoint
    @app.get(
        "/metrics",
        tags=["Monitoring"],
        summary="Prometheus Metrics",
        description="Expose Prometheus metrics for monitoring.",
    )
    async def metrics() -> Response:
        """Prometheus metrics endpoint.

        Returns:
            Prometheus metrics in text format
        """
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # Root endpoint
    @app.get(
        "/",
        tags=["Info"],
        summary="API Info",
        description="Get basic API information.",
    )
    async def root() -> dict:
        """Root endpoint with API info.

        Returns:
            API information
        """
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "metrics": "/metrics",
        }

    # UI Config endpoint (for decoupled frontend)
    @app.get(
        "/ui/config",
        tags=["Info"],
        summary="UI Configuration",
        description="Get configuration for the web UI.",
    )
    async def ui_config(request: Request) -> dict:
        """UI configuration endpoint.

        Returns configuration needed by the frontend, including
        authentication options, the proxy-authenticated identity (if any),
        and version information.

        Returns:
            UI configuration
        """
        from dns_zone_manager.auth.proxy import proxy_user_from_request

        proxy_user = proxy_user_from_request(request)
        return {
            "azureEnabled": settings.azure_ad.enabled,
            "apiKeyEnabled": settings.api_key.enabled,
            "proxyAuthEnabled": settings.proxy_auth.enabled,
            "user": (
                {"email": proxy_user.email, "name": proxy_user.name}
                if proxy_user
                else None
            ),
            "version": __version__,
        }

    return app


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "dns_zone_manager.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="debug" if settings.debug else settings.logging.level.lower(),
    )
