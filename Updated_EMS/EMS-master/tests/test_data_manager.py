"""Tests for data_manager: RTDB lifecycle, ZMQ publisher, health monitor, snapshots.

Covers DATA-01 through DATA-08:
- C creates shm via libems_rtdb.so (loaded with ctypes)
- Python attaches/detaches with resource_tracker fix
- Topology validation
- Stale detection
- Round-trip C write / Python read
- 1Hz ZMQ PUB telemetry publisher
- Health monitor for stale section detection
- Periodic RTDB snapshot to disk
"""

import asyncio
import ctypes
import os
import struct  # noqa: F401 -- used in stale detection test
import time
from multiprocessing import shared_memory
from pathlib import Path

import msgpack
import pytest
import zmq
import zmq.asyncio

from ems_common.ipc import (
    MSG_KEY_PAYLOAD,
    MSG_KEY_SEQUENCE,
    MSG_KEY_SOURCE,
    MSG_KEY_TIMESTAMP,
    MSG_KEY_TOPIC,
    SOCK_TELEMETRY,
    TOPIC_BTMS,
    TOPIC_GPIO,
    TOPIC_METER,
    TOPIC_PCS,
    TOPIC_SYSTEM,
)
from ems_common.rtdb import (
    RTDB_MAGIC,
    RTDB_VERSION,
    EmsRtdb,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BUILD_DIR = Path(__file__).resolve().parent.parent / "build"
LIB_PATH = BUILD_DIR / "src" / "data_manager" / "c" / "libems_rtdb.so"

SHM_NAME = "ems_rtdb"
SHM_PATH = Path("/dev/shm") / SHM_NAME


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
    lib = ctypes.CDLL(str(LIB_PATH))

    # rtdb_create(uint8, uint8, uint8, uint8, uint8) -> ems_rtdb_t*
    lib.rtdb_create.argtypes = [
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
    ]
    lib.rtdb_create.restype = ctypes.c_void_p

    # rtdb_attach() -> ems_rtdb_t*
    lib.rtdb_attach.argtypes = []
    lib.rtdb_attach.restype = ctypes.c_void_p

    # rtdb_detach(ems_rtdb_t*)
    lib.rtdb_detach.argtypes = [ctypes.c_void_p]
    lib.rtdb_detach.restype = None

    # rtdb_destroy()
    lib.rtdb_destroy.argtypes = []
    lib.rtdb_destroy.restype = None

    return lib


def _create_rtdb_via_c(clusters: int = 1, racks: int = 4) -> tuple[ctypes.CDLL, int]:
    """Create RTDB via C library, return (lib, ptr)."""
    lib = _load_lib()
    ptr = lib.rtdb_create(clusters, racks, 10, 16, 8)
    assert ptr is not None and ptr != 0
    return lib, ptr


# ---------------------------------------------------------------------------
# Tests: C library via ctypes
# ---------------------------------------------------------------------------


class TestCLibrary:
    """Test the C library directly via ctypes."""

    def test_shm_create(self) -> None:
        """rtdb_create produces /dev/shm/ems_rtdb with correct size."""
        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        assert SHM_PATH.exists()
        assert SHM_PATH.stat().st_size == ctypes.sizeof(EmsRtdb)

        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_rtdb_init_header(self) -> None:
        """Created RTDB has correct magic, version, and topology."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib = _load_lib()
        ptr = lib.rtdb_create(2, 8, 12, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()

        assert rtdb.magic == RTDB_MAGIC
        assert rtdb.version == RTDB_VERSION
        assert rtdb.cluster_count == 2
        assert rtdb.racks_per_cluster == 8
        assert rtdb.modules_per_rack == 12
        assert rtdb.cells_per_module == 16
        assert rtdb.temps_per_module == 8

        del rtdb  # Release ctypes reference before closing shm
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_stale_detection(self) -> None:
        """rtdb_create detects stale shm (wrong magic) and recreates."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        # Manually create a stale shm with wrong magic
        shm = shared_memory.SharedMemory(name=SHM_NAME, create=True, size=ctypes.sizeof(EmsRtdb))
        # Write wrong magic at offset 0
        struct.pack_into("<I", shm.buf, 0, 0xDEADBEEF)
        shm.close()

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        # Verify it was recreated with correct magic
        shm2, rtdb = attach_rtdb()
        assert rtdb.magic == RTDB_MAGIC
        assert rtdb.version == RTDB_VERSION

        del rtdb
        detach_rtdb(shm2)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_detach_no_unlink(self) -> None:
        """After rtdb_detach, shm still exists in /dev/shm."""
        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        lib.rtdb_detach(ptr)

        # shm should still exist
        assert SHM_PATH.exists()

        lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Tests: Python attach/detach helpers
# ---------------------------------------------------------------------------


class TestPythonHelpers:
    """Test Python attach_rtdb / detach_rtdb / validate_topology."""

    def test_attach_returns_correct_header(self) -> None:
        """attach_rtdb() returns (shm, rtdb) with correct magic and version."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        assert rtdb.magic == RTDB_MAGIC
        assert rtdb.version == RTDB_VERSION

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_attach_resource_tracker_unregister(self) -> None:
        """attach_rtdb() calls resource_tracker.unregister to prevent premature cleanup."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        del rtdb
        detach_rtdb(shm)

        # After Python detach, shm should still exist (resource_tracker didn't unlink it)
        assert SHM_PATH.exists()

        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_detach_no_unlink(self) -> None:
        """detach_rtdb() closes shm without unlinking."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        del rtdb
        detach_rtdb(shm)

        # shm still accessible from C
        attached = lib.rtdb_attach()
        assert attached is not None and attached != 0
        lib.rtdb_detach(attached)

        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_python_reads_topology_from_c(self) -> None:
        """Python reads topology counts written by C."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb

        lib = _load_lib()
        ptr = lib.rtdb_create(3, 12, 15, 64, 20)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        assert rtdb.cluster_count == 3
        assert rtdb.racks_per_cluster == 12
        assert rtdb.modules_per_rack == 15
        assert rtdb.cells_per_module == 64
        assert rtdb.temps_per_module == 20

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_validate_topology_ok(self) -> None:
        """validate_topology() passes with matching counts."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb, validate_topology

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        # Should not raise
        validate_topology(rtdb, {
            "cluster_count": 1,
            "racks_per_cluster": 4,
            "modules_per_rack": 10,
            "cells_per_module": 16,
            "temps_per_module": 8,
        })

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_validate_topology_mismatch(self) -> None:
        """validate_topology() raises ValueError on mismatch."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb, validate_topology

        lib = _load_lib()
        ptr = lib.rtdb_create(1, 4, 10, 16, 8)
        assert ptr is not None and ptr != 0

        shm, rtdb = attach_rtdb()
        with pytest.raises(ValueError, match="cluster_count"):
            validate_topology(rtdb, {
                "cluster_count": 99,  # Wrong
                "racks_per_cluster": 4,
                "modules_per_rack": 10,
                "cells_per_module": 16,
                "temps_per_module": 8,
            })

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Tests: ZMQ Telemetry Publisher (DATA-05, DATA-06)
# ---------------------------------------------------------------------------


class TestTelemetryPublisher:
    """Test TelemetryPublisher ZMQ PUB behavior."""

    @staticmethod
    def _tcp_endpoint(port: int) -> str:
        return f"tcp://127.0.0.1:{port}"

    def test_pub_pcs_section(self) -> None:
        """Publisher reads PCS section via seqlock and publishes correct topic+envelope."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.publisher import TelemetryPublisher

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        # Write test data to PCS section
        rtdb.pcs.lock.sequence = 0  # Even = no write in progress
        rtdb.pcs.last_update_ms = 1000
        rtdb.pcs.ac_voltage = 230.5
        rtdb.pcs.active_power = 50.0
        rtdb.pcs.dc_voltage = 400.0

        ctx = zmq.Context()
        ep = self._tcp_endpoint(15550)
        try:
            publisher = TelemetryPublisher(rtdb, ctx, endpoint=ep)

            # Subscribe to PCS topic
            sub = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_PCS)
            time.sleep(0.1)  # Allow connection

            # Publish one cycle
            publisher.publish_once()

            # Receive with timeout
            if sub.poll(2000):
                topic_bytes = sub.recv()
                data = sub.recv()
                msg = msgpack.unpackb(data, raw=False)

                assert topic_bytes.decode() == TOPIC_PCS
                assert MSG_KEY_TIMESTAMP in msg
                assert MSG_KEY_SEQUENCE in msg
                assert MSG_KEY_SOURCE in msg
                assert msg[MSG_KEY_SOURCE] == "data_manager"
                assert msg[MSG_KEY_TOPIC] == TOPIC_PCS
                payload = msg[MSG_KEY_PAYLOAD]
                assert abs(payload["ac_voltage"] - 230.5) < 0.01
                assert abs(payload["active_power"] - 50.0) < 0.01
                assert abs(payload["dc_voltage"] - 400.0) < 0.01
            else:
                pytest.fail("No message received from publisher within timeout")

            sub.close()
            publisher.close()
        finally:
            del publisher  # Release rtdb reference held by publisher
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()

    def test_pub_all_sections(self) -> None:
        """Publisher publishes all sections (bms racks, pcs, gpio, meter, btms, system) at 1Hz."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.publisher import TelemetryPublisher

        lib, ptr = _create_rtdb_via_c(1, 2)
        shm, rtdb = attach_rtdb()

        # Set last_update_ms on all sections so they get published
        rtdb.pcs.last_update_ms = 1000
        rtdb.gpio.last_update_ms = 1000
        rtdb.meter.last_update_ms = 1000
        rtdb.btms.last_update_ms = 1000
        rtdb.system.last_update_ms = 1000
        # Set BMS rack data
        for c in range(1):
            for r in range(2):
                rtdb.clusters[c].racks[r].last_update_ms = 1000

        ctx = zmq.Context()
        ep = self._tcp_endpoint(15551)
        try:
            publisher = TelemetryPublisher(rtdb, ctx, endpoint=ep)

            sub = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to all
            time.sleep(0.1)

            publisher.publish_once()

            received_topics: set[str] = set()
            # Expect: 2 BMS racks + pcs + gpio + meter + btms + system = 7 messages
            for _ in range(7):
                if sub.poll(2000):
                    topic_bytes = sub.recv()
                    _data = sub.recv()
                    received_topics.add(topic_bytes.decode())
                else:
                    break

            assert TOPIC_PCS in received_topics
            assert TOPIC_GPIO in received_topics
            assert TOPIC_METER in received_topics
            assert TOPIC_BTMS in received_topics
            assert TOPIC_SYSTEM in received_topics
            assert "bms.rack.0.0" in received_topics
            assert "bms.rack.0.1" in received_topics

            sub.close()
            publisher.close()
        finally:
            del publisher
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()

    def test_pub_bms_rack_topic_includes_indices(self) -> None:
        """BMS rack topics include cluster/rack index (e.g., 'bms.rack.0.3')."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.publisher import TelemetryPublisher

        lib, ptr = _create_rtdb_via_c(2, 3)
        shm, rtdb = attach_rtdb()

        # Mark all racks as having data
        for c in range(2):
            for r in range(3):
                rtdb.clusters[c].racks[r].last_update_ms = 1000

        ctx = zmq.Context()
        ep = self._tcp_endpoint(15552)
        try:
            publisher = TelemetryPublisher(rtdb, ctx, endpoint=ep)

            sub = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "bms.rack")
            time.sleep(0.1)

            publisher.publish_once()

            bms_topics: set[str] = set()
            for _ in range(6):  # 2 clusters * 3 racks
                if sub.poll(2000):
                    topic_bytes = sub.recv()
                    _data = sub.recv()
                    bms_topics.add(topic_bytes.decode())

            expected = {"bms.rack.0.0", "bms.rack.0.1", "bms.rack.0.2",
                        "bms.rack.1.0", "bms.rack.1.1", "bms.rack.1.2"}
            assert bms_topics == expected

            sub.close()
            publisher.close()
        finally:
            del publisher
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()

    def test_pub_uses_memmove_for_section_copy(self) -> None:
        """Publisher uses ctypes.memmove for section copy inside seqlock read."""
        from ems_data_manager.publisher import _seqlock_read_section

        lib, ptr = _create_rtdb_via_c(1, 4)
        from ems_common.rtdb import EmsPcs, attach_rtdb, detach_rtdb
        shm, rtdb = attach_rtdb()

        rtdb.pcs.lock.sequence = 0
        rtdb.pcs.ac_voltage = 123.456

        # _seqlock_read_section should return a copy via memmove
        copy = _seqlock_read_section(rtdb.pcs)
        assert isinstance(copy, EmsPcs)
        assert abs(copy.ac_voltage - 123.456) < 0.001

        # Modify original -- copy should be unaffected
        rtdb.pcs.ac_voltage = 999.0
        assert abs(copy.ac_voltage - 123.456) < 0.001

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Tests: Health Monitor (DATA-06)
# ---------------------------------------------------------------------------


class TestHealthMonitor:
    """Test HealthMonitor stale section detection."""

    @staticmethod
    def _tcp_endpoint(port: int) -> str:
        return f"tcp://127.0.0.1:{port}"

    def test_health_detects_stale_section(self) -> None:
        """Health monitor detects section with last_update_ms older than threshold."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.health import HealthMonitor

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        # Set PCS last_update_ms to a very old value (1ms -- long ago)
        rtdb.pcs.last_update_ms = 1

        ctx = zmq.Context()
        ep = self._tcp_endpoint(15560)
        try:
            pub = ctx.socket(zmq.PUB)
            pub.bind(ep)
            monitor = HealthMonitor(rtdb, pub, stale_threshold_ms=5000)

            sub = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "system.health")
            time.sleep(0.1)

            # Run one monitor cycle
            monitor.check_once()

            # Should receive stale warning
            if sub.poll(2000):
                topic_bytes = sub.recv()
                data = sub.recv()
                msg = msgpack.unpackb(data, raw=False)
                assert topic_bytes.decode() == "system.health"
                assert "stale" in msg.get("message", "").lower() or "stale" in msg.get("event_type", "").lower()
            else:
                pytest.fail("No stale warning received from health monitor")

            sub.close()
            monitor.close()
            pub.close()
        finally:
            del monitor
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()

    def test_health_no_warn_for_zero_last_update(self) -> None:
        """Health monitor does NOT warn for sections with last_update_ms == 0 (no data yet)."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.health import HealthMonitor

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        # RTDB is zero-filled on creation -- last_update_ms is 0 for all sections
        assert rtdb.pcs.last_update_ms == 0
        assert rtdb.gpio.last_update_ms == 0

        ctx = zmq.Context()
        ep = self._tcp_endpoint(15561)
        try:
            pub = ctx.socket(zmq.PUB)
            pub.bind(ep)
            monitor = HealthMonitor(rtdb, pub, stale_threshold_ms=5000)

            sub = ctx.socket(zmq.SUB)
            sub.connect(ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, "system.health")
            time.sleep(0.1)

            monitor.check_once()

            # Should NOT receive any warning
            if sub.poll(500):
                topic_bytes = sub.recv()
                _data = sub.recv()
                pytest.fail(f"Received unexpected health warning for zero last_update_ms: {topic_bytes}")

            sub.close()
            monitor.close()
            pub.close()
        finally:
            del monitor
            ctx.term()
            del rtdb
            detach_rtdb(shm)
            lib.rtdb_detach(ptr)
            lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Tests: Snapshot (DATA-08)
# ---------------------------------------------------------------------------


class TestSnapshot:
    """Test SnapshotManager disk dump."""

    def test_snapshot_dumps_rtdb_to_disk(self) -> None:
        """Snapshot dumps full RTDB bytes to disk file at configured path."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.snapshot import SnapshotManager

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = SnapshotManager(rtdb, Path(tmpdir), interval_s=60)
            result = snap.dump_snapshot("test")
            assert result.exists()
            assert result.stat().st_size == ctypes.sizeof(EmsRtdb)
            del snap

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_snapshot_file_size_matches(self) -> None:
        """Snapshot file size matches sizeof(ems_rtdb_t)."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.snapshot import SnapshotManager

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = SnapshotManager(rtdb, Path(tmpdir), interval_s=60)
            result = snap.dump_snapshot("test")
            assert result.stat().st_size == ctypes.sizeof(EmsRtdb)
            del snap

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()

    def test_snapshot_atomic_write(self) -> None:
        """Snapshot uses atomic write (write to .tmp, rename) -- no .tmp files left."""
        from ems_common.rtdb import attach_rtdb, detach_rtdb
        from ems_data_manager.snapshot import SnapshotManager

        lib, ptr = _create_rtdb_via_c(1, 4)
        shm, rtdb = attach_rtdb()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = SnapshotManager(rtdb, Path(tmpdir), interval_s=60)
            snap.dump_snapshot("test")

            # No .tmp files should remain
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            assert len(tmp_files) == 0, f"Found leftover .tmp files: {tmp_files}"
            del snap

        del rtdb
        detach_rtdb(shm)
        lib.rtdb_detach(ptr)
        lib.rtdb_destroy()


# ---------------------------------------------------------------------------
# Tests: systemd service files (DATA-07)
# ---------------------------------------------------------------------------


class TestSystemdUnits:
    """Test systemd service file content."""

    DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy" / "systemd"

    def test_data_manager_c_service_type_simple(self) -> None:
        """data_manager_c service has Type=simple."""
        service_path = self.DEPLOY_DIR / "ems-data-manager.service"
        assert service_path.exists(), f"Service file not found: {service_path}"
        content = service_path.read_text()
        assert "Type=simple" in content

    def test_data_manager_c_service_has_exec_stop(self) -> None:
        """data_manager_c service has ExecStop for shm cleanup."""
        service_path = self.DEPLOY_DIR / "ems-data-manager.service"
        assert service_path.exists()
        content = service_path.read_text()
        assert "ExecStop=" in content
        assert "ems_rtdb" in content or "/dev/shm" in content

    def test_data_manager_python_after_c_service(self) -> None:
        """data_manager Python service has After=ems-data-manager.service."""
        service_path = self.DEPLOY_DIR / "ems-data-manager-python.service"
        assert service_path.exists(), f"Service file not found: {service_path}"
        content = service_path.read_text()
        assert "After=ems-data-manager.service" in content
        assert "Requires=ems-data-manager.service" in content
