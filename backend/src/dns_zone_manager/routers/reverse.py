"""Reverse PTR management endpoints."""

from enum import Enum
from typing import Annotated

import dns.exception
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import DNSClient, PrerequisiteFailedError, UpdateError
from dns_zone_manager.dns.reverse import (
    find_reverse_zone,
    get_ptr_record_name,
    ip_to_ptr_name,
)
from dns_zone_manager.middleware import enrich_dns_context

router = APIRouter(prefix="/reverse-ptr", tags=["Reverse PTR"])

# These will be injected by the main app
_dns_client: DNSClient | None = None
_zone_cache: ZoneCache | None = None


def set_dns_client(client: DNSClient) -> None:
    """Set the DNS client instance."""
    global _dns_client
    _dns_client = client


def set_zone_cache(cache: ZoneCache) -> None:
    """Set the zone cache instance."""
    global _zone_cache
    _zone_cache = cache


def get_dns_client() -> DNSClient:
    """Get the DNS client dependency."""
    if _dns_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DNS client not initialized",
        )
    return _dns_client


def get_zone_cache() -> ZoneCache:
    """Get the zone cache dependency."""
    if _zone_cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zone cache not initialized",
        )
    return _zone_cache


# Request/Response Models


class ReversePtrCheckRequest(BaseModel):
    """Request to check reverse PTR creation feasibility."""

    source_name: str = Field(
        ...,
        description="FQDN of the source record (e.g., www.example.com.)",
    )
    source_type: str = Field(
        ...,
        description="Source record type (must be A or AAAA)",
        pattern="^(A|AAAA)$",
    )
    records: list[str] = Field(
        ...,
        description="List of IP addresses from the A/AAAA record",
        min_length=1,
    )


class ReversePtrCheckResult(BaseModel):
    """Result for a single IP address check."""

    ip: str = Field(..., description="The IP address")
    ptr_fqdn: str = Field(..., description="Full PTR name (e.g., 100.2.0.192.in-addr.arpa.)")
    reverse_zone: str | None = Field(
        None, description="Managed reverse zone if found (e.g., 2.0.192.in-addr.arpa.)"
    )
    record_name: str | None = Field(None, description="Record name within the zone (e.g., 100)")
    zone_managed: bool = Field(..., description="Whether we manage the reverse zone")
    existing_ptrs: list[str] = Field(
        default_factory=list,
        description="Existing PTR records at this name (if zone is managed)",
    )
    can_create: bool = Field(..., description="Whether a PTR can be created")
    error: str | None = Field(None, description="Error message if IP is invalid")


class ReversePtrCheckResponse(BaseModel):
    """Response from reverse PTR check."""

    results: list[ReversePtrCheckResult] = Field(..., description="Check results for each IP")
    any_can_create: bool = Field(..., description="Whether any PTR can be created")


class CreateMode(str, Enum):
    """Mode for handling existing PTR records."""

    SKIP_EXISTING = "skip_existing"
    REPLACE = "replace"
    ADD_ROUNDROBIN = "add_roundrobin"


class ReversePtrCreateRequest(BaseModel):
    """Request to create reverse PTR records."""

    ptr_target: str = Field(
        ...,
        description="Target hostname for the PTR record (e.g., www.example.com.)",
    )
    ttl: int = Field(
        3600,
        ge=0,
        le=2147483647,
        description="TTL for the PTR records",
    )
    ips: list[str] = Field(
        ...,
        description="List of IP addresses to create PTRs for",
        min_length=1,
    )
    mode: CreateMode = Field(
        CreateMode.SKIP_EXISTING,
        description="How to handle existing PTR records",
    )


class ReversePtrCreateResult(BaseModel):
    """Result for a single PTR creation."""

    ip: str = Field(..., description="The IP address")
    ptr_fqdn: str = Field(..., description="Full PTR name")
    reverse_zone: str | None = Field(None, description="Reverse zone used")
    status: str = Field(
        ...,
        description="Status: created, skipped, replaced, added, zone_not_managed, error",
    )
    message: str = Field(..., description="Human-readable status message")


class ReversePtrCreateResponse(BaseModel):
    """Response from reverse PTR creation."""

    results: list[ReversePtrCreateResult] = Field(..., description="Creation results for each IP")
    created_count: int = Field(..., description="Number of PTRs created")
    skipped_count: int = Field(..., description="Number of PTRs skipped")
    error_count: int = Field(..., description="Number of errors")


# Endpoints


