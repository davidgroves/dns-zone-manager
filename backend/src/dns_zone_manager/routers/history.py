"""Zone history and rollback endpoints."""

from typing import Annotated

import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.update
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.cache import ZoneCache
from dns_zone_manager.dns.client import (
    DNSClient,
    HistoryBatch,
    HistoryChange,
    PrerequisiteFailedError,
    UpdateError,
    ZoneTransferError,
)
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import (
    HistoryBatchResponse,
    HistoryChangeResponse,
    RollbackPreviewResponse,
    RollbackRequest,
    RollbackResponse,
    ZoneHistoryResponse,
)

router = APIRouter(prefix="/zones/{zone}/history", tags=["Zone History"])

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


# Record types that should not be modified by rollback
PROTECTED_TYPES = {"SOA", "NS"}


def _convert_history_change(change: HistoryChange) -> HistoryChangeResponse:
    """Convert internal HistoryChange to response model."""
    return HistoryChangeResponse(
        action=change.action,
        name=change.name,
        ttl=change.ttl,
        type=change.rdtype,
        rdclass=change.rdclass,
        records=change.records,
    )


def _convert_history_batch(batch: HistoryBatch) -> HistoryBatchResponse:
    """Convert internal HistoryBatch to response model."""
    return HistoryBatchResponse(
        from_serial=batch.from_serial,
        to_serial=batch.to_serial,
        changes=[_convert_history_change(c) for c in batch.changes],
    )


def _is_protected_record(change: HistoryChange, zone: str) -> bool:
    """Check if a record change affects protected records.

    Protected records are:
    - SOA records (always)
    - NS records at zone apex

    Args:
        change: The history change to check
        zone: Zone name (with trailing dot)

    Returns:
        True if this is a protected record
    """
    if change.rdtype == "SOA":
        return True

    if change.rdtype == "NS":
        # NS at apex is protected
        apex_name = zone
        if change.name == apex_name or change.name == zone.rstrip("."):
            return True

    return False


def _reverse_changes(
    batches: list[HistoryBatch], zone: str
) -> tuple[list[HistoryChange], list[str]]:
    """Reverse history changes for rollback.

    Processes batches in reverse order and swaps add/delete actions.

    Args:
        batches: History batches to reverse (should be newest to oldest order)
        zone: Zone name for protected record detection

    Returns:
        Tuple of (reversed changes, warning messages)
    """
    reversed_changes: list[HistoryChange] = []
    warnings: list[str] = []
    skipped_protected = 0

    # Process batches from newest to oldest (reverse chronological)
    for batch in reversed(batches):
        # Process changes in reverse order within each batch
        for change in reversed(batch.changes):
            # Skip protected records
            if _is_protected_record(change, zone):
                skipped_protected += 1
                continue

            # Swap action: add becomes delete, delete becomes add
            reversed_action = "delete" if change.action == "add" else "add"

            reversed_changes.append(
                HistoryChange(
                    action=reversed_action,
                    name=change.name,
                    ttl=change.ttl,
                    rdtype=change.rdtype,
                    rdclass=change.rdclass,
                    records=change.records,
                )
            )

    if skipped_protected > 0:
        warnings.append(
            f"Skipped {skipped_protected} protected record(s) (SOA/apex NS) during rollback"
        )

    return reversed_changes, warnings


# RFC 1982 serial number arithmetic constants
# Serial numbers are 32-bit unsigned integers with special comparison rules
SERIAL_BITS = 32
SERIAL_MAX = 2**SERIAL_BITS  # 4294967296
SERIAL_HALF = 2 ** (SERIAL_BITS - 1)  # 2147483648
SERIAL_MAX_DIFF = SERIAL_HALF - 1  # 2147483647 - max meaningful difference


