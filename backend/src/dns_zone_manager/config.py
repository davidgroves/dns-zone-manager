"""Configuration settings for DNS Zone Manager."""

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus, urlparse

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Strict colour validation for theme tokens that end up in a generated <style>
# element — reject anything that could break out of a CSS property value.
_HEX_COLOUR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CSS_NAMED_COLOURS = frozenset(
    {
        "aliceblue",
        "antiquewhite",
        "aqua",
        "aquamarine",
        "azure",
        "beige",
        "bisque",
        "black",
        "blanchedalmond",
        "blue",
        "blueviolet",
        "brown",
        "burlywood",
        "cadetblue",
        "chartreuse",
        "chocolate",
        "coral",
        "cornflowerblue",
        "cornsilk",
        "crimson",
        "cyan",
        "darkblue",
        "darkcyan",
        "darkgoldenrod",
        "darkgray",
        "darkgreen",
        "darkgrey",
        "darkkhaki",
        "darkmagenta",
        "darkolivegreen",
        "darkorange",
        "darkorchid",
        "darkred",
        "darksalmon",
        "darkseagreen",
        "darkslateblue",
        "darkslategray",
        "darkslategrey",
        "darkturquoise",
        "darkviolet",
        "deeppink",
        "deepskyblue",
        "dimgray",
        "dimgrey",
        "dodgerblue",
        "firebrick",
        "floralwhite",
        "forestgreen",
        "fuchsia",
        "gainsboro",
        "ghostwhite",
        "gold",
        "goldenrod",
        "gray",
        "green",
        "greenyellow",
        "grey",
        "honeydew",
        "hotpink",
        "indianred",
        "indigo",
        "ivory",
        "khaki",
        "lavender",
        "lavenderblush",
        "lawngreen",
        "lemonchiffon",
        "lightblue",
        "lightcoral",
        "lightcyan",
        "lightgoldenrodyellow",
        "lightgray",
        "lightgreen",
        "lightgrey",
        "lightpink",
        "lightsalmon",
        "lightseagreen",
        "lightskyblue",
        "lightslategray",
        "lightslategrey",
        "lightsteelblue",
        "lightyellow",
        "lime",
        "limegreen",
        "linen",
        "magenta",
        "maroon",
        "mediumaquamarine",
        "mediumblue",
        "mediumorchid",
        "mediumpurple",
        "mediumseagreen",
        "mediumslateblue",
        "mediumspringgreen",
        "mediumturquoise",
        "mediumvioletred",
        "midnightblue",
        "mintcream",
        "mistyrose",
        "moccasin",
        "navajowhite",
        "navy",
        "oldlace",
        "olive",
        "olivedrab",
        "orange",
        "orangered",
        "orchid",
        "palegoldenrod",
        "palegreen",
        "paleturquoise",
        "palevioletred",
        "papayawhip",
        "peachpuff",
        "peru",
        "pink",
        "plum",
        "powderblue",
        "purple",
        "rebeccapurple",
        "red",
        "rosybrown",
        "royalblue",
        "saddlebrown",
        "salmon",
        "sandybrown",
        "seagreen",
        "seashell",
        "sienna",
        "silver",
        "skyblue",
        "slateblue",
        "slategray",
        "slategrey",
        "snow",
        "springgreen",
        "steelblue",
        "tan",
        "teal",
        "thistle",
        "tomato",
        "turquoise",
        "violet",
        "wheat",
        "white",
        "whitesmoke",
        "yellow",
        "yellowgreen",
        "transparent",
    }
)
_LOGO_SUFFIXES = frozenset({".svg", ".png", ".jpg", ".jpeg", ".webp"})
_LOGO_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def validate_css_colour(value: str | None) -> str | None:
    """Accept only hex colours and CSS named colours (CSS-injection safe)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("colour must be a string")
    stripped = value.strip()
    if _HEX_COLOUR_RE.match(stripped) or stripped.lower() in _CSS_NAMED_COLOURS:
        return stripped
    raise ValueError(
        f"invalid CSS colour {value!r}: use #rgb/#rrggbb/#rrggbbaa or a CSS named colour"
    )


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


class SchedulerSettings(BaseSettings):
    """Scheduled DNS change persistence and execution settings.

    SQLite stores only intent (what to do, when) — never zone state.
    DNS remains the sole source of truth for what a zone contains.
    """

    model_config = SettingsConfigDict(env_prefix="SCHEDULER_")

    enabled: bool = Field(
        default=True,
        description="Enable the scheduled-change store and background runner",
    )
    database_path: str = Field(
        default="scheduler.db",
        description="Path to the SQLite database file for scheduled changes",
    )
    poll_interval: float = Field(
        default=10.0,
        description="Seconds between scheduler poll ticks",
    )
    max_attempts: int = Field(
        default=3,
        description="Maximum execution attempts before leaving a change as failed",
    )
    retry_backoff: int = Field(
        default=60,
        description="Base retry backoff in seconds (multiplied by attempt number)",
    )
    lease_ttl: int = Field(
        default=120,
        description="Seconds a claimed change stays locked to a worker",
    )
    default_expiry_window: int = Field(
        default=3600,
        description="Default seconds after scheduled_at before a change expires",
    )


DatabaseBackend = Literal["sqlite", "postgres"]


class PostgresSettings(BaseSettings):
    """Connection settings for an external PostgreSQL database."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = Field(default="", description="PostgreSQL server hostname")
    port: int = Field(default=5432, description="PostgreSQL server port")
    database: str = Field(default="", description="Database name")
    user: str = Field(default="", description="Database user")
    password: SecretStr = Field(default=SecretStr(""), description="Database password")
    dsn: SecretStr = Field(
        default=SecretStr(""),
        description="Full connection URI; overrides the discrete host/port/database/user fields",
    )
    sslmode: str = Field(
        default="prefer",
        description="libpq sslmode (disable, allow, prefer, require, verify-ca, verify-full)",
    )
    db_schema: str = Field(
        default="public",
        validation_alias=AliasChoices("db_schema", "schema", "POSTGRES_SCHEMA"),
        description="Schema placed at the head of search_path",
    )
    pool_size: int = Field(default=5, description="Connection pool size")
    connect_timeout: int = Field(default=10, description="Connection timeout in seconds")
    statement_timeout_ms: int = Field(
        default=30000,
        description="Server-side statement timeout in milliseconds (0 disables)",
    )

    @model_validator(mode="after")
    def validate_connection_fields(self) -> "PostgresSettings":
        """Require either a DSN or the discrete connection fields."""
        if self.dsn.get_secret_value():
            return self
        missing = [
            field
            for field, value in (
                ("host", self.host),
                ("database", self.database),
                ("user", self.user),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "database.postgres requires either 'dsn' or "
                f"host/database/user (missing: {', '.join(missing)})"
            )
        return self

    def url(self, *, async_driver: bool = True) -> str:
        """Build the SQLAlchemy URL.

        psycopg serves both the sync and async engines, so the same URL works
        for the application and for Alembic.
        """
        del async_driver  # psycopg drives both; kept for symmetry with SQLite
        dsn = self.dsn.get_secret_value()
        if dsn:
            return _normalize_postgres_dsn(dsn)
        password = quote_plus(self.password.get_secret_value())
        user = quote_plus(self.user)
        credentials = f"{user}:{password}" if password else user
        return f"postgresql+psycopg://{credentials}@{self.host}:{self.port}/{self.database}"

    def redacted_url(self) -> str:
        """The URL with any password replaced, safe for logs."""
        return _redact_url_password(self.url())

    def connect_args(self) -> dict[str, Any]:
        """libpq connection arguments passed through to psycopg."""
        args: dict[str, Any] = {}
        options: list[str] = []
        if self.db_schema:
            options.append(f"-c search_path={self.db_schema}")
        if self.statement_timeout_ms > 0:
            options.append(f"-c statement_timeout={self.statement_timeout_ms}")
        if options:
            args["options"] = " ".join(options)
        # A DSN may already carry these, and duplicates are an error in libpq.
        if not self.dsn.get_secret_value():
            args["sslmode"] = self.sslmode
            args["connect_timeout"] = self.connect_timeout
        return args


class DatabaseSettings(BaseSettings):
    """Persistence backend for scheduled changes and the audit log.

    SQLite is the default and needs no external service. PostgreSQL is for
    deployments that want durable, shared storage across app instances.
    """

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    backend: DatabaseBackend = Field(
        default="sqlite",
        description="Which database to store scheduled changes in",
    )
    auto_migrate: bool = Field(
        default=True,
        description="Create or upgrade the schema at startup (required for an empty database)",
    )
    path: str = Field(
        default="",
        description="SQLite database file; defaults to scheduler.database_path when unset",
    )
    postgres: PostgresSettings | None = Field(
        default=None,
        description="PostgreSQL connection settings (required when backend is 'postgres')",
    )

    @model_validator(mode="after")
    def validate_backend(self) -> "DatabaseSettings":
        """Require a postgres section when the postgres backend is selected."""
        if self.backend == "postgres" and self.postgres is None:
            raise ValueError(
                "database.backend is 'postgres' but no database.postgres section given"
            )
        return self

    def url(self, *, async_driver: bool = True) -> str:
        """Build the SQLAlchemy URL for the configured backend."""
        if self.backend == "postgres":
            assert self.postgres is not None  # guaranteed by validate_backend
            return self.postgres.url(async_driver=async_driver)
        driver = "aiosqlite" if async_driver else "pysqlite"
        return f"sqlite+{driver}:///{self.path}"

    def redacted_url(self) -> str:
        """The URL with any password replaced, safe for logs."""
        if self.backend == "postgres":
            assert self.postgres is not None  # guaranteed by validate_backend
            return self.postgres.redacted_url()
        return self.url()

    def connect_args(self) -> dict[str, Any]:
        """Driver-specific connection arguments."""
        if self.backend == "postgres":
            assert self.postgres is not None  # guaranteed by validate_backend
            return self.postgres.connect_args()
        return {}


def _normalize_postgres_dsn(dsn: str) -> str:
    """Force the psycopg driver on a user-supplied PostgreSQL URI."""
    for prefix in ("postgresql+psycopg://", "postgres+psycopg://"):
        if dsn.startswith(prefix):
            return dsn.replace(prefix, "postgresql+psycopg://", 1)
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn[len(prefix) :]
    return dsn


def _redact_url_password(url: str) -> str:
    """Replace the password in a database URL with a placeholder."""
    scheme, _, remainder = url.partition("://")
    if not remainder or "@" not in remainder:
        return url
    userinfo, _, hostpart = remainder.rpartition("@")
    if ":" not in userinfo:
        return url
    user, _, _ = userinfo.partition(":")
    return f"{scheme}://{user}:***@{hostpart}"


WebhookTargetType = Literal["slack", "teams", "generic"]
WebhookAuthType = Literal["none", "bearer", "basic", "header", "hmac"]
WebhookEvent = Literal["change_applied", "change_failed"]

# Default header names per auth type when the target does not specify one
_DEFAULT_AUTH_HEADERS: dict[str, str] = {
    "hmac": "X-DNS-Signature",
    "header": "X-API-Key",
}


class WebhookAuth(BaseModel):
    """Authentication for an outbound webhook target.

    Slack and Teams webhooks authenticate by possession of the URL and accept
    no auth header, so they use type "none". Generic receivers can require a
    bearer token, basic credentials, a static secret header, or an HMAC
    signature over the request body.
    """

    type: WebhookAuthType = Field(
        default="none",
        description="Auth scheme: none, bearer, basic, header, or hmac",
    )
    secret: SecretStr | None = Field(
        default=None,
        description="Bearer token, header value, or HMAC signing secret",
    )
    username: str | None = Field(default=None, description="Username for basic auth")
    password: SecretStr | None = Field(default=None, description="Password for basic auth")
    header: str | None = Field(
        default=None,
        description="Header name for header/hmac auth (defaults per auth type)",
    )
    algorithm: Literal["sha256", "sha512"] = Field(
        default="sha256",
        description="HMAC digest algorithm",
    )
    timestamp_header: str = Field(
        default="X-DNS-Timestamp",
        description="Header carrying the signed timestamp for hmac auth",
    )

    @property
    def effective_header(self) -> str:
        """Header name to send, falling back to the per-type default."""
        return self.header or _DEFAULT_AUTH_HEADERS.get(self.type, "X-API-Key")

    @model_validator(mode="after")
    def validate_auth_fields(self) -> "WebhookAuth":
        """Ensure the credentials required by the chosen scheme are present."""
        if self.type in ("bearer", "header", "hmac") and self.secret is None:
            raise ValueError(f"webhook auth type '{self.type}' requires 'secret'")
        if self.type == "basic" and (self.username is None or self.password is None):
            raise ValueError("webhook auth type 'basic' requires 'username' and 'password'")
        return self


class WebhookTarget(BaseModel):
    """A single outbound webhook destination."""

    name: str = Field(description="Target name (used in logs and metrics)")
    type: WebhookTargetType = Field(
        default="generic",
        description="Payload format: slack, teams, or generic",
    )
    url: SecretStr = Field(
        description="Webhook URL. Treated as a secret since Slack/Teams URLs are credentials"
    )
    events: list[WebhookEvent] | None = Field(
        default=None,
        description="Events to send to this target (defaults to the global events list)",
    )
    zones: list[str] | None = Field(
        default=None,
        description="Zone patterns to notify on, e.g. ['example.com.', '*.internal.']",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra static headers to send with each request",
    )
    auth: WebhookAuth = Field(
        default_factory=WebhookAuth,
        description="Authentication for this target",
    )
    verify_tls: bool | str = Field(
        default=True,
        description="Verify TLS: true, false, or a path to a CA bundle",
    )
    allow_insecure: bool = Field(
        default=False,
        description="Permit a plain http:// URL (sends credentials in clear text)",
    )
    timeout: float | None = Field(
        default=None,
        description="Per-target request timeout in seconds (defaults to webhooks.timeout)",
    )

    @model_validator(mode="after")
    def validate_url_scheme(self) -> "WebhookTarget":
        """Reject non-HTTP(S) URLs and clear-text HTTP unless explicitly allowed."""
        scheme = urlparse(self.url.get_secret_value()).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"webhook target '{self.name}' url must be http or https, got '{scheme}'"
            )
        if scheme == "http" and not self.allow_insecure:
            raise ValueError(
                f"webhook target '{self.name}' uses an insecure http:// URL. "
                "Set allow_insecure: true to permit sending credentials in clear text."
            )
        return self

    def matches_event(self, event: str, default_events: list[str]) -> bool:
        """Whether this target should receive the given event type."""
        allowed = self.events if self.events is not None else default_events
        return event in allowed

    def matches_zone(self, zone: str) -> bool:
        """Whether this target should receive changes for the given zone."""
        if not self.zones:
            return True
        if not zone.endswith("."):
            zone = zone + "."
        for pattern in self.zones:
            normalized = pattern if pattern.endswith(".") else pattern + "."
            if fnmatch.fnmatch(zone, normalized):
                return True
        return False


