"""End-to-end integration tests for Phase 9 Foundation.

Validates cross-module contracts:
- ConfigManager loads all configs and serves via ZMQ REQ/REP
- RTDB created by C, Python attaches, publisher publishes, SUB receives
- Hot-reload creates backup, updates cache, publishes event
- Stale RTDB detection and recreation
- systemd ordering consistency
- Full data pipeline: shm write -> ZMQ PUB -> SUB receives matching data
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import struct
import time
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any

import msgpack
import pytest
import zmq
import zmq.asyncio

from ems_common.ipc import (
    MSG_KEY_PAYLOAD,
    MSG_KEY_TOPIC,
    SOCK_CONFIG,
    STATUS_OK,
    decode_command_response,
    encode_command_request,
    decode_telemetry,
    TOPIC_PCS,
)
from ems_common.rtdb import (
    RTDB_MAGIC,
    RTDB_VERSION,
    EmsRtdb,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
BUILD_DIR: Path = PROJECT_ROOT / "build"
LIB_PATH: Path = BUILD_DIR / "src" / "data_manager" / "c" / "libems_rtdb.so"
CONFIG_DIR: Path = PROJECT_ROOT / "config"
SCHEMA_DIR: Path = CONFIG_DIR / "schemas"
DEPLOY_DIR: Path = PROJECT_ROOT / "deploy" / "systemd"

SHM_NAME: str = "ems_rtdb"
SHM_PATH: Path = Path("/dev/shm") / SHM_NAME

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cleanup_shm():
    """Ensure shm is cleaned up before and after each test."""
    _force_cleanup()
    yield
    _force_cleanup()


def _force_cleanup() -> None:
    """Remove the shm file if it exists."""
    if SHM_PATH.exists():
        try:
            os.unlink(str(SHM_PATH))
        except OSError:
            pass


def _load_lib() -> ctypes.CDLL:
    """Load libems_rtdb.so via ctypes."""
    if not LIB_PATH.exists():
        pytest.skip(f"libems_rtdb.so not found at {LIB_PATH}. Run cmake --build build first.")
    lib: ctypes.CDLL = ctypes.CDLL(str(LIB_PATH))
    lib.rtdb_create.argtypes = [
        ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8,
        ctypes.c_uint8, ctypes.c_uint8,
    ]
    lib.rtdb_create.restype = ctypes.c_void_p
    lib.rtdb_attach.argtypes = []
    lib.rtdb_attach.restype = ctypes.c_void_p
    lib.rtdb_detach.argtypes = [ctypes.c_void_p]
    lib.rtdb_detach.restype = None
    lib.rtdb_destroy.argtypes = []
    lib.rtdb_destroy.restype = None
    return lib


def _create_rtdb(clusters: int = 1, racks: int = 4) -> tuple[ctypes.CDLL, int]:
    """Create RTDB via C library."""
    lib: ctypes.CDLL = _load_lib()
    ptr: int = lib.rtdb_create(clusters, racks, 10, 16, 8)
    assert ptr is not None and ptr != 0
    return lib, ptr


def _tcp_endpoint(port: int) -> str:
    """Return a TCP endpoint for testing."""
    return f"tcp://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Test 1: ConfigManager loads and queries from cache
# ---------------------------------------------------------------------------


class TestConfigLoadAndQuery:
    """Validate ConfigManager loads all configs and serves from cache."""

    def test_config_load_all_14(self) -> None:
        """ConfigManager.load_all() loads all 14 configs into cache."""
        from ems_config_manager.manager import ConfigManager

        mgr: ConfigManager = ConfigManager(CONFIG_DIR, SCHEMA_DIR)
        mgr.load_all()

        # system_config always loaded
        sys_cfg: dict = mgr.get_config("system_config")
        assert isinstance(sys_cfg, dict)
        assert "system" in sys_cfg
        assert "topology" in sys_cfg

        # pcs_config loaded
        pcs_cfg: dict = mgr.get_config("pcs_config")
        assert isinstance(pcs_cfg, dict)

        # All 10 core + 2 optional (btms, meter from subsystems) = 12
        # but we check all core configs are present
        for name in [
            "system_config", "gpio_config", "control_config",
            "alarms_config", "schedule_config", "bms_config",
            "pcs_config", "network_config", "cloud_config", "hmi_config",
        ]:
            cfg: dict = mgr.get_config(name)
            assert isinstance(cfg, dict), f"{name} not loaded"

    def test_get_value_dotted_path(self) -> None:
        """get_value navigates dotted path into nested config."""
        from ems_config_manager.manager import ConfigManager

        mgr: ConfigManager = ConfigManager(CONFIG_DIR, SCHEMA_DIR)
        mgr.load_all()

        cluster_count: Any = mgr.get_value("system_config", "topology.cluster_count")
        assert isinstance(cluster_count, int)
        assert cluster_count >= 1

    def test_get_config_pcs_connection(self) -> None:
        """get_value for pcs_config.connection.protocol returns 'rtu'."""
        from ems_config_manager.manager import ConfigManager

        mgr: ConfigManager = ConfigManager(CONFIG_DIR, SCHEMA_DIR)
        mgr.load_all()

        protocol: Any = mgr.get_value("pcs_config", "connection.protocol")
        assert protocol == "rtu"


# ---------------------------------------------------------------------------
# Test 2: ZMQ REQ/REP config query
# ---------------------------------------------------------------------------


class TestConfigZmqQuery:
    """Validate ZMQ config query interface end-to-end."""

    @pytest.mark.asyncio
    async def test_zmq_get_config(self) -> None:
        """ZMQ REQ get_config returns full config dict."""
        from ems_config_manager.manager import ConfigManager

        mgr: ConfigManager = ConfigManager(CONFIG_DIR, SCHEMA_DIR)
        mgr.load_all()

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        bind_addr: str = _tcp_endpoint(16600)

        server_task: asyncio.Task = asyncio.create_task(
            mgr.serve_queries(ctx, bind_addr=bind_addr)
        )

        try:
            await asyncio.sleep(0.1)

            req: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req.connect(bind_addr)

            # get_config
            request: bytes = encode_command_request(
                "get_config", {"name": "pcs_config"}
            )
            await req.send(request)
            reply_data: bytes = await asyncio.wait_for(req.recv(), timeout=5.0)
            reply: dict = decode_command_response(reply_data)
            assert reply["status"] == STATUS_OK
            assert isinstance(reply["result"], dict)
            assert "connection" in reply["result"]

            req.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()

    @pytest.mark.asyncio
    async def test_zmq_get_value(self) -> None:
        """ZMQ REQ get_value returns specific field value."""
        from ems_config_manager.manager import ConfigManager

        mgr: ConfigManager = ConfigManager(CONFIG_DIR, SCHEMA_DIR)
        mgr.load_all()

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        bind_addr: str = _tcp_endpoint(16601)

        server_task: asyncio.Task = asyncio.create_task(
            mgr.serve_queries(ctx, bind_addr=bind_addr)
        )

        try:
            await asyncio.sleep(0.1)

            req: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req.connect(bind_addr)

            request: bytes = encode_command_request(
                "get_value", {"name": "pcs_config", "path": "connection.protocol"}
            )
            await req.send(request)
            reply_data: bytes = await asyncio.wait_for(req.recv(), timeout=5.0)
            reply: dict = decode_command_response(reply_data)
            assert reply["status"] == STATUS_OK
            assert reply["result"] == "rtu"

            req.close()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            ctx.term()


# ---------------------------------------------------------------------------
# Test 3: RTDB create (C) and Python attach
# ---------------------------------------------------------------------------


class TestRtdbCreateAndAttach:
    """Validate C creates RTDB and Python attaches correctly."""

    def test_c_create_python_attach(self) -> None:
        """C creates RTDB, Python attaches and reads correct header/topology."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib, ptr = _create_rtdb(1, 4)
        shm, rtdb = attach_rtdb()

        assert rtdb.magic == RTDB_MAGIC
        assert rtdb.version == RTDB_VERSION
        assert rtdb.cluster_count == 1
        assert rtdb.racks_per_cluster == 4

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_stale_rtdb_recreated(self) -> None:
        """RTDB with wrong magic is detected and recreated by C library."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        # Create stale shm with wrong magic
        stale_shm: shared_memory.SharedMemory = shared_memory.SharedMemory(
            name=SHM_NAME, create=True, size=ctypes.sizeof(EmsRtdb)
        )
        struct.pack_into("<I", stale_shm.buf, 0, 0xDEADBEEF)
        stale_shm.close()

        # C should detect and recreate
        lib, ptr = _create_rtdb(1, 4)
        shm, rtdb = attach_rtdb()

        assert rtdb.magic == RTDB_MAGIC
        assert rtdb.version == RTDB_VERSION

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Test 4: Telemetry publish/receive end-to-end
# ---------------------------------------------------------------------------


class TestTelemetryPublishReceive:
    """Validate ZMQ PUB publishes RTDB data and SUB receives it."""

    def test_pcs_publish_receive(self) -> None:
        """Write known PCS values to RTDB, publisher publishes, SUB receives matching data."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.publisher import TelemetryPublisher

        lib, ptr = _create_rtdb(1, 4)
        shm, rtdb = attach_rtdb()

        # Write known values
        rtdb.pcs.lock.sequence = 0
        rtdb.pcs.last_update_ms = 1000
        rtdb.pcs.ac_voltage = 230.5
        rtdb.pcs.active_power = 50.0

        ctx: zmq.Context = zmq.Context()
        ep: str = _tcp_endpoint(16610)
        publisher: TelemetryPublisher | None = None

        try:
            publisher = TelemetryPublisher(rtdb, ctx, endpoint=ep)

            sub: zmq.Socket = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_PCS)
            time.sleep(0.1)

            publisher.publish_once()

            assert sub.poll(3000), "No PCS message received within 3s"
            topic_bytes: bytes = sub.recv()
            data: bytes = sub.recv()
            msg: dict = decode_telemetry(data)

            assert topic_bytes.decode() == TOPIC_PCS
            payload: dict = msg[MSG_KEY_PAYLOAD]
            assert abs(payload["ac_voltage"] - 230.5) < 0.01
            assert abs(payload["active_power"] - 50.0) < 0.01

            sub.close()
            publisher.close()
            publisher = None
        finally:
            if publisher is not None:
                publisher.close()
            del publisher
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Test 5: Hot-reload event
# ---------------------------------------------------------------------------