def calculate_oldest_serial(current_serial: int) -> int:
    """Calculate the oldest serial we can request via IXFR using RFC 1982 arithmetic.

    Per RFC 1982, the maximum meaningful difference between two serial numbers
    is 2^(SERIAL_BITS-1) - 1 = 2147483647. A serial S1 is considered "less than"
    S2 if S2 - S1 (mod 2^32) is in the range (0, 2^31).

    To get maximum history, we request from (current_serial - 2^31 + 1) mod 2^32.

    Args:
        current_serial: Current zone serial number

    Returns:
        Oldest serial number to request history from
    """
    return (current_serial - SERIAL_MAX_DIFF) % SERIAL_MAX


def serial_lt(s1: int, s2: int) -> bool:
    """Check if serial s1 < s2 using RFC 1982 arithmetic.

    Per RFC 1982 section 3.2, s1 < s2 if:
    (s1 < s2 and s2 - s1 < 2^31) or (s1 > s2 and s1 - s2 > 2^31)

    Args:
        s1: First serial number
        s2: Second serial number

    Returns:
        True if s1 is less than s2 in serial space
    """
    diff = (s2 - s1) % SERIAL_MAX
    return 0 < diff < SERIAL_HALF


def serial_gt(s1: int, s2: int) -> bool:
    """Check if serial s1 > s2 using RFC 1982 arithmetic.

    Args:
        s1: First serial number
        s2: Second serial number

    Returns:
        True if s1 is greater than s2 in serial space
    """
    return serial_lt(s2, s1)


@router.get(
    "",
    response_model=ZoneHistoryResponse,
    summary="Get Zone History",
    description="""
Get the change history for a zone via IXFR (Incremental Zone Transfer).

Returns a list of change batches, each containing the additions and deletions
that occurred between serial numbers.

**Note:** History availability depends on the DNS server's journal configuration.
If the server doesn't have incremental history, it may respond with a full AXFR,
in which case `is_full_axfr` will be `true`.

The `from_serial` parameter can be used to request history from a specific point.
If omitted, uses RFC 1982 serial arithmetic to request maximum available history.
""",
)
async def get_zone_history(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    from_serial: Annotated[
        int | None,
        Query(
            ge=0,
            description="Get history from this serial number "
            "(omit for max history using RFC 1982 arithmetic)",
        ),
    ] = None,
) -> ZoneHistoryResponse:
    """Get zone change history via IXFR.

    Args:
        http_request: FastAPI request
        zone: Zone name
        user: Authenticated user
        dns_client: DNS client
        from_serial: Starting serial number (None = use RFC 1982 arithmetic for max history)

    Returns:
        Zone history response
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    # If from_serial not specified, calculate optimal value using RFC 1982 arithmetic
    requested_serial = from_serial  # Keep original for logging
    if from_serial is None:
        current_serial = dns_client.get_zone_serial(zone)
        if current_serial is not None:
            from_serial = calculate_oldest_serial(current_serial)
        else:
            # Fallback to 1 if we can't get current serial
            from_serial = 1

    # Log after calculating the actual serial to use
    enrich_dns_context(
        http_request,
        operation="get_zone_history",
        zone=zone,
        requested_serial=requested_serial,
        calculated_serial=from_serial,
    )

    # Perform IXFR to get history
    try:
        result = dns_client.perform_ixfr(zone, from_serial)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="IXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve zone history: {e}",
        )

    enrich_dns_context(
        http_request,
        result="success",
        current_serial=result.current_serial,
        batch_count=len(result.batches),
        is_full_axfr=result.is_full_axfr,
    )

    return ZoneHistoryResponse(
        zone=result.zone,
        current_serial=result.current_serial,
        history=[_convert_history_batch(b) for b in result.batches],
        available_from_serial=result.available_from_serial,
        is_full_axfr=result.is_full_axfr,
    )


@router.get(
    "/rollback/preview",
    response_model=RollbackPreviewResponse,
    summary="Preview Rollback",
    description="""
Preview the changes that would be applied by rolling back to a specific serial.

