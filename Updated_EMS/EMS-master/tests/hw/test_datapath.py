"""Stage 3: Data Pipeline Integrity on ARM64 — ECU-1170-552A.

Validates the full CAN simulator -> RTDB -> ZMQ -> Parquet pipeline on real
ARM64 hardware. All tests run via SSH to the ECU using the helpers in conftest.

Run from developer laptop (requires SSH access to ECU):
    uv run pytest tests/hw/test_datapath.py -v

Skip when ECU is offline (graceful no-hardware behaviour):
    All tests auto-skip via ecu_reachable session fixture.
"""

from __future__ import annotations

import time

import pytest

from tests.hw.conftest import DATA_DIR, EMS_VENV, ecu_ssh

# All tests in this module require ECU hardware and respect the 600s timeout
pytestmark = [pytest.mark.hw, pytest.mark.timeout(600)]


# ---------------------------------------------------------------------------
# Helper: run a Python one-liner on the ECU via the Yocto venv
# ---------------------------------------------------------------------------


def _run_ecu_python(script: str, timeout_override: int = 30) -> str:
    """Run a Python one-liner on the ECU via the Yocto venv.

    Args:
        script: Single-quoted Python source to execute.
        timeout_override: SSH timeout in seconds (default 30).

    Returns:
        Stripped stdout from the ECU.
    """
    import subprocess

    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            f"root@192.168.1.100",
            f"{EMS_VENV}/bin/python3 -c '{script}'",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_override,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Stage 3: Data pipeline tests (CAN sim -> RTDB -> ZMQ -> Parquet)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ecu_reachable")
def test_rtdb_shm_exists() -> None:
    """RTDB shared memory file must exist — confirms data_manager created it.

    Looks for /dev/shm/ems_rtdb* on the ECU. At least one file means
    data_manager_c has run shm_open() and mapped the RTDB successfully.
    """
    result = ecu_ssh("ls /dev/shm/ems_rtdb* 2>/dev/null")
    shm_files: list[str] = [
        line for line in result.stdout.strip().splitlines() if line
    ]
    assert shm_files, (
        f"No /dev/shm/ems_rtdb* files found on ECU — "
        f"data_manager_c may not have started (stderr: {result.stderr.strip()!r})"
    )
    print(f"\nRTDB SHM files: {shm_files}")


@pytest.mark.usefixtures("ecu_reachable")
def test_zmq_telemetry_publishing() -> None:
    """ZMQ telemetry socket must be publishing messages on ipc:///run/ems/telemetry.sock.

    Subscribes to the telemetry socket for 10 seconds using the Yocto venv.
    Verifies at least one multipart message is received, confirming data_manager
    is pumping telemetry into ZMQ.
    """
    script: str = (
        "import zmq; "
        "ctx=zmq.Context(); "
        "s=ctx.socket(zmq.SUB); "
        "s.setsockopt(zmq.RCVTIMEO, 10000); "
        "s.setsockopt(zmq.LINGER, 0); "
        "s.connect('ipc:///run/ems/telemetry.sock'); "
        "s.setsockopt_string(zmq.SUBSCRIBE, ''); "
        "msg=s.recv_multipart(); "
        "print('OK', len(msg), 'frames,', sum(len(f) for f in msg), 'bytes')"
    )
    result = ecu_ssh(f"{EMS_VENV}/bin/python3 -c \"{script}\"")
    assert result.returncode == 0, (
        f"ZMQ telemetry receive failed (rc={result.returncode}): "
        f"{result.stderr.strip()!r}"
    )
    output: str = result.stdout.strip()
    assert output.startswith("OK"), (
        f"Unexpected ZMQ output: {output!r} (stderr: {result.stderr.strip()!r})"
    )
    print(f"\nZMQ telemetry: {output}")


