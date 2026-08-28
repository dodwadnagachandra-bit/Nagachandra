"""IPC contract definitions -- socket paths, topic strings, schema helpers.

Mirrors src/common/c/include/ipc_defs.h. All modules import from here.
"""

from __future__ import annotations

import msgpack

# ---------------------------------------------------------------------------
# Socket paths
# ---------------------------------------------------------------------------

IPC_BASE_DIR: str = "/run/ems"

SOCK_TELEMETRY: str = "ipc:///run/ems/telemetry.sock"
SOCK_CONTROL_CMD: str = "ipc:///run/ems/control_cmd.sock"
SOCK_ALARM_CMD: str = "ipc:///run/ems/alarm_cmd.sock"
SOCK_LOGGER: str = "ipc:///run/ems/logger.sock"
SOCK_LOGGER_QUERY: str = "ipc:///run/ems/logger_query.sock"
SOCK_CONFIG: str = "ipc:///run/ems/config.sock"
SOCK_CONFIG_PUB: str = "ipc:///run/ems/config_pub.sock"
SOCK_SCHEDULER_PUB: str = "ipc:///run/ems/scheduler_pub.sock"
SOCK_CLOUD_PUB: str = "ipc:///run/ems/cloud_pub.sock"
SOCK_ALARM_PUB: str = "ipc:///run/ems/alarm_pub.sock"
SOCK_OTA_PUB: str = "ipc:///run/ems/ota_pub.sock"
SOCK_OTA_CMD: str = "ipc:///run/ems/ota_cmd.sock"
SOCK_DIAGNOSTICS_CMD: str = "ipc:///run/ems/diagnostics_cmd.sock"
SOCK_DIAGNOSTICS_PUB: str = "ipc:///run/ems/diagnostics_pub.sock"

# ---------------------------------------------------------------------------
# PUB/SUB topics (telemetry -- published by data_manager at 1 Hz)
# ---------------------------------------------------------------------------

TOPIC_BMS_RACK: str = "bms.rack"
TOPIC_PCS: str = "pcs"
TOPIC_GPIO: str = "gpio"
TOPIC_METER: str = "meter"
TOPIC_BTMS: str = "btms"
TOPIC_SYSTEM: str = "system"

# ---------------------------------------------------------------------------
# Event topics (PUSH/PULL prefixes)
# ---------------------------------------------------------------------------

TOPIC_ALARM: str = "alarm"
TOPIC_STATE_CHANGE: str = "state_change"
TOPIC_COMM_FAULT: str = "comm_fault"
TOPIC_CONFIG_RELOAD: str = "config_reload"

# Control state topic
TOPIC_CONTROL_STATE: str = "control.state"

# Scheduler topic
TOPIC_SCHEDULE: str = "schedule"

# Cloud topic
TOPIC_CLOUD: str = "cloud"

# OTA topic
TOPIC_OTA: str = "ota"

# Diagnostics topic
TOPIC_DIAGNOSTICS: str = "diagnostics"

# ---------------------------------------------------------------------------
# Message envelope field keys
# ---------------------------------------------------------------------------

MSG_KEY_TIMESTAMP: str = "ts"
MSG_KEY_SEQUENCE: str = "seq"
MSG_KEY_SOURCE: str = "src"
MSG_KEY_TOPIC: str = "topic"
MSG_KEY_PAYLOAD: str = "payload"

# Command request/response keys
MSG_KEY_ACTION: str = "action"
MSG_KEY_PARAMS: str = "params"
MSG_KEY_STATUS: str = "status"
MSG_KEY_RESULT: str = "result"
MSG_KEY_ERROR_MSG: str = "error_msg"

# Event keys
MSG_KEY_SEVERITY: str = "severity"
MSG_KEY_EVENT_TYPE: str = "event_type"
MSG_KEY_MESSAGE: str = "message"
MSG_KEY_DATA: str = "data"

# ---------------------------------------------------------------------------
# Status and severity string constants
# ---------------------------------------------------------------------------

STATUS_OK: str = "ok"
STATUS_ERROR: str = "error"

SEVERITY_INFO: str = "info"
SEVERITY_WARNING: str = "warning"
SEVERITY_ERROR: str = "error"
SEVERITY_CRITICAL: str = "critical"

# ---------------------------------------------------------------------------
# Schema helpers -- encode/decode for each message pattern
# ---------------------------------------------------------------------------


def encode_telemetry(
    timestamp_ms: int,
    seq: int,
    source: str,
    topic: str,
    payload: dict,
) -> bytes:
    """Encode a telemetry envelope with nested payload."""
    msg: dict = {
        MSG_KEY_TIMESTAMP: timestamp_ms,
        MSG_KEY_SEQUENCE: seq,
        MSG_KEY_SOURCE: source,
        MSG_KEY_TOPIC: topic,
        MSG_KEY_PAYLOAD: payload,
    }
    return msgpack.packb(msg, use_bin_type=True)


def decode_telemetry(data: bytes) -> dict:
    """Decode a telemetry message, return dict with envelope + payload."""
    return msgpack.unpackb(data, raw=False)


def encode_command_request(action: str, params: dict) -> bytes:
    """Encode a command request: {action, params}."""
    msg: dict = {
        MSG_KEY_ACTION: action,
        MSG_KEY_PARAMS: params,
    }
    return msgpack.packb(msg, use_bin_type=True)


def decode_command_request(data: bytes) -> tuple[str, dict]:
    """Decode a command request. Returns (action, params)."""
    msg: dict = msgpack.unpackb(data, raw=False)
    return msg[MSG_KEY_ACTION], msg[MSG_KEY_PARAMS]


def encode_command_response(
    status: str,
    result: dict | None = None,
    error_msg: str | None = None,
) -> bytes:
    """Encode a command response: {status, result, error_msg}."""
    msg: dict = {
        MSG_KEY_STATUS: status,
        MSG_KEY_RESULT: result,
        MSG_KEY_ERROR_MSG: error_msg,
    }
    return msgpack.packb(msg, use_bin_type=True)


def decode_command_response(data: bytes) -> dict:
    """Decode a command response. Returns dict with status, result, error_msg."""
    return msgpack.unpackb(data, raw=False)


def encode_event(
    timestamp_ms: int,
    source: str,
    severity: str,
    event_type: str,
    message: str,
    data: dict | None = None,
) -> bytes:
    """Encode a structured event for PUSH/PULL."""
    msg: dict = {
        MSG_KEY_TIMESTAMP: timestamp_ms,
        MSG_KEY_SOURCE: source,
        MSG_KEY_SEVERITY: severity,
        MSG_KEY_EVENT_TYPE: event_type,
        MSG_KEY_MESSAGE: message,
        MSG_KEY_DATA: data,
    }
    return msgpack.packb(msg, use_bin_type=True)


def decode_event(data: bytes) -> dict:
    """Decode a structured event. Returns dict with all event fields."""
    return msgpack.unpackb(data, raw=False)
