"""Parser for NSUPDATE-formatted text files.

This module parses the nsupdate(1) file format and converts it into
structured data that can be executed by the DNS client.

Supported commands:
- zone <zonename> - Set target zone
- prereq nxdomain <name> - Name must not exist
- prereq yxdomain <name> - Name must exist
- prereq nxrrset <name> [class] <type> - RRset must not exist
- prereq yxrrset <name> [class] <type> [data...] - RRset must exist
- update add <name> <ttl> [class] <type> <data> - Add record
- update delete <name> [ttl] [class] [type] [data] - Delete record(s)
- send - Execute the update transaction

Ignored commands (API uses its own configuration):
- server - Uses configured DNS server
- key - Uses configured TSIG key
- local - Not applicable
"""

import logging
import shlex
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class NSUpdateParseError(Exception):
    """Raised when parsing NSUPDATE text fails."""

    def __init__(self, message: str, line_number: int | None = None):
        self.line_number = line_number
        if line_number is not None:
            message = f"Line {line_number}: {message}"
        super().__init__(message)


class PrereqType(str, Enum):
    """Types of prerequisite checks."""

    NXDOMAIN = "nxdomain"  # Name must not exist
    YXDOMAIN = "yxdomain"  # Name must exist
    NXRRSET = "nxrrset"  # RRset must not exist
    YXRRSET = "yxrrset"  # RRset must exist (optionally with specific data)


class UpdateAction(str, Enum):
    """Types of update actions."""

    ADD = "add"
    DELETE = "delete"


@dataclass
class Prerequisite:
    """A prerequisite check for an update."""

    prereq_type: PrereqType
    name: str
    rdclass: str = "IN"
    rdtype: str | None = None
    data: str | None = None  # For yxrrset with specific data


@dataclass
class UpdateOperation:
    """A single update operation (add or delete)."""

    action: UpdateAction
    name: str
    ttl: int | None = None
    rdclass: str = "IN"
    rdtype: str | None = None
    data: str | None = None


@dataclass
class ParsedUpdate:
    """A parsed update transaction (everything between zone/start and send)."""

    zone: str
    prerequisites: list[Prerequisite] = field(default_factory=list)
    operations: list[UpdateOperation] = field(default_factory=list)


# DNS classes for validation
DNS_CLASSES = {"IN", "CH", "HS", "NONE", "ANY"}


def _is_dns_class(token: str) -> bool:
    """Check if a token is a DNS class."""
    return token.upper() in DNS_CLASSES


def _is_ttl(token: str) -> bool:
    """Check if a token looks like a TTL (numeric)."""
    return token.isdigit()


def _tokenize_line(line: str) -> list[str]:
    """Tokenize a line, handling quoted strings properly."""
    try:
        # shlex handles quoted strings
        return shlex.split(line)
    except ValueError:
        # Fall back to simple split if quotes are unbalanced
        return line.split()


def _parse_prereq(tokens: list[str], line_number: int) -> Prerequisite:
    """Parse a prereq command.

    Formats:
    - prereq nxdomain <name>
    - prereq yxdomain <name>
    - prereq nxrrset <name> [class] <type>
    - prereq yxrrset <name> [class] <type> [data...]
    """
    if len(tokens) < 2:
        raise NSUpdateParseError("prereq requires at least type and name", line_number)

    prereq_type_str = tokens[0].lower()
    try:
        prereq_type = PrereqType(prereq_type_str)
    except ValueError:
        raise NSUpdateParseError(
            f"Unknown prereq type: {prereq_type_str}. "
            f"Expected: nxdomain, yxdomain, nxrrset, yxrrset",
            line_number,
        )

    name = tokens[1]

    if prereq_type in (PrereqType.NXDOMAIN, PrereqType.YXDOMAIN):
        # Just need the name
        return Prerequisite(prereq_type=prereq_type, name=name)

    # nxrrset or yxrrset need type, optionally class
    remaining = tokens[2:]
    if not remaining:
        raise NSUpdateParseError(f"prereq {prereq_type_str} requires a record type", line_number)

    rdclass = "IN"
    if _is_dns_class(remaining[0]):
        rdclass = remaining[0].upper()
        remaining = remaining[1:]

    if not remaining:
        raise NSUpdateParseError(f"prereq {prereq_type_str} requires a record type", line_number)

    rdtype = remaining[0].upper()
    data = None

    # yxrrset can have optional data
    if prereq_type == PrereqType.YXRRSET and len(remaining) > 1:
        data = " ".join(remaining[1:])

    return Prerequisite(
        prereq_type=prereq_type,
        name=name,
        rdclass=rdclass,
        rdtype=rdtype,
        data=data,
    )


def _parse_update_add(tokens: list[str], line_number: int) -> UpdateOperation:
    """Parse an update add command.

    Format: update add <name> <ttl> [class] <type> <data...>
    """
    if len(tokens) < 4:
        raise NSUpdateParseError("update add requires: name ttl [class] type data", line_number)

    name = tokens[0]

    if not _is_ttl(tokens[1]):
        raise NSUpdateParseError(f"Expected TTL, got: {tokens[1]}", line_number)
    ttl = int(tokens[1])

    remaining = tokens[2:]

    rdclass = "IN"
    if _is_dns_class(remaining[0]):
        rdclass = remaining[0].upper()
        remaining = remaining[1:]

    if len(remaining) < 2:
        raise NSUpdateParseError("update add requires type and data after TTL/class", line_number)

    rdtype = remaining[0].upper()
    data = " ".join(remaining[1:])

    return UpdateOperation(
        action=UpdateAction.ADD,
        name=name,
        ttl=ttl,
        rdclass=rdclass,
        rdtype=rdtype,
        data=data,
    )


