"""Shared FastAPI dependencies -- auth injection, config access, ZMQ helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import zmq
from fastapi import Depends, HTTPException, Request

from ems_common.ipc import decode_command_response, encode_command_request


def get_config(request: Request) -> dict:
    """Return the loaded HMI config dict from app state."""
    return request.app.state.config


async def require_auth(request: Request) -> dict:
    """Validate Bearer token from Authorization header.

    Returns:
        Dict with 'level' (operator/admin) and 'token' string.

    Raises:
        HTTPException 401: Missing or invalid token.
        HTTPException 403: Expired token.
    """
    auth_header: str | None = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token: str = auth_header[len("Bearer "):]
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token_store = request.app.state.token_store
    result: dict | None = token_store.validate_token(token)
    if result is None:
        # Check if it was expired (already removed by validate_token)
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    if result.get("expired"):
        raise HTTPException(status_code=403, detail="Token expired")

    return {"level": result["level"], "token": token}


async def require_admin(auth: dict = Depends(require_auth)) -> dict:
    """Require admin-level authentication.

    Raises:
        HTTPException 403: If auth level is not admin.
    """
    if auth["level"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth


async def zmq_command(socket_path: str, action: str, params: dict) -> dict:
    """Send a ZMQ REQ command and return decoded response.

    Creates a dedicated REQ socket per call (no sharing across requests).
    Uses run_in_executor for blocking ZMQ operations.

    Args:
        socket_path: ZMQ IPC socket path.
        action: Command action string.
        params: Command parameters dict.

    Returns:
        Decoded response dict with status, result, error_msg.

    Raises:
        HTTPException 504: ZMQ receive timeout.
    """

    def _send_recv() -> dict[str, Any]:
        ctx: zmq.Context = zmq.Context()
        sock: zmq.Socket = ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.RCVTIMEO, 5000)
        sock.connect(socket_path)
        try:
            sock.send(encode_command_request(action, params))
            reply: bytes = sock.recv()
            return decode_command_response(reply)
        except zmq.Again:
            raise HTTPException(status_code=504, detail="Backend service timeout")
        finally:
            sock.close()
            ctx.term()

    loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_recv)