class WebhookSettings(BaseSettings):
    """Outbound webhook notifications for DNS changes."""

    model_config = SettingsConfigDict(env_prefix="WEBHOOK_")

    enabled: bool = Field(
        default=False,
        description="Enable outbound webhook notifications for DNS changes",
    )
    base_url: str = Field(
        default="",
        description="Public base URL of this app, used to build change links",
    )
    timeout: float = Field(default=5.0, description="Default request timeout in seconds")
    max_retries: int = Field(
        default=3,
        description="Delivery attempts per target before giving up",
    )
    retry_backoff: float = Field(
        default=1.0,
        description="Base retry backoff in seconds (doubled per attempt)",
    )
    queue_size: int = Field(
        default=1000,
        description="Pending event queue depth; events are dropped when full",
    )
    events: list[WebhookEvent] = Field(
        default_factory=lambda: ["change_applied", "change_failed"],
        description="Default event types to notify on",
    )
    targets: list[WebhookTarget] = Field(
        default_factory=list,
        description="Webhook destinations",
    )
    autorecord_manual_changes: bool = Field(
        default=True,
        description="Record manual changes in the scheduler store so they are linkable",
    )

    @model_validator(mode="after")
    def validate_enabled(self) -> "WebhookSettings":
        """Require base_url when enabled, and normalize it."""
        self.base_url = self.base_url.rstrip("/")
        if self.enabled and not self.base_url:
            raise ValueError("webhooks.base_url is required when webhooks are enabled")
        names = [t.name for t in self.targets]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate webhook target names: {sorted(duplicates)}")
        return self


