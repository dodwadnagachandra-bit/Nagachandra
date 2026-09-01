"""Alarm command router -- proxies REST requests to alarm_manager via ZMQ."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ems_common.ipc import SOCK_ALARM_CMD
import ems_hmi_server.deps as deps
from ems_hmi_server.deps import require_auth
from ems_hmi_server.models import AcknowledgeRequest, ZmqResponse

router: APIRouter = APIRouter(prefix="/api/alarm", tags=["alarm"])


async def _send_alarm(action: str, params: dict) -> dict:
    """Send an alarm command and return ZMQ response or raise on error."""
    response: dict = await deps.zmq_command(SOCK_ALARM_CMD, action, params)
    if response.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=response.get("error_msg", "Unknown error"),
        )
    return response


@router.post("/acknowledge", response_model=ZmqResponse)
async def acknowledge_alarm(
    body: AcknowledgeRequest,
    auth: dict = Depends(require_auth),
) -> dict:
    """Acknowledge an active alarm."""
    return await _send_alarm("acknowledge", {"alarm_id": body.alarm_id})


@router.get("/active", response_model=ZmqResponse)
async def get_active_alarms(
    auth: dict = Depends(require_auth),
) -> dict:
    """Get list of active alarms."""
    return await _send_alarm("get_active_alarms", {})


@router.get("/config", response_model=ZmqResponse)
async def get_alarm_config(
    auth: dict = Depends(require_auth),
) -> dict:
    """Get alarm configuration rules."""
    return await _send_alarm("get_alarm_config", {})
