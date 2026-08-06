"""PyArrow schema definitions for Parquet telemetry files.

Builds schemas for per-cluster and system Parquet files that match
the RTDB field structure published by data_manager at 1Hz.

Cluster files contain per-rack flat fields and per-module LIST columns.
System files contain PCS, meter, BTMS, GPIO, and system aggregate fields.
"""

from __future__ import annotations

import pyarrow as pa


def build_cluster_schema(
    racks_per_cluster: int,
    modules_per_rack: int,
    cells_per_module: int,
    temps_per_module: int,
) -> pa.Schema:
    """Build PyArrow schema for a per-cluster Parquet file.

    Field naming matches publisher._rack_to_dict() output with rack{N}_ prefix.
    Cell voltage and temperature arrays are stored as LIST(float32) columns
    per module for efficient Snappy compression.

    Args:
        racks_per_cluster: Number of racks in this cluster.
        modules_per_rack: Number of battery modules per rack.
        cells_per_module: Number of cells per module (for LIST column sizing).
        temps_per_module: Number of temperature sensors per module.

    Returns:
        PyArrow schema with compression metadata set to "snappy".
    """
    fields: list[pa.Field] = [
        pa.field("ts", pa.int64()),  # ms since epoch
    ]

    for r in range(racks_per_cluster):
        prefix: str = f"rack{r}_"

        # Flat pack-level fields (90% of queries target these)
        fields.extend([
            pa.field(f"{prefix}pack_v", pa.float32()),
            pa.field(f"{prefix}pack_i", pa.float32()),
            pa.field(f"{prefix}soc", pa.float32()),
            pa.field(f"{prefix}soh", pa.float32()),
            pa.field(f"{prefix}min_cell_v", pa.float32()),
            pa.field(f"{prefix}max_cell_v", pa.float32()),
            pa.field(f"{prefix}avg_cell_v", pa.float32()),
            pa.field(f"{prefix}min_cell_t", pa.float32()),
            pa.field(f"{prefix}max_cell_t", pa.float32()),
            pa.field(f"{prefix}avg_cell_t", pa.float32()),
            pa.field(f"{prefix}fault_code", pa.uint32()),
            pa.field(f"{prefix}online", pa.uint8()),
        ])

        # LIST columns for cell-level data per module
        for m in range(modules_per_rack):
            mod_prefix: str = f"{prefix}mod{m}_"
            fields.append(
                pa.field(f"{mod_prefix}cell_v", pa.list_(pa.float32()))
            )
            fields.append(
                pa.field(f"{mod_prefix}cell_t", pa.list_(pa.float32()))
            )

    schema: pa.Schema = pa.schema(fields)

    # Attach compression metadata
    return schema.with_metadata({b"compression": b"snappy"})


def build_system_schema() -> pa.Schema:
    """Build PyArrow schema for the system-level Parquet file.

    Contains all non-BMS telemetry sections: PCS, meter, BTMS, GPIO,
    and system aggregates. Field names match publisher section dicts
    with section prefix (pcs_, meter_, btms_, gpio_, system_).

    Returns:
        PyArrow schema with compression metadata set to "snappy".
    """
    fields: list[pa.Field] = [
        pa.field("ts", pa.int64()),  # ms since epoch

        # PCS section (10 fields)
        pa.field("pcs_ac_voltage", pa.float32()),
        pa.field("pcs_ac_current", pa.float32()),
        pa.field("pcs_active_power", pa.float32()),
        pa.field("pcs_reactive_power", pa.float32()),
        pa.field("pcs_dc_voltage", pa.float32()),
        pa.field("pcs_dc_current", pa.float32()),
        pa.field("pcs_frequency", pa.float32()),
        pa.field("pcs_temperature", pa.float32()),
        pa.field("pcs_state", pa.int32()),
        pa.field("pcs_fault_code", pa.uint32()),

        # Meter section (8 fields)
        pa.field("meter_voltage", pa.float32()),
        pa.field("meter_current", pa.float32()),
        pa.field("meter_active_power", pa.float32()),
        pa.field("meter_reactive_power", pa.float32()),
        pa.field("meter_frequency", pa.float32()),
        pa.field("meter_power_factor", pa.float32()),
        pa.field("meter_energy_import", pa.float32()),
        pa.field("meter_energy_export", pa.float32()),

        # BTMS section (4 fields)
        pa.field("btms_inlet_temp", pa.float32()),
        pa.field("btms_outlet_temp", pa.float32()),
        pa.field("btms_fan_speed_pct", pa.float32()),
        pa.field("btms_cooling_active", pa.uint8()),

        # GPIO section (2 LIST fields)
        pa.field("gpio_di", pa.list_(pa.uint8())),
        pa.field("gpio_do", pa.list_(pa.uint8())),

        # System aggregates (7 fields)
        pa.field("system_control_state", pa.int32()),
        pa.field("system_source_priority", pa.int32()),
        pa.field("system_active_setpoint_kw", pa.float32()),
        pa.field("system_total_soc", pa.float32()),
        pa.field("system_total_power_kw", pa.float32()),
        pa.field("system_total_energy_kwh", pa.float32()),
        pa.field("system_ems_uptime_s", pa.uint32()),
    ]

    schema: pa.Schema = pa.schema(fields)
    return schema.with_metadata({b"compression": b"snappy"})


def build_topology_metadata(
    cluster_count: int,
    racks_per_cluster: int,
    modules_per_rack: int,
    cells_per_module: int,
) -> dict[str, str]:
    """Build topology metadata dict for Parquet file metadata.

    Stored in Parquet file metadata so readers can reshape flat cell
    arrays back to [module][cell] structure.

    Args:
        cluster_count: Total number of clusters.
        racks_per_cluster: Racks per cluster.
        modules_per_rack: Modules per rack.
        cells_per_module: Cells per module.

    Returns:
        String-valued dict suitable for pa.Schema.with_metadata().
    """
    return {
        "cluster_count": str(cluster_count),
        "racks_per_cluster": str(racks_per_cluster),
        "modules_per_rack": str(modules_per_rack),
        "cells_per_module": str(cells_per_module),
    }
