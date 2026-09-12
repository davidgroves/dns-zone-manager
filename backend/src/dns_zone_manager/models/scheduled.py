"""Pydantic models for scheduled DNS changes."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from dns_zone_manager.models.requests import AtomicOperation

ChangeStatus = Literal[
    "draft",
    "scheduled",
    "running",
    "applied",
    "failed",
    "cancelled",
    "expired",
    "reverted",
]

PrereqTypeLiteral = Literal["nxdomain", "yxdomain", "nxrrset", "yxrrset"]


def _ensure_utc(v: datetime | None) -> datetime | None:
    """Normalize datetimes to timezone-aware UTC."""
    if v is None:
        return None
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v.astimezone(UTC)


class ChangePrerequisite(BaseModel):
    """A DNS UPDATE prerequisite attached to a scheduled change."""

    prereq_type: PrereqTypeLiteral = Field(
        ...,
        description="Prerequisite type: nxdomain, yxdomain, nxrrset, or yxrrset",
    )
    name: str = Field(
        ...,
        description="DNS name (relative to zone or fully qualified)",
    )
    rdtype: str | None = Field(
        default=None,
        description="Record type (required for nxrrset/yxrrset)",
    )
    rdclass: str = Field(
        default="IN",
        description="DNS record class",
    )
    data: str | None = Field(
        default=None,
        description="Rdata value (optional for yxrrset value match)",
    )

    @model_validator(mode="after")
    def validate_prereq_fields(self) -> "ChangePrerequisite":
        """Ensure rdtype is present for RRset prerequisites."""
        if self.prereq_type in ("nxrrset", "yxrrset") and not self.rdtype:
            raise ValueError(f"rdtype is required for {self.prereq_type} prerequisites")
        if self.prereq_type in ("nxdomain", "yxdomain") and self.rdtype:
            raise ValueError(f"rdtype must not be set for {self.prereq_type} prerequisites")
        return self


class ScheduledChangeCreate(BaseModel):
    """Request to create a named scheduled change."""

    name: str = Field(..., min_length=1, max_length=200, description="Human-readable name")
    description: str | None = Field(default=None, max_length=2000, description="Optional notes")
    zone: str = Field(..., description="Target zone name")
    operations: list[AtomicOperation] = Field(
        ...,
        min_length=1,
        description="DNS operations to apply atomically",
    )
    prerequisites: list[ChangePrerequisite] = Field(
        default_factory=list,
        description="Explicit DNS UPDATE prerequisites",
    )
    scheduled_at: datetime | None = Field(
        default=None,
        description="UTC time to apply (null = draft / apply manually)",
    )
    not_valid_after: datetime | None = Field(
        default=None,
        description="UTC expiry; defaults to scheduled_at + default_expiry_window",
    )
    auto_prerequisites: bool = Field(
        default=True,
        description="Derive absent/present prereqs from cache at execution time",
    )

    @field_validator("zone")
    @classmethod
    def normalize_zone(cls, v: str) -> str:
        """Ensure zone ends with a trailing dot."""
        v = v.strip()
        if not v.endswith("."):
            v = v + "."
        return v

    @field_validator("scheduled_at", "not_valid_after")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        """Normalize datetimes to timezone-aware UTC."""
        return _ensure_utc(v)


class ScheduledChangeUpdate(BaseModel):
    """Partial update for a draft/scheduled/failed change."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    operations: list[AtomicOperation] | None = Field(default=None, min_length=1)
    prerequisites: list[ChangePrerequisite] | None = Field(default=None)
    scheduled_at: datetime | None = Field(default=None)
    not_valid_after: datetime | None = Field(default=None)
    auto_prerequisites: bool | None = Field(default=None)

    @field_validator("scheduled_at", "not_valid_after")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        """Normalize datetimes to timezone-aware UTC."""
        return _ensure_utc(v)


