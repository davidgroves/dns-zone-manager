"""DNS record type and class definitions based on IANA registry.

Reference: https://www.iana.org/assignments/dns-parameters/dns-parameters.xhtml
"""

from dataclasses import dataclass
from enum import IntEnum

# =============================================================================
# DNS Classes (IANA DNS CLASSes registry)
# =============================================================================


class RRClass(IntEnum):
    """DNS Resource Record Classes from IANA registry."""

    RESERVED = 0  # Reserved
    IN = 1  # Internet
    CH = 3  # Chaos
    HS = 4  # Hesiod
    NONE = 254  # QCLASS NONE (used in updates)
    ANY = 255  # QCLASS * (ANY)


@dataclass
class RecordClassInfo:
    """Information about a DNS record class."""

    class_code: int
    name: str
    description: str
    rfc: str | None = None


# DNS Classes from IANA registry
DNS_RECORD_CLASSES: dict[str, RecordClassInfo] = {
    "IN": RecordClassInfo(1, "IN", "Internet", "RFC1035"),
    "CH": RecordClassInfo(3, "CH", "Chaos", None),
    "HS": RecordClassInfo(4, "HS", "Hesiod", None),
    "NONE": RecordClassInfo(254, "NONE", "QCLASS NONE", "RFC2136"),
    "ANY": RecordClassInfo(255, "ANY", "QCLASS * (ANY)", "RFC1035"),
}

# Common classes shown in UI dropdown
COMMON_CLASSES = frozenset({"IN", "CH", "HS"})

# Create reverse mapping from class code to name
CLASS_CODE_TO_NAME: dict[int, str] = {
    info.class_code: name for name, info in DNS_RECORD_CLASSES.items()
}


def get_class_code(class_name: str) -> int | None:
    """Get the numeric class code for a record class name."""
    info = DNS_RECORD_CLASSES.get(class_name.upper())
    return info.class_code if info else None


def get_class_name(class_code: int) -> str | None:
    """Get the record class name for a numeric class code."""
    return CLASS_CODE_TO_NAME.get(class_code)


def is_valid_class(rdclass: str) -> bool:
    """Check if a record class name or number is valid.

    Accepts:
    - Named classes: IN, CH, HS, NONE, ANY
    - Numeric classes: 1, 3, 4, etc.
    - CLASS### format: CLASS1, CLASS3, etc.
    - Hex format: 0x1, 0x3, etc.

    Returns:
        True if the class is valid (0-65535 range for numbers)
    """
    rdclass = rdclass.strip().upper()

    # Check named classes
    if rdclass in DNS_RECORD_CLASSES:
        return True

    # Check CLASS### format
    if rdclass.startswith("CLASS"):
        try:
            code = int(rdclass[5:])
            return 0 <= code <= 65535
        except ValueError:
            return False

    # Check hex format
    if rdclass.startswith("0X"):
        try:
            code = int(rdclass, 16)
            return 0 <= code <= 65535
        except ValueError:
            return False

    # Check decimal number
    try:
        code = int(rdclass)
        return 0 <= code <= 65535
    except ValueError:
        return False


def normalize_class(rdclass: str) -> str:
    """Normalize a class input to standard format.

    Converts numeric inputs to names where possible, otherwise CLASS### format.

    Args:
        rdclass: Class name, number, or CLASS### format

    Returns:
        Normalized class string (e.g., "IN", "CH", or "CLASS123")
    """
    rdclass = rdclass.strip().upper()

    # Already a known name
    if rdclass in DNS_RECORD_CLASSES:
        return rdclass

    # Extract numeric value
    code: int | None = None

    if rdclass.startswith("CLASS"):
        try:
            code = int(rdclass[5:])
        except ValueError:
            pass
    elif rdclass.startswith("0X"):
        try:
            code = int(rdclass, 16)
        except ValueError:
            pass
    else:
        try:
            code = int(rdclass)
        except ValueError:
            pass

    if code is not None:
        # Try to convert to name
        name = get_class_name(code)
        if name:
            return name
        # Return CLASS### format
        return f"CLASS{code}"

    # Return as-is if we can't parse it
    return rdclass


