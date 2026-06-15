"""Structured JSON logging with wide events.

Implements the "wide event" pattern where each request emits one comprehensive
JSON event containing all context - user info, DNS operation details, timing,
and outcome.

See: https://loggingsucks.com/
"""

import json
import logging
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dns_zone_manager import __version__


def get_request_id() -> str:
    """Generate a unique request ID."""
    return f"req_{uuid.uuid4().hex[:12]}"


@dataclass
class WideEvent:
    """Wide event that accumulates context throughout a request lifecycle.

    This is attached to request.state and enriched by handlers as they process.
    At the end of the request, it's emitted as a single comprehensive JSON log.
    """

    request_id: str = field(default_factory=get_request_id)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    service: str = "dns-api"
    version: str = field(default_factory=lambda: __version__)

    # Request context (set by middleware)
    request: dict[str, Any] = field(default_factory=dict)

    # User context (set by auth)
    user: dict[str, Any] = field(default_factory=dict)

    # DNS operation context (set by handlers)
    dns: dict[str, Any] = field(default_factory=dict)

    # Response context (set by middleware at end)
    response: dict[str, Any] = field(default_factory=dict)

    # Error context (set on exceptions)
    error: dict[str, Any] | None = None

    # Additional custom fields
    extra: dict[str, Any] = field(default_factory=dict)

    def set_request(
        self,
        method: str,
        path: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
        query_params: dict[str, str] | None = None,
    ) -> None:
        """Set request context."""
        self.request = {
            "id": self.request_id,
            "method": method,
            "path": path,
            "client_ip": client_ip,
            "user_agent": user_agent,
        }
        if query_params:
            self.request["query_params"] = query_params

    def set_user(
        self,
        user_id: str,
        auth_type: str,
        name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> None:
        """Set user context from authentication."""
        self.user = {
            "id": user_id,
            "auth_type": auth_type,
        }
        if name:
            self.user["name"] = name
        if email:
            self.user["email"] = email
        if roles:
            self.user["roles"] = roles

    def set_dns(self, **kwargs: Any) -> None:
        """Set DNS operation context.

        Common fields: zone, operation, name, rdtype, ttl, records,
        prereq_check, cache_hit, serial, etc.
        """
        self.dns.update(kwargs)

    def set_response(
        self,
        status_code: int,
        duration_ms: float,
        outcome: str = "success",
    ) -> None:
        """Set response context."""
        self.response = {
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "outcome": outcome,
        }

    def set_error(
        self,
        error_type: str,
        message: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Set error context."""
        self.error = {
            "type": error_type,
            "message": message,
        }
        if code:
            self.error["code"] = code
        if details:
            self.error["details"] = details

    def add_extra(self, key: str, value: Any) -> None:
        """Add custom extra field."""
        self.extra[key] = value

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "timestamp": self.timestamp,
            "level": "ERROR" if self.error else "INFO",
            "event": "request_completed",
            "service": self.service,
            "version": self.version,
        }

        if self.request:
            result["request"] = self.request

        if self.user:
            result["user"] = self.user

        if self.dns:
            result["dns"] = self.dns

        if self.response:
            result["response"] = self.response

        if self.error:
            result["error"] = self.error

        if self.extra:
            result.update(self.extra)

        return result


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Outputs each log record as a single JSON line.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        # If the message is already a dict (wide event), use it directly
        if isinstance(record.msg, dict):
            log_dict = record.msg
        else:
            # Standard log message - wrap in structured format
            log_dict: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }

            # Add exception info if present
            if record.exc_info:
                log_dict["exception"] = self.formatException(record.exc_info)

            # Add extra fields from record
            for key in ["request_id", "zone", "operation", "user_id"]:
                if hasattr(record, key):
                    log_dict[key] = getattr(record, key)

        return json.dumps(log_dict, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable text formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as human-readable text."""
        if isinstance(record.msg, dict):
            # Wide event - format nicely for humans
            event = record.msg
            parts = [
                f"{event.get('timestamp', '')}",
                f"{event.get('level', 'INFO'):5}",
            ]

            if "request" in event:
                req = event["request"]
                parts.append(f"{req.get('method', '')} {req.get('path', '')}")

            if "user" in event:
                parts.append(f"user={event['user'].get('id', 'anonymous')}")

            if "dns" in event:
                dns = event["dns"]
                op = dns.get("operation", "")
                zone = dns.get("zone", "")
                if op and zone:
                    parts.append(f"{op}@{zone}")

            if "response" in event:
                resp = event["response"]
                status = resp.get("status_code", "")
                duration = resp.get("duration_ms", "")
                parts.append(f"status={status} {duration}ms")

            if "error" in event and event["error"]:
                parts.append(f"ERROR: {event['error'].get('message', '')}")

            return " | ".join(parts)
        else:
            # Standard log message
            return super().format(record)


def should_sample(
    wide_event: WideEvent,
    sample_rate: float = 0.1,
    slow_threshold_ms: float = 1000.0,
) -> bool:
    """Determine if this event should be logged (tail sampling).

    Always logs:
    - Errors (status >= 400)
    - Slow requests (duration > threshold)
    - Mutations (POST, PUT, DELETE)

    Samples at configured rate:
    - Successful GET requests

    Args:
        wide_event: The wide event to evaluate
        sample_rate: Sampling rate for successful GET requests (0.0-1.0)
        slow_threshold_ms: Threshold in ms above which requests are always logged

    Returns:
        True if the event should be logged
    """
    # Always log errors
    if wide_event.error:
        return True

    # Always log if status code indicates error
    status = wide_event.response.get("status_code", 200)
    if status >= 400:
        return True

    # Always log slow requests
    duration = wide_event.response.get("duration_ms", 0)
    if duration > slow_threshold_ms:
        return True

    # Always log mutations (POST, PUT, DELETE, PATCH)
    method = wide_event.request.get("method", "GET")
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        return True

    # Sample successful reads
    return random.random() < sample_rate


def configure_logging(
    log_format: str = "json",
    log_level: str = "INFO",
) -> None:
    """Configure application logging.

    Args:
        log_format: "json" for structured JSON output, "text" for human-readable
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level.upper()))

    # Set formatter based on format preference
    if log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def emit_wide_event(wide_event: WideEvent, logger: logging.Logger | None = None) -> None:
    """Emit a wide event to the log.

    Args:
        wide_event: The wide event to emit
        logger: Logger to use (defaults to dns_zone_manager logger)
    """
    if logger is None:
        logger = logging.getLogger("dns_zone_manager")

    log_dict = wide_event.to_dict()

    # Use appropriate log level based on error state
    if wide_event.error or wide_event.response.get("status_code", 200) >= 500:
        logger.error(log_dict)
    elif wide_event.response.get("status_code", 200) >= 400:
        logger.warning(log_dict)
    else:
        logger.info(log_dict)


def log_internal_event(
    event_type: str,
    logger: logging.Logger,
    level: str = "INFO",
    **kwargs: Any,
) -> None:
    """Log an internal (non-request) event in structured format.

    Use this for background operations like cache refresh, AXFR, etc.

    Args:
        event_type: Type of event (e.g., "axfr_complete", "cache_refresh")
        logger: Logger to use
        level: Log level
        **kwargs: Additional fields to include
    """
    log_dict: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "event": event_type,
        "service": "dns-api",
        **kwargs,
    }

    log_level = getattr(logging, level.upper())
    logger.log(log_level, log_dict)
