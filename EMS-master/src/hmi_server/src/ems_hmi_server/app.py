"""FastAPI application factory with lifespan context manager."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import zmq
import zmq.asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ems_common.ipc import SOCK_CLOUD_PUB, SOCK_OTA_PUB, SOCK_TELEMETRY
from ems_hmi_server.alarm import router as alarm_router
from ems_hmi_server.auth import TokenStore
from ems_hmi_server.auth import router as auth_router
from ems_hmi_server.control import router as control_router
from ems_hmi_server.health import router as health_router
from ems_hmi_server.query import router as query_router
from ems_hmi_server.schedule import router as schedule_router
from ems_hmi_server.ws import ClientManager, router as ws_router, telemetry_bridge


async def _cleanup_loop(token_store: TokenStore) -> None:
    """Periodically clean up expired tokens every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        token_store.cleanup_expired()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle -- startup and shutdown."""
    app.state.start_time = time.time()
    app.state.zmq_ctx = zmq.asyncio.Context()
    app.state.client_manager = ClientManager()

    # Start token cleanup background task
    cleanup_task: asyncio.Task = asyncio.create_task(
        _cleanup_loop(app.state.token_store)
    )

    # Determine socket paths (overridable for testing)
    telemetry_socket: str = getattr(
        app.state, "telemetry_socket", SOCK_TELEMETRY
    )
    cloud_socket: str = getattr(app.state, "cloud_socket", SOCK_CLOUD_PUB)
    ota_socket: str = getattr(app.state, "ota_socket", SOCK_OTA_PUB)

    # Start telemetry bridge background task (polls telemetry + cloud + ota)
    bridge_task: asyncio.Task = asyncio.create_task(
        telemetry_bridge(
            app.state.zmq_ctx,
            app.state.client_manager,
            telemetry_socket,
            cloud_socket_path=cloud_socket,
            ota_socket_path=ota_socket,
        )
    )

    yield

    # Shutdown
    bridge_task.cancel()
    try:
        await bridge_task
    except asyncio.CancelledError:
        pass

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    app.state.zmq_ctx.term()


def create_app(
    config: dict,
    telemetry_socket: str | None = None,
    cloud_socket: str | None = None,
    ota_socket: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Parsed HMI configuration dictionary.
        telemetry_socket: Optional ZMQ endpoint override for telemetry SUB
            (defaults to SOCK_TELEMETRY). Used by tests to point at tcp://.
        cloud_socket: Optional ZMQ endpoint override for cloud status SUB
            (defaults to SOCK_CLOUD_PUB). Pass empty string to disable.
        ota_socket: Optional ZMQ endpoint override for OTA status SUB
            (defaults to SOCK_OTA_PUB). Pass empty string to disable.

    Returns:
        Configured FastAPI application instance.
    """
    app: FastAPI = FastAPI(
        title="EMS HMI Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store config on app state (available during lifespan and request handling)
    app.state.config = config
    app.state.token_store = TokenStore()
    # Set defaults that lifespan will override in production.
    # This ensures the app works in test environments where lifespan
    # may not be triggered (e.g., httpx ASGITransport).
    app.state.start_time = time.time()
    app.state.zmq_ctx = None
    app.state.client_manager = ClientManager()

    # Store socket overrides for lifespan to pick up
    if telemetry_socket is not None:
        app.state.telemetry_socket = telemetry_socket
    if cloud_socket is not None:
        app.state.cloud_socket = cloud_socket
    if ota_socket is not None:
        app.state.ota_socket = ota_socket

    # CORS middleware for Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(control_router)
    app.include_router(alarm_router)
    app.include_router(query_router)
    app.include_router(schedule_router)
    app.include_router(ws_router)

    # Resolve frontend dist directory relative to package location
    frontend_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend",
        "dist",
    )

    # Mount static files LAST (after all routes) -- only if build exists
    # html=True enables SPA fallback: serves index.html for paths that don't
    # match a static file, which handles React Router client-side routes.
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")

    return app
