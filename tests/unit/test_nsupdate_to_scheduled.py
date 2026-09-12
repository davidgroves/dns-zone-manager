"""Unit tests for nsupdate → scheduled change conversion."""

from datetime import UTC, datetime

import pytest
from dns_zone_manager.dns.nsupdate_parser import ParsedUpdate, UpdateAction, UpdateOperation
from dns_zone_manager.dns.nsupdate_to_scheduled import (
    NSUpdateConversionError,
    nsupdate_text_to_creates,
    parsed_update_to_create,
)

FIXED_NOW = datetime(2030, 6, 15, 12, 30, 0, tzinfo=UTC)


class TestGroupAdds:
    """Consecutive identical adds collapse into one AtomicOperation."""

    def test_groups_consecutive_same_rrset(self):
        text = """
zone example.com.
update add lb.example.com. 300 A 192.0.2.10
update add lb.example.com. 300 A 192.0.2.11
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert len(creates) == 1
        assert len(creates[0].operations) == 1
        op = creates[0].operations[0]
        assert op.action == "add"
        assert op.name == "lb.example.com."
        assert op.type == "A"
        assert op.ttl == 300
        assert op.records == ["192.0.2.10", "192.0.2.11"]

    def test_does_not_group_non_consecutive_or_different_keys(self):
        text = """
zone example.com.
update add a.example.com. 300 A 192.0.2.1
update add b.example.com. 300 A 192.0.2.2
update add a.example.com. 300 A 192.0.2.3
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        ops = creates[0].operations
        assert len(ops) == 3
        assert ops[0].records == ["192.0.2.1"]
        assert ops[1].name == "b.example.com."
        assert ops[2].records == ["192.0.2.3"]


class TestPrereqMapping:
    """Prerequisites map 1:1 and control auto_prerequisites."""

    def test_maps_prereqs_and_disables_auto(self):
        text = """
zone example.com.
prereq nxdomain new.example.com.
prereq yxrrset www.example.com. A
update add new.example.com. 3600 A 192.0.2.100
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert len(creates) == 1
        c = creates[0]
        assert c.auto_prerequisites is False
        assert len(c.prerequisites) == 2
        assert c.prerequisites[0].prereq_type == "nxdomain"
        assert c.prerequisites[1].prereq_type == "yxrrset"
        assert c.prerequisites[1].rdtype == "A"

    def test_no_prereqs_enables_auto(self):
        text = """
zone example.com.
update add new.example.com. 3600 A 192.0.2.100
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert creates[0].auto_prerequisites is True
        assert creates[0].prerequisites == []


class TestMultiSend:
    """Each send becomes its own draft."""

    def test_one_create_per_send(self):
        text = """
zone example.com.
update add a.example.com. 300 A 192.0.2.1
send
update add b.example.com. 300 A 192.0.2.2
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert len(creates) == 2
        assert creates[0].operations[0].name == "a.example.com."
        assert creates[1].operations[0].name == "b.example.com."
        assert creates[0].name.endswith("· 1")
        assert creates[1].name.endswith("· 2")
        assert creates[0].scheduled_at is None
        assert "NSUPDATE · example.com · 20300615T123000Z" in creates[0].name


class TestDeleteWithoutType:
    """Delete without type is rejected."""

    def test_rejects_delete_all_at_name(self):
        text = """
zone example.com.
update delete old.example.com.
send
"""
        with pytest.raises(NSUpdateConversionError) as exc:
            nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert "record type" in str(exc.value).lower()

    def test_delete_with_type_ok(self):
        text = """
zone example.com.
update delete old.example.com. A
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        op = creates[0].operations[0]
        assert op.action == "delete"
        assert op.type == "A"
        assert op.records is None

    def test_delete_specific_rdata(self):
        text = """
zone example.com.
update delete old.example.com. A 192.0.2.99
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        op = creates[0].operations[0]
        assert op.records == ["192.0.2.99"]


class TestEmptyAndErrors:
    """Empty / invalid inputs."""

    def test_empty_raises(self):
        with pytest.raises(NSUpdateConversionError) as exc:
            nsupdate_text_to_creates("; just a comment\n", now=FIXED_NOW)
        assert "No update transactions" in str(exc.value)

    def test_prereq_only_raises(self):
        text = """
zone example.com.
prereq nxdomain new.example.com.
send
"""
        with pytest.raises(NSUpdateConversionError) as exc:
            nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert "no update operations" in str(exc.value).lower()

    def test_description_contains_transaction(self):
        text = """
zone example.com.
update add host.example.com. 3600 A 192.0.2.1
send
"""
        creates = nsupdate_text_to_creates(text, now=FIXED_NOW)
        assert "update add host.example.com." in (creates[0].description or "")
        assert "zone example.com." in (creates[0].description or "")


class TestParsedUpdateDirect:
    """Direct conversion helper."""

    def test_custom_name(self):
        parsed = ParsedUpdate(
            zone="example.com.",
            operations=[
                UpdateOperation(
                    action=UpdateAction.ADD,
                    name="www",
                    ttl=300,
                    rdtype="A",
                    data="192.0.2.1",
                )
            ],
        )
        create = parsed_update_to_create(parsed, name="Custom draft name")
        assert create.name == "Custom draft name"