This endpoint does not modify the zone - it only shows what changes would occur.
Use this before executing a rollback to understand the impact.

**Note:** Protected records (SOA, NS at zone apex) will not be modified during rollback.
""",
)
async def preview_rollback(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    target_serial: Annotated[
        int,
        Query(
            ge=1,
            description="Serial number to rollback to",
        ),
    ],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
) -> RollbackPreviewResponse:
    """Preview changes that would be applied by rollback.

    Args:
        http_request: FastAPI request
        zone: Zone name
        target_serial: Target serial to rollback to
        user: Authenticated user
        dns_client: DNS client

    Returns:
        Preview of rollback changes
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="preview_rollback",
        zone=zone,
        target_serial=target_serial,
    )

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    # Get current serial
    current_serial = dns_client.get_zone_serial(zone)
    if current_serial is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to get current zone serial",
        )

    # Can't rollback to a future serial (using RFC 1982 comparison)
    if not serial_lt(target_serial, current_serial):
        return RollbackPreviewResponse(
            zone=zone,
            current_serial=current_serial,
            target_serial=target_serial,
            changes=[],
            change_count=0,
            can_rollback=False,
            warning="Target serial is not in the past "
            "(must be less than current serial per RFC 1982)",
        )

    # Get history from target serial
    try:
        result = dns_client.perform_ixfr(zone, target_serial)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="IXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve zone history: {e}",
        )

    # Check if we got AXFR instead of IXFR (history not available)
    if result.is_full_axfr:
        return RollbackPreviewResponse(
            zone=zone,
            current_serial=current_serial,
            target_serial=target_serial,
            changes=[],
            change_count=0,
            can_rollback=False,
            warning="History not available from target serial. "
            "Server responded with full zone transfer instead of incremental history.",
        )

    # Check if target serial is available in history
    if target_serial < result.available_from_serial:
        return RollbackPreviewResponse(
            zone=zone,
            current_serial=current_serial,
            target_serial=target_serial,
            changes=[],
            change_count=0,
            can_rollback=False,
            warning=f"Target serial {target_serial} is older than available history "
            f"(oldest available: {result.available_from_serial})",
        )

    # Reverse the changes to create rollback operations
    reversed_changes, warnings = _reverse_changes(result.batches, zone)

    enrich_dns_context(
        http_request,
        result="success",
        change_count=len(reversed_changes),
    )

    warning_msg = "; ".join(warnings) if warnings else None

    return RollbackPreviewResponse(
        zone=zone,
        current_serial=current_serial,
        target_serial=target_serial,
        changes=[_convert_history_change(c) for c in reversed_changes],
        change_count=len(reversed_changes),
        can_rollback=True,
        warning=warning_msg,
    )


