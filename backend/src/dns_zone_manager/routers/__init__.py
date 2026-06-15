"""API routers for DNS API."""

from dns_zone_manager.routers.history import router as history_router
from dns_zone_manager.routers.rrsets import router as rrsets_router
from dns_zone_manager.routers.zones import router as zones_router

__all__ = ["rrsets_router", "zones_router", "history_router"]
