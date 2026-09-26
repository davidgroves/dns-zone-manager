"""Live zone-change WebSocket package."""

from dns_zone_manager.live.hub import ZoneChangeHub, normalize_zone_name

__all__ = ["ZoneChangeHub", "normalize_zone_name"]
