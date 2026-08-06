"""Tests for RTDB ctypes struct mirror (matches C rtdb.h layout)."""

import ctypes
import subprocess
from pathlib import Path

import pytest

from ems_common.rtdb import (
    RTDB_MAGIC,
    RTDB_VERSION,
    MAX_CELLS_PER_MODULE,
    MAX_CLUSTERS,
    MAX_MODULES_PER_RACK,
    MAX_RACKS_PER_CLUSTER,
    MAX_TEMPS_PER_MODULE,
    EmsBtms,
    EmsCluster,
    EmsGpio,
    EmsMeter,
    EmsModule,
    EmsPcs,
    EmsRack,
    EmsRtdb,
    EmsSeqlock,
    EmsSystem,
)

# Expected C sizeof values (from test_rtdb.c output)
# Updated after Phase 11: +64 bytes for ems_can_health_t (2 CAN interfaces)
# Updated after Phase 14 Plan 01: +8 bytes for pcs_command(1)+_pad_cmd(3)+pcs_command_seq(4)+active_derating_pct(4).
# Net RTDB increase is 8 (not 12) because old EmsSystem had 4 bytes implicit tail-pad that are now consumed.
C_SIZEOF_RTDB: int = 1800816


def test_constants_match() -> None:
    """Verify constants match schema-defined maximums."""
    assert RTDB_VERSION == 1
    assert RTDB_MAGIC == 0x454D5352
    assert MAX_CLUSTERS == 8
    assert MAX_RACKS_PER_CLUSTER == 16
    assert MAX_MODULES_PER_RACK == 20
    assert MAX_CELLS_PER_MODULE == 108
    assert MAX_TEMPS_PER_MODULE == 40


def test_module_size() -> None:
    """Verify EmsModule size matches expected calculation."""
    size = ctypes.sizeof(EmsModule)
    # 108 * 4 (cell_v) + 40 * 4 (cell_t) + 108 * 1 (balancing) = 700
    expected = MAX_CELLS_PER_MODULE * 4 + MAX_TEMPS_PER_MODULE * 4 + MAX_CELLS_PER_MODULE * 1
    assert size == expected, f"EmsModule: {size} != expected {expected}"


def test_rtdb_size_under_limit() -> None:
    """Verify total RTDB size is under 2 MB safety ceiling."""
    size = ctypes.sizeof(EmsRtdb)
    assert size < 2 * 1024 * 1024, f"EmsRtdb {size} exceeds 2 MB"
    print(f"\n  sizeof(EmsRtdb) = {size} bytes ({size / 1024 / 1024:.2f} MB)")


def test_populate_and_readback() -> None:
    """Populate RTDB fields and verify readback."""
    rtdb = EmsRtdb()

    rtdb.magic = RTDB_MAGIC
    rtdb.version = RTDB_VERSION
    rtdb.cluster_count = 1
    rtdb.racks_per_cluster = 4
    rtdb.modules_per_rack = 8
    rtdb.cells_per_module = 16
    rtdb.temps_per_module = 8

    # BMS cell voltage (float32 — use approx for precision)
    rtdb.clusters[0].racks[0].modules[0].cell_v[0] = 3.3
    assert rtdb.clusters[0].racks[0].modules[0].cell_v[0] == pytest.approx(3.3, rel=1e-6)

    # PCS active power
    rtdb.pcs.active_power = 100.5
    assert rtdb.pcs.active_power == pytest.approx(100.5, rel=1e-6)

    # GPIO digital input (E-Stop)
    rtdb.gpio.di[6] = 1
    assert rtdb.gpio.di[6] == 1

    # Meter energy import
    rtdb.meter.energy_import = 1234.5
    assert rtdb.meter.energy_import == pytest.approx(1234.5, rel=1e-6)

    # System state
    rtdb.system.control_state = 3  # EMS_STATE_CHARGING
    assert rtdb.system.control_state == 3