@router.post(
    "/check",
    response_model=ReversePtrCheckResponse,
    summary="Check Reverse PTR Feasibility",
    description="""
Check whether reverse PTR records can be created for the given IP addresses.

Returns information about:
- The computed reverse zone for each IP
- Whether we manage that reverse zone
- Any existing PTR records at that name
""",
)
async def check_reverse_ptr(
    http_request: Request,
    request: ReversePtrCheckRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> ReversePtrCheckResponse:
    """Check reverse PTR creation feasibility."""
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="check_reverse_ptr",
        source_name=request.source_name,
        source_type=request.source_type,
        ip_count=len(request.records),
    )

    # Get all managed zones (lowercase, with trailing dots)
    managed_zones = {z.lower() for z in zone_cache.list_zones()}

    results: list[ReversePtrCheckResult] = []

    for ip in request.records:
        try:
            # Convert IP to PTR name
            ptr_name = ip_to_ptr_name(ip)
            ptr_fqdn = ptr_name.to_text()

            # Find managed reverse zone
            reverse_zone = find_reverse_zone(ptr_name, managed_zones)

            if reverse_zone:
                # Zone is managed - get record name and check for existing PTRs
                record_name = get_ptr_record_name(ptr_name, reverse_zone)

                # Check for existing PTR records
                existing_ptrs: list[str] = []
                cached_rrset = zone_cache.get_rrset(reverse_zone, record_name, "PTR", "IN")
                if cached_rrset:
                    existing_ptrs = list(cached_rrset.records)

                results.append(
                    ReversePtrCheckResult(
                        ip=ip,
                        ptr_fqdn=ptr_fqdn,
                        reverse_zone=reverse_zone,
                        record_name=record_name,
                        zone_managed=True,
                        existing_ptrs=existing_ptrs,
                        can_create=True,
                        error=None,
                    )
                )
            else:
                # Zone not managed
                results.append(
                    ReversePtrCheckResult(
                        ip=ip,
                        ptr_fqdn=ptr_fqdn,
                        reverse_zone=None,
                        record_name=None,
                        zone_managed=False,
                        existing_ptrs=[],
                        can_create=False,
                        error=None,
                    )
                )

        except dns.exception.SyntaxError as e:
            results.append(
                ReversePtrCheckResult(
                    ip=ip,
                    ptr_fqdn="",
                    reverse_zone=None,
                    record_name=None,
                    zone_managed=False,
                    existing_ptrs=[],
                    can_create=False,
                    error=f"Invalid IP address: {e}",
                )
            )

    any_can_create = any(r.can_create for r in results)

    enrich_dns_context(
        http_request,
        results_count=len(results),
        any_can_create=any_can_create,
    )

    return ReversePtrCheckResponse(results=results, any_can_create=any_can_create)


