"""Unit tests for search functionality."""

import re

from dns_zone_manager.models.requests import (
    GlobalSearchResponse,
    RRsetResponse,
    ZoneSearchResponse,
    ZoneSearchResultItem,
)


class TestSearchResponseModels:
    """Tests for search response models."""

    def test_zone_search_response(self):
        """Test ZoneSearchResponse model."""
        response = ZoneSearchResponse(
            zone="example.com.",
            serial=2024010101,
            results=[
                RRsetResponse(
                    name="foo.example.com.",
                    ttl=3600,
                    type="CNAME",
                    records=["bar.example.com."],
                )
            ],
            total_count=1,
        )
        assert response.zone == "example.com."
        assert response.serial == 2024010101
        assert len(response.results) == 1
        assert response.total_count == 1

    def test_zone_search_response_empty_results(self):
        """Test ZoneSearchResponse with no results."""
        response = ZoneSearchResponse(
            zone="example.com.",
            serial=2024010101,
            results=[],
            total_count=0,
        )
        assert response.zone == "example.com."
        assert len(response.results) == 0
        assert response.total_count == 0

    def test_zone_search_result_item(self):
        """Test ZoneSearchResultItem model."""
        item = ZoneSearchResultItem(
            zone="example.com.",
            serial=2024010101,
            rrsets=[
                RRsetResponse(
                    name="foo.example.com.",
                    ttl=3600,
                    type="A",
                    records=["192.0.2.1"],
                )
            ],
        )
        assert item.zone == "example.com."
        assert item.serial == 2024010101
        assert len(item.rrsets) == 1

    def test_global_search_response(self):
        """Test GlobalSearchResponse model."""
        response = GlobalSearchResponse(
            results=[
                ZoneSearchResultItem(
                    zone="example.com.",
                    serial=2024010101,
                    rrsets=[
                        RRsetResponse(
                            name="foo.example.com.",
                            ttl=3600,
                            type="CNAME",
                            records=["bar.example.com."],
                        )
                    ],
                ),
                ZoneSearchResultItem(
                    zone="other.com.",
                    serial=2024010201,
                    rrsets=[
                        RRsetResponse(
                            name="foo.other.com.",
                            ttl=3600,
                            type="A",
                            records=["192.0.2.1"],
                        )
                    ],
                ),
            ],
            total_count=2,
        )
        assert len(response.results) == 2
        assert response.total_count == 2
        assert response.results[0].zone == "example.com."
        assert response.results[1].zone == "other.com."

    def test_global_search_response_empty(self):
        """Test GlobalSearchResponse with no results."""
        response = GlobalSearchResponse(
            results=[],
            total_count=0,
        )
        assert len(response.results) == 0
        assert response.total_count == 0


class TestRegexPatterns:
    """Tests for regex pattern compilation and matching."""

    def test_simple_prefix_pattern(self):
        """Test matching names starting with a prefix."""
        pattern = re.compile(r"^api-.*", re.IGNORECASE)
        assert pattern.search("api-v1.example.com.")
        assert pattern.search("API-V2.example.com.")
        assert not pattern.search("www.example.com.")

    def test_simple_suffix_pattern(self):
        """Test matching names ending with a suffix."""
        pattern = re.compile(r".*\.internal\.$", re.IGNORECASE)
        assert pattern.search("db.internal.")
        assert pattern.search("cache.internal.")
        assert not pattern.search("db.external.")

    def test_contains_pattern(self):
        """Test matching names containing a substring."""
        pattern = re.compile(r".*foo.*", re.IGNORECASE)
        assert pattern.search("foo.example.com.")
        assert pattern.search("prefoo.example.com.")
        assert pattern.search("foobar.example.com.")
        assert not pattern.search("bar.example.com.")

    def test_ip_pattern(self):
        """Test matching IP addresses."""
        pattern = re.compile(r"^192\.168\..*")
        assert pattern.search("192.168.1.1")
        assert pattern.search("192.168.100.50")
        assert not pattern.search("10.0.0.1")

    def test_cname_target_pattern(self):
        """Test matching CNAME targets."""
        pattern = re.compile(r".*\.cdn\..*", re.IGNORECASE)
        assert pattern.search("assets.cdn.example.com.")
        assert pattern.search("images.CDN.provider.net.")
        assert not pattern.search("www.example.com.")

    def test_case_insensitive(self):
        """Test case insensitive matching."""
        pattern = re.compile(r"www", re.IGNORECASE)
        assert pattern.search("www.example.com.")
        assert pattern.search("WWW.example.com.")
        assert pattern.search("Www.example.com.")
