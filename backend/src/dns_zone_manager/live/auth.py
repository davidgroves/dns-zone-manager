"""WebSocket authentication helpers for live zone subscriptions."""

from __future__ import annotations

from fastapi import HTTPException, WebSocket, status

from dns_zone_manager.auth.combined import AuthenticatedUser, _authenticate


async def authenticate_websocket(websocket: WebSocket) -> AuthenticatedUser:
    """Authenticate a WebSocket upgrade using headers and query params.

    Browser ``WebSocket`` cannot set custom headers, so API keys may be
    supplied as the ``api_key`` query parameter. Proxy identity headers and
    ``X-API-Key`` on the upgrade request still work when present.
    """
    api_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")

    # ``_authenticate`` expects a Request-like object for proxy headers.
    # WebSocket exposes ``.headers`` the same way.
    try:
        return await _authenticate(websocket, api_key)  # type: ignore[arg-type]
    except HTTPException as e:
        # Close before accept with policy violation / auth failure
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e.detail))
        raise