def _parse_update_delete(tokens: list[str], line_number: int) -> UpdateOperation:
    """Parse an update delete command.

    Formats:
    - update delete <name> - Delete all RRsets at name
    - update delete <name> [class] <type> - Delete RRset
    - update delete <name> [class] <type> <data> - Delete specific record
    """
    if len(tokens) < 1:
        raise NSUpdateParseError("update delete requires at least a name", line_number)

    name = tokens[0]
    remaining = tokens[1:]

    # Skip TTL if present (ignored for delete)
    if remaining and _is_ttl(remaining[0]):
        remaining = remaining[1:]

    rdclass = "IN"
    rdtype = None
    data = None

    if remaining:
        if _is_dns_class(remaining[0]):
            rdclass = remaining[0].upper()
            remaining = remaining[1:]

        if remaining:
            rdtype = remaining[0].upper()
            remaining = remaining[1:]

            if remaining:
                data = " ".join(remaining)

    return UpdateOperation(
        action=UpdateAction.DELETE,
        name=name,
        rdclass=rdclass,
        rdtype=rdtype,
        data=data,
    )


def parse(text: str, default_zone: str | None = None) -> list[ParsedUpdate]:
    """Parse NSUPDATE-formatted text into structured updates.

    Args:
        text: The nsupdate-formatted text
        default_zone: Default zone if not specified in the text

    Returns:
        List of ParsedUpdate objects, one per 'send' command

    Raises:
        NSUpdateParseError: If parsing fails
    """
    updates: list[ParsedUpdate] = []

    # Normalize default zone
    current_zone = default_zone
    if current_zone and not current_zone.endswith("."):
        current_zone += "."
    current_prereqs: list[Prerequisite] = []
    current_ops: list[UpdateOperation] = []

    lines = text.splitlines()

    for line_num, line in enumerate(lines, start=1):
        # Strip whitespace
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith(";") or line.startswith("#"):
            continue

        # Tokenize
        tokens = _tokenize_line(line)
        if not tokens:
            continue

        cmd = tokens[0].lower()

        # Handle commands
        if cmd == "zone":
            if len(tokens) < 2:
                raise NSUpdateParseError("zone requires a zone name", line_num)
            current_zone = tokens[1]
            # Ensure zone ends with dot for consistency
            if not current_zone.endswith("."):
                current_zone += "."
            logger.debug(f"Set zone to: {current_zone}")

        elif cmd == "server":
            # Ignored - API uses its own server configuration
            logger.debug("Ignoring 'server' command (using API config)")

        elif cmd == "key":
            # Ignored - API uses its own TSIG configuration
            logger.debug("Ignoring 'key' command (using API config)")

        elif cmd == "local":
            # Ignored - not applicable
            logger.debug("Ignoring 'local' command")

        elif cmd == "prereq":
            prereq = _parse_prereq(tokens[1:], line_num)
            current_prereqs.append(prereq)
            logger.debug(f"Added prereq: {prereq}")

        elif cmd == "update":
            if len(tokens) < 2:
                raise NSUpdateParseError("update requires add or delete", line_num)

            subcmd = tokens[1].lower()
            if subcmd == "add":
                op = _parse_update_add(tokens[2:], line_num)
                current_ops.append(op)
                logger.debug(f"Added update add: {op}")
            elif subcmd == "delete":
                op = _parse_update_delete(tokens[2:], line_num)
                current_ops.append(op)
                logger.debug(f"Added update delete: {op}")
            else:
                raise NSUpdateParseError(
                    f"Unknown update subcommand: {subcmd}. Expected: add, delete",
                    line_num,
                )

        elif cmd == "send":
            # Finalize current update
            if not current_zone:
                raise NSUpdateParseError(
                    "No zone specified. Use 'zone' command or provide default_zone",
                    line_num,
                )

            if not current_prereqs and not current_ops:
                logger.warning(f"Empty update transaction at line {line_num}")
            else:
                updates.append(
                    ParsedUpdate(
                        zone=current_zone,
                        prerequisites=current_prereqs,
                        operations=current_ops,
                    )
                )
                logger.info(
                    f"Parsed update for zone {current_zone}: "
                    f"{len(current_prereqs)} prereqs, {len(current_ops)} operations"
                )

            # Reset for next transaction (keep zone)
            current_prereqs = []
            current_ops = []

        elif cmd == "show":
            # Debug command - ignored in API
            logger.debug("Ignoring 'show' command")

        elif cmd == "answer":
            # Debug command - ignored in API
            logger.debug("Ignoring 'answer' command")

        elif cmd == "debug":
            # Debug command - ignored in API
            logger.debug("Ignoring 'debug' command")

        elif cmd == "quit":
            # Stop processing
            break

        else:
            raise NSUpdateParseError(f"Unknown command: {cmd}", line_num)

    # Handle trailing operations without send (implicit send)
    if current_prereqs or current_ops:
        if not current_zone:
            raise NSUpdateParseError(
                "No zone specified for trailing operations. "
                "Use 'zone' command or provide default_zone"
            )
        updates.append(
            ParsedUpdate(
                zone=current_zone,
                prerequisites=current_prereqs,
                operations=current_ops,
            )
        )
        logger.info(
            f"Parsed trailing update for zone {current_zone}: "
            f"{len(current_prereqs)} prereqs, {len(current_ops)} operations"
        )

    return updates
