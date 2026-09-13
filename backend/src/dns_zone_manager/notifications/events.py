"""Change event model and derivation of operations from a DNS UPDATE message."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import dns.rdataclass
import dns.rdatatype
import dns.update

from dns_zone_manager.notifications.context import TRIGGER_MANUAL

EVENT_CHANGE_APPLIED = "change_applied"
EVENT_CHANGE_FAILED = "change_failed"


@dataclass(frozen=True)
class ChangeOperation:
    """A single record operation recovered from a DNS UPDATE message."""

    action: str  # add, delete, or replace
    name: str
    rdtype: str
    rdclass: str = "IN"
    ttl: int | None = None
    records: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """Human-readable one-line summary, e.g. 'add A www.example.com. -> 10.0.0.1'."""
        head = f"{self.action} {self.rdtype} {self.name}"
        if self.records:
            return f"{head} -> {', '.join(self.records)}"
        return head

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the generic webhook payload."""
        return {
            "action": self.action,
            "name": self.name,
            "type": self.rdtype,
            "rdclass": self.rdclass,
            "ttl": self.ttl,
            "records": list(self.records),
        }


@dataclass(frozen=True)
class DnsChangeEvent:
    """A committed or failed DNS change, ready for notification."""

    event: str
    zone: str
    operations: list[ChangeOperation]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    trigger: str = TRIGGER_MANUAL
    actor: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    auth_type: str | None = None
    change_id: str | None = None
    change_name: str | None = None
    request_id: str | None = None
    server: str | None = None
    rcode: str | None = None
    error: str | None = None
    # True when change_id was minted here and still needs persisting
    autorecord: bool = False

    @property
    def succeeded(self) -> bool:
        """Whether this event represents a successful change."""
        return self.event == EVENT_CHANGE_APPLIED

    @property
    def actor_display(self) -> str:
        """Best available human-readable actor label."""
        return self.actor_name or self.actor_email or self.actor or "unknown"

    @property
    def trigger_display(self) -> str:
        """Human-readable trigger label."""
        return {
            "manual": "Manual",
            "scheduler": "Scheduled",
            "apply_now": "Scheduled (applied now)",
            "revert": "Revert of a scheduled change",
        }.get(self.trigger, self.trigger)

    def summary(self) -> str:
        """Short summary used as the notification title."""
        verb = "DNS change applied" if self.succeeded else "DNS change failed"
        return f"{verb}: {self.zone}"

    def default_name(self) -> str:
        """Generated name used when auto-recording this change in the scheduler."""
        if self.change_name:
            return self.change_name
        if not self.operations:
            return f"{self.trigger_display} change on {self.zone}"
        first = self.operations[0]
        label = f"{first.action} {first.rdtype} {first.name}"
        if len(self.operations) > 1:
            label += f" (+{len(self.operations) - 1} more)"
        return label[:200]

    def link(self, base_url: str) -> str | None:
        """Deep link to this change, preferring the scheduler view."""
        if not base_url:
            return None
        if self.change_id:
            return f"{base_url}/?view=scheduled&change={self.change_id}"
        return f"{base_url}/?zone={self.zone}"

    def to_dict(self, base_url: str = "") -> dict[str, Any]:
        """Serialise for the generic webhook payload."""
        return {
            "event": self.event,
            "timestamp": self.timestamp.isoformat(),
            "zone": self.zone,
            "trigger": self.trigger,
            "actor": {
                "id": self.actor,
                "name": self.actor_name,
                "email": self.actor_email,
                "auth_type": self.auth_type,
            },
            "change": {
                "id": self.change_id,
                "name": self.change_name or self.default_name(),
                "link": self.link(base_url),
            },
            "operations": [op.to_dict() for op in self.operations],
            "result": {
                "success": self.succeeded,
                "rcode": self.rcode,
                "error": self.error,
            },
            "server": self.server,
            "request_id": self.request_id,
        }


def operations_from_update(update: dns.update.Update) -> list[ChangeOperation]:
    """Recover the record operations from a DNS UPDATE message.

    The UPDATE section encodes intent in the record class: ``deleting`` is None
    for additions, NONE (254) to delete specific rdata, and ANY (255) to delete
    a whole RRset. dnspython's ``replace()`` emits a delete-ANY followed by an
    add for the same name and type, so those pairs are coalesced back into a
    single "replace" operation.
    """
    operations: list[ChangeOperation] = []
    last_index: dict[tuple[str, str, str], int] = {}

    for rrset in update.update:
        name = rrset.name.to_text()
        rdtype = dns.rdatatype.to_text(rrset.rdtype)
        rdclass = dns.rdataclass.to_text(rrset.rdclass)
        records = [rdata.to_text() for rdata in rrset]
        key = (name, rdclass, rdtype)

        if rrset.deleting is None:
            previous = last_index.get(key)
            if (
                previous is not None
                and operations[previous].action == "delete"
                and not operations[previous].records
            ):
                operations[previous] = ChangeOperation(
                    action="replace",
                    name=name,
                    rdtype=rdtype,
                    rdclass=rdclass,
                    ttl=rrset.ttl,
                    records=records,
                )
                continue
            operation = ChangeOperation(
                action="add",
                name=name,
                rdtype=rdtype,
                rdclass=rdclass,
                ttl=rrset.ttl,
                records=records,
            )
        else:
            operation = ChangeOperation(
                action="delete",
                name=name,
                rdtype=rdtype,
                rdclass=rdclass,
                ttl=None,
                records=records,
            )

        last_index[key] = len(operations)
        operations.append(operation)

    return operations
