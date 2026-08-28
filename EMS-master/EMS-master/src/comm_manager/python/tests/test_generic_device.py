"""Tests for GenericDevice -- BTMS/Meter/DG/PV Modbus device subclass.

Covers: RTDB write to btms/meter sections, no-RTDB-section graceful
handling for DG/PV, register name-to-struct-field mapping.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ems_common.rtdb import EmsBtms, EmsMeter, EmsSeqlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_btms_register_map(tmp_path: Path) -> str:
    """Create a BTMS register map YAML for unit testing."""
    content = """\
metadata:
  version: "synthetic-1.0"
  device_type: btms
registers:
  - address: 0x0001
    name: inlet_temp
    scale: 10
    unit: degC
    access: r
    signed: true
    default: 250
  - address: 0x0002
    name: outlet_temp
    scale: 10
    unit: degC
    access: r
    signed: true
    default: 280
  - address: 0x0003
    name: fan_speed_pct
    scale: 10
    unit: "%"
    access: r
    signed: false
    default: 0
  - address: 0x0004
    name: cooling_active
    scale: 1
    unit: ""
    access: r
    signed: false
    default: 0
"""
    p = tmp_path / "btms_register_map.yaml"
    p.write_text(content)
    return str(p)


def _make_meter_register_map(tmp_path: Path) -> str:
    """Create a Meter register map YAML for unit testing."""
    content = """\
metadata:
  version: "synthetic-1.0"
  device_type: meter
registers:
  - address: 0x0001
    name: voltage
    scale: 10
    unit: V
    access: r
    signed: false
    default: 2300
  - address: 0x0002
    name: current
    scale: 100
    unit: A
    access: r
    signed: false
    default: 0
  - address: 0x0003
    name: active_power
    scale: 10
    unit: kW
    access: r
    signed: true
    default: 0
  - address: 0x0004
    name: reactive_power
    scale: 10
    unit: kVAr
    access: r
    signed: true
    default: 0
  - address: 0x0005
    name: frequency
    scale: 100
    unit: Hz
    access: r
    signed: false
    default: 5000
  - address: 0x0006
    name: power_factor
    scale: 1000
    unit: ""
    access: r
    signed: true
    default: 1000
  - address: 0x0007
    name: energy_import
    scale: 10
    unit: kWh
    access: r
    signed: false
    default: 0
  - address: 0x0008
    name: energy_export
    scale: 10
    unit: kWh
    access: r
    signed: false
    default: 0
"""
    p = tmp_path / "meter_register_map.yaml"
    p.write_text(content)
    return str(p)


def _make_dg_register_map(tmp_path: Path) -> str:
    """Create a minimal DG register map (no RTDB section)."""
    content = """\
metadata:
  version: "synthetic-1.0"
  device_type: dg
registers:
  - address: 0x0001
    name: output_power
    scale: 10
    unit: kW
    access: r
    signed: false
    default: 0