@router.post(
    "/rollback",
    response_model=RollbackResponse,
    summary="Rollback Zone",
    description="""
Rollback a zone to a previous serial by reversing the changes that occurred after it.

This is a destructive operation that modifies the zone. The changes are applied
atomically - either all succeed or none are applied.

**Important considerations:**
- Protected records (SOA, NS at zone apex) will not be modified
- The zone must have history available from the target serial
- If the zone has been modified since the history was fetched, the operation may fail
""",
)
async def rollback_zone(
    http_request: Request,
    zone: Annotated[str, Path(description="Zone name (e.g., example.com)")],
    request: RollbackRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone_cache: Annotated[ZoneCache, Depends(get_zone_cache)],
) -> RollbackResponse:
    """Rollback a zone to a previous serial.

    Args:
        http_request: FastAPI request
        zone: Zone name
        request: Rollback request with target serial
        user: Authenticated user
        dns_client: DNS client
        zone_cache: Zone cache

    Returns:
        Rollback result
    """
    # Normalize zone name
    if not zone.endswith("."):
        zone = zone + "."

    target_serial = request.target_serial

    enrich_user_context(http_request, user)
    enrich_dns_context(
        http_request,
        operation="rollback_zone",
        zone=zone,
        target_serial=target_serial,
    )

    # Check if zone exists
    if not dns_client.check_zone_exists(zone):
        enrich_error_context(
            http_request,
            error_type="NotFoundError",
            message=f"Zone '{zone}' not found",
            code="ZONE_NOT_FOUND",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Zone '{zone}' not found or not accessible",
        )

    # Get current serial
    current_serial = dns_client.get_zone_serial(zone)
    if current_serial is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to get current zone serial",
        )

    # Can't rollback to a future serial (using RFC 1982 comparison)
    if not serial_lt(target_serial, current_serial):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target serial {target_serial} must be less than "
            f"current serial {current_serial} (per RFC 1982)",
        )

    # Get history from target serial
    try:
        result = dns_client.perform_ixfr(zone, target_serial)
    except ZoneTransferError as e:
        enrich_error_context(
            http_request,
            error_type="ZoneTransferError",
            message=str(e),
            code="IXFR_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve zone history: {e}",
        )

    # Check if we got AXFR instead of IXFR
    if result.is_full_axfr:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot rollback: history not available from target serial. "
            "Server responded with full zone transfer instead of incremental history.",
        )

    # Check if target serial is available
    if target_serial < result.available_from_serial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot rollback: target serial {target_serial} is older than "
            f"available history (oldest: {result.available_from_serial})",
        )

    # Reverse the changes
    reversed_changes, warnings = _reverse_changes(result.batches, zone)

    if not reversed_changes:
        return RollbackResponse(
            success=True,
            zone=zone,
            from_serial=current_serial,
            to_serial=target_serial,
            new_serial=current_serial,
            changes_applied=0,
            message="No changes to apply (zone state unchanged or only protected records)",
        )

    # Build a single DDNS UPDATE with all the changes
    zone_name = dns.name.from_text(zone)
    update = dns.update.Update(
        zone_name,
        keyring=dns_client.keyring,
        keyname=dns_client.keyname,
        keyalgorithm=dns_client.keyalgorithm,
    )

    for change in reversed_changes:
        fqdn = dns_client.normalize_name(change.name, zone)
        rdtype_obj = dns.rdatatype.from_text(change.rdtype)
        rdclass_obj = dns.rdataclass.from_text(change.rdclass)

        if change.action == "add":
            # Add record
            for record in change.records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                update.add(fqdn, change.ttl, rdata)
        else:  # delete
            # Delete specific records
            for record in change.records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                update.delete(fqdn, rdata)

    # Send the update
    try:
        dns_client._send_update(update, zone)
    except PrerequisiteFailedError as e:
        enrich_error_context(
            http_request,
            error_type="PrerequisiteFailedError",
            message=str(e),
            code="PREREQ_FAILED",
        )
        # Refresh cache
        try:
            zone_cache.refresh_zone(zone)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Rollback failed: zone was modified. {e}",
        )
    except UpdateError as e:
        enrich_error_context(
            http_request,
            error_type="UpdateError",
            message=str(e),
            code="DDNS_FAILED",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rollback failed: {e}",
        )

    # Refresh zone cache to get new state
    try:
        zone_cache.refresh_zone(zone)
    except Exception:
        pass

    # Get new serial after rollback
    new_serial = dns_client.get_zone_serial(zone) or current_serial + 1

    enrich_dns_context(
        http_request,
        result="success",
        changes_applied=len(reversed_changes),
        new_serial=new_serial,
    )

    warning_suffix = f" ({'; '.join(warnings)})" if warnings else ""

    return RollbackResponse(
        success=True,
        zone=zone,
        from_serial=current_serial,
        to_serial=target_serial,
        new_serial=new_serial,
        changes_applied=len(reversed_changes),
        message=f"Successfully rolled back zone to serial {target_serial}{warning_suffix}",
    )
