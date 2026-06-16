"""Trusted reverse-proxy (forward-auth) header authentication.

When ``proxy_auth`` is enabled, a front proxy (e.g. Traefik + oauth2-proxy)
authenticates the user and injects an identity header. The app trusts that
header and uses it for authorization and audit logging.

Security: this is only safe when the backend is reachable solely through the
trusted proxy, which must overwrite the configured headers on every request so
that clients cannot spoof an identity.
"""

import logging

from fastapi import Request

from dns_zone_manager.config import get_settings

logger = logging.getLogger(__name__)


class ProxyUser:
    """Represents a user authenticated by a trusted front proxy."""

    def __init__(self, email: str, name: str | None = None):
        self.user_id = email
        self.email = email
        self.name = name or email
        self.auth_type = "proxy"

    def __repr__(self) -> str:
        return f"ProxyUser(email={self.email!r})"


def proxy_user_from_request(request: Request) -> ProxyUser | None:
    """Extract the proxy-authenticated user from the request headers.

    Args:
        request: The incoming FastAPI request

    Returns:
        ProxyUser if proxy auth is enabled and the identity header is present,
        None otherwise.
    """
    settings = get_settings()

    if not settings.proxy_auth.enabled:
        return None

    email = request.headers.get(settings.proxy_auth.user_header)
    if not email:
        return None

    name = None
    if settings.proxy_auth.name_header:
        name = request.headers.get(settings.proxy_auth.name_header)

    logger.debug(f"Proxy header authentication: {email}")
    return ProxyUser(email=email, name=name)
