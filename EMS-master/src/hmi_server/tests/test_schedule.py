"""Tests for schedule configuration save endpoint."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml
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


async def _login_admin(client: AsyncClient) -> dict[str, str]:
    """Login as admin and return auth headers."""
    resp = await client.post("/api/auth/login", json={"pin": "5678"})
    token: str = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _valid_schedule_body() -> dict:
    """Return a schedule body that passes the schedule_config schema."""
    return {
        "_schema_version": "1.0",
        "mode": "manual",
        "time_windows": [],
        "day_night": {
            "day_start": "06:00",
            "night_start": "18:00",
        },
        "power_curve": [0.0] * 96,
    }


# ---------------------------------------------------------------------------
# PUT /api/config/schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_schedule_valid(
    test_config: dict, tmp_path: Path
) -> None:
    """PUT /api/config/schedule with valid body (admin auth) -> 200, {"status": "ok"}."""
    app = create_app(test_config)
    # Override paths so we don't touch real config files
    schema_path: Path = (
        Path(__file__).parent.parent.parent.parent
        / "config"
        / "schemas"
        / "schedule_config.schema.json"
    )
    app.state.schedule_schema_path = schema_path
    app.state.schedule_config_path = tmp_path / "schedule_config.yaml"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_admin(client)
        resp = await client.put(
            "/api/config/schedule",
            json=_valid_schedule_body(),
            headers=headers,
        )

    assert resp.status_code == 200
    data: dict = resp.json()
    assert data["status"] == "ok"
    assert data["result"]["written"] is True


@pytest.mark.asyncio
async def test_update_schedule_invalid_body(
    test_config: dict, tmp_path: Path
) -> None:
    """PUT /api/config/schedule with body failing schema -> 422."""
    app = create_app(test_config)
    schema_path: Path = (
        Path(__file__).parent.parent.parent.parent
        / "config"
        / "schemas"
        / "schedule_config.schema.json"
    )
    app.state.schedule_schema_path = schema_path
    app.state.schedule_config_path = tmp_path / "schedule_config.yaml"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_admin(client)
        # Missing required fields: time_windows, day_night, power_curve
        resp = await client.put(
            "/api/config/schedule",
            json={"mode": "manual"},
            headers=headers,
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_schedule_forbidden(
    test_config: dict, tmp_path: Path
) -> None:
    """PUT /api/config/schedule with operator-level auth -> 403."""
    app = create_app(test_config)
    schema_path: Path = (
        Path(__file__).parent.parent.parent.parent
        / "config"
        / "schemas"
        / "schedule_config.schema.json"
    )
    app.state.schedule_schema_path = schema_path
    app.state.schedule_config_path = tmp_path / "schedule_config.yaml"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_operator(client)
        resp = await client.put(
            "/api/config/schedule",
            json=_valid_schedule_body(),
            headers=headers,
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_schedule_writes_yaml(
    test_config: dict, tmp_path: Path
) -> None:
    """After successful PUT, schedule_config.yaml contains the submitted data."""
    app = create_app(test_config)
    schema_path: Path = (
        Path(__file__).parent.parent.parent.parent
        / "config"
        / "schemas"
        / "schedule_config.schema.json"
    )
    config_out: Path = tmp_path / "schedule_config.yaml"
    app.state.schedule_schema_path = schema_path
    app.state.schedule_config_path = config_out

    body: dict = _valid_schedule_body()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _login_admin(client)
        resp = await client.put(
            "/api/config/schedule",
            json=body,
            headers=headers,
        )

    assert resp.status_code == 200
    assert config_out.exists(), "schedule_config.yaml was not written"

    written: dict = yaml.safe_load(config_out.read_text())
    assert written["mode"] == "manual"
    assert written["_schema_version"] == "1.0"
    assert written["day_night"]["day_start"] == "06:00"
    assert len(written["power_curve"]) == 96
