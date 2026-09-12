"""NSUPDATE endpoint for executing nsupdate-formatted DNS updates."""

import logging
from typing import Annotated

import dns.name
import dns.rdatatype
import dns.update
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from dns_zone_manager.auth.combined import AuthenticatedUser, enrich_user_context, get_current_user
from dns_zone_manager.dns.client import (
    RCODE_DESCRIPTIONS,
    DNSClient,
    PrerequisiteFailedError,
    UpdateError,
)
from dns_zone_manager.dns.nsupdate_parser import (
    NSUpdateParseError,
    ParsedUpdate,
    PrereqType,
    UpdateAction,
    parse,
)
from dns_zone_manager.dns.nsupdate_to_scheduled import (
    NSUpdateConversionError,
    nsupdate_text_to_creates,
)
from dns_zone_manager.metrics import (
    scheduled_changes_created_total,
    scheduled_changes_pending,
)
from dns_zone_manager.middleware import enrich_dns_context, enrich_error_context
from dns_zone_manager.models.requests import (
    NSUpdateOperation,
    NSUpdateResponse,
    NSUpdateTransactionResult,
)
from dns_zone_manager.models.scheduled import NSUpdateDraftsResponse
from dns_zone_manager.scheduler.store import ChangeCreateData, ScheduledChangeStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nsupdate", tags=["NSUPDATE"])

# These will be injected by the main app
_dns_client: DNSClient | None = None
_store: ScheduledChangeStore | None = None


def set_dns_client(client: DNSClient) -> None:
    """Set the DNS client instance."""
    global _dns_client
    _dns_client = client


def set_store(store: ScheduledChangeStore) -> None:
    """Set the scheduled change store instance."""
    global _store
    _store = store


def get_dns_client() -> DNSClient:
    """Get the DNS client dependency."""
    if _dns_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DNS client not initialized",
        )
    return _dns_client


def get_store() -> ScheduledChangeStore:
    """Get the scheduled change store dependency."""
    if _store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduled change store not initialized",
        )
    return _store


def _parsed_update_to_operations(parsed: ParsedUpdate) -> list[NSUpdateOperation]:
    """Convert ParsedUpdate to list of NSUpdateOperation for response."""
    operations = []

    # Add prerequisites
    for prereq in parsed.prerequisites:
        operations.append(
            NSUpdateOperation(
                action=f"prereq_{prereq.prereq_type.value}",
                name=prereq.name,
                ttl=None,
                rdtype=prereq.rdtype,
                data=prereq.data,
            )
        )

    # Add operations
    for op in parsed.operations:
        operations.append(
            NSUpdateOperation(
                action=op.action.value,
                name=op.name,
                ttl=op.ttl,
                rdtype=op.rdtype,
                data=op.data,
            )
        )

    return operations


def _build_dns_update(parsed: ParsedUpdate, dns_client: DNSClient) -> dns.update.Update:
    """Build a dns.update.Update message from parsed data.

    Args:
        parsed: The parsed update transaction
        dns_client: DNS client for TSIG configuration

    Returns:
        Configured dns.update.Update ready to send
    """
    zone_name = dns.name.from_text(parsed.zone)

    update = dns.update.Update(
        zone_name,
        keyring=dns_client.keyring,
        keyname=dns_client.keyname,
        keyalgorithm=dns_client.keyalgorithm,
    )

    # Add prerequisites
    for prereq in parsed.prerequisites:
        name = dns_client.normalize_name(prereq.name, parsed.zone)

        if prereq.prereq_type == PrereqType.NXDOMAIN:
            # Name must not exist
            update.absent(name)

        elif prereq.prereq_type == PrereqType.YXDOMAIN:
            # Name must exist
            update.present(name)

        elif prereq.prereq_type == PrereqType.NXRRSET:
            # RRset must not exist
            rdtype = dns.rdatatype.from_text(prereq.rdtype) if prereq.rdtype else None
            if rdtype:
                update.absent(name, rdtype)
            else:
                update.absent(name)

        elif prereq.prereq_type == PrereqType.YXRRSET:
            # RRset must exist (optionally with specific data)
            rdtype = dns.rdatatype.from_text(prereq.rdtype) if prereq.rdtype else None
            if rdtype and prereq.data:
                update.present(name, rdtype, prereq.data)
            elif rdtype:
                update.present(name, rdtype)
            else:
                update.present(name)

    # Add update operations
    for op in parsed.operations:
        name = dns_client.normalize_name(op.name, parsed.zone)

        if op.action == UpdateAction.ADD:
            if not op.rdtype:
                raise ValueError(f"ADD operation requires rdtype for {op.name}")
            rdtype = dns.rdatatype.from_text(op.rdtype)
            update.add(name, op.ttl, rdtype, op.data)

        elif op.action == UpdateAction.DELETE:
            if op.rdtype:
                rdtype = dns.rdatatype.from_text(op.rdtype)
                if op.data:
                    # Delete specific record
                    update.delete(name, rdtype, op.data)
                else:
                    # Delete entire RRset of this type
                    update.delete(name, rdtype)
            else:
                # Delete all RRsets at this name
                update.delete(name)

    return update


