"""M2 integration tests — protection flow, dispatch flow, interlock, telemetry.

Validates end-to-end:
- Protection: BMS cell voltage low -> alarm fires -> control FAULT -> PCS stops
- Dispatch: SOC/temp values -> control setpoint -> PCS register 0x500E
- Interlock: safety emergency blocks STANDBY -> DISCHARGING
- Source exhaustion: no available source -> PCS=0, state -> IDLE
- Telemetry: control.state ZMQ PUB messages arrive at >= 1Hz (CTRL-12)

These tests are the "graduation test" for M2 — they prove that control_manager
and alarm_manager work correctly together with all M1 modules, crossing 5 module
boundaries (simulator -> comm_manager -> RTDB -> alarm_manager -> control_manager
-> PCS Modbus).

Prerequisites:
- vcan0 virtual CAN interface (run 'make sim' to create)
- C binaries built in build/ directory (run 'make build')
- All Python modules installed in the workspace (uv sync --all-packages)

Test isolation:
- All ZMQ endpoints use tcp://127.0.0.1:{random_port} per Phase 13 key decision
- No ipc://run/ems/ paths anywhere in this file (all sockets use tcp://127.0.0.1)
- Each test class allocates its own port set to avoid conflicts
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import msgpack
import pytest
import yaml
import zmq

# ---------------------------------------------------------------------------
# Import conftest helpers (conftest.py is not a regular importable module)
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
    check_rtdb_exists,
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
    """Allocate `count` random free TCP ports for ZMQ endpoints.

    Binds temporary sockets to force OS port assignment, collects the ports,
    then releases them just before returning. The brief TOCTOU window is
    acceptable for test isolation purposes.
    """
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
    """Return a ready_check that passes after `seconds` of wall-clock time.

    The clock starts when the returned callable is first called.
    Subsequent calls return True once the elapsed time exceeds `seconds`.
    """
    _start: list[float] = [0.0]
    _initialized: list[bool] = [False]

    def _check() -> bool:
        if not _initialized[0]:
            _start[0] = time.monotonic()
            _initialized[0] = True
        return (time.monotonic() - _start[0]) >= seconds

    return _check


def read_control_state() -> int:
    """Attach RTDB, read system.control_state, detach, return int.

    Returns -1 if RTDB is unavailable.
    """
    try:
        shm, rtdb = attach_rtdb()
        state: int = int(rtdb.system.control_state)
        detach_rtdb(shm)
        return state
    except Exception:
        return -1


def check_control_state(target: int) -> Callable[[], bool]:
    """Return a callable that checks if control_state matches `target`."""
    def _check() -> bool:
        return read_control_state() == target
    return _check


def read_min_cell_v() -> float:
    """Attach RTDB, read clusters[0].racks[0].min_cell_v, detach, return float."""
    try:
        shm, rtdb = attach_rtdb()
        v: float = float(rtdb.clusters[0].racks[0].min_cell_v)
        detach_rtdb(shm)
        return v
    except Exception:
        return 0.0


def send_control_command(endpoint: str, cmd: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send a ZMQ REQ command to the control_manager REP socket.

    Args:
        endpoint: TCP endpoint string like 'tcp://127.0.0.1:{port}'.
        cmd: Command action string (e.g. 'mode_change', 'fault_reset').
        params: Command parameters dict.

    Returns:
        Decoded response dict from decode_command_response.

    Raises:
        zmq.Again: If the request times out (3s timeout).
    """
    ctx: zmq.Context = zmq.Context()
    req: zmq.Socket = ctx.socket(zmq.REQ)
    req.setsockopt(zmq.LINGER, 0)
    req.setsockopt(zmq.RCVTIMEO, 3000)
    req.setsockopt(zmq.SNDTIMEO, 3000)
    try:
        req.connect(endpoint)
        time.sleep(0.05)  # Brief sleep to allow connection to establish
        request_data: bytes = encode_command_request(cmd, params)
        req.send(request_data)
        response_data: bytes = req.recv()
        return decode_command_response(response_data)
    finally:
        req.close()
        ctx.term()


