"""Unit tests for perf.probes helpers."""

from __future__ import annotations

from perf.probes import MetricsSnapshot, metrics_delta


def test_metrics_delta_skips_created_timestamps() -> None:
    before = MetricsSnapshot(
        timestamp=1.0,
        values={
            "dns_zone_manager_rrset_adds_total": 10.0,
            "dns_zone_manager_rrset_adds_created": 1_700_000_000.0,
            'dns_zone_manager_rrset_adds_created{zone="a."}': 1_700_000_000.0,
        },
    )
    after = MetricsSnapshot(
        timestamp=2.0,
        values={
            "dns_zone_manager_rrset_adds_total": 25.0,
            "dns_zone_manager_rrset_adds_created": 1_700_000_000.0,
            'dns_zone_manager_rrset_adds_created{zone="a."}': 1_700_000_000.0,
        },
    )
    delta = metrics_delta(before, after)
    assert delta == {"dns_zone_manager_rrset_adds_total": 15.0}