def get_supported_classes() -> list[str]:
    """Get list of all supported class names.

    Returns:
        List of class names
    """
    return list(DNS_RECORD_CLASSES.keys())


# Classes that should not be used for records (query-only classes)
PROTECTED_CLASSES = frozenset({"NONE", "ANY"})

# Classes that can be used for records
UPDATABLE_CLASSES = frozenset(
    name for name in DNS_RECORD_CLASSES.keys() if name not in PROTECTED_CLASSES
)


class RRType(IntEnum):
    """DNS Resource Record Types from IANA registry."""

    # Standard types (RFC 1035 and extensions)
    A = 1  # Host address (IPv4)
    NS = 2  # Authoritative name server
    MD = 3  # Mail destination (obsolete)
    MF = 4  # Mail forwarder (obsolete)
    CNAME = 5  # Canonical name
    SOA = 6  # Start of authority
    MB = 7  # Mailbox domain name
    MG = 8  # Mail group member
    MR = 9  # Mail rename domain
    NULL = 10  # Null RR
    WKS = 11  # Well known service
    PTR = 12  # Pointer
    HINFO = 13  # Host information
    MINFO = 14  # Mailbox information
    MX = 15  # Mail exchange
    TXT = 16  # Text strings
    RP = 17  # Responsible person
    AFSDB = 18  # AFS database location
    X25 = 19  # X.25 PSDN address
    ISDN = 20  # ISDN address
    RT = 21  # Route through
    NSAP = 22  # NSAP address
    NSAP_PTR = 23  # NSAP pointer
    SIG = 24  # Security signature (obsolete)
    KEY = 25  # Security key (obsolete)
    PX = 26  # X.400 mail mapping
    GPOS = 27  # Geographical position
    AAAA = 28  # IPv6 address
    LOC = 29  # Location information
    NXT = 30  # Next domain (obsolete)
    EID = 31  # Endpoint identifier
    NIMLOC = 32  # Nimrod locator
    SRV = 33  # Server selection
    ATMA = 34  # ATM address
    NAPTR = 35  # Naming authority pointer
    KX = 36  # Key exchanger
    CERT = 37  # Certificate
    A6 = 38  # A6 (obsolete)
    DNAME = 39  # DNAME
    SINK = 40  # SINK
    OPT = 41  # OPT (EDNS)
    APL = 42  # APL
    DS = 43  # Delegation signer
    SSHFP = 44  # SSH key fingerprint
    IPSECKEY = 45  # IPsec key
    RRSIG = 46  # DNSSEC signature
    NSEC = 47  # Next secure
    DNSKEY = 48  # DNS key
    DHCID = 49  # DHCP identifier
    NSEC3 = 50  # NSEC3
    NSEC3PARAM = 51  # NSEC3 parameters
    TLSA = 52  # TLSA certificate association
    SMIMEA = 53  # S/MIME certificate association
    HIP = 55  # Host identity protocol
    NINFO = 56  # NINFO
    RKEY = 57  # RKEY
    TALINK = 58  # Trust anchor link
    CDS = 59  # Child DS
    CDNSKEY = 60  # Child DNSKEY
    OPENPGPKEY = 61  # OpenPGP key
    CSYNC = 62  # Child-to-parent synchronization
    ZONEMD = 63  # Message digest for DNS zone
    SVCB = 64  # Service binding
    HTTPS = 65  # HTTPS service binding
    SPF = 99  # SPF (obsolete, use TXT)
    UINFO = 100  # UINFO
    UID = 101  # UID
    GID = 102  # GID
    UNSPEC = 103  # UNSPEC
    NID = 104  # Node identifier
    L32 = 105  # 32-bit locator
    L64 = 106  # 64-bit locator
    LP = 107  # Locator pointer
    EUI48 = 108  # EUI-48 address
    EUI64 = 109  # EUI-64 address
    NXNAME = 128  # NXNAME
    TKEY = 249  # Transaction key
    TSIG = 250  # Transaction signature
    IXFR = 251  # Incremental transfer
    AXFR = 252  # Authoritative transfer
    MAILB = 253  # Mailbox-related (obsolete)
    MAILA = 254  # Mail agent (obsolete)
    ANY = 255  # Any type (QTYPE)
    URI = 256  # URI
    CAA = 257  # Certification authority authorization
    AVC = 258  # Application visibility and control
    DOA = 259  # Digital object architecture
    AMTRELAY = 260  # Automatic multicast tunneling relay
    RESINFO = 261  # Resolver information
    WALLET = 262  # Wallet
    CLA = 263  # CLA
    IPN = 264  # IPN
    TA = 32768  # Trust authorities
    DLV = 32769  # DNSSEC lookaside validation


