"""Request and response models for DNS API endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AddRRsetRequest(BaseModel):
    """Request to add a new RRset to a zone."""

    name: str = Field(
        ...,
        description="Record name (relative to zone or fully qualified)",
        examples=["www", "mail"],
    )
    ttl: int = Field(
        default=3600,
        ge=0,
        le=2147483647,
        description="Time to live in seconds",
    )
    type: str = Field(
        ...,
        description="DNS record type",
        examples=["A", "AAAA", "MX", "TXT"],
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class",
        examples=["IN", "CH", "HS"],
    )
    records: list[str] = Field(
        ...,
        min_length=1,
        description="Record data values in standard format",
        examples=[["192.0.2.1", "192.0.2.2"]],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "www",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.1", "192.0.2.2"],
                },
                {
                    "name": "mail",
                    "ttl": 3600,
                    "type": "MX",
                    "rdclass": "IN",
                    "records": ["10 mail1.example.com.", "20 mail2.example.com."],
                },
            ]
        }
    }


class DeleteRRsetRequest(BaseModel):
    """Request to delete an RRset or specific records from a zone."""

    name: str = Field(
        ...,
        description="Record name (relative to zone or fully qualified)",
    )
    type: str = Field(
        ...,
        description="DNS record type",
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class",
        examples=["IN", "CH", "HS"],
    )
    records: list[str] | None = Field(
        default=None,
        description="Specific records to delete. If None, deletes entire RRset.",
        examples=[["192.0.2.1"]],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "www",
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.1"],
                },
                {
                    "name": "old-host",
                    "type": "A",
                    "rdclass": "IN",
                    "records": None,
                },
            ]
        }
    }


class ReplaceRRsetRequest(BaseModel):
    """Request to replace an entire RRset in a zone."""

    name: str = Field(
        ...,
        description="Record name (relative to zone or fully qualified)",
    )
    ttl: int = Field(
        default=3600,
        ge=0,
        le=2147483647,
        description="Time to live in seconds",
    )
    type: str = Field(
        ...,
        description="DNS record type",
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class",
        examples=["IN", "CH", "HS"],
    )
    records: list[str] = Field(
        ...,
        min_length=1,
        description="New record data values (replaces all existing)",
        examples=[["192.0.2.10"]],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "www",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.10"],
                }
            ]
        }
    }


class RRsetResponse(BaseModel):
    """Response containing an RRset."""

    name: str = Field(..., description="Fully qualified record name")
    ttl: int = Field(..., description="Time to live in seconds")
    type: str = Field(..., description="DNS record type")
    rdclass: str = Field(default="IN", description="DNS record class")
    records: list[str] = Field(..., description="Record data values")
    name_is_idn: bool = Field(
        default=False,
        description="True if the record name contains punycode (IDN)",
    )
    name_utf8: str | None = Field(
        default=None,
        description="UTF-8 decoded record name (only set if name_is_idn is True)",
    )
    records_is_utf8: bool = Field(
        default=False,
        description="True if any record data contains escaped UTF-8 sequences",
    )
    records_utf8: list[str] | None = Field(
        default=None,
        description="UTF-8 decoded record data (only set if records_is_utf8 is True)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "www.example.com.",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.1", "192.0.2.2"],
                    "name_is_idn": False,
                    "name_utf8": None,
                    "records_is_utf8": False,
                    "records_utf8": None,
                },
                {
                    "name": "xn--0zwm56d.idn.test.",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.10"],
                    "name_is_idn": True,
                    "name_utf8": "测试.idn.test.",
                    "records_is_utf8": False,
                    "records_utf8": None,
                },
            ]
        }
    }


class PaginatedRRsetResponse(BaseModel):
    """Paginated response for RRset listings."""

    rrsets: list[RRsetResponse] = Field(..., description="List of RRsets in this page")
    total_count: int = Field(..., description="Total number of RRsets in the zone")
    page_size: int | None = Field(
        default=None,
        description="Page size used (None means all records returned)",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for next page (name of next record). None if last page.",
    )
    has_more: bool = Field(..., description="Whether there are more records after this page")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "rrsets": [
                        {
                            "name": "host00001.largezone.test.",
                            "ttl": 3600,
                            "type": "A",
                            "rdclass": "IN",
                            "records": ["10.0.0.1"],
                        }
                    ],
                    "total_count": 50000,
                    "page_size": 100,
                    "next_cursor": "host00101.largezone.test.",
                    "has_more": True,
                }
            ]
        }
    }


class ZoneResponse(BaseModel):
    """Response containing zone information."""

    zone: str = Field(..., description="Zone name")
    serial: int = Field(..., description="SOA serial number")
    rrset_count: int = Field(..., description="Number of RRsets in the zone")
    last_refresh: datetime | None = Field(
        default=None,
        description="Last time zone was refreshed from server",
    )
    zone_is_idn: bool = Field(
        default=False,
        description="True if the zone name contains punycode (IDN)",
    )
    zone_utf8: str | None = Field(
        default=None,
        description="UTF-8 decoded zone name (only set if zone_is_idn is True)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "serial": 2024010101,
                    "rrset_count": 42,
                    "last_refresh": "2024-01-15T10:30:00Z",
                    "zone_is_idn": False,
                    "zone_utf8": None,
                },
                {
                    "zone": "xn--0zwm56d.xn--85x722f.",
                    "serial": 2024010101,
                    "rrset_count": 10,
                    "last_refresh": "2024-01-15T10:30:00Z",
                    "zone_is_idn": True,
                    "zone_utf8": "测试.公司.",
                },
            ]
        }
    }


class ZoneListResponse(BaseModel):
    """Response containing list of zones."""

    zones: list[ZoneResponse] = Field(..., description="List of available zones")


class PaginatedZoneResponse(BaseModel):
    """Paginated response for zone listings."""

    zones: list[ZoneResponse] = Field(..., description="List of zones in this page")
    total_count: int = Field(..., description="Total number of zones")
    page_size: int | None = Field(
        default=None,
        description="Page size used (None means all zones returned)",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for next page (zone name). None if last page.",
    )
    has_more: bool = Field(..., description="Whether there are more zones after this page")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zones": [
                        {
                            "zone": "example.com.",
                            "serial": 2024010101,
                            "rrset_count": 42,
                            "last_refresh": "2024-01-15T10:30:00Z",
                        }
                    ],
                    "total_count": 500,
                    "page_size": 50,
                    "next_cursor": "example.org.",
                    "has_more": True,
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: dict | None = Field(default=None, description="Additional error details")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "error": "ConflictError",
                    "message": "DNS state has changed, refresh and retry",
                    "details": {"current_serial": 2024010102},
                }
            ]
        }
    }


class SuccessResponse(BaseModel):
    """Success response for operations."""

    success: bool = Field(default=True)
    message: str = Field(..., description="Success message")
    rrset: RRsetResponse | None = Field(default=None, description="Affected RRset if applicable")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "message": "RRset added successfully",
                    "rrset": {
                        "name": "www.example.com.",
                        "ttl": 3600,
                        "type": "A",
                        "records": ["192.0.2.1"],
                    },
                }
            ]
        }
    }


class ZoneSearchResponse(BaseModel):
    """Response for per-zone search results."""

    zone: str = Field(..., description="Zone name")
    serial: int = Field(..., description="SOA serial number of the zone")
    results: list[RRsetResponse] = Field(..., description="Matching RRsets")
    total_count: int = Field(..., description="Total number of matching RRsets")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "serial": 2024010101,
                    "results": [
                        {
                            "name": "foo.example.com.",
                            "ttl": 3600,
                            "type": "CNAME",
                            "records": ["bar.example.com."],
                        }
                    ],
                    "total_count": 1,
                }
            ]
        }
    }


class ZoneSearchResultItem(BaseModel):
    """Search results for a single zone in global search."""

    zone: str = Field(..., description="Zone name")
    serial: int = Field(..., description="SOA serial number of the zone")
    rrsets: list[RRsetResponse] = Field(..., description="Matching RRsets in this zone")
    zone_is_idn: bool = Field(
        default=False,
        description="True if the zone name contains punycode (IDN)",
    )
    zone_utf8: str | None = Field(
        default=None,
        description="UTF-8 decoded zone name (only set if zone_is_idn is True)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "serial": 2024010101,
                    "rrsets": [
                        {
                            "name": "foo.example.com.",
                            "ttl": 3600,
                            "type": "CNAME",
                            "records": ["bar.example.com."],
                        }
                    ],
                    "zone_is_idn": False,
                    "zone_utf8": None,
                }
            ]
        }
    }


class GlobalSearchResponse(BaseModel):
    """Response for global search across all zones."""

    results: list[ZoneSearchResultItem] = Field(..., description="Search results grouped by zone")
    total_count: int = Field(..., description="Total number of matching RRsets across all zones")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "results": [
                        {
                            "zone": "example.com.",
                            "serial": 2024010101,
                            "rrsets": [
                                {
                                    "name": "foo.example.com.",
                                    "ttl": 3600,
                                    "type": "CNAME",
                                    "records": ["bar.example.com."],
                                }
                            ],
                        },
                        {
                            "zone": "other.com.",
                            "serial": 2024010201,
                            "rrsets": [
                                {
                                    "name": "foo.other.com.",
                                    "ttl": 3600,
                                    "type": "A",
                                    "records": ["192.0.2.1"],
                                }
                            ],
                        },
                    ],
                    "total_count": 2,
                }
            ]
        }
    }


class PaginatedZoneSearchResponse(BaseModel):
    """Paginated response for per-zone search results."""

    zone: str = Field(..., description="Zone name")
    serial: int = Field(..., description="SOA serial number of the zone")
    results: list[RRsetResponse] = Field(..., description="Matching RRsets in this page")
    total_count: int = Field(..., description="Total number of matching RRsets")
    page_size: int | None = Field(
        default=None,
        description="Page size used (None means all results returned)",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for next page (record name). None if last page.",
    )
    has_more: bool = Field(..., description="Whether there are more results after this page")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "serial": 2024010101,
                    "results": [
                        {
                            "name": "foo.example.com.",
                            "ttl": 3600,
                            "type": "CNAME",
                            "records": ["bar.example.com."],
                        }
                    ],
                    "total_count": 100,
                    "page_size": 50,
                    "next_cursor": "bar.example.com.",
                    "has_more": True,
                }
            ]
        }
    }


class PaginatedGlobalSearchResponse(BaseModel):
    """Paginated response for global search across all zones."""

    results: list[ZoneSearchResultItem] = Field(
        ..., description="Search results grouped by zone (current page)"
    )
    total_count: int = Field(..., description="Total number of matching RRsets across all zones")
    page_size: int | None = Field(
        default=None,
        description="Page size used (None means all results returned)",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for next page. None if last page.",
    )
    has_more: bool = Field(..., description="Whether there are more results after this page")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "results": [
                        {
                            "zone": "example.com.",
                            "serial": 2024010101,
                            "rrsets": [
                                {
                                    "name": "foo.example.com.",
                                    "ttl": 3600,
                                    "type": "CNAME",
                                    "records": ["bar.example.com."],
                                }
                            ],
                        }
                    ],
                    "total_count": 500,
                    "page_size": 50,
                    "next_cursor": "gamma.test.:host001.gamma.test.",
                    "has_more": True,
                }
            ]
        }
    }


# NSUPDATE Models


class NSUpdateOperation(BaseModel):
    """A single operation parsed from nsupdate input."""

    action: str = Field(
        ...,
        description="Operation type: add, delete, prereq_nxdomain, prereq_yxdomain, "
        "prereq_nxrrset, prereq_yxrrset",
    )
    name: str = Field(..., description="Record name")
    ttl: int | None = Field(default=None, description="TTL (for add operations)")
    rdtype: str | None = Field(default=None, description="Record type")
    data: str | None = Field(default=None, description="Record data")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "action": "add",
                    "name": "www.example.com.",
                    "ttl": 3600,
                    "rdtype": "A",
                    "data": "192.0.2.1",
                },
                {"action": "delete", "name": "old.example.com.", "rdtype": "A", "data": None},
                {
                    "action": "prereq_nxdomain",
                    "name": "new.example.com.",
                    "rdtype": None,
                    "data": None,
                },
            ]
        }
    }


class NSUpdateTransactionResult(BaseModel):
    """Result of a single nsupdate transaction (one 'send' block)."""

    zone: str = Field(..., description="Zone name")
    operations: list[NSUpdateOperation] = Field(..., description="Operations in this transaction")
    success: bool = Field(..., description="Whether the transaction succeeded")
    message: str = Field(..., description="Result message or error description")
    rcode: str | None = Field(
        default=None,
        description="DNS rcode if operation failed (e.g., YXRRSET, NXDOMAIN)",
    )
    rcode_description: str | None = Field(
        default=None,
        description="User-friendly description of the rcode",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "operations": [
                        {
                            "action": "prereq_nxdomain",
                            "name": "newhost.example.com.",
                            "ttl": None,
                            "rdtype": None,
                            "data": None,
                        },
                        {
                            "action": "add",
                            "name": "newhost.example.com.",
                            "ttl": 3600,
                            "rdtype": "A",
                            "data": "192.0.2.100",
                        },
                    ],
                    "success": True,
                    "message": "Update successful",
                }
            ]
        }
    }


class NSUpdateResponse(BaseModel):
    """Response for nsupdate endpoint."""

    transactions: list[NSUpdateTransactionResult] = Field(
        ..., description="Results for each transaction"
    )
    total_success: int = Field(..., description="Number of successful transactions")
    total_failed: int = Field(..., description="Number of failed transactions")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "transactions": [
                        {
                            "zone": "example.com.",
                            "operations": [
                                {
                                    "action": "add",
                                    "name": "www.example.com.",
                                    "ttl": 3600,
                                    "rdtype": "A",
                                    "data": "192.0.2.1",
                                },
                            ],
                            "success": True,
                            "message": "Update successful",
                        }
                    ],
                    "total_success": 1,
                    "total_failed": 0,
                }
            ]
        }
    }


# Atomic Update Models


class AtomicOperation(BaseModel):
    """A single operation in an atomic update request."""

    action: Literal["add", "delete", "replace"] = Field(
        ...,
        description="Operation type: add, delete, or replace",
    )
    name: str = Field(
        ...,
        description="Record name (relative to zone or fully qualified)",
        examples=["www", "mail"],
    )
    type: str = Field(
        ...,
        description="DNS record type",
        examples=["A", "AAAA", "MX", "TXT"],
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class",
        examples=["IN", "CH", "HS"],
    )
    ttl: int = Field(
        default=3600,
        ge=0,
        le=2147483647,
        description="Time to live in seconds (used for add/replace)",
    )
    records: list[str] | None = Field(
        default=None,
        description="Record data values. Required for add/replace, optional for delete.",
        examples=[["192.0.2.1", "192.0.2.2"]],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "action": "add",
                    "name": "www",
                    "type": "A",
                    "rdclass": "IN",
                    "ttl": 3600,
                    "records": ["192.0.2.1"],
                },
                {
                    "action": "delete",
                    "name": "old",
                    "type": "A",
                    "rdclass": "IN",
                    "records": None,
                },
                {
                    "action": "replace",
                    "name": "mail",
                    "type": "MX",
                    "rdclass": "IN",
                    "ttl": 3600,
                    "records": ["10 mail.example.com."],
                },
            ]
        }
    }


class AtomicUpdateRequest(BaseModel):
    """Request to perform multiple operations atomically on a zone."""

    operations: list[AtomicOperation] = Field(
        ...,
        min_length=1,
        description="List of operations to perform atomically",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "operations": [
                        {
                            "action": "add",
                            "name": "www",
                            "type": "A",
                            "ttl": 3600,
                            "records": ["192.0.2.1"],
                        },
                        {
                            "action": "delete",
                            "name": "old",
                            "type": "A",
                        },
                        {
                            "action": "replace",
                            "name": "mail",
                            "type": "MX",
                            "ttl": 3600,
                            "records": ["10 mail.example.com."],
                        },
                    ]
                }
            ]
        }
    }


class AtomicUpdateResponse(BaseModel):
    """Response for atomic update operations."""

    success: bool = Field(..., description="Whether all operations succeeded")
    zone: str = Field(..., description="Zone name")
    operations_count: int = Field(..., description="Number of operations executed")
    message: str = Field(..., description="Result message")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "zone": "example.com.",
                    "operations_count": 3,
                    "message": "All operations completed successfully",
                },
                {
                    "success": False,
                    "zone": "example.com.",
                    "operations_count": 3,
                    "message": "DNS update failed: prerequisite not satisfied",
                },
            ]
        }
    }


# Zone History Models


class HistoryChangeResponse(BaseModel):
    """A single record change in zone history."""

    action: Literal["add", "delete"] = Field(
        ...,
        description="Type of change: 'add' for new records, 'delete' for removed records",
    )
    name: str = Field(..., description="Fully qualified record name")
    ttl: int = Field(..., description="Time to live in seconds")
    type: str = Field(..., description="DNS record type")
    rdclass: str = Field(default="IN", description="DNS record class")
    records: list[str] = Field(..., description="Record data values")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "action": "add",
                    "name": "www.example.com.",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.1"],
                },
                {
                    "action": "delete",
                    "name": "old.example.com.",
                    "ttl": 3600,
                    "type": "A",
                    "rdclass": "IN",
                    "records": ["192.0.2.99"],
                },
            ]
        }
    }


class HistoryBatchResponse(BaseModel):
    """A batch of changes between two serial numbers."""

    from_serial: int = Field(..., description="Serial number before the changes")
    to_serial: int = Field(..., description="Serial number after the changes")
    changes: list[HistoryChangeResponse] = Field(
        ..., description="List of record changes in this batch"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "from_serial": 2024010101,
                    "to_serial": 2024010102,
                    "changes": [
                        {
                            "action": "delete",
                            "name": "www.example.com.",
                            "ttl": 3600,
                            "type": "A",
                            "rdclass": "IN",
                            "records": ["192.0.2.1"],
                        },
                        {
                            "action": "add",
                            "name": "www.example.com.",
                            "ttl": 3600,
                            "type": "A",
                            "rdclass": "IN",
                            "records": ["192.0.2.10"],
                        },
                    ],
                }
            ]
        }
    }


class ZoneHistoryResponse(BaseModel):
    """Response containing zone change history."""

    zone: str = Field(..., description="Zone name")
    current_serial: int = Field(..., description="Current SOA serial number")
    history: list[HistoryBatchResponse] = Field(
        ..., description="List of change batches, oldest first"
    )
    available_from_serial: int = Field(
        ..., description="Oldest serial number available in the history"
    )
    is_full_axfr: bool = Field(
        default=False,
        description="True if server responded with full AXFR instead of incremental IXFR",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "current_serial": 2024010103,
                    "history": [
                        {
                            "from_serial": 2024010101,
                            "to_serial": 2024010102,
                            "changes": [
                                {
                                    "action": "add",
                                    "name": "www.example.com.",
                                    "ttl": 3600,
                                    "type": "A",
                                    "rdclass": "IN",
                                    "records": ["192.0.2.1"],
                                }
                            ],
                        },
                        {
                            "from_serial": 2024010102,
                            "to_serial": 2024010103,
                            "changes": [
                                {
                                    "action": "add",
                                    "name": "mail.example.com.",
                                    "ttl": 3600,
                                    "type": "A",
                                    "rdclass": "IN",
                                    "records": ["192.0.2.2"],
                                }
                            ],
                        },
                    ],
                    "available_from_serial": 2024010101,
                    "is_full_axfr": False,
                }
            ]
        }
    }


class RollbackRequest(BaseModel):
    """Request to rollback a zone to a specific serial."""

    target_serial: int = Field(
        ...,
        description="Serial number to rollback to",
        ge=1,
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "target_serial": 2024010101,
                }
            ]
        }
    }


class RollbackPreviewResponse(BaseModel):
    """Preview of changes that would be applied by a rollback."""

    zone: str = Field(..., description="Zone name")
    current_serial: int = Field(..., description="Current SOA serial number")
    target_serial: int = Field(..., description="Target serial number for rollback")
    changes: list[HistoryChangeResponse] = Field(
        ..., description="Changes that would be applied (reversed from history)"
    )
    change_count: int = Field(..., description="Number of changes to apply")
    can_rollback: bool = Field(..., description="Whether rollback is possible (history available)")
    warning: str | None = Field(
        default=None,
        description="Warning message if rollback has caveats",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "zone": "example.com.",
                    "current_serial": 2024010103,
                    "target_serial": 2024010101,
                    "changes": [
                        {
                            "action": "delete",
                            "name": "mail.example.com.",
                            "ttl": 3600,
                            "type": "A",
                            "rdclass": "IN",
                            "records": ["192.0.2.2"],
                        },
                        {
                            "action": "delete",
                            "name": "www.example.com.",
                            "ttl": 3600,
                            "type": "A",
                            "rdclass": "IN",
                            "records": ["192.0.2.1"],
                        },
                    ],
                    "change_count": 2,
                    "can_rollback": True,
                    "warning": None,
                }
            ]
        }
    }


class RollbackResponse(BaseModel):
    """Response after executing a rollback."""

    success: bool = Field(..., description="Whether the rollback succeeded")
    zone: str = Field(..., description="Zone name")
    from_serial: int = Field(..., description="Serial before rollback")
    to_serial: int = Field(..., description="Target serial (rolled back to)")
    new_serial: int = Field(..., description="New serial after rollback operation")
    changes_applied: int = Field(..., description="Number of changes applied")
    message: str = Field(..., description="Result message")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "zone": "example.com.",
                    "from_serial": 2024010103,
                    "to_serial": 2024010101,
                    "new_serial": 2024010104,
                    "changes_applied": 2,
                    "message": "Successfully rolled back zone to serial 2024010101",
                },
                {
                    "success": False,
                    "zone": "example.com.",
                    "from_serial": 2024010103,
                    "to_serial": 2024010101,
                    "new_serial": 2024010103,
                    "changes_applied": 0,
                    "message": "Rollback failed: prerequisite not satisfied - zone was modified",
                },
            ]
        }
    }
