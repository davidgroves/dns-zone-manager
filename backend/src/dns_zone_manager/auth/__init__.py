"""Authentication modules for DNS API."""

from dns_zone_manager.auth.api_key import api_key_auth
from dns_zone_manager.auth.combined import get_current_user

__all__ = ["api_key_auth", "get_current_user"]
