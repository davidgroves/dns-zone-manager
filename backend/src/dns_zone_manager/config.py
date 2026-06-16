"""Configuration settings for DNS Zone Manager."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environment variable name for config file path (survives uvicorn reload)
_CONFIG_FILE_ENV_VAR = "DNS_ZONE_MANAGER_CONFIG_FILE"

# Module-level config file path (set via set_config_file before importing app)
_config_file: Path | None = None


def set_config_file(path: Path | None) -> None:
    """Set the config file path. Must be called before get_settings()."""
    global _config_file
    _config_file = path
    # Also set environment variable so it survives uvicorn reload
    if path:
        os.environ[_CONFIG_FILE_ENV_VAR] = str(path)
    elif _CONFIG_FILE_ENV_VAR in os.environ:
        del os.environ[_CONFIG_FILE_ENV_VAR]
    # Clear the cached settings so they'll be reloaded
    get_settings.cache_clear()


def get_config_file() -> Path | None:
    """Get the currently configured config file path.

    Checks module global first, then falls back to environment variable
    (needed for uvicorn reload which starts a fresh process).
    """
    global _config_file
    if _config_file:
        return _config_file
    # Check environment variable (survives uvicorn reload)
    env_path = os.environ.get(_CONFIG_FILE_ENV_VAR)
    if env_path:
        _config_file = Path(env_path)
        return _config_file
    return None


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Load configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file

    Returns:
        Dictionary of configuration values

    Raises:
        FileNotFoundError: If the config file doesn't exist
        yaml.YAMLError: If the YAML is invalid
    """
    with open(path) as f:
        config = yaml.safe_load(f)
    return config if config else {}


class TSIGKeyEntry(BaseModel):
    """A single TSIG key definition.

    TSIG keys are used for authenticating DNS operations like DDNS updates,
    zone transfers (AXFR), and NOTIFY message validation.
    """

    name: str = Field(
        description="Key name (used to reference this key from other config sections)"
    )
    secret: SecretStr = Field(description="Base64-encoded TSIG secret")
    algorithm: str = Field(
        default="hmac-sha256",
        description="TSIG algorithm (hmac-sha256, hmac-sha384, hmac-sha512, hmac-md5)",
    )


class AzureADSettings(BaseSettings):
    """Azure AD authentication settings."""

    model_config = SettingsConfigDict(env_prefix="AZURE_AD_")

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("enabled", "AZURE_AD_AUTH_ENABLED"),
        description="Enable Azure AD authentication",
    )
    tenant_id: str = Field(default="", description="Azure AD tenant ID")
    client_id: str = Field(default="", description="Azure AD application client ID")
    scopes: list[str] = Field(
        default_factory=lambda: ["api://dns-api/.default"],
        description="Required OAuth2 scopes",
    )


class ProxyAuthSettings(BaseSettings):
    """Trusted reverse-proxy (forward-auth) header authentication.

    When enabled, the app trusts an identity header set by a front proxy
    (e.g. Traefik + oauth2-proxy) and uses it as the authenticated user for
    audit logging. This is ONLY safe when the backend is reachable solely via
    that proxy, since the proxy must overwrite the header on every request so
    clients cannot spoof it.
    """

    model_config = SettingsConfigDict(env_prefix="PROXY_AUTH_")

    enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("enabled", "PROXY_AUTH_ENABLED"),
        description="Trust an identity header set by a front proxy",
    )
    user_header: str = Field(
        default="X-Auth-Request-Email",
        description="Request header carrying the authenticated user identity (email)",
    )
    name_header: str = Field(
        default="X-Auth-Request-Preferred-Username",
        description="Request header carrying the user's display name (optional)",
    )


class APIKeyEntry(BaseModel):
    """A single API key entry with name and secret."""

    name: str = Field(description="Name/identifier for the API key (used in audit logs)")
    secret: SecretStr = Field(description="The API key secret")


