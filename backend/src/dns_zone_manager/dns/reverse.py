"""Reverse DNS PTR utilities.

This module provides utilities for working with reverse DNS zones and PTR records,
using dnspython for IP-to-PTR name conversions.
"""

import dns.name
import dns.reversename


def ip_to_ptr_name(ip: str) -> dns.name.Name:
    """Convert an IP address to its full PTR name.

    Uses dnspython's reversename module which handles both IPv4 and IPv6:
    - IPv4: 192.0.2.100 -> 100.2.0.192.in-addr.arpa.
    - IPv6: 2001:db8::50 -> 0.5.0.0...8.b.d.0.1.0.0.2.ip6.arpa.

    Args:
        ip: IP address string (IPv4 or IPv6)

    Returns:
        dns.name.Name representing the full PTR name

    Raises:
        dns.exception.SyntaxError: If IP address is invalid
    """
    return dns.reversename.from_address(ip)


def find_reverse_zone(ptr_name: dns.name.Name, managed_zones: set[str]) -> str | None:
    """Find the managed reverse zone for a PTR name.

    Walks up the PTR name hierarchy until a managed zone is found.
    For example, for 100.2.0.192.in-addr.arpa:
    - First checks: 100.2.0.192.in-addr.arpa. (unlikely to be a zone)
    - Then: 2.0.192.in-addr.arpa. (common /24 delegation)
    - Then: 0.192.in-addr.arpa. (/16 delegation)
    - Then: 192.in-addr.arpa. (/8 delegation)
    - Finally: in-addr.arpa. (root reverse zone)

    Args:
        ptr_name: Full PTR name from ip_to_ptr_name()
        managed_zones: Set of zone names this system manages (lowercase, with trailing dot)

    Returns:
        Zone name if found in managed_zones, None otherwise
    """
    name = ptr_name
    while len(name) > 1:
        zone_str = name.to_text().lower()
        if zone_str in managed_zones:
            return zone_str
        name = name.parent()
    return None


def get_ptr_record_name(ptr_name: dns.name.Name, zone: str) -> str:
    """Get the relative record name within a zone.

    For example:
    - ptr_name: 100.2.0.192.in-addr.arpa.
    - zone: 2.0.192.in-addr.arpa.
    - returns: "100"

    For IPv6 with /48 delegation:
    - ptr_name: 0.5.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa.
    - zone: 8.b.d.0.1.0.0.2.ip6.arpa.
    - returns: "0.5.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0"

    Args:
        ptr_name: Full PTR name
        zone: Zone name (with trailing dot)

    Returns:
        Relative record name within the zone
    """
    # Ensure zone has trailing dot for comparison
    if not zone.endswith("."):
        zone = zone + "."

    zone_name = dns.name.from_text(zone)
    # relativize() returns the part of ptr_name that's not in zone_name
    relative = ptr_name.relativize(zone_name)
    return relative.to_text()


def is_reverse_zone(zone: str) -> bool:
    """Check if a zone name is a reverse DNS zone.

    Args:
        zone: Zone name

    Returns:
        True if zone is a reverse zone (in-addr.arpa or ip6.arpa)
    """
    zone_lower = zone.lower()
    return (
        zone_lower.endswith(".in-addr.arpa.")
        or zone_lower.endswith(".ip6.arpa.")
        or zone_lower.endswith(".in-addr.arpa")
        or zone_lower.endswith(".ip6.arpa")
    )


def ptr_name_to_ip(ptr_name: dns.name.Name) -> str:
    """Convert a PTR name back to an IP address.

    Args:
        ptr_name: PTR name (e.g., 100.2.0.192.in-addr.arpa.)

    Returns:
        IP address string

    Raises:
        dns.exception.SyntaxError: If ptr_name is not a valid reverse name
    """
    return dns.reversename.to_address(ptr_name)
