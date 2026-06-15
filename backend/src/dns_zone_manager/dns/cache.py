"""Thread-safe zone cache with AXFR population and LRU eviction."""

import logging
import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING

import dns.exception
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.zone

from dns_zone_manager.dns.client import DNSClient, HistoryBatch, RRsetInfo, ZoneTransferError
from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import zone_transfers_failed, zone_transfers_total

if TYPE_CHECKING:
    from dns_zone_manager.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ZoneSearchResult:
    """Search results for a single zone."""

    zone: str
    serial: int
    rrsets: list[RRsetInfo]


@dataclass
class PaginatedRRsets:
    """Paginated RRset results."""

    rrsets: list[RRsetInfo]
    total_count: int
    has_more: bool
    next_cursor: str | None  # Name of next record for cursor-based pagination


@dataclass
class PaginatedSearchResult:
    """Paginated search results for a single zone."""

    zone: str
    serial: int
    rrsets: list[RRsetInfo]
    total_count: int
    has_more: bool
    next_cursor: str | None


@dataclass
class PaginatedGlobalSearchResult:
    """Paginated global search results across all zones."""

    zone_results: list[ZoneSearchResult]
    total_count: int
    has_more: bool
    next_cursor: str | None  # Format: "zone_name:record_name"


class CachedZone:
    """Cached zone data with metadata."""

    # Jitter range: use 90-100% of refresh interval to avoid stampeding herd
    JITTER_MIN = 0.9
    JITTER_MAX = 1.0

    def __init__(self, zone_name: str, zone_data: dns.zone.Zone, serial: int):
        self.zone_name = zone_name
        self.zone_data = zone_data
        self.serial = serial
        self.last_refresh = datetime.now(UTC)
        self._lock = RLock()
        self.soa_refresh: int | None = self._extract_soa_refresh()
        self._refresh_jitter: float = self._generate_jitter()

    def _generate_jitter(self) -> float:
        """Generate a random jitter factor between JITTER_MIN and JITTER_MAX.

        This spreads out refresh times for zones with identical SOA refresh
        values, avoiding the "stampeding herd" problem.

        Returns:
            Random float between JITTER_MIN and JITTER_MAX
        """
        return random.uniform(self.JITTER_MIN, self.JITTER_MAX)

    def regenerate_jitter(self) -> None:
        """Regenerate the jitter factor for the next refresh interval.

        Should be called after each successful refresh to spread out
        subsequent refresh times.
        """
        self._refresh_jitter = self._generate_jitter()

    def _extract_soa_refresh(self) -> int | None:
        """Extract refresh interval from SOA record.

        Returns:
            SOA refresh value in seconds, or None if not found
        """
        try:
            origin = self.zone_data.origin
            if origin is None:
                return None
            soa_rdataset = self.zone_data.find_rdataset(dns.name.empty, dns.rdatatype.SOA)
            for rdata in soa_rdataset:
                return rdata.refresh
        except KeyError:
            return None
        return None

    def get_effective_refresh_interval(
        self, min_interval: int = 60, max_interval: int = 86400
    ) -> int:
        """Get the effective refresh interval bounded by min/max with jitter.

        Applies a jitter factor (90-100% of the interval) to spread out
        refresh times for zones with identical SOA refresh values,
        avoiding the "stampeding herd" problem.

        Args:
            min_interval: Minimum refresh interval in seconds
            max_interval: Maximum refresh interval in seconds

        Returns:
            Bounded refresh interval in seconds with jitter applied
        """
        if self.soa_refresh is None:
            base_interval = max_interval
        else:
            base_interval = min(max(self.soa_refresh, min_interval), max_interval)
        # Apply jitter to spread out refresh times
        return int(base_interval * self._refresh_jitter)

    def _get_zone_origin(self) -> dns.name.Name | None:
        """Get the zone origin as a Name object."""
        origin = self.zone_data.origin
        if origin is None:
            return None
        if isinstance(origin, str):
            return dns.name.from_text(origin)
        return origin

    def get_rrset(self, name: str, rdtype: str, rdclass: str = "IN") -> RRsetInfo | None:
        """Get an RRset from the cached zone.

        Args:
            name: Record name (relative or absolute)
            rdtype: Record type
            rdclass: Record class (default: IN)

        Returns:
            RRsetInfo if found, None otherwise
        """
        with self._lock:
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                return None

            # Normalize the name
            if name.endswith("."):
                name_obj = dns.name.from_text(name)
                # Make relative to zone
                if name_obj.is_subdomain(zone_origin):
                    name_obj = name_obj.relativize(zone_origin)
            else:
                name_obj = dns.name.from_text(name, None)

            # Handle zone apex
            if str(name_obj) == "@" or name_obj == dns.name.empty:
                name_obj = dns.name.empty

            rdtype_obj = dns.rdatatype.from_text(rdtype)
            rdclass_obj = dns.rdataclass.from_text(rdclass)

            try:
                node = self.zone_data.find_node(name_obj)
                rdataset = node.find_rdataset(rdclass_obj, rdtype_obj)

                records = [rdata.to_text() for rdata in rdataset]
                fqdn = name_obj.derelativize(zone_origin)

                return RRsetInfo(
                    name=str(fqdn),
                    ttl=rdataset.ttl,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    records=records,
                )
            except KeyError:
                return None

    def get_all_rrsets(
        self,
        rdclass: str | None = None,
        after_name: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[RRsetInfo] | PaginatedRRsets:
        """Get all RRsets in the zone, optionally paginated.

        Args:
            rdclass: Optional class filter (None = all classes)
            after_name: Return records after this name (cursor-based pagination)
            limit: Maximum number of records to return
            offset: Start at this index (0-based, for direct page jumps)

        Returns:
            If neither after_name nor limit nor offset provided: List of all RRsets
            Otherwise: PaginatedRRsets with cursor information
        """
        with self._lock:
            rrsets: list[RRsetInfo] = []
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                if after_name is not None or limit is not None or offset is not None:
                    return PaginatedRRsets(
                        rrsets=[], total_count=0, has_more=False, next_cursor=None
                    )
                return rrsets

            rdclass_filter = None
            if rdclass is not None:
                rdclass_filter = dns.rdataclass.from_text(rdclass)

            # Collect all RRsets first
            all_rrsets: list[RRsetInfo] = []
            for name, node in self.zone_data.items():
                fqdn = name.derelativize(zone_origin)
                for rdataset in node.rdatasets:
                    # Filter by class if specified
                    if rdclass_filter is not None and rdataset.rdclass != rdclass_filter:
                        continue

                    records = [rdata.to_text() for rdata in rdataset]
                    rdtype = dns.rdatatype.to_text(rdataset.rdtype)
                    rdclass_str = dns.rdataclass.to_text(rdataset.rdclass)

                    all_rrsets.append(
                        RRsetInfo(
                            name=str(fqdn),
                            ttl=rdataset.ttl,
                            rdtype=rdtype,
                            rdclass=rdclass_str,
                            records=records,
                        )
                    )

            # Sort by name, then by type for consistent pagination
            all_rrsets.sort(key=lambda r: (r.name.lower(), r.rdtype))
            total_count = len(all_rrsets)

            # If no pagination requested, return simple list
            if after_name is None and limit is None and offset is None:
                return all_rrsets

            # Determine start_index: offset takes priority over cursor
            start_index = 0
            if offset is not None:
                start_index = max(0, min(offset, total_count))
            elif after_name is not None:
                # Parse cursor which may be "name" (legacy) or "name:type" (new format)
                # The cursor points to the first record of the next page
                if ":" in after_name and not after_name.startswith("xn--"):
                    # New format: "name:type" - but be careful not to split IDN punycode
                    # Find the last colon (type is always at end, names can have colons)
                    last_colon = after_name.rfind(":")
                    cursor_name = after_name[:last_colon].lower()
                    cursor_type = after_name[last_colon + 1 :]
                    # Find exact position by (name, type) comparison
                    for i, rrset in enumerate(all_rrsets):
                        key = (rrset.name.lower(), rrset.rdtype)
                        if key >= (cursor_name, cursor_type):
                            start_index = i
                            break
                    else:
                        start_index = total_count
                else:
                    # Legacy format: just name - find first record with this name
                    for i, rrset in enumerate(all_rrsets):
                        if rrset.name.lower() >= after_name.lower():
                            start_index = i
                            break
                    else:
                        start_index = total_count

            # Apply limit
            if limit is not None:
                end_index = min(start_index + limit, total_count)
            else:
                end_index = total_count

            result_rrsets = all_rrsets[start_index:end_index]
            has_more = end_index < total_count
            # Use "name:type" format for cursor to handle multiple records per name
            next_cursor = (
                f"{all_rrsets[end_index].name}:{all_rrsets[end_index].rdtype}" if has_more else None
            )

            return PaginatedRRsets(
                rrsets=result_rrsets,
                total_count=total_count,
                has_more=has_more,
                next_cursor=next_cursor,
            )

    def get_rrsets_by_name(self, name: str, rdclass: str | None = None) -> list[RRsetInfo]:
        """Get all RRsets for a specific name.

        Args:
            name: Record name
            rdclass: Optional class filter (None = all classes)

        Returns:
            List of RRsets at that name
        """
        with self._lock:
            zone_origin = self._get_zone_origin()
            rrsets: list[RRsetInfo] = []
            if zone_origin is None:
                return rrsets

            # Normalize the name
            if name.endswith("."):
                name_obj = dns.name.from_text(name)
                if name_obj.is_subdomain(zone_origin):
                    name_obj = name_obj.relativize(zone_origin)
            else:
                name_obj = dns.name.from_text(name, None)

            if str(name_obj) == "@" or name_obj == dns.name.empty:
                name_obj = dns.name.empty

            rdclass_filter = None
            if rdclass is not None:
                rdclass_filter = dns.rdataclass.from_text(rdclass)

            try:
                node = self.zone_data.find_node(name_obj)
                fqdn = name_obj.derelativize(zone_origin)

                for rdataset in node.rdatasets:
                    # Filter by class if specified
                    if rdclass_filter is not None and rdataset.rdclass != rdclass_filter:
                        continue

                    records = [rdata.to_text() for rdata in rdataset]
                    rdtype = dns.rdatatype.to_text(rdataset.rdtype)
                    rdclass_str = dns.rdataclass.to_text(rdataset.rdclass)

                    rrsets.append(
                        RRsetInfo(
                            name=str(fqdn),
                            ttl=rdataset.ttl,
                            rdtype=rdtype,
                            rdclass=rdclass_str,
                            records=records,
                        )
                    )
            except KeyError:
                pass

            return rrsets

    def search_rrsets(
        self,
        name_pattern: re.Pattern[str] | None = None,
        rdtype: str | None = None,
        rdclass: str | None = None,
        value_pattern: re.Pattern[str] | None = None,
        after: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[RRsetInfo] | tuple[list[RRsetInfo], int, str | None, bool]:
        """Search for RRsets matching the given criteria.

        Args:
            name_pattern: Compiled regex pattern to match record names
            rdtype: Optional record type filter (e.g., "CNAME", "A")
            rdclass: Optional record class filter (e.g., "IN", "CH")
            value_pattern: Compiled regex pattern to match record values
            after: Return results after this record name (cursor-based pagination)
            limit: Maximum number of results to return
            offset: Start at this index (0-based, for direct page jumps)

        Returns:
            If no pagination params: List of matching RRsets
            If pagination params: Tuple of (rrsets, total_count, next_cursor, has_more)
        """
        with self._lock:
            results: list[RRsetInfo] = []
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                if after is not None or limit is not None:
                    return [], 0, None, False
                return results

            rdclass_filter = None
            if rdclass is not None:
                rdclass_filter = dns.rdataclass.from_text(rdclass)

            for name, node in self.zone_data.items():
                fqdn = name.derelativize(zone_origin)
                fqdn_str = str(fqdn)

                # Check name pattern if provided
                if name_pattern is not None and not name_pattern.search(fqdn_str):
                    continue

                for rdataset in node.rdatasets:
                    # Filter by class if specified
                    if rdclass_filter is not None and rdataset.rdclass != rdclass_filter:
                        continue

                    rdtype_str = dns.rdatatype.to_text(rdataset.rdtype)
                    rdclass_str = dns.rdataclass.to_text(rdataset.rdclass)

                    # Check record type filter if provided
                    if rdtype is not None and rdtype_str.upper() != rdtype.upper():
                        continue

                    records = [rdata.to_text() for rdata in rdataset]

                    # Check value pattern if provided
                    if value_pattern is not None:
                        matching_records = [r for r in records if value_pattern.search(r)]
                        if not matching_records:
                            continue
                        # Only include records that match
                        records = matching_records

                    results.append(
                        RRsetInfo(
                            name=fqdn_str,
                            ttl=rdataset.ttl,
                            rdtype=rdtype_str,
                            rdclass=rdclass_str,
                            records=records,
                        )
                    )

            # If no pagination requested, return simple list
            if after is None and limit is None and offset is None:
                return results

            # Sort for consistent pagination
            results.sort(key=lambda r: (r.name.lower(), r.rdtype))
            total_count = len(results)

            # Determine start_index: offset takes priority over cursor
            start_index = 0
            if offset is not None:
                start_index = max(0, min(offset, total_count))
            elif after is not None:
                # Find the position at or after the cursor
                # (next_cursor points to the first record of the next page)
                after_lower = after.lower()
                for i, rrset in enumerate(results):
                    if rrset.name.lower() >= after_lower:
                        start_index = i
                        break
                else:
                    start_index = total_count

            # Apply limit
            if limit is not None:
                end_index = min(start_index + limit, total_count)
            else:
                end_index = total_count

            result_rrsets = results[start_index:end_index]
            has_more = end_index < total_count
            next_cursor = results[end_index].name if has_more else None

            return result_rrsets, total_count, next_cursor, has_more

    def update_rrset(
        self,
        name: str,
        ttl: int,
        rdtype: str,
        records: list[str],
        rdclass: str = "IN",
    ) -> None:
        """Update an RRset in the cache.

        Args:
            name: Record name
            ttl: TTL
            rdtype: Record type
            records: New record values
            rdclass: Record class (default: IN)
        """
        with self._lock:
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                return

            # Normalize name
            if name.endswith("."):
                name_obj = dns.name.from_text(name)
                if name_obj.is_subdomain(zone_origin):
                    name_obj = name_obj.relativize(zone_origin)
            else:
                name_obj = dns.name.from_text(name, None)

            if str(name_obj) == "@" or name_obj == dns.name.empty:
                name_obj = dns.name.empty

            rdtype_obj = dns.rdatatype.from_text(rdtype)
            rdclass_obj = dns.rdataclass.from_text(rdclass)

            # Get or create node
            try:
                node = self.zone_data.find_node(name_obj)
            except KeyError:
                node = self.zone_data.find_node(name_obj, create=True)

            # Remove existing rdataset if present
            try:
                node.delete_rdataset(rdclass_obj, rdtype_obj)
            except KeyError:
                pass

            # Add new records
            rdataset = node.find_rdataset(rdclass_obj, rdtype_obj, create=True)
            rdataset.update_ttl(ttl)

            for record in records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                rdataset.add(rdata)

    def delete_rrset(
        self,
        name: str,
        rdtype: str,
        records: list[str] | None = None,
        rdclass: str = "IN",
    ) -> None:
        """Delete an RRset or specific records from the cache.

        Args:
            name: Record name
            rdtype: Record type
            records: Specific records to delete (None = delete entire RRset)
            rdclass: Record class (default: IN)
        """
        with self._lock:
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                return

            # Normalize name
            if name.endswith("."):
                name_obj = dns.name.from_text(name)
                if name_obj.is_subdomain(zone_origin):
                    name_obj = name_obj.relativize(zone_origin)
            else:
                name_obj = dns.name.from_text(name, None)

            if str(name_obj) == "@" or name_obj == dns.name.empty:
                name_obj = dns.name.empty

            rdtype_obj = dns.rdatatype.from_text(rdtype)
            rdclass_obj = dns.rdataclass.from_text(rdclass)

            try:
                node = self.zone_data.find_node(name_obj)

                if records is None:
                    # Delete entire RRset
                    node.delete_rdataset(rdclass_obj, rdtype_obj)
                else:
                    # Delete specific records
                    rdataset = node.find_rdataset(rdclass_obj, rdtype_obj)
                    for record in records:
                        rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                        rdataset.discard(rdata)

                    # If no records left, remove the rdataset
                    if len(rdataset) == 0:
                        node.delete_rdataset(rdclass_obj, rdtype_obj)

            except KeyError:
                pass  # Already doesn't exist

    @property
    def rrset_count(self) -> int:
        """Count total RRsets in the zone."""
        with self._lock:
            count = 0
            for _name, node in self.zone_data.items():
                count += len(node.rdatasets)
            return count

    def estimate_size_bytes(self) -> int:
        """Estimate memory usage of this cached zone.

        Provides a rough estimate of the memory footprint by counting
        records and estimating overhead per object. This is not exact
        but gives a reasonable approximation for cache size limiting.

        Returns:
            Estimated size in bytes
        """
        with self._lock:
            # Base overhead for CachedZone object itself
            size = 500

            # Iterate through all records and estimate size
            for name, node in self.zone_data.items():
                # Name object overhead (dns.name.Name + labels)
                size += 100 + len(str(name)) * 2

                for rdataset in node.rdatasets:
                    # Rdataset overhead
                    size += 50

                    for rdata in rdataset:
                        # Record data + rdata object overhead
                        size += len(rdata.to_text()) + 50

            return size

    def apply_ixfr_changes(
        self,
        batches: list[HistoryBatch],
        new_serial: int,
    ) -> tuple[int, int]:
        """Apply incremental changes from IXFR to the cached zone data.

        Processes IXFR batches in order, applying deletions then additions
        for each batch. Updates the zone serial after all changes are applied.

        Args:
            batches: List of HistoryBatch from IXFR result
            new_serial: The new serial number after applying all changes

        Returns:
            Tuple of (total_deletes, total_adds) applied
        """
        total_deletes = 0
        total_adds = 0

        with self._lock:
            zone_origin = self._get_zone_origin()
            if zone_origin is None:
                return (0, 0)

            for batch in batches:
                for change in batch.changes:
                    # Normalize the name to be relative to zone
                    if change.name.endswith("."):
                        name_obj = dns.name.from_text(change.name)
                        if name_obj.is_subdomain(zone_origin):
                            name_obj = name_obj.relativize(zone_origin)
                    else:
                        name_obj = dns.name.from_text(change.name, None)

                    # Handle zone apex
                    if str(name_obj) == "@" or name_obj == dns.name.empty:
                        name_obj = dns.name.empty

                    rdtype_obj = dns.rdatatype.from_text(change.rdtype)
                    rdclass_obj = dns.rdataclass.from_text(change.rdclass)

                    if change.action == "delete":
                        # Delete specific records
                        try:
                            node = self.zone_data.find_node(name_obj)
                            rdataset = node.find_rdataset(rdclass_obj, rdtype_obj)
                            for record in change.records:
                                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                                rdataset.discard(rdata)
                                total_deletes += 1

                            # Remove empty rdataset
                            if len(rdataset) == 0:
                                node.delete_rdataset(rdclass_obj, rdtype_obj)
                        except KeyError:
                            # Record doesn't exist, skip
                            pass

                    elif change.action == "add":
                        # Add records
                        try:
                            node = self.zone_data.find_node(name_obj)
                        except KeyError:
                            node = self.zone_data.find_node(name_obj, create=True)

                        rdataset = node.find_rdataset(rdclass_obj, rdtype_obj, create=True)
                        rdataset.update_ttl(change.ttl)

                        for record in change.records:
                            rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                            rdataset.add(rdata)
                            total_adds += 1

            # Update serial and SOA record
            self.serial = new_serial
            self._update_soa_serial(new_serial)
            self.last_refresh = datetime.now(UTC)
            # Regenerate jitter for next refresh cycle
            self.regenerate_jitter()

        return (total_deletes, total_adds)

    def _update_soa_serial(self, new_serial: int) -> None:
        """Update the SOA serial in the zone data.

        Args:
            new_serial: New serial number to set
        """
        try:
            soa_rdataset = self.zone_data.find_rdataset(dns.name.empty, dns.rdatatype.SOA)
            if soa_rdataset:
                for old_soa in list(soa_rdataset):
                    new_soa = dns.rdata.from_text(
                        dns.rdataclass.IN,
                        dns.rdatatype.SOA,
                        f"{old_soa.mname} {old_soa.rname} {new_serial} "
                        f"{old_soa.refresh} {old_soa.retry} {old_soa.expire} "
                        f"{old_soa.minimum}",
                    )
                    soa_rdataset.discard(old_soa)
                    soa_rdataset.add(new_soa)
                    break  # Only one SOA record
        except (KeyError, dns.exception.DNSException):
            pass  # SOA not found or update failed


class ZoneCache:
    """Thread-safe cache for multiple DNS zones with LRU eviction."""

    def __init__(self, settings: "Settings", dns_client: DNSClient):
        """Initialize zone cache.

        Args:
            settings: Application settings
            dns_client: DNS client for zone transfers
        """
        self.settings = settings
        self.dns_client = dns_client
        # OrderedDict for O(1) LRU operations - most recently used at end
        self._zones: OrderedDict[str, CachedZone] = OrderedDict()
        self._catalog_zones: set[str] = set()  # Track zones from catalog
        self._lock = RLock()
        self.enabled = settings.cache.enabled
        self._current_size_bytes: int = 0

    def _normalize_zone_name(self, zone: str) -> str:
        """Ensure zone name ends with a dot."""
        if not zone.endswith("."):
            zone = zone + "."
        return zone.lower()

    def _evict_if_needed(self, required_bytes: int) -> int:
        """Evict LRU zones until there's room for required_bytes.

        Must be called with self._lock held.

        Args:
            required_bytes: Number of bytes needed for the new zone

        Returns:
            Number of zones evicted
        """
        # Import here to avoid circular import at module level
        from dns_zone_manager.metrics import cache_evictions_total

        max_size = self.settings.cache.max_size_bytes
        if max_size == 0:  # Unlimited
            return 0

        evicted_count = 0
        while self._current_size_bytes + required_bytes > max_size and self._zones:
            # Pop from front (least recently used)
            zone_name, cached = self._zones.popitem(last=False)
            evicted_size = cached.estimate_size_bytes()
            self._current_size_bytes -= evicted_size
            self._catalog_zones.discard(zone_name)
            evicted_count += 1

            cache_evictions_total.inc()
            log_internal_event(
                "cache_evicted",
                logger,
                zone=zone_name,
                evicted_size_bytes=evicted_size,
                current_size_bytes=self._current_size_bytes,
                reason="lru",
            )

        return evicted_count

    def _mark_recently_used(self, zone: str) -> None:
        """Mark a zone as recently used by moving it to end of LRU.

        Must be called with self._lock held.

        Args:
            zone: Normalized zone name
        """
        if zone in self._zones:
            self._zones.move_to_end(zone)

    def refresh_zone(self, zone: str, force_axfr: bool = False) -> CachedZone:
        """Refresh a zone from the DNS server, preferring IXFR when possible.

        When the zone is already cached and prefer_ixfr is enabled:
        1. Query current server serial via SOA
        2. If serial unchanged, skip refresh (just update last_refresh timestamp)
        3. If serial changed, attempt IXFR from cached serial
        4. If IXFR returns true incremental data, apply changes
        5. If IXFR falls back to AXFR, replace entire zone
        6. Fall back to full AXFR on any IXFR error

        Args:
            zone: Zone name to refresh
            force_axfr: Force full AXFR even if IXFR is possible

        Returns:
            Refreshed CachedZone

        Raises:
            ZoneTransferError: If zone transfer fails
        """
        zone = self._normalize_zone_name(zone)

        # Check if zone is already cached
        with self._lock:
            existing_cached = self._zones.get(zone)

        # Check if we should try IXFR
        prefer_ixfr = getattr(self.settings, "notify", None)
        prefer_ixfr = prefer_ixfr.prefer_ixfr if prefer_ixfr else True

        if existing_cached is not None and prefer_ixfr and not force_axfr:
            # Try IXFR-based refresh
            result = self._refresh_zone_ixfr(zone, existing_cached)
            if result is not None:
                return result
            # IXFR failed, fall back to AXFR
            log_internal_event(
                "ixfr_fallback_to_axfr",
                logger,
                zone=zone,
                cached_serial=existing_cached.serial,
            )

        # Perform full AXFR
        return self._refresh_zone_axfr(zone)

    def _refresh_zone_axfr(self, zone: str) -> CachedZone:
        """Refresh a zone using full AXFR.

        Args:
            zone: Normalized zone name

        Returns:
            Refreshed CachedZone

        Raises:
            ZoneTransferError: If AXFR fails
        """
        # Import here to avoid circular import at module level
        from dns_zone_manager.metrics import cache_size_bytes

        log_internal_event("zone_refresh_start", logger, zone=zone, method="axfr")
        try:
            zone_data = self.dns_client.perform_axfr(zone)
        except ZoneTransferError:
            zone_transfers_failed.labels(method="axfr", zone=zone).inc()
            raise

        serial = self._get_serial_from_zone(zone_data)

        cached = CachedZone(zone, zone_data, serial)
        new_size = cached.estimate_size_bytes()

        with self._lock:
            # Track size of old zone if replacing
            old_cached = self._zones.get(zone)
            if old_cached is not None:
                old_size = old_cached.estimate_size_bytes()
                self._current_size_bytes -= old_size
            else:
                # New zone - check if we need to evict
                self._evict_if_needed(new_size)

            self._zones[zone] = cached
            self._zones.move_to_end(zone)  # Mark as recently used
            self._current_size_bytes += new_size

            # Update gauge metric
            cache_size_bytes.set(self._current_size_bytes)

        zone_transfers_total.labels(method="axfr", zone=zone).inc()

        log_internal_event(
            "zone_refresh_complete",
            logger,
            zone=zone,
            serial=serial,
            rrset_count=cached.rrset_count,
            method="axfr",
            size_bytes=new_size,
        )
        return cached

    def _refresh_zone_ixfr(self, zone: str, cached: CachedZone) -> CachedZone | None:
        """Attempt to refresh a zone using IXFR.

        Args:
            zone: Normalized zone name
            cached: Existing cached zone

        Returns:
            Updated CachedZone if IXFR succeeded, None if should fall back to AXFR
        """
        try:
            # Query current server serial
            server_serial = self.dns_client.get_zone_serial(zone)
            if server_serial is None:
                log_internal_event(
                    "ixfr_serial_query_failed",
                    logger,
                    level="WARNING",
                    zone=zone,
                )
                return None

            # If serial unchanged, just update timestamp
            if server_serial == cached.serial:
                log_internal_event(
                    "zone_refresh_skipped",
                    logger,
                    zone=zone,
                    serial=cached.serial,
                    reason="serial_unchanged",
                )
                cached.last_refresh = datetime.now(UTC)
                # Regenerate jitter for next refresh cycle
                cached.regenerate_jitter()
                return cached

            log_internal_event(
                "zone_refresh_start",
                logger,
                zone=zone,
                method="ixfr",
                from_serial=cached.serial,
                to_serial=server_serial,
            )

            # Attempt IXFR from cached serial
            ixfr_result = self.dns_client.perform_ixfr(zone, cached.serial)

            # Check if server returned full AXFR instead of incremental
            if ixfr_result.is_full_axfr:
                log_internal_event(
                    "ixfr_server_returned_axfr",
                    logger,
                    zone=zone,
                    from_serial=cached.serial,
                )
                return None  # Fall back to our AXFR code path

            # Apply incremental changes
            deletes, adds = cached.apply_ixfr_changes(
                ixfr_result.batches,
                ixfr_result.current_serial,
            )

            zone_transfers_total.labels(method="ixfr", zone=zone).inc()

            log_internal_event(
                "zone_refresh_complete",
                logger,
                zone=zone,
                serial=cached.serial,
                rrset_count=cached.rrset_count,
                method="ixfr",
                deletes=deletes,
                adds=adds,
                batch_count=len(ixfr_result.batches),
            )

            return cached

        except ZoneTransferError as e:
            log_internal_event(
                "ixfr_failed",
                logger,
                level="WARNING",
                zone=zone,
                error=str(e),
            )
            zone_transfers_failed.labels(method="ixfr", zone=zone).inc()
            return None
        except Exception as e:
            log_internal_event(
                "ixfr_error",
                logger,
                level="ERROR",
                zone=zone,
                error=str(e),
            )
            zone_transfers_failed.labels(method="ixfr", zone=zone).inc()
            return None

    def _get_serial_from_zone(self, zone_data: dns.zone.Zone) -> int:
        """Extract SOA serial from zone data."""
        try:
            soa_rdataset = zone_data.find_rdataset(
                dns.name.empty,
                dns.rdatatype.SOA,
            )
            for rdata in soa_rdataset:
                return rdata.serial
        except KeyError:
            pass
        return 0

    def get_zone(self, zone: str) -> CachedZone | None:
        """Get a cached zone, optionally loading it if not cached.

        Args:
            zone: Zone name

        Returns:
            CachedZone if available, None otherwise
        """
        zone = self._normalize_zone_name(zone)

        with self._lock:
            cached = self._zones.get(zone)
            if cached is not None:
                # Mark as recently used for LRU tracking
                self._mark_recently_used(zone)

        if cached is None and self.enabled:
            # Try to load the zone
            try:
                cached = self.refresh_zone(zone)
            except ZoneTransferError:
                return None

        return cached

    def get_or_refresh_zone(self, zone: str) -> CachedZone:
        """Get a zone, refreshing if necessary.

        Args:
            zone: Zone name

        Returns:
            CachedZone

        Raises:
            ZoneTransferError: If zone cannot be loaded
        """
        zone = self._normalize_zone_name(zone)
        cached = self.get_zone(zone)

        if cached is None:
            cached = self.refresh_zone(zone)

        return cached

    def get_rrset(self, zone: str, name: str, rdtype: str, rdclass: str = "IN") -> RRsetInfo | None:
        """Get an RRset from the cache.

        Args:
            zone: Zone name
            name: Record name
            rdtype: Record type
            rdclass: Record class (default: IN)

        Returns:
            RRsetInfo if found, None otherwise
        """
        cached = self.get_zone(zone)
        if cached is None:
            return None

        return cached.get_rrset(name, rdtype, rdclass)

    def refresh_zone_serial(self, zone: str) -> int | None:
        """Refresh the serial for a zone by querying the SOA.

        This is called after DDNS updates to get the new serial from BIND.
        Updates both the CachedZone.serial attribute and the SOA record data.

        Args:
            zone: Zone name

        Returns:
            New serial number or None if refresh failed
        """
        zone = self._normalize_zone_name(zone)
        cached = self.get_zone(zone)
        if not cached:
            return None

        new_serial = self.dns_client.get_zone_serial(zone)
        if new_serial is not None:
            with cached._lock:
                cached.serial = new_serial
                # Also update the SOA record in the zone data
                try:
                    soa_rdataset = cached.zone_data.find_rdataset(dns.name.empty, dns.rdatatype.SOA)
                    if soa_rdataset:
                        # Get the current SOA rdata
                        for old_soa in list(soa_rdataset):
                            # Create new SOA with updated serial
                            new_soa = dns.rdata.from_text(
                                dns.rdataclass.IN,
                                dns.rdatatype.SOA,
                                f"{old_soa.mname} {old_soa.rname} {new_serial} "
                                f"{old_soa.refresh} {old_soa.retry} {old_soa.expire} "
                                f"{old_soa.minimum}",
                            )
                            # Replace old with new
                            soa_rdataset.discard(old_soa)
                            soa_rdataset.add(new_soa)
                            break  # Only one SOA record
                except (KeyError, dns.exception.DNSException):
                    pass  # SOA not found or update failed, serial attr is still updated
        return new_serial

    def update_cache_after_add(
        self,
        zone: str,
        name: str,
        ttl: int,
        rdtype: str,
        records: list[str],
        rdclass: str = "IN",
    ) -> None:
        """Update cache after adding records.

        Args:
            zone: Zone name
            name: Record name
            ttl: TTL
            rdtype: Record type
            records: Added records
            rdclass: Record class (default: IN)
        """
        cached = self.get_zone(zone)
        if cached:
            cached.update_rrset(name, ttl, rdtype, records, rdclass)
        # Refresh serial from DNS server
        self.refresh_zone_serial(zone)

    def update_cache_after_delete(
        self,
        zone: str,
        name: str,
        rdtype: str,
        records: list[str] | None = None,
        rdclass: str = "IN",
    ) -> None:
        """Update cache after deleting records.

        Args:
            zone: Zone name
            name: Record name
            rdtype: Record type
            records: Deleted records (None = entire RRset)
            rdclass: Record class (default: IN)
        """
        cached = self.get_zone(zone)
        if cached:
            cached.delete_rrset(name, rdtype, records, rdclass)
        # Refresh serial from DNS server
        self.refresh_zone_serial(zone)

    def update_cache_after_replace(
        self,
        zone: str,
        name: str,
        ttl: int,
        rdtype: str,
        records: list[str],
        rdclass: str = "IN",
    ) -> None:
        """Update cache after replacing an RRset.

        Args:
            zone: Zone name
            name: Record name
            ttl: New TTL
            rdtype: Record type
            records: New records
            rdclass: Record class (default: IN)
        """
        cached = self.get_zone(zone)
        if cached:
            cached.update_rrset(name, ttl, rdtype, records, rdclass)
        # Refresh serial from DNS server
        self.refresh_zone_serial(zone)

    def list_zones(self) -> list[str]:
        """List all cached zone names.

        Returns:
            List of zone names
        """
        with self._lock:
            return list(self._zones.keys())

    def list_zones_paginated(
        self,
        after: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple[list["CachedZone"], int, str | None, bool]:
        """List cached zones with optional pagination.

        Args:
            after: Return zones after this zone name (cursor-based pagination)
            limit: Maximum number of zones to return
            offset: Start at this index (0-based, for direct page jumps)

        Returns:
            Tuple of (zones, total_count, next_cursor, has_more)
        """
        with self._lock:
            # Get all zones sorted alphabetically for consistent pagination
            all_zones = sorted(self._zones.keys(), key=str.lower)
            total_count = len(all_zones)

            # If no pagination requested, return all zones
            if after is None and limit is None and offset is None:
                cached_zones = [self._zones[z] for z in all_zones]
                return cached_zones, total_count, None, False

            # Determine start_index: offset takes priority over cursor
            start_index = 0
            if offset is not None:
                start_index = max(0, min(offset, total_count))
            elif after is not None:
                # Find the position at or after the cursor
                # (next_cursor points to the first zone of the next page)
                after_lower = after.lower()
                for i, zone_name in enumerate(all_zones):
                    if zone_name.lower() >= after_lower:
                        start_index = i
                        break
                else:
                    # Cursor is past all zones
                    start_index = total_count

            # Apply limit
            if limit is not None:
                end_index = min(start_index + limit, total_count)
            else:
                end_index = total_count

            result_zone_names = all_zones[start_index:end_index]
            cached_zones = [self._zones[z] for z in result_zone_names]
            has_more = end_index < total_count
            next_cursor = all_zones[end_index] if has_more else None

            return cached_zones, total_count, next_cursor, has_more

    def search_zone(
        self,
        zone: str,
        name_pattern: re.Pattern[str] | None = None,
        rdtype: str | None = None,
        rdclass: str | None = None,
        value_pattern: re.Pattern[str] | None = None,
        after: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> ZoneSearchResult | PaginatedSearchResult | None:
        """Search for RRsets in a specific zone.

        Args:
            zone: Zone name to search
            name_pattern: Compiled regex pattern to match record names
            rdtype: Optional record type filter
            rdclass: Optional record class filter
            value_pattern: Compiled regex pattern to match record values
            after: Return results after this record name (cursor)
            limit: Maximum results to return
            offset: Start at this index (0-based, for direct page jumps)

        Returns:
            ZoneSearchResult (no pagination) or PaginatedSearchResult, or None if zone not found
        """
        cached = self.get_zone(zone)
        if cached is None:
            return None

        result = cached.search_rrsets(
            name_pattern, rdtype, rdclass, value_pattern, after, limit, offset
        )

        # Check if pagination was used
        if isinstance(result, tuple):
            rrsets, total_count, next_cursor, has_more = result
            return PaginatedSearchResult(
                zone=cached.zone_name,
                serial=cached.serial,
                rrsets=rrsets,
                total_count=total_count,
                has_more=has_more,
                next_cursor=next_cursor,
            )

        return ZoneSearchResult(
            zone=cached.zone_name,
            serial=cached.serial,
            rrsets=result,
        )

    def search_all_zones(
        self,
        name_pattern: re.Pattern[str] | None = None,
        rdtype: str | None = None,
        rdclass: str | None = None,
        value_pattern: re.Pattern[str] | None = None,
        after: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ZoneSearchResult] | PaginatedGlobalSearchResult:
        """Search for RRsets across all cached zones.

        Args:
            name_pattern: Compiled regex pattern to match record names
            rdtype: Optional record type filter
            rdclass: Optional record class filter
            value_pattern: Compiled regex pattern to match record values
            after: Cursor for pagination (format: "zone_name:record_name")
            limit: Maximum total results to return
            offset: Start at this index (0-based, for direct page jumps)

        Returns:
            List of ZoneSearchResult (no pagination) or PaginatedGlobalSearchResult
        """
        # Collect all matching results from all zones
        all_results: list[tuple[str, int, RRsetInfo]] = []  # (zone, serial, rrset)

        with self._lock:
            zones = list(self._zones.values())

        zone_serials: dict[str, int] = {}
        for cached in zones:
            zone_serials[cached.zone_name] = cached.serial
            # Get all matching rrsets without pagination
            rrsets = cached.search_rrsets(name_pattern, rdtype, rdclass, value_pattern)
            if isinstance(rrsets, tuple):
                rrsets = rrsets[0]  # Just get the list part
            for rrset in rrsets:
                all_results.append((cached.zone_name, cached.serial, rrset))

        # If no pagination requested, return grouped results
        if after is None and limit is None and offset is None:
            results: list[ZoneSearchResult] = []
            zone_rrsets: dict[str, list[RRsetInfo]] = {}
            for zone_name, _serial, rrset in all_results:
                if zone_name not in zone_rrsets:
                    zone_rrsets[zone_name] = []
                zone_rrsets[zone_name].append(rrset)

            for zone_name, rrsets_list in zone_rrsets.items():
                results.append(
                    ZoneSearchResult(
                        zone=zone_name,
                        serial=zone_serials[zone_name],
                        rrsets=rrsets_list,
                    )
                )
            return results

        # Sort by zone then record name for consistent pagination
        all_results.sort(key=lambda x: (x[0].lower(), x[2].name.lower(), x[2].rdtype))
        total_count = len(all_results)

        # Determine start_index: offset takes priority over cursor
        start_index = 0
        if offset is not None:
            start_index = max(0, min(offset, total_count))
        elif after is not None:
            # Parse cursor (format: "zone_name:record_name")
            # next_cursor points to the first record of the next page
            if ":" in after:
                cursor_zone, cursor_name = after.split(":", 1)
                cursor_zone = cursor_zone.lower()
                cursor_name = cursor_name.lower()
                for i, (zone_name, _serial, rrset) in enumerate(all_results):
                    # Find first result at or after the cursor
                    if (zone_name.lower(), rrset.name.lower()) >= (cursor_zone, cursor_name):
                        start_index = i
                        break
                else:
                    start_index = total_count
            else:
                # Just a record name, find first match at or after it
                cursor_name = after.lower()
                for i, (_zone_name, _serial, rrset) in enumerate(all_results):
                    if rrset.name.lower() >= cursor_name:
                        start_index = i
                        break
                else:
                    start_index = total_count

        # Apply limit
        if limit is not None:
            end_index = min(start_index + limit, total_count)
        else:
            end_index = total_count

        result_slice = all_results[start_index:end_index]
        has_more = end_index < total_count

        # Build next cursor
        next_cursor = None
        if has_more:
            last_zone, _serial, last_rrset = all_results[end_index - 1]
            next_cursor = f"{last_zone}:{last_rrset.name}"

        # Group results by zone
        zone_results: list[ZoneSearchResult] = []
        zone_rrsets_map: dict[str, list[RRsetInfo]] = {}
        for zone_name, _serial, rrset in result_slice:
            if zone_name not in zone_rrsets_map:
                zone_rrsets_map[zone_name] = []
            zone_rrsets_map[zone_name].append(rrset)

        for zone_name, rrsets_list in zone_rrsets_map.items():
            zone_results.append(
                ZoneSearchResult(
                    zone=zone_name,
                    serial=zone_serials[zone_name],
                    rrsets=rrsets_list,
                )
            )

        return PaginatedGlobalSearchResult(
            zone_results=zone_results,
            total_count=total_count,
            has_more=has_more,
            next_cursor=next_cursor,
        )

    def invalidate_zone(self, zone: str) -> None:
        """Remove a zone from the cache.

        Args:
            zone: Zone name to invalidate
        """
        # Import here to avoid circular import at module level
        from dns_zone_manager.metrics import cache_size_bytes

        zone = self._normalize_zone_name(zone)
        with self._lock:
            cached = self._zones.pop(zone, None)
            if cached is not None:
                self._current_size_bytes -= cached.estimate_size_bytes()
                cache_size_bytes.set(self._current_size_bytes)

    def invalidate_all(self) -> None:
        """Clear all cached zones."""
        # Import here to avoid circular import at module level
        from dns_zone_manager.metrics import cache_size_bytes

        with self._lock:
            self._zones.clear()
            self._catalog_zones.clear()
            self._current_size_bytes = 0
            cache_size_bytes.set(0)

    def is_catalog_zone(self, zone: str) -> bool:
        """Check if a zone was discovered from the catalog.

        Args:
            zone: Zone name

        Returns:
            True if zone came from catalog, False otherwise
        """
        zone = self._normalize_zone_name(zone)
        with self._lock:
            return zone in self._catalog_zones

    def get_catalog_zones(self) -> list[str]:
        """Get list of zones discovered from catalog.

        Returns:
            List of zone names from catalog
        """
        with self._lock:
            return list(self._catalog_zones)

    def sync_from_catalog(
        self,
        zone_names: list[str],
        remove_stale: bool = False,
    ) -> dict[str, str]:
        """Synchronize zones from catalog zone discovery.

        Args:
            zone_names: List of zone names discovered from catalog
            remove_stale: If True, remove zones no longer in catalog

        Returns:
            Dict mapping zone names to sync status ("added", "exists", "failed", "removed")
        """
        # Import here to avoid circular import at module level
        from dns_zone_manager.metrics import cache_size_bytes

        results: dict[str, str] = {}
        normalized_zones = {self._normalize_zone_name(z) for z in zone_names}

        # Handle stale zones removal
        if remove_stale:
            with self._lock:
                stale_zones = self._catalog_zones - normalized_zones
                for zone in stale_zones:
                    cached = self._zones.pop(zone, None)
                    if cached is not None:
                        self._current_size_bytes -= cached.estimate_size_bytes()
                    self._catalog_zones.discard(zone)
                    results[zone] = "removed"
                    log_internal_event(
                        "catalog_zone_removed",
                        logger,
                        zone=zone,
                    )
                cache_size_bytes.set(self._current_size_bytes)

        # Add/refresh new zones
        for zone_name in zone_names:
            zone = self._normalize_zone_name(zone_name)

            with self._lock:
                already_cached = zone in self._zones

            if already_cached:
                # Mark as catalog zone if not already
                with self._lock:
                    self._catalog_zones.add(zone)
                    self._mark_recently_used(zone)
                results[zone] = "exists"
                continue

            # Try to AXFR the new zone
            try:
                log_internal_event(
                    "catalog_zone_loading",
                    logger,
                    zone=zone,
                )
                zone_data = self.dns_client.perform_axfr(zone)
                serial = self._get_serial_from_zone(zone_data)
                cached = CachedZone(zone, zone_data, serial)
                new_size = cached.estimate_size_bytes()

                with self._lock:
                    self._evict_if_needed(new_size)
                    self._zones[zone] = cached
                    self._zones.move_to_end(zone)
                    self._catalog_zones.add(zone)
                    self._current_size_bytes += new_size
                    cache_size_bytes.set(self._current_size_bytes)

                results[zone] = "added"
                log_internal_event(
                    "catalog_zone_added",
                    logger,
                    zone=zone,
                    serial=serial,
                    rrset_count=cached.rrset_count,
                    size_bytes=new_size,
                )

            except ZoneTransferError as e:
                log_internal_event(
                    "catalog_zone_load_failed",
                    logger,
                    level="WARNING",
                    zone=zone,
                    error=str(e),
                )
                results[zone] = "failed"
            except Exception as e:
                log_internal_event(
                    "catalog_zone_load_error",
                    logger,
                    level="ERROR",
                    zone=zone,
                    error=str(e),
                )
                results[zone] = "failed"

        return results

    def get_zones_needing_refresh(self) -> list[str]:
        """Get zones that are past their SOA refresh time.

        Uses the zone's SOA refresh value bounded by the configured min/max
        refresh intervals to determine if a zone needs refreshing.

        Returns:
            List of zone names that need refreshing
        """
        now = datetime.now(UTC)
        min_interval = self.settings.cache.min_refresh_interval
        max_interval = self.settings.cache.max_refresh_interval
        zones_to_refresh: list[str] = []

        with self._lock:
            for zone_name, cached in self._zones.items():
                refresh_interval = cached.get_effective_refresh_interval(min_interval, max_interval)
                from datetime import timedelta

                next_refresh_time = cached.last_refresh + timedelta(seconds=refresh_interval)
                if now >= next_refresh_time:
                    zones_to_refresh.append(zone_name)

        return zones_to_refresh

    def get_next_refresh_time(self) -> datetime | None:
        """Get the earliest next refresh time across all zones.

        Returns:
            Datetime of the next zone refresh, or None if no zones are cached
        """
        from datetime import timedelta

        min_interval = self.settings.cache.min_refresh_interval
        max_interval = self.settings.cache.max_refresh_interval
        earliest: datetime | None = None

        with self._lock:
            for cached in self._zones.values():
                refresh_interval = cached.get_effective_refresh_interval(min_interval, max_interval)
                next_refresh = cached.last_refresh + timedelta(seconds=refresh_interval)
                if earliest is None or next_refresh < earliest:
                    earliest = next_refresh

        return earliest

    def get_zone_refresh_info(self) -> list[dict]:
        """Get refresh information for all cached zones.

        Returns:
            List of dicts with zone refresh details (zone, soa_refresh,
            effective_refresh, last_refresh, next_refresh)
        """
        from datetime import timedelta

        min_interval = self.settings.cache.min_refresh_interval
        max_interval = self.settings.cache.max_refresh_interval
        info: list[dict] = []

        with self._lock:
            for zone_name, cached in self._zones.items():
                effective_refresh = cached.get_effective_refresh_interval(
                    min_interval, max_interval
                )
                next_refresh = cached.last_refresh + timedelta(seconds=effective_refresh)
                info.append(
                    {
                        "zone": zone_name,
                        "soa_refresh": cached.soa_refresh,
                        "effective_refresh": effective_refresh,
                        "last_refresh": cached.last_refresh,
                        "next_refresh": next_refresh,
                    }
                )

        return info
