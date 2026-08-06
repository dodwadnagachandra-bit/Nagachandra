"""Tests for register map loader and value scaling."""

from __future__ import annotations

from pathlib import Path

import pytest

from ems_comm_manager.register_map import RegisterDef, load_register_map, scale_value, build_read_ranges

REPO_ROOT: Path = Path(__file__).resolve().parents[4]  # src/comm_manager/python/tests -> repo root
CONFIG_DIR: Path = REPO_ROOT / "config"


class TestLoadRegisterMap:
    """Loading register maps from YAML files."""

    def test_load_pcs_register_map(self) -> None:
        """PCS register map loads successfully."""
        regs = load_register_map(CONFIG_DIR / "pcs_register_map.yaml")
        assert len(regs) > 0
        assert all(isinstance(r, RegisterDef) for r in regs)

    def test_pcs_register_has_correct_fields(self) -> None:
        """Parsed registers have all required fields."""
        regs = load_register_map(CONFIG_DIR / "pcs_register_map.yaml")
        first = regs[0]
        assert first.address == 0x0001
        assert first.name == "ac_voltage_l1"
        assert first.scale == 10
        assert first.unit == "V"
        assert first.access == "r"
        assert first.signed is False
        assert first.default == 2300

    def test_load_btms_register_map(self) -> None:
        """BTMS stub register map loads."""
        regs = load_register_map(CONFIG_DIR / "btms_register_map.yaml")
        names = {r.name for r in regs}
        assert "inlet_temp" in names
        assert "outlet_temp" in names
        assert "fan_speed_pct" in names
        assert "cooling_active" in names

    def test_load_meter_register_map(self) -> None:
        """Meter stub register map loads with all fields."""
        regs = load_register_map(CONFIG_DIR / "meter_register_map.yaml")
        names = {r.name for r in regs}
        for field in ["voltage", "current", "active_power", "reactive_power",
                      "frequency", "power_factor", "energy_import", "energy_export"]:
            assert field in names, f"Missing {field}"

    def test_load_dg_register_map(self) -> None:
        """DG stub register map loads."""
        regs = load_register_map(CONFIG_DIR / "dg_register_map.yaml")
        assert len(regs) >= 4

    def test_load_pv_register_map(self) -> None:
        """PV stub register map loads."""
        regs = load_register_map(CONFIG_DIR / "pv_register_map.yaml")
        names = {r.name for r in regs}
        assert "dc_voltage" in names
        assert "dc_current" in names


class TestScaleValue:
    """Value scaling with signed/unsigned handling."""

    def test_unsigned_scale(self) -> None:
        """scale_value with signed=False, raw=2200, scale=10 returns 220.0."""
        reg = RegisterDef(address=1, name="test", scale=10, unit="V",
                          access="r", signed=False)
        assert scale_value(2200, reg) == pytest.approx(220.0)

    def test_signed_negative_twos_complement(self) -> None:
        """scale_value with signed=True, raw=65236, scale=10 returns -30.0."""
        reg = RegisterDef(address=1, name="test", scale=10, unit="V",
                          access="r", signed=True)
        # 65236 = 65536 - 300, so signed value = -300, / 10 = -30.0
        assert scale_value(65236, reg) == pytest.approx(-30.0)

    def test_signed_positive(self) -> None:
        """scale_value with signed=True, raw=100, scale=10 returns 10.0."""
        reg = RegisterDef(address=1, name="test", scale=10, unit="V",
                          access="r", signed=True)
        assert scale_value(100, reg) == pytest.approx(10.0)

    def test_scale_one_passthrough(self) -> None:
        """scale=1 returns raw value as float."""
        reg = RegisterDef(address=1, name="test", scale=1, unit="enum",
                          access="r", signed=False)
        assert scale_value(42, reg) == pytest.approx(42.0)


class TestBuildReadRanges:
    """Grouping contiguous registers for efficient Modbus reads."""

    def test_contiguous_registers_grouped(self) -> None:
        regs = [
            RegisterDef(address=1, name="a", scale=1, unit="", access="r", signed=False),
            RegisterDef(address=2, name="b", scale=1, unit="", access="r", signed=False),
            RegisterDef(address=3, name="c", scale=1, unit="", access="r", signed=False),
        ]
        ranges = build_read_ranges(regs)
        assert ranges == [(1, 3)]

    def test_non_contiguous_split(self) -> None:
        regs = [
            RegisterDef(address=1, name="a", scale=1, unit="", access="r", signed=False),
            RegisterDef(address=2, name="b", scale=1, unit="", access="r", signed=False),
            RegisterDef(address=10, name="c", scale=1, unit="", access="r", signed=False),
        ]
        ranges = build_read_ranges(regs)
        assert ranges == [(1, 2), (10, 1)]

    def test_empty_registers(self) -> None:
        ranges = build_read_ranges([])
        assert ranges == []

    def test_single_register(self) -> None:
        regs = [RegisterDef(address=5, name="a", scale=1, unit="", access="r", signed=False)]
        ranges = build_read_ranges(regs)
        assert ranges == [(5, 1)]