class ThemePalette(BaseModel):
    """Optional per-token colour overrides for one theme mode.

    Every field defaults to ``None`` so operators can override any subset;
    built-in CSS defaults fill the rest. Values are validated strictly so they
    are safe to inject into a generated ``<style>`` element.
    """

    model_config = {"extra": "forbid"}

    bg_primary: str | None = None
    bg_secondary: str | None = None
    bg_tertiary: str | None = None
    bg_hover: str | None = None
    border_color: str | None = None
    text_primary: str | None = None
    text_secondary: str | None = None
    text_muted: str | None = None
    accent_primary: str | None = None
    accent_success: str | None = None
    accent_warning: str | None = None
    accent_danger: str | None = None
    accent_info: str | None = None
    # Record-type accents (unreadable on light backgrounds if left dark-only)
    record_a: str | None = None
    record_aaaa: str | None = None
    record_cname: str | None = None
    record_mx: str | None = None
    record_txt: str | None = None
    record_ns: str | None = None
    record_soa: str | None = None
    record_ptr: str | None = None
    record_srv: str | None = None
    record_caa: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _validate_colour(cls, value: Any) -> str | None:
        return validate_css_colour(value)

    def as_css_vars(self) -> dict[str, str]:
        """Return only set tokens as ``--token-name`` → value mappings."""
        result: dict[str, str] = {}
        for name, value in self.model_dump(exclude_none=True).items():
            result[f"--{name.replace('_', '-')}"] = value
        return result

    def overridden_count(self) -> int:
        """Number of tokens the operator explicitly set."""
        return len(self.model_dump(exclude_none=True))


