"""Control command router -- proxies REST requests to control_manager via ZMQ."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ems_common.ipc import SOCK_CONTROL_CMD
import ems_hmi_server.deps as deps
from ems_hmi_server.deps import require_admin, require_auth
from ems_hmi_server.models import (
    MaintenanceRequest,
    ManualSetpointRequest,
    ModeChangeRequest,
    SourcePriorityRequest,
    ZmqResponse,
)

router: APIRouter = APIRouter(prefix="/api/control", tags=["control"])


async def _send_control(action: str, params: dict) -> dict:
    """Send a control command and return ZMQ response or raise on error."""
    response: dict = await deps.zmq_command(SOCK_CONTROL_CMD, action, params)
    if response.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=response.get("error_msg", "Unknown error"),
        )
    return response


@router.post("/mode", response_model=ZmqResponse)
async def change_mode(
    body: ModeChangeRequest,
    auth: dict = Depends(require_auth),
) -> dict:
    """Change the control mode/state."""
    return await _send_control("mode_change", {"target_state": body.target_state})


@router.post("/setpoint", response_model=ZmqResponse)
async def set_manual_setpoint(
    body: ManualSetpointRequest,
    auth: dict = Depends(require_auth),
) -> dict:
    """Set manual power setpoint in kW."""
    return await _send_control("manual_setpoint", {"power_kw": body.power_kw})


@router.post("/priority", response_model=ZmqResponse)
async def set_source_priority(
    body: SourcePriorityRequest,
    auth: dict = Depends(require_auth),
) -> dict:
    """Set source priority mode."""
    return await _send_control("source_priority", {"mode": body.mode})


@router.post("/fault-reset", response_model=ZmqResponse)
async def fault_reset(
    auth: dict = Depends(require_auth),
) -> dict:
    """Reset fault state."""
    return await _send_control("fault_reset", {})


@router.post("/maintenance", response_model=ZmqResponse)
async def maintenance(
    body: MaintenanceRequest,
    auth: dict = Depends(require_admin),
) -> dict:
    """Enter or exit maintenance mode. Requires admin auth."""
    action: str = f"maintenance_{body.action}"
    return await _send_control(action, {})