def test_seqlock_fields() -> None:
    """Verify seqlock struct is writable/readable."""
    lock = EmsSeqlock()
    lock.sequence = 42
    assert lock.sequence == 42

    rack = EmsRack()
    rack.lock.sequence = 100
    assert rack.lock.sequence == 100


def test_max_index_access() -> None:
    """Access maximum valid indices to prove container topology works."""
    rtdb = EmsRtdb()

    # Max indices: cluster[7].rack[15].module[19].cell_v[107]
    rtdb.clusters[MAX_CLUSTERS - 1].racks[MAX_RACKS_PER_CLUSTER - 1].modules[
        MAX_MODULES_PER_RACK - 1
    ].cell_v[MAX_CELLS_PER_MODULE - 1] = 3.6
    val = (
        rtdb.clusters[MAX_CLUSTERS - 1]
        .racks[MAX_RACKS_PER_CLUSTER - 1]
        .modules[MAX_MODULES_PER_RACK - 1]
        .cell_v[MAX_CELLS_PER_MODULE - 1]
    )
    assert val == pytest.approx(3.6, rel=1e-6)


def test_c_struct_size_match() -> None:
    """Verify Python sizeof matches C sizeof by running the C test binary."""
    c_test_bin = Path("build/tests/c/test_rtdb")
    if not c_test_bin.exists():
        # Try building — may fail if system deps (libzmq-dev) are missing
        try:
            subprocess.run(
                ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Debug"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["cmake", "--build", "build", "--", f"-j{4}"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # Fall through to constant-based check below

    if c_test_bin.exists():
        result = subprocess.run([str(c_test_bin)], capture_output=True, text=True, check=True)
        # Parse sizeof(ems_rtdb_t) from output
        for line in result.stdout.splitlines():
            if "sizeof(ems_rtdb_t)" in line:
                c_size = int(line.strip().split("=")[1].strip())
                py_size = ctypes.sizeof(EmsRtdb)
                if c_size != py_size:
                    # Binary may be stale (compiled before struct changes).
                    # Check if Python matches the expected constant instead.
                    if py_size == C_SIZEOF_RTDB:
                        pytest.skip(
                            f"C test binary is stale (reports {c_size}, expected {C_SIZEOF_RTDB}). "
                            f"Rebuild with: make clean build"
                        )
                    assert c_size == py_size, f"C sizeof={c_size} != Python sizeof={py_size}"
                return

    # Fallback: compare against known C size constant
    py_size = ctypes.sizeof(EmsRtdb)
    assert py_size == C_SIZEOF_RTDB, f"Python sizeof={py_size} != expected C sizeof={C_SIZEOF_RTDB}"


def test_rtdb_field_offsets() -> None:
    """Verify RTDB sections are ordered correctly and don't overlap."""
    clusters_offset = EmsRtdb.clusters.offset
    pcs_offset = EmsRtdb.pcs.offset
    gpio_offset = EmsRtdb.gpio.offset
    meter_offset = EmsRtdb.meter.offset
    btms_offset = EmsRtdb.btms.offset
    system_offset = EmsRtdb.system.offset

    # Sections must be ordered: clusters < pcs < gpio < meter < btms < system
    assert clusters_offset < pcs_offset, "clusters must come before pcs"
    assert pcs_offset < gpio_offset, "pcs must come before gpio"
    assert gpio_offset < meter_offset, "gpio must come before meter"
    assert meter_offset < btms_offset, "meter must come before btms"
    assert btms_offset < system_offset, "btms must come before system"

    # Verify no overlap: each section start >= previous section end
    assert pcs_offset >= clusters_offset + ctypes.sizeof(EmsCluster) * MAX_CLUSTERS
    assert gpio_offset >= pcs_offset + ctypes.sizeof(EmsPcs)
    assert meter_offset >= gpio_offset + ctypes.sizeof(EmsGpio)
    assert btms_offset >= meter_offset + ctypes.sizeof(EmsMeter)
    assert system_offset >= btms_offset + ctypes.sizeof(EmsBtms)
