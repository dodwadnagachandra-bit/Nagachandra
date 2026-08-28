"""Comm manager event publisher functions.

Publishes structured comm_fault, comm_recovery, and comm_exception events
via ZMQ PUSH socket to the logger. Uses encode_event from ems_common.ipc
for MessagePack envelope encoding.
"""

from __future__ import annotations

import time

import zmq

from ems_common.ipc import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    TOPIC_COMM_FAULT,
    encode_event,
)

# Event types
EVENT_COMM_RECOVERY: str = "comm_recovery"
EVENT_COMM_EXCEPTION: str = "comm_exception"

# Source identifier
SOURCE: str = "comm_manager"


def _now_ms() -> int:
    """Return current time in milliseconds since epoch."""
    return int(time.time() * 1000)


def publish_comm_fault(
    push_socket: zmq.Socket,
    device_id: str,
    device_address: int,
    port: str,
    fault_type: str,
    last_seen_ms: int,
    consecutive_failures: int,
) -> None:
    """Publish a comm_fault event when device goes offline.

    Args:
        push_socket: ZMQ PUSH socket connected to logger.
        device_id: Device identifier (e.g., "pcs", "btms").
        device_address: Modbus slave address.
        port: Serial port path.
        fault_type: Fault type string (timeout, crc_error, exception_code, etc.).
        last_seen_ms: Timestamp of last successful communication.
        consecutive_failures: Number of consecutive failures at transition.
    """
    data: dict = {
        "device_id": device_id,
        "device_address": device_address,
        "port": port,
        "fault_type": fault_type,
        "last_seen_ms": last_seen_ms,
        "consecutive_failures": consecutive_failures,
    }
    msg: bytes = encode_event(
        timestamp_ms=_now_ms(),
        source=SOURCE,
        severity=SEVERITY_ERROR,
        event_type=TOPIC_COMM_FAULT,
        message=f"Device {device_id} offline: {fault_type}",
        data=data,
    )
    try:
        push_socket.send(msg, zmq.NOBLOCK)
    except zmq.Again:
        pass  # Drop on EAGAIN -- non-blocking send


def publish_comm_recovery(
    push_socket: zmq.Socket,
    device_id: str,
    device_address: int,
    port: str,
    offline_duration_ms: int,
) -> None:
    """Publish a comm_recovery event when device comes back online.

    Args:
        push_socket: ZMQ PUSH socket connected to logger.
        device_id: Device identifier.
        device_address: Modbus slave address.
        port: Serial port path.
        offline_duration_ms: How long the device was offline.
    """
    data: dict = {
        "device_id": device_id,
        "device_address": device_address,
        "port": port,
        "offline_duration_ms": offline_duration_ms,
    }
    msg: bytes = encode_event(
        timestamp_ms=_now_ms(),
        source=SOURCE,
        severity=SEVERITY_INFO,
        event_type=EVENT_COMM_RECOVERY,
        message=f"Device {device_id} recovered after {offline_duration_ms}ms offline",
        data=data,
    )
    try:
        push_socket.send(msg, zmq.NOBLOCK)
    except zmq.Again:
        pass


def publish_comm_exception(
    push_socket: zmq.Socket,
    device_id: str,
    device_address: int,
    port: str,
    exception_code: int,
    message: str,
) -> None:
    """Publish a comm_exception event for Modbus exception responses.

    Severity is WARNING for codes 01-03, ERROR for code 04 (Device Failure).

    Args:
        push_socket: ZMQ PUSH socket connected to logger.
        device_id: Device identifier.
        device_address: Modbus slave address.
        port: Serial port path.
        exception_code: Modbus exception code (1-4).
        message: Human-readable exception description.
    """
    severity: str = SEVERITY_ERROR if exception_code >= 4 else SEVERITY_WARNING
    data: dict = {
        "device_id": device_id,
        "device_address": device_address,
        "port": port,
        "exception_code": exception_code,
        "message": message,
    }
    msg: bytes = encode_event(
        timestamp_ms=_now_ms(),
        source=SOURCE,
        severity=severity,
        event_type=EVENT_COMM_EXCEPTION,
        message=f"Device {device_id} exception {exception_code}: {message}",
        data=data,
    )
    try:
        push_socket.send(msg, zmq.NOBLOCK)
    except zmq.Again:
        pass
