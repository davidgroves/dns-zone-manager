"""Microsoft Teams Adaptive Card webhook payload.

Uses the ``attachments`` envelope with an Adaptive Card, which is the format
accepted by Power Automate workflow URLs (the replacement for the retired
Office 365 connector MessageCard format).
"""

from typing import Any

from dns_zone_manager.notifications.events import DnsChangeEvent

MAX_OPERATIONS = 15

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


def _operation_text(event: DnsChangeEvent) -> str:
    operations = event.operations[:MAX_OPERATIONS]
    lines = [op.describe() for op in operations]
    remaining = len(event.operations) - len(operations)
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    if not lines:
        return "_(no record operations)_"
    return "\n\n".join(f"- {line}" for line in lines)


def format_teams(event: DnsChangeEvent, base_url: str) -> dict[str, Any]:
    """Render the event as a Teams Adaptive Card."""
    facts = [
        {"title": "Zone", "value": event.zone},
        {"title": "Changed by", "value": event.actor_display},
        {"title": "Change type", "value": event.trigger_display},
        {
            "title": "Result",
            "value": event.rcode or ("OK" if event.succeeded else "FAILED"),
        },
    ]
    if event.auth_type:
        facts.append({"title": "Auth", "value": event.auth_type})
    if event.change_name:
        facts.append({"title": "Change", "value": event.change_name})
    facts.append({"title": "Time", "value": event.timestamp.isoformat()})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": event.summary(),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "color": "Good" if event.succeeded else "Attention",
        },
        {"type": "FactSet", "facts": facts},
        {
            "type": "TextBlock",
            "text": "**Records**",
            "wrap": True,
            "spacing": "Medium",
        },
        {"type": "TextBlock", "text": _operation_text(event), "wrap": True},
    ]

    if event.error:
        body.append(
            {
                "type": "TextBlock",
                "text": f"**Error:** {event.error}",
                "wrap": True,
                "color": "Attention",
            }
        )

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }

    link = event.link(base_url)
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "View change", "url": link}]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
                "contentUrl": None,
                "content": card,
            }
        ],
    }
