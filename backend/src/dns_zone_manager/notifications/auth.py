"""Outbound authentication for webhook requests.

Slack and Teams authenticate by possession of the URL and accept no auth
header, so they use auth type "none" and rely on the URL being kept secret.
Generic receivers can require a bearer token, basic credentials, a static
secret header, or an HMAC signature over the request body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from dns_zone_manager.config import WebhookTarget

_DIGESTS = {
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}


def signing_input(timestamp: str, body: bytes) -> bytes:
    """Build the HMAC signing input: the timestamp, a dot, then the raw body.

    Including the timestamp lets the receiver reject replayed requests, and
    signing the exact transmitted bytes lets it detect tampering.
    """
    return timestamp.encode("utf-8") + b"." + body


def compute_signature(secret: str, algorithm: str, timestamp: str, body: bytes) -> str:
    """Compute the hex HMAC signature, prefixed with its algorithm."""
    digest = _DIGESTS[algorithm]
    mac = hmac.new(secret.encode("utf-8"), signing_input(timestamp, body), digest)
    return f"{algorithm}={mac.hexdigest()}"


def build_auth_headers(target: WebhookTarget, body: bytes, timestamp: str) -> dict[str, str]:
    """Build the authentication headers for one request to a target.

    Args:
        target: The webhook target configuration
        body: Exact request body bytes that will be transmitted
        timestamp: RFC 3339 timestamp, signed alongside the body for hmac auth

    Returns:
        Headers to merge into the request
    """
    auth = target.auth

    if auth.type == "none":
        return {}

    if auth.type == "bearer":
        assert auth.secret is not None  # guaranteed by WebhookAuth validator
        return {"Authorization": f"Bearer {auth.secret.get_secret_value()}"}

    if auth.type == "basic":
        assert auth.username is not None and auth.password is not None
        raw = f"{auth.username}:{auth.password.get_secret_value()}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}

    if auth.type == "header":
        assert auth.secret is not None
        return {auth.effective_header: auth.secret.get_secret_value()}

    if auth.type == "hmac":
        assert auth.secret is not None
        signature = compute_signature(
            auth.secret.get_secret_value(),
            auth.algorithm,
            timestamp,
            body,
        )
        return {
            auth.effective_header: signature,
            auth.timestamp_header: timestamp,
        }

    raise ValueError(f"Unknown webhook auth type: {auth.type}")


def target_secrets(target: WebhookTarget) -> list[str]:
    """Every secret string associated with a target, for log redaction."""
    secrets = [target.url.get_secret_value()]
    if target.auth.secret is not None:
        secrets.append(target.auth.secret.get_secret_value())
    if target.auth.password is not None:
        secrets.append(target.auth.password.get_secret_value())
    return [s for s in secrets if s]


def redact(message: str, target: WebhookTarget) -> str:
    """Replace any of the target's secrets appearing in a message.

    httpx error messages embed the request URL, which is itself the credential
    for Slack and Teams, so anything derived from an exception must pass
    through here before being logged.
    """
    for secret in target_secrets(target):
        message = message.replace(secret, "***")
    return message
