"""Tests for health endpoint -- GET /api/health."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_200(async_client: AsyncClient) -> None:
    """Health endpoint returns 200 with status and uptime."""
    response = await async_client.get("/api/health/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_s"], int)
    assert data["uptime_s"] >= 0


async def test_health_response_has_correct_keys(async_client: AsyncClient) -> None:
    """Health response contains exactly the expected keys."""
    response = await async_client.get("/api/health/")
    data = response.json()
    assert set(data.keys()) == {"status", "uptime_s"}


async def test_health_no_auth_required(async_client: AsyncClient) -> None:
    """Health endpoint works without any Authorization header."""
    response = await async_client.get("/api/health/")
    assert response.status_code == 200
