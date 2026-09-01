"""RTDB shared memory struct definitions and lifecycle helpers.

Provides:
- ctypes mirror of rtdb.h (EmsRtdb and sub-structs)
- attach_rtdb() / detach_rtdb() for consumers to access C-owned shm
- validate_topology() to verify RTDB matches expected config
"""

from __future__ import annotations

import ctypes
from multiprocessing import shared_memory
from multiprocessing.resource_tracker import unregister as _rt_unregister

# Constants — must match rtdb.h
RTDB_VERSION: int = 1
RTDB_MAGIC: int = 0x454D5352  # "EMSR" in ASCII

MAX_CLUSTERS: int = 8
MAX_RACKS_PER_CLUSTER: int = 16
MAX_MODULES_PER_RACK: int = 20
MAX_CELLS_PER_MODULE: int = 108
MAX_TEMPS_PER_MODULE: int = 40
MAX_CAN_INTERFACES: int = 2


class EmsSeqlock(ctypes.Structure):
    """Seqlock concurrency primitive — mirrors ems_seqlock_t."""

    _fields_ = [("sequence", ctypes.c_uint32)]


class EmsModule(ctypes.Structure):
    """Battery module (LMU) — mirrors ems_module_t."""

    _fields_ = [
        ("cell_v", ctypes.c_float * MAX_CELLS_PER_MODULE),
        ("cell_t", ctypes.c_float * MAX_TEMPS_PER_MODULE),
        ("balancing", ctypes.c_uint8 * MAX_CELLS_PER_MODULE),
    ]


class EmsRack(ctypes.Structure):
    """Battery rack (BMU) — mirrors ems_rack_t."""

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("modules", EmsModule * MAX_MODULES_PER_RACK),
        ("pack_v", ctypes.c_float),
        ("pack_i", ctypes.c_float),
        ("pack_soc", ctypes.c_float),
        ("pack_soh", ctypes.c_float),
        ("min_cell_v", ctypes.c_float),
        ("max_cell_v", ctypes.c_float),
        ("avg_cell_v", ctypes.c_float),
        ("min_cell_t", ctypes.c_float),
        ("max_cell_t", ctypes.c_float),
        ("avg_cell_t", ctypes.c_float),
        ("max_cell_num", ctypes.c_float),
 	("min_cell_num", ctypes.c_float),
	("full_cap_rem", ctypes.c_float),
	("Delta_t", ctypes.c_float),
	("Delta_v", ctypes.c_float),
	("cycle_cnt", ctypes.c_float),

    	("Tmax_id", ctypes.c_float),
        ("Tmin_id", ctypes.c_float),
        ("fault_code", ctypes.c_uint32),
        ("online", ctypes.c_uint8),

         ("V_0", ctypes.c_float),
        ("V_1", ctypes.c_float),
        ("V_2", ctypes.c_float),
        ("V_3", ctypes.c_float),
        ("V_4", ctypes.c_float),
        ("V_5", ctypes.c_float),
        ("V_6", ctypes.c_float),
        ("V_7", ctypes.c_float),
        ("V_8", ctypes.c_float),
        ("V_9", ctypes.c_float),
        ("V_10", ctypes.c_float),
        ("V_11", ctypes.c_float),
        ("V_12", ctypes.c_float),
        ("V_13", ctypes.c_float),
        ("V_14", ctypes.c_float),
        ("V_15", ctypes.c_float),
        ("V_16", ctypes.c_float),
        ("V_17", ctypes.c_float),
        ("V_18", ctypes.c_float),
        ("V_19", ctypes.c_float),
        ("V_20", ctypes.c_float),
	("V_21", ctypes.c_float),
        ("V_22", ctypes.c_float),
        ("V_23", ctypes.c_float),
        ("V_24", ctypes.c_float),
        ("V_25", ctypes.c_float),
        ("V_26", ctypes.c_float),
        ("V_27", ctypes.c_float),
        ("V_28", ctypes.c_float),
        ("V_29", ctypes.c_float),
        ("V_30", ctypes.c_float),
        ("V_31", ctypes.c_float),
        ("V_32", ctypes.c_float),
        ("V_33", ctypes.c_float),
        ("V_34", ctypes.c_float),
        ("V_35", ctypes.c_float),
        ("V_36", ctypes.c_float),
        ("V_37", ctypes.c_float),
        ("V_38", ctypes.c_float),
        ("V_39", ctypes.c_float),
        ("V_40", ctypes.c_float),
        ("V_41", ctypes.c_float),
        ("V_42", ctypes.c_float),
        ("V_43", ctypes.c_float),
        ("V_44", ctypes.c_float),
        ("V_45", ctypes.c_float),
        ("V_46", ctypes.c_float),
        ("V_47", ctypes.c_float),
        ("V_48", ctypes.c_float),
        ("V_49", ctypes.c_float),
        ("V_50", ctypes.c_float),
        ("V_51", ctypes.c_float),
        ("V_52", ctypes.c_float),
        ("V_53", ctypes.c_float),
        ("V_54", ctypes.c_float),


    ]


