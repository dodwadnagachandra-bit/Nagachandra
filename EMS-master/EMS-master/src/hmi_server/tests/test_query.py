"""Tests for query router -- ZMQ REQ proxy to logger."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from ems_hmi_server.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login_operator(client: AsyncClient) -> dict[str, str]:
    """Login as operator and return auth headers."""
    resp = await client.post("/api/auth/login", json={"pin": "1234"})
    token: str = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Valid query types
# ---------------------------------------------------------------------------

VALID_QUERY_TYPES: list[str] = [
    "time_series",
    "latest",
    "range_stats",
    "event_log",
    "energy_totals",
    "cell_snapshot",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query_type", VALID_QUERY_TYPES)
async def test_query_type_sends_zmq_command(
    test_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    query_type: str,
) -> None:
    """POST /api/query/{type} sends query with correct type to logger ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["socket_path"] = socket_path
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"data": []}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(f"/api/query/{query_type}", headers=headers, json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert captured["action"] == "query"
    assert captured["params"]["type"] == query_type
    assert "logger_query" in captured["socket_path"]


# ---------------------------------------------------------------------------
# Invalid query type -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_query_type_returns_422(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/query/invalid_type returns 422 validation error."""
    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        return {"status": "ok", "result": {}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post("/api/query/invalid_type", headers=headers, json={})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Additional params forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_forwards_additional_params(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/query/time_series forwards body params to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["params"] = params
        return {"status": "ok", "result": {"data": []}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/query/time_series",
            headers=headers,
            json={"start_ts": 1234, "end_ts": 5678, "topics": ["pcs"]},
        )

    assert resp.status_code == 200
    assert captured["params"]["type"] == "time_series"
    assert captured["params"]["start_ts"] == 1234
    assert captured["params"]["end_ts"] == 5678
    assert captured["params"]["topics"] == ["pcs"]


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_requires_auth(test_config: dict) -> None:
    """POST /api/query/time_series returns 401 without auth."""
    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/query/time_series", json={})

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# ZMQ timeout -> 504
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_zmq_timeout_returns_504(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Query endpoint returns 504 when ZMQ times out."""

    async def mock_zmq_timeout(socket_path: str, action: str, params: dict) -> dict:
        raise HTTPException(status_code=504, detail="Backend service timeout")

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_timeout)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post("/api/query/latest", headers=headers, json={})

    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# ZMQ error -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_zmq_error_returns_400(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Query endpoint returns 400 when ZMQ returns error status."""

    async def mock_zmq_error(socket_path: str, action: str, params: dict) -> dict:
        return {"status": "error", "result": None, "error_msg": "Invalid query parameters"}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_error)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post("/api/query/time_series", headers=headers, json={})

    assert resp.status_code == 400
    assert "Invalid query parameters" in resp.json()["detail"]
