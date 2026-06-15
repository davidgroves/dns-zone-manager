"""Unit tests for the nsupdate parser."""

import pytest
from dns_zone_manager.dns.nsupdate_parser import (
    NSUpdateParseError,
    PrereqType,
    UpdateAction,
    parse,
)


class TestParseBasic:
    """Test basic parsing functionality."""

    def test_parse_empty_string(self):
        """Empty input returns empty list."""
        result = parse("")
        assert result == []

    def test_parse_whitespace_only(self):
        """Whitespace-only input returns empty list."""
        result = parse("   \n\n   \n")
        assert result == []

    def test_parse_comments_only(self):
        """Comment-only input returns empty list."""
        result = parse("; this is a comment\n# another comment\n")
        assert result == []

    def test_parse_requires_zone(self):
        """Parsing without zone and no default raises error."""
        text = "update add test 300 A 1.2.3.4\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "No zone specified" in str(exc_info.value)

    def test_parse_with_default_zone(self):
        """Parsing with default_zone works."""
        text = "update add test 300 A 1.2.3.4\nsend"
        result = parse(text, default_zone="example.com")
        assert len(result) == 1
        assert result[0].zone == "example.com."

    def test_parse_zone_command(self):
        """Zone command sets the zone."""
        text = "zone example.com\nupdate add test 300 A 1.2.3.4\nsend"
        result = parse(text)
        assert len(result) == 1
        assert result[0].zone == "example.com."

    def test_zone_command_overrides_default(self):
        """Zone command overrides default_zone."""
        text = "zone other.com\nupdate add test 300 A 1.2.3.4\nsend"
        result = parse(text, default_zone="example.com")
        assert len(result) == 1
        assert result[0].zone == "other.com."


class TestParsePrerequisites:
    """Test prerequisite parsing."""

    def test_prereq_nxdomain(self):
        """Parse prereq nxdomain."""
        text = "zone test.com\nprereq nxdomain foo.test.com.\nsend"
        result = parse(text)
        assert len(result) == 1
        assert len(result[0].prerequisites) == 1
        prereq = result[0].prerequisites[0]
        assert prereq.prereq_type == PrereqType.NXDOMAIN
        assert prereq.name == "foo.test.com."

    def test_prereq_yxdomain(self):
        """Parse prereq yxdomain."""
        text = "zone test.com\nprereq yxdomain foo.test.com.\nsend"
        result = parse(text)
        prereq = result[0].prerequisites[0]
        assert prereq.prereq_type == PrereqType.YXDOMAIN
        assert prereq.name == "foo.test.com."

    def test_prereq_nxrrset(self):
        """Parse prereq nxrrset."""
        text = "zone test.com\nprereq nxrrset foo.test.com. A\nsend"
        result = parse(text)
        prereq = result[0].prerequisites[0]
        assert prereq.prereq_type == PrereqType.NXRRSET
        assert prereq.name == "foo.test.com."
        assert prereq.rdtype == "A"

    def test_prereq_nxrrset_with_class(self):
        """Parse prereq nxrrset with explicit class."""
        text = "zone test.com\nprereq nxrrset foo.test.com. IN A\nsend"
        result = parse(text)
        prereq = result[0].prerequisites[0]
        assert prereq.rdclass == "IN"
        assert prereq.rdtype == "A"

    def test_prereq_yxrrset(self):
        """Parse prereq yxrrset."""
        text = "zone test.com\nprereq yxrrset foo.test.com. A\nsend"
        result = parse(text)
        prereq = result[0].prerequisites[0]
        assert prereq.prereq_type == PrereqType.YXRRSET
        assert prereq.rdtype == "A"
        assert prereq.data is None

    def test_prereq_yxrrset_with_data(self):
        """Parse prereq yxrrset with specific data."""
        text = "zone test.com\nprereq yxrrset foo.test.com. A 1.2.3.4\nsend"
        result = parse(text)
        prereq = result[0].prerequisites[0]
        assert prereq.prereq_type == PrereqType.YXRRSET
        assert prereq.rdtype == "A"
        assert prereq.data == "1.2.3.4"

    def test_prereq_invalid_type(self):
        """Invalid prereq type raises error."""
        text = "zone test.com\nprereq invalid foo.test.com.\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "Unknown prereq type" in str(exc_info.value)


class TestParseUpdateAdd:
    """Test update add parsing."""

    def test_update_add_a_record(self):
        """Parse update add for A record."""
        text = "zone test.com\nupdate add www.test.com. 3600 A 192.0.2.1\nsend"
        result = parse(text)
        assert len(result[0].operations) == 1
        op = result[0].operations[0]
        assert op.action == UpdateAction.ADD
        assert op.name == "www.test.com."
        assert op.ttl == 3600
        assert op.rdtype == "A"
        assert op.data == "192.0.2.1"

    def test_update_add_with_class(self):
        """Parse update add with explicit class."""
        text = "zone test.com\nupdate add www.test.com. 3600 IN A 192.0.2.1\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdclass == "IN"
        assert op.rdtype == "A"

    def test_update_add_mx_record(self):
        """Parse update add for MX record."""
        text = "zone test.com\nupdate add test.com. 3600 MX 10 mail.test.com.\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdtype == "MX"
        assert op.data == "10 mail.test.com."

    def test_update_add_txt_record(self):
        """Parse update add for TXT record with quoted string."""
        text = 'zone test.com\nupdate add test.com. 3600 TXT "v=spf1 -all"\nsend'
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdtype == "TXT"
        assert op.data == "v=spf1 -all"

    def test_update_add_missing_data(self):
        """Update add without data raises error."""
        text = "zone test.com\nupdate add www.test.com. 3600 A\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "name ttl" in str(exc_info.value) or "type data" in str(exc_info.value)


