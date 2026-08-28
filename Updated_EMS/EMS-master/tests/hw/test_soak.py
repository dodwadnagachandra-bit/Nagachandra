"""Stage 5: 24-Hour Soak Test — pytest wrapper.

Tests in this module require:
- ECU hardware connected (auto-skipped if ECU is unreachable)
- CAN simulator running on a laptop connected via USB-CAN adapter
- Modbus simulator running on a laptop connected via USB-RS485 adapter
- All 14 EMS services active on the ECU

IMPORTANT: Exclude this module from normal test runs.
  Normal suite:   uv run pytest tests/ --ignore=tests/hw/test_soak.py
  Soak test only: uv run pytest tests/hw/test_soak.py -v

The test_soak_24h test has a 25-hour timeout (90000 seconds). Do not run
it in CI — it requires physical hardware and a dedicated 25-hour window.

test_soak_results_exist is a quick post-soak check: run it after the soak
test completes to confirm results in pytest format without re-running the full
24-hour test.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.hw.conftest import ECU_IP, ECU_USER, DATA_DIR, EMS_VENV, ecu_ssh

# ---------------------------------------------------------------------------
# Module-level markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.hw,
    pytest.mark.timeout(90000),  # 25 hours max for the soak test
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOAK_OUTPUT_PATH: str = f"{DATA_DIR}/soak_monitor.jsonl"
ECU_MONITOR_PATH: str = "/opt/ems/soak_monitor.py"
SOAK_POLL_INTERVAL_S: int = 300  # poll every 5 minutes
SOAK_MONITOR_LOCAL: Path = (
    Path(__file__).parent.parent.parent / "tools" / "hw-validation" / "soak_monitor.py"
)


# ---------------------------------------------------------------------------
# Helper: SCP a file to the ECU
# ---------------------------------------------------------------------------


def _scp_to_ecu(local_path: Path, remote_path: str) -> None:
    """Copy a local file to the ECU via SCP.

    Args:
        local_path: Path to local file.
        remote_path: Destination path on the ECU.

    Raises:
        AssertionError: If SCP fails.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            str(local_path),
            f"{ECU_USER}@{ECU_IP}:{remote_path}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"SCP failed: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# test_soak_preconditions
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_soak_preconditions() -> None:
    """Verify CAN traffic and Modbus responses are live before committing to 24h soak.

    This is a dependency for test_soak_24h. Run this first. If this test
    fails, do NOT run test_soak_24h — the soak results would be invalid
    because the system under test would not be receiving real inputs.
    """
    errors: list[str] = []

    # --- CAN traffic check ---
    can_result = ecu_ssh("candump can0 -n 1 -T 5000")
    if can_result.returncode != 0 or not can_result.stdout.strip():
        errors.append(
            "CAN bus traffic check FAILED: No CAN frame received on can0 within 5 seconds.\n"
            "  Fix: Start the CAN simulator on your laptop:\n"
            "    cd simulators/can && uv run python can_simulator.py\n"
            "  Connect USB-CAN adapter from laptop to ECU CAN0 bus.\n"
            "  Verify termination resistors (120 Ω at each end of the bus)."
        )
    else:
        # Confirm output looks like a CAN frame (not an error message)
        stdout: str = can_result.stdout.strip()
        if any(w in stdout.lower() for w in ["error", "cannot", "no such"]):
            errors.append(
                f"CAN bus check returned an error: {stdout}\n"
                "  Verify can0 interface is up: 'ip link show can0'"
            )

    # --- Modbus response check ---
    modbus_cmd: str = (
        "python3 -c \""
        "from pymodbus.client import ModbusSerialClient; "
        "c = ModbusSerialClient('/dev/ttyS1', baudrate=9600, timeout=3); "
        "c.connect(); "
        "r = c.read_holding_registers(0, 1, slave=1); "
        "print('OK' if not r.isError() else 'ERR'); "
        "c.close()"
        "\""
    )
    modbus_result = ecu_ssh(modbus_cmd)
    if modbus_result.returncode != 0 or "OK" not in modbus_result.stdout:
        errors.append(
            "Modbus response check FAILED: No valid response from RS485-1 (/dev/ttyS1).\n"
            f"  Output: {modbus_result.stdout!r}\n"
            "  Fix: Start the Modbus PCS simulator on your laptop:\n"
            "    cd simulators/modbus && uv run python pcs_simulator.py\n"
            "  Connect USB-RS485 adapter from laptop to ECU RS485-1 port.\n"
            "  Verify baud rate: 9600 baud, 8N1, slave ID: 1."
        )

    if errors:
        pytest.fail(
            "Soak test pre-conditions not met — do NOT run test_soak_24h.\n\n"
            + "\n\n".join(errors)
        )


