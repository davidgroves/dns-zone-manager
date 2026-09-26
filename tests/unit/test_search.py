"""Unit tests for search — thin model checks only.

Real search behavior is covered by tests/integration/test_search.py against
ZoneCache and the HTTP API. Do not re-test Python's re module here.
"""

from dns_zone_manager.models.requests import (
    GlobalSearchResponse,
    RRsetResponse,
    ZoneSearchResponse,
    ZoneSearchResultItem,
)


def test_zone_search_response_round_trip():
    response = ZoneSearchResponse(
        zone="example.com.",
        serial=1,
        results=[
            RRsetResponse(
                name="www.example.com.",
                ttl=3600,
                type="A",
                records=["192.0.2.1"],
            )
        ],
        total_count=1,
    )
    assert response.model_dump()["total_count"] == 1
    assert response.results[0].type == "A"


def test_global_search_response_round_trip():
    response = GlobalSearchResponse(
        results=[
            ZoneSearchResultItem(
                zone="example.com.",
                serial=1,
                rrsets=[
                    RRsetResponse(
                        name="www.example.com.",
                        ttl=3600,
                        type="A",
                        records=["192.0.2.1"],
                    )
                ],
            )
        ],
        total_count=1,
    )
    assert len(response.results) == 1
    assert response.total_count == 1