@router.post(
    "",
    response_model=ReversePtrCreateResponse,
    summary="Create Reverse PTR Records",
    description="""
Create PTR records in managed reverse zones for the given IP addresses.

Modes for handling existing PTR records:
- `skip_existing`: Skip IPs that already have a PTR record
- `replace`: Replace existing PTR records with the new target
- `add_roundrobin`: Add the new target to existing PTRs (round-robin)
""",
)
async def create_reverse_ptr(
    http_request: Request,
    request: ReversePtrCreateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> ReversePtrCreateResponse:
    """Create reverse PTR records."""
    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="create_reverse_ptr",
        ptr_target=request.ptr_target,
        ttl=request.ttl,
        ip_count=len(request.ips),
        mode=request.mode.value,
    )

    # Ensure target has trailing dot
    ptr_target = request.ptr_target
    if not ptr_target.endswith("."):
        ptr_target = ptr_target + "."

    # Get all managed zones
    managed_zones = {z.lower() for z in zone_cache.list_zones()}

    results: list[ReversePtrCreateResult] = []
    created_count = 0
    skipped_count = 0
    error_count = 0

    for ip in request.ips:
        try:
            # Convert IP to PTR name
            ptr_name = ip_to_ptr_name(ip)
            ptr_fqdn = ptr_name.to_text()

            # Find managed reverse zone
            reverse_zone = find_reverse_zone(ptr_name, managed_zones)

            if not reverse_zone:
                results.append(
                    ReversePtrCreateResult(
                        ip=ip,
                        ptr_fqdn=ptr_fqdn,
                        reverse_zone=None,
                        status="zone_not_managed",
                        message="Reverse zone is not managed by this system",
                    )
                )
                error_count += 1
                continue

            record_name = get_ptr_record_name(ptr_name, reverse_zone)

            # Check for existing PTR records
            cached_rrset = zone_cache.get_rrset(reverse_zone, record_name, "PTR", "IN")
            existing_ptrs = list(cached_rrset.records) if cached_rrset else []

            if existing_ptrs:
                # Handle based on mode
                if request.mode == CreateMode.SKIP_EXISTING:
                    results.append(
                        ReversePtrCreateResult(
                            ip=ip,
                            ptr_fqdn=ptr_fqdn,
                            reverse_zone=reverse_zone,
                            status="skipped",
                            message=f"PTR already exists: {', '.join(existing_ptrs)}",
                        )
                    )
                    skipped_count += 1
                    continue

                elif request.mode == CreateMode.REPLACE:
                    # Delete existing and add new
                    try:
                        dns_client.delete_rrset(
                            zone=reverse_zone,
                            name=record_name,
                            rdtype="PTR",
                        )
                        dns_client.add_rrset(
                            zone=reverse_zone,
                            name=record_name,
                            ttl=request.ttl,
                            rdtype="PTR",
                            records=[ptr_target],
                        )
                        zone_cache.update_cache_after_replace(
                            zone=reverse_zone,
                            name=record_name,
                            ttl=request.ttl,
                            rdtype="PTR",
                            records=[ptr_target],
                        )
                        results.append(
                            ReversePtrCreateResult(
                                ip=ip,
                                ptr_fqdn=ptr_fqdn,
                                reverse_zone=reverse_zone,
                                status="replaced",
                                message=f"Replaced existing PTR (was: {', '.join(existing_ptrs)})",
                            )
                        )
                        created_count += 1
                    except (UpdateError, PrerequisiteFailedError) as e:
                        results.append(
                            ReversePtrCreateResult(
                                ip=ip,
                                ptr_fqdn=ptr_fqdn,
                                reverse_zone=reverse_zone,
                                status="error",
                                message=f"Failed to replace PTR: {e}",
                            )
                        )
                        error_count += 1
                    continue

                elif request.mode == CreateMode.ADD_ROUNDROBIN:
                    # Add to existing records
                    if ptr_target in existing_ptrs:
                        results.append(
                            ReversePtrCreateResult(
                                ip=ip,
                                ptr_fqdn=ptr_fqdn,
                                reverse_zone=reverse_zone,
                                status="skipped",
                                message="PTR target already exists in round-robin",
                            )
                        )
                        skipped_count += 1
                        continue

                    try:
                        new_records = existing_ptrs + [ptr_target]
                        dns_client.replace_rrset(
                            zone=reverse_zone,
                            name=record_name,
                            ttl=request.ttl,
                            rdtype="PTR",
                            new_records=new_records,
                        )
                        zone_cache.update_cache_after_replace(
                            zone=reverse_zone,
                            name=record_name,
                            ttl=request.ttl,
                            rdtype="PTR",
                            records=new_records,
                        )
                        results.append(
                            ReversePtrCreateResult(
                                ip=ip,
                                ptr_fqdn=ptr_fqdn,
                                reverse_zone=reverse_zone,
                                status="added",
                                message=f"Added to existing PTRs (now: {', '.join(new_records)})",
                            )
                        )
                        created_count += 1
                    except (UpdateError, PrerequisiteFailedError) as e:
                        results.append(
                            ReversePtrCreateResult(
                                ip=ip,
                                ptr_fqdn=ptr_fqdn,
                                reverse_zone=reverse_zone,
                                status="error",
                                message=f"Failed to add PTR: {e}",
                            )
                        )
                        error_count += 1
                    continue

            else:
                # No existing PTR - create new
                try:
                    dns_client.add_rrset(
                        zone=reverse_zone,
                        name=record_name,
                        ttl=request.ttl,
                        rdtype="PTR",
                        records=[ptr_target],
                        prereq_not_exists=True,
                    )
                    zone_cache.update_cache_after_add(
                        zone=reverse_zone,
                        name=record_name,
                        ttl=request.ttl,
                        rdtype="PTR",
                        records=[ptr_target],
                    )
                    results.append(
                        ReversePtrCreateResult(
                            ip=ip,
                            ptr_fqdn=ptr_fqdn,
                            reverse_zone=reverse_zone,
                            status="created",
                            message=f"PTR created: {ptr_target}",
                        )
                    )
                    created_count += 1
                except PrerequisiteFailedError:
                    # Race condition - PTR was created between check and add
                    results.append(
                        ReversePtrCreateResult(
                            ip=ip,
                            ptr_fqdn=ptr_fqdn,
                            reverse_zone=reverse_zone,
                            status="skipped",
                            message="PTR was created by another process",
                        )
                    )
                    skipped_count += 1
                except UpdateError as e:
                    results.append(
                        ReversePtrCreateResult(
                            ip=ip,
                            ptr_fqdn=ptr_fqdn,
                            reverse_zone=reverse_zone,
                            status="error",
                            message=f"Failed to create PTR: {e}",
                        )
                    )
                    error_count += 1

        except dns.exception.SyntaxError as e:
            results.append(
                ReversePtrCreateResult(
                    ip=ip,
                    ptr_fqdn="",
                    reverse_zone=None,
                    status="error",
                    message=f"Invalid IP address: {e}",
                )
            )
            error_count += 1

    enrich_dns_context(
        http_request,
        created_count=created_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )

    return ReversePtrCreateResponse(
        results=results,
        created_count=created_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )
