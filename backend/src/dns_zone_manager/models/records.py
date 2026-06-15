"""Pydantic models for DNS records."""

from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from dns_zone_manager.dns.types import UPDATABLE_TYPES, is_valid_type


class RRsetData(BaseModel):
    """Base model for an RRset (Resource Record Set)."""

    name: str = Field(
        ...,
        description="Record name (relative to zone or fully qualified)",
        examples=["www", "mail.example.com."],
    )
    ttl: int = Field(
        default=3600,
        ge=0,
        le=2147483647,
        description="Time to live in seconds",
    )
    record_type: str = Field(
        ...,
        alias="type",
        description="DNS record type (A, AAAA, MX, etc.)",
        examples=["A", "AAAA", "MX", "TXT"],
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class (IN, CH, HS)",
        examples=["IN", "CH", "HS"],
    )
    records: list[str] = Field(
        ...,
        min_length=1,
        description="Record data values",
        examples=[["192.0.2.1"], ["10 mail.example.com."]],
    )

    model_config = {"populate_by_name": True}

    @field_validator("record_type")
    @classmethod
    def validate_record_type(cls, v: str) -> str:
        """Validate that the record type is supported and updatable."""
        v = v.upper()
        if not is_valid_type(v):
            raise ValueError(f"Unknown record type: {v}")
        if v not in UPDATABLE_TYPES:
            raise ValueError(f"Record type {v} cannot be modified via API")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate and normalize the record name."""
        v = v.strip()
        if not v:
            raise ValueError("Record name cannot be empty")
        # Basic validation - more thorough validation happens in DNS client
        if len(v) > 253:
            raise ValueError("Record name too long (max 253 characters)")
        return v


# Type-specific record models for common types


class ARecord(BaseModel):
    """IPv4 address record."""

    address: IPv4Address

    def to_string(self) -> str:
        return str(self.address)


class AAAARecord(BaseModel):
    """IPv6 address record."""

    address: IPv6Address

    def to_string(self) -> str:
        return str(self.address)


class MXRecord(BaseModel):
    """Mail exchange record."""

    priority: Annotated[int, Field(ge=0, le=65535)]
    exchange: str

    def to_string(self) -> str:
        return f"{self.priority} {self.exchange}"


class SRVRecord(BaseModel):
    """Service record."""

    priority: Annotated[int, Field(ge=0, le=65535)]
    weight: Annotated[int, Field(ge=0, le=65535)]
    port: Annotated[int, Field(ge=0, le=65535)]
    target: str

    def to_string(self) -> str:
        return f"{self.priority} {self.weight} {self.port} {self.target}"


class TXTRecord(BaseModel):
    """Text record."""

    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        """Validate TXT record content."""
        # TXT records can contain any data, but individual strings are limited to 255 chars
        # The DNS library handles splitting long strings
        return v

    def to_string(self) -> str:
        # Quote the text if it contains spaces and isn't already quoted
        if " " in self.text and not (self.text.startswith('"') and self.text.endswith('"')):
            return f'"{self.text}"'
        return self.text


class CAARecord(BaseModel):
    """Certification Authority Authorization record."""

    flags: Annotated[int, Field(ge=0, le=255)]
    tag: Literal["issue", "issuewild", "iodef", "contactemail", "contactphone"]
    value: str

    def to_string(self) -> str:
        return f'{self.flags} {self.tag} "{self.value}"'


class SOARecord(BaseModel):
    """Start of Authority record."""

    mname: str  # Primary nameserver
    rname: str  # Responsible person email (with @ replaced by .)
    serial: Annotated[int, Field(ge=0, le=4294967295)]
    refresh: Annotated[int, Field(ge=0)]
    retry: Annotated[int, Field(ge=0)]
    expire: Annotated[int, Field(ge=0)]
    minimum: Annotated[int, Field(ge=0)]

    def to_string(self) -> str:
        return (
            f"{self.mname} {self.rname} {self.serial} "
            f"{self.refresh} {self.retry} {self.expire} {self.minimum}"
        )


class NSRecord(BaseModel):
    """Nameserver record."""

    nsdname: str

    def to_string(self) -> str:
        return self.nsdname


class CNAMERecord(BaseModel):
    """Canonical name record."""

    cname: str

    def to_string(self) -> str:
        return self.cname


class PTRRecord(BaseModel):
    """Pointer record."""

    ptrdname: str

    def to_string(self) -> str:
        return self.ptrdname


class SSHFPRecord(BaseModel):
    """SSH fingerprint record."""

    algorithm: Annotated[int, Field(ge=0, le=255)]  # 1=RSA, 2=DSA, 3=ECDSA, 4=Ed25519
    fp_type: Annotated[int, Field(ge=0, le=255)]  # 1=SHA-1, 2=SHA-256
    fingerprint: str

    def to_string(self) -> str:
        return f"{self.algorithm} {self.fp_type} {self.fingerprint}"


class TLSARecord(BaseModel):
    """TLSA certificate association record."""

    usage: Annotated[int, Field(ge=0, le=255)]  # Certificate usage
    selector: Annotated[int, Field(ge=0, le=255)]  # Selector
    matching_type: Annotated[int, Field(ge=0, le=255)]  # Matching type
    certificate_data: str  # Hex-encoded certificate data

    def to_string(self) -> str:
        return f"{self.usage} {self.selector} {self.matching_type} {self.certificate_data}"


class DSRecord(BaseModel):
    """Delegation Signer record."""

    key_tag: Annotated[int, Field(ge=0, le=65535)]
    algorithm: Annotated[int, Field(ge=0, le=255)]
    digest_type: Annotated[int, Field(ge=0, le=255)]
    digest: str

    def to_string(self) -> str:
        return f"{self.key_tag} {self.algorithm} {self.digest_type} {self.digest}"


class DNSKEYRecord(BaseModel):
    """DNSKEY record."""

    flags: Annotated[int, Field(ge=0, le=65535)]
    protocol: Annotated[int, Field(ge=0, le=255)]
    algorithm: Annotated[int, Field(ge=0, le=255)]
    public_key: str  # Base64-encoded

    def to_string(self) -> str:
        return f"{self.flags} {self.protocol} {self.algorithm} {self.public_key}"


class NAPTRRecord(BaseModel):
    """Naming Authority Pointer record."""

    order: Annotated[int, Field(ge=0, le=65535)]
    preference: Annotated[int, Field(ge=0, le=65535)]
    flags: str
    service: str
    regexp: str
    replacement: str

    def to_string(self) -> str:
        return (
            f'{self.order} {self.preference} "{self.flags}" '
            f'"{self.service}" "{self.regexp}" {self.replacement}'
        )


class LOCRecord(BaseModel):
    """Location record."""

    # Simplified - accepts the full LOC string format
    location: str

    def to_string(self) -> str:
        return self.location


class HTTPSRecord(BaseModel):
    """HTTPS service binding record."""

    priority: Annotated[int, Field(ge=0, le=65535)]
    target: str
    params: str = ""  # SvcParams

    def to_string(self) -> str:
        if self.params:
            return f"{self.priority} {self.target} {self.params}"
        return f"{self.priority} {self.target}"


class SVCBRecord(BaseModel):
    """Service binding record."""

    priority: Annotated[int, Field(ge=0, le=65535)]
    target: str
    params: str = ""

    def to_string(self) -> str:
        if self.params:
            return f"{self.priority} {self.target} {self.params}"
        return f"{self.priority} {self.target}"


# Mapping of record types to their model classes
RECORD_TYPE_MODELS: dict[str, type[BaseModel]] = {
    "A": ARecord,
    "AAAA": AAAARecord,
    "MX": MXRecord,
    "SRV": SRVRecord,
    "TXT": TXTRecord,
    "CAA": CAARecord,
    "SOA": SOARecord,
    "NS": NSRecord,
    "CNAME": CNAMERecord,
    "PTR": PTRRecord,
    "SSHFP": SSHFPRecord,
    "TLSA": TLSARecord,
    "DS": DSRecord,
    "DNSKEY": DNSKEYRecord,
    "NAPTR": NAPTRRecord,
    "LOC": LOCRecord,
    "HTTPS": HTTPSRecord,
    "SVCB": SVCBRecord,
}


def parse_record_data(record_type: str, data: str | dict[str, Any]) -> str:
    """Parse and validate record data, returning the string representation.

    Args:
        record_type: The DNS record type (e.g., "A", "MX")
        data: Either a string in standard format or a dict with structured fields

    Returns:
        String representation suitable for DNS wire format
    """
    record_type = record_type.upper()

    # If already a string, return as-is (validation happens in dnspython)
    if isinstance(data, str):
        return data

    # If we have a model for this type, use it for validation
    model_class = RECORD_TYPE_MODELS.get(record_type)
    if model_class:
        record = model_class.model_validate(data)
        return record.to_string()  # type: ignore[attr-defined]

    # For unknown types, require string format
    raise ValueError(f"Structured data not supported for type {record_type}, use string format")
