"""Shared DNS UPDATE message builder for atomic and scheduled changes.

Builds a single ``dns.update.Update`` from operations and prerequisites.
Auto-derived prerequisites are evaluated against the zone cache at build time
(execution time for scheduled changes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.update

from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient
from dns_zone_manager.dns.types import normalize_class
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    ChangePrerequisite,
    PrerequisitePreviewResult,
)


class UpdateBuildError(Exception):
    """Raised when an operation cannot be built into an UPDATE message."""

    def __init__(self, message: str, *, index: int | None = None, code: str | None = None):
        super().__init__(message)
        self.index = index
        self.code = code
        self.message = message


@dataclass
class CacheUpdate:
    """Describes a cache mutation to apply after a successful DDNS update."""

    action: Literal["add", "delete", "replace"]
    name: str
    rdtype: str
    rdclass: str
    ttl: int = 0
    records: list[str] | None = None


@dataclass
class BuiltUpdate:
    """Result of building a DNS UPDATE message."""

    update: dns.update.Update
    cache_updates: list[CacheUpdate] = field(default_factory=list)
    auto_prerequisites: list[PrerequisitePreviewResult] = field(default_factory=list)


def apply_explicit_prerequisites(
    update: dns.update.Update,
    zone: str,
    prerequisites: list[ChangePrerequisite],
    dns_client: DNSClient,
) -> None:
    """Add explicit user-supplied prerequisites to an UPDATE message."""
    for prereq in prerequisites:
        fqdn = dns_client.normalize_name(prereq.name, zone)
        if prereq.prereq_type == "nxdomain":
            update.absent(fqdn)
        elif prereq.prereq_type == "yxdomain":
            update.present(fqdn)
        elif prereq.prereq_type == "nxrrset":
            rdtype_obj = dns.rdatatype.from_text(prereq.rdtype.upper())  # type: ignore[union-attr]
            update.absent(fqdn, rdtype_obj)
        elif prereq.prereq_type == "yxrrset":
            rdtype_obj = dns.rdatatype.from_text(prereq.rdtype.upper())  # type: ignore[union-attr]
            if prereq.data:
                update.present(fqdn, rdtype_obj, prereq.data)
            else:
                update.present(fqdn, rdtype_obj)


def preview_explicit_prerequisites(
    zone: str,
    prerequisites: list[ChangePrerequisite],
    zone_cache: ZoneCache,
) -> list[PrerequisitePreviewResult]:
    """Evaluate explicit prerequisites against the current cache (dry-run)."""
    results: list[PrerequisitePreviewResult] = []
    for prereq in prerequisites:
        rdclass = normalize_class(prereq.rdclass)
        passed = False
        message = ""

        if prereq.prereq_type == "nxdomain":
            rrsets = zone_cache.get_zone(zone)
            # Check if any RRset exists at this name (approximation via cache)
            name_rrsets = []
            if rrsets is not None:
                name_rrsets = rrsets.get_rrsets_by_name(prereq.name, rdclass)
            passed = len(name_rrsets) == 0
            message = (
                "Name does not exist (pass)"
                if passed
                else f"Name exists with {len(name_rrsets)} RRset(s) (fail)"
            )
        elif prereq.prereq_type == "yxdomain":
            name_rrsets = []
            cached = zone_cache.get_zone(zone)
            if cached is not None:
                name_rrsets = cached.get_rrsets_by_name(prereq.name, rdclass)
            passed = len(name_rrsets) > 0
            message = (
                f"Name exists with {len(name_rrsets)} RRset(s) (pass)"
                if passed
                else "Name does not exist (fail)"
            )
        elif prereq.prereq_type == "nxrrset":
            rdtype = prereq.rdtype.upper() if prereq.rdtype else ""
            existing = zone_cache.get_rrset(zone, prereq.name, rdtype, rdclass)
            passed = existing is None
            message = (
                f"RRset {prereq.name} {rdclass} {rdtype} does not exist (pass)"
                if passed
                else f"RRset {prereq.name} {rdclass} {rdtype} already exists (fail)"
            )
        elif prereq.prereq_type == "yxrrset":
            rdtype = prereq.rdtype.upper() if prereq.rdtype else ""
            existing = zone_cache.get_rrset(zone, prereq.name, rdtype, rdclass)
            if existing is None:
                passed = False
                message = f"RRset {prereq.name} {rdclass} {rdtype} does not exist (fail)"
            elif prereq.data is not None:
                passed = prereq.data in existing.records
                message = (
                    f"RRset contains '{prereq.data}' (pass)"
                    if passed
                    else f"RRset exists but does not contain '{prereq.data}' (fail)"
                )
            else:
                passed = True
                message = f"RRset {prereq.name} {rdclass} {rdtype} exists (pass)"

        results.append(
            PrerequisitePreviewResult(
                prereq_type=prereq.prereq_type,
                name=prereq.name,
                rdtype=prereq.rdtype,
                rdclass=rdclass,
                data=prereq.data,
                passed=passed,
                message=message,
                source="explicit",
            )
        )
    return results


def preview_auto_prerequisites(
    zone: str,
    operations: list[AtomicOperation],
    zone_cache: ZoneCache,
) -> list[PrerequisitePreviewResult]:
    """Preview auto-derived prerequisites for operations against the cache."""
    results: list[PrerequisitePreviewResult] = []
    for op in operations:
        rdtype = op.type.upper()
        rdclass = normalize_class(op.rdclass)
        existing = zone_cache.get_rrset(zone, op.name, rdtype, rdclass)

        if op.action == "add":
            passed = existing is None
            results.append(
                PrerequisitePreviewResult(
                    prereq_type="nxrrset",
                    name=op.name,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    passed=passed,
                    message=(
                        "RRset does not exist — add can proceed (pass)"
                        if passed
                        else "RRset already exists — add would fail (fail)"
                    ),
                    source="auto",
                )
            )
        elif op.action in ("delete", "replace"):
            passed = existing is not None
            results.append(
                PrerequisitePreviewResult(
                    prereq_type="yxrrset",
                    name=op.name,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    passed=passed,
                    message=(
                        f"RRset exists — {op.action} can proceed (pass)"
                        if passed
                        else f"RRset does not exist — {op.action} would fail (fail)"
                    ),
                    source="auto",
                )
            )
    return results


def build_update(
    zone: str,
    operations: list[AtomicOperation],
    dns_client: DNSClient,
    zone_cache: ZoneCache,
    *,
    prerequisites: list[ChangePrerequisite] | None = None,
    auto_prerequisites: bool = True,
    validate_cache_state: bool = True,
) -> BuiltUpdate:
    """Build a single DNS UPDATE message from operations and prerequisites.

    Args:
        zone: Zone name (must end with '.')
        operations: List of add/delete/replace operations
        dns_client: DNS client (for TSIG and name normalisation)
        zone_cache: Zone cache for auto-prereq derivation and validation
        prerequisites: Explicit user-supplied prerequisites
        auto_prerequisites: If True, derive absent/present from cache
        validate_cache_state: If True, raise UpdateBuildError when cache
            state already contradicts an operation (used by immediate atomic)

    Returns:
        BuiltUpdate with the message and cache mutation list

    Raises:
        UpdateBuildError: When validation fails
    """
    prerequisites = prerequisites or []
    zone_name = dns.name.from_text(zone)
    update = dns.update.Update(
        zone_name,
        keyring=dns_client.keyring,
        keyname=dns_client.keyname,
        keyalgorithm=dns_client.keyalgorithm,
    )

    # Explicit prerequisites first
    apply_explicit_prerequisites(update, zone, prerequisites, dns_client)

    cache_updates: list[CacheUpdate] = []
    auto_preview: list[PrerequisitePreviewResult] = []

    for i, op in enumerate(operations):
        rdtype = op.type.upper()
        rdclass = normalize_class(op.rdclass)
        rdtype_obj = dns.rdatatype.from_text(rdtype)
        rdclass_obj = dns.rdataclass.from_text(rdclass)
        fqdn = dns_client.normalize_name(op.name, zone)

        if op.action == "add":
            if not op.records:
                raise UpdateBuildError(
                    f"Operation {i}: 'add' requires records",
                    index=i,
                    code="MISSING_RECORDS",
                )

            cached_rrset = zone_cache.get_rrset(zone, op.name, rdtype, rdclass)
            if validate_cache_state and cached_rrset is not None:
                raise UpdateBuildError(
                    f"Operation {i}: RRset {op.name} {rdclass} {rdtype} already exists",
                    index=i,
                    code="RRSET_EXISTS",
                )

            if auto_prerequisites:
                update.absent(fqdn, rdtype_obj)
                auto_preview.append(
                    PrerequisitePreviewResult(
                        prereq_type="nxrrset",
                        name=op.name,
                        rdtype=rdtype,
                        rdclass=rdclass,
                        passed=cached_rrset is None,
                        message="auto nxrrset for add",
                        source="auto",
                    )
                )

            for record in op.records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                update.add(fqdn, op.ttl, rdata)

            cache_updates.append(
                CacheUpdate(
                    action="add",
                    name=op.name,
                    ttl=op.ttl,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    records=op.records,
                )
            )

        elif op.action == "delete":
            cached_rrset = zone_cache.get_rrset(zone, op.name, rdtype, rdclass)
            if validate_cache_state and cached_rrset is None:
                raise UpdateBuildError(
                    f"Operation {i}: RRset {op.name} {rdclass} {rdtype} not found",
                    index=i,
                    code="RRSET_NOT_FOUND",
                )

            if auto_prerequisites:
                if cached_rrset is not None:
                    for record in cached_rrset.records:
                        update.present(fqdn, rdtype_obj, record)
                else:
                    # At execution time without validate: require RRset present
                    update.present(fqdn, rdtype_obj)
                auto_preview.append(
                    PrerequisitePreviewResult(
                        prereq_type="yxrrset",
                        name=op.name,
                        rdtype=rdtype,
                        rdclass=rdclass,
                        passed=cached_rrset is not None,
                        message="auto yxrrset for delete",
                        source="auto",
                    )
                )

            if op.records:
                for record in op.records:
                    rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                    update.delete(fqdn, rdata)
            else:
                update.delete(fqdn, rdtype_obj)

            cache_updates.append(
                CacheUpdate(
                    action="delete",
                    name=op.name,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    records=op.records,
                )
            )

        elif op.action == "replace":
            if not op.records:
                raise UpdateBuildError(
                    f"Operation {i}: 'replace' requires records",
                    index=i,
                    code="MISSING_RECORDS",
                )

            cached_rrset = zone_cache.get_rrset(zone, op.name, rdtype, rdclass)
            if validate_cache_state and cached_rrset is None:
                raise UpdateBuildError(
                    f"Operation {i}: RRset {op.name} {rdclass} {rdtype} not found (use add)",
                    index=i,
                    code="RRSET_NOT_FOUND",
                )

            if auto_prerequisites:
                if cached_rrset is not None:
                    for record in cached_rrset.records:
                        update.present(fqdn, rdtype_obj, record)
                else:
                    update.present(fqdn, rdtype_obj)
                auto_preview.append(
                    PrerequisitePreviewResult(
                        prereq_type="yxrrset",
                        name=op.name,
                        rdtype=rdtype,
                        rdclass=rdclass,
                        passed=cached_rrset is not None,
                        message="auto yxrrset for replace",
                        source="auto",
                    )
                )

            update.delete(fqdn, rdtype_obj)
            for record in op.records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                update.add(fqdn, op.ttl, rdata)

            cache_updates.append(
                CacheUpdate(
                    action="replace",
                    name=op.name,
                    ttl=op.ttl,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    records=op.records,
                )
            )

        else:
            raise UpdateBuildError(
                f"Operation {i}: unknown action '{op.action}'",
                index=i,
                code="UNKNOWN_ACTION",
            )

    return BuiltUpdate(
        update=update,
        cache_updates=cache_updates,
        auto_prerequisites=auto_preview,
    )


def apply_cache_updates(zone: str, cache_updates: list[CacheUpdate], zone_cache: ZoneCache) -> None:
    """Apply optimistic cache mutations after a successful DDNS update."""
    for cu in cache_updates:
        if cu.action == "add":
            zone_cache.update_cache_after_add(
                zone=zone,
                name=cu.name,
                ttl=cu.ttl,
                rdtype=cu.rdtype,
                records=cu.records or [],
                rdclass=cu.rdclass,
            )
        elif cu.action == "delete":
            zone_cache.update_cache_after_delete(
                zone=zone,
                name=cu.name,
                rdtype=cu.rdtype,
                records=cu.records,
                rdclass=cu.rdclass,
            )
        elif cu.action == "replace":
            zone_cache.update_cache_after_replace(
                zone=zone,
                name=cu.name,
                ttl=cu.ttl,
                rdtype=cu.rdtype,
                records=cu.records or [],
                rdclass=cu.rdclass,
            )