class EmsCluster(ctypes.Structure):
    """Battery cluster — mirrors ems_cluster_t."""

    _fields_ = [
        ("racks", EmsRack * MAX_RACKS_PER_CLUSTER),
        ("cluster_v", ctypes.c_float),
        ("cluster_i", ctypes.c_float),
        ("cluster_soc", ctypes.c_float),
        ("cluster_soh", ctypes.c_float),
        ("min_cell_v", ctypes.c_float),
        ("max_cell_v", ctypes.c_float),
        ("avg_cell_v", ctypes.c_float),
        ("min_cell_t", ctypes.c_float),
        ("max_cell_t", ctypes.c_float),
        ("avg_cell_t", ctypes.c_float),
    ]


class EmsPcs(ctypes.Structure):
    """PCS inverter telemetry — mirrors ems_pcs_t."""

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("ac_voltage", ctypes.c_float),
        ("ac_current", ctypes.c_float),
        ("active_power", ctypes.c_float),
        ("reactive_power", ctypes.c_float),
        ("dc_voltage", ctypes.c_float),
        ("dc_current", ctypes.c_float),
        ("frequency", ctypes.c_float),
        ("temperature", ctypes.c_float),
        ("state", ctypes.c_int),
        ("fault_code", ctypes.c_uint32),
    ]


class EmsGpio(ctypes.Structure):
    """Safety digital I/O — mirrors ems_gpio_t."""

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("di", ctypes.c_uint8 * 8),
        ("do_state", ctypes.c_uint8 * 8),
    ]


class EmsMeter(ctypes.Structure):
    """Energy meter readings — mirrors ems_meter_t."""

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("voltage", ctypes.c_float),
        ("current", ctypes.c_float),
        ("active_power", ctypes.c_float),
        ("reactive_power", ctypes.c_float),
        ("frequency", ctypes.c_float),
        ("power_factor", ctypes.c_float),
        ("energy_import", ctypes.c_float),
        ("energy_export", ctypes.c_float),
    ]


class EmsBtms(ctypes.Structure):
    """Battery thermal management — mirrors ems_btms_t."""

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("inlet_temp", ctypes.c_float),
        ("outlet_temp", ctypes.c_float),
        ("fan_speed_pct", ctypes.c_float),
        ("cooling_active", ctypes.c_uint8),
    ]


class EmsSystem(ctypes.Structure):
    """System-level aggregates — mirrors ems_system_t.

    New fields added for M2 PCS command path:
    - pcs_command: PCS_CMD_* enum (0=NONE, 1=ON, 2=OFF, 3=FAULT_RESET)
    - _pad_cmd: 3-byte alignment pad before pcs_command_seq
    - pcs_command_seq: monotonic counter; comm_manager acts on each increment
    - active_derating_pct: active derating percentage 0.0-100.0 (used in Phase 16)
    """

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("control_state", ctypes.c_int),
        ("source_priority", ctypes.c_int),
        ("active_setpoint_kw", ctypes.c_float),
        ("total_soc", ctypes.c_float),
        ("total_power_kw", ctypes.c_float),
        ("total_energy_kwh", ctypes.c_float),
        ("ems_uptime_s", ctypes.c_uint32),
        ("pcs_command", ctypes.c_uint8),
        ("_pad_cmd", ctypes.c_uint8 * 3),
        ("pcs_command_seq", ctypes.c_uint32),
        ("active_derating_pct", ctypes.c_float),
    ]


