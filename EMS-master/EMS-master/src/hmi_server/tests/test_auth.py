"""Tests for PIN authentication -- login, logout, token lifecycle."""

from __future__ import annotations

import time

from httpx import AsyncClient

from ems_hmi_server.auth import TokenStore


async def test_login_operator_pin(async_client: AsyncClient) -> None:
    """POST /api/auth/login with operator PIN returns operator-level token."""
    response = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "operator"
    assert "token" in data
    assert data["expires_in"] == 1800


async def test_login_admin_pin(async_client: AsyncClient) -> None:
    """POST /api/auth/login with admin PIN returns admin-level token."""
    response = await async_client.post(
        "/api/auth/login", json={"pin": "5678"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "admin"
    assert "token" in data
    assert data["expires_in"] == 1800


async def test_login_wrong_pin(async_client: AsyncClient) -> None:
    """POST /api/auth/login with wrong PIN returns 401."""
    response = await async_client.post(
        "/api/auth/login", json={"pin": "9999"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid PIN"


async def test_login_empty_pin_rejected(async_client: AsyncClient) -> None:
    """POST /api/auth/login with empty pin is rejected by Pydantic (422)."""
    response = await async_client.post(
        "/api/auth/login", json={"pin": ""}
    )
    assert response.status_code == 422


async def test_token_is_64_char_hex(async_client: AsyncClient) -> None:
    """Token is a 64-character hex string (32 bytes)."""
    response = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    token: str = response.json()["token"]
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)


async def test_logout_valid_token(async_client: AsyncClient) -> None:
    """POST /api/auth/logout with valid token returns 200 and invalidates token."""
    # Login first
    login_resp = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    token: str = login_resp.json()["token"]

    # Logout
    logout_resp = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out"

    # Token should no longer work
    logout_resp2 = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout_resp2.status_code == 401


async def test_logout_invalid_token(async_client: AsyncClient) -> None:
    """POST /api/auth/logout with invalid token returns 401."""
    response = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": "Bearer invalidtoken123"},
    )
    assert response.status_code == 401


async def test_protected_endpoint_no_token(async_client: AsyncClient) -> None:
    """Protected endpoint without Authorization header returns 401."""
    response = await async_client.post("/api/auth/logout")
    assert response.status_code == 401


async def test_protected_endpoint_expired_token(async_client: AsyncClient, test_app) -> None:
    """Protected endpoint with expired token returns 403."""
    # Login to get a valid token
    login_resp = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    token: str = login_resp.json()["token"]

    # Manually expire the token
    token_store: TokenStore = test_app.state.token_store
    token_store.tokens[token]["expires_at"] = time.time() - 1

    # Try to use expired token
    response = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Token expired"


async def test_max_concurrent_sessions(async_client: AsyncClient) -> None:
    """Max 10 concurrent sessions -- 11th login returns 429."""
    # Create 10 sessions
    for _ in range(10):
        resp = await async_client.post(
            "/api/auth/login", json={"pin": "1234"}
        )
        assert resp.status_code == 200

    # 11th should fail
    resp = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    assert resp.status_code == 429
    assert "Too many active sessions" in resp.json()["detail"]


async def test_expired_tokens_cleaned_on_auth_check(
    async_client: AsyncClient, test_app
) -> None:
    """Expired tokens are removed when validate_token is called."""
    # Login
    login_resp = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    token: str = login_resp.json()["token"]

    # Manually expire it
    token_store: TokenStore = test_app.state.token_store
    token_store.tokens[token]["expires_at"] = time.time() - 1

    # Auth check should clean it up (returns 403 for expired)
    response = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403

    # Token should be removed from store
    assert token not in token_store.tokens


async def test_max_sessions_with_expired_cleanup(
    async_client: AsyncClient, test_app
) -> None:
    """Max sessions cleanup: expired tokens freed allows new login."""
    token_store: TokenStore = test_app.state.token_store

    # Create 10 sessions
    tokens: list[str] = []
    for _ in range(10):
        resp = await async_client.post(
            "/api/auth/login", json={"pin": "1234"}
        )
        assert resp.status_code == 200
        tokens.append(resp.json()["token"])

    # Expire 5 tokens
    for t in tokens[:5]:
        token_store.tokens[t]["expires_at"] = time.time() - 1

    # 11th login should succeed (cleanup frees expired slots)
    resp = await async_client.post(
        "/api/auth/login", json={"pin": "1234"}
    )
    assert resp.status_code == 200


def test_token_store_cleanup_expired() -> None:
    """TokenStore.cleanup_expired removes all expired tokens."""
    store = TokenStore()
    # Add some tokens manually
    store.tokens["valid1"] = {"level": "operator", "expires_at": time.time() + 3600}
    store.tokens["expired1"] = {"level": "operator", "expires_at": time.time() - 1}
    store.tokens["expired2"] = {"level": "admin", "expires_at": time.time() - 100}

    removed: int = store.cleanup_expired()
    assert removed == 2
    assert "valid1" in store.tokens
    assert "expired1" not in store.tokens
    assert "expired2" not in store.tokens
