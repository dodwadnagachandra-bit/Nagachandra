"""CAN bus simulator tests -- DBC correctness, signal encoding, multi-rack IDs."""

from __future__ import annotations

import can
import cantools
import pytest

from tools.simulators.can_sim import CANSimulator, SignalGenerator
from tools.simulators.can_sim.rack import RackSimulator

DBC_PATH: str = "config/bms_layer2.dbc"
BASE_CAN_ID: int = 0x18FF0003  # 29-bit CAN ID (without DBC extended flag)


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(scope="session")
def dbc() -> cantools.Database:
    return cantools.database.load_file(DBC_PATH)


@pytest.fixture()
def signal_gen() -> SignalGenerator:
    return SignalGenerator(rack_index=0)


# -- DBC correctness tests ---------------------------------------------------


def test_dbc_loads(dbc: cantools.Database) -> None:
    assert len(dbc.messages) == 10


def test_dbc_message_names(dbc: cantools.Database) -> None:
    expected: set[str] = {
        "PackSummary",
        "CellVoltage_01",
        "CellVoltage_02",
        "CellVoltage_03",
        "CellVoltage_04",
        "CellVoltage_05",
        "CellVoltage_06",
        "CellVoltage_07",
        "CellTemperature",
        "RackStatus",
    }
    actual: set[str] = {m.name for m in dbc.messages}
    assert actual == expected


def test_pack_summary_encode_decode(dbc: cantools.Database) -> None:
    signals: dict[str, float | int] = {
        "pack_v": 52.5,
        "pack_i": -10.0,
        "pack_soc": 75.0,
        "pack_soh": 98.0,
        "fault_code": 0,
    }
    data: bytes = dbc.encode_message("PackSummary", signals)
    decoded: dict = dbc.decode_message("PackSummary", data)
    assert decoded["pack_v"] == pytest.approx(52.5, abs=0.1)
    assert decoded["pack_i"] == pytest.approx(-10.0, abs=0.1)
    assert decoded["pack_soc"] == pytest.approx(75.0, abs=0.5)
    assert decoded["pack_soh"] == pytest.approx(98.0, abs=0.5)
    assert decoded["fault_code"] == 0


def test_signed_pack_current(dbc: cantools.Database) -> None:
    signals: dict[str, float | int] = {
        "pack_v": 52.0,
        "pack_i": -25.5,
        "pack_soc": 50.0,
        "pack_soh": 98.0,
        "fault_code": 0,
    }
    data: bytes = dbc.encode_message("PackSummary", signals)
    decoded: dict = dbc.decode_message("PackSummary", data)
    assert decoded["pack_i"] == pytest.approx(-25.5, abs=0.1)


def test_cell_voltage_encode_decode(dbc: cantools.Database) -> None:
    signals: dict[str, float] = {
        "cell_v_01": 3.350,
        "cell_v_02": 3.400,
        "cell_v_03": 3.450,
        "cell_v_04": 3.500,
    }
    data: bytes = dbc.encode_message("CellVoltage_01", signals)
    decoded: dict = dbc.decode_message("CellVoltage_01", data)
    assert decoded["cell_v_01"] == pytest.approx(3.350, abs=0.001)
    assert decoded["cell_v_02"] == pytest.approx(3.400, abs=0.001)
    assert decoded["cell_v_03"] == pytest.approx(3.450, abs=0.001)
    assert decoded["cell_v_04"] == pytest.approx(3.500, abs=0.001)


def test_temperature_offset(dbc: cantools.Database) -> None:
    signals: dict[str, float] = {f"cell_t_{i:02d}": 32.0 for i in range(1, 9)}
    data: bytes = dbc.encode_message("CellTemperature", signals)
    # Raw value for 32 degC with offset -40 should be 72
    assert data[0] == 72
    decoded: dict = dbc.decode_message("CellTemperature", data)
    assert decoded["cell_t_01"] == pytest.approx(32.0, abs=1.0)


def test_rack_status_encode_decode(dbc: cantools.Database) -> None:
    signals: dict[str, float | int] = {
        "online": 1,
        "balancing_count": 3,
        "min_cell_v": 3.200,
        "max_cell_v": 3.500,
        "avg_cell_v": 3.350,
    }
    data: bytes = dbc.encode_message("RackStatus", signals)
    decoded: dict = dbc.decode_message("RackStatus", data)
    assert decoded["online"] == 1
    assert decoded["balancing_count"] == 3
    assert decoded["min_cell_v"] == pytest.approx(3.200, abs=0.001)
    assert decoded["max_cell_v"] == pytest.approx(3.500, abs=0.001)
    assert decoded["avg_cell_v"] == pytest.approx(3.350, abs=0.001)