class APIKeySettings(BaseSettings):
    """API key authentication settings."""

    model_config = SettingsConfigDict(env_prefix="API_")

    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("enabled", "API_KEY_AUTH_ENABLED"),
        description="Enable API key authentication",
    )
    keys_str: str = Field(
        default="",
        validation_alias=AliasChoices("keys_str", "keys", "API_KEYS"),
        description="Comma-separated list of name:secret API key pairs (env var format)",
    )
    keys_list: list[APIKeyEntry] = Field(
        default_factory=list,
        description="List of API key entries (YAML format)",
    )
    header_name: str = Field(default="X-API-Key", description="Header name for API key")

    @property
    def keys(self) -> dict[str, SecretStr]:
        """Get the API keys as a name -> SecretStr mapping.

        Merges keys from both formats:
        - keys_list: List of {name, secret} objects (from YAML)
        - keys_str: Comma-separated name:secret pairs (from env vars)

        YAML keys take precedence over env var keys with the same name.
        """
        result: dict[str, SecretStr] = {}

        # First, parse env var format (keys_str)
        if self.keys_str:
            for entry in self.keys_str.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if ":" in entry:
                    name, secret = entry.split(":", 1)
                    result[name.strip()] = SecretStr(secret.strip())

        # Then, add YAML format keys (overwriting any duplicates)
        for key_entry in self.keys_list:
            result[key_entry.name] = key_entry.secret

        return result


class DNSSettings(BaseSettings):
    """DNS server configuration."""

    model_config = SettingsConfigDict(env_prefix="DNS_")

    server: str = Field(..., description="DNS server hostname or IP")
    port: int = Field(default=53, description="DNS server port (UDP for queries)")
    tcp_port: int | None = Field(
        default=None,
        description="DNS TCP port for AXFR/DDNS (defaults to port if not set)",
    )
    timeout: float = Field(default=10.0, description="DNS query timeout in seconds")
    axfr_timeout: float = Field(default=60.0, description="AXFR transfer timeout in seconds")

    # TSIG key references (names of keys defined in tsig_keys section)
    update_tsig_key: str = Field(
        default="default",
        description="Name of TSIG key for DDNS updates (references tsig_keys entry)",
    )
    axfr_tsig_key: str | None = Field(
        default=None,
        description="Name of TSIG key for AXFR transfers (defaults to update_tsig_key if not set)",
    )

    @property
    def effective_tcp_port(self) -> int:
        """Get the TCP port, falling back to main port if not set."""
        return self.tcp_port if self.tcp_port is not None else self.port

    @property
    def effective_axfr_tsig_key(self) -> str:
        """Get the AXFR TSIG key name, falling back to update key if not set."""
        return self.axfr_tsig_key if self.axfr_tsig_key is not None else self.update_tsig_key


class CacheSettings(BaseSettings):
    """Zone cache configuration."""

    model_config = SettingsConfigDict(env_prefix="CACHE_")

    enabled: bool = Field(default=True, description="Enable zone caching")
    min_refresh_interval: int = Field(
        default=60,
        description="Minimum zone refresh interval in seconds (floor for SOA refresh)",
    )
    max_refresh_interval: int = Field(
        default=86400,  # 24 hours
        description="Maximum zone refresh interval in seconds (ceiling for SOA refresh)",
    )
    max_size_bytes: int = Field(
        default=1_073_741_824,  # 1 GB
        description="Maximum cache size in bytes (0 = unlimited)",
    )


