"""PIN authentication router -- login, logout, token store."""

from __future__ import annotations

import secrets
import time

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request

from ems_hmi_server.deps import require_auth
from ems_hmi_server.models import LoginRequest, LoginResponse, MessageResponse

MAX_CONCURRENT_SESSIONS: int = 10

router: APIRouter = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenStore:
    """In-memory bearer token store with expiry and max session enforcement."""

    def __init__(self) -> None:
        self._tokens: dict[str, dict] = {}

    @property
    def tokens(self) -> dict[str, dict]:
        """Return the raw token dict (for testing access)."""
        return self._tokens

    def create_token(self, level: str, timeout_s: int) -> str:
        """Generate a new bearer token and store it.

        Args:
            level: Auth level ('operator' or 'admin').
            timeout_s: Session timeout in seconds.

        Returns:
            64-character hex token string.

        Raises:
            HTTPException 429: If max concurrent sessions reached after cleanup.
        """
        # Clean expired first if at capacity
        if len(self._tokens) >= MAX_CONCURRENT_SESSIONS:
            self.cleanup_expired()
        # Still at capacity after cleanup -- reject
        if len(self._tokens) >= MAX_CONCURRENT_SESSIONS:
            raise HTTPException(
                status_code=429,
                detail="Too many active sessions",
            )

        token: str = secrets.token_hex(32)
        self._tokens[token] = {
            "level": level,
            "expires_at": time.time() + timeout_s,
        }
        return token

    def validate_token(self, token: str) -> dict | None:
        """Validate a token and return its metadata.

        Args:
            token: Bearer token string.

        Returns:
            Dict with 'level' key if valid, dict with 'expired' key if expired,
            or None if token not found.
        """
        entry: dict | None = self._tokens.get(token)
        if entry is None:
            return None

        if time.time() > entry["expires_at"]:
            # Lazy cleanup -- remove expired token
            del self._tokens[token]
            return {"expired": True}

        return {"level": entry["level"]}

    def remove_token(self, token: str) -> bool:
        """Remove a token from the store.

        Returns:
            True if the token existed and was removed.
        """
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False

    def cleanup_expired(self) -> int:
        """Remove all expired tokens.

        Returns:
            Number of tokens removed.
        """
        now: float = time.time()
        expired: list[str] = [
            t for t, meta in self._tokens.items() if now > meta["expires_at"]
        ]
        for t in expired:
            del self._tokens[t]
        return len(expired)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Authenticate with a PIN and receive a bearer token.

    Checks operator PIN first, then admin PIN. If admin matches, admin level wins.
    """
    config_auth: dict = request.app.state.config["auth"]
    operator_hash: str = config_auth["operator_pin_hash"]
    admin_hash: str = config_auth["admin_pin_hash"]
    timeout_s: int = config_auth["session_timeout_s"]

    pin_bytes: bytes = body.pin.encode("utf-8")
    level: str | None = None

    # Check operator first
    if bcrypt.checkpw(pin_bytes, operator_hash.encode("utf-8")):
        level = "operator"

    # Check admin -- admin wins if both match
    if bcrypt.checkpw(pin_bytes, admin_hash.encode("utf-8")):
        level = "admin"

    if level is None:
        raise HTTPException(status_code=401, detail="Invalid PIN")

    token_store: TokenStore = request.app.state.token_store
    token: str = token_store.create_token(level, timeout_s)

    return LoginResponse(token=token, level=level, expires_in=timeout_s)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    auth: dict = Depends(require_auth),
) -> MessageResponse:
    """Logout by invalidating the bearer token."""
    token_store: TokenStore = request.app.state.token_store
    token_store.remove_token(auth["token"])
    return MessageResponse(message="Logged out")
