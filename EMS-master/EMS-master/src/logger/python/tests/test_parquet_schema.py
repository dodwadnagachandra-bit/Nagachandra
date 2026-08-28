"""Tests for Parquet schema builders.

Validates cluster and system PyArrow schema structure, field counts,
LIST column types, compression metadata, and topology metadata.
"""

from __future__ import annotations

import pyarrow as pa

from ems_logger.parquet_schema import (
    build_cluster_schema,
    build_system_schema,
    build_topology_metadata,
)


class TestBuildClusterSchema:
    """Tests for build_cluster_schema()."""

    def test_returns_pyarrow_schema(self, default_topology: dict) -> None:
        """build_cluster_schema returns a pa.Schema instance."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=default_topology["racks_per_cluster"],
            modules_per_rack=default_topology["modules_per_rack"],
            cells_per_module=default_topology["cells_per_module"],
            temps_per_module=default_topology["temps_per_module"],
        )
        assert isinstance(schema, pa.Schema)

    def test_has_timestamp_field(self, default_topology: dict) -> None:
        """First field is ts (int64)."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=default_topology["racks_per_cluster"],
            modules_per_rack=default_topology["modules_per_rack"],
            cells_per_module=default_topology["cells_per_module"],
            temps_per_module=default_topology["temps_per_module"],
        )
        assert schema.field("ts").type == pa.int64()

    def test_field_count(self, default_topology: dict) -> None:
        """Field count = 1 (ts) + racks * (12 flat + modules * 2 LIST)."""
        racks: int = default_topology["racks_per_cluster"]
        modules: int = default_topology["modules_per_rack"]

        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=racks,
            modules_per_rack=modules,
            cells_per_module=default_topology["cells_per_module"],
            temps_per_module=default_topology["temps_per_module"],
        )
        expected: int = 1 + racks * (12 + modules * 2)
        assert len(schema) == expected, f"Expected {expected} fields, got {len(schema)}"

    def test_rack_flat_fields(self) -> None:
        """Rack flat fields use rack{N}_ prefix."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=2,
            modules_per_rack=1,
            cells_per_module=4,
            temps_per_module=2,
        )
        # Check rack0 flat fields exist
        for suffix in [
            "pack_v", "pack_i", "soc", "soh",
            "min_cell_v", "max_cell_v", "avg_cell_v",
            "min_cell_t", "max_cell_t", "avg_cell_t",
            "fault_code", "online",
        ]:
            name: str = f"rack0_{suffix}"
            assert name in schema.names, f"Missing field: {name}"

    def test_module_list_columns(self) -> None:
        """Module cell voltage/temp columns are LIST(float32)."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=1,
            modules_per_rack=2,
            cells_per_module=15,
            temps_per_module=4,
        )
        # Check rack0_mod0_cell_v is list of float32
        cell_v_field: pa.Field = schema.field("rack0_mod0_cell_v")
        assert cell_v_field.type == pa.list_(pa.float32())

        cell_t_field: pa.Field = schema.field("rack0_mod0_cell_t")
        assert cell_t_field.type == pa.list_(pa.float32())

        # Check rack0_mod1 also exists
        assert "rack0_mod1_cell_v" in schema.names
        assert "rack0_mod1_cell_t" in schema.names

    def test_compression_metadata(self, default_topology: dict) -> None:
        """Schema metadata includes compression key."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=default_topology["racks_per_cluster"],
            modules_per_rack=default_topology["modules_per_rack"],
            cells_per_module=default_topology["cells_per_module"],
            temps_per_module=default_topology["temps_per_module"],
        )
        metadata: dict = schema.metadata
        assert metadata is not None
        assert b"compression" in metadata
        assert metadata[b"compression"] == b"snappy"

    def test_minimal_topology(self) -> None:
        """Schema works with minimal topology (1 rack, 1 module)."""
        schema: pa.Schema = build_cluster_schema(
            racks_per_cluster=1,
            modules_per_rack=1,
            cells_per_module=1,
            temps_per_module=1,
        )
        # 1 (ts) + 1 * (12 flat + 1 * 2 LIST) = 15
        assert len(schema) == 15


class TestBuildSystemSchema:
    """Tests for build_system_schema()."""

    def test_returns_pyarrow_schema(self) -> None:
        """build_system_schema returns a pa.Schema instance."""
        schema: pa.Schema = build_system_schema()
        assert isinstance(schema, pa.Schema)

    def test_has_timestamp_field(self) -> None:
        """First field is ts (int64)."""
        schema: pa.Schema = build_system_schema()
        assert schema.field("ts").type == pa.int64()

    def test_pcs_fields(self) -> None:
        """PCS fields are present with pcs_ prefix."""
        schema: pa.Schema = build_system_schema()
        pcs_fields: list[str] = [
            "pcs_ac_voltage", "pcs_ac_current", "pcs_active_power",
            "pcs_reactive_power", "pcs_dc_voltage", "pcs_dc_current",
            "pcs_frequency", "pcs_temperature", "pcs_state", "pcs_fault_code",
        ]
        for name in pcs_fields:
            assert name in schema.names, f"Missing PCS field: {name}"

    def test_meter_fields(self) -> None:
        """Meter fields are present with meter_ prefix."""
        schema: pa.Schema = build_system_schema()
        meter_fields: list[str] = [
            "meter_voltage", "meter_current", "meter_active_power",
            "meter_reactive_power", "meter_frequency", "meter_power_factor",
            "meter_energy_import", "meter_energy_export",
        ]
        for name in meter_fields:
            assert name in schema.names, f"Missing meter field: {name}"

    def test_btms_fields(self) -> None:
        """BTMS fields are present with btms_ prefix."""
        schema: pa.Schema = build_system_schema()
        btms_fields: list[str] = [
            "btms_inlet_temp", "btms_outlet_temp",
            "btms_fan_speed_pct", "btms_cooling_active",
        ]
        for name in btms_fields:
            assert name in schema.names, f"Missing BTMS field: {name}"

    def test_gpio_list_fields(self) -> None:
        """GPIO di and do fields are LIST(uint8)."""
        schema: pa.Schema = build_system_schema()
        assert schema.field("gpio_di").type == pa.list_(pa.uint8())
        assert schema.field("gpio_do").type == pa.list_(pa.uint8())

    def test_system_aggregate_fields(self) -> None:
        """System aggregate fields are present with system_ prefix."""
        schema: pa.Schema = build_system_schema()
        system_fields: list[str] = [
            "system_control_state", "system_source_priority",
            "system_active_setpoint_kw", "system_total_soc",
            "system_total_power_kw", "system_total_energy_kwh",
            "system_ems_uptime_s",
        ]
        for name in system_fields:
            assert name in schema.names, f"Missing system field: {name}"

    def test_total_field_count(self) -> None:
        """System schema has exactly the expected number of fields."""
        schema: pa.Schema = build_system_schema()
        # 1 ts + 10 pcs + 8 meter + 4 btms + 2 gpio + 7 system = 32
        assert len(schema) == 32, f"Expected 32 fields, got {len(schema)}"

    def test_compression_metadata(self) -> None:
        """Schema metadata includes compression key."""
        schema: pa.Schema = build_system_schema()
        metadata: dict = schema.metadata
        assert metadata is not None
        assert b"compression" in metadata
        assert metadata[b"compression"] == b"snappy"


class TestBuildTopologyMetadata:
    """Tests for build_topology_metadata()."""

    def test_returns_string_dict(self, default_topology: dict) -> None:
        """build_topology_metadata returns dict[str, str]."""
        meta: dict[str, str] = build_topology_metadata(
            cluster_count=default_topology["cluster_count"],
            racks_per_cluster=default_topology["racks_per_cluster"],
            modules_per_rack=default_topology["modules_per_rack"],
            cells_per_module=default_topology["cells_per_module"],
        )
        assert isinstance(meta, dict)
        for k, v in meta.items():
            assert isinstance(k, str)
            assert isinstance(v, str)

    def test_contains_topology_keys(self, default_topology: dict) -> None:
        """Metadata contains all topology dimension keys."""
        meta: dict[str, str] = build_topology_metadata(
            cluster_count=default_topology["cluster_count"],
            racks_per_cluster=default_topology["racks_per_cluster"],
            modules_per_rack=default_topology["modules_per_rack"],
            cells_per_module=default_topology["cells_per_module"],
        )
        for key in ["cluster_count", "racks_per_cluster", "modules_per_rack", "cells_per_module"]:
            assert key in meta, f"Missing metadata key: {key}"

    def test_values_are_string_ints(self, default_topology: dict) -> None:
        """Metadata values are string representations of integers."""
        meta: dict[str, str] = build_topology_metadata(
            cluster_count=1,
            racks_per_cluster=4,
            modules_per_rack=8,
            cells_per_module=15,
        )
        assert meta["cluster_count"] == "1"
        assert meta["racks_per_cluster"] == "4"
        assert meta["modules_per_rack"] == "8"
        assert meta["cells_per_module"] == "15"