"""
    p = tmp_path / "dg_register_map.yaml"
    p.write_text(content)
    return str(p)


class MockRtdb:
    """In-process mock RTDB with real struct sections (no shm)."""

    def __init__(self) -> None:
        self.btms = EmsBtms()
        self.meter = EmsMeter()
        # No dg or pv section -- GenericDevice must handle gracefully


# ---------------------------------------------------------------------------
# GenericDevice RTDB Write Tests
# ---------------------------------------------------------------------------


class TestGenericDeviceBtmsWrite:
    """Test GenericDevice writes decoded values to RTDB btms section."""

    @pytest.fixture
    def btms_device(self, tmp_path: Path):
        from ems_comm_manager.generic_device import GenericDevice

        reg_map_path = _make_btms_register_map(tmp_path)
        push_socket = MagicMock()
        rtdb = MockRtdb()
        dev = GenericDevice(
            device_id="btms",
            device_type="btms",
            slave_id=2,
            port="/dev/ttyUSB1",
            register_map_path=reg_map_path,
            poll_interval_ms=2000,
            timeout_s=3.0,
            priority=2,
            push_socket=push_socket,
            rtdb=rtdb,
            rtdb_section_name="btms",
        )
        return dev, rtdb

    def test_write_to_rtdb_btms_maps_values(self, btms_device) -> None:
        """GenericDevice writes scaled BTMS values to rtdb.btms fields."""
        dev, rtdb = btms_device
        # inlet_temp=250 / 10 = 25.0, outlet_temp=280 / 10 = 28.0
        # fan_speed_pct=500 / 10 = 50.0, cooling_active=1 / 1 = 1.0
        dev._write_to_rtdb([250, 280, 500, 1], now_ms=5000)

        assert abs(rtdb.btms.inlet_temp - 25.0) < 0.01
        assert abs(rtdb.btms.outlet_temp - 28.0) < 0.01
        assert abs(rtdb.btms.fan_speed_pct - 50.0) < 0.01
        assert rtdb.btms.last_update_ms == 5000

    def test_write_to_rtdb_btms_signed_negative(self, btms_device) -> None:
        """Signed BTMS register with negative value (two's complement)."""
        dev, rtdb = btms_device
        # inlet_temp raw=65486 -> 65486-65536=-50 -> /10 = -5.0 degC
        dev._write_to_rtdb([65486, 280, 0, 0], now_ms=6000)
        assert abs(rtdb.btms.inlet_temp - (-5.0)) < 0.01

    def test_on_success_writes_to_rtdb(self, btms_device) -> None:
        """GenericDevice.on_success() invokes _write_to_rtdb correctly."""
        dev, rtdb = btms_device
        dev.on_success([250, 280, 500, 1], now_ms=7000)
        assert abs(rtdb.btms.inlet_temp - 25.0) < 0.01
        assert rtdb.btms.last_update_ms == 7000


class TestGenericDeviceMeterWrite:
    """Test GenericDevice writes decoded values to RTDB meter section."""

    @pytest.fixture
    def meter_device(self, tmp_path: Path):
        from ems_comm_manager.generic_device import GenericDevice

        reg_map_path = _make_meter_register_map(tmp_path)
        push_socket = MagicMock()
        rtdb = MockRtdb()
        dev = GenericDevice(
            device_id="meter",
            device_type="meter",
            slave_id=3,
            port="/dev/ttyUSB2",
            register_map_path=reg_map_path,
            poll_interval_ms=1000,
            timeout_s=3.0,
            priority=1,
            push_socket=push_socket,
            rtdb=rtdb,
            rtdb_section_name="meter",
        )
        return dev, rtdb

    def test_write_to_rtdb_meter_maps_values(self, meter_device) -> None:
        """GenericDevice writes scaled Meter values to rtdb.meter fields."""
        dev, rtdb = meter_device
        # voltage=2300/10=230.0, current=1500/100=15.0
        # active_power=500/10=50.0, reactive_power=100/10=10.0
        # frequency=5000/100=50.0, power_factor=950/1000=0.95
        # energy_import=10000/10=1000.0, energy_export=5000/10=500.0
        dev._write_to_rtdb([2300, 1500, 500, 100, 5000, 950, 10000, 5000], now_ms=8000)

        assert abs(rtdb.meter.voltage - 230.0) < 0.01
        assert abs(rtdb.meter.current - 15.0) < 0.01
        assert abs(rtdb.meter.active_power - 50.0) < 0.01
        assert abs(rtdb.meter.frequency - 50.0) < 0.01
        assert abs(rtdb.meter.power_factor - 0.95) < 0.01
        assert abs(rtdb.meter.energy_import - 1000.0) < 0.01
        assert abs(rtdb.meter.energy_export - 500.0) < 0.01
        assert rtdb.meter.last_update_ms == 8000


class TestGenericDeviceNoRtdbSection:
    """Test GenericDevice gracefully handles missing RTDB sections (DG/PV)."""

    @pytest.fixture
    def dg_device(self, tmp_path: Path):
        from ems_comm_manager.generic_device import GenericDevice

        reg_map_path = _make_dg_register_map(tmp_path)
        push_socket = MagicMock()
        rtdb = MockRtdb()
        dev = GenericDevice(
            device_id="dg",
            device_type="dg",
            slave_id=4,
            port="/dev/ttyUSB3",
            register_map_path=reg_map_path,
            poll_interval_ms=2000,
            timeout_s=3.0,
            priority=3,
            push_socket=push_socket,
            rtdb=rtdb,
            rtdb_section_name="dg",  # Does not exist in MockRtdb
        )
        return dev, rtdb

    def test_no_rtdb_section_does_not_crash(self, dg_device) -> None:
        """GenericDevice with no matching RTDB section returns without error."""
        dev, rtdb = dg_device
        # Should not raise
        dev._write_to_rtdb([100], now_ms=9000)

    def test_no_rtdb_on_success_works(self, dg_device) -> None:
        """on_success() with no RTDB section completes without error."""
        dev, rtdb = dg_device
        dev.on_success([100], now_ms=10000)

    def test_no_rtdb_at_all(self, tmp_path: Path) -> None:
        """GenericDevice with rtdb=None does not crash."""
        from ems_comm_manager.generic_device import GenericDevice

        reg_map_path = _make_dg_register_map(tmp_path)
        push_socket = MagicMock()
        dev = GenericDevice(
            device_id="pv",
            device_type="pv",
            slave_id=5,
            port="/dev/ttyUSB3",
            register_map_path=reg_map_path,
            poll_interval_ms=1000,
            timeout_s=3.0,
            priority=4,
            push_socket=push_socket,
            rtdb=None,
            rtdb_section_name="pv",
        )
        dev._write_to_rtdb([100], now_ms=11000)  # Should not raise


class TestGenericDevicePriority:
    """Test GenericDevice priority constants."""

    def test_priority_ordering(self, tmp_path: Path) -> None:
        """Device priorities follow PCS=0 > Meter=1 > BTMS=2 > DG=3 > PV=4."""
        from ems_comm_manager.generic_device import (
            PRIORITY_BTMS,
            PRIORITY_DG,
            PRIORITY_METER,
            PRIORITY_PV,
        )

        assert PRIORITY_METER == 1
        assert PRIORITY_BTMS == 2
        assert PRIORITY_DG == 3
        assert PRIORITY_PV == 4
