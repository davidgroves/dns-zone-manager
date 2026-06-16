"""Authentication tracking endpoints for Prometheus metrics."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from dns_zone_manager.auth.combined import AuthenticatedUser, get_current_user
from dns_zone_manager.metrics import logins_total, logouts_total
from dns_zone_manager.middleware import enrich_dns_context

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginEvent(BaseModel):
    """Login event for metrics tracking."""

    auth_type: Literal["api_key", "azure_ad", "proxy"]


class MetricsResponse(BaseModel):
    """Response for metrics tracking endpoints."""

    status: str = "ok"


@router.post(
    "/login",
    response_model=MetricsResponse,
    summary="Track login event",
    description="Track a UI login event for Prometheus metrics.",
)
async def track_login(
    http_request: Request,
    event: LoginEvent,
) -> MetricsResponse:
    """Track a login event for metrics.

    This endpoint is called by the UI after successful authentication
    to track login events for Prometheus metrics.

    Args:
        http_request: FastAPI request
        event: Login event with auth_type

    Returns:
        Success response
    """
    logins_total.labels(auth_type=event.auth_type).inc()

    # Enrich wide event with context
    enrich_dns_context(
        http_request,
        operation="track_login",
        auth_type=event.auth_type,
    )

    return MetricsResponse()


@router.post(
    "/logout",
    response_model=MetricsResponse,
    summary="Track logout event",
    description="Track a UI logout event for Prometheus metrics.",
)
async def track_logout(
    http_request: Request,
) -> MetricsResponse:
    """Track a logout event for metrics.

    This endpoint is called by the UI when a user logs out
    to track logout events for Prometheus metrics.

    Args:
        http_request: FastAPI request

    Returns:
        Success response
    """
    logouts_total.inc()

    # Enrich wide event with context
    enrich_dns_context(
        http_request,
        operation="track_logout",
    )

    return MetricsResponse()


class ValidateResponse(BaseModel):
    """Response for API key validation."""

    valid: bool = True
    user_id: str
    name: str | None
    auth_type: str


@router.get(
    "/validate",
    response_model=ValidateResponse,
    summary="Validate authentication",
    description="Validate the provided API key or token and return user information.",
)
async def validate_auth(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> ValidateResponse:
    """Validate authentication credentials.

    This endpoint requires valid authentication and returns user info.
    Used by the UI to verify API keys before allowing access.

    Args:
        user: Authenticated user from dependency

    Returns:
        Validation response with user info
    """
    return ValidateResponse(
        valid=True,
        user_id=user.user_id,
        name=user.name,
        auth_type=user.auth_type,
    )
