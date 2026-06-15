"""API key authentication."""

import logging
import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from dns_zone_manager.config import get_settings

logger = logging.getLogger(__name__)

# API key header scheme
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API key for authentication",
)


class APIKeyUser:
    """Represents an API key authenticated user."""

    def __init__(self, key_name: str):
        """Initialize API key user.

        Args:
            key_name: The configured name for this API key
        """
        self.key_id = key_name
        self.key_name = key_name
        self.auth_type = "api_key"

    def __repr__(self) -> str:
        return f"APIKeyUser(key_name={self.key_name!r})"


def validate_api_key(
    api_key: Annotated[str | None, Security(api_key_header)],
) -> APIKeyUser | None:
    """Validate API key from header.

    Args:
        api_key: API key from X-API-Key header

    Returns:
        APIKeyUser if valid, None if no key provided

    Raises:
        HTTPException: If key is invalid
    """
    settings = get_settings()

    if not settings.api_key.enabled:
        return None

    if api_key is None:
        return None

    # Check against configured keys using constant-time comparison
    for key_name, valid_key in settings.api_key.keys.items():
        if secrets.compare_digest(api_key, valid_key.get_secret_value()):
            logger.debug(f"API key authentication successful: {key_name}")
            return APIKeyUser(key_name=key_name)

    # Invalid key provided
    logger.warning("Invalid API key provided")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def api_key_auth(
    api_key: Annotated[str | None, Security(api_key_header)],
) -> APIKeyUser | None:
    """Dependency for API key authentication.

    Args:
        api_key: API key from header

    Returns:
        APIKeyUser if authenticated, None otherwise
    """
    return validate_api_key(api_key)
