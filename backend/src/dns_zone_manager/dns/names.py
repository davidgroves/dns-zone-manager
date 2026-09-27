"""DNS name validation helpers."""

from __future__ import annotations

import re

import dns.exception
import dns.name

# Safe characters for Content-Disposition filenames derived from zone names.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ZONE_LABELS = 127
_MAX_ZONE_LEN = 253


class InvalidZoneNameError(ValueError):
    """Raised when a zone or record name fails validation."""


def normalize_zone_name(zone: str) -> str:
    """Validate and normalize a zone name (lowercase, trailing dot).

    Raises:
        InvalidZoneNameError: If the name is empty, too long, contains control
            characters, or is not a valid DNS name.
    """
    if not zone or not zone.strip():
        raise InvalidZoneNameError("Zone name must not be empty")
    zone = zone.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in zone):
        raise InvalidZoneNameError("Zone name must not contain control characters")
    if len(zone.rstrip(".")) > _MAX_ZONE_LEN:
        raise InvalidZoneNameError(f"Zone name exceeds {_MAX_ZONE_LEN} characters")
    if not zone.endswith("."):
        zone = zone + "."
    zone = zone.lower()
    try:
        name = dns.name.from_text(zone)
    except dns.exception.DNSException as e:
        raise InvalidZoneNameError(f"Invalid zone name: {e}") from e
    if len(name.labels) > _MAX_ZONE_LABELS:
        raise InvalidZoneNameError("Zone name has too many labels")
    return str(name)


def sanitize_zone_filename(zone: str) -> str:
    """Build a safe download filename from a zone name."""
    base = zone.rstrip(".").lower() or "zone"
    safe = _SAFE_FILENAME_RE.sub("_", base).strip("._") or "zone"
    return f"{safe}.zone"


def require_name_in_zone(fqdn: dns.name.Name, zone: str) -> None:
    """Require ``fqdn`` to be equal to or a subdomain of ``zone``.

    Raises:
        InvalidZoneNameError: If the name is outside the zone.
    """
    zone_name = dns.name.from_text(zone if zone.endswith(".") else zone + ".")
    if fqdn != zone_name and not fqdn.is_subdomain(zone_name):
        raise InvalidZoneNameError(f"Name '{fqdn}' is outside zone '{zone_name}'")
