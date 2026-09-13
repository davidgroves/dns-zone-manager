"""Task-scoped attribution context for DNS changes.

All DNS writes funnel through ``DNSClient._send_update()``, which has no access
to the authenticated user or to whether the write came from an API request or
the scheduler. Rather than threading those through eight call sites, they are
carried in a ContextVar.

ContextVars are per-task, so an API request and the scheduler loop each see
their own value with no cross-contamination.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

# How a change was triggered
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULER = "scheduler"
TRIGGER_APPLY_NOW = "apply_now"
TRIGGER_REVERT = "revert"


@dataclass(frozen=True)
class ChangeContext:
    """Who is making a DNS change and why."""

    actor: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    auth_type: str | None = None
    trigger: str = TRIGGER_MANUAL
    change_id: str | None = None
    change_name: str | None = None
    request_id: str | None = None


_change_context: ContextVar[ChangeContext | None] = ContextVar(
    "dns_change_context",
    default=None,
)


def get_change_context() -> ChangeContext | None:
    """Return the change context for the current task, if any."""
    return _change_context.get()


def set_change_context(context: ChangeContext | None) -> None:
    """Set (or clear, with None) the change context for the current task.

    Used by the auth dependency, whose value must outlive its own frame so the
    endpoint body can see it.
    """
    _change_context.set(context)


@contextmanager
def change_context(**overrides: object) -> Iterator[ChangeContext]:
    """Override fields of the current change context for the duration of a block.

    Fields that are not overridden are inherited from the enclosing context, so
    the scheduler can set a trigger and change ID while keeping the actor that
    an API request already established.
    """
    current = _change_context.get() or ChangeContext()
    updated = replace(current, **overrides)
    token = _change_context.set(updated)
    try:
        yield updated
    finally:
        _change_context.reset(token)