@dataclass
class RecordTypeInfo:
    """Information about a DNS record type."""

    type_code: int
    name: str
    description: str
    rfc: str | None = None
    obsolete: bool = False
    experimental: bool = False


# Comprehensive mapping of all DNS record types
DNS_RECORD_TYPES: dict[str, RecordTypeInfo] = {
    "A": RecordTypeInfo(1, "A", "Host address (IPv4)", "RFC1035"),
    "NS": RecordTypeInfo(2, "NS", "Authoritative name server", "RFC1035"),
    "MD": RecordTypeInfo(3, "MD", "Mail destination", "RFC1035", obsolete=True),
    "MF": RecordTypeInfo(4, "MF", "Mail forwarder", "RFC1035", obsolete=True),
    "CNAME": RecordTypeInfo(5, "CNAME", "Canonical name for an alias", "RFC1035"),
    "SOA": RecordTypeInfo(6, "SOA", "Start of authority", "RFC1035"),
    "MB": RecordTypeInfo(7, "MB", "Mailbox domain name", "RFC1035", experimental=True),
    "MG": RecordTypeInfo(8, "MG", "Mail group member", "RFC1035", experimental=True),
    "MR": RecordTypeInfo(9, "MR", "Mail rename domain name", "RFC1035", experimental=True),
    "NULL": RecordTypeInfo(10, "NULL", "Null RR", "RFC1035", experimental=True),
    "WKS": RecordTypeInfo(11, "WKS", "Well known service description", "RFC1035"),
    "PTR": RecordTypeInfo(12, "PTR", "Domain name pointer", "RFC1035"),
    "HINFO": RecordTypeInfo(13, "HINFO", "Host information", "RFC1035"),
    "MINFO": RecordTypeInfo(14, "MINFO", "Mailbox information", "RFC1035"),
    "MX": RecordTypeInfo(15, "MX", "Mail exchange", "RFC1035"),
    "TXT": RecordTypeInfo(16, "TXT", "Text strings", "RFC1035"),
    "RP": RecordTypeInfo(17, "RP", "Responsible person", "RFC1183"),
    "AFSDB": RecordTypeInfo(18, "AFSDB", "AFS database location", "RFC1183"),
    "X25": RecordTypeInfo(19, "X25", "X.25 PSDN address", "RFC1183"),
    "ISDN": RecordTypeInfo(20, "ISDN", "ISDN address", "RFC1183"),
    "RT": RecordTypeInfo(21, "RT", "Route through", "RFC1183"),
    "NSAP": RecordTypeInfo(22, "NSAP", "NSAP address", "RFC1706"),
    "NSAP-PTR": RecordTypeInfo(23, "NSAP-PTR", "NSAP pointer", "RFC1706"),
    "SIG": RecordTypeInfo(24, "SIG", "Security signature", "RFC2535", obsolete=True),
    "KEY": RecordTypeInfo(25, "KEY", "Security key", "RFC2535", obsolete=True),
    "PX": RecordTypeInfo(26, "PX", "X.400 mail mapping information", "RFC2163"),
    "GPOS": RecordTypeInfo(27, "GPOS", "Geographical position", "RFC1712"),
    "AAAA": RecordTypeInfo(28, "AAAA", "IPv6 address", "RFC3596"),
    "LOC": RecordTypeInfo(29, "LOC", "Location information", "RFC1876"),
    "NXT": RecordTypeInfo(30, "NXT", "Next domain", "RFC2535", obsolete=True),
    "EID": RecordTypeInfo(31, "EID", "Endpoint identifier", None),
    "NIMLOC": RecordTypeInfo(32, "NIMLOC", "Nimrod locator", None),
    "SRV": RecordTypeInfo(33, "SRV", "Server selection", "RFC2782"),
    "ATMA": RecordTypeInfo(34, "ATMA", "ATM address", None),
    "NAPTR": RecordTypeInfo(35, "NAPTR", "Naming authority pointer", "RFC3403"),
    "KX": RecordTypeInfo(36, "KX", "Key exchanger", "RFC2230"),
    "CERT": RecordTypeInfo(37, "CERT", "Certificate", "RFC4398"),
    "A6": RecordTypeInfo(38, "A6", "A6", "RFC2874", obsolete=True),
    "DNAME": RecordTypeInfo(39, "DNAME", "DNAME", "RFC6672"),
    "SINK": RecordTypeInfo(40, "SINK", "SINK", None),
    "OPT": RecordTypeInfo(41, "OPT", "OPT pseudo-RR", "RFC6891"),
    "APL": RecordTypeInfo(42, "APL", "APL", "RFC3123"),
    "DS": RecordTypeInfo(43, "DS", "Delegation signer", "RFC4034"),
    "SSHFP": RecordTypeInfo(44, "SSHFP", "SSH key fingerprint", "RFC4255"),
    "IPSECKEY": RecordTypeInfo(45, "IPSECKEY", "IPsec key", "RFC4025"),
    "RRSIG": RecordTypeInfo(46, "RRSIG", "DNSSEC signature", "RFC4034"),
    "NSEC": RecordTypeInfo(47, "NSEC", "Next secure", "RFC4034"),
    "DNSKEY": RecordTypeInfo(48, "DNSKEY", "DNS public key", "RFC4034"),
    "DHCID": RecordTypeInfo(49, "DHCID", "DHCP identifier", "RFC4701"),
    "NSEC3": RecordTypeInfo(50, "NSEC3", "NSEC3", "RFC5155"),
    "NSEC3PARAM": RecordTypeInfo(51, "NSEC3PARAM", "NSEC3 parameters", "RFC5155"),
    "TLSA": RecordTypeInfo(52, "TLSA", "TLSA certificate association", "RFC6698"),
    "SMIMEA": RecordTypeInfo(53, "SMIMEA", "S/MIME certificate association", "RFC8162"),
    "HIP": RecordTypeInfo(55, "HIP", "Host identity protocol", "RFC8005"),
    "NINFO": RecordTypeInfo(56, "NINFO", "NINFO", None),
    "RKEY": RecordTypeInfo(57, "RKEY", "RKEY", None),
    "TALINK": RecordTypeInfo(58, "TALINK", "Trust anchor link", None),
    "CDS": RecordTypeInfo(59, "CDS", "Child DS", "RFC7344"),
    "CDNSKEY": RecordTypeInfo(60, "CDNSKEY", "Child DNSKEY", "RFC7344"),
    "OPENPGPKEY": RecordTypeInfo(61, "OPENPGPKEY", "OpenPGP key", "RFC7929"),
    "CSYNC": RecordTypeInfo(62, "CSYNC", "Child-to-parent synchronization", "RFC7477"),
    "ZONEMD": RecordTypeInfo(63, "ZONEMD", "Message digest for DNS zone", "RFC8976"),
    "SVCB": RecordTypeInfo(64, "SVCB", "Service binding", "RFC9460"),
    "HTTPS": RecordTypeInfo(65, "HTTPS", "HTTPS service binding", "RFC9460"),
    "SPF": RecordTypeInfo(99, "SPF", "SPF record", "RFC7208", obsolete=True),
    "UINFO": RecordTypeInfo(100, "UINFO", "UINFO", None),
    "UID": RecordTypeInfo(101, "UID", "UID", None),
    "GID": RecordTypeInfo(102, "GID", "GID", None),
    "UNSPEC": RecordTypeInfo(103, "UNSPEC", "UNSPEC", None),
    "NID": RecordTypeInfo(104, "NID", "Node identifier", "RFC6742"),
    "L32": RecordTypeInfo(105, "L32", "32-bit locator", "RFC6742"),
    "L64": RecordTypeInfo(106, "L64", "64-bit locator", "RFC6742"),
    "LP": RecordTypeInfo(107, "LP", "Locator pointer", "RFC6742"),
    "EUI48": RecordTypeInfo(108, "EUI48", "EUI-48 address", "RFC7043"),
    "EUI64": RecordTypeInfo(109, "EUI64", "EUI-64 address", "RFC7043"),
    "NXNAME": RecordTypeInfo(128, "NXNAME", "NXNAME", None),
    "TKEY": RecordTypeInfo(249, "TKEY", "Transaction key", "RFC2930"),
    "TSIG": RecordTypeInfo(250, "TSIG", "Transaction signature", "RFC8945"),
    "IXFR": RecordTypeInfo(251, "IXFR", "Incremental zone transfer", "RFC1995"),
    "AXFR": RecordTypeInfo(252, "AXFR", "Authoritative zone transfer", "RFC1035"),
    "MAILB": RecordTypeInfo(253, "MAILB", "Mailbox-related RRs", "RFC1035"),
    "MAILA": RecordTypeInfo(254, "MAILA", "Mail agent RRs", "RFC1035", obsolete=True),
    "ANY": RecordTypeInfo(255, "ANY", "Request for all records", "RFC1035"),
    "URI": RecordTypeInfo(256, "URI", "URI", "RFC7553"),
    "CAA": RecordTypeInfo(257, "CAA", "Certification authority authorization", "RFC8659"),
    "AVC": RecordTypeInfo(258, "AVC", "Application visibility and control", None),
    "DOA": RecordTypeInfo(259, "DOA", "Digital object architecture", None),
    "AMTRELAY": RecordTypeInfo(260, "AMTRELAY", "Automatic multicast tunneling relay", "RFC8777"),
    "RESINFO": RecordTypeInfo(261, "RESINFO", "Resolver information", "RFC9606"),
    "WALLET": RecordTypeInfo(262, "WALLET", "Wallet", None),
    "CLA": RecordTypeInfo(263, "CLA", "CLA", None),
    "IPN": RecordTypeInfo(264, "IPN", "IPN", None),
    "TA": RecordTypeInfo(32768, "TA", "DNSSEC trust authorities", None),
    "DLV": RecordTypeInfo(32769, "DLV", "DNSSEC lookaside validation", "RFC4431"),
}

