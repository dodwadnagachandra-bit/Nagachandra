"""Pydantic v2 request/response models for HMI server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Health ---


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    uptime_s: int


# --- Auth ---


class LoginRequest(BaseModel):
    """PIN login request."""

    pin: str = Field(min_length=1)


class LoginResponse(BaseModel):
    """Successful login response with bearer token."""

    token: str
    level: str
    expires_in: int


class LogoutRequest(BaseModel):
    """Logout request -- token comes from Authorization header, body empty."""

    pass


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class ErrorResponse(BaseModel):
    """Error detail response."""

    detail: str


# --- Control ---


class ModeChangeRequest(BaseModel):
    """Request to change control mode/state."""

    target_state: str


class ManualSetpointRequest(BaseModel):
    """Request to set manual power setpoint."""

    power_kw: float


class SourcePriorityRequest(BaseModel):
    """Request to set source priority mode."""

    mode: str


class MaintenanceRequest(BaseModel):
    """Request to enter or exit maintenance mode."""

    action: Literal["enter", "exit"]


# --- Alarms ---


class AcknowledgeRequest(BaseModel):
    """Request to acknowledge an alarm."""

    alarm_id: str


# --- Query ---


class QueryRequest(BaseModel):
    """Data query request to logger."""

    type: str
    params: dict = Field(default_factory=dict)


# --- ZMQ response ---


class ZmqResponse(BaseModel):
    """Standard ZMQ command response envelope."""

    status: str
    result: dict | None = None
    error_msg: str | None = None
