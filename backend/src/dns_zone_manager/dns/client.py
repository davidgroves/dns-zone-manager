"""DNS client for AXFR/IXFR zone transfers and DDNS updates."""

import logging
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import dns.inet
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rdtypes.ANY.SOA
import dns.resolver
import dns.tsig
import dns.tsigkeyring
import dns.update
import dns.zone
from dns.exception import DNSException

from dns_zone_manager.logging import log_internal_event

if TYPE_CHECKING:
    from dns_zone_manager.config import Settings

logger = logging.getLogger(__name__)


def resolve_server_address(server: str) -> str:
    """Resolve a DNS server hostname to an IP address.

    dnspython's resolver and ``dns.query`` functions require the target
    nameserver to be an IP address, not a hostname. This lets configuration
    use a hostname (for example a Docker service name like ``bind``) that is
    resolved to an IP via the system resolver before use.

    Args:
        server: An IPv4/IPv6 address or a hostname.

    Returns:
        An IP address string. If ``server`` is already an IP it is returned
        unchanged.

    Raises:
        ValueError: If the hostname cannot be resolved to an IP address.
    """
    try:
        # Raises ValueError if not a valid IP literal.
        dns.inet.af_for_address(server)
        return server
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(server, None)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve DNS server hostname '{server}': {e}") from e

    # Prefer IPv4 for broad compatibility, otherwise take the first result.
    for family, _type, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            return str(sockaddr[0])
    if infos:
        return str(infos[0][4][0])
    raise ValueError(f"Could not resolve DNS server hostname '{server}': no addresses returned")


class DNSClientError(Exception):
    """Base exception for DNS client errors."""

    pass


class PrerequisiteFailedError(DNSClientError):
    """Raised when DDNS update prerequisites fail (NXRRSET, YXRRSET, etc.)."""

    def __init__(self, message: str, rcode: int | None = None, rcode_text: str | None = None):
        super().__init__(message)
        self.rcode = rcode
        self.rcode_text = rcode_text


class ZoneTransferError(DNSClientError):
    """Raised when zone transfer fails."""

    pass


class UpdateError(DNSClientError):
    """Raised when DDNS update fails."""

    pass


# User-friendly descriptions for DNS UPDATE rcodes
RCODE_DESCRIPTIONS: dict[str, str] = {
    "YXRRSET": "Record already exists when it shouldn't",
    "NXRRSET": "Record doesn't exist when it should",
    "YXDOMAIN": "Name already exists when it shouldn't",
    "NXDOMAIN": "Name doesn't exist when it should",
}


@dataclass
class RRsetInfo:
    """Information about a cached RRset."""

    name: str
    ttl: int
    rdtype: str
    rdclass: str
    records: list[str]


@dataclass
class HistoryChange:
    """A single record change in zone history."""

    action: Literal["add", "delete"]
    name: str
    ttl: int
    rdtype: str
    rdclass: str
    records: list[str]


@dataclass
class HistoryBatch:
    """A batch of changes between two serial numbers."""

    from_serial: int
    to_serial: int
    changes: list[HistoryChange] = field(default_factory=list)


@dataclass
class IXFRResult:
    """Result of an IXFR transfer."""

    zone: str
    current_serial: int
    batches: list[HistoryBatch]
    is_full_axfr: bool  # True if server responded with AXFR instead of IXFR
    available_from_serial: int  # Oldest serial in the returned history