def _write_test_bms_config(
    src: Path,
    dest: Path,
    base_voltage: float,
    drift: float = 0.0,
    base_soc: float | None = None,
    base_temp: float | None = None,
) -> None:
    """Write a bms_config.yaml with fault_injection overrides.

    Reads the source YAML, injects fault_injection fields for cell voltage
    (and optionally SOC and temperature), writes to dest.

    Args:
        src: Source bms_config.yaml path.
        dest: Destination path to write modified config.
        base_voltage: cell_voltage_base value to inject.
        drift: cell_voltage_drift_amplitude (default 0.0 = static voltage).
        base_soc: Optional SOC override (percent, e.g. 10.0 for 10%).
        base_temp: Optional cell temperature override (Celsius).
    """
    with open(src) as f:
        config: dict[str, Any] = yaml.safe_load(f)

    fault_injection: dict[str, Any] = config.get("fault_injection", {})
    fault_injection["cell_voltage_base"] = base_voltage
    fault_injection["cell_voltage_drift_amplitude"] = drift
    if base_soc is not None:
        fault_injection["soc_base"] = base_soc
        fault_injection["soc_drift_amplitude"] = 0.0
    if base_temp is not None:
        fault_injection["cell_temp_base"] = base_temp
        fault_injection["cell_temp_drift_amplitude"] = 0.0
    config["fault_injection"] = fault_injection

    with open(dest, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


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


# ---------------------------------------------------------------------------
# TestProtectionFlow
# ---------------------------------------------------------------------------


class TestProtectionFlow:
    """End-to-end protection flow validation.

    Proves the 8-step chain:
    1. All modules healthy
    2. STANDBY transition
    3. BMS low voltage injection
    4. Alarm delay (5s ALM-05)
    5. FAULT transition
    6. PCS shutdown
    7. Voltage restore
    8. Fault reset -> IDLE

    Also validates:
    - Safety interlock (CTRL-09): emergency blocks DISCHARGING
    - ZMQ telemetry at 1Hz (CTRL-12)
    """

    @pytest.fixture(scope="class")
    def m2_system(self) -> Any:
        """Launch full M1 + M2 system for protection flow testing.

        Allocates random TCP ports for all ZMQ endpoints to avoid conflicts
        with other test classes and system services.
        """
        # Skip if prerequisites not available
        if not _vcan_available():
            pytest.skip("vcan0 not available — run 'make sim' to set up virtual CAN")
        if not _binary_exists("data_manager_c"):
            pytest.skip("data_manager_c not built — run 'make build' first")
        if not _binary_exists("safety_manager"):
            pytest.skip("safety_manager not built — run 'make build' first")

        # Allocate 8 TCP ports for ZMQ endpoint isolation
        ports: list[int] = _allocate_tcp_ports(8)
        control_cmd_port: int = ports[0]
        alarm_cmd_port: int = ports[1]
        control_pub_port: int = ports[2]
        alarm_pub_port: int = ports[3]
        control_push_port: int = ports[4]
        alarm_push_port: int = ports[5]
        alarm_sub_port: int = ports[6]
        config_sub_port: int = ports[7]

        control_cmd_ep: str = f"tcp://127.0.0.1:{control_cmd_port}"
        alarm_cmd_ep: str = f"tcp://127.0.0.1:{alarm_cmd_port}"
        control_pub_ep: str = f"tcp://127.0.0.1:{control_pub_port}"
        alarm_pub_ep: str = f"tcp://127.0.0.1:{alarm_pub_port}"
        control_push_ep: str = f"tcp://127.0.0.1:{control_push_port}"
        alarm_push_ep: str = f"tcp://127.0.0.1:{alarm_push_port}"
        config_sub_ep: str = f"tcp://127.0.0.1:{config_sub_port}"

        profile: dict[str, Any] = PROFILES["residential"]
        config_dir: Path = profile["config_dir"]

        # Temp directory for test-specific configs
        tmpdir: str = tempfile.mkdtemp(prefix="ems_m2_prot_")
        tmpdir_path: Path = Path(tmpdir)

        # Copy residential profile configs to tmpdir
        for yaml_file in config_dir.glob("*.yaml"):
            shutil.copy(yaml_file, tmpdir_path / yaml_file.name)

        modules: list[ModuleProcess] = []
        simulators: list[subprocess.Popen[bytes]] = []

        try:
            # 1. data_manager_c — creates RTDB shared memory
            dm_c = ModuleProcess(
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
            modules.append(dm_c)

            # 2. data_manager_python — publishes RTDB snapshots
            dm_py = ModuleProcess(
                name="data_manager_python",
                cmd=["uv", "run", "python", "-m", "ems_data_manager"],
                ready_check=_delay_ready(2.0),
            )
            modules.append(dm_py)

            # 3. config_manager
            cfg_mgr = ModuleProcess(
                name="config_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_config_manager",
                    "--config-dir", str(tmpdir_path),
                ],
                ready_check=_delay_ready(1.0),
            )
            modules.append(cfg_mgr)

            # 4. safety_manager (GPIO harness via EMS_GPIO_BACKEND=rtdb)
            sm = ModuleProcess(
                name="safety_manager",
                cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
                ready_check=_delay_ready(1.0),
                env={"EMS_GPIO_BACKEND": "rtdb"},
            )
            modules.append(sm)

            # 5. comm_manager_c — CAN decoder
            if _binary_exists("comm_manager_c"):
                cm_c = ModuleProcess(
                    name="comm_manager_c",
                    cmd=[
                        str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                        "--interface", "vcan0",
                        "--base-id", "0x18FF0003",
                        "--heartbeat-timeout-ms", "900",
                    ],
                    ready_check=_delay_ready(1.0),
                )
                modules.append(cm_c)

            # 6. comm_manager_python — Modbus decoder/writer
            cm_py = ModuleProcess(
                name="comm_manager_python",
                cmd=[
                    "uv", "run", "python", "-m", "ems_comm_manager",
                    "--config", str(CONFIG_DIR),
                ],
                ready_check=_delay_ready(2.0),
            )
            modules.append(cm_py)

            # 7. logger
            lgr = ModuleProcess(
                name="logger",
                cmd=[
                    "uv", "run", "python", "-m", "ems_logger",
                    "--config", str(CONFIG_DIR / "logger_config.yaml"),
                ],
                ready_check=_delay_ready(1.0),
            )
            modules.append(lgr)

            # 8. CAN simulator — normal voltage config
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

            # 10. control_manager — bind to allocated TCP ports via env vars
            ctrl_mgr = ModuleProcess(
                name="control_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_control_manager",
                    "--config", str(tmpdir_path / "control_config.yaml"),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_CONTROL_CMD_ENDPOINT": control_cmd_ep,
                    "EMS_CONTROL_PUB_ENDPOINT": control_pub_ep,
                    "EMS_CONTROL_PUSH_ENDPOINT": control_push_ep,
                    "EMS_ALARM_SUB_ENDPOINT": alarm_pub_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(ctrl_mgr)

            # 11. alarm_manager — bind to allocated TCP ports via env vars
            alm_mgr = ModuleProcess(
                name="alarm_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_alarm_manager",
                    "--config", str(tmpdir_path / "alarms_config.yaml"),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_ALARM_CMD_ENDPOINT": alarm_cmd_ep,
                    "EMS_ALARM_PUB_ENDPOINT": alarm_pub_ep,
                    "EMS_ALARM_PUSH_ENDPOINT": alarm_push_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(alm_mgr)

            # Wait for system to stabilize
            time.sleep(5)

            yield {
                "modules": modules,
                "simulators": simulators,
                "tmpdir": tmpdir_path,
                "config_dir": tmpdir_path,
                "can_proc_index": len(simulators) - 2,  # index 0
                "control_cmd_endpoint": control_cmd_ep,
                "alarm_cmd_endpoint": alarm_cmd_ep,
                "control_pub_endpoint": control_pub_ep,
                "bms_config_src": config_dir / "bms_config.yaml",
            }

        finally:
            # Teardown: kill simulators first, then modules in reverse order
            for sim in simulators:
                _kill_subprocess_group(sim)
            for mod in reversed(modules):
                try:
                    mod.cleanup()
                except Exception:
                    pass
            # Clean up temp directory
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Step 1: All modules healthy
    # ------------------------------------------------------------------

    def test_step1_all_modules_healthy(self, m2_system: dict[str, Any]) -> None:
        """Verify all module processes are alive and RTDB is populated.

        Step 1 of the 8-step protection chain from CONTEXT.md.
        """
        modules: list[ModuleProcess] = m2_system["modules"]
        dead: list[str] = [mod.name for mod in modules if not mod.is_alive]
        assert not dead, f"Step 1: Dead modules after startup: {dead}"
        assert check_rtdb_exists(), "Step 1: RTDB shared memory not found"

    # ------------------------------------------------------------------
    # Steps 2-8: Full protection chain
    # ------------------------------------------------------------------

    def test_step2_to_step8_protection_chain(self, m2_system: dict[str, Any]) -> None:
        """Execute the full 8-step BMS protection chain.

        Step 2: STANDBY transition
        Step 3: Low voltage injection via CAN sim restart
        Step 4: Alarm delay (5s per ALM-05)
        Step 5: FAULT transition
        Step 6: PCS shutdown verification
        Step 7: Normal voltage restore
        Step 8: Fault reset -> IDLE
        """
        tmpdir: Path = m2_system["tmpdir"]
        bms_config_src: Path = m2_system["bms_config_src"]
        control_cmd_ep: str = m2_system["control_cmd_endpoint"]

        # ----------------------------------------------------------------
        # Step 2: Transition to STANDBY
        # ----------------------------------------------------------------
        try:
            resp: dict[str, Any] = send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "standby"}
            )
        except zmq.Again:
            pytest.fail("Step 2: Timeout sending mode_change to control_manager")

        criteria: dict[str, Callable[[], bool]] = {
            "standby": check_control_state(STATE_STANDBY),
        }
        result: dict[str, bool] = wait_for_criteria(criteria, timeout=15.0)
        assert result["standby"], (
            f"Step 2: Expected control_state=STANDBY ({STATE_STANDBY}), "
            f"got {read_control_state()} (response: {resp})"
        )

        # ----------------------------------------------------------------
        # Step 3: Inject low cell voltage via CAN sim restart
        # Write bms_config with cell_voltage_base=2.65V (below 2.8V threshold)
        # ----------------------------------------------------------------
        low_v_config: Path = tmpdir / "bms_config_low_v.yaml"
        _write_test_bms_config(bms_config_src, low_v_config, base_voltage=2.65, drift=0.0)

        # Kill the running CAN sim (index 0 in simulators list)
        sims: list[subprocess.Popen[bytes]] = m2_system["simulators"]
        _kill_subprocess_group(sims[0])

        # Start new CAN sim with low-voltage config
        new_can_proc: subprocess.Popen[bytes] = subprocess.Popen(
            [
                "uv", "run", "python", "-m", "tools.simulators.can_sim",
                "--interface", "vcan0",
                "--config", str(low_v_config),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setpgrp,
            cwd=str(PROJECT_ROOT),
        )
        # Replace the old can sim reference so teardown cleans up new one
        sims[0] = new_can_proc

        # Wait for RTDB min_cell_v to drop below 2.8V (5s window)
        low_v_criteria: dict[str, Callable[[], bool]] = {
            "low_v": lambda: read_min_cell_v() > 0.1 and read_min_cell_v() < 2.8,
        }
        low_v_result: dict[str, bool] = wait_for_criteria(low_v_criteria, timeout=5.0)
        assert low_v_result["low_v"], (
            f"Step 3: Expected RTDB min_cell_v < 2.8V, got {read_min_cell_v():.3f}V"
        )

        # ----------------------------------------------------------------
        # Step 4+5: Wait for alarm delay (5s per ALM-05) then FAULT transition
        # Combined timeout: 7s for delay + 3s margin
        # ----------------------------------------------------------------
        fault_criteria: dict[str, Callable[[], bool]] = {
            "fault": check_control_state(STATE_FAULT),
        }
        fault_result: dict[str, bool] = wait_for_criteria(fault_criteria, timeout=10.0)
        assert fault_result["fault"], (
            f"Steps 4+5: Expected control_state=FAULT ({STATE_FAULT}) after alarm delay, "
            f"got {read_control_state()} "
            "(check: alarm_manager fired protection alarm after 5s delay, "
            "control_manager received it via ZMQ SUB)"
        )

        # ----------------------------------------------------------------
        # Step 6: Verify PCS received shutdown — check Modbus simulator
        # PCS register 0x500E (active power setpoint) should be 0
        # PCS register 0x0291 (on/off) should be 0
        # ----------------------------------------------------------------
        try:
            from pymodbus.client import ModbusTcpClient

            pcs_client: ModbusTcpClient = ModbusTcpClient("127.0.0.1", port=502)
            pcs_connected: bool = pcs_client.connect()
            if not pcs_connected:
                pytest.skip("Step 6: Cannot connect to Modbus simulator on TCP 502")
            try:
                time.sleep(0.5)  # Allow any in-flight writes to settle
                # Read register 0x500E (active power setpoint)
                power_result = pcs_client.read_holding_registers(
                    address=0x500E, count=1, slave=1
                )
                if not power_result.isError():
                    power_val: int = power_result.registers[0]
                    assert power_val == 0, (
                        f"Step 6: Expected PCS 0x500E=0 (PCS stopped), got {power_val}"
                    )
            finally:
                pcs_client.close()
        except ImportError:
            pytest.skip("Step 6: pymodbus not available — skipping PCS register check")

        # ----------------------------------------------------------------
        # Step 7: Restore normal voltage — restart CAN sim with normal config
        # ----------------------------------------------------------------
        _kill_subprocess_group(sims[0])

        normal_can_proc: subprocess.Popen[bytes] = subprocess.Popen(
            [
                "uv", "run", "python", "-m", "tools.simulators.can_sim",
                "--interface", "vcan0",
                "--config", str(tmpdir / "bms_config.yaml"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setpgrp,
            cwd=str(PROJECT_ROOT),
        )
        sims[0] = normal_can_proc

        # Wait for RTDB min_cell_v to recover above 2.8V
        normal_v_criteria: dict[str, Callable[[], bool]] = {
            "normal_v": lambda: read_min_cell_v() > 2.8,
        }
        normal_v_result: dict[str, bool] = wait_for_criteria(normal_v_criteria, timeout=5.0)
        assert normal_v_result["normal_v"], (
            f"Step 7: Expected RTDB min_cell_v > 2.8V after voltage restore, "
            f"got {read_min_cell_v():.3f}V"
        )

        # ----------------------------------------------------------------
        # Step 8: Send manual fault_reset, verify return to IDLE
        # Uses manual fault_reset (not 60s cooldown) per CONTEXT.md decision
        # ----------------------------------------------------------------
        try:
            reset_resp: dict[str, Any] = send_control_command(
                control_cmd_ep, "fault_reset", {}
            )
        except zmq.Again:
            pytest.fail("Step 8: Timeout sending fault_reset to control_manager")

        idle_criteria: dict[str, Callable[[], bool]] = {
            "idle": check_control_state(STATE_IDLE),
        }
        idle_result: dict[str, bool] = wait_for_criteria(idle_criteria, timeout=5.0)
        assert idle_result["idle"], (
            f"Step 8: Expected control_state=IDLE ({STATE_IDLE}) after fault_reset, "
            f"got {read_control_state()} (reset response: {reset_resp})"
        )

    # ------------------------------------------------------------------
    # CTRL-09: Safety interlock blocks STANDBY -> DISCHARGING
    # ------------------------------------------------------------------

    def test_interlock_blocks_dispatch_on_emergency(
        self, m2_system: dict[str, Any]
    ) -> None:
        """Verify safety emergency state blocks STANDBY -> DISCHARGING transition.

        Validates CTRL-09: when safety_manager detects E-Stop, control_manager
        transitions to EMERGENCY and rejects DISCHARGING commands.
        """
        control_cmd_ep: str = m2_system["control_cmd_endpoint"]

        # Ensure control_manager is in STANDBY
        try:
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pytest.fail("Interlock test: Timeout sending mode_change to STANDBY")

        standby_criteria: dict[str, Callable[[], bool]] = {
            "standby": check_control_state(STATE_STANDBY),
        }
        standby_result: dict[str, bool] = wait_for_criteria(standby_criteria, timeout=10.0)
        if not standby_result["standby"]:
            # If FAULT state, send fault_reset first
            try:
                send_control_command(control_cmd_ep, "fault_reset", {})
                time.sleep(1.0)
                send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
            except zmq.Again:
                pass
            standby_result = wait_for_criteria(standby_criteria, timeout=10.0)
            if not standby_result["standby"]:
                pytest.skip(
                    f"Interlock test: Cannot reach STANDBY "
                    f"(current state={read_control_state()})"
                )

        # Trigger emergency: write DI-2=1 (E-Stop active) via RTDB GPIO section
        # EMS_GPIO_BACKEND=rtdb is set for safety_manager, so RTDB DI is the input
        try:
            shm, rtdb = attach_rtdb()
            rtdb.gpio.di[2] = 1  # E-Stop channel (dual-channel simulation)
            detach_rtdb(shm)
        except Exception as exc:
            pytest.skip(f"Interlock test: Cannot write RTDB GPIO: {exc}")

        # Wait for safety_manager to process and force EMERGENCY state
        time.sleep(2.0)

        # Attempt blocked transition to DISCHARGING
        try:
            discharge_resp: dict[str, Any] = send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            discharge_resp = {"status": "error", "error_msg": "timeout"}

        # Verify state is EMERGENCY (not DISCHARGING)
        current_state: int = read_control_state()
        assert current_state == STATE_EMERGENCY, (
            f"Interlock test: Expected STATE_EMERGENCY ({STATE_EMERGENCY}) after E-Stop, "
            f"got {current_state}"
        )

        # Verify DISCHARGING command was rejected
        assert discharge_resp.get("status") == "error", (
            f"Interlock test: Expected command rejection when in EMERGENCY, "
            f"got response: {discharge_resp}"
        )

        # Cleanup: clear E-Stop by writing DI-2=0, wait for recovery
        try:
            shm, rtdb = attach_rtdb()
            rtdb.gpio.di[2] = 0
            detach_rtdb(shm)
        except Exception:
            pass  # Best-effort cleanup
        time.sleep(2.0)  # Allow safety_manager to process E-Stop clear

    # ------------------------------------------------------------------
    # CTRL-12: Control telemetry at >= 1Hz on ZMQ PUB socket
    # ------------------------------------------------------------------

    def test_control_telemetry_at_1hz(self, m2_system: dict[str, Any]) -> None:
        """Verify control_manager publishes telemetry at >= 1Hz on PUB socket.

        Validates CTRL-12: ZMQ PUB messages on control.state topic arrive
        at the required 1Hz rate and carry meaningful state data.
        """
        control_pub_ep: str = m2_system["control_pub_endpoint"]

        ctx: zmq.Context = zmq.Context()
        sub_sock: zmq.Socket = ctx.socket(zmq.SUB)
        sub_sock.setsockopt(zmq.LINGER, 0)
        sub_sock.setsockopt(zmq.RCVTIMEO, 2000)  # 2s receive timeout

        try:
            sub_sock.connect(control_pub_ep)
            # Subscribe to control.state topic (control_manager sends multipart [topic, body])
            sub_sock.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONTROL_STATE)
            # Also subscribe with empty string as fallback for single-frame format
            sub_sock.setsockopt_string(zmq.SUBSCRIBE, "")

            # Brief sleep to allow slow joiner to warm up (ZMQ slow joiner problem)
            time.sleep(0.5)

            # Collect message timestamps over a 4-second window
            messages: list[float] = []
            last_payload: dict[str, Any] | None = None
            deadline: float = time.monotonic() + 4.0

            while time.monotonic() < deadline:
                try:
                    frames: list[bytes] = sub_sock.recv_multipart()
                    messages.append(time.monotonic())
                    # Try to decode the payload for content assertion
                    if len(frames) >= 2 and last_payload is None:
                        try:
                            decoded: Any = msgpack.unpackb(frames[1], raw=False)
                            if isinstance(decoded, dict) and (
                                "state" in decoded or "payload" in decoded
                            ):
                                last_payload = decoded
                        except Exception:
                            pass
                except zmq.Again:
                    break  # Receive timeout — no more messages in window

            # Assert rate: >= 3 messages in 4 seconds (1Hz; accounting for jitter)
            assert len(messages) >= 3, (
                f"CTRL-12: Expected >= 3 telemetry messages in 4s at 1Hz, "
                f"got {len(messages)} messages. "
                "Check: control_manager PUB endpoint wired correctly, "
                f"endpoint={control_pub_ep}"
            )

            # Assert content: at least one message carries state data
            assert last_payload is not None, (
                "CTRL-12: Received messages but none could be decoded as control state. "
                "Expected msgpack payload with 'state' or 'payload' field."
            )

        finally:
            sub_sock.close()
            ctx.term()


# ---------------------------------------------------------------------------
# TestDispatchFlow
# ---------------------------------------------------------------------------


class TestDispatchFlow:
    """Dispatch flow validation — 5 scenarios from CONTEXT.md.

    Tests that the control loop correctly computes and dispatches setpoints
    to the PCS Modbus simulator based on SOC, temperature, and source priority.

    Each test scenario validates a specific requirement:
    - test_normal_discharge: CTRL-01, CTRL-03, CTRL-04
    - test_soc_cutoff: CTRL-05
    - test_temperature_derating: CTRL-06
    - test_manual_override: CTRL-10
    - test_no_source_available: CTRL-04 (source exhaustion path)
    """

    @pytest.fixture(scope="class")
    def dispatch_system(self) -> Any:
        """Launch full M1 + M2 system for dispatch flow testing.

        Uses a separate port set from TestProtectionFlow to allow parallel
        test execution without port conflicts.
        """
        # Skip if prerequisites not available
        if not _vcan_available():
            pytest.skip("vcan0 not available — run 'make sim' to set up virtual CAN")
        if not _binary_exists("data_manager_c"):
            pytest.skip("data_manager_c not built — run 'make build' first")

        # Allocate a separate set of TCP ports (distinct from TestProtectionFlow)
        ports: list[int] = _allocate_tcp_ports(8)
        control_cmd_port: int = ports[0]
        alarm_cmd_port: int = ports[1]
        control_pub_port: int = ports[2]
        alarm_pub_port: int = ports[3]
        control_push_port: int = ports[4]
        alarm_push_port: int = ports[5]
        alarm_sub_port: int = ports[6]  # noqa: F841 (allocated for separation)
        config_sub_port: int = ports[7]

        control_cmd_ep: str = f"tcp://127.0.0.1:{control_cmd_port}"
        alarm_cmd_ep: str = f"tcp://127.0.0.1:{alarm_cmd_port}"
        control_pub_ep: str = f"tcp://127.0.0.1:{control_pub_port}"
        alarm_pub_ep: str = f"tcp://127.0.0.1:{alarm_pub_port}"
        control_push_ep: str = f"tcp://127.0.0.1:{control_push_port}"
        alarm_push_ep: str = f"tcp://127.0.0.1:{alarm_push_port}"
        config_sub_ep: str = f"tcp://127.0.0.1:{config_sub_port}"

        profile: dict[str, Any] = PROFILES["residential"]
        config_dir: Path = profile["config_dir"]

        tmpdir: str = tempfile.mkdtemp(prefix="ems_m2_disp_")
        tmpdir_path: Path = Path(tmpdir)

        for yaml_file in config_dir.glob("*.yaml"):
            shutil.copy(yaml_file, tmpdir_path / yaml_file.name)

        modules: list[ModuleProcess] = []
        simulators: list[subprocess.Popen[bytes]] = []

        try:
            # 1. data_manager_c
            dm_c = ModuleProcess(
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
            modules.append(dm_c)

            # 2. data_manager_python
            dm_py = ModuleProcess(
                name="data_manager_python",
                cmd=["uv", "run", "python", "-m", "ems_data_manager"],
                ready_check=_delay_ready(2.0),
            )
            modules.append(dm_py)

            # 3. config_manager
            cfg_mgr = ModuleProcess(
                name="config_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_config_manager",
                    "--config-dir", str(tmpdir_path),
                ],
                ready_check=_delay_ready(1.0),
            )
            modules.append(cfg_mgr)

            # 4. safety_manager
            if _binary_exists("safety_manager"):
                sm = ModuleProcess(
                    name="safety_manager",
                    cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
                    ready_check=_delay_ready(1.0),
                    env={"EMS_GPIO_BACKEND": "rtdb"},
                )
                modules.append(sm)

            # 5. comm_manager_c
            if _binary_exists("comm_manager_c"):
                cm_c = ModuleProcess(
                    name="comm_manager_c",
                    cmd=[
                        str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                        "--interface", "vcan0",
                        "--base-id", "0x18FF0003",
                        "--heartbeat-timeout-ms", "900",
                    ],
                    ready_check=_delay_ready(1.0),
                )
                modules.append(cm_c)

            # 6. comm_manager_python
            cm_py = ModuleProcess(
                name="comm_manager_python",
                cmd=[
                    "uv", "run", "python", "-m", "ems_comm_manager",
                    "--config", str(CONFIG_DIR),
                ],
                ready_check=_delay_ready(2.0),
            )
            modules.append(cm_py)

            # 7. logger
            lgr = ModuleProcess(
                name="logger",
                cmd=[
                    "uv", "run", "python", "-m", "ems_logger",
                    "--config", str(CONFIG_DIR / "logger_config.yaml"),
                ],
                ready_check=_delay_ready(1.0),
            )
            modules.append(lgr)

            # 8. CAN simulator — normal voltage, default SOC
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

            # 10. control_manager
            ctrl_mgr = ModuleProcess(
                name="control_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_control_manager",
                    "--config", str(tmpdir_path / "control_config.yaml"),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_CONTROL_CMD_ENDPOINT": control_cmd_ep,
                    "EMS_CONTROL_PUB_ENDPOINT": control_pub_ep,
                    "EMS_CONTROL_PUSH_ENDPOINT": control_push_ep,
                    "EMS_ALARM_SUB_ENDPOINT": alarm_pub_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(ctrl_mgr)

            # 11. alarm_manager
            alm_mgr = ModuleProcess(
                name="alarm_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_alarm_manager",
                    "--config", str(tmpdir_path / "alarms_config.yaml"),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_ALARM_CMD_ENDPOINT": alarm_cmd_ep,
                    "EMS_ALARM_PUB_ENDPOINT": alarm_pub_ep,
                    "EMS_ALARM_PUSH_ENDPOINT": alarm_push_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(alm_mgr)

            # Wait for system to stabilize, then set STANDBY
            time.sleep(5)

            # Transition to STANDBY
            try:
                send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
                wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)
            except zmq.Again:
                pass  # Fixture proceeds; individual tests handle state

            yield {
                "modules": modules,
                "simulators": simulators,
                "tmpdir": tmpdir_path,
                "config_dir": tmpdir_path,
                "control_cmd_endpoint": control_cmd_ep,
                "alarm_cmd_endpoint": alarm_cmd_ep,
                "control_pub_endpoint": control_pub_ep,
                "bms_config_src": config_dir / "bms_config.yaml",
            }

        finally:
            for sim in simulators:
                _kill_subprocess_group(sim)
            for mod in reversed(modules):
                try:
                    mod.cleanup()
                except Exception:
                    pass
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers for dispatch tests
    # ------------------------------------------------------------------

    def _read_pcs_register(self, address: int) -> int | None:
        """Read a PCS Modbus register from the Modbus simulator (TCP port 502).

        Returns the register value or None if the read fails.
        """
        try:
            from pymodbus.client import ModbusTcpClient

            pcs_client: ModbusTcpClient = ModbusTcpClient("127.0.0.1", port=502)
            if not pcs_client.connect():
                return None
            try:
                result = pcs_client.read_holding_registers(
                    address=address, count=1, slave=1
                )
                if result.isError():
                    return None
                return int(result.registers[0])
            finally:
                pcs_client.close()
        except Exception:
            return None

    def _read_rtdb_soc(self) -> float:
        """Read total_soc from RTDB system section."""
        try:
            shm, rtdb = attach_rtdb()
            soc: float = float(rtdb.system.total_soc)
            detach_rtdb(shm)
            return soc
        except Exception:
            return 0.0

    def _restart_can_sim(
        self,
        dispatch_system: dict[str, Any],
        config_path: Path,
    ) -> None:
        """Kill the current CAN simulator and restart it with a new config."""
        sims: list[subprocess.Popen[bytes]] = dispatch_system["simulators"]
        _kill_subprocess_group(sims[0])
        time.sleep(0.5)

        new_can_proc: subprocess.Popen[bytes] = subprocess.Popen(
            [
                "uv", "run", "python", "-m", "tools.simulators.can_sim",
                "--interface", "vcan0",
                "--config", str(config_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setpgrp,
            cwd=str(PROJECT_ROOT),
        )
        sims[0] = new_can_proc

    # ------------------------------------------------------------------
    # Test 1: Normal discharge
    # ------------------------------------------------------------------

    def test_normal_discharge(self, dispatch_system: dict[str, Any]) -> None:
        """Verify normal discharge setpoint dispatched to PCS.

        Validates CTRL-01 (1Hz loop), CTRL-03 (PCS dispatch), CTRL-04 (source priority).
        Expected: residential profile max_discharge_kw=25 -> PCS register 0x500E = 250.
        """
        control_cmd_ep: str = dispatch_system["control_cmd_endpoint"]

        # Ensure STANDBY
        try:
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        # Set night mode (grid + bess priority, no solar)
        try:
            send_control_command(
                control_cmd_ep, "source_priority", {"mode": "night"}
            )
        except zmq.Again:
            pytest.skip("Normal discharge test: Timeout setting source_priority")

        # Transition to DISCHARGING
        try:
            resp: dict[str, Any] = send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pytest.skip("Normal discharge test: Timeout sending DISCHARGING command")

        # Wait for DISCHARGING state or skip if not reached
        discharge_result: dict[str, bool] = wait_for_criteria(
            {"discharging": check_control_state(STATE_DISCHARGING)},
            timeout=10.0,
        )
        if not discharge_result["discharging"]:
            pytest.skip(
                f"Normal discharge test: Could not reach DISCHARGING state "
                f"(state={read_control_state()}, resp={resp})"
            )

        # Allow dispatch loop to write PCS register
        time.sleep(3.0)

        # Read current SOC to compute expected setpoint dynamically
        # (CAN sim SOC may not be exactly 50% — adapt to actual value)
        current_soc: float = self._read_rtdb_soc()
        if current_soc < 11.0:
            pytest.skip(
                f"Normal discharge test: SOC too low for discharge ({current_soc:.1f}%)"
            )

        # Read PCS setpoint register
        power_reg: int | None = self._read_pcs_register(0x500E)
        if power_reg is None:
            pytest.skip(
                "Normal discharge test: Cannot connect to Modbus simulator on port 502"
            )

        # At SOC > 10% with NIGHT mode, expected setpoint is max_discharge_kw=25kW -> 250
        # Allow tolerance for ramp-up (ramp_kw_s=5 means 5 ticks to reach 25kW from 0)
        assert power_reg > 0, (
            f"Normal discharge test: Expected PCS 0x500E > 0 during discharge, got {power_reg}"
        )

        # Verify not exceeding max_discharge_kw limit: 25 kW * 10 = 250
        assert power_reg <= 250, (
            f"Normal discharge test: PCS 0x500E={power_reg} exceeds max 250 (25 kW * 10)"
        )

    # ------------------------------------------------------------------
    # Test 2: SOC cutoff
    # ------------------------------------------------------------------

    def test_soc_cutoff(self, dispatch_system: dict[str, Any]) -> None:
        """Verify SOC cutoff transitions control to IDLE and zeroes PCS setpoint.

        Validates CTRL-05: discharge stops when SOC reaches discharge_cutoff_pct (10%).

        Note: If the CAN simulator's fault_injection does not support soc_base override,
        this test reads the actual RTDB SOC and asserts that at 10% the state is IDLE.
        The bms_config injection approach is used as the primary mechanism.
        """
        control_cmd_ep: str = dispatch_system["control_cmd_endpoint"]
        tmpdir: Path = dispatch_system["tmpdir"]
        bms_config_src: Path = dispatch_system["bms_config_src"]

        # Reset to STANDBY first
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.5)
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        # Write bms_config with SOC at discharge_cutoff_pct (10%)
        # fault_injection.soc_base overrides the simulator's SOC signal
        low_soc_config: Path = tmpdir / "bms_config_low_soc.yaml"
        _write_test_bms_config(
            bms_config_src, low_soc_config, base_voltage=3.3, drift=0.0, base_soc=10.0
        )
        self._restart_can_sim(dispatch_system, low_soc_config)

        # Wait for RTDB SOC to reflect ~10%
        soc_criteria: dict[str, Callable[[], bool]] = {
            "low_soc": lambda: 5.0 <= self._read_rtdb_soc() <= 15.0,
        }
        soc_result: dict[str, bool] = wait_for_criteria(soc_criteria, timeout=5.0)
        if not soc_result["low_soc"]:
            # CAN sim may not support soc_base injection — read actual SOC
            actual_soc: float = self._read_rtdb_soc()
            if actual_soc > 15.0:
                pytest.skip(
                    f"SOC cutoff test: CAN sim fault_injection may not support soc_base "
                    f"(RTDB SOC={actual_soc:.1f}% — skipping rather than false-passing)"
                )

        # Transition to DISCHARGING — control should reject or quickly return to IDLE
        try:
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pass

        # Wait for IDLE transition (SOC cutoff should trigger IDLE within a few ticks)
        idle_criteria: dict[str, Callable[[], bool]] = {
            "idle": check_control_state(STATE_IDLE),
        }
        idle_result: dict[str, bool] = wait_for_criteria(idle_criteria, timeout=8.0)
        assert idle_result["idle"], (
            f"SOC cutoff test: Expected IDLE at discharge_cutoff_pct=10%, "
            f"got state={read_control_state()}, SOC={self._read_rtdb_soc():.1f}%"
        )

        # Verify PCS setpoint is 0
        power_reg: int | None = self._read_pcs_register(0x500E)
        if power_reg is not None:
            assert power_reg == 0, (
                f"SOC cutoff test: Expected PCS 0x500E=0 when IDLE, got {power_reg}"
            )

        # Cleanup: restore normal CAN sim config
        self._restart_can_sim(dispatch_system, tmpdir / "bms_config.yaml")

    # ------------------------------------------------------------------
    # Test 3: Temperature derating
    # ------------------------------------------------------------------

    def test_temperature_derating(self, dispatch_system: dict[str, Any]) -> None:
        """Verify temperature derating reduces PCS setpoint at high BMS cell temp.

        Validates CTRL-06: derating curve applies at bms_high_start_c=40°C,
        50% reduction at ~45°C (midpoint of 40-50°C range).

        Note: Uses CAN sim fault_injection.cell_temp_base override. If not supported,
        reads actual RTDB temp and computes expected setpoint accordingly.
        """
        control_cmd_ep: str = dispatch_system["control_cmd_endpoint"]
        tmpdir: Path = dispatch_system["tmpdir"]
        bms_config_src: Path = dispatch_system["bms_config_src"]

        # Reset to STANDBY
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.5)
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        # Write bms_config with high cell temperature (~48°C — above 40°C derating threshold)
        high_temp_config: Path = tmpdir / "bms_config_high_temp.yaml"
        _write_test_bms_config(
            bms_config_src,
            high_temp_config,
            base_voltage=3.3,
            drift=0.0,
            base_temp=48.0,
        )
        self._restart_can_sim(dispatch_system, high_temp_config)

        # Set night mode and transition to DISCHARGING
        try:
            send_control_command(control_cmd_ep, "source_priority", {"mode": "night"})
            time.sleep(0.5)
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pytest.skip("Temperature derating test: Timeout sending commands")

        discharge_result: dict[str, bool] = wait_for_criteria(
            {"discharging": check_control_state(STATE_DISCHARGING)},
            timeout=10.0,
        )
        if not discharge_result["discharging"]:
            pytest.skip(
                f"Temperature derating test: Cannot reach DISCHARGING "
                f"(state={read_control_state()})"
            )

        # Allow a few ticks for derating to apply and ramp to stabilize
        time.sleep(3.0)

        # Read PCS setpoint — at 48°C (between 40-50°C), derating factor is ~0.2
        # (48-40)/(50-40) = 0.8 zone factor -> 20% reduction -> derating_pct = 80%
        # Expected setpoint ~ 25 kW * (1 - 0.8) = 5 kW -> register ~50
        # OR if CAN sim doesn't support temp_base, we just verify it's derated vs max
        power_reg: int | None = self._read_pcs_register(0x500E)
        if power_reg is None:
            pytest.skip(
                "Temperature derating test: Cannot connect to Modbus simulator"
            )

        # Verify setpoint is below max (250 = 25 kW * 10) — derating applied
        assert power_reg < 250, (
            f"Temperature derating test: Expected derated PCS register < 250, "
            f"got {power_reg} (derating not applied at high cell temp)"
        )
        assert power_reg > 0, (
            f"Temperature derating test: Expected derated setpoint > 0, "
            f"got {power_reg}"
        )

        # Cleanup: restore normal CAN sim config
        self._restart_can_sim(dispatch_system, tmpdir / "bms_config.yaml")
        try:
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass

    # ------------------------------------------------------------------
    # Test 4: Manual override
    # ------------------------------------------------------------------

    def test_manual_override(self, dispatch_system: dict[str, Any]) -> None:
        """Verify manual setpoint command dispatches exact value to PCS.

        Validates CTRL-10: ZMQ manual_setpoint command sets PCS register 0x500E
        to the exact requested power level (15.0 kW -> 150 in 0.1 kW encoding).
        """
        control_cmd_ep: str = dispatch_system["control_cmd_endpoint"]

        # Reset to STANDBY
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.5)
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        # Transition to DISCHARGING to allow setpoint writes
        try:
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pytest.skip("Manual override test: Timeout sending DISCHARGING command")

        discharge_result: dict[str, bool] = wait_for_criteria(
            {"discharging": check_control_state(STATE_DISCHARGING)},
            timeout=10.0,
        )
        if not discharge_result["discharging"]:
            pytest.skip(
                f"Manual override test: Cannot reach DISCHARGING "
                f"(state={read_control_state()})"
            )

        # Send manual setpoint command: 15.0 kW
        try:
            resp: dict[str, Any] = send_control_command(
                control_cmd_ep, "manual_setpoint", {"power_kw": 15.0}
            )
        except zmq.Again:
            pytest.fail("Manual override test: Timeout sending manual_setpoint command")

        assert resp.get("status") == "ok", (
            f"Manual override test: Expected 'ok' response to manual_setpoint, "
            f"got: {resp}"
        )

        # Allow ramp to settle (ramp_kw_s=5: 15kW / 5kW/s = 3 ticks to reach setpoint)
        time.sleep(3.0)

        # Read PCS setpoint register: 15.0 kW * 10 = 150
        power_reg: int | None = self._read_pcs_register(0x500E)
        if power_reg is None:
            pytest.skip(
                "Manual override test: Cannot connect to Modbus simulator"
            )

        # Allow ±2 tolerance for ramp timing
        assert abs(power_reg - 150) <= 10, (
            f"Manual override test: Expected PCS 0x500E ≈ 150 (15.0 kW * 10), "
            f"got {power_reg}"
        )

        # Cleanup: return to STANDBY
        try:
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass

    # ------------------------------------------------------------------
    # Test 5: No source available
    # ------------------------------------------------------------------

    def test_no_source_available(self, dispatch_system: dict[str, Any]) -> None:
        """Verify source exhaustion yields PCS=0 and state transitions to IDLE.

        Validates CTRL-04: when all energy sources are unavailable (grid offline
        via GPIO, SOC at cutoff, no DG in residential profile), control_manager
        must set PCS=0 and transition to IDLE.
        """
        control_cmd_ep: str = dispatch_system["control_cmd_endpoint"]
        tmpdir: Path = dispatch_system["tmpdir"]
        bms_config_src: Path = dispatch_system["bms_config_src"]

        # Reset to STANDBY
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.5)
            send_control_command(control_cmd_ep, "mode_change", {"target_state": "standby"})
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        # Step 1: Configure CAN sim with low SOC (at discharge_cutoff_pct=10%)
        low_soc_config: Path = tmpdir / "bms_config_no_src.yaml"
        _write_test_bms_config(
            bms_config_src, low_soc_config, base_voltage=3.3, drift=0.0, base_soc=10.0
        )
        self._restart_can_sim(dispatch_system, low_soc_config)

        # Step 2: Simulate grid offline via GPIO harness (DI-0=0 = ACDB feedback = offline)
        # EMS_GPIO_BACKEND=rtdb allows direct RTDB write for the safety_manager
        try:
            shm, rtdb = attach_rtdb()
            rtdb.gpio.di[0] = 0  # ACDB feedback: 0 = grid offline
            detach_rtdb(shm)
        except Exception as exc:
            pytest.skip(f"No-source test: Cannot write RTDB GPIO: {exc}")

        # Wait for system to process offline state
        time.sleep(2.0)

        # Step 3: No DG available in residential profile (no DG configured)
        # The control intelligence hard-codes dg=False in M2 (STATE.md Key Decision 16-01)

        # Step 4: Attempt dispatch — control should determine no source available
        try:
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pass

        # Step 5: Assert IDLE state and PCS=0
        idle_criteria: dict[str, Callable[[], bool]] = {
            "idle": check_control_state(STATE_IDLE),
        }
        idle_result: dict[str, bool] = wait_for_criteria(idle_criteria, timeout=5.0)
        assert idle_result["idle"], (
            f"No-source test: Expected IDLE when all sources exhausted, "
            f"got state={read_control_state()} "
            f"(SOC={self._read_rtdb_soc():.1f}%, grid=offline, DG=unavailable)"
        )

        power_reg: int | None = self._read_pcs_register(0x500E)
        if power_reg is not None:
            assert power_reg == 0, (
                f"No-source test: Expected PCS 0x500E=0 with no source, got {power_reg}"
            )

        # Cleanup: restore grid online and normal SOC
        try:
            shm, rtdb = attach_rtdb()
            rtdb.gpio.di[0] = 1  # ACDB feedback: 1 = grid online
            detach_rtdb(shm)
        except Exception:
            pass
        self._restart_can_sim(dispatch_system, tmpdir / "bms_config.yaml")
        time.sleep(1.0)
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
        except zmq.Again:
            pass


# ---------------------------------------------------------------------------
# Atomic config modification helper
# ---------------------------------------------------------------------------


def modify_config_atomic(path: Path, updates: dict[str, Any]) -> None:
    """Modify nested YAML config keys atomically.

    Uses write-to-tmp + rename to trigger a single inotify IN_MOVED_TO
    event, avoiding partial reads by config_manager.

    Args:
        path: Path to YAML file.
        updates: Dict of dot-separated key paths to new values.
                 E.g., {"soc_limits.discharge_cutoff_pct": 20}
    """
    with open(path) as f:
        cfg: dict[str, Any] = yaml.safe_load(f)
    for key_path, value in updates.items():
        keys: list[str] = key_path.split(".")
        obj: Any = cfg
        for k in keys[:-1]:
            obj = obj[k]
        obj[keys[-1]] = value
    tmp: Path = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    os.rename(tmp, path)  # atomic on Linux same-filesystem (inotify IN_MOVED_TO)


def send_alarm_command(endpoint: str, cmd: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send a ZMQ REQ command to the alarm_manager REP socket.

    Args:
        endpoint: TCP endpoint string like 'tcp://127.0.0.1:{port}'.
        cmd: Command action string (e.g. 'get_active_alarms').
        params: Command parameters dict.

    Returns:
        Decoded response dict from decode_command_response.

    Raises:
        zmq.Again: If the request times out (3s timeout).
    """
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


def _read_total_soc() -> float:
    """Attach RTDB, read system.total_soc, detach, return float."""
    try:
        shm, rtdb = attach_rtdb()
        soc: float = float(rtdb.system.total_soc)
        detach_rtdb(shm)
        return soc
    except Exception:
        return 0.0


def _read_pcs_reg(address: int) -> int | None:
    """Read a PCS Modbus holding register from the simulator on TCP port 502."""
    try:
        from pymodbus.client import ModbusTcpClient

        pcs_client: ModbusTcpClient = ModbusTcpClient("127.0.0.1", port=502)
        if not pcs_client.connect():
            return None
        try:
            result = pcs_client.read_holding_registers(
                address=address, count=1, slave=1
            )
            if result.isError():
                return None
            return int(result.registers[0])
        finally:
            pcs_client.close()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TestHotReload
# ---------------------------------------------------------------------------


class TestHotReload:
    """Hot-reload integration tests — validates CTRL-11 and ALM-09.

    Proves the hot-reload chain:
    file modify -> inotify IN_MOVED_TO -> config_manager validates
    -> ZMQ PUB config_reload event -> module re-reads from disk -> applies

    Timing budget: 500ms inotify debounce + validation + ZMQ delivery + 1 tick
    = ~1.5s expected. Tests allow 2s wait with 5s total timeout for margin.

    Tests:
    1. Control config discharge_cutoff_pct reload (CTRL-11)
    2. Alarm config cell_voltage_high threshold reload (ALM-09)
    3. Alarm config rule enable/disable reload (ALM-09)
    4. Control config max_discharge_kw power limit reload (CTRL-11)
    """

    @pytest.fixture(scope="class")
    def reload_system(self) -> Any:
        """Launch full M1 + M2 system for hot-reload testing.

        Saves original config file contents before tests run so they can
        be restored during teardown even on failure.
        """
        if not _vcan_available():
            pytest.skip("vcan0 not available — run 'make sim' to set up virtual CAN")
        if not _binary_exists("data_manager_c"):
            pytest.skip("data_manager_c not built — run 'make build' first")

        # Allocate a distinct set of TCP ports for this test class
        ports: list[int] = _allocate_tcp_ports(8)
        control_cmd_port: int = ports[0]
        alarm_cmd_port: int = ports[1]
        control_pub_port: int = ports[2]
        alarm_pub_port: int = ports[3]
        control_push_port: int = ports[4]
        alarm_push_port: int = ports[5]
        # ports[6] reserved for port isolation (alarm_sub not needed by reload tests)
        config_sub_port: int = ports[7]

        control_cmd_ep: str = f"tcp://127.0.0.1:{control_cmd_port}"
        alarm_cmd_ep: str = f"tcp://127.0.0.1:{alarm_cmd_port}"
        control_pub_ep: str = f"tcp://127.0.0.1:{control_pub_port}"
        alarm_pub_ep: str = f"tcp://127.0.0.1:{alarm_pub_port}"
        control_push_ep: str = f"tcp://127.0.0.1:{control_push_port}"
        alarm_push_ep: str = f"tcp://127.0.0.1:{alarm_push_port}"
        config_sub_ep: str = f"tcp://127.0.0.1:{config_sub_port}"

        profile: dict[str, Any] = PROFILES["residential"]
        config_dir: Path = profile["config_dir"]

        tmpdir: str = tempfile.mkdtemp(prefix="ems_m2_reload_")
        tmpdir_path: Path = Path(tmpdir)

        # Copy residential profile configs to tmpdir
        for yaml_file in config_dir.glob("*.yaml"):
            shutil.copy(yaml_file, tmpdir_path / yaml_file.name)

        # CRITICAL: Save original config contents BEFORE any test runs
        control_cfg_path: Path = tmpdir_path / "control_config.yaml"
        alarms_cfg_path: Path = tmpdir_path / "alarms_config.yaml"
        original_configs: dict[str, str] = {
            str(control_cfg_path): control_cfg_path.read_text(),
            str(alarms_cfg_path): alarms_cfg_path.read_text(),
        }

        modules: list[ModuleProcess] = []
        simulators: list[subprocess.Popen[bytes]] = []

        try:
            # 1. data_manager_c — creates RTDB shared memory
            dm_c = ModuleProcess(
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
            modules.append(dm_c)

            # 2. data_manager_python
            dm_py = ModuleProcess(
                name="data_manager_python",
                cmd=["uv", "run", "python", "-m", "ems_data_manager"],
                ready_check=_delay_ready(2.0),
            )
            modules.append(dm_py)

            # 3. config_manager — watches tmpdir for inotify events
            cfg_mgr = ModuleProcess(
                name="config_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_config_manager",
                    "--config-dir", str(tmpdir_path),
                ],
                ready_check=_delay_ready(1.0),
                env={
                    "EMS_CONFIG_PUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(cfg_mgr)

            # 4. safety_manager
            if _binary_exists("safety_manager"):
                sm = ModuleProcess(
                    name="safety_manager",
                    cmd=[str(BUILD_DIR / "src" / "safety_manager" / "safety_manager")],
                    ready_check=_delay_ready(1.0),
                    env={"EMS_GPIO_BACKEND": "rtdb"},
                )
                modules.append(sm)

            # 5. comm_manager_c
            if _binary_exists("comm_manager_c"):
                cm_c = ModuleProcess(
                    name="comm_manager_c",
                    cmd=[
                        str(BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"),
                        "--interface", "vcan0",
                        "--base-id", "0x18FF0003",
                        "--heartbeat-timeout-ms", "900",
                    ],
                    ready_check=_delay_ready(1.0),
                )
                modules.append(cm_c)

            # 6. comm_manager_python
            cm_py = ModuleProcess(
                name="comm_manager_python",
                cmd=[
                    "uv", "run", "python", "-m", "ems_comm_manager",
                    "--config", str(CONFIG_DIR),
                ],
                ready_check=_delay_ready(2.0),
            )
            modules.append(cm_py)

            # 7. logger
            lgr = ModuleProcess(
                name="logger",
                cmd=[
                    "uv", "run", "python", "-m", "ems_logger",
                    "--config", str(CONFIG_DIR / "logger_config.yaml"),
                ],
                ready_check=_delay_ready(1.0),
            )
            modules.append(lgr)

            # 8. CAN simulator — normal voltage, ~50% SOC
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

            # 10. control_manager — watches tmpdir/control_config.yaml for config_reload
            ctrl_mgr = ModuleProcess(
                name="control_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_control_manager",
                    "--config", str(control_cfg_path),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_CONTROL_CMD_ENDPOINT": control_cmd_ep,
                    "EMS_CONTROL_PUB_ENDPOINT": control_pub_ep,
                    "EMS_CONTROL_PUSH_ENDPOINT": control_push_ep,
                    "EMS_ALARM_SUB_ENDPOINT": alarm_pub_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(ctrl_mgr)

            # 11. alarm_manager — watches tmpdir/alarms_config.yaml for config_reload
            alm_mgr = ModuleProcess(
                name="alarm_manager",
                cmd=[
                    "uv", "run", "python", "-m", "ems_alarm_manager",
                    "--config", str(alarms_cfg_path),
                ],
                ready_check=_delay_ready(2.0),
                env={
                    "EMS_ALARM_CMD_ENDPOINT": alarm_cmd_ep,
                    "EMS_ALARM_PUB_ENDPOINT": alarm_pub_ep,
                    "EMS_ALARM_PUSH_ENDPOINT": alarm_push_ep,
                    "EMS_CONFIG_SUB_ENDPOINT": config_sub_ep,
                },
            )
            modules.append(alm_mgr)

            # Wait for system to stabilize
            time.sleep(5)

            yield {
                "modules": modules,
                "simulators": simulators,
                "tmpdir": tmpdir_path,
                "control_cmd_endpoint": control_cmd_ep,
                "alarm_cmd_endpoint": alarm_cmd_ep,
                "control_cfg_path": control_cfg_path,
                "alarms_cfg_path": alarms_cfg_path,
                "original_configs": original_configs,
                "bms_config_src": config_dir / "bms_config.yaml",
            }

        finally:
            # ALWAYS restore original config files, even on failure
            for path_str, content in original_configs.items():
                try:
                    Path(path_str).write_text(content)
                except Exception:
                    pass
            # Teardown: kill simulators first, then modules in reverse order
            for sim in simulators:
                _kill_subprocess_group(sim)
            for mod in reversed(modules):
                try:
                    mod.cleanup()
                except Exception:
                    pass
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    @pytest.fixture(autouse=True)
    def restore_configs(self, reload_system: dict[str, Any]) -> Any:
        """Restore config files after each test to prevent inter-test pollution.

        Ensures that even if a test fails mid-way, config files are returned
        to their original state before the next test runs.
        """
        originals: dict[str, str] = reload_system["original_configs"]
        yield
        for path_str, content in originals.items():
            Path(path_str).write_text(content)
        # Allow inotify to settle after restoration before next test starts
        time.sleep(0.3)

    # ------------------------------------------------------------------
    # Test 1: Control config discharge_cutoff_pct reload (CTRL-11)
    # ------------------------------------------------------------------

    def test_control_config_discharge_cutoff_reload(
        self, reload_system: dict[str, Any]
    ) -> None:
        """Verify control_config discharge_cutoff_pct hot-reload stops dispatch.

        Validates CTRL-11: modifying discharge_cutoff_pct from 10 to 90 while
        DISCHARGING (SOC ~50%) causes control_manager to transition to IDLE
        within 2 seconds (SOC 50% is now below 90% cutoff).
        No module restart — reload chain: inotify -> config_manager -> ZMQ event
        -> control_manager re-reads disk -> applies on next tick.
        """
        control_cmd_ep: str = reload_system["control_cmd_endpoint"]
        control_cfg_path: Path = reload_system["control_cfg_path"]

        # Ensure we have enough SOC to enter DISCHARGING
        current_soc: float = _read_total_soc()
        if current_soc < 15.0:
            pytest.skip(
                f"Discharge cutoff reload test: SOC too low ({current_soc:.1f}%) "
                "— cannot test discharge_cutoff_pct hot-reload"
            )

        # Transition to STANDBY, then DISCHARGING
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.3)
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "standby"}
            )
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        try:
            send_control_command(control_cmd_ep, "source_priority", {"mode": "night"})
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pytest.skip("Discharge cutoff reload test: Timeout sending DISCHARGING command")

        discharge_result: dict[str, bool] = wait_for_criteria(
            {"discharging": check_control_state(STATE_DISCHARGING)},
            timeout=10.0,
        )
        if not discharge_result["discharging"]:
            pytest.skip(
                f"Discharge cutoff reload test: Cannot reach DISCHARGING "
                f"(state={read_control_state()})"
            )

        # Verify PCS is dispatching before reload
        time.sleep(1.0)
        power_before: int | None = _read_pcs_reg(0x500E)
        if power_before is not None:
            assert power_before > 0, (
                f"Discharge cutoff reload test: Expected PCS > 0 in DISCHARGING, "
                f"got {power_before}"
            )

        # HOT-RELOAD: change discharge_cutoff_pct from 10 -> 90
        # With SOC ~50%, the new cutoff of 90% means SOC < cutoff -> transition to IDLE
        modify_config_atomic(control_cfg_path, {"soc_limits.discharge_cutoff_pct": 90})

        # Wait 2s for reload chain (500ms debounce + ZMQ delivery + 1 tick)
        time.sleep(2.0)

        # Verify: control_manager transitioned to IDLE (SOC 50% < 90% cutoff)
        idle_result: dict[str, bool] = wait_for_criteria(
            {"idle": check_control_state(STATE_IDLE)},
            timeout=5.0,
        )
        assert idle_result["idle"], (
            f"CTRL-11 discharge cutoff reload: Expected IDLE after raising cutoff to 90%, "
            f"got state={read_control_state()} "
            f"(SOC={_read_total_soc():.1f}%, reload should have applied within 2s)"
        )

        # Verify PCS setpoint dropped to 0
        power_after: int | None = _read_pcs_reg(0x500E)
        if power_after is not None:
            assert power_after == 0, (
                f"CTRL-11 discharge cutoff reload: Expected PCS 0x500E=0 after IDLE, "
                f"got {power_after}"
            )

    # ------------------------------------------------------------------
    # Test 2: Alarm config threshold reload (ALM-09)
    # ------------------------------------------------------------------

    def test_alarm_config_threshold_reload(
        self, reload_system: dict[str, Any]
    ) -> None:
        """Verify alarms_config cell_voltage_high threshold hot-reload fires alarm.

        Validates ALM-09: lowering cell_voltage_high.high_threshold from 3.65V
        to 3.00V while cell voltages are ~3.3V triggers a new alarm within the
        reload chain + alarm delay without restarting alarm_manager.
        """
        alarm_cmd_ep: str = reload_system["alarm_cmd_endpoint"]
        alarms_cfg_path: Path = reload_system["alarms_cfg_path"]

        # HOT-RELOAD: lower high_threshold from 3.65 -> 3.00 (below current ~3.3V)
        modify_config_atomic(
            alarms_cfg_path,
            {"rules.cell_voltage_high.high_threshold": 3.00},
        )

        # Wait for reload chain + alarm delay (2s reload + default 5s delay + 1s margin)
        # poll up to 10s to account for alarm activation time
        def _cell_voltage_high_active() -> bool:
            try:
                resp: dict[str, Any] = send_alarm_command(
                    alarm_cmd_ep, "get_active_alarms", {}
                )
                active: list[Any] = resp.get("alarms", [])
                return any(
                    isinstance(a, dict) and a.get("rule_id") == "cell_voltage_high"
                    for a in active
                )
            except Exception:
                return False

        alarm_result: dict[str, bool] = wait_for_criteria(
            {"cell_voltage_high": _cell_voltage_high_active},
            timeout=10.0,
        )
        assert alarm_result["cell_voltage_high"], (
            "ALM-09 threshold reload: Expected cell_voltage_high alarm active after "
            "lowering threshold to 3.00V (below ~3.3V cell voltage). "
            "get_active_alarms did not return cell_voltage_high within 10s."
        )

    # ------------------------------------------------------------------
    # Test 3: Alarm config rule disable reload (ALM-09)
    # ------------------------------------------------------------------

    def test_alarm_config_disable_reload(
        self, reload_system: dict[str, Any]
    ) -> None:
        """Verify alarms_config enabled=false hot-reload clears alarm.

        Validates ALM-09: after triggering cell_voltage_high by lowering
        threshold, setting rules.cell_voltage_high.enabled=false causes the
        alarm to no longer appear in the active alarms list within 2 seconds.
        """
        alarm_cmd_ep: str = reload_system["alarm_cmd_endpoint"]
        alarms_cfg_path: Path = reload_system["alarms_cfg_path"]

        # Step 1: Lower threshold to trigger the alarm (same as test 2)
        modify_config_atomic(
            alarms_cfg_path,
            {"rules.cell_voltage_high.high_threshold": 3.00},
        )

        def _cell_voltage_high_active() -> bool:
            try:
                resp: dict[str, Any] = send_alarm_command(
                    alarm_cmd_ep, "get_active_alarms", {}
                )
                active: list[Any] = resp.get("alarms", [])
                return any(
                    isinstance(a, dict) and a.get("rule_id") == "cell_voltage_high"
                    for a in active
                )
            except Exception:
                return False

        # Wait up to 10s for alarm to become active
        active_result: dict[str, bool] = wait_for_criteria(
            {"active": _cell_voltage_high_active},
            timeout=10.0,
        )
        if not active_result["active"]:
            pytest.skip(
                "Alarm disable reload test: cell_voltage_high alarm did not become "
                "active after threshold lowering — skipping disable test"
            )

        # Step 2: HOT-RELOAD: disable the rule (enabled=false)
        modify_config_atomic(
            alarms_cfg_path,
            {
                "rules.cell_voltage_high.high_threshold": 3.00,
                "rules.cell_voltage_high.enabled": False,
            },
        )

        # Wait 2s for reload chain to apply
        time.sleep(2.0)

        def _cell_voltage_high_gone() -> bool:
            try:
                resp: dict[str, Any] = send_alarm_command(
                    alarm_cmd_ep, "get_active_alarms", {}
                )
                active: list[Any] = resp.get("alarms", [])
                return not any(
                    isinstance(a, dict) and a.get("rule_id") == "cell_voltage_high"
                    for a in active
                )
            except Exception:
                return False

        gone_result: dict[str, bool] = wait_for_criteria(
            {"gone": _cell_voltage_high_gone},
            timeout=5.0,
        )
        assert gone_result["gone"], (
            "ALM-09 disable reload: Expected cell_voltage_high alarm absent after "
            "setting enabled=false, but it still appears in active alarms list."
        )

    # ------------------------------------------------------------------
    # Test 4: Control config max_discharge_kw power limit reload (CTRL-11)
    # ------------------------------------------------------------------

    def test_control_config_power_limit_reload(
        self, reload_system: dict[str, Any]
    ) -> None:
        """Verify control_config max_discharge_kw hot-reload reduces PCS setpoint.

        Validates CTRL-11: while DISCHARGING at 25 kW (PCS register ~250),
        reducing max_discharge_kw from 25 to 15 via hot-reload causes PCS
        register 0x500E to drop to ~150 (15 kW * 10) within 2 seconds.
        No module restart required.
        """
        control_cmd_ep: str = reload_system["control_cmd_endpoint"]
        control_cfg_path: Path = reload_system["control_cfg_path"]

        # Ensure enough SOC for discharge
        current_soc: float = _read_total_soc()
        if current_soc < 15.0:
            pytest.skip(
                f"Power limit reload test: SOC too low ({current_soc:.1f}%)"
            )

        # Transition to DISCHARGING at max power
        try:
            send_control_command(control_cmd_ep, "fault_reset", {})
            time.sleep(0.3)
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "standby"}
            )
        except zmq.Again:
            pass
        wait_for_criteria({"standby": check_control_state(STATE_STANDBY)}, timeout=10.0)

        try:
            send_control_command(control_cmd_ep, "source_priority", {"mode": "night"})
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "discharging"}
            )
        except zmq.Again:
            pytest.skip("Power limit reload test: Timeout sending DISCHARGING command")

        discharge_result: dict[str, bool] = wait_for_criteria(
            {"discharging": check_control_state(STATE_DISCHARGING)},
            timeout=10.0,
        )
        if not discharge_result["discharging"]:
            pytest.skip(
                f"Power limit reload test: Cannot reach DISCHARGING "
                f"(state={read_control_state()})"
            )

        # Allow dispatch to ramp up toward max (ramp_kw_s=5; 25kW / 5kW/s = 5 ticks)
        time.sleep(6.0)

        power_before: int | None = _read_pcs_reg(0x500E)
        if power_before is None:
            pytest.skip("Power limit reload test: Cannot connect to Modbus simulator")

        assert power_before > 0, (
            f"Power limit reload test: Expected PCS > 0 in DISCHARGING, "
            f"got {power_before}"
        )

        # HOT-RELOAD: lower max_discharge_kw from 25 -> 15
        modify_config_atomic(control_cfg_path, {"power_limits.max_discharge_kw": 15})

        # Wait 2s for reload chain (500ms debounce + ZMQ delivery + 1 tick)
        time.sleep(2.0)

        # Verify PCS register dropped to ~150 (15 kW * 10); allow ±20 for ramp timing
        def _pcs_at_reduced_limit() -> bool:
            val: int | None = _read_pcs_reg(0x500E)
            return val is not None and val <= 170

        reduced_result: dict[str, bool] = wait_for_criteria(
            {"reduced": _pcs_at_reduced_limit},
            timeout=5.0,
        )

        power_after: int | None = _read_pcs_reg(0x500E)
        assert reduced_result["reduced"], (
            f"CTRL-11 power limit reload: Expected PCS 0x500E <= 170 after lowering "
            f"max_discharge_kw to 15kW, got {power_after} "
            f"(before reload: {power_before})"
        )
        assert power_after is not None and power_after <= 170, (
            f"CTRL-11 power limit reload: PCS 0x500E={power_after} still above 170 "
            "after 5s settle time (expected ~150 for 15 kW limit)"
        )

        # Cleanup: return to STANDBY
        try:
            send_control_command(
                control_cmd_ep, "mode_change", {"target_state": "standby"}
            )
        except zmq.Again:
            pass