# -- CAN ID tests ------------------------------------------------------------


def test_can_id_stride_no_collision() -> None:
    """Verify no CAN ID collisions for 4 clusters x 16 racks x 10 messages."""
    all_ids: set[int] = set()
    for cluster in range(4):
        for rack in range(16):
            for msg_offset in range(10):
                can_id: int = BASE_CAN_ID + cluster * 0x1000 + rack * 0x10 + msg_offset
                all_ids.add(can_id)
    assert len(all_ids) == 4 * 16 * 10


def test_multi_rack_id_ranges() -> None:
    """Verify per-rack CAN ID ranges with 0x10 stride."""
    # Rack 0: base + 0x00..0x09
    for offset in range(10):
        assert BASE_CAN_ID + 0 * 0x10 + offset == BASE_CAN_ID + offset

    # Rack 1: base + 0x10..0x19
    for offset in range(10):
        assert BASE_CAN_ID + 1 * 0x10 + offset == BASE_CAN_ID + 0x10 + offset

    # Rack 15: base + 0xF0..0xF9
    for offset in range(10):
        assert BASE_CAN_ID + 15 * 0x10 + offset == BASE_CAN_ID + 0xF0 + offset


# -- Signal generator tests --------------------------------------------------


def test_signal_generator_voltage_range(signal_gen: SignalGenerator) -> None:
    for i in range(1000):
        v: float = signal_gen.cell_voltage(i % 16)
        assert 2.5 <= v <= 4.2, f"cell_voltage out of range: {v}"


def test_signal_generator_temperature_range(signal_gen: SignalGenerator) -> None:
    for i in range(1000):
        t: float = signal_gen.cell_temperature(i % 8)
        assert -10.0 <= t <= 60.0, f"cell_temperature out of range: {t}"


def test_signal_generator_soc_range(signal_gen: SignalGenerator) -> None:
    # Check SOC at various elapsed times via direct calculation
    import time

    gen: SignalGenerator = SignalGenerator(0)
    gen.start_time = time.monotonic() - 150  # simulate 150s elapsed
    soc: float = gen.pack_soc()
    assert 0.0 <= soc <= 100.0

    gen.start_time = time.monotonic() - 450  # simulate 450s elapsed
    soc = gen.pack_soc()
    assert 0.0 <= soc <= 100.0


def test_signal_generator_rack_offset() -> None:
    gen0: SignalGenerator = SignalGenerator(rack_index=0)
    gen3: SignalGenerator = SignalGenerator(rack_index=3)
    # Different rack indices should produce different base values
    # Due to noise, check pack_voltage which is deterministic (no noise)
    v0: float = gen0.pack_voltage()
    v3: float = gen3.pack_voltage()
    assert v0 != v3


# -- Import test --------------------------------------------------------------


def test_can_simulator_import() -> None:
    assert CANSimulator is not None
    assert isinstance(CANSimulator, type)


# -- Integration test using python-can virtual interface ----------------------


def test_virtual_bus_send(dbc: cantools.Database) -> None:
    """Send one fast cycle via virtual bus and verify frames are received."""
    send_bus: can.BusABC = can.Bus(interface="virtual", channel="test_can_sim")
    recv_bus: can.BusABC = can.Bus(interface="virtual", channel="test_can_sim")

    try:
        rack: RackSimulator = RackSimulator(
            bus=send_bus,
            db=dbc,
            rack_index=0,
            cluster_index=0,
            base_can_id=BASE_CAN_ID,
            fast_cycle_s=0.3,
            slow_cycle_s=2.0,
            num_cells=16,
            num_temps=8,
        )
        rack._send_fast_cycle()

        # Receive PackSummary (first frame sent)
        msg: can.Message | None = recv_bus.recv(timeout=1.0)
        assert msg is not None
        assert msg.arbitration_id == BASE_CAN_ID  # rack 0, offset 0x00
        assert msg.is_extended_id is True

        # Decode and verify
        decoded: dict = dbc.decode_message("PackSummary", msg.data)
        assert "pack_v" in decoded
        assert "pack_i" in decoded
        assert "pack_soc" in decoded

        # Receive cell voltage frames (should be 4 groups for 16 cells)
        for group_idx in range(4):
            cv_msg: can.Message | None = recv_bus.recv(timeout=1.0)
            assert cv_msg is not None
            expected_id: int = BASE_CAN_ID + group_idx + 1
            assert cv_msg.arbitration_id == expected_id
    finally:
        send_bus.shutdown()
        recv_bus.shutdown()
