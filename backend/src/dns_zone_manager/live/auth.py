"""WebSocket authentication helpers for live zone subscriptions."""

from __future__ import annotations

from fastapi import HTTPException, WebSocket, status

from dns_zone_manager.auth.combined import AuthenticatedUser, _authenticate


async def authenticate_websocket(websocket: WebSocket) -> AuthenticatedUser:
    """Authenticate a WebSocket upgrade using headers and query params.

    Browser ``WebSocket`` cannot set custom headers, so API keys and Azure
    bearer tokens may be supplied as ``api_key`` / ``access_token`` query
    parameters. Proxy identity headers and ``Authorization`` on the upgrade
    request still work when present.
    """
    api_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
    bearer = None
    auth_header = websocket.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
    elif websocket.query_params.get("access_token"):
        bearer = websocket.query_params.get("access_token")

    # ``_authenticate`` expects a Request-like object for proxy headers.
    # WebSocket exposes ``.headers`` the same way.
    try:
        return await _authenticate(websocket, api_key, bearer)  # type: ignore[arg-type]
    except HTTPException as e:
        # Close before accept with policy violation / auth failure
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e.detail))
        raise