class ScheduledOperationResponse(BaseModel):
    """A stored operation, including any pre-apply snapshot used for revert."""

    action: Literal["add", "delete", "replace"]
    name: str
    type: str
    rdclass: str = "IN"
    ttl: int = 3600
    records: list[str] | None = None
    prior_ttl: int | None = None
    prior_records: list[str] | None = None
    snapshot_at: datetime | None = None

    def to_atomic(self) -> AtomicOperation:
        """Return the forward operation without snapshot fields."""
        return AtomicOperation(
            action=self.action,
            name=self.name,
            type=self.type,
            rdclass=self.rdclass,
            ttl=self.ttl,
            records=self.records,
        )


class ScheduledChangeEventResponse(BaseModel):
    """An audit-trail event for a scheduled change."""

    id: int
    change_id: str
    ts: datetime
    event: str
    actor: str | None = None
    detail: dict[str, Any] | None = None


class AuditEventResponse(BaseModel):
    """A scheduled-change audit event with joined change metadata."""

    id: int
    ts: datetime
    event: str
    actor: str | None = None
    detail: dict[str, Any] | None = None
    change_id: str
    change_name: str
    zone: str
    change_status: ChangeStatus


class AuditEventListResponse(BaseModel):
    """Paginated cross-change audit event list."""

    events: list[AuditEventResponse]
    total: int


class ScheduledChangeResponse(BaseModel):
    """Full scheduled change including operations and prerequisites."""

    id: str
    name: str
    description: str | None = None
    zone: str
    status: ChangeStatus
    scheduled_at: datetime | None = None
    not_valid_after: datetime | None = None
    auto_prerequisites: bool
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime
    attempts: int
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    applied_at: datetime | None = None
    result_rcode: str | None = None
    new_serial: int | None = None
    reverted_at: datetime | None = None
    operations: list[ScheduledOperationResponse]
    prerequisites: list[ChangePrerequisite]
    events: list[ScheduledChangeEventResponse] = Field(default_factory=list)


class ScheduledChangeListResponse(BaseModel):
    """Paginated list of scheduled changes."""

    changes: list[ScheduledChangeResponse]
    total: int


class NSUpdateDraftsResponse(BaseModel):
    """Response when nsupdate text is saved as draft scheduled changes."""

    created: list[ScheduledChangeResponse] = Field(
        ...,
        description="Created draft scheduled changes",
    )
    total: int = Field(..., description="Number of drafts created")


class PrerequisitePreviewResult(BaseModel):
    """Result of evaluating one prerequisite against the cache."""

    prereq_type: PrereqTypeLiteral
    name: str
    rdtype: str | None = None
    rdclass: str = "IN"
    data: str | None = None
    passed: bool
    message: str
    source: Literal["explicit", "auto"] = "explicit"


class ConflictWarning(BaseModel):
    """Warning that another draft/scheduled/failed change touches the same RRset."""

    other_change_id: str
    other_change_name: str
    name: str
    type: str
    rdclass: str


class PreviewResponse(BaseModel):
    """Dry-run preview of a scheduled change."""

    change_id: str
    zone: str
    prerequisites: list[PrerequisitePreviewResult]
    all_prerequisites_passed: bool
    conflicts: list[ConflictWarning]
    operations_count: int
    message: str


class ApplyResponse(BaseModel):
    """Result of applying a scheduled change immediately."""

    success: bool
    change_id: str
    zone: str
    status: ChangeStatus
    message: str
    result_rcode: str | None = None
    new_serial: int | None = None


class RevertOperation(BaseModel):
    """An inverse ADD or DELETE that a revert will perform."""

    action: Literal["add", "delete"]
    name: str
    type: str
    rdclass: str = "IN"
    ttl: int = 3600
    records: list[str] | None = None


class RevertPreviewResponse(BaseModel):
    """Preview of the inverse operations a revert would perform."""

    change_id: str
    zone: str
    operations: list[RevertOperation]
    warning: str
    message: str
    can_revert: bool = True


class RevertResponse(BaseModel):
    """Result of reverting an applied scheduled change."""

    success: bool
    change_id: str
    zone: str
    status: ChangeStatus
    message: str
    result_rcode: str | None = None
    new_serial: int | None = None
    operations: list[RevertOperation] = Field(default_factory=list)