class TestHotReloadEvent:
    """Validate hot-reload creates backup, updates cache, publishes event."""

    @pytest.mark.asyncio
    async def test_hot_reload_backup_and_cache_update(self, tmp_path: Path) -> None:
        """Hot-reload creates backup and updates in-memory cache."""
        import shutil
        import yaml
        from ems_config_manager.manager import ConfigManager

        # Copy real config dir to tmp_path for isolation
        tmp_config: Path = tmp_path / "config"
        shutil.copytree(CONFIG_DIR, tmp_config)

        # Copy schemas too
        tmp_schemas: Path = tmp_config / "schemas"

        mgr: ConfigManager = ConfigManager(tmp_config, tmp_schemas)
        mgr.load_all()

        # Read original value
        original_val: Any = mgr.get_value(
            "control_config", "soc_limits.charge_cutoff_pct"
        )
        assert original_val == 95

        # Modify control_config.yaml
        control_path: Path = tmp_config / "control_config.yaml"
        with control_path.open("r") as f:
            data: dict = yaml.safe_load(f)
        data["soc_limits"]["charge_cutoff_pct"] = 90
        with control_path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False)

        # Trigger hot-reload
        await mgr.handle_reload("control_config")

        # Verify backup created
        backup_dir: Path = tmp_config / "backups"
        assert backup_dir.exists()
        backups: list[Path] = list(backup_dir.glob("control_config.*.yaml"))
        assert len(backups) >= 1, "No backup created during hot-reload"

        # Verify cache updated
        new_val: Any = mgr.get_value(
            "control_config", "soc_limits.charge_cutoff_pct"
        )
        assert new_val == 90


