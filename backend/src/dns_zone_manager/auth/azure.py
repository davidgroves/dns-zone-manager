"""Azure AD authentication using fastapi-azure-auth."""

import logging
from typing import TYPE_CHECKING

from fastapi import Depends
from fastapi.security import OAuth2AuthorizationCodeBearer

if TYPE_CHECKING:
    from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
    from fastapi_azure_auth.user import User

from dns_zone_manager.config import get_settings

logger = logging.getLogger(__name__)

# Lazy initialization of Azure auth scheme
_azure_scheme: "SingleTenantAzureAuthorizationCodeBearer | None" = None


def get_azure_scheme() -> "SingleTenantAzureAuthorizationCodeBearer | None":
    """Get the Azure authentication scheme, initializing if needed.

    Returns:
        Azure auth scheme if enabled, None otherwise
    """
    global _azure_scheme

    settings = get_settings()

    if not settings.azure_ad.enabled:
        return None

    if _azure_scheme is None:
        try:
            from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

            scope_uri = f"api://{settings.azure_ad.client_id}/DNS.ReadWrite"
            _azure_scheme = SingleTenantAzureAuthorizationCodeBearer(
                app_client_id=settings.azure_ad.client_id,
                tenant_id=settings.azure_ad.tenant_id,
                scopes={scope_uri: "Read and write DNS records"},
            )
            logger.info("Azure AD authentication configured")
        except ImportError:
            logger.warning("fastapi-azure-auth not installed, Azure AD auth disabled")
            return None
        except Exception as e:
            logger.error(f"Failed to configure Azure AD auth: {e}")
            return None

    return _azure_scheme


# Placeholder scheme for OpenAPI documentation when Azure is enabled
azure_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    tokenUrl="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    auto_error=False,
)


async def validate_azure_token(
    token: str | None = Depends(azure_scheme),
) -> "User | None":
    """Validate Azure AD token and return user info.

    Note: This function is a placeholder. In production, use the
    SingleTenantAzureAuthorizationCodeBearer as a FastAPI dependency directly.

    Args:
        token: Bearer token from Authorization header

    Returns:
        User object if valid, None if Azure auth is disabled or no token
    """
    settings = get_settings()

    if not settings.azure_ad.enabled:
        return None

    if token is None:
        return None

    # Note: For proper Azure AD validation, use the scheme as a FastAPI dependency
    # The actual validation happens via fastapi-azure-auth's dependency injection
    logger.debug("Azure token provided, validation delegated to combined auth")
    return None


class AzureUser:
    """Represents an authenticated Azure AD user."""

    def __init__(
        self,
        user_id: str,
        name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ):
        self.user_id = user_id
        self.name = name
        self.email = email
        self.roles = roles or []

    @classmethod
    def from_token_claims(cls, claims: dict) -> "AzureUser":
        """Create AzureUser from JWT claims.

        Args:
            claims: Decoded JWT claims

        Returns:
            AzureUser instance
        """
        return cls(
            user_id=claims.get("oid", claims.get("sub", "")),
            name=claims.get("name"),
            email=claims.get("preferred_username", claims.get("email")),
            roles=claims.get("roles", []),
        )

    def __repr__(self) -> str:
        return f"AzureUser(user_id={self.user_id!r}, email={self.email!r})"
