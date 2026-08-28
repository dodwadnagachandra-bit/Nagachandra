"""IPC contract tests -- validate message patterns and C/Python interop.

Tests all three message patterns (telemetry, command, event) and verifies
that mpack (C) and msgpack (Python) produce compatible binary encoding.
"""

from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import msgpack
import pytest

from ems_common.ipc import (
    MSG_KEY_ACTION,
    MSG_KEY_DATA,
    MSG_KEY_ERROR_MSG,
    MSG_KEY_EVENT_TYPE,
    MSG_KEY_MESSAGE,
    MSG_KEY_PARAMS,
    MSG_KEY_PAYLOAD,
    MSG_KEY_RESULT,
    MSG_KEY_SEQUENCE,
    MSG_KEY_SEVERITY,
    MSG_KEY_SOURCE,
    MSG_KEY_STATUS,
    MSG_KEY_TIMESTAMP,
    MSG_KEY_TOPIC,
    SOCK_ALARM_CMD,
    SOCK_CONFIG,
    SOCK_CONTROL_CMD,
    SOCK_LOGGER,
    SOCK_TELEMETRY,
    TOPIC_ALARM,
    TOPIC_BMS_RACK,
    TOPIC_BTMS,
    TOPIC_COMM_FAULT,
    TOPIC_CONFIG_RELOAD,
    TOPIC_CONTROL_STATE,
    TOPIC_GPIO,
    TOPIC_METER,
    TOPIC_PCS,
    TOPIC_STATE_CHANGE,
    TOPIC_SYSTEM,
    decode_command_request,
    decode_command_response,
    decode_event,
    decode_telemetry,
    encode_command_request,
    encode_command_response,
    encode_event,
    encode_telemetry,
)

# Root of the repo
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
C_TEST_BINARY: Path = REPO_ROOT / "build" / "tests" / "c" / "test_ipc_interop"


def test_constants_match_c_header() -> None:
    """Verify critical Python constants match expected C values."""
    assert SOCK_TELEMETRY == "ipc:///run/ems/telemetry.sock"
    assert SOCK_CONTROL_CMD == "ipc:///run/ems/control_cmd.sock"
    assert SOCK_ALARM_CMD == "ipc:///run/ems/alarm_cmd.sock"
    assert SOCK_LOGGER == "ipc:///run/ems/logger.sock"
    assert SOCK_CONFIG == "ipc:///run/ems/config.sock"

    assert TOPIC_BMS_RACK == "bms.rack"
    assert TOPIC_PCS == "pcs"
    assert TOPIC_GPIO == "gpio"
    assert TOPIC_METER == "meter"
    assert TOPIC_BTMS == "btms"
    assert TOPIC_SYSTEM == "system"
    assert TOPIC_ALARM == "alarm"
    assert TOPIC_STATE_CHANGE == "state_change"
    assert TOPIC_COMM_FAULT == "comm_fault"
    assert TOPIC_CONFIG_RELOAD == "config_reload"
    assert TOPIC_CONTROL_STATE == "control.state"

    # All socket paths start with ipc:///run/ems/
    for sock in [SOCK_TELEMETRY, SOCK_CONTROL_CMD, SOCK_ALARM_CMD, SOCK_LOGGER, SOCK_CONFIG]:
        assert sock.startswith("ipc:///run/ems/"), f"{sock} must start with ipc:///run/ems/"
        assert len(sock) > 0


def test_telemetry_roundtrip() -> None:
    """Encode and decode a telemetry message, verify all fields."""
    payload: dict = {
        "ac_voltage": 230.5,
        "active_power": 100.0,
        "frequency": 50.01,
        "state": 2,
        "fault_code": 0,
    }
    data: bytes = encode_telemetry(
        timestamp_ms=1234567890,
        seq=42,
        source="data_manager",
        topic="pcs",
        payload=payload,
    )
    msg: dict = decode_telemetry(data)

    assert msg[MSG_KEY_TIMESTAMP] == 1234567890
    assert msg[MSG_KEY_SEQUENCE] == 42
    assert msg[MSG_KEY_SOURCE] == "data_manager"
    assert msg[MSG_KEY_TOPIC] == "pcs"

    p: dict = msg[MSG_KEY_PAYLOAD]
    assert p["ac_voltage"] == pytest.approx(230.5)
    assert p["active_power"] == pytest.approx(100.0)
    assert p["frequency"] == pytest.approx(50.01)
    assert p["state"] == 2
    assert p["fault_code"] == 0


def test_command_request_roundtrip() -> None:
    """Encode and decode a command request, verify action and params."""
    data: bytes = encode_command_request(
        action="set_setpoint",
        params={"power_kw": 100.0},
    )
    action, params = decode_command_request(data)

    assert action == "set_setpoint"
    assert params["power_kw"] == pytest.approx(100.0)


def test_command_response_ok() -> None:
    """Encode and decode a success response."""
    data: bytes = encode_command_response(
        status="ok",
        result={"applied": True},
    )
    msg: dict = decode_command_response(data)

    assert msg[MSG_KEY_STATUS] == "ok"
    assert msg[MSG_KEY_RESULT] == {"applied": True}
    assert msg[MSG_KEY_ERROR_MSG] is None


def test_command_response_error() -> None:
    """Encode and decode an error response."""
    data: bytes = encode_command_response(
        status="error",
        error_msg="Invalid mode",
    )
    msg: dict = decode_command_response(data)

    assert msg[MSG_KEY_STATUS] == "error"
    assert msg[MSG_KEY_ERROR_MSG] == "Invalid mode"
    assert msg[MSG_KEY_RESULT] is None


