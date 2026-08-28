"""Tests for control command router -- ZMQ REQ proxy endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from ems_hmi_server.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(test_config: dict, zmq_response: dict | None = None):
    """Create test app with monkeypatched zmq_command."""
    app = create_app(test_config)
    return app


async def _login_operator(client: AsyncClient) -> dict[str, str]:
    """Login as operator and return auth headers."""
    resp = await client.post("/api/auth/login", json={"pin": "1234"})
    token: str = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def _login_admin(client: AsyncClient) -> dict[str, str]:
    """Login as admin and return auth headers."""
    resp = await client.post("/api/auth/login", json={"pin": "5678"})
    token: str = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# POST /api/control/mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_change_sends_zmq_command(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/mode sends mode_change to ZMQ and returns response."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["socket_path"] = socket_path
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/mode",
            json={"target_state": "CHARGING"},
            headers=headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["result"]["accepted"] is True
    assert captured["action"] == "mode_change"
    assert captured["params"] == {"target_state": "CHARGING"}
    assert "control_cmd" in captured["socket_path"]


# ---------------------------------------------------------------------------
# POST /api/control/setpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setpoint_sends_zmq_command(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/setpoint sends manual_setpoint to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/setpoint",
            json={"power_kw": 25.0},
            headers=headers,
        )

    assert resp.status_code == 200
    assert captured["action"] == "manual_setpoint"
    assert captured["params"] == {"power_kw": 25.0}


# ---------------------------------------------------------------------------
# POST /api/control/priority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_sends_zmq_command(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/priority sends source_priority to ZMQ."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/priority",
            json={"mode": "manual"},
            headers=headers,
        )

    assert resp.status_code == 200
    assert captured["action"] == "source_priority"
    assert captured["params"] == {"mode": "manual"}


# ---------------------------------------------------------------------------
# POST /api/control/fault-reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fault_reset_sends_zmq_command(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/fault-reset sends fault_reset with empty params."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        captured["params"] = params
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post("/api/control/fault-reset", headers=headers)

    assert resp.status_code == 200
    assert captured["action"] == "fault_reset"
    assert captured["params"] == {}


# ---------------------------------------------------------------------------
# POST /api/control/maintenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_enter_requires_admin(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/maintenance with admin sends maintenance_enter."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_admin(client)
        resp = await client.post(
            "/api/control/maintenance",
            json={"action": "enter"},
            headers=headers,
        )

    assert resp.status_code == 200
    assert captured["action"] == "maintenance_enter"


@pytest.mark.asyncio
async def test_maintenance_exit_sends_zmq(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/maintenance with action=exit sends maintenance_exit."""
    captured: dict[str, Any] = {}

    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        captured["action"] = action
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_admin(client)
        resp = await client.post(
            "/api/control/maintenance",
            json={"action": "exit"},
            headers=headers,
        )

    assert resp.status_code == 200
    assert captured["action"] == "maintenance_exit"


@pytest.mark.asyncio
async def test_maintenance_operator_gets_403(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/control/maintenance with operator auth returns 403."""
    async def mock_zmq_command(socket_path: str, action: str, params: dict) -> dict:
        return {"status": "ok", "result": {"accepted": True}, "error_msg": None}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_command)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/maintenance",
            json={"action": "enter"},
            headers=headers,
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_endpoints_require_auth(test_config: dict) -> None:
    """All control endpoints return 401 without auth token."""
    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        endpoints = [
            ("POST", "/api/control/mode", {"target_state": "IDLE"}),
            ("POST", "/api/control/setpoint", {"power_kw": 10.0}),
            ("POST", "/api/control/priority", {"mode": "manual"}),
            ("POST", "/api/control/fault-reset", None),
            ("POST", "/api/control/maintenance", {"action": "enter"}),
        ]
        for method, path, body in endpoints:
            if body:
                resp = await client.post(path, json=body)
            else:
                resp = await client.post(path)
            assert resp.status_code == 401, f"{path} should require auth"


# ---------------------------------------------------------------------------
# ZMQ timeout -> 504
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_zmq_timeout_returns_504(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control endpoint returns 504 when ZMQ times out."""

    async def mock_zmq_timeout(socket_path: str, action: str, params: dict) -> dict:
        raise HTTPException(status_code=504, detail="Backend service timeout")

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_timeout)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/mode",
            json={"target_state": "CHARGING"},
            headers=headers,
        )

    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# ZMQ error response -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_zmq_error_returns_400(test_config: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control endpoint returns 400 when ZMQ returns error status."""

    async def mock_zmq_error(socket_path: str, action: str, params: dict) -> dict:
        return {"status": "error", "result": None, "error_msg": "Invalid state transition"}

    import ems_hmi_server.deps as deps_mod
    monkeypatch.setattr(deps_mod, "zmq_command", mock_zmq_error)

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.post(
            "/api/control/mode",
            json={"target_state": "INVALID"},
            headers=headers,
        )

    assert resp.status_code == 400
    assert "Invalid state transition" in resp.json()["detail"]
