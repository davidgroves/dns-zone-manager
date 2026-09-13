"""Unit tests for change event derivation, context, and payload formatting."""

import asyncio
from datetime import UTC, datetime

import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.update
import pytest
from dns_zone_manager.notifications.context import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULER,
    ChangeContext,
    change_context,
    get_change_context,
    set_change_context,
)
from dns_zone_manager.notifications.events import (
    EVENT_CHANGE_APPLIED,
    EVENT_CHANGE_FAILED,
    ChangeOperation,
    DnsChangeEvent,
    operations_from_update,
)
from dns_zone_manager.notifications.formatters import format_payload
from dns_zone_manager.notifications.formatters.slack import format_slack
from dns_zone_manager.notifications.formatters.teams import format_teams

ZONE = "test.example."


def _update() -> dns.update.Update:
    return dns.update.Update(ZONE)


def _a(value: str) -> dns.rdata.Rdata:
    return dns.rdata.from_text("IN", "A", value)


def _event(**overrides) -> DnsChangeEvent:
    defaults = {
        "event": EVENT_CHANGE_APPLIED,
        "zone": ZONE,
        "operations": [
            ChangeOperation(
                action="add",
                name="www.test.example.",
                rdtype="A",
                ttl=300,
                records=["10.0.0.1"],
            )
        ],
        "timestamp": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "trigger": TRIGGER_MANUAL,
        "actor": "alice@example.com",
        "actor_name": "Alice Example",
        "auth_type": "azure_ad",
        "change_id": "abc-123",
        "rcode": "NOERROR",
    }
    return DnsChangeEvent(**{**defaults, **overrides})


class TestOperationsFromUpdate:
    """Recovering record operations from a DNS UPDATE message."""

    def test_add(self):
        update = _update()
        update.add("www.test.example.", 300, _a("10.0.0.1"))

        ops = operations_from_update(update)

        assert len(ops) == 1
        assert ops[0].action == "add"
        assert ops[0].name == "www.test.example."
        assert ops[0].rdtype == "A"
        assert ops[0].rdclass == "IN"
        assert ops[0].ttl == 300
        assert ops[0].records == ["10.0.0.1"]

    def test_delete_whole_rrset_has_no_records(self):
        update = _update()
        update.delete("old.test.example.", dns.rdatatype.TXT)

        ops = operations_from_update(update)

        assert len(ops) == 1
        assert ops[0].action == "delete"
        assert ops[0].rdtype == "TXT"
        assert ops[0].records == []

    def test_delete_specific_rdata_keeps_values(self):
        update = _update()
        update.delete("www.test.example.", dns.rdatatype.A, "10.0.0.9")

        ops = operations_from_update(update)

        assert ops[0].action == "delete"
        assert ops[0].records == ["10.0.0.9"]

    def test_replace_is_coalesced_from_delete_plus_add(self):
        # dnspython emits delete-ANY followed by an add for the same name/type
        update = _update()
        update.replace("www.test.example.", 60, _a("10.0.0.2"))

        ops = operations_from_update(update)

        assert len(ops) == 1
        assert ops[0].action == "replace"
        assert ops[0].ttl == 60
        assert ops[0].records == ["10.0.0.2"]

    def test_multiple_operations_preserve_order(self):
        update = _update()
        update.add("a.test.example.", 300, _a("10.0.0.1"))
        update.delete("b.test.example.", dns.rdatatype.TXT)
        update.replace("c.test.example.", 60, _a("10.0.0.3"))

        ops = operations_from_update(update)

        assert [(op.action, op.name) for op in ops] == [
            ("add", "a.test.example."),
            ("delete", "b.test.example."),
            ("replace", "c.test.example."),
        ]

    def test_delete_all_types(self):
        update = _update()
        update.delete("gone.test.example.")

        ops = operations_from_update(update)

        assert ops[0].action == "delete"
        assert ops[0].rdtype == "ANY"

    def test_empty_update_yields_no_operations(self):
        assert operations_from_update(_update()) == []

    def test_separate_deletes_are_not_coalesced_into_replace(self):
        update = _update()
        update.delete("a.test.example.", dns.rdatatype.A)
        update.add("b.test.example.", 300, _a("10.0.0.1"))

        ops = operations_from_update(update)

        assert [op.action for op in ops] == ["delete", "add"]


class TestChangeContext:
    """Task-scoped attribution context."""

    @pytest.fixture(autouse=True)
    def _clear_context(self):
        set_change_context(None)
        yield
        set_change_context(None)

    def test_defaults_to_none(self):
        assert get_change_context() is None

    def test_change_context_overrides_and_restores(self):
        set_change_context(ChangeContext(actor="alice", trigger=TRIGGER_MANUAL))

        with change_context(trigger=TRIGGER_SCHEDULER, change_id="c1"):
            inner = get_change_context()
            assert inner is not None
            # Actor is inherited, trigger and change id overridden
            assert inner.actor == "alice"
            assert inner.trigger == TRIGGER_SCHEDULER
            assert inner.change_id == "c1"

        outer = get_change_context()
        assert outer is not None
        assert outer.trigger == TRIGGER_MANUAL
        assert outer.change_id is None

    @pytest.mark.asyncio
    async def test_context_does_not_leak_between_tasks(self):
        set_change_context(ChangeContext(actor="parent"))
        seen: dict[str, str | None] = {}

        async def worker(name: str, actor: str) -> None:
            set_change_context(ChangeContext(actor=actor))
            await asyncio.sleep(0)
            ctx = get_change_context()
            seen[name] = ctx.actor if ctx else None

        await asyncio.gather(worker("a", "alice"), worker("b", "bob"))

        # Concurrent tasks each keep their own actor, and neither overwrites
        # the caller's: this is what lets the scheduler loop and API requests
        # attribute writes correctly while running on the same event loop.
        assert seen == {"a": "alice", "b": "bob"}
        parent = get_change_context()
        assert parent is not None
        assert parent.actor == "parent"