class DNSClient:
    """Client for DNS operations using dnspython."""

    def __init__(self, settings: "Settings"):
        """Initialize DNS client with settings.

        Args:
            settings: Application settings containing DNS server and TSIG config
        """
        # Configuration may specify a hostname (e.g. a Docker service name);
        # dnspython requires an IP, so resolve it once at construction time.
        self.server_host = settings.dns.server
        self.server = resolve_server_address(settings.dns.server)
        if self.server != self.server_host:
            log_internal_event(
                "dns_server_resolved",
                logger,
                server_host=self.server_host,
                server_ip=self.server,
            )
        self.port = settings.dns.port  # UDP port for queries
        self.tcp_port = settings.dns.effective_tcp_port  # TCP port for AXFR/DDNS
        self.timeout = settings.dns.timeout
        self.axfr_timeout = settings.dns.axfr_timeout

        # Set up TSIG keyring for authenticated operations
        tsig_key = settings.get_update_tsig_key()
        if not tsig_key:
            raise ValueError(
                f"TSIG key '{settings.dns.update_tsig_key}' not found in tsig_keys. "
                f"Available keys: {[k.name for k in settings.tsig_keys]}"
            )

        self.keyring = dns.tsigkeyring.from_text(
            {tsig_key.name: tsig_key.secret.get_secret_value()}
        )
        self.keyname = dns.name.from_text(tsig_key.name)
        self.keyalgorithm = self._get_tsig_algorithm(tsig_key.algorithm)

    def _get_tsig_algorithm(self, algorithm: str) -> dns.name.Name:
        """Convert algorithm string to dnspython constant."""
        algorithms = {
            "hmac-sha256": dns.tsig.HMAC_SHA256,
            "hmac-sha384": dns.tsig.HMAC_SHA384,
            "hmac-sha512": dns.tsig.HMAC_SHA512,
            "hmac-md5": dns.tsig.HMAC_MD5,
            "hmac-sha1": dns.tsig.HMAC_SHA1,
        }
        return algorithms.get(algorithm.lower(), dns.tsig.HMAC_SHA256)

    def normalize_name(self, name: str, zone: str) -> dns.name.Name:
        """Normalize a record name to be absolute (FQDN).

        Args:
            name: Record name (relative or absolute)
            zone: Zone name

        Returns:
            Absolute DNS name
        """
        zone_name = dns.name.from_text(zone)

        if name == "@" or name == "":
            return zone_name

        # If name ends with a dot, it's explicitly absolute
        if name.endswith("."):
            return dns.name.from_text(name)

        # Otherwise, treat as relative to the zone
        return dns.name.from_text(name, origin=zone_name)

    def perform_axfr(self, zone: str) -> dns.zone.Zone:
        """Perform AXFR zone transfer.

        Args:
            zone: Zone name to transfer

        Returns:
            dns.zone.Zone object containing all records

        Raises:
            ZoneTransferError: If transfer fails
        """
        zone_name = dns.name.from_text(zone)

        try:
            log_internal_event(
                "axfr_start",
                logger,
                zone=zone,
                server=self.server,
                port=self.tcp_port,
            )
            xfr = dns.query.xfr(
                self.server,
                zone_name,
                port=self.tcp_port,  # AXFR uses TCP
                keyring=self.keyring,
                keyname=self.keyname,
                keyalgorithm=self.keyalgorithm,
                lifetime=self.axfr_timeout,
            )

            zone_obj = dns.zone.from_xfr(xfr)
            log_internal_event(
                "axfr_complete",
                logger,
                zone=zone,
                nodes=len(zone_obj.nodes),
            )
            return zone_obj

        except DNSException as e:
            log_internal_event(
                "axfr_failed",
                logger,
                level="ERROR",
                zone=zone,
                error=str(e),
            )
            raise ZoneTransferError(f"Zone transfer failed for {zone}: {e}") from e

    def perform_ixfr(self, zone: str, from_serial: int = 0) -> IXFRResult:
        """Perform IXFR to get zone history from a serial number.

        IXFR (RFC 1995) returns incremental changes between serial numbers.
        The response format is:
        - Initial SOA (current serial)
        - For each change: [old SOA] [deleted records] [new SOA] [added records]
        - Final SOA

        If the server doesn't have history from the requested serial, it may
        respond with a full AXFR instead.

        Args:
            zone: Zone name to transfer
            from_serial: Serial number to start from (0 = get all history)

        Returns:
            IXFRResult containing history batches

        Raises:
            ZoneTransferError: If transfer fails
        """
        zone_name = dns.name.from_text(zone)

        try:
            log_internal_event(
                "ixfr_start",
                logger,
                zone=zone,
                from_serial=from_serial,
                server=self.server,
                port=self.tcp_port,
            )

            # Request IXFR by providing serial parameter and rdtype=IXFR
            xfr = dns.query.xfr(
                self.server,
                zone_name,
                port=self.tcp_port,
                keyring=self.keyring,
                keyname=self.keyname,
                keyalgorithm=self.keyalgorithm,
                lifetime=self.axfr_timeout,
                serial=from_serial,
                rdtype=dns.rdatatype.IXFR,  # Explicitly request IXFR
                use_udp=False,  # IXFR over TCP for reliability
            )

            return self._parse_ixfr_response(zone, xfr)

        except DNSException as e:
            log_internal_event(
                "ixfr_failed",
                logger,
                level="ERROR",
                zone=zone,
                error=str(e),
            )
            raise ZoneTransferError(f"IXFR transfer failed for {zone}: {e}") from e

    def _parse_ixfr_response(
        self, zone: str, xfr_messages: Iterable[dns.message.Message]
    ) -> IXFRResult:
        """Parse IXFR response messages into history batches.

        IXFR format per RFC 1995:
        - SOA (current) - marks start
        - For each delta:
          - SOA (old serial N) - marks start of deletions
          - RRs to delete
          - SOA (new serial N+1) - marks start of additions
          - RRs to add
        - SOA (current) - marks end

        If only 2 SOAs are present with same serial, it's an AXFR response.

        Args:
            zone: Zone name
            xfr_messages: Iterator of DNS messages from xfr query

        Returns:
            IXFRResult with parsed history batches
        """
        # Collect all records from the transfer
        all_records: list[tuple[dns.name.Name, int, dns.rdata.Rdata]] = []
        # Store SOA records with index and cast type for serial access
        soa_records: list[tuple[int, dns.rdtypes.ANY.SOA.SOA]] = []

        record_index = 0
        zone_origin = dns.name.from_text(zone)

        for message in xfr_messages:
            for rrset in message.answer:
                for rdata in rrset:
                    all_records.append((rrset.name, rrset.ttl, rdata))
                    if rdata.rdtype == dns.rdatatype.SOA:
                        # Cast to SOA type for proper attribute access
                        soa_rdata: dns.rdtypes.ANY.SOA.SOA = rdata
                        soa_records.append((record_index, soa_rdata))
                    record_index += 1

        if not soa_records:
            raise ZoneTransferError(f"IXFR response for {zone} contains no SOA records")

        # Get current serial from first SOA
        current_serial = soa_records[0][1].serial

        # Detect if this is AXFR (only 2 identical SOAs) or true IXFR
        is_full_axfr = len(soa_records) == 2 and all(
            soa[1].serial == current_serial for soa in soa_records
        )

        if is_full_axfr:
            # AXFR response - return all records as a single "add" batch
            log_internal_event(
                "ixfr_fallback_axfr",
                logger,
                zone=zone,
                serial=current_serial,
                record_count=len(all_records),
            )
            batch = HistoryBatch(
                from_serial=0,
                to_serial=current_serial,
                changes=[],
            )
            # Add all non-SOA records as "adds"
            for name, ttl, rdata in all_records:
                if rdata.rdtype != dns.rdatatype.SOA:
                    fqdn = name.derelativize(zone_origin) if not name.is_absolute() else name
                    batch.changes.append(
                        HistoryChange(
                            action="add",
                            name=str(fqdn),
                            ttl=ttl,
                            rdtype=dns.rdatatype.to_text(rdata.rdtype),
                            rdclass=dns.rdataclass.to_text(rdata.rdclass),
                            records=[rdata.to_text()],
                        )
                    )

            return IXFRResult(
                zone=zone,
                current_serial=current_serial,
                batches=[batch] if batch.changes else [],
                is_full_axfr=True,
                available_from_serial=0,
            )

        # True IXFR response - parse incremental batches
        batches: list[HistoryBatch] = []
        available_from_serial = current_serial

        # Skip the first and last SOA (they're just markers)
        # Process pairs: old_soa -> deletions -> new_soa -> additions
        i = 1  # Start after first SOA
        while i < len(soa_records) - 1:
            old_soa_idx, old_soa = soa_records[i]
            old_serial = old_soa.serial

            if i + 1 >= len(soa_records):
                break

            new_soa_idx, new_soa = soa_records[i + 1]
            new_serial = new_soa.serial

            # Track the oldest serial we have history from
            if old_serial < available_from_serial:
                available_from_serial = old_serial

            batch = HistoryBatch(
                from_serial=old_serial,
                to_serial=new_serial,
                changes=[],
            )

            # Records between old_soa and new_soa are deletions
            for idx in range(old_soa_idx + 1, new_soa_idx):
                name, ttl, rdata = all_records[idx]
                if rdata.rdtype != dns.rdatatype.SOA:
                    fqdn = name.derelativize(zone_origin) if not name.is_absolute() else name
                    batch.changes.append(
                        HistoryChange(
                            action="delete",
                            name=str(fqdn),
                            ttl=ttl,
                            rdtype=dns.rdatatype.to_text(rdata.rdtype),
                            rdclass=dns.rdataclass.to_text(rdata.rdclass),
                            records=[rdata.to_text()],
                        )
                    )

            # Find the end of additions (next SOA or end of records)
            if i + 2 < len(soa_records):
                next_soa_idx = soa_records[i + 2][0]
            else:
                next_soa_idx = len(all_records)

            # Records between new_soa and next_soa are additions
            for idx in range(new_soa_idx + 1, next_soa_idx):
                name, ttl, rdata = all_records[idx]
                if rdata.rdtype != dns.rdatatype.SOA:
                    fqdn = name.derelativize(zone_origin) if not name.is_absolute() else name
                    batch.changes.append(
                        HistoryChange(
                            action="add",
                            name=str(fqdn),
                            ttl=ttl,
                            rdtype=dns.rdatatype.to_text(rdata.rdtype),
                            rdclass=dns.rdataclass.to_text(rdata.rdclass),
                            records=[rdata.to_text()],
                        )
                    )

            if batch.changes:
                batches.append(batch)

            i += 2  # Move to next pair

        log_internal_event(
            "ixfr_complete",
            logger,
            zone=zone,
            current_serial=current_serial,
            batch_count=len(batches),
            available_from_serial=available_from_serial,
        )

        return IXFRResult(
            zone=zone,
            current_serial=current_serial,
            batches=batches,
            is_full_axfr=False,
            available_from_serial=available_from_serial,
        )

    def get_rrset(
        self,
        zone: str,
        name: str,
        rdtype: str,
    ) -> RRsetInfo | None:
        """Query for a specific RRset.

        Args:
            zone: Zone name
            name: Record name (relative or absolute)
            rdtype: Record type (e.g., "A", "MX")

        Returns:
            RRsetInfo if found, None otherwise
        """
        fqdn = self.normalize_name(name, zone)

        try:
            resolver = self._create_resolver()
            answer = resolver.resolve(fqdn, rdtype)
            rrset = answer.rrset
            if rrset is None:
                return None
            records = [rdata.to_text() for rdata in rrset]
            rdclass_str = dns.rdataclass.to_text(rrset.rdclass)

            return RRsetInfo(
                name=str(fqdn),
                ttl=rrset.ttl,
                rdtype=rdtype,
                rdclass=rdclass_str,
                records=records,
            )

        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return None
        except DNSException as e:
            log_internal_event(
                "dns_query_failed",
                logger,
                level="WARNING",
                fqdn=str(fqdn),
                rdtype=rdtype,
                error=str(e),
            )
            return None

    def add_rrset(
        self,
        zone: str,
        name: str,
        ttl: int,
        rdtype: str,
        records: list[str],
        rdclass: str = "IN",
        prereq_not_exists: bool = True,
    ) -> None:
        """Add a new RRset using DDNS update with prerequisites.

        Args:
            zone: Zone name
            name: Record name
            ttl: TTL in seconds
            rdtype: Record type
            records: List of record data strings
            rdclass: Record class (default: IN)
            prereq_not_exists: If True, require that the RRset doesn't already exist

        Raises:
            PrerequisiteFailedError: If prerequisites fail
            UpdateError: If update fails
        """
        zone_name = dns.name.from_text(zone)
        fqdn = self.normalize_name(name, zone)
        rdtype_obj = dns.rdatatype.from_text(rdtype)
        rdclass_obj = dns.rdataclass.from_text(rdclass)

        update = dns.update.Update(
            zone_name,
            keyring=self.keyring,
            keyname=self.keyname,
            keyalgorithm=self.keyalgorithm,
        )

        # Add prerequisite: RRset should not exist
        if prereq_not_exists:
            update.absent(fqdn, rdtype_obj)

        # Add the records with specified class
        for record in records:
            rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
            update.add(fqdn, ttl, rdata)

        self._send_update(update, zone)

    def delete_rrset(
        self,
        zone: str,
        name: str,
        rdtype: str,
        records: list[str] | None = None,
        rdclass: str = "IN",
        prereq_records: list[str] | None = None,
    ) -> None:
        """Delete an RRset or specific records using DDNS update.

        Args:
            zone: Zone name
            name: Record name
            rdtype: Record type
            records: Specific records to delete (None = delete entire RRset)
            rdclass: Record class (default: IN)
            prereq_records: Expected current records for prerequisite check

        Raises:
            PrerequisiteFailedError: If prerequisites fail
            UpdateError: If update fails
        """
        zone_name = dns.name.from_text(zone)
        fqdn = self.normalize_name(name, zone)
        rdtype_obj = dns.rdatatype.from_text(rdtype)
        rdclass_obj = dns.rdataclass.from_text(rdclass)

        update = dns.update.Update(
            zone_name,
            keyring=self.keyring,
            keyname=self.keyname,
            keyalgorithm=self.keyalgorithm,
        )

        # Add prerequisite: RRset must exist (optionally with specific values)
        if prereq_records:
            for record in prereq_records:
                update.present(fqdn, rdtype_obj, record)
        else:
            # Just require the RRset exists
            update.present(fqdn, rdtype_obj)

        # Delete specific records or entire RRset
        if records:
            for record in records:
                rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
                update.delete(fqdn, rdata)
        else:
            # Delete entire RRset of this type
            update.delete(fqdn, rdtype_obj)

        self._send_update(update, zone)

    def replace_rrset(
        self,
        zone: str,
        name: str,
        ttl: int,
        rdtype: str,
        new_records: list[str],
        rdclass: str = "IN",
        prereq_records: list[str] | None = None,
    ) -> None:
        """Replace an entire RRset using DDNS update.

        Args:
            zone: Zone name
            name: Record name
            ttl: New TTL
            rdtype: Record type
            new_records: New record values
            rdclass: Record class (default: IN)
            prereq_records: Expected current records for prerequisite check

        Raises:
            PrerequisiteFailedError: If prerequisites fail
            UpdateError: If update fails
        """
        zone_name = dns.name.from_text(zone)
        fqdn = self.normalize_name(name, zone)
        rdtype_obj = dns.rdatatype.from_text(rdtype)
        rdclass_obj = dns.rdataclass.from_text(rdclass)

        update = dns.update.Update(
            zone_name,
            keyring=self.keyring,
            keyname=self.keyname,
            keyalgorithm=self.keyalgorithm,
        )

        # Add prerequisite: verify current state matches expected
        if prereq_records:
            for record in prereq_records:
                update.present(fqdn, rdtype_obj, record)
        else:
            # At minimum, require the RRset exists
            update.present(fqdn, rdtype_obj)

        # Delete existing and add new with specified class
        update.delete(fqdn, rdtype_obj)
        for record in new_records:
            rdata = dns.rdata.from_text(rdclass_obj, rdtype_obj, record)
            update.add(fqdn, ttl, rdata)

        self._send_update(update, zone)

    def _send_update(self, update: dns.update.Update, zone: str) -> None:
        """Send a DDNS update to the server.

        Args:
            update: The update message to send
            zone: Zone name (for logging)

        Raises:
            PrerequisiteFailedError: If prerequisites fail (NXRRSET/YXRRSET)
            UpdateError: If update fails for other reasons
        """
        try:
            response = dns.query.tcp(
                update,
                self.server,
                port=self.tcp_port,  # DDNS uses TCP
                timeout=self.timeout,
            )

            rcode = response.rcode()

            if rcode == dns.rcode.NOERROR:
                log_internal_event(
                    "ddns_update_success",
                    logger,
                    zone=zone,
                    server=self.server,
                )
                return

            rcode_text = dns.rcode.to_text(rcode)

            # Check for prerequisite failures
            if rcode in (
                dns.rcode.NXRRSET,
                dns.rcode.YXRRSET,
                dns.rcode.NXDOMAIN,
                dns.rcode.YXDOMAIN,
            ):
                log_internal_event(
                    "ddns_prereq_failed",
                    logger,
                    level="WARNING",
                    zone=zone,
                    rcode=rcode_text,
                )
                raise PrerequisiteFailedError(
                    f"Prerequisite failed: {rcode_text} - DNS state may have changed",
                    rcode=rcode,
                    rcode_text=rcode_text,
                )

            # Other errors
            log_internal_event(
                "ddns_update_failed",
                logger,
                level="ERROR",
                zone=zone,
                rcode=rcode_text,
            )
            raise UpdateError(f"Update failed with rcode {rcode_text}")

        except DNSException as e:
            if isinstance(e, PrerequisiteFailedError | UpdateError):
                raise
            log_internal_event(
                "ddns_update_error",
                logger,
                level="ERROR",
                zone=zone,
                error=str(e),
            )
            raise UpdateError(f"Update failed: {e}") from e

    def _create_resolver(self) -> dns.resolver.Resolver:
        """Create a configured resolver for DNS queries.

        Returns:
            Configured dns.resolver.Resolver
        """
        resolver = dns.resolver.Resolver()
        # Set nameservers and per-server port mapping for non-standard ports
        resolver.nameservers = [self.server]
        resolver.port = self.port
        resolver.nameserver_ports = {self.server: self.port}
        resolver.lifetime = self.timeout
        return resolver

    def check_server_responding(self) -> bool:
        """Check whether the DNS server is reachable and responding.

        Any DNS response counts as connected, including REFUSED, NXDOMAIN, or
        an empty answer. This matters for authoritative-only servers that
        refuse queries for zones they don't serve (e.g. the root zone): such a
        refusal still proves the server is up and answering. Only a timeout or
        transport error counts as not responding.

        Returns:
            True if the server returned any DNS response, False otherwise.
        """
        try:
            request = dns.message.make_query(dns.name.root, dns.rdatatype.SOA)
            # A returned message (any rcode, including REFUSED) means the
            # server responded. Only timeouts/transport errors raise here.
            dns.query.udp(request, self.server, port=self.port, timeout=self.timeout)
            return True
        except (DNSException, OSError):
            return False

    def check_zone_exists(self, zone: str) -> bool:
        """Check if a zone exists on the server.

        Args:
            zone: Zone name to check

        Returns:
            True if zone exists and is queryable
        """
        try:
            resolver = self._create_resolver()
            zone_name = dns.name.from_text(zone)
            resolver.resolve(zone_name, "SOA")
            return True

        except (
            dns.resolver.NXDOMAIN,
            dns.resolver.NoAnswer,
            dns.resolver.NoNameservers,
        ):
            return False
        except DNSException:
            return False

    def get_zone_serial(self, zone: str) -> int | None:
        """Get the SOA serial number for a zone.

        Args:
            zone: Zone name

        Returns:
            Serial number or None if zone doesn't exist
        """
        try:
            resolver = self._create_resolver()
            zone_name = dns.name.from_text(zone)
            answer = resolver.resolve(zone_name, "SOA")

            for rdata in answer:
                return rdata.serial

            return None

        except DNSException:
            return None