def _execute_update(parsed: ParsedUpdate, dns_client: DNSClient) -> NSUpdateTransactionResult:
    """Execute a single parsed update transaction.

    Args:
        parsed: The parsed update to execute
        dns_client: DNS client for sending the update

    Returns:
        NSUpdateTransactionResult with success/failure info
    """
    operations = _parsed_update_to_operations(parsed)

    try:
        # Build the update message
        update = _build_dns_update(parsed, dns_client)

        # Send it using the DNS client's internal method
        dns_client._send_update(update, parsed.zone)

        return NSUpdateTransactionResult(
            zone=parsed.zone,
            operations=operations,
            success=True,
            message="Update successful",
        )

    except PrerequisiteFailedError as e:
        logger.warning(f"NSUPDATE prereq failed for {parsed.zone}: {e}")
        rcode_desc = RCODE_DESCRIPTIONS.get(e.rcode_text or "") if e.rcode_text else None
        return NSUpdateTransactionResult(
            zone=parsed.zone,
            operations=operations,
            success=False,
            message=f"Prerequisite failed: {e}",
            rcode=e.rcode_text,
            rcode_description=rcode_desc,
        )

    except UpdateError as e:
        logger.error(f"NSUPDATE failed for {parsed.zone}: {e}")
        return NSUpdateTransactionResult(
            zone=parsed.zone,
            operations=operations,
            success=False,
            message=f"Update failed: {e}",
        )

    except Exception as e:
        logger.exception(f"Unexpected error executing NSUPDATE for {parsed.zone}")
        return NSUpdateTransactionResult(
            zone=parsed.zone,
            operations=operations,
            success=False,
            message=f"Unexpected error: {e}",
        )


# Example nsupdate text for OpenAPI documentation
NSUPDATE_EXAMPLE = """zone example.com.
prereq nxdomain newhost.example.com.
update add newhost.example.com. 3600 A 192.0.2.100
send
"""


