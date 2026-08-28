"""Query router -- proxies REST requests to logger query server via ZMQ."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException

from ems_common.ipc import SOCK_LOGGER_QUERY
import ems_hmi_server.deps as deps
from ems_hmi_server.deps import require_auth
from ems_hmi_server.models import ZmqResponse

router: APIRouter = APIRouter(prefix="/api/query", tags=["query"])

QueryType = Literal[
    "time_series",
    "latest",
    "range_stats",
    "event_log",
    "energy_totals",
    "cell_snapshot",
]


@router.post("/{query_type}", response_model=ZmqResponse)
async def run_query(
    query_type: QueryType,
    body: dict[str, Any] = Body(default={}),
    auth: dict = Depends(require_auth),
) -> dict:
    """Run a data query against the logger.

    Path parameter selects the query type. Request body provides
    additional query parameters (start_ts, end_ts, topics, etc.).
    """
    params: dict[str, Any] = {"type": query_type, **body}
    response: dict = await deps.zmq_command(SOCK_LOGGER_QUERY, "query", params)
    if response.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=response.get("error_msg", "Unknown error"),
        )
    return response