@pytest.mark.usefixtures("ecu_reachable")
def test_parquet_files_created() -> None:
    """Parquet files must be created in /data within 5 seconds of a timestamp marker.

    Creates /tmp/hw_test_marker, waits 5 seconds, then finds Parquet files newer
    than the marker. At least one match confirms logger is writing to SSD.
    """
    # Create timestamp marker
    mk_result = ecu_ssh("touch /tmp/hw_test_marker")
    assert mk_result.returncode == 0, (
        f"Failed to create marker on ECU: {mk_result.stderr.strip()!r}"
    )

    # Wait for logger to produce at least one new file
    time.sleep(5)

    find_result = ecu_ssh(
        f"find {DATA_DIR} -name '*.parquet' -newer /tmp/hw_test_marker 2>/dev/null | head -5"
    )
    parquet_files: list[str] = [
        line for line in find_result.stdout.strip().splitlines() if line
    ]
    assert parquet_files, (
        f"No new Parquet files in {DATA_DIR} after 5s — "
        f"logger may not be writing (stderr: {find_result.stderr.strip()!r})"
    )
    print(f"\nNew Parquet files: {parquet_files}")


@pytest.mark.usefixtures("ecu_reachable")
def test_parquet_row_count_growing() -> None:
    """Parquet row count must increase over 10 seconds — confirms 1Hz writes.

    Reads row count at t=0, waits 10 seconds, reads again.  The delta must
    be positive, proving the logger continuously appends telemetry rows.
    """
    count_script: str = (
        "import pyarrow.parquet as pq, pathlib; "
        f"files=list(pathlib.Path('{DATA_DIR}').rglob('*.parquet')); "
        "total=sum(pq.read_metadata(str(f)).num_rows for f in files if f.exists()); "
        "print(total)"
    )

    result_t0 = ecu_ssh(f"{EMS_VENV}/bin/python3 -c \"{count_script}\"")
    assert result_t0.returncode == 0, (
        f"pyarrow row count at t=0 failed: {result_t0.stderr.strip()!r}"
    )
    rows_t0: int = int(result_t0.stdout.strip() or "0")
    print(f"\nParquet rows at t=0: {rows_t0}")

    time.sleep(10)

    result_t10 = ecu_ssh(f"{EMS_VENV}/bin/python3 -c \"{count_script}\"")
    assert result_t10.returncode == 0, (
        f"pyarrow row count at t=10s failed: {result_t10.stderr.strip()!r}"
    )
    rows_t10: int = int(result_t10.stdout.strip() or "0")
    print(f"Parquet rows at t=10s: {rows_t10}")

    delta: int = rows_t10 - rows_t0
    assert delta > 0, (
        f"Parquet row count did not grow over 10s: "
        f"t0={rows_t0}, t10={rows_t10} (delta={delta})"
    )
    print(f"Row delta: +{delta} rows in 10s ({delta / 10.0:.1f} rows/sec)")


@pytest.mark.usefixtures("ecu_reachable")
def test_duckdb_query_works() -> None:
    """DuckDB must execute a COUNT(*) query over Parquet data without error.

    Runs read_parquet('/data/**/*.parquet') on the ECU.  A non-zero return
    code or exception confirms DuckDB, pyarrow, and the Parquet data are
    all functioning correctly on ARM64.
    """
    query_script: str = (
        "import duckdb; "
        f"result=duckdb.execute(\\\"SELECT COUNT(*) FROM read_parquet('{DATA_DIR}/**/*.parquet')\\\").fetchone(); "
        "print('rows:', result[0])"
    )
    result = ecu_ssh(f"{EMS_VENV}/bin/python3 -c \"{query_script}\"")
    assert result.returncode == 0, (
        f"DuckDB query failed on ECU (rc={result.returncode}): "
        f"{result.stderr.strip()!r}"
    )
    output: str = result.stdout.strip()
    assert output.startswith("rows:"), (
        f"Unexpected DuckDB output: {output!r}"
    )
    row_count_str: str = output.split(":", 1)[1].strip()
    row_count: int = int(row_count_str)
    print(f"\nDuckDB COUNT(*) result: {row_count} rows")
    assert row_count >= 0, f"Unexpected negative row count: {row_count}"
