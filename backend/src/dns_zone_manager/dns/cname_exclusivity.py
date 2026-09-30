"""CNAME exclusivity checks (RFC 1034 / RFC 2136 ignore semantics).

BIND accepts conflicting CNAME/non-CNAME adds with NOERROR and silently ignores
the conflicting Update RR. The app rejects those cases instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# DNSSEC records may coexist with CNAME at an owner name.
DNSSEC_COMPANION_TYPES: frozenset[str] = frozenset({"RRSIG", "NSEC", "NSEC3"})


class CnameConflictError(Exception):
    """Raised when an add would be ignored by BIND due to CNAME exclusivity."""

    def __init__(
        self,
        message: str,
        *,
        name: str,
        rdtype: str,
        conflicting_types: list[str],
        index: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.name = name
        self.rdtype = rdtype
        self.conflicting_types = conflicting_types
        self.index = index


@dataclass(frozen=True)
class OwnerTypeOp:
    """A delete/add/replace affecting types at an owner name (for simulation)."""

    action: Literal["add", "delete", "replace"]
    name: str
    rdtype: str | None  # None on delete means delete all types at the name
    index: int | None = None


def material_types(existing: Iterable[str]) -> set[str]:
    """Return non-DNSSEC types present at a name."""
    return {t.upper() for t in existing} - DNSSEC_COMPANION_TYPES


def conflicting_types_for_add(existing: Iterable[str], adding: str) -> list[str]:
    """Return types that would conflict with adding ``adding`` at a name.

    Same-type duplicates are not listed here (handled by NXRRSET / RRSET_EXISTS).
    """
    adding_u = adding.upper()
    present = material_types(existing)
    if adding_u == "CNAME":
        return sorted(t for t in present if t != "CNAME")
    if "CNAME" in present:
        return ["CNAME"]
    return []


def format_cname_conflict_message(
    name: str,
    adding: str,
    conflicting_types: Sequence[str],
    *,
    index: int | None = None,
) -> str:
    """Human-readable conflict message for API responses."""
    adding_u = adding.upper()
    types = ", ".join(conflicting_types)
    prefix = f"Operation {index}: " if index is not None else ""
    if adding_u == "CNAME":
        return (
            f"{prefix}Cannot add CNAME at {name}: name already has {types}. "
            "Delete those records first (or include deletes in the same atomic update)."
        )
    return (
        f"{prefix}Cannot add {adding_u} at {name}: name already has a CNAME. "
        "Delete the CNAME first (or include the delete in the same atomic update)."
    )


def simulate_cname_exclusivity(
    initial_types: Mapping[str, set[str]],
    ops: Sequence[OwnerTypeOp],
) -> CnameConflictError | None:
    """Walk delete/add/replace ops in order; return the first CNAME conflict.

    Models BIND's ignore rules after updates are applied in sequence (prereqs
    are not modeled — callers must not attach NXDOMAIN when deletes clear the
    name in the same UPDATE).
    """
    state: dict[str, set[str]] = {name: set(types) for name, types in initial_types.items()}

    for op in ops:
        types = state.setdefault(op.name, set())

        if op.action == "delete":
            if op.rdtype is None:
                types.clear()
            else:
                types.discard(op.rdtype.upper())
            continue

        if op.rdtype is None:
            continue

        rdtype = op.rdtype.upper()
        if op.action == "replace":
            types.discard(rdtype)

        conflicts = conflicting_types_for_add(types, rdtype)
        if conflicts:
            message = format_cname_conflict_message(
                op.name,
                rdtype,
                conflicts,
                index=op.index,
            )
            return CnameConflictError(
                message,
                name=op.name,
                rdtype=rdtype,
                conflicting_types=conflicts,
                index=op.index,
            )

        types.add(rdtype)

    return None


def ops_delete_cname_or_all(
    ops: Sequence[OwnerTypeOp],
    name: str,
) -> bool:
    """True if ops include a delete of CNAME or all types at ``name``."""
    for op in ops:
        if op.action != "delete" or op.name != name:
            continue
        if op.rdtype is None or op.rdtype.upper() == "CNAME":
            return True
    return False