class TestParseUpdateDelete:
    """Test update delete parsing."""

    def test_update_delete_name_only(self):
        """Parse update delete with name only."""
        text = "zone test.com\nupdate delete www.test.com.\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.action == UpdateAction.DELETE
        assert op.name == "www.test.com."
        assert op.rdtype is None
        assert op.data is None

    def test_update_delete_rrset(self):
        """Parse update delete for specific RRset."""
        text = "zone test.com\nupdate delete www.test.com. A\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdtype == "A"
        assert op.data is None

    def test_update_delete_specific_record(self):
        """Parse update delete for specific record."""
        text = "zone test.com\nupdate delete www.test.com. A 192.0.2.1\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdtype == "A"
        assert op.data == "192.0.2.1"

    def test_update_delete_with_ttl(self):
        """TTL in delete is ignored."""
        text = "zone test.com\nupdate delete www.test.com. 3600 A\nsend"
        result = parse(text)
        op = result[0].operations[0]
        assert op.rdtype == "A"


class TestParseMultipleTransactions:
    """Test parsing multiple transactions."""

    def test_multiple_sends(self):
        """Multiple send commands create multiple transactions."""
        text = """
zone test.com
update add a.test.com. 300 A 1.2.3.4
send
update add b.test.com. 300 A 5.6.7.8
send
"""
        result = parse(text)
        assert len(result) == 2
        assert result[0].operations[0].name == "a.test.com."
        assert result[1].operations[0].name == "b.test.com."

    def test_different_zones(self):
        """Different zones in multiple transactions."""
        text = """
zone test1.com
update add a.test1.com. 300 A 1.2.3.4
send
zone test2.com
update add b.test2.com. 300 A 5.6.7.8
send
"""
        result = parse(text)
        assert len(result) == 2
        assert result[0].zone == "test1.com."
        assert result[1].zone == "test2.com."

    def test_implicit_send(self):
        """Trailing operations without explicit send are included."""
        text = "zone test.com\nupdate add www.test.com. 300 A 1.2.3.4"
        result = parse(text)
        assert len(result) == 1
        assert len(result[0].operations) == 1


class TestParseIgnoredCommands:
    """Test that ignored commands don't cause errors."""

    def test_server_ignored(self):
        """Server command is ignored."""
        text = "server ns1.example.com\nzone test.com\nupdate add a.test.com. 300 A 1.2.3.4\nsend"
        result = parse(text)
        assert len(result) == 1

    def test_key_ignored(self):
        """Key command is ignored."""
        text = "key mykey secret123\nzone test.com\nupdate add a.test.com. 300 A 1.2.3.4\nsend"
        result = parse(text)
        assert len(result) == 1

    def test_local_ignored(self):
        """Local command is ignored."""
        text = "local 10.0.0.1\nzone test.com\nupdate add a.test.com. 300 A 1.2.3.4\nsend"
        result = parse(text)
        assert len(result) == 1


class TestParseQuit:
    """Test quit command handling."""

    def test_quit_stops_processing(self):
        """Quit command stops processing."""
        text = """
zone test.com
update add a.test.com. 300 A 1.2.3.4
send
quit
update add b.test.com. 300 A 5.6.7.8
send
"""
        result = parse(text)
        assert len(result) == 1  # Only first transaction


class TestParseErrors:
    """Test error handling."""

    def test_unknown_command(self):
        """Unknown command raises error."""
        text = "zone test.com\nunknown foo bar\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "Unknown command" in str(exc_info.value)

    def test_error_includes_line_number(self):
        """Parse errors include line number."""
        text = "zone test.com\n\n\nunknown foo bar\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "Line 4" in str(exc_info.value)

    def test_update_missing_subcommand(self):
        """Update without add/delete raises error."""
        text = "zone test.com\nupdate\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "add or delete" in str(exc_info.value)

    def test_update_invalid_subcommand(self):
        """Update with invalid subcommand raises error."""
        text = "zone test.com\nupdate modify www 300 A 1.2.3.4\nsend"
        with pytest.raises(NSUpdateParseError) as exc_info:
            parse(text)
        assert "Unknown update subcommand" in str(exc_info.value)


class TestComplexScenarios:
    """Test complex real-world scenarios."""

    def test_full_add_with_prereq(self):
        """Full add with prerequisite check."""
        text = """
zone example.com.
prereq nxdomain newhost.example.com.
update add newhost.example.com. 3600 A 192.0.2.100
send
"""
        result = parse(text)
        assert len(result) == 1
        assert len(result[0].prerequisites) == 1
        assert len(result[0].operations) == 1
        assert result[0].prerequisites[0].prereq_type == PrereqType.NXDOMAIN
        assert result[0].operations[0].action == UpdateAction.ADD

    def test_replace_pattern(self):
        """Delete and add pattern for replacement."""
        text = """
zone example.com.
prereq yxrrset www.example.com. A
update delete www.example.com. A
update add www.example.com. 3600 A 192.0.2.200
send
"""
        result = parse(text)
        assert len(result[0].prerequisites) == 1
        assert len(result[0].operations) == 2
        assert result[0].operations[0].action == UpdateAction.DELETE
        assert result[0].operations[1].action == UpdateAction.ADD

    def test_multiple_records(self):
        """Multiple records in one transaction."""
        text = """
zone example.com.
update add www.example.com. 300 A 192.0.2.1
update add www.example.com. 300 A 192.0.2.2
update add www.example.com. 300 AAAA 2001:db8::1
send
"""
        result = parse(text)
        assert len(result[0].operations) == 3