class ThemeLogoSettings(BaseModel):
    """Logo used as the top-left home button.

    Supply either a remote/relative ``url`` or a local ``path`` the backend
    serves from ``GET /ui/logo`` — never both.
    """

    url: str | None = Field(
        default=None,
        description="Remote or relative URL for the logo image",
    )
    path: str | None = Field(
        default=None,
        description="Local filesystem path served via GET /ui/logo",
    )
    alt: str = Field(
        default="Home",
        description="Accessible alt text for the logo image",
    )

    @model_validator(mode="after")
    def validate_source(self) -> "ThemeLogoSettings":
        """Require exactly one of url/path, and that a local file is usable."""
        if self.url and self.path:
            raise ValueError("theme.logo.url and theme.logo.path are mutually exclusive")
        if not self.url and not self.path:
            raise ValueError("theme.logo requires either url or path")
        if self.path:
            logo_path = Path(self.path)
            if not logo_path.is_file():
                raise ValueError(f"theme.logo.path does not exist or is not a file: {self.path}")
            if not os.access(logo_path, os.R_OK):
                raise ValueError(f"theme.logo.path is not readable: {self.path}")
            suffix = logo_path.suffix.lower()
            if suffix not in _LOGO_SUFFIXES:
                raise ValueError(
                    f"theme.logo.path must end in one of {sorted(_LOGO_SUFFIXES)}, got {suffix!r}"
                )
        return self

    def media_type(self) -> str | None:
        """MIME type for a local logo file, or None when using a URL."""
        if not self.path:
            return None
        return _LOGO_MEDIA_TYPES[Path(self.path).suffix.lower()]

    def resolved_url(self) -> str:
        """URL the frontend should load for the logo."""
        if self.path:
            return "/ui/logo"
        assert self.url is not None
        return self.url