def test_event_roundtrip() -> None:
    """Encode and decode a structured event with all fields."""
    event_data: dict = {"rack_id": 3, "cell_id": 15, "voltage": 3.65}
    data: bytes = encode_event(
        timestamp_ms=1234567890,
        source="alarm_manager",
        severity="warning",
        event_type="alarm",
        message="Cell voltage high on rack 3",
        data=event_data,
    )
    msg: dict = decode_event(data)

    assert msg[MSG_KEY_TIMESTAMP] == 1234567890
    assert msg[MSG_KEY_SOURCE] == "alarm_manager"
    assert msg[MSG_KEY_SEVERITY] == "warning"
    assert msg[MSG_KEY_EVENT_TYPE] == "alarm"
    assert msg[MSG_KEY_MESSAGE] == "Cell voltage high on rack 3"
    assert msg[MSG_KEY_DATA]["rack_id"] == 3
    assert msg[MSG_KEY_DATA]["cell_id"] == 15
    assert msg[MSG_KEY_DATA]["voltage"] == pytest.approx(3.65)


def test_msgpack_binary_format() -> None:
    """Verify encoded telemetry produces valid msgpack bytes."""
    data: bytes = encode_telemetry(
        timestamp_ms=1000,
        seq=1,
        source="test",
        topic="pcs",
        payload={"v": 1},
    )
    assert isinstance(data, bytes)
    # First byte should be a msgpack map marker
    # fixmap: 0x80-0x8f, map16: 0xde, map32: 0xdf
    first_byte: int = data[0]
    assert (0x80 <= first_byte <= 0x8F) or first_byte in (0xDE, 0xDF), (
        f"Expected msgpack map marker, got 0x{first_byte:02x}"
    )
    # Raw msgpack decode should work
    result: dict = msgpack.unpackb(data, raw=False)
    assert isinstance(result, dict)
    assert "ts" in result


def test_c_python_interop() -> None:
    """Run C test binary, decode its output with Python msgpack."""
    if not C_TEST_BINARY.exists():
        # Try building
        subprocess.run(
            ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Debug"],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["cmake", "--build", "build", "--", f"-j{os.cpu_count() or 1}"],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
        )

    assert C_TEST_BINARY.exists(), f"C test binary not found at {C_TEST_BINARY}"

    output_path: str = "/tmp/ems_ipc_test.bin"
    result = subprocess.run(
        [str(C_TEST_BINARY), "--output", output_path],
        capture_output=True,
        text=True,
        check=True,
    )

    with open(output_path, "rb") as f:
        raw: bytes = f.read()

    # Parse 4 length-prefixed messages
    messages: list[dict] = []
    offset: int = 0
    while offset < len(raw):
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        offset += 4
        msg_bytes: bytes = raw[offset : offset + length]
        offset += length
        messages.append(msgpack.unpackb(msg_bytes, raw=False))

    assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}"

    # Message 1: telemetry
    m1: dict = messages[0]
    assert m1["ts"] == 1234567890
    assert m1["src"] == "data_manager"
    assert m1["topic"] == "pcs"
    assert m1["payload"]["ac_voltage"] == pytest.approx(230.5)
    assert m1["payload"]["active_power"] == pytest.approx(100.0)
    assert m1["payload"]["frequency"] == pytest.approx(50.01)
    assert m1["payload"]["state"] == 2
    assert m1["payload"]["fault_code"] == 0

    # Message 2: command request
    m2: dict = messages[1]
    assert m2["action"] == "set_mode"
    assert m2["params"]["mode"] == "charging"

    # Message 3: command response
    m3: dict = messages[2]
    assert m3["status"] == "ok"
    assert m3["result"]["previous_mode"] == "idle"
    assert m3["error_msg"] is None

    # Message 4: event
    m4: dict = messages[3]
    assert m4["severity"] == "warning"
    assert m4["event_type"] == "alarm"
    assert m4["src"] == "alarm_manager"
    assert m4["message"] == "Cell voltage high on rack 3"
    assert m4["data"]["rack_id"] == 3
    assert m4["data"]["cell_id"] == 15
    assert m4["data"]["voltage"] == pytest.approx(3.65)


def test_all_topics_are_strings() -> None:
    """All TOPIC_* constants are non-empty strings with no duplicates."""
    topics: list[str] = [
        TOPIC_BMS_RACK,
        TOPIC_PCS,
        TOPIC_GPIO,
        TOPIC_METER,
        TOPIC_BTMS,
        TOPIC_SYSTEM,
        TOPIC_ALARM,
        TOPIC_STATE_CHANGE,
        TOPIC_COMM_FAULT,
        TOPIC_CONFIG_RELOAD,
        TOPIC_CONTROL_STATE,
    ]
    for t in topics:
        assert isinstance(t, str), f"{t} is not a string"
        assert len(t) > 0, f"topic is empty"

    assert len(topics) == len(set(topics)), "Duplicate topics found"


def test_all_sockets_are_unique() -> None:
    """All SOCK_* constants are unique and start with ipc://."""
    sockets: list[str] = [
        SOCK_TELEMETRY,
        SOCK_CONTROL_CMD,
        SOCK_ALARM_CMD,
        SOCK_LOGGER,
        SOCK_CONFIG,
    ]
    for s in sockets:
        assert s.startswith("ipc://"), f"{s} must start with ipc://"

    assert len(sockets) == len(set(sockets)), "Duplicate socket paths found"