@router.post(
    "",
    response_model=NSUpdateResponse,
    summary="Execute NSUPDATE commands",
    description="""
Execute DNS updates using nsupdate(1) file format.

**Supported commands:**
- `zone <zonename>` - Set target zone
- `prereq nxdomain <name>` - Name must not exist
- `prereq yxdomain <name>` - Name must exist
- `prereq nxrrset <name> [class] <type>` - RRset must not exist
- `prereq yxrrset <name> [class] <type> [data...]` - RRset must exist
- `update add <name> <ttl> [class] <type> <data>` - Add record
- `update delete <name> [ttl] [class] [type] [data]` - Delete record(s)
- `send` - Execute the update transaction

**Ignored commands** (API uses its own configuration):
- `server`, `key`, `local`

**Notes:**
- Lines starting with `;` or `#` are comments
- Multiple `send` commands create separate transactions
- If no `zone` command is present, the `zone` query parameter is used
""",
    responses={
        200: {"description": "Update results (may include partial failures)"},
        400: {"description": "Parse error in nsupdate text"},
        503: {"description": "DNS client not available"},
    },
)
async def execute_nsupdate(
    http_request: Request,
    body: Annotated[
        str,
        Body(
            media_type="text/plain",
            openapi_examples={
                "add_record": {
                    "summary": "Add a new A record",
                    "description": "Add a new A record with prerequisite check",
                    "value": (
                        "zone example.com.\n"
                        "prereq nxdomain newhost.example.com.\n"
                        "update add newhost.example.com. 3600 A 192.0.2.100\n"
                        "send\n"
                    ),
                },
                "delete_record": {
                    "summary": "Delete a record",
                    "description": "Delete an A record",
                    "value": (
                        "zone example.com.\n"
                        "prereq yxdomain oldhost.example.com.\n"
                        "update delete oldhost.example.com. A\n"
                        "send\n"
                    ),
                },
                "replace_record": {
                    "summary": "Replace a record",
                    "description": "Delete and add to replace a record",
                    "value": (
                        "zone example.com.\n"
                        "prereq yxrrset www.example.com. A\n"
                        "update delete www.example.com. A\n"
                        "update add www.example.com. 3600 A 192.0.2.200\n"
                        "send\n"
                    ),
                },
                "multiple_records": {
                    "summary": "Add multiple records",
                    "description": "Add multiple A records in one transaction",
                    "value": (
                        "zone example.com.\n"
                        "update add lb.example.com. 300 A 192.0.2.10\n"
                        "update add lb.example.com. 300 A 192.0.2.11\n"
                        "send\n"
                    ),
                },
            },
        ),
    ],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    dns_client: Annotated[DNSClient, Depends(get_dns_client)],
    zone: Annotated[
        str | None,
        Query(description="Default zone if not specified in the nsupdate text"),
    ] = None,
) -> NSUpdateResponse:
    """Execute NSUPDATE-formatted DNS updates.

    Accepts the request body as text/plain containing nsupdate commands.
    Each 'send' command triggers a separate DNS update transaction.

    Args:
        http_request: FastAPI request
        body: nsupdate-formatted text
        user: Authenticated user
        dns_client: DNS client
        zone: Optional default zone

    Returns:
        NSUpdateResponse with results for each transaction
    """
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="nsupdate", default_zone=zone)

    text = body

    if not text.strip():
        enrich_error_context(
            http_request,
            error_type="ValidationError",
            message="Empty request body",
            code="EMPTY_BODY",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty request body",
        )

    # Normalize zone parameter
    default_zone = None
    if zone:
        default_zone = zone if zone.endswith(".") else zone + "."

    # Parse the nsupdate text
    try:
        parsed_updates = parse(text, default_zone=default_zone)
    except NSUpdateParseError as e:
        enrich_error_context(
            http_request,
            error_type="NSUpdateParseError",
            message=str(e),
            code="PARSE_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parse error: {e}",
        )

    if not parsed_updates:
        enrich_error_context(
            http_request,
            error_type="ValidationError",
            message="No update transactions found",
            code="NO_TRANSACTIONS",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No update transactions found in input",
        )

    # Build operations summary for wide event
    all_operations = []
    for parsed in parsed_updates:
        for op in parsed.operations:
            if op.action == UpdateAction.ADD:
                all_operations.append(
                    {
                        "action": "ADD",
                        "zone": parsed.zone,
                        "name": op.name,
                        "ttl": op.ttl,
                        "rdtype": op.rdtype,
                        "data": op.data,
                    }
                )
            elif op.action == UpdateAction.DELETE:
                all_operations.append(
                    {
                        "action": "DELETE",
                        "zone": parsed.zone,
                        "name": op.name,
                        "rdtype": op.rdtype or "ALL",
                        "data": op.data,
                    }
                )

    enrich_dns_context(
        http_request,
        transactions_count=len(parsed_updates),
        operations=all_operations,
    )

    # Execute each transaction
    results: list[NSUpdateTransactionResult] = []
    for parsed in parsed_updates:
        result = _execute_update(parsed, dns_client)
        results.append(result)

    # Count successes and failures
    total_success = sum(1 for r in results if r.success)
    total_failed = len(results) - total_success

    enrich_dns_context(
        http_request,
        total_success=total_success,
        total_failed=total_failed,
    )

    if total_failed > 0:
        enrich_error_context(
            http_request,
            error_type="PartialFailure",
            message=f"{total_failed} of {len(results)} transactions failed",
            code="PARTIAL_FAILURE",
        )

    return NSUpdateResponse(
        transactions=results,
        total_success=total_success,
        total_failed=total_failed,
    )


