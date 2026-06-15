"""Unit tests for pagination functionality in the cache layer."""

from typing import cast

import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.zone
from dns_zone_manager.dns.cache import CachedZone, PaginatedRRsets, RRsetInfo


def create_test_zone_with_records(zone_name: str, num_records: int) -> dns.zone.Zone:
    """Create a test zone with a specified number of A records.

    Args:
        zone_name: Zone name (e.g., "example.com.")
        num_records: Number of A records to create (named host001, host002, etc.)

    Returns:
        dns.zone.Zone object
    """
    # Create zone text with SOA, NS, and numbered host records
    zone_text = f"""$ORIGIN {zone_name}
$TTL 3600
@   IN  SOA ns1.{zone_name} admin.{zone_name} 2024010101 3600 600 604800 300
@   IN  NS  ns1.{zone_name}
ns1 IN  A   192.0.2.1
"""
    # Add numbered host records
    for i in range(1, num_records + 1):
        zone_text += f"host{i:03d} IN A 192.0.2.{(i % 254) + 1}\n"

    zone = dns.zone.from_text(zone_text, origin=zone_name, check_origin=False)
    return zone


class TestCachedZonePagination:
    """Tests for CachedZone.get_all_rrsets pagination."""

    def test_pagination_returns_correct_first_page(self):
        """Test that first page returns correct records."""
        zone = create_test_zone_with_records("example.com.", 10)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        result = cached_zone.get_all_rrsets(limit=3)

        assert isinstance(result, PaginatedRRsets)
        assert len(result.rrsets) == 3
        assert result.has_more is True
        assert result.next_cursor is not None
        # Total should include SOA, NS, ns1 A record, plus 10 host records = 13
        assert result.total_count == 13

    def test_pagination_cursor_points_to_first_record_of_next_page(self):
        """Test that next_cursor is the first record of the next page."""
        zone = create_test_zone_with_records("example.com.", 10)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Get first page
        page1 = cached_zone.get_all_rrsets(limit=3)
        assert isinstance(page1, PaginatedRRsets)
        assert page1.has_more is True
        cursor = page1.next_cursor
        assert cursor is not None

        # Get second page using cursor
        page2 = cached_zone.get_all_rrsets(after_name=cursor, limit=3)
        assert isinstance(page2, PaginatedRRsets)

        # The cursor format is "name:type" - verify it matches first record of next page
        assert len(page2.rrsets) > 0
        # Parse cursor (format: "name:type")
        last_colon = cursor.rfind(":")
        cursor_name = cursor[:last_colon].lower()
        cursor_type = cursor[last_colon + 1 :]
        assert page2.rrsets[0].name.lower() == cursor_name
        assert page2.rrsets[0].rdtype == cursor_type

    def test_pagination_no_records_skipped(self):
        """Test that paginating through all pages returns all records.

        This is a regression test for a bug where using cursor-based pagination
        would skip records because of incorrect > vs >= comparison.
        """
        zone = create_test_zone_with_records("example.com.", 10)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Get all records without pagination
        all_records_result = cached_zone.get_all_rrsets()
        assert isinstance(all_records_result, list)
        all_records = cast(list[RRsetInfo], all_records_result)
        all_names = {r.name for r in all_records}
        total_expected = len(all_records)

        # Paginate through with a small page size
        page_size = 3
        collected_names: set[str] = set()
        cursor: str | None = None
        pages = 0
        max_pages = total_expected + 1  # Safety limit

        while pages < max_pages:
            result = cached_zone.get_all_rrsets(after_name=cursor, limit=page_size)
            assert isinstance(result, PaginatedRRsets)

            for rrset in result.rrsets:
                collected_names.add(rrset.name)

            pages += 1

            if result.has_more and result.next_cursor:
                cursor = result.next_cursor
            else:
                break

        # Verify all records were collected
        assert collected_names == all_names, (
            f"Pagination missed records! "
            f"Expected {len(all_names)} records, got {len(collected_names)}. "
            f"Missing: {all_names - collected_names}"
        )

    def test_pagination_no_duplicates(self):
        """Test that paginating through all pages doesn't return duplicates."""
        zone = create_test_zone_with_records("example.com.", 10)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Paginate through with a small page size
        page_size = 3
        collected_records: list[str] = []
        cursor: str | None = None
        pages = 0
        max_pages = 20

        while pages < max_pages:
            result = cached_zone.get_all_rrsets(after_name=cursor, limit=page_size)
            assert isinstance(result, PaginatedRRsets)

            for rrset in result.rrsets:
                collected_records.append(f"{rrset.name}|{rrset.rdtype}")

            pages += 1

            if result.has_more and result.next_cursor:
                cursor = result.next_cursor
            else:
                break

        # Check for duplicates
        unique_records = set(collected_records)
        assert len(unique_records) == len(collected_records), (
            f"Pagination returned duplicates! "
            f"Got {len(collected_records)} records but only {len(unique_records)} unique."
        )

    def test_pagination_last_page_has_no_more(self):
        """Test that the last page has has_more=False."""
        zone = create_test_zone_with_records("example.com.", 5)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Navigate to last page
        cursor: str | None = None
        result: list[RRsetInfo] | PaginatedRRsets | None = None

        for _ in range(20):  # Safety limit
            result = cached_zone.get_all_rrsets(after_name=cursor, limit=3)
            assert isinstance(result, PaginatedRRsets)

            if not result.has_more:
                break
            cursor = result.next_cursor

        assert result is not None
        assert result.has_more is False
        assert result.next_cursor is None

    def test_pagination_cursor_past_end_returns_empty(self):
        """Test that a cursor past all records returns empty page."""
        zone = create_test_zone_with_records("example.com.", 5)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        result = cached_zone.get_all_rrsets(after_name="zzzzzzzzz.example.com.", limit=10)

        assert isinstance(result, PaginatedRRsets)
        assert len(result.rrsets) == 0
        assert result.has_more is False
        assert result.next_cursor is None

    def test_pagination_with_class_filter(self):
        """Test pagination works correctly with class filter."""
        zone = create_test_zone_with_records("example.com.", 5)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Get IN class records only with pagination
        result = cached_zone.get_all_rrsets(rdclass="IN", limit=3)

        assert isinstance(result, PaginatedRRsets)
        for rrset in result.rrsets:
            assert rrset.rdclass == "IN"

    def test_pagination_multiple_records_same_name(self):
        """Test pagination correctly handles multiple records at the same name.

        This is a regression test for a bug where records would be skipped when
        paginating through names with multiple record types (like zone apex with
        SOA, NS, MX, TXT). The cursor must include both name and type to be unique.
        """
        # Create a zone with multiple record types at the apex
        zone_text = """$ORIGIN example.com.
$TTL 3600
@   IN  SOA ns1.example.com. admin.example.com. 2024010101 3600 600 604800 300
@   IN  NS  ns1.example.com.
@   IN  MX  10 mail.example.com.
@   IN  TXT "v=spf1 mx -all"
@   IN  A   192.0.2.100
ns1 IN  A   192.0.2.1
mail IN  A   192.0.2.2
www IN  A   192.0.2.3
"""
        zone = dns.zone.from_text(zone_text, origin="example.com.", check_origin=False)
        cached_zone = CachedZone("example.com.", zone, 2024010101)

        # Get all records without pagination
        all_records_result = cached_zone.get_all_rrsets()
        assert isinstance(all_records_result, list)
        all_records = cast(list[RRsetInfo], all_records_result)
        all_record_keys = {(r.name, r.rdtype) for r in all_records}
        total_expected = len(all_records)

        # Zone apex (example.com.) has 5 types: SOA, NS, MX, TXT, A
        # Plus ns1, mail, www = 8 total records
        assert total_expected == 8

        # Paginate with small page size to force cursor through apex records
        page_size = 2
        collected_keys: set[tuple[str, str]] = set()
        cursor: str | None = None
        pages = 0
        max_pages = total_expected + 1

        while pages < max_pages:
            result = cached_zone.get_all_rrsets(after_name=cursor, limit=page_size)
            assert isinstance(result, PaginatedRRsets)

            for rrset in result.rrsets:
                collected_keys.add((rrset.name, rrset.rdtype))

            pages += 1

            if result.has_more and result.next_cursor:
                cursor = result.next_cursor
            else:
                break

        # Verify ALL records were collected (no skipping)
        assert collected_keys == all_record_keys, (
            f"Pagination missed records! "
            f"Expected {len(all_record_keys)} records, got {len(collected_keys)}. "
            f"Missing: {all_record_keys - collected_keys}"
        )

        # Verify no duplicates by checking page count
        # 8 records / 2 per page = 4 pages
        assert pages == 4