class ThemeSettings(BaseSettings):
    """Frontend branding and colour theming."""

    model_config = SettingsConfigDict(env_prefix="THEME_")

    app_name: str | None = Field(
        default=None,
        description="Display name for the UI; falls back to top-level app_name",
    )
    default_mode: Literal["dark", "light", "auto"] = Field(
        default="dark",
        description="Initial colour mode before any user preference is stored",
    )
    allow_mode_toggle: bool = Field(
        default=True,
        description="Show a light/dark mode toggle in the UI",
    )
    logo: ThemeLogoSettings | None = Field(
        default=None,
        description="Optional logo used as the top-left home button",
    )
    light: ThemePalette = Field(
        default_factory=ThemePalette,
        description="Optional colour overrides for light mode",
    )
    dark: ThemePalette = Field(
        default_factory=ThemePalette,
        description="Optional colour overrides for dark mode",
    )

    def to_ui_dict(self, fallback_app_name: str) -> dict[str, Any]:
        """Public payload for GET /ui/config (no filesystem paths)."""
        logo_payload: dict[str, str] | None = None
        if self.logo is not None:
            logo_payload = {
                "url": self.logo.resolved_url(),
                "alt": self.logo.alt,
            }
        return {
            "appName": self.app_name or fallback_app_name,
            "defaultMode": self.default_mode,
            "allowModeToggle": self.allow_mode_toggle,
            "logo": logo_payload,
            "light": self.light.as_css_vars(),
            "dark": self.dark.as_css_vars(),
        }

    def override_count(self) -> int:
        """Total number of colour tokens overridden across both modes."""
        return self.light.overridden_count() + self.dark.overridden_count()


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

    # Scheduled change settings
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)

    # Persistence backend for scheduled changes
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    # Outbound webhook notification settings
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)

    # Frontend branding / colour theming
    theme: ThemeSettings = Field(default_factory=ThemeSettings)

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
    def default_sqlite_path_from_scheduler(self) -> "Settings":
        """Fall back to scheduler.database_path for the SQLite file location.

        Keeps configs that predate the database section working unchanged.
        """
        if self.database.backend == "sqlite" and not self.database.path:
            self.database.path = self.scheduler.database_path
        return self

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
            scheduler=from_yaml(SchedulerSettings, yaml_config.get("scheduler")),
            database=from_yaml(DatabaseSettings, yaml_config.get("database")),
            webhooks=from_yaml(WebhookSettings, yaml_config.get("webhooks")),
            theme=from_yaml(ThemeSettings, yaml_config.get("theme")),
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
