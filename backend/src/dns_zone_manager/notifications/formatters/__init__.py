"""Per-provider webhook payload formatters."""

from typing import Any

from dns_zone_manager.notifications.events import DnsChangeEvent
from dns_zone_manager.notifications.formatters.generic import format_generic
from dns_zone_manager.notifications.formatters.slack import format_slack
from dns_zone_manager.notifications.formatters.teams import format_teams

_FORMATTERS = {
    "slack": format_slack,
    "teams": format_teams,
    "generic": format_generic,
}


def format_payload(target_type: str, event: DnsChangeEvent, base_url: str) -> dict[str, Any]:
    """Render an event into the payload shape expected by the target type."""
    formatter = _FORMATTERS.get(target_type)
    if formatter is None:
        raise ValueError(f"Unknown webhook target type: {target_type}")
    return formatter(event, base_url)


__all__ = ["format_generic", "format_payload", "format_slack", "format_teams"]