# Create reverse mapping from type code to name
TYPE_CODE_TO_NAME: dict[int, str] = {
    info.type_code: name for name, info in DNS_RECORD_TYPES.items()
}


def get_type_code(type_name: str) -> int | None:
    """Get the numeric type code for a record type name."""
    info = DNS_RECORD_TYPES.get(type_name.upper())
    return info.type_code if info else None


def get_type_name(type_code: int) -> str | None:
    """Get the record type name for a numeric type code."""
    return TYPE_CODE_TO_NAME.get(type_code)


def is_valid_type(type_name: str) -> bool:
    """Check if a record type name is valid."""
    return type_name.upper() in DNS_RECORD_TYPES


def get_supported_types() -> list[str]:
    """Get list of all supported record type names."""
    return list(DNS_RECORD_TYPES.keys())


# Record types that should not be modified via API (meta/special types)
PROTECTED_TYPES = frozenset(
    {
        "OPT",  # EDNS pseudo-RR
        "TKEY",  # Transaction key
        "TSIG",  # Transaction signature
        "IXFR",  # Incremental transfer
        "AXFR",  # Zone transfer
        "ANY",  # Query type only
        "MAILB",  # Obsolete
        "MAILA",  # Obsolete
    }
)

# Record types that can be updated via DDNS
UPDATABLE_TYPES = frozenset(name for name in DNS_RECORD_TYPES.keys() if name not in PROTECTED_TYPES)
