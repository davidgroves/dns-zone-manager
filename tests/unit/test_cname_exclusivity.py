"""Unit tests for CNAME exclusivity helpers."""

from dns_zone_manager.dns.cname_exclusivity import (
    OwnerTypeOp,
    conflicting_types_for_add,
    format_cname_conflict_message,
    ops_delete_cname_or_all,
    simulate_cname_exclusivity,
)


class TestConflictingTypesForAdd:
    def test_cname_onto_a(self):
        assert conflicting_types_for_add({"A"}, "CNAME") == ["A"]

    def test_cname_onto_a_and_txt(self):
        assert conflicting_types_for_add({"TXT", "A"}, "CNAME") == ["A", "TXT"]

    def test_a_onto_cname(self):
        assert conflicting_types_for_add({"CNAME"}, "A") == ["CNAME"]

    def test_a_onto_txt_ok(self):
        assert conflicting_types_for_add({"TXT"}, "A") == []

    def test_cname_ignores_dnssec_companions(self):
        assert conflicting_types_for_add({"RRSIG", "NSEC"}, "CNAME") == []

    def test_cname_with_a_and_rrsig(self):
        assert conflicting_types_for_add({"A", "RRSIG"}, "CNAME") == ["A"]

    def test_same_type_cname_not_listed(self):
        # Same-type duplicate is RRSET_EXISTS, not CNAME exclusivity.
        assert conflicting_types_for_add({"CNAME"}, "CNAME") == []


class TestSimulate:
    def test_cname_onto_existing_a(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": {"A"}},
            [OwnerTypeOp("add", "host.example.com.", "CNAME", index=0)],
        )
        assert err is not None
        assert err.conflicting_types == ["A"]
        assert "CNAME" in err.message

    def test_a_onto_existing_cname(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": {"CNAME"}},
            [OwnerTypeOp("add", "host.example.com.", "A", index=0)],
        )
        assert err is not None
        assert err.conflicting_types == ["CNAME"]

    def test_delete_then_add_cname_ok(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": {"A"}},
            [
                OwnerTypeOp("delete", "host.example.com.", "A", index=0),
                OwnerTypeOp("add", "host.example.com.", "CNAME", index=1),
            ],
        )
        assert err is None

    def test_delete_all_then_add_cname_ok(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": {"A", "TXT"}},
            [
                OwnerTypeOp("delete", "host.example.com.", None, index=0),
                OwnerTypeOp("add", "host.example.com.", "CNAME", index=1),
            ],
        )
        assert err is None

    def test_add_a_and_cname_same_txn(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": set()},
            [
                OwnerTypeOp("add", "host.example.com.", "A", index=0),
                OwnerTypeOp("add", "host.example.com.", "CNAME", index=1),
            ],
        )
        assert err is not None
        assert err.index == 1

    def test_add_cname_then_a_same_txn(self):
        err = simulate_cname_exclusivity(
            {"host.example.com.": set()},
            [
                OwnerTypeOp("add", "host.example.com.", "CNAME", index=0),
                OwnerTypeOp("add", "host.example.com.", "A", index=1),
            ],
        )
        assert err is not None
        assert err.index == 1

    def test_apex_soa_blocks_cname(self):
        err = simulate_cname_exclusivity(
            {"example.com.": {"SOA", "NS"}},
            [OwnerTypeOp("add", "example.com.", "CNAME", index=0)],
        )
        assert err is not None
        assert "SOA" in err.conflicting_types
        assert "NS" in err.conflicting_types


class TestOpsDeleteCnameOrAll:
    def test_delete_cname(self):
        ops = [OwnerTypeOp("delete", "h.example.com.", "CNAME")]
        assert ops_delete_cname_or_all(ops, "h.example.com.") is True

    def test_delete_all(self):
        ops = [OwnerTypeOp("delete", "h.example.com.", None)]
        assert ops_delete_cname_or_all(ops, "h.example.com.") is True

    def test_delete_a_only(self):
        ops = [OwnerTypeOp("delete", "h.example.com.", "A")]
        assert ops_delete_cname_or_all(ops, "h.example.com.") is False


def test_format_message_cname():
    msg = format_cname_conflict_message("h.example.com.", "CNAME", ["A", "TXT"])
    assert "A, TXT" in msg
    assert "CNAME" in msg


def test_format_message_other():
    msg = format_cname_conflict_message("h.example.com.", "A", ["CNAME"], index=2)
    assert msg.startswith("Operation 2:")
    assert "CNAME" in msg
