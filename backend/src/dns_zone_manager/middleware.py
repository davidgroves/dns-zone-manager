"""FastAPI middleware for wide event logging.

Creates a WideEvent at request start, attaches it to request.state,
and emits it at the end of the request with full context.
"""

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from dns_zone_manager.logging import WideEvent, emit_wide_event, should_sample

logger = logging.getLogger(__name__)


class WideEventMiddleware(BaseHTTPMiddleware):
    """Middleware that creates and emits wide events for each request.

    This middleware:
    1. Creates a WideEvent at request start with request context
    2. Attaches it to request.state.wide_event for handlers to enrich
    3. Captures response status and duration at completion
    4. Applies tail sampling before emission
    5. Emits the wide event via structured logging
    """

    def __init__(
        self,
        app: ASGIApp,
        sample_rate: float = 0.1,
        slow_threshold_ms: float = 1000.0,
        exclude_paths: list[str] | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            app: The ASGI application
            sample_rate: Sampling rate for successful GET requests (0.0-1.0)
            slow_threshold_ms: Threshold in ms above which requests are always logged
            exclude_paths: Paths to exclude from wide event logging (e.g., /health)
        """
        super().__init__(app)
        self.sample_rate = sample_rate
        self.slow_threshold_ms = slow_threshold_ms
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/redoc", "/openapi.json"]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process the request and emit wide event."""
        # Skip excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip static files
        if request.url.path.startswith("/static"):
            return await call_next(request)

        # Create wide event and attach to request state
        wide_event = WideEvent()

        # Get client IP (handle proxies)
        client_ip = request.client.host if request.client else None
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # Set request context
        wide_event.set_request(
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            query_params=dict(request.query_params) if request.query_params else None,
        )

        # Attach to request state for handlers to enrich
        request.state.wide_event = wide_event

        # Record start time
        start_time = time.perf_counter()

        # Process the request
        try:
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Set response context
            outcome = "success" if response.status_code < 400 else "error"
            wide_event.set_response(
                status_code=response.status_code,
                duration_ms=duration_ms,
                outcome=outcome,
            )

            # Apply tail sampling and emit
            if should_sample(wide_event, self.sample_rate, self.slow_threshold_ms):
                emit_wide_event(wide_event)

            return response

        except Exception as e:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Set error context
            wide_event.set_error(
                error_type=type(e).__name__,
                message=str(e),
            )

            # Set response context for errors
            wide_event.set_response(
                status_code=500,
                duration_ms=duration_ms,
                outcome="error",
            )

            # Always emit errors
            emit_wide_event(wide_event)

            raise


def get_wide_event(request: Request) -> WideEvent | None:
    """Get the wide event from request state if available.

    Helper function for handlers to access the wide event.

    Args:
        request: The FastAPI request

    Returns:
        The WideEvent if available, None otherwise
    """
    return getattr(request.state, "wide_event", None)


def enrich_dns_context(request: Request, **kwargs) -> None:
    """Helper to enrich wide event with DNS context.

    Args:
        request: The FastAPI request
        **kwargs: DNS context fields (zone, operation, name, rdtype, etc.)
    """
    wide_event = get_wide_event(request)
    if wide_event:
        wide_event.set_dns(**kwargs)


def enrich_error_context(
    request: Request,
    error_type: str,
    message: str,
    code: str | None = None,
    details: dict | None = None,
) -> None:
    """Helper to enrich wide event with error context.

    Args:
        request: The FastAPI request
        error_type: Type of error
        message: Error message
        code: Optional error code
        details: Optional additional details
    """
    wide_event = get_wide_event(request)
    if wide_event:
        wide_event.set_error(error_type, message, code, details)
