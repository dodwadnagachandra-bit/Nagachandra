"""Schedule configuration save endpoint.

Provides PUT /api/config/schedule to validate and persist schedule changes.
Body is validated against the schedule_config JSON Schema before writing
to config/schedule_config.yaml, which triggers hot-reload in config_manager.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ems_hmi_server.deps import require_admin

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter()

# Path to schedule schema, resolved relative to repo root (WorkingDirectory)
_SCHEDULE_SCHEMA_PATH: Path = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "config"
    / "schemas"
    / "schedule_config.schema.json"
)

# Path to schedule config YAML, resolved relative to repo root
_SCHEDULE_CONFIG_PATH: Path = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "config"
    / "schedule_config.yaml"
)


def _get_schema_path(request: Request) -> Path:
    """Return schedule schema path, preferring app.state override for tests."""
    override: Path | None = getattr(request.app.state, "schedule_schema_path", None)
    if override is not None:
        return override
    return _SCHEDULE_SCHEMA_PATH


def _get_config_path(request: Request) -> Path:
    """Return schedule config path, preferring app.state override for tests."""
    override: Path | None = getattr(request.app.state, "schedule_config_path", None)
    if override is not None:
        return override
    return _SCHEDULE_CONFIG_PATH


@router.put("/api/config/schedule")
async def update_schedule(
    request: Request,
    body: dict[str, Any] = Body(...),
    _auth: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Validate and persist a new schedule configuration.

    Validates the request body against the schedule_config JSON Schema.
    On success, writes the YAML file which triggers hot-reload in config_manager.

    Args:
        request: FastAPI request (used to access app.state for path overrides).
        body: Raw schedule config dict from request body.
        _auth: Admin auth dependency (injected, ensures caller is admin).

    Returns:
        {"status": "ok", "result": {"written": True}, "error_msg": None}

    Raises:
        HTTPException 422: Body fails JSON Schema validation.
    """
    schema_path: Path = _get_schema_path(request)
    config_path: Path = _get_config_path(request)

    # Load and validate against schedule schema
    try:
        with schema_path.open("r") as f:
            schema: dict = json.load(f)
    except FileNotFoundError:
        logger.error("Schedule schema not found at %s", schema_path)
        raise HTTPException(status_code=500, detail="Schedule schema not found")

    try:
        jsonschema.validate(instance=body, schema=schema)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc.message))

    # Write validated config to YAML (triggers hot-reload via inotify)
    config_path.write_text(yaml.dump(body, default_flow_style=False))
    logger.info("Schedule config updated: %s", config_path)

    return {"status": "ok", "result": {"written": True}, "error_msg": None}
