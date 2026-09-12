"""Convert parsed nsupdate transactions into scheduled-change create payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from dns_zone_manager.dns.nsupdate_parser import (
    ParsedUpdate,
    Prerequisite,
    UpdateAction,
    UpdateOperation,
    parse,
)
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import ChangePrerequisite, ScheduledChangeCreate

_DESCRIPTION_MAX = 2000


class NSUpdateConversionError(ValueError):
    """Raised when an nsupdate transaction cannot map to a scheduled change."""


def _format_prereq_line(prereq: Prerequisite) -> str:
    """Render a prerequisite as an nsupdate-like line."""
    parts = ["prereq", prereq.prereq_type.value, prereq.name]
    if prereq.prereq_type.value in ("nxrrset", "yxrrset"):
        if prereq.rdclass and prereq.rdclass != "IN":
            parts.append(prereq.rdclass)
        if prereq.rdtype:
            parts.append(prereq.rdtype)
        if prereq.data:
            parts.append(prereq.data)
    return " ".join(parts)


def _format_update_line(op: UpdateOperation) -> str:
    """Render an update operation as an nsupdate-like line."""
    if op.action == UpdateAction.ADD:
        parts = ["update", "add", op.name, str(op.ttl or 0)]
        if op.rdclass and op.rdclass != "IN":
            parts.append(op.rdclass)
        if op.rdtype:
            parts.append(op.rdtype)
        if op.data:
            parts.append(op.data)
        return " ".join(parts)

    parts = ["update", "delete", op.name]
    if op.rdclass and op.rdclass != "IN":
        parts.append(op.rdclass)
    if op.rdtype:
        parts.append(op.rdtype)
    if op.data:
        parts.append(op.data)
    return " ".join(parts)


def _transaction_description(parsed: ParsedUpdate) -> str:
    """Build a human-readable description from the parsed transaction."""
    lines = [f"zone {parsed.zone}"]
    for prereq in parsed.prerequisites:
        lines.append(_format_prereq_line(prereq))
    for op in parsed.operations:
        lines.append(_format_update_line(op))
    lines.append("send")
    text = "\n".join(lines)
    if len(text) > _DESCRIPTION_MAX:
        return text[: _DESCRIPTION_MAX - 1] + "…"
    return text


def _prereqs_to_change_prereqs(
    prerequisites: list[Prerequisite],
) -> list[ChangePrerequisite]:
    """Map nsupdate prerequisites to ChangePrerequisite models."""
    return [
        ChangePrerequisite(
            prereq_type=prereq.prereq_type.value,
            name=prereq.name,
            rdtype=prereq.rdtype,
            rdclass=prereq.rdclass or "IN",
            data=prereq.data,
        )
        for prereq in prerequisites
    ]


def _ops_to_atomic(operations: list[UpdateOperation]) -> list[AtomicOperation]:
    """Map nsupdate update ops to AtomicOperations, grouping consecutive adds."""
    atomic: list[AtomicOperation] = []

    for op in operations:
        if op.action == UpdateAction.DELETE:
            if not op.rdtype:
                raise NSUpdateConversionError(
                    "update delete without a record type cannot be saved as a "
                    "scheduled change; specify the type "
                    "(e.g. 'update delete name A')"
                )
            atomic.append(
                AtomicOperation(
                    action="delete",
                    name=op.name,
                    type=op.rdtype,
                    rdclass=op.rdclass or "IN",
                    ttl=op.ttl if op.ttl is not None else 3600,
                    records=[op.data] if op.data else None,
                )
            )
            continue

        # ADD — group consecutive identical RRset keys into one op.
        if not op.rdtype:
            raise NSUpdateConversionError(f"update add requires a record type for {op.name}")
        if not op.data:
            raise NSUpdateConversionError(f"update add requires data for {op.name} {op.rdtype}")

        ttl = op.ttl if op.ttl is not None else 3600
        rdclass = op.rdclass or "IN"
        key = (op.name, op.rdtype, rdclass, ttl)

        if (
            atomic
            and atomic[-1].action == "add"
            and (
                atomic[-1].name,
                atomic[-1].type,
                atomic[-1].rdclass,
                atomic[-1].ttl,
            )
            == key
            and atomic[-1].records is not None
        ):
            atomic[-1].records.append(op.data)
        else:
            atomic.append(
                AtomicOperation(
                    action="add",
                    name=op.name,
                    type=op.rdtype,
                    rdclass=rdclass,
                    ttl=ttl,
                    records=[op.data],
                )
            )

    return atomic


def parsed_update_to_create(
    parsed: ParsedUpdate,
    *,
    name: str | None = None,
    index: int | None = None,
    total: int | None = None,
    now: datetime | None = None,
) -> ScheduledChangeCreate:
    """Convert one ParsedUpdate into a draft ScheduledChangeCreate."""
    if not parsed.operations:
        raise NSUpdateConversionError(
            f"Transaction for zone {parsed.zone} has no update operations "
            "(prerequisites alone cannot form a scheduled change)"
        )

    operations = _ops_to_atomic(parsed.operations)
    prerequisites = _prereqs_to_change_prereqs(parsed.prerequisites)

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    zone_label = parsed.zone.rstrip(".")
    if name is None:
        name = f"NSUPDATE · {zone_label} · {stamp}"
        if total is not None and total > 1 and index is not None:
            name = f"{name} · {index}"

    return ScheduledChangeCreate(
        name=name[:200],
        description=_transaction_description(parsed),
        zone=parsed.zone,
        operations=operations,
        prerequisites=prerequisites,
        scheduled_at=None,
        not_valid_after=None,
        auto_prerequisites=len(prerequisites) == 0,
    )


def nsupdate_text_to_creates(
    text: str,
    default_zone: str | None = None,
    *,
    now: datetime | None = None,
) -> list[ScheduledChangeCreate]:
    """Parse nsupdate text and convert each send transaction to a draft create."""
    parsed_updates = parse(text, default_zone=default_zone)
    if not parsed_updates:
        raise NSUpdateConversionError("No update transactions found in input")

    now = now or datetime.now(UTC)
    total = len(parsed_updates)
    return [
        parsed_update_to_create(
            parsed,
            index=i + 1,
            total=total,
            now=now,
        )
        for i, parsed in enumerate(parsed_updates)
    ]
