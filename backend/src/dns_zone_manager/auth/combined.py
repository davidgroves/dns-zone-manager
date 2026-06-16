"""Combined authentication supporting both Azure AD and API keys."""

import base64
import json
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status

from dns_zone_manager.auth.api_key import APIKeyUser, api_key_header, validate_api_key
from dns_zone_manager.auth.azure import AzureUser, azure_scheme, get_azure_scheme
from dns_zone_manager.auth.proxy import ProxyUser, proxy_user_from_request
from dns_zone_manager.config import get_settings

logger = logging.getLogger(__name__)


def enrich_user_context(request: Request, user: "AuthenticatedUser") -> None:
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


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT claims without verification (for extracting user info).

    Note: This does not verify the token signature. Verification should be
    done by fastapi-azure-auth's dependency injection.

    Args:
        token: JWT token string

    Returns:
        Decoded claims dict, or empty dict on failure
    """
    try:
        # JWT has 3 parts: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        # Decode the payload (middle part)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


@dataclass
class AuthenticatedUser:
    """Represents an authenticated user from any auth method."""

    user_id: str
    auth_type: str  # "azure_ad", "api_key", "proxy", or "none"
    name: str | None = None
    email: str | None = None
    roles: list[str] | None = None

    @classmethod
    def from_proxy(cls, user: "ProxyUser") -> "AuthenticatedUser":
        """Create from a trusted reverse-proxy (forward-auth) user."""
        return cls(
            user_id=user.user_id,
            auth_type="proxy",
            name=user.name,
            email=user.email,
            roles=["proxy_user"],
        )

    @classmethod
    def from_azure(cls, user: AzureUser) -> "AuthenticatedUser":
        """Create from Azure AD user."""
        return cls(
            user_id=user.user_id,
            auth_type="azure_ad",
            name=user.name,
            email=user.email,
            roles=user.roles,
        )

    @classmethod
    def from_api_key(cls, user: APIKeyUser) -> "AuthenticatedUser":
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
    bearer_token: Annotated[str | None, Depends(azure_scheme)] = None,
) -> AuthenticatedUser:
    """Get the current authenticated user from any enabled auth method.

    Tries trusted proxy header, then API key, then Azure AD. At least one
    must succeed (unless no auth method is enabled, which allows anonymous).

    Args:
        request: The incoming request (used for proxy header auth)
        api_key: API key from header
        bearer_token: Bearer token for Azure AD

    Returns:
        AuthenticatedUser

    Raises:
        HTTPException: If no valid authentication provided
    """
    settings = get_settings()

    # Check if any auth method is enabled
    if (
        not settings.api_key.enabled
        and not settings.azure_ad.enabled
        and not settings.proxy_auth.enabled
    ):
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

    # Try Azure AD authentication
    if bearer_token is not None and settings.azure_ad.enabled:
        scheme = get_azure_scheme()
        if scheme is not None:
            try:
                # Extract user info from JWT claims
                # Note: Token verification is handled by fastapi-azure-auth
                claims = _decode_jwt_claims(bearer_token)

                # Extract user identifier - prefer email/upn for logging
                user_id = claims.get("oid", claims.get("sub", "azure_user"))
                email = claims.get("preferred_username", claims.get("email", claims.get("upn")))
                name = claims.get("name")
                roles = claims.get("roles", [])

                # Use email as user_id for better log readability if available
                display_id = email or user_id

                azure_user = AzureUser(
                    user_id=display_id,
                    name=name,
                    email=email,
                    roles=roles,
                )
                return AuthenticatedUser.from_azure(azure_user)

            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Azure AD token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

    # No valid authentication provided
    auth_methods = []
    if settings.proxy_auth.enabled:
        auth_methods.append(f"Trusted proxy header ({settings.proxy_auth.user_header})")
    if settings.api_key.enabled:
        auth_methods.append("API Key (X-API-Key header)")
    if settings.azure_ad.enabled:
        auth_methods.append("Azure AD (Bearer token)")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Authentication required. Supported methods: {', '.join(auth_methods)}",
        headers={"WWW-Authenticate": "Bearer, ApiKey"},
    )


async def get_optional_user(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
    bearer_token: Annotated[str | None, Depends(azure_scheme)] = None,
) -> AuthenticatedUser | None:
    """Get current user if authenticated, None otherwise.

    Unlike get_current_user, this doesn't raise an error if no auth is provided.

    Args:
        request: The incoming request (used for proxy header auth)
        api_key: API key from header
        bearer_token: Bearer token for Azure AD

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

    # Try Azure AD authentication
    if bearer_token is not None and settings.azure_ad.enabled:
        try:
            scheme = get_azure_scheme()
            if scheme is not None:
                claims = _decode_jwt_claims(bearer_token)
                user_id = claims.get("oid", claims.get("sub", "azure_user"))
                email = claims.get("preferred_username", claims.get("email", claims.get("upn")))
                display_id = email or user_id
                azure_user = AzureUser(
                    user_id=display_id,
                    name=claims.get("name"),
                    email=email,
                    roles=claims.get("roles", []),
                )
                return AuthenticatedUser.from_azure(azure_user)
        except Exception:
            return None

    return None
