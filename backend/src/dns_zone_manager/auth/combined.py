"""Combined authentication supporting API keys and trusted reverse-proxy headers."""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import HTTPException, Request, Security, status

from dns_zone_manager.auth.api_key import APIKeyUser, api_key_header, validate_api_key
from dns_zone_manager.auth.proxy import ProxyUser, proxy_user_from_request
from dns_zone_manager.config import get_settings
from dns_zone_manager.notifications.context import (
    TRIGGER_MANUAL,
    ChangeContext,
    set_change_context,
)

logger = logging.getLogger(__name__)


def enrich_user_context(request: Request, user: AuthenticatedUser) -> None:
    """Enrich the wide event with user context.

    Args:
        request: The FastAPI request (must have wide_event in state)
        user: The authenticated user
    """
    if hasattr(request.state, "wide_event"):
        request.state.wide_event.set_user(
            user_id=user.user_id,
            auth_type=user.auth_type,
            name=user.name,
            email=user.email,
            roles=user.roles,
        )


@dataclass
class AuthenticatedUser:
    """Represents an authenticated user from any auth method."""

    user_id: str
    auth_type: str  # "api_key", "proxy", or "none"
    name: str | None = None
    email: str | None = None
    roles: list[str] | None = None

    @classmethod
    def from_proxy(cls, user: ProxyUser) -> AuthenticatedUser:
        """Create from a trusted reverse-proxy (forward-auth) user."""
        return cls(
            user_id=user.user_id,
            auth_type="proxy",
            name=user.name,
            email=user.email,
            roles=["proxy_user"],
        )

    @classmethod
    def from_api_key(cls, user: APIKeyUser) -> AuthenticatedUser:
        """Create from API key user."""
        return cls(
            user_id=user.key_name,
            auth_type="api_key",
            name=f"API Key: {user.key_name}",
            email=None,
            roles=["api_key_user"],
        )


async def get_current_user(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> AuthenticatedUser:
    """Get the current authenticated user from any enabled auth method.

    Tries trusted proxy header, then API key. At least one must succeed
    (unless no auth method is enabled, which allows anonymous).

    Also records the caller in the task-scoped change context so DNS writes
    made while handling this request can be attributed without every handler
    having to pass the user down.

    Args:
        request: The incoming request (used for proxy header auth)
        api_key: API key from header

    Returns:
        AuthenticatedUser

    Raises:
        HTTPException: If no valid authentication provided
    """
    user = await _authenticate(request, api_key)

    state = getattr(request, "state", None)
    wide_event = getattr(state, "wide_event", None)
    set_change_context(
        ChangeContext(
            actor=user.user_id,
            actor_name=user.name,
            actor_email=user.email,
            auth_type=user.auth_type,
            trigger=TRIGGER_MANUAL,
            request_id=getattr(wide_event, "request_id", None),
        )
    )
    return user


async def _authenticate(
    request: Request,
    api_key: str | None,
) -> AuthenticatedUser:
    """Resolve the caller identity from the enabled auth methods."""
    settings = get_settings()

    # Check if any auth method is enabled
    if not settings.api_key.enabled and not settings.proxy_auth.enabled:
        # No auth configured - allow anonymous access
        return AuthenticatedUser(
            user_id="anonymous",
            auth_type="none",
            name="Anonymous",
        )

    # Try trusted reverse-proxy header authentication first
    if settings.proxy_auth.enabled:
        proxy_user = proxy_user_from_request(request)
        if proxy_user is not None:
            return AuthenticatedUser.from_proxy(proxy_user)

    # Try API key authentication
    if api_key is not None and settings.api_key.enabled:
        try:
            api_key_user = validate_api_key(api_key)
            if api_key_user is not None:
                return AuthenticatedUser.from_api_key(api_key_user)
        except HTTPException:
            # API key was provided but invalid
            raise

    # No valid authentication provided
    auth_methods = []
    if settings.proxy_auth.enabled:
        auth_methods.append(f"Trusted proxy header ({settings.proxy_auth.user_header})")
    if settings.api_key.enabled:
        auth_methods.append("API Key (X-API-Key header)")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Authentication required. Supported methods: {', '.join(auth_methods)}",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def get_optional_user(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> AuthenticatedUser | None:
    """Get current user if authenticated, None otherwise.

    Unlike get_current_user, this doesn't raise an error if no auth is provided.

    Args:
        request: The incoming request (used for proxy header auth)
        api_key: API key from header

    Returns:
        AuthenticatedUser if authenticated, None otherwise
    """
    settings = get_settings()

    # Try trusted reverse-proxy header authentication first
    if settings.proxy_auth.enabled:
        proxy_user = proxy_user_from_request(request)
        if proxy_user is not None:
            return AuthenticatedUser.from_proxy(proxy_user)

    # Try API key authentication
    if api_key is not None and settings.api_key.enabled:
        try:
            api_key_user = validate_api_key(api_key)
            if api_key_user is not None:
                return AuthenticatedUser.from_api_key(api_key_user)
        except HTTPException:
            return None

    return None
