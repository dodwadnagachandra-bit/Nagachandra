"""M3 integration tests -- E2E command flow, telemetry streaming, schedule
dispatch, and WebSocket reconnection.

Validates end-to-end:
- Command flow: REST POST -> ZMQ REQ -> control_manager -> RTDB -> PCS
- Telemetry flow: simulator -> RTDB -> ZMQ PUB -> WebSocket -> JSON
- Schedule dispatch: SchedulerLoop(now_func) -> ZMQ REQ -> control_manager -> RTDB
- WebSocket reconnection: kill hmi_server, restart, reconnect within 10s

These tests are the "graduation test" for M3 -- they prove that hmi_server
and scheduler work correctly with all M1+M2 modules in a running system.

Prerequisites:
- vcan0 virtual CAN interface (run 'make sim' to create)
- C binaries built in build/ directory (run 'make build')
- All Python modules installed in the workspace (uv sync --all-packages)
- /run/ems/ directory exists (for IPC sockets)

Test isolation:
- hmi_server uses ipc:// for ZMQ control/alarm sockets (matches backend modules)
- hmi_server HTTP port is randomly allocated to avoid conflicts
- Each test class has its own class-scoped system fixture
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import msgpack
import pytest
import yaml
import zmq

try:
    import websockets
    import websockets.exceptions
except ImportError:
    pytest.skip(
        "websockets not installed -- run 'uv add --dev websockets'",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Import conftest helpers
# ---------------------------------------------------------------------------

_INTEG_DIR: Path = Path(__file__).resolve().parent
if str(_INTEG_DIR) not in sys.path:
    sys.path.insert(0, str(_INTEG_DIR))

from conftest import (  # noqa: E402
    BUILD_DIR,
    CONFIG_DIR,
    ModuleProcess,
    PROJECT_ROOT,
    PROFILES,
    _check_http_health,
    check_rtdb_exists,
    check_rtdb_fresh,
    wait_for_criteria,
)

from ems_common.ipc import (  # noqa: E402
    decode_command_response,
    encode_command_request,
    TOPIC_CONTROL_STATE,
)
from ems_common.rtdb import attach_rtdb, detach_rtdb  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level markers
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# ---------------------------------------------------------------------------
# State constants (mirror ems_control_manager/state_machine.py)
# ---------------------------------------------------------------------------

STATE_INIT: int = 0
STATE_IDLE: int = 1
STATE_STANDBY: int = 2
STATE_CHARGING: int = 3
STATE_DISCHARGING: int = 4
STATE_FAULT: int = 5
STATE_EMERGENCY: int = 6
STATE_MAINTENANCE: int = 7

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allocate_tcp_ports(count: int) -> list[int]:
    """Allocate ``count`` random free TCP ports."""
    ports: list[int] = []
    sockets: list[socket.socket] = []
    for _ in range(count):
        s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        ports.append(s.getsockname()[1])
        sockets.append(s)
    for s in sockets:
        s.close()
    return ports


def _vcan_available() -> bool:
    """Return True if the vcan0 network interface exists."""
    return Path("/sys/class/net/vcan0").exists()


def _binary_exists(name: str) -> bool:
    """Return True if the named C binary is available in the build directory."""
    binaries: dict[str, Path] = {
        "data_manager_c": BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c",
        "safety_manager": BUILD_DIR / "src" / "safety_manager" / "safety_manager",
        "comm_manager_c": BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c",
    }
    path: Path | None = binaries.get(name)
    return path is not None and path.exists()


def _delay_ready(seconds: float) -> Callable[[], bool]:
    """Return a ready_check that passes after ``seconds`` of wall-clock time."""
    _start: list[float] = [0.0]
    _initialized: list[bool] = [False]

    def _check() -> bool:
        if not _initialized[0]:
            _start[0] = time.monotonic()
            _initialized[0] = True
        return (time.monotonic() - _start[0]) >= seconds

    return _check


def read_control_state() -> int:
    """Attach RTDB, read system.control_state, detach, return int."""
    try:
        shm, rtdb = attach_rtdb()
        state: int = int(rtdb.system.control_state)
        detach_rtdb(shm)
        return state
    except Exception:
        return -1


def check_control_state(target: int) -> Callable[[], bool]:
    """Return a callable that checks if control_state matches ``target``."""
    def _check() -> bool:
        return read_control_state() == target
    return _check


def read_active_setpoint_kw() -> float:
    """Read the current active setpoint from RTDB system section."""
    try:
        shm, rtdb = attach_rtdb()
        val: float = float(rtdb.system.active_setpoint_kw)
        detach_rtdb(shm)
        return val
    except Exception:
        return float("nan")


def send_control_command(
    endpoint: str, cmd: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Send a ZMQ REQ command to the control_manager REP socket."""
    ctx: zmq.Context = zmq.Context()
    req: zmq.Socket = ctx.socket(zmq.REQ)
    req.setsockopt(zmq.LINGER, 0)
    req.setsockopt(zmq.RCVTIMEO, 3000)
    req.setsockopt(zmq.SNDTIMEO, 3000)
    try:
        req.connect(endpoint)
        time.sleep(0.05)
        request_data: bytes = encode_command_request(cmd, params)
        req.send(request_data)
        response_data: bytes = req.recv()
        return decode_command_response(response_data)
    finally:
        req.close()
        ctx.term()