@router.post(
    "/drafts",
    response_model=NSUpdateDraftsResponse,
    summary="Save NSUPDATE commands as draft scheduled changes",
    description="""
Parse nsupdate(1) text and create one **draft** scheduled change per `send`
transaction. Drafts appear in the Scheduled Changes UI for review, edit,
preview, and Apply Now.

Does not send DDNS updates. Use `POST /v1/nsupdate` for immediate apply.
""",
    responses={
        200: {"description": "Drafts created"},
        400: {"description": "Parse or conversion error"},
        503: {"description": "Scheduled store or DNS client not available"},
    },
)
async def create_nsupdate_drafts(
    http_request: Request,
    body: Annotated[
        str,
        Body(
            media_type="text/plain",
            openapi_examples={
                "add_record": {
                    "summary": "Add a new A record",
                    "value": (
                        "zone example.com.\n"
                        "prereq nxdomain newhost.example.com.\n"
                        "update add newhost.example.com. 3600 A 192.0.2.100\n"
                        "send\n"
                    ),
                },
                "multiple_sends": {
                    "summary": "Two transactions → two drafts",
                    "value": (
                        "zone example.com.\n"
                        "update add a.example.com. 300 A 192.0.2.1\n"
                        "send\n"
                        "update add b.example.com. 300 A 192.0.2.2\n"
                        "send\n"
                    ),
                },
            },
        ),
    ],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[ScheduledChangeStore, Depends(get_store)],
    zone: Annotated[
        str | None,
        Query(description="Default zone if not specified in the nsupdate text"),
    ] = None,
) -> NSUpdateDraftsResponse:
    """Save nsupdate text as draft scheduled changes (one per send)."""
    enrich_user_context(http_request, user)
    enrich_dns_context(http_request, operation="nsupdate_drafts", default_zone=zone)

    text = body
    if not text.strip():
        enrich_error_context(
            http_request,
            error_type="ValidationError",
            message="Empty request body",
            code="EMPTY_BODY",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty request body",
        )

    default_zone = None
    if zone:
        default_zone = zone if zone.endswith(".") else zone + "."

    try:
        creates = nsupdate_text_to_creates(text, default_zone=default_zone)
    except NSUpdateParseError as e:
        enrich_error_context(
            http_request,
            error_type="NSUpdateParseError",
            message=str(e),
            code="PARSE_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parse error: {e}",
        )
    except NSUpdateConversionError as e:
        enrich_error_context(
            http_request,
            error_type="NSUpdateConversionError",
            message=str(e),
            code="CONVERSION_ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    created = []
    for create in creates:
        change = await store.create(
            ChangeCreateData(
                name=create.name,
                description=create.description,
                zone=create.zone,
                operations=create.operations,
                prerequisites=create.prerequisites,
                scheduled_at=create.scheduled_at,
                not_valid_after=create.not_valid_after,
                auto_prerequisites=create.auto_prerequisites,
                created_by=user.user_id,
            )
        )
        scheduled_changes_created_total.inc()
        created.append(change)

    scheduled_changes_pending.set(await store.count_pending())
    enrich_dns_context(
        http_request,
        drafts_created=len(created),
        zones=[c.zone for c in created],
    )

    return NSUpdateDraftsResponse(created=created, total=len(created))