# ---------------------------------------------------------------------------
# test_soak_24h
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_soak_24h() -> None:
    """Run the 24-hour soak test on the ECU.

    Copies soak_monitor.py to the ECU, starts it, and polls every 5 minutes
    until the soak completes. Asserts that the final result is PASS.

    Timeout: 25 hours (90000 seconds) — configured via pytestmark.

    Pre-conditions: test_soak_preconditions must pass first.
    """
    # Verify soak_monitor.py exists locally
    assert SOAK_MONITOR_LOCAL.exists(), (
        f"soak_monitor.py not found at {SOAK_MONITOR_LOCAL}. "
        "Run this test from the repository root."
    )

    # Copy soak_monitor.py to ECU
    _scp_to_ecu(SOAK_MONITOR_LOCAL, ECU_MONITOR_PATH)

    # Make executable
    chmod_result = ecu_ssh(f"chmod +x {ECU_MONITOR_PATH}")
    assert chmod_result.returncode == 0, (
        f"Failed to chmod soak_monitor.py on ECU: {chmod_result.stderr}"
    )

    # Clear previous output
    ecu_ssh(f"rm -f {SOAK_OUTPUT_PATH}")

    # Launch soak monitor via nohup (survives SSH disconnect)
    launch_cmd: str = (
        f"nohup {EMS_VENV}/bin/python3 {ECU_MONITOR_PATH} "
        f"--duration 86400 "
        f"--output {SOAK_OUTPUT_PATH} "
        f"> /data/soak_monitor.log 2>&1 & "
        f"echo $!"
    )
    launch_result = ecu_ssh(launch_cmd)
    assert launch_result.returncode == 0, (
        f"Failed to launch soak monitor: {launch_result.stderr}"
    )
    pid: str = launch_result.stdout.strip()
    assert pid.isdigit(), f"Expected PID, got: {pid!r}"
    print(f"\n[soak] soak_monitor.py started on ECU with PID {pid}", flush=True)

    # Poll for completion by reading last line of soak_monitor.jsonl
    soak_duration_s: int = 86400  # 24 hours
    poll_deadline: float = time.time() + soak_duration_s + 3600  # 25h deadline

    while time.time() < poll_deadline:
        time.sleep(SOAK_POLL_INTERVAL_S)

        # Read last line of JSONL output
        tail_result = ecu_ssh(f"tail -1 {SOAK_OUTPUT_PATH} 2>/dev/null || echo ''")
        last_line: str = tail_result.stdout.strip()

        if not last_line:
            print("[soak] No output yet, continuing to poll...", flush=True)
            continue

        try:
            record: dict[str, object] = json.loads(last_line)
        except json.JSONDecodeError:
            print(f"[soak] Non-JSON output: {last_line!r}", flush=True)
            continue

        check: str = str(record.get("check", ""))
        timestamp: str = str(record.get("timestamp", ""))
        print(f"[soak] Last check: {check} @ {timestamp}", flush=True)

        if check == "summary":
            result: str = str(record.get("result", "FAIL"))
            total_restarts: int = int(record.get("total_restarts", -1))
            max_rss_growth: float = float(record.get("max_rss_growth_pct", -1))
            data_gaps: int = int(record.get("data_gaps", -1))

            assert result == "PASS", (
                f"Soak test FAILED.\n"
                f"  Total restarts:   {total_restarts} (target: 0)\n"
                f"  Max RSS growth:   {max_rss_growth:.1f}% (target: <10%)\n"
                f"  Data gaps:        {data_gaps} (target: 0)\n"
                f"\nFull summary:\n{json.dumps(record, indent=2)}"
            )
            # PASS
            print(
                f"\n[soak] PASS — {soak_duration_s/3600:.0f}h soak completed successfully",
                flush=True,
            )
            return

    pytest.fail(
        "Soak test timed out: soak_monitor.py did not write a summary line within 25 hours."
    )


# ---------------------------------------------------------------------------
# test_soak_results_exist
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_soak_results_exist() -> None:
    """Quick post-soak check: read JSONL summary and assert PASS.

    Run this test AFTER the 24-hour soak completes to confirm results in
    pytest report format, without re-running the full soak test.

    Usage:
        uv run pytest tests/hw/test_soak.py::test_soak_results_exist -v
    """
    # Read last line of soak output
    tail_result = ecu_ssh(f"tail -1 {SOAK_OUTPUT_PATH} 2>/dev/null || echo ''")
    last_line: str = tail_result.stdout.strip()

    assert last_line, (
        f"No soak results found at {SOAK_OUTPUT_PATH}.\n"
        "Run the 24-hour soak test first:\n"
        "  uv run pytest tests/hw/test_soak.py::test_soak_preconditions -v\n"
        "  uv run pytest tests/hw/test_soak.py::test_soak_24h -v"
    )

    try:
        record: dict[str, object] = json.loads(last_line)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Last line of {SOAK_OUTPUT_PATH} is not valid JSON: {exc}\n{last_line!r}")

    # Must be a summary record
    check: str = str(record.get("check", ""))
    assert check == "summary", (
        f"Last record is not a summary (check={check!r}). "
        "The soak test may still be running. "
        f"Full record: {json.dumps(record, indent=2)}"
    )

    result: str = str(record.get("result", "FAIL"))
    total_restarts: int = int(record.get("total_restarts", -1))
    max_rss_growth: float = float(record.get("max_rss_growth_pct", -1))
    data_gaps: int = int(record.get("data_gaps", -1))
    duration_s: int = int(record.get("duration_s", 0))

    assert result == "PASS", (
        f"Soak test results show FAIL.\n"
        f"  Duration:         {duration_s / 3600:.2f}h\n"
        f"  Total restarts:   {total_restarts} (target: 0)\n"
        f"  Max RSS growth:   {max_rss_growth:.1f}% (target: <10%)\n"
        f"  Data gaps:        {data_gaps} (target: 0)\n"
        f"\nFull summary:\n{json.dumps(record, indent=2)}"
    )

    # Report success with measurements
    print(
        f"\n[soak results] PASS\n"
        f"  Duration:       {duration_s / 3600:.2f}h\n"
        f"  Restarts:       {total_restarts}\n"
        f"  Max RSS growth: {max_rss_growth:.1f}%\n"
        f"  Data gaps:      {data_gaps}",
        flush=True,
    )
