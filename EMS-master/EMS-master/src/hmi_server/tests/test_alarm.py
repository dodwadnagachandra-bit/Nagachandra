"""Tests for alarm command router -- ZMQ REQ proxy endpoints."""

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
# POST /api/alarm/acknowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_sends_zmq_command(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/alarm/acknowledge sends acknowledge to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["socket_path"] = socket_path
        captured["action"] = action
        captured["params"] = params
        return {
            "status": "ok",
            "result": {"alarm_id": "ALM-001", "from_state": "active", "to_state": "acked"},
            "error_msg": None,
        }

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/alarm/acknowledge",
            json={"alarm_id": "ALM-001"},
            headers=headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["result"]["alarm_id"] == "ALM-001"
    assert captured["action"] == "acknowledge"
    assert captured["params"] == {"alarm_id": "ALM-001"}
    assert "alarm_cmd" in captured["socket_path"]


# ---------------------------------------------------------------------------
# GET /api/alarm/active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_alarms(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/alarm/active sends get_active_alarms to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"alarms": []}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.get("/api/alarm/active", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["alarms"] == []
    assert captured["action"] == "get_active_alarms"
    assert captured["params"] == {}


# ---------------------------------------------------------------------------
# GET /api/alarm/config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_alarm_config(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/alarm/config sends get_alarm_config to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        return {"status": "ok", "result": {"rules": []}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.get("/api/alarm/config", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"]["rules"] == []
    assert captured["action"] == "get_alarm_config"


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alarm_endpoints_require_auth(test_config: dict) -> None:
    """All alarm endpoints return 401 without auth token."""
    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.post("/api/alarm/acknowledge", json={"alarm_id": "ALM-001"})
        assert resp1.status_code == 401

        resp2 = await client.get("/api/alarm/active")
        assert resp2.status_code == 401

        resp3 = await client.get("/api/alarm/config")
        assert resp3.status_code == 401


# ---------------------------------------------------------------------------
# ZMQ timeout -> 504
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alarm_zmq_timeout_returns_504(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Alarm endpoint returns 504 when ZMQ times out."""

    async def mock_zmq_timeout(socket_path: str, action: str, params: dict) -> dict:
        raise HTTPException(status_code=504, detail="Backend service timeout")

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_timeout)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.get("/api/alarm/active", headers=headers)

    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# ZMQ error -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alarm_zmq_error_returns_400(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Alarm endpoint returns 400 when ZMQ returns error status."""

    async def mock_zmq_error(socket_path: str, action: str, params: dict) -> dict:
        return {"status": "error", "result": None, "error_msg": "Alarm not found"}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_error)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/alarm/acknowledge",
            json={"alarm_id": "ALM-999"},
            headers=headers,
        )

    assert resp.status_code == 400
    assert "Alarm not found" in resp.json()["detail"]
