"""Plain JSON webhook payload."""

from typing import Any

from dns_zone_manager.notifications.events import DnsChangeEvent


def format_generic(event: DnsChangeEvent, base_url: str) -> dict[str, Any]:
    """Render the event as plain structured JSON."""
    return event.to_dict(base_url)