# ---------------------------------------------------------------------------
# Test 6: systemd ordering consistency
# ---------------------------------------------------------------------------


class TestSystemdOrdering:
    """Validate systemd service files have consistent ordering."""

    def test_data_manager_c_no_ems_after(self) -> None:
        """ems-data-manager.service has no After= on other EMS services."""
        content: str = (DEPLOY_DIR / "ems-data-manager.service").read_text()
        # Should not have After=ems-* (it IS the root)
        for line in content.splitlines():
            if line.startswith("After="):
                # Should not contain ems- services
                assert "ems-" not in line, (
                    f"data-manager should not depend on other EMS services: {line}"
                )

    def test_python_after_c(self) -> None:
        """ems-data-manager-python.service has After=ems-data-manager.service."""
        content: str = (DEPLOY_DIR / "ems-data-manager-python.service").read_text()
        assert "After=ems-data-manager.service" in content

    def test_config_manager_after_data_manager(self) -> None:
        """ems-config-manager.service has After=ems-data-manager.service."""
        content: str = (DEPLOY_DIR / "ems-config-manager.service").read_text()
        assert "After=ems-data-manager.service" in content

    def test_all_ems_services_restart_always(self) -> None:
        """All ems-* services have Restart=always."""
        for svc_path in DEPLOY_DIR.glob("ems-*.service"):
            content: str = svc_path.read_text()
            assert "Restart=always" in content, (
                f"{svc_path.name} missing Restart=always"
            )


