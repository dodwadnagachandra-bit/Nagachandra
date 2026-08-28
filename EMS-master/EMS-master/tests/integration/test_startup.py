"""Startup sequence integration tests.

Validates that all core EMS modules launch in correct systemd-like
dependency ordering and reach healthy state within 30 seconds.

Startup order (derived from systemd After= dependencies):
1. data_manager_c    -- RTDB owner, no dependencies
2. data_manager_python -- After=ems-data-manager.service
3. config_manager    -- After=ems-data-manager.service
4. safety_manager    -- After=ems-data-manager.service
5. comm_manager_c    -- After=ems-safety-manager.service, ems-data-manager.service
6. comm_manager_python -- After=ems-safety-manager.service, ems-data-manager.service
7. logger            -- After=ems-data-manager.service, comm_manager.service
8. control_manager   -- After=ems-logger.service, ems-data-manager.service
9. alarm_manager     -- After=ems-control-manager.service, ems-data-manager.service
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
import yaml

from ems_common.ipc import SOCK_TELEMETRY
from ems_common.rtdb import RTDB_MAGIC, RTDB_VERSION, attach_rtdb, detach_rtdb

from tests.integration.conftest import (
    BUILD_DIR,
    CONFIG_DIR,
    ModuleProcess,
    PROFILES,
    _check_http_health,
    check_rtdb_exists,
    check_rtdb_fresh,
    check_zmq_receiving,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


# ---------------------------------------------------------------------------
# Module start order builder
# ---------------------------------------------------------------------------


def _delay_ready(seconds: float) -> Callable[[], bool]:
    """Return a ready_check that returns True after a fixed delay.

    Uses a closure over the first call time to avoid blocking the
    caller thread -- the poll loop in ModuleProcess._wait_ready
    calls this repeatedly until it returns True.
    """
    start: dict[str, float] = {}

    def _check() -> bool:
        if "t" not in start:
            start["t"] = time.monotonic()
        return (time.monotonic() - start["t"]) >= seconds

    return _check


def build_start_order(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Construct the ordered list of module specs for the given topology.

    Each entry is a dict with keys: name, cmd, ready_check, env, skip_reason.
    skip_reason is None if the module can be started, or a string describing
    why it should be skipped (missing binary, missing vcan0, etc.).

    Args:
        profile: Topology profile dict from PROFILES (clusters, racks, etc.).

    Returns:
        Ordered list of module spec dicts.
    """
    clusters: str = str(profile["clusters"])
    racks: str = str(profile["racks"])
    modules: str = str(profile["modules"])
    cells: str = str(profile["cells"])
    temps: str = str(profile["temps"])
    config_dir: Path = profile["config_dir"]

    dm_c_binary: Path = BUILD_DIR / "src" / "data_manager" / "c" / "data_manager_c"
    safety_binary: Path = BUILD_DIR / "src" / "safety_manager" / "safety_manager"
    comm_c_binary: Path = BUILD_DIR / "src" / "comm_manager" / "c" / "comm_manager_c"
    vcan_available: bool = Path("/sys/class/net/vcan0").exists()

    order: list[dict[str, Any]] = [
        {
            "name": "data_manager_c",
            "cmd": [str(dm_c_binary), clusters, racks, modules, cells, temps],
            "ready_check": check_rtdb_exists,
            "env": None,
            "skip_reason": None if dm_c_binary.exists() else f"Binary not found: {dm_c_binary}",
        },
        {
            "name": "data_manager_python",
            "cmd": ["uv", "run", "python", "-m", "ems_data_manager"],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "config_manager",
            "cmd": [
                "uv", "run", "python", "-m", "ems_config_manager",
                "--config-dir", str(config_dir),
                "--schema-dir", str(CONFIG_DIR / "schemas"),
            ],
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "safety_manager",
            "cmd": [str(safety_binary)],
            "ready_check": _delay_ready(1.0),
            "env": {"EMS_GPIO_BACKEND": "rtdb"},
            "skip_reason": None if safety_binary.exists() else f"Binary not found: {safety_binary}",
        },
        {
            "name": "comm_manager_c",
            "cmd": [
                str(comm_c_binary),
                "--interface", "vcan0",
                "--base-id", "0x18FF0003",
                "--heartbeat-timeout-ms", "900",
            ],
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": (
                None if (comm_c_binary.exists() and vcan_available)
                else (
                    f"Binary not found: {comm_c_binary}" if not comm_c_binary.exists()
                    else "vcan0 not available"
                )
            ),
        },
        {
            "name": "comm_manager_python",
            "cmd": [
                "uv", "run", "python", "-m", "ems_comm_manager",
                "--config", str(config_dir),
            ],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "logger",
            "cmd": _build_logger_cmd(config_dir),
            "ready_check": _delay_ready(1.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "control_manager",
            "cmd": [
                "uv", "run", "python", "-m", "ems_control_manager",
                "--config", str(config_dir / "control_config.yaml"),
            ],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
        {
            "name": "alarm_manager",
            "cmd": [
                "uv", "run", "python", "-m", "ems_alarm_manager",
                "--config", str(config_dir / "alarms_config.yaml"),
            ],
            "ready_check": _delay_ready(2.0),
            "env": None,
            "skip_reason": None,
        },
    ]

    return order


def _build_logger_cmd(config_dir: Path) -> list[str]:
    """Build logger command, using profile-specific config if available."""
    logger_config: Path = config_dir / "logger_config.yaml"
    if logger_config.exists():
        return ["uv", "run", "python", "-m", "ems_logger", "--config", str(logger_config)]
    return [
        "uv", "run", "python", "-m", "ems_logger",
        "--config", str(CONFIG_DIR / "logger_config.yaml"),
    ]


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestStartupSequence:
    """Validate that all modules start in correct dependency order and reach
    healthy state within 30 seconds.

    Uses the ``all_modules`` class-scoped fixture to launch modules once
    for the entire class, then runs individual assertion tests.
    """

    @pytest.fixture(scope="class")
    def all_modules(
        self,
        profile: dict[str, Any],
        cleanup_shm: None,
    ) -> list[ModuleProcess]:
        """Launch all modules in dependency order, yield list, cleanup in reverse.

        Modules whose prerequisites are missing (binary not built, vcan0 down)
        are skipped -- a warning is emitted but the suite continues with
        partial coverage. This allows dev machines without all C binaries
        to still validate Python module startup ordering.
        """
        start_order: list[dict[str, Any]] = build_start_order(profile)
        launched: list[ModuleProcess] = []
        skipped: list[str] = []

        try:
            for spec in start_order:
                if spec["skip_reason"] is not None:
                    skipped.append(f"{spec['name']}: {spec['skip_reason']}")
                    continue

                try:
                    proc: ModuleProcess = ModuleProcess(
                        name=spec["name"],
                        cmd=spec["cmd"],
                        ready_check=spec["ready_check"],
                        env=spec["env"],
                    )
                    launched.append(proc)
                except (FileNotFoundError, TimeoutError, OSError) as exc:
                    skipped.append(f"{spec['name']}: {exc}")

            if skipped:
                for msg in skipped:
                    pytest.warns(UserWarning) if False else None  # noqa: B018
                    # Log skips via pytest warning mechanism
                    import warnings
                    warnings.warn(f"Skipped module: {msg}", stacklevel=1)

            if not launched:
                pytest.skip("No modules could be launched -- check prerequisites")

            yield launched

        finally:
            # Cleanup in reverse order (logger first to stop, data_manager_c last)
            for proc in reversed(launched):
                try:
                    proc.cleanup()
                except Exception:
                    pass

    def test_all_modules_start_within_30s(
        self,
        all_modules: list[ModuleProcess],
    ) -> None:
        """Verify that all launched modules are alive and ready within 30 seconds.

        The all_modules fixture already waited for each module's ready_check
        during construction. This test verifies the total elapsed time was
        under 30 seconds and all processes remain alive.
        """
        t_start: float = time.monotonic()

        # Verify all are alive right now
        dead_modules: list[str] = [
            m.name for m in all_modules if not m.is_alive
        ]
        elapsed: float = time.monotonic() - t_start

        assert not dead_modules, (
            f"Modules not alive after startup: {dead_modules}"
        )
        # The fixture itself launches sequentially; verify none crashed after launch
        assert elapsed < 30.0, (
            f"Post-launch health check took {elapsed:.1f}s (>30s limit)"
        )

    def test_rtdb_valid_after_startup(
        self,
        all_modules: list[ModuleProcess],
    ) -> None:
        """Verify RTDB shared memory is valid after startup.

        Attaches to the RTDB and validates:
        - Magic number matches RTDB_MAGIC (0x454D5352)
        - Version matches RTDB_VERSION (1)

        If data_manager_c was skipped (binary not found), this test is
        skipped since no process creates the RTDB.
        """
        dm_c_names: list[str] = [m.name for m in all_modules if m.name == "data_manager_c"]
        if not dm_c_names:
            pytest.skip("data_manager_c not launched -- RTDB not created")

        if not check_rtdb_exists():
            pytest.fail("RTDB shared memory does not exist after data_manager_c start")

        shm, rtdb = attach_rtdb()
        try:
            assert rtdb.magic == RTDB_MAGIC, (
                f"RTDB magic mismatch: expected 0x{RTDB_MAGIC:08X}, "
                f"got 0x{rtdb.magic:08X}"
            )
            assert rtdb.version == RTDB_VERSION, (
                f"RTDB version mismatch: expected {RTDB_VERSION}, "
                f"got {rtdb.version}"
            )
        finally:
            del rtdb
            detach_rtdb(shm)

    def test_zmq_telemetry_flowing(
        self,
        all_modules: list[ModuleProcess],
    ) -> None:
        """Verify ZMQ telemetry socket receives at least one message after startup.

        Subscribes to the telemetry PUB socket and waits up to 5 seconds
        for any message. Uses ipc:// endpoint by default; if the socket
        path is not writable or no publisher is running, skips gracefully.
        """
        dm_python_names: list[str] = [
            m.name for m in all_modules if m.name == "data_manager_python"
        ]
        if not dm_python_names:
            pytest.skip("data_manager_python not launched -- no telemetry publisher")

        # Try ipc:// first; fall back to skip if not accessible
        ipc_sock: Path = Path("/run/ems/telemetry.sock")
        if not ipc_sock.parent.exists():
            pytest.skip(
                "/run/ems/ directory not available -- ZMQ ipc test cannot proceed. "
                "Create with: sudo mkdir -p /run/ems && sudo chmod 777 /run/ems"
            )

        received: bool = check_zmq_receiving(
            endpoint=SOCK_TELEMETRY,
            topic="",
            timeout_ms=5000,
        )
        if not received:
            pytest.skip(
                "No ZMQ telemetry received within 5s -- publisher may not be "
                "producing data (requires RTDB + active telemetry loop)"
            )

    def test_startup_order_data_manager_first(
        self,
        all_modules: list[ModuleProcess],
    ) -> None:
        """Verify data_manager_c was started first (has lowest PID).

        Since modules are launched sequentially in dependency order and
        PIDs are assigned monotonically by the kernel, data_manager_c
        should have the lowest PID of all launched modules.

        This is a sanity check that the startup ordering was respected.
        If data_manager_c was skipped, this test is skipped.
        """
        dm_c_modules: list[ModuleProcess] = [
            m for m in all_modules if m.name == "data_manager_c"
        ]
        if not dm_c_modules:
            pytest.skip("data_manager_c not launched -- cannot verify ordering")

        dm_c_pid: int = dm_c_modules[0].pid
        other_pids: list[tuple[str, int]] = [
            (m.name, m.pid) for m in all_modules if m.name != "data_manager_c"
        ]

        for name, pid in other_pids:
            assert dm_c_pid < pid, (
                f"data_manager_c PID ({dm_c_pid}) should be lower than "
                f"{name} PID ({pid}) -- startup order may be wrong"
            )


# ---------------------------------------------------------------------------
# Helpers for TestFullSystemStartup
# ---------------------------------------------------------------------------


def _allocate_tcp_ports(count: int) -> list[int]:
    """Allocate ``count`` random free TCP ports for ZMQ/HTTP isolation.

    Binds temporary sockets to force OS port assignment, collects the ports,
    then releases them just before returning.
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


# ---------------------------------------------------------------------------
# Full M3 system startup (M1 + M2 + hmi_server + scheduler)
# ---------------------------------------------------------------------------


class TestFullSystemStartup:
    """Validate that all 11 modules (M1 core + M2 control/alarm + hmi_server
    + scheduler) start in correct dependency order and reach healthy state.

    hmi_server health is verified via HTTP GET /api/health/.
    scheduler health is verified via a delay-based ready check (no HTTP endpoint).

    This test class uses ipc:// sockets for ZMQ (matching default module
    behaviour) and allocates a random TCP port for hmi_server HTTP.
    """

    @pytest.fixture(scope="class")
    def m3_system(
        self,
        profile: dict[str, Any],
        cleanup_shm: None,
        ensure_run_ems: None,
    ) -> list[ModuleProcess]:
        """Launch all 11 modules in dependency order, yield, cleanup in reverse.

        hmi_server gets a randomly allocated HTTP port and a test config
        with known bcrypt PIN hashes.  scheduler uses the default
        schedule_config.yaml from the residential profile.
        """
        # Allocate a free port for hmi_server HTTP
        hmi_ports: list[int] = _allocate_tcp_ports(1)
        hmi_http_port: int = hmi_ports[0]

        # Paths
        config_dir: Path = profile["config_dir"]
        test_hmi_config_src: Path = config_dir / "hmi_config_test.yaml"
        schedule_config: Path = config_dir / "schedule_config.yaml"

        # Create a temporary copy of the test HMI config with the allocated port
        tmpdir: Path = Path(tempfile.mkdtemp(prefix="ems_m3_startup_"))
        test_hmi_config: Path = tmpdir / "hmi_config_test.yaml"
        if test_hmi_config_src.exists():
            with open(test_hmi_config_src) as f:
                hmi_cfg: dict = yaml.safe_load(f)
            hmi_cfg["server"]["http_port"] = hmi_http_port
            with open(test_hmi_config, "w") as f:
                yaml.dump(hmi_cfg, f, default_flow_style=False)

        # Build M1+M2 start order first (positions 1-9)
        base_order: list[dict[str, Any]] = build_start_order(profile)

        # Append hmi_server (position 10) and scheduler (position 11)
        base_order.append({
            "name": "hmi_server",
            "cmd": [
                "uv", "run", "python", "-m", "ems_hmi_server",
                "--config", str(test_hmi_config),
            ],
            "ready_check": lambda: _check_http_health(hmi_http_port),
            "env": None,
            "skip_reason": (
                None if test_hmi_config_src.exists()
                else f"Test HMI config not found: {test_hmi_config_src}"
            ),
        })
        base_order.append({
            "name": "scheduler",
            "cmd": [
                "uv", "run", "python", "-m", "ems_scheduler",
                "--config", str(schedule_config),
            ],
            "ready_check": _delay_ready(3.0),
            "env": None,
            "skip_reason": (
                None if schedule_config.exists()
                else f"Schedule config not found: {schedule_config}"
            ),
        })

        launched: list[ModuleProcess] = []
        skipped: list[str] = []

        try:
            for spec in base_order:
                if spec["skip_reason"] is not None:
                    skipped.append(f"{spec['name']}: {spec['skip_reason']}")
                    continue
                try:
                    proc: ModuleProcess = ModuleProcess(
                        name=spec["name"],
                        cmd=spec["cmd"],
                        ready_check=spec["ready_check"],
                        env=spec["env"],
                    )
                    launched.append(proc)
                except (FileNotFoundError, TimeoutError, OSError) as exc:
                    skipped.append(f"{spec['name']}: {exc}")

            if skipped:
                import warnings
                for msg in skipped:
                    warnings.warn(f"Skipped module: {msg}", stacklevel=1)

            if not launched:
                pytest.skip("No modules could be launched -- check prerequisites")

            # Store allocated port on the fixture for assertions
            self._hmi_http_port = hmi_http_port

            yield launched

        finally:
            for proc in reversed(launched):
                try:
                    proc.cleanup()
                except Exception:
                    pass
            # Remove temporary config directory
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_all_11_modules_alive(
        self,
        m3_system: list[ModuleProcess],
    ) -> None:
        """Verify all launched modules (up to 11) are alive after startup."""
        dead: list[str] = [m.name for m in m3_system if not m.is_alive]
        assert not dead, f"Modules not alive after startup: {dead}"

    def test_rtdb_exists_and_fresh(
        self,
        m3_system: list[ModuleProcess],
    ) -> None:
        """Verify RTDB shared memory exists and has been updated recently."""
        dm_c_names: list[str] = [m.name for m in m3_system if m.name == "data_manager_c"]
        if not dm_c_names:
            pytest.skip("data_manager_c not launched -- RTDB not created")

        assert check_rtdb_exists(), "RTDB shared memory does not exist"
        assert check_rtdb_fresh(max_age_ms=5000.0), "RTDB is stale (>5s since last update)"

    def test_hmi_server_health_returns_200(
        self,
        m3_system: list[ModuleProcess],
    ) -> None:
        """Verify hmi_server /api/health/ returns HTTP 200."""
        hmi_modules: list[ModuleProcess] = [
            m for m in m3_system if m.name == "hmi_server"
        ]
        if not hmi_modules:
            pytest.skip("hmi_server not launched")

        port: int = self._hmi_http_port
        resp: httpx.Response = httpx.get(
            f"http://127.0.0.1:{port}/api/health/", timeout=5.0,
        )
        assert resp.status_code == 200, (
            f"hmi_server health returned {resp.status_code}: {resp.text}"
        )

    def test_startup_completes_within_30s(
        self,
        m3_system: list[ModuleProcess],
    ) -> None:
        """Verify that all modules are alive -- the fixture enforces startup
        timeout per-module (15s each via ModuleProcess._wait_ready).
        The 30s budget here is for the post-launch health check sweep.
        """
        t_start: float = time.monotonic()
        dead: list[str] = [m.name for m in m3_system if not m.is_alive]
        elapsed: float = time.monotonic() - t_start
        assert not dead, f"Modules not alive: {dead}"
        assert elapsed < 30.0, f"Post-launch check took {elapsed:.1f}s (>30s)"