class EmsCanHealth(ctypes.Structure):
    """Per-CAN-interface health — mirrors ems_can_health_t.

    Expected sizeof: 32 bytes (must match C _Static_assert).
    """

    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("bus_state", ctypes.c_uint32),
        ("tx_error_count", ctypes.c_uint8),
        ("rx_error_count", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 2),
        ("last_error_frame_ms", ctypes.c_uint64),
    ]


class EmsRtdb(ctypes.Structure):
    """Top-level RTDB container — mirrors ems_rtdb_t."""

    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("cluster_count", ctypes.c_uint8),
        ("racks_per_cluster", ctypes.c_uint8),
        ("modules_per_rack", ctypes.c_uint8),
        ("cells_per_module", ctypes.c_uint8),
        ("temps_per_module", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 3),
        ("clusters", EmsCluster * MAX_CLUSTERS),
        ("pcs", EmsPcs),
        ("gpio", EmsGpio),
        ("meter", EmsMeter),
        ("btms", EmsBtms),
        ("system", EmsSystem),
        ("can_health", EmsCanHealth * MAX_CAN_INTERFACES),
    ]


# ---------------------------------------------------------------------------
# Shared memory lifecycle helpers
# ---------------------------------------------------------------------------

RTDB_SHM_NAME: str = "ems_rtdb"


def attach_rtdb() -> tuple[shared_memory.SharedMemory, EmsRtdb]:
    """Attach to the C-owned RTDB shared memory segment.

    Opens the POSIX shm created by data_manager_c, disables Python's
    resource_tracker to prevent premature unlink (the C process owns the
    segment lifetime), and returns a ctypes view of the RTDB.

    Returns:
        Tuple of (SharedMemory handle, EmsRtdb ctypes struct).

    Raises:
        FileNotFoundError: If shm does not exist.
        RuntimeError: If magic or version mismatch.
    """
    shm = shared_memory.SharedMemory(name=RTDB_SHM_NAME, create=False)

    # Python 3.12 lacks track=False on SharedMemory. Unregister from
    # resource_tracker to prevent it from unlinking the shm when this
    # process exits (the C process owns the segment).
    _rt_unregister(f"/{RTDB_SHM_NAME}", "shared_memory")

    rtdb = EmsRtdb.from_buffer(shm.buf)

    if rtdb.magic != RTDB_MAGIC:
        shm.close()
        msg = f"RTDB magic mismatch: expected 0x{RTDB_MAGIC:08X}, got 0x{rtdb.magic:08X}"
        raise RuntimeError(msg)

    if rtdb.version != RTDB_VERSION:
        shm.close()
        msg = f"RTDB version mismatch: expected {RTDB_VERSION}, got {rtdb.version}"
        raise RuntimeError(msg)

    return shm, rtdb


def detach_rtdb(shm: shared_memory.SharedMemory) -> None:
    """Detach from the RTDB shared memory (close, do NOT unlink).

    Args:
        shm: SharedMemory handle returned by attach_rtdb().
    """
    shm.close()


def validate_topology(rtdb: EmsRtdb, expected: dict[str, int]) -> None:
    """Validate RTDB topology counts against expected configuration.

    Args:
        rtdb: Attached EmsRtdb ctypes struct.
        expected: Dict with keys: cluster_count, racks_per_cluster,
                  modules_per_rack, cells_per_module, temps_per_module.

    Raises:
        ValueError: If any topology field does not match expected value.
    """
    fields = [
        "cluster_count",
        "racks_per_cluster",
        "modules_per_rack",
        "cells_per_module",
        "temps_per_module",
    ]
    for field in fields:
        actual = getattr(rtdb, field)
        exp = expected.get(field)
        if exp is not None and actual != exp:
            msg = f"{field}: expected {exp}, got {actual}"
            raise ValueError(msg)
