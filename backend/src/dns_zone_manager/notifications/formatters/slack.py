"""Slack Block Kit webhook payload."""

from typing import Any

from dns_zone_manager.notifications.events import DnsChangeEvent

# Slack renders at most 10 fields per section and truncates long text blocks
MAX_OPERATIONS = 10


def _operation_lines(event: DnsChangeEvent) -> str:
    operations = event.operations[:MAX_OPERATIONS]
    lines = [op.describe() for op in operations]
    remaining = len(event.operations) - len(operations)
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines) if lines else "(no record operations)"


def format_slack(event: DnsChangeEvent, base_url: str) -> dict[str, Any]:
    """Render the event as a Slack Block Kit message."""
    icon = ":white_check_mark:" if event.succeeded else ":x:"
    fields = [
        {"type": "mrkdwn", "text": f"*Zone*\n{event.zone}"},
        {"type": "mrkdwn", "text": f"*Changed by*\n{event.actor_display}"},
        {"type": "mrkdwn", "text": f"*Change type*\n{event.trigger_display}"},
        {
            "type": "mrkdwn",
            "text": f"*Result*\n{event.rcode or ('OK' if event.succeeded else 'FAILED')}",
        },
    ]
    if event.auth_type:
        fields.append({"type": "mrkdwn", "text": f"*Auth*\n{event.auth_type}"})
    if event.change_name:
        fields.append({"type": "mrkdwn", "text": f"*Change*\n{event.change_name}"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{icon} {event.summary()}"},
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Records*\n```{_operation_lines(event)}```"},
        },
    ]

    if event.error:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error*\n{event.error}"},
            }
        )

    link = event.link(base_url)
    if link:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View change"},
                        "url": link,
                    }
                ],
            }
        )

    context_parts = [f"{event.timestamp.isoformat()}"]
    if event.request_id:
        context_parts.append(f"request {event.request_id}")
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(context_parts)}],
        }
    )

    return {"text": event.summary(), "blocks": blocks}