class CatalogZoneSettings(BaseSettings):
    """Catalog zone auto-discovery configuration."""

    model_config = SettingsConfigDict(env_prefix="CATALOG_")

    enabled: bool = Field(default=False, description="Enable catalog zone auto-discovery")
    zone_name: str = Field(default="", description="Catalog zone name (e.g., catalog.example.com)")
    poll_interval: float = Field(
        default=300.0,
        description="Interval in seconds for polling catalog zone SOA serial",
    )
    notify_bind_address: str = Field(
        default="0.0.0.0",
        description="Address to bind NOTIFY listeners to",
    )
    notify_udp_port: int = Field(
        default=5354,
        description="UDP port for NOTIFY listener (different from main DNS port)",
    )
    notify_tcp_port: int = Field(
        default=5354,
        description="TCP port for NOTIFY listener (different from main DNS port)",
    )
    auto_load_zones: bool = Field(
        default=True,
        description="Automatically AXFR discovered zones into cache",
    )
    remove_stale_zones: bool = Field(
        default=False,
        description="Remove zones from cache when removed from catalog",
    )


class LoggingSettings(BaseSettings):
    """Logging configuration for structured JSON wide events."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    format: str = Field(
        default="json",
        description="Log format: 'json' for structured output, 'text' for human-readable",
    )
    level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    sample_rate: float = Field(
        default=0.1,
        description="Sample rate for successful GET requests (0.0-1.0)",
    )
    slow_threshold_ms: int = Field(
        default=1000,
        description="Always log requests slower than this (milliseconds)",
    )


class NotifySettings(BaseSettings):
    """NOTIFY listener configuration for zone change notifications."""

    model_config = SettingsConfigDict(env_prefix="NOTIFY_")

    enabled: bool = Field(
        default=True,
        description="Enable NOTIFY listener for zone change notifications",
    )
    bind_address: str = Field(
        default="0.0.0.0",
        description="Address to bind NOTIFY listeners to",
    )
    udp_port: int = Field(
        default=5354,
        description="UDP port for NOTIFY listener",
    )
    tcp_port: int = Field(
        default=5354,
        description="TCP port for NOTIFY listener",
    )
    prefer_ixfr: bool = Field(
        default=True,
        description="Prefer IXFR over AXFR when refreshing zones",
    )
    require_tsig: bool = Field(
        default=False,
        description="Require valid TSIG signature on incoming NOTIFY messages",
    )
    # TSIG key reference (name of key defined in tsig_keys section)
    tsig_key: str | None = Field(
        default=None,
        description="Name of TSIG key for NOTIFY validation (references tsig_keys entry)",
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Application settings
    app_name: str = Field(default="DNS Zone Manager")
    debug: bool = Field(default=False)

    # TSIG keys (referenced by name from dns and notify sections)
    tsig_keys: list[TSIGKeyEntry] = Field(
        default_factory=list,
        description="List of TSIG keys for DNS authentication",
    )

    # DNS settings
    dns: DNSSettings = Field(default_factory=DNSSettings)

    # Authentication settings
    azure_ad: AzureADSettings = Field(default_factory=AzureADSettings)
    api_key: APIKeySettings = Field(default_factory=APIKeySettings)
    proxy_auth: ProxyAuthSettings = Field(default_factory=ProxyAuthSettings)

    # Cache settings
    cache: CacheSettings = Field(default_factory=CacheSettings)

    # Catalog zone settings
    catalog: CatalogZoneSettings = Field(default_factory=CatalogZoneSettings)

    # NOTIFY listener settings
    notify: NotifySettings = Field(default_factory=NotifySettings)

    # Logging settings
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    def get_tsig_key(self, name: str) -> TSIGKeyEntry | None:
        """Look up a TSIG key by name.

        Args:
            name: The key name to look up

        Returns:
            TSIGKeyEntry if found, None otherwise
        """
        for key in self.tsig_keys:
            if key.name == name:
                return key
        return None

    def get_update_tsig_key(self) -> TSIGKeyEntry | None:
        """Get the TSIG key configured for DDNS updates."""
        return self.get_tsig_key(self.dns.update_tsig_key)

    def get_axfr_tsig_key(self) -> TSIGKeyEntry | None:
        """Get the TSIG key configured for AXFR transfers."""
        return self.get_tsig_key(self.dns.effective_axfr_tsig_key)

    def get_notify_tsig_key(self) -> TSIGKeyEntry | None:
        """Get the TSIG key configured for NOTIFY validation.

        Falls back to update TSIG key if notify.tsig_key is not set.
        """
        if self.notify.tsig_key:
            return self.get_tsig_key(self.notify.tsig_key)
        # Fall back to update key
        return self.get_update_tsig_key()

    @model_validator(mode="after")
    def validate_tsig_key_references(self) -> "Settings":
        """Validate that all referenced TSIG keys exist.

        Only validates when tsig_keys are defined. This allows backward
        compatibility with env-var-only configurations where the key name
        is implicit.
        """
        if not self.tsig_keys:
            # No keys defined - skip validation (legacy/env-var mode)
            return self

        key_names = {key.name for key in self.tsig_keys}

        # Check update key reference
        if self.dns.update_tsig_key and self.dns.update_tsig_key not in key_names:
            raise ValueError(
                f"dns.update_tsig_key references unknown key '{self.dns.update_tsig_key}'. "
                f"Available keys: {sorted(key_names)}"
            )

        # Check AXFR key reference (only if explicitly set)
        if self.dns.axfr_tsig_key and self.dns.axfr_tsig_key not in key_names:
            raise ValueError(
                f"dns.axfr_tsig_key references unknown key '{self.dns.axfr_tsig_key}'. "
                f"Available keys: {sorted(key_names)}"
            )

        # Check notify key reference (only if explicitly set)
        if self.notify.tsig_key and self.notify.tsig_key not in key_names:
            raise ValueError(
                f"notify.tsig_key references unknown key '{self.notify.tsig_key}'. "
                f"Available keys: {sorted(key_names)}"
            )

        return self

    @classmethod
    def load(cls, config_file: Path) -> "Settings":
        """Load settings from a YAML config file.

        Args:
            config_file: Path to YAML configuration file

        Returns:
            Settings instance from the config file
        """
        yaml_config = load_yaml_config(config_file)

        # Parse TSIG keys
        tsig_keys: list[TSIGKeyEntry] = []
        for key_data in yaml_config.get("tsig_keys", []):
            tsig_keys.append(TSIGKeyEntry.model_validate(key_data))

        # Handle api_key section - support keys list format
        api_key_yaml = yaml_config.get("api_key", yaml_config.get("api_keys", {})) or {}
        if "keys" in api_key_yaml and isinstance(api_key_yaml["keys"], list):
            # Convert YAML keys list to keys_list format
            api_key_yaml["keys_list"] = api_key_yaml.pop("keys")

        # Helper to create settings from YAML section
        def from_yaml[T: BaseModel](model_cls: type[T], section: dict[str, Any] | None) -> T:
            """Create model instance from YAML section, using defaults for missing fields."""
            return model_cls.model_validate(section or {})

        return cls(
            app_name=yaml_config.get("app_name", "DNS Zone Manager"),
            debug=yaml_config.get("debug", False),
            tsig_keys=tsig_keys,
            dns=from_yaml(DNSSettings, yaml_config.get("dns")),
            azure_ad=from_yaml(AzureADSettings, yaml_config.get("azure_ad")),
            api_key=from_yaml(APIKeySettings, api_key_yaml if api_key_yaml else None),
            proxy_auth=from_yaml(ProxyAuthSettings, yaml_config.get("proxy_auth")),
            cache=from_yaml(CacheSettings, yaml_config.get("cache")),
            catalog=from_yaml(CatalogZoneSettings, yaml_config.get("catalog")),
            notify=from_yaml(NotifySettings, yaml_config.get("notify")),
            logging=from_yaml(LoggingSettings, yaml_config.get("logging")),
        )


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Requires a config file to be set via set_config_file() first.
    """
    config_file = get_config_file()
    if not config_file:
        raise RuntimeError(
            "No config file set. Use --config option or call set_config_file() first.\n"
            "Example: dns-zone-manager --config examples/config.yaml"
        )
    return Settings.load(config_file)