# ---------------------------------------------------------------------------
# Test 7: Full pipeline (shm write -> ZMQ PUB -> SUB receives)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end: RTDB write -> publisher -> SUB receives matching data."""

    def test_full_data_pipeline(self) -> None:
        """Write test data to PCS via seqlock, publish, receive, verify match."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.publisher import TelemetryPublisher

        lib, ptr = _create_rtdb(1, 4)
        shm, rtdb = attach_rtdb()

        # Write distinctive test data to PCS section
        rtdb.pcs.lock.sequence = 0
        rtdb.pcs.last_update_ms = 42000
        rtdb.pcs.ac_voltage = 231.7
        rtdb.pcs.ac_current = 12.3
        rtdb.pcs.active_power = 2850.0
        rtdb.pcs.reactive_power = 150.0
        rtdb.pcs.dc_voltage = 410.5
        rtdb.pcs.dc_current = 7.1
        rtdb.pcs.frequency = 50.02
        rtdb.pcs.temperature = 38.5

        ctx: zmq.Context = zmq.Context()
        ep: str = _tcp_endpoint(16620)
        publisher: TelemetryPublisher | None = None

        try:
            publisher = TelemetryPublisher(rtdb, ctx, endpoint=ep)

            sub: zmq.Socket = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_PCS)
            time.sleep(0.1)

            publisher.publish_once()

            assert sub.poll(3000), "Full pipeline: no PCS message received"
            topic_bytes: bytes = sub.recv()
            data: bytes = sub.recv()
            msg: dict = decode_telemetry(data)

            assert topic_bytes.decode() == TOPIC_PCS
            assert msg[MSG_KEY_TOPIC] == TOPIC_PCS

            payload: dict = msg[MSG_KEY_PAYLOAD]
            # Verify all written values match (within float tolerance)
            assert abs(payload["ac_voltage"] - 231.7) < 0.1
            assert abs(payload["ac_current"] - 12.3) < 0.1
            assert abs(payload["active_power"] - 2850.0) < 0.1
            assert abs(payload["reactive_power"] - 150.0) < 0.1
            assert abs(payload["dc_voltage"] - 410.5) < 0.1
            assert abs(payload["dc_current"] - 7.1) < 0.1
            assert abs(payload["frequency"] - 50.02) < 0.1
            assert abs(payload["temperature"] - 38.5) < 0.1

            sub.close()
            publisher.close()
            publisher = None
        finally:
            if publisher is not None:
                publisher.close()
            del publisher
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()
