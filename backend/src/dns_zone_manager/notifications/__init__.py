"""Outbound notifications for DNS changes."""

from dns_zone_manager.notifications.context import (
    TRIGGER_APPLY_NOW,
    TRIGGER_MANUAL,
    TRIGGER_REVERT,
    TRIGGER_SCHEDULER,
    ChangeContext,
    change_context,
    get_change_context,
    set_change_context,
)
from dns_zone_manager.notifications.dispatcher import WebhookDispatcher
from dns_zone_manager.notifications.events import (
    EVENT_CHANGE_APPLIED,
    EVENT_CHANGE_FAILED,
    ChangeOperation,
    DnsChangeEvent,
    operations_from_update,
)

__all__ = [
    "EVENT_CHANGE_APPLIED",
    "EVENT_CHANGE_FAILED",
    "TRIGGER_APPLY_NOW",
    "TRIGGER_MANUAL",
    "TRIGGER_REVERT",
    "TRIGGER_SCHEDULER",
    "ChangeContext",
    "ChangeOperation",
    "DnsChangeEvent",
    "WebhookDispatcher",
    "change_context",
    "get_change_context",
    "operations_from_update",
    "set_change_context",
]
