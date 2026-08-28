"""Health check router -- GET /api/health returns status and uptime."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ems_hmi_server.models import HealthResponse

router: APIRouter = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Return server health status and uptime in seconds."""
    start_time: float = request.app.state.start_time
    uptime_s: int = int(time.time() - start_time)
    return HealthResponse(status="ok", uptime_s=uptime_s)