def _kill_subprocess_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill a subprocess and its entire process group (best-effort)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _build_m3_system(
    include_hmi: bool = True,
    include_scheduler: bool = False,
) -> dict[str, Any]:
    """Launch the full M1+M2 system with optional hmi_server and scheduler.

    Uses ipc:// sockets for all ZMQ endpoints (matching default module
    behaviour). Allocates a random TCP port for hmi_server HTTP.

    Returns a dict with keys:
        modules: dict[str, ModuleProcess]
        simulators: list[subprocess.Popen]
        tmpdir: Path
        hmi_http_port: int
        schedule_config_path: Path
        control_cmd_endpoint: str  (ipc://)
    """
    # Check prerequisites
    if not _vcan_available():
        pytest.skip("vcan0 not available -- run 'make sim' to set up virtual CAN")
    if not _binary_exists("data_manager_c"):
        pytest.skip("data_manager_c not built -- run 'make build' first")
    if not _binary_exists("safety_manager"):
        pytest.skip("safety_manager not built -- run 'make build' first")

    # Ensure /run/ems/ exists for ipc:// sockets
    run_ems: Path = Path("/run/ems")
    if not run_ems.exists():
        pytest.skip(
            "/run/ems/ directory not available -- "
            "run 'sudo mkdir -p /run/ems && sudo chmod 777 /run/ems'"
        )

    profile: dict[str, Any] = PROFILES["residential"]
    config_dir: Path = profile["config_dir"]

    # Allocate TCP ports for hmi_server HTTP
    ports: list[int] = _allocate_tcp_ports(1)
    hmi_http_port: int = ports[0]

    # Temp directory for test-specific configs
    tmpdir: str = tempfile.mkdtemp(prefix="ems_m3_e2e_")
    tmpdir_path: Path = Path(tmpdir)

    # Copy residential profile configs to tmpdir
    for yaml_file in config_dir.glob("*.yaml"):
        shutil.copy(yaml_file, tmpdir_path / yaml_file.name)

    # Write test HMI config with allocated port
    test_hmi_config_src: Path = config_dir / "hmi_config_test.yaml"
    test_hmi_config: Path = tmpdir_path / "hmi_config_test.yaml"
    if test_hmi_config_src.exists():
        with open(test_hmi_config_src) as f:
            hmi_cfg: dict = yaml.safe_load(f)
        hmi_cfg["server"]["http_port"] = hmi_http_port
        with open(test_hmi_config, "w") as f:
            yaml.dump(hmi_cfg, f, default_flow_style=False)

    modules: dict[str, ModuleProcess] = {}
    simulators: list[subprocess.Popen[bytes]] = []

    # 1. data_manager_c
    modules["data_manager_c"] = ModuleProcess(
        name="data_manager_c",
        cmd=[
            str(BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c"),
            str(profile["clusters"]),
            str(profile["racks"]),
            str(profile["modules"]),
            str(profile["cells"]),
            str(profile["temps"]),
        ],
        ready_check=check_rtdb_exists,
    )

    # 2. data_manager_python
    modules["data_manager_python"] = ModuleProcess(
        name="data_manager_python",
        cmd=["uv", "run", "python", "-m", "ems_data_manager"],
        ready_check=_delay_ready(2.0),
    )

    # 3. config_manager
    modules["config_manager"] = ModuleProcess(
        name="config_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_config_manager",
            "--config-dir", str(tmpdir_path),
            "--schema-dir", str(CONFIG_DIR / "schemas"),
        ],
        ready_check=_delay_ready(1.0),
    )

    # 4. safety_manager
    modules["safety_manager"] = ModuleProcess(
        name="safety_manager",
        cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
        ready_check=_delay_ready(1.0),
        env={"EMS_GPIO_BACKEND": "rtdb"},
    )

    # 5. comm_manager_c
    if _binary_exists("comm_manager_c"):
        modules["comm_manager_c"] = ModuleProcess(
            name="comm_manager_c",
            cmd=[
                str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                "--interface", "vcan0",
                "--base-id", "0x18FF0003",
                "--heartbeat-timeout-ms", "900",
            ],
            ready_check=_delay_ready(1.0),
        )

    # 6. comm_manager_python
    modules["comm_manager_python"] = ModuleProcess(
        name="comm_manager_python",
        cmd=[
            "uv", "run", "python", "-m", "ems_comm_manager",
            "--config", str(CONFIG_DIR),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 7. logger
    modules["logger"] = ModuleProcess(
        name="logger",
        cmd=[
            "uv", "run", "python", "-m", "ems_logger",
            "--config", str(CONFIG_DIR / "logger_config.yaml"),
        ],
        ready_check=_delay_ready(1.0),
    )

    # 8. CAN simulator
    can_proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.can_sim",
            "--interface", "vcan0",
            "--config", str(tmpdir_path / "bms_config.yaml"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setpgrp,
        cwd=str(PROJECT_ROOT),
    )
    simulators.append(can_proc)

    # 9. Modbus simulator
    modbus_proc: subprocess.Popen[bytes] = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "tools.simulators.modbus_sim",
            "--transport", "tcp",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setpgrp,
        cwd=str(PROJECT_ROOT),
    )
    simulators.append(modbus_proc)

    # 10. control_manager (uses ipc:// defaults)
    modules["control_manager"] = ModuleProcess(
        name="control_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_control_manager",
            "--config", str(tmpdir_path / "control_config.yaml"),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 11. alarm_manager (uses ipc:// defaults)
    modules["alarm_manager"] = ModuleProcess(
        name="alarm_manager",
        cmd=[
            "uv", "run", "python", "-m", "ems_alarm_manager",
            "--config", str(tmpdir_path / "alarms_config.yaml"),
        ],
        ready_check=_delay_ready(2.0),
    )

    # 12. hmi_server (optional)
    if include_hmi and test_hmi_config.exists():
        modules["hmi_server"] = ModuleProcess(
            name="hmi_server",
            cmd=[
                "uv", "run", "python", "-m", "ems_hmi_server",
                "--config", str(test_hmi_config),
            ],
            ready_check=lambda: _check_http_health(hmi_http_port),
        )

    # 13. scheduler (optional, as subprocess)
    schedule_config_path: Path = tmpdir_path / "schedule_config.yaml"
    if include_scheduler and schedule_config_path.exists():
        modules["scheduler"] = ModuleProcess(
            name="scheduler",
            cmd=[
                "uv", "run", "python", "-m", "ems_scheduler",
                "--config", str(schedule_config_path),
            ],
            ready_check=_delay_ready(3.0),
        )

    # Wait for system to stabilise
    time.sleep(5)

    return {
        "modules": modules,
        "simulators": simulators,
        "tmpdir": tmpdir_path,
        "hmi_http_port": hmi_http_port,
        "schedule_config_path": schedule_config_path,
        "control_cmd_endpoint": "ipc:///run/ems/control_cmd.sock",
        "config_sub_endpoint": "ipc:///run/ems/config_pub.sock",
        "scheduler_pub_endpoint": "ipc:///run/ems/scheduler_pub.sock",
    }


def _teardown_m3_system(system: dict[str, Any]) -> None:
    """Tear down the M3 system in reverse order."""
    # Kill simulators first
    for sim in system.get("simulators", []):
        _kill_subprocess_group(sim)
    # Kill modules in reverse order
    modules: dict[str, ModuleProcess] = system.get("modules", {})
    for name in reversed(list(modules.keys())):
        try:
            modules[name].cleanup()
        except Exception:
            pass
    # Clean up temp directory
    tmpdir: Path | None = system.get("tmpdir")
    if tmpdir is not None:
        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ===========================================================================
# TestCommandFlow
# ===========================================================================


class TestCommandFlow:
    """E2E: HMI REST -> ZMQ -> control_manager -> RTDB -> PCS simulator.

    Validates HMI-03: REST API commands reach PCS via the full module chain.
    """

    @pytest.fixture(scope="class")
    def m3_system(self) -> Any:
        """Launch full M1+M2+hmi_server system."""
        system: dict[str, Any] = _build_m3_system(
            include_hmi=True, include_scheduler=False,
        )
        try:
            yield system
        finally:
            _teardown_m3_system(system)

    @pytest.mark.asyncio
    async def test_e2e_command_flow(self, m3_system: dict[str, Any]) -> None:
        """Step-by-step command flow: login -> mode change -> setpoint -> alarm query."""
        hmi_port: int = m3_system["hmi_http_port"]
        base_url: str = f"http://127.0.0.1:{hmi_port}"

        # Verify all modules are alive
        modules: dict[str, ModuleProcess] = m3_system["modules"]
        dead: list[str] = [name for name, m in modules.items() if not m.is_alive]
        assert not dead, f"Dead modules before test: {dead}"

        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            # Step 1: Health check
            resp: httpx.Response = await client.get("/api/health/")
            assert resp.status_code == 200, f"Health check failed: {resp.text}"

            # Step 2: Login with operator PIN "1234"
            resp = await client.post("/api/auth/login", json={"pin": "1234"})
            assert resp.status_code == 200, f"Login failed: {resp.text}"
            token: str = resp.json()["token"]
            headers: dict[str, str] = {"Authorization": f"Bearer {token}"}

            # Step 3: POST /api/control/mode {target_state: "standby"}
            resp = await client.post(
                "/api/control/mode",
                json={"target_state": "standby"},
                headers=headers,
            )
            assert resp.status_code == 200, f"Mode change failed: {resp.text}"
            body: dict = resp.json()
            assert body.get("status") == "ok", f"Mode change rejected: {body}"

            # Step 4: Verify control_state reaches STANDBY via RTDB (up to 15s)
            state: dict[str, bool] = wait_for_criteria(
                {"standby": check_control_state(STATE_STANDBY)},
                timeout=15.0,
            )
            assert state["standby"], "control_state did not reach STANDBY within 15s"

            # Step 5: POST /api/control/setpoint {power_kw: 15.0}
            resp = await client.post(
                "/api/control/setpoint",
                json={"power_kw": 15.0},
                headers=headers,
            )
            assert resp.status_code == 200, f"Setpoint failed: {resp.text}"
            body = resp.json()
            assert body.get("status") == "ok", f"Setpoint rejected: {body}"

            # Step 6: Verify RTDB active_setpoint_kw (wait up to 5s)
            deadline: float = time.monotonic() + 5.0
            setpoint_verified: bool = False
            while time.monotonic() < deadline:
                setpoint: float = read_active_setpoint_kw()
                if abs(setpoint - 15.0) < 0.1:
                    setpoint_verified = True
                    break
                time.sleep(0.3)
            assert setpoint_verified, (
                f"active_setpoint_kw={read_active_setpoint_kw()}, expected 15.0"
            )

            # Step 7: GET /api/alarm/active (verify endpoint works)
            resp = await client.get("/api/alarm/active", headers=headers)
            assert resp.status_code == 200, f"Alarm query failed: {resp.text}"


# ===========================================================================
# TestTelemetryFlow
# ===========================================================================


class TestTelemetryFlow:
    """E2E: simulator -> RTDB -> ZMQ PUB -> WebSocket -> JSON.

    Validates HMI-02: 1Hz telemetry streaming through WebSocket.
    """

    @pytest.fixture(scope="class")
    def m3_system(self) -> Any:
        """Launch full system with hmi_server for WebSocket testing."""
        system: dict[str, Any] = _build_m3_system(
            include_hmi=True, include_scheduler=False,
        )
        try:
            yield system
        finally:
            _teardown_m3_system(system)

    @pytest.mark.asyncio
    async def test_websocket_telemetry_stream(
        self, m3_system: dict[str, Any]
    ) -> None:
        """Connect to /ws/telemetry, receive 3+ messages in 5 seconds."""
        hmi_port: int = m3_system["hmi_http_port"]
        ws_uri: str = f"ws://127.0.0.1:{hmi_port}/ws/telemetry"

        messages: list[dict] = []
        async with websockets.connect(ws_uri) as ws:
            for _ in range(5):  # Try to receive 5 messages
                try:
                    raw: str | bytes = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    msg: dict = json.loads(raw)
                    messages.append(msg)
                except asyncio.TimeoutError:
                    break

        # Must receive at least 3 messages (1Hz rate)
        assert len(messages) >= 3, (
            f"Only received {len(messages)} messages in 5s (expected >= 3)"
        )

        # Validate message shape -- look for expected fields
        has_parseable: bool = False
        for msg in messages:
            if isinstance(msg, dict):
                # Messages have {topic, data, ts} structure per ws.py
                if "topic" in msg and "data" in msg:
                    has_parseable = True
                    data: dict = msg["data"]
                    # If system topic, check for expected fields
                    if msg.get("topic") == "system":
                        if "total_soc" in data:
                            soc: float = data["total_soc"]
                            assert 0 <= soc <= 100, f"SOC out of range: {soc}"

        assert has_parseable, (
            "No messages had expected {topic, data} structure"
        )


# ===========================================================================
# TestScheduleDispatch
# ===========================================================================


class TestScheduleDispatch:
    """Schedule-to-dispatch: scheduler -> ZMQ -> control_manager -> RTDB -> PCS.

    Uses in-process SchedulerLoop with now_func clock injection.
    Validates SCHED-01, SCHED-03, SCHED-04, SCHED-05.
    """

    @pytest.fixture(scope="class")
    def m3_system(self) -> Any:
        """Launch full M1+M2 system (no scheduler subprocess -- we run it in-process)."""
        system: dict[str, Any] = _build_m3_system(
            include_hmi=False, include_scheduler=False,
        )
        try:
            yield system
        finally:
            _teardown_m3_system(system)

    def _make_scheduler_loop(
        self,
        m3_system: dict[str, Any],
        config_path: Path,
        mock_time: datetime,
    ) -> Any:
        """Create a SchedulerLoop with mock time against the running system.

        Uses ipc:// sockets (matching control_manager defaults).
        """
        from ems_scheduler.config import load_schedule_config
        from ems_scheduler.loop import SchedulerLoop

        schedule_config: dict = load_schedule_config(config_path)

        # Use unique PUB endpoints per test to avoid address-in-use
        ports: list[int] = _allocate_tcp_ports(1)
        pub_ep: str = f"tcp://127.0.0.1:{ports[0]}"

        loop: SchedulerLoop = SchedulerLoop(
            config=schedule_config,
            config_path=config_path,
            req_endpoint=m3_system["control_cmd_endpoint"],
            config_sub_endpoint=m3_system["config_sub_endpoint"],
            pub_endpoint=pub_ep,
            now_func=lambda: mock_time,
        )
        return loop

    def test_discharge_window_active(self, m3_system: dict[str, Any]) -> None:
        """Mock time 12:00 (inside 06:00-18:00 discharge) -> PCS receives 10 kW."""
        mock_time: datetime = datetime(2026, 3, 15, 12, 0, 0)
        config_path: Path = m3_system["schedule_config_path"]
        loop = self._make_scheduler_loop(m3_system, config_path, mock_time)

        try:
            # First tick: evaluates and sends command
            loop._evaluate_tick(mock_time)

            # Wait for control_manager to process command and update RTDB
            time.sleep(3.0)

            # Verify RTDB setpoint
            setpoint: float = read_active_setpoint_kw()
            assert abs(setpoint - 10.0) < 0.5, (
                f"Expected setpoint ~10.0 kW (discharge window), got {setpoint}"
            )
        finally:
            loop.cleanup()

    def test_charge_window_active(self, m3_system: dict[str, Any]) -> None:
        """Mock time 23:00 (inside 22:00-06:00 charge) -> charge at -15 kW."""
        mock_time: datetime = datetime(2026, 3, 15, 23, 0, 0)
        config_path: Path = m3_system["schedule_config_path"]
        loop = self._make_scheduler_loop(m3_system, config_path, mock_time)

        try:
            loop._evaluate_tick(mock_time)
            time.sleep(3.0)

            setpoint: float = read_active_setpoint_kw()
            # Charge = negative setpoint (sign convention from evaluator.py)
            assert setpoint < 0, (
                f"Expected negative setpoint for charge, got {setpoint}"
            )
            assert abs(setpoint + 15.0) < 0.5, (
                f"Expected setpoint ~-15.0 kW (charge window), got {setpoint}"
            )
        finally:
            loop.cleanup()

    def test_between_windows_idle(self, m3_system: dict[str, Any]) -> None:
        """Mock time 19:00 (outside all windows) -> setpoint should be 0."""
        mock_time: datetime = datetime(2026, 3, 15, 19, 0, 0)
        config_path: Path = m3_system["schedule_config_path"]
        loop = self._make_scheduler_loop(m3_system, config_path, mock_time)

        try:
            loop._evaluate_tick(mock_time)
            time.sleep(3.0)

            setpoint: float = read_active_setpoint_kw()
            assert abs(setpoint) < 0.5, (
                f"Expected setpoint ~0 kW (idle, outside windows), got {setpoint}"
            )
        finally:
            loop.cleanup()

    def test_manual_mode_no_dispatch(self, m3_system: dict[str, Any]) -> None:
        """Manual mode -> scheduler sends no setpoint command."""
        # Create a manual-mode config from the existing schedule_config
        config_path: Path = m3_system["schedule_config_path"]
        manual_config_path: Path = m3_system["tmpdir"] / "schedule_config_manual.yaml"

        with open(config_path) as f:
            config_dict: dict = yaml.safe_load(f)
        config_dict["mode"] = "manual"
        with open(manual_config_path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False)

        mock_time: datetime = datetime(2026, 3, 15, 12, 0, 0)

        # Record RTDB state before tick
        shm, rtdb = attach_rtdb()
        cmd_seq_before: int = int(rtdb.system.pcs_command_seq)
        detach_rtdb(shm)

        loop = self._make_scheduler_loop(m3_system, manual_config_path, mock_time)

        try:
            loop._evaluate_tick(mock_time)
            time.sleep(2.0)

            # In manual mode, scheduler should not send setpoint commands
            # pcs_command_seq should not have changed
            shm, rtdb = attach_rtdb()
            cmd_seq_after: int = int(rtdb.system.pcs_command_seq)
            detach_rtdb(shm)

            # Manual mode sends day/night source_priority only (not setpoint),
            # so pcs_command_seq should be unchanged
            assert cmd_seq_after == cmd_seq_before, (
                f"pcs_command_seq changed in manual mode: "
                f"before={cmd_seq_before}, after={cmd_seq_after}"
            )
        finally:
            loop.cleanup()


# ===========================================================================
# TestWebSocketReconnection
# ===========================================================================


class TestWebSocketReconnection:
    """WebSocket server-side recovery: kill hmi_server, verify reconnectable.

    Validates HMI-13: WebSocket resilience after server crash.
    """

    @pytest.fixture(scope="class")
    def m3_system(self) -> Any:
        """Launch full system with hmi_server for WebSocket reconnection testing."""
        system: dict[str, Any] = _build_m3_system(
            include_hmi=True, include_scheduler=False,
        )
        try:
            yield system
        finally:
            _teardown_m3_system(system)

    @pytest.mark.asyncio
    async def test_websocket_reconnect_after_server_kill(
        self, m3_system: dict[str, Any]
    ) -> None:
        """Kill hmi_server, wait 5s, restart, reconnect WebSocket within 10s."""
        hmi_port: int = m3_system["hmi_http_port"]
        ws_uri: str = f"ws://127.0.0.1:{hmi_port}/ws/telemetry"
        hmi: ModuleProcess = m3_system["modules"]["hmi_server"]

        # Step 1: Connect WebSocket, receive 3+ messages (confirms data flowing)
        messages_before: list[dict] = []
        async with websockets.connect(ws_uri) as ws:
            for _ in range(3):
                raw: str | bytes = await asyncio.wait_for(ws.recv(), timeout=5.0)
                messages_before.append(json.loads(raw))
        assert len(messages_before) >= 3, (
            f"Pre-kill: only received {len(messages_before)} messages"
        )

        # Step 2: Kill hmi_server with SIGKILL
        hmi.kill(sig=signal.SIGKILL)

        # Wait for process to die
        kill_deadline: float = time.monotonic() + 3.0
        while hmi.is_alive and time.monotonic() < kill_deadline:
            time.sleep(0.1)
        assert not hmi.is_alive, "hmi_server did not die after SIGKILL"

        # Step 3: Wait 5 seconds (simulates systemd RestartSec=5)
        await asyncio.sleep(5.0)

        # Step 4: Restart hmi_server
        hmi.restart()

        # Verify health endpoint comes back
        health_ok: dict[str, bool] = wait_for_criteria(
            {"health": lambda: _check_http_health(hmi_port)},
            timeout=15.0,
        )
        assert health_ok["health"], (
            "hmi_server health endpoint not responding after restart"
        )

        # Step 5: Reconnect WebSocket within 10s
        reconnected: bool = False
        reconnect_deadline: float = time.monotonic() + 10.0
        messages_after: list[dict] = []

        while time.monotonic() < reconnect_deadline:
            try:
                async with websockets.connect(ws_uri) as ws:
                    for _ in range(3):
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        messages_after.append(json.loads(raw))
                    reconnected = True
                    break
            except (
                ConnectionRefusedError,
                OSError,
                websockets.exceptions.WebSocketException,
            ):
                await asyncio.sleep(1.0)

        # Step 6: Verify data flowing on new connection
        assert reconnected, "Failed to reconnect WebSocket within 10s after restart"
        assert len(messages_after) >= 3, (
            f"Post-restart: only {len(messages_after)} messages received"
        )
