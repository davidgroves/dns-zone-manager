"""Unit tests for the shared DNS UPDATE builder."""

from unittest.mock import MagicMock

import pytest
from dns_zone_manager.dns.update_builder import (
    UpdateBuildError,
    build_update,
    preview_auto_prerequisites,
    preview_explicit_prerequisites,
)
from dns_zone_manager.models.requests import AtomicOperation
from dns_zone_manager.models.scheduled import ChangePrerequisite


def _mock_dns_client():
    client = MagicMock()
    client.keyring = None
    client.keyname = None
    client.keyalgorithm = None

    def normalize_name(name, zone):
        import dns.name

        zone_name = dns.name.from_text(zone)
        if name.endswith("."):
            return dns.name.from_text(name)
        return dns.name.from_text(name, origin=zone_name)

    client.normalize_name.side_effect = normalize_name
    return client


def _mock_cache(*, existing=None):
    cache = MagicMock()
    cache.get_rrset.return_value = existing
    zone = MagicMock()
    zone.get_rrsets_by_name.return_value = [existing] if existing else []
    zone.serial = 100
    cache.get_zone.return_value = zone
    return cache


def _rrset(records=None):
    rr = MagicMock()
    rr.records = records or ["192.0.2.1"]
    return rr


class TestBuildUpdate:
    def test_add_with_auto_prereq(self):
        client = _mock_dns_client()
        cache = _mock_cache(existing=None)
        ops = [AtomicOperation(action="add", name="www", type="A", ttl=3600, records=["192.0.2.1"])]
        built = build_update(
            "example.com.",
            ops,
            client,
            cache,
            auto_prerequisites=True,
            validate_cache_state=True,
        )
        assert len(built.cache_updates) == 1
        assert built.cache_updates[0].action == "add"
        assert len(built.auto_prerequisites) == 1
        assert built.auto_prerequisites[0].prereq_type == "nxrrset"

    def test_add_fails_when_exists_and_validating(self):
        client = _mock_dns_client()
        cache = _mock_cache(existing=_rrset())
        ops = [AtomicOperation(action="add", name="www", type="A", ttl=3600, records=["192.0.2.1"])]
        with pytest.raises(UpdateBuildError) as exc:
            build_update(
                "example.com.",
                ops,
                client,
                cache,
                validate_cache_state=True,
            )
        assert exc.value.code == "RRSET_EXISTS"

    def test_add_without_validate_when_exists(self):
        """Scheduled execution: do not fail at build time if cache says exists."""
        client = _mock_dns_client()
        cache = _mock_cache(existing=_rrset())
        ops = [AtomicOperation(action="add", name="www", type="A", ttl=3600, records=["192.0.2.1"])]
        built = build_update(
            "example.com.",
            ops,
            client,
            cache,
            validate_cache_state=False,
        )
        assert len(built.cache_updates) == 1

    def test_delete_requires_existing_when_validating(self):
        client = _mock_dns_client()
        cache = _mock_cache(existing=None)
        ops = [AtomicOperation(action="delete", name="www", type="A", records=None)]
        with pytest.raises(UpdateBuildError) as e:
            build_update("example.com.", ops, client, cache, validate_cache_state=True)
        assert e.value.code == "RRSET_NOT_FOUND"

    def test_replace_with_explicit_prereq(self):
        client = _mock_dns_client()
        cache = _mock_cache(existing=_rrset(["192.0.2.1"]))
        ops = [
            AtomicOperation(
                action="replace",
                name="www",
                type="A",
                ttl=300,
                records=["192.0.2.2"],
            )
        ]
        prereqs = [
            ChangePrerequisite(prereq_type="yxrrset", name="www", rdtype="A"),
        ]
        built = build_update(
            "example.com.",
            ops,
            client,
            cache,
            prerequisites=prereqs,
            auto_prerequisites=False,
        )
        assert built.cache_updates[0].action == "replace"


class TestPreview:
    def test_preview_auto_add_pass(self):
        cache = _mock_cache(existing=None)
        ops = [AtomicOperation(action="add", name="www", type="A", records=["192.0.2.1"])]
        results = preview_auto_prerequisites("example.com.", ops, cache)
        assert len(results) == 1
        assert results[0].passed is True

    def test_preview_auto_add_fail(self):
        cache = _mock_cache(existing=_rrset())
        ops = [AtomicOperation(action="add", name="www", type="A", records=["192.0.2.1"])]
        results = preview_auto_prerequisites("example.com.", ops, cache)
        assert results[0].passed is False

    def test_preview_explicit_nxrrset(self):
        cache = _mock_cache(existing=None)
        prereqs = [
            ChangePrerequisite(prereq_type="nxrrset", name="www", rdtype="A"),
        ]
        results = preview_explicit_prerequisites("example.com.", prereqs, cache)
        assert results[0].passed is True
        assert results[0].source == "explicit"