class TestEventModel:
    """DnsChangeEvent derived properties."""

    def test_link_prefers_scheduler_deep_link(self):
        event = _event(change_id="abc-123")
        assert (
            event.link("https://dns.example.com")
            == "https://dns.example.com/?view=scheduled&change=abc-123"
        )

    def test_link_falls_back_to_zone_when_unlinkable(self):
        event = _event(change_id=None)
        assert event.link("https://dns.example.com") == (f"https://dns.example.com/?zone={ZONE}")

    def test_link_is_none_without_base_url(self):
        assert _event().link("") is None

    def test_actor_display_prefers_name_then_email_then_id(self):
        assert _event().actor_display == "Alice Example"
        assert _event(actor_name=None).actor_display == "alice@example.com"
        assert (
            _event(actor_name=None, actor="key-admin", actor_email=None).actor_display
            == "key-admin"
        )
        assert _event(actor_name=None, actor=None, actor_email=None).actor_display == "unknown"

    def test_trigger_display_labels(self):
        assert _event(trigger="manual").trigger_display == "Manual"
        assert _event(trigger="scheduler").trigger_display == "Scheduled"
        assert _event(trigger="apply_now").trigger_display == "Scheduled (applied now)"

    def test_default_name_summarizes_operations(self):
        assert _event().default_name() == "add A www.test.example."

    def test_default_name_counts_extra_operations(self):
        ops = [
            ChangeOperation(action="add", name="a.test.example.", rdtype="A"),
            ChangeOperation(action="add", name="b.test.example.", rdtype="A"),
        ]
        assert _event(operations=ops).default_name() == "add A a.test.example. (+1 more)"

    def test_default_name_uses_change_name_when_present(self):
        assert _event(change_name="Nightly rotation").default_name() == "Nightly rotation"

    def test_summary_reflects_outcome(self):
        assert _event().summary() == f"DNS change applied: {ZONE}"
        assert _event(event=EVENT_CHANGE_FAILED).summary() == f"DNS change failed: {ZONE}"

    def test_to_dict_carries_who_what_and_link(self):
        payload = _event().to_dict("https://dns.example.com")

        assert payload["event"] == "change_applied"
        assert payload["trigger"] == "manual"
        assert payload["actor"] == {
            "id": "alice@example.com",
            "name": "Alice Example",
            "email": None,
            "auth_type": "azure_ad",
        }
        assert payload["change"]["link"].endswith("change=abc-123")
        assert payload["operations"][0]["records"] == ["10.0.0.1"]
        assert payload["result"] == {"success": True, "rcode": "NOERROR", "error": None}


class TestSlackFormatter:
    """Slack Block Kit payloads."""

    def test_includes_actor_trigger_and_link_button(self):
        payload = format_slack(_event(), "https://dns.example.com")

        assert payload["text"] == f"DNS change applied: {ZONE}"
        rendered = str(payload["blocks"])
        assert "Alice Example" in rendered
        assert "Manual" in rendered

        actions = [b for b in payload["blocks"] if b["type"] == "actions"]
        assert len(actions) == 1
        assert actions[0]["elements"][0]["url"].endswith("change=abc-123")

    def test_omits_button_without_base_url(self):
        payload = format_slack(_event(), "")
        assert not [b for b in payload["blocks"] if b["type"] == "actions"]

    def test_failure_includes_error_section(self):
        payload = format_slack(
            _event(event=EVENT_CHANGE_FAILED, error="NXRRSET", rcode="NXRRSET"),
            "https://dns.example.com",
        )
        assert "NXRRSET" in str(payload["blocks"])

    def test_long_operation_list_is_truncated(self):
        ops = [
            ChangeOperation(action="add", name=f"h{i}.test.example.", rdtype="A") for i in range(25)
        ]
        payload = format_slack(_event(operations=ops), "https://dns.example.com")

        assert "and 15 more" in str(payload["blocks"])


class TestTeamsFormatter:
    """Teams Adaptive Card payloads."""

    def test_adaptive_card_envelope(self):
        payload = format_teams(_event(), "https://dns.example.com")

        assert payload["type"] == "message"
        attachment = payload["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        card = attachment["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["actions"][0]["url"].endswith("change=abc-123")

    def test_facts_carry_who_and_trigger(self):
        card = format_teams(_event(), "https://dns.example.com")["attachments"][0]["content"]
        facts = {f["title"]: f["value"] for f in card["body"][1]["facts"]}

        assert facts["Changed by"] == "Alice Example"
        assert facts["Change type"] == "Manual"
        assert facts["Zone"] == ZONE
        assert facts["Result"] == "NOERROR"

    def test_failure_is_colored_attention(self):
        card = format_teams(
            _event(event=EVENT_CHANGE_FAILED, error="boom"),
            "https://dns.example.com",
        )["attachments"][0]["content"]

        assert card["body"][0]["color"] == "Attention"
        assert any("boom" in str(block.get("text", "")) for block in card["body"])


class TestFormatterDispatch:
    """format_payload selection by target type."""

    @pytest.mark.parametrize("target_type", ["slack", "teams", "generic"])
    def test_known_types(self, target_type: str):
        assert format_payload(target_type, _event(), "https://dns.example.com")

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown webhook target type"):
            format_payload("discord", _event(), "https://dns.example.com")

    def test_generic_is_plain_event_dict(self):
        payload = format_payload("generic", _event(), "https://dns.example.com")
        assert payload["event"] == "change_applied"
        assert payload["zone"] == ZONE
