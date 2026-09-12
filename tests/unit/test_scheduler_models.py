"""Unit tests for scheduled change Pydantic models."""

from datetime import UTC, datetime, timedelta

import pytest
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import (
    ChangePrerequisite,
    ScheduledChangeCreate,
    ScheduledChangeUpdate,
)
from pydantic import ValidationError


class TestChangePrerequisite:
    def test_nxrrset_requires_rdtype(self):
        with pytest.raises(ValidationError, match="rdtype is required"):
            ChangePrerequisite(prereq_type="nxrrset", name="www")

    def test_nxdomain_rejects_rdtype(self):
        with pytest.raises(ValidationError, match="rdtype must not be set"):
            ChangePrerequisite(prereq_type="nxdomain", name="www", rdtype="A")

    def test_yxrrset_valid(self):
        p = ChangePrerequisite(
            prereq_type="yxrrset",
            name="www",
            rdtype="A",
            data="192.0.2.1",
        )
        assert p.prereq_type == "yxrrset"
        assert p.rdtype == "A"


class TestScheduledChangeCreate:
    def test_normalizes_zone_trailing_dot(self):
        c = ScheduledChangeCreate(
            name="Add www",
            zone="example.com",
            operations=[
                AtomicOperation(
                    action="add",
                    name="www",
                    type="A",
                    records=["192.0.2.1"],
                )
            ],
        )
        assert c.zone == "example.com."

    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2030, 1, 1, 12, 0, 0)
        c = ScheduledChangeCreate(
            name="Timed",
            zone="example.com.",
            operations=[
                AtomicOperation(
                    action="add",
                    name="www",
                    type="A",
                    records=["192.0.2.1"],
                )
            ],
            scheduled_at=naive,
        )
        assert c.scheduled_at is not None
        assert c.scheduled_at.tzinfo is not None
        assert c.scheduled_at == naive.replace(tzinfo=UTC)

    def test_aware_datetime_converted_to_utc(self):
        from datetime import timezone

        eastern = timezone(timedelta(hours=-5))
        aware = datetime(2030, 1, 1, 7, 0, 0, tzinfo=eastern)
        c = ScheduledChangeCreate(
            name="Timed",
            zone="example.com.",
            operations=[
                AtomicOperation(
                    action="add",
                    name="www",
                    type="A",
                    records=["192.0.2.1"],
                )
            ],
            scheduled_at=aware,
        )
        assert c.scheduled_at == datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_requires_operations(self):
        with pytest.raises(ValidationError):
            ScheduledChangeCreate(name="Empty", zone="example.com.", operations=[])


class TestScheduledChangeUpdate:
    def test_partial_update_allows_none_fields(self):
        u = ScheduledChangeUpdate(name="Renamed")
        assert u.name == "Renamed"
        assert u.operations is None
        assert u.scheduled_at is None
