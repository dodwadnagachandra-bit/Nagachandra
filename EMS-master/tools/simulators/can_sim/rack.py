"""Per-rack CAN frame simulator with dual-rate async cycling."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

import can
import cantools

from tools.simulators.can_sim.signals import SignalGenerator

if TYPE_CHECKING:
    pass

log: logging.Logger = logging.getLogger(__name__)

# DBC message names (template messages, same for all racks)
MSG_PACK_SUMMARY: str = "PackSummary"
MSG_CELL_VOLTAGE: list[str] = [f"CellVoltage_{i:02d}" for i in range(1, 8)]
MSG_CELL_TEMPERATURE: str = "CellTemperature"
MSG_RACK_STATUS: str = "RackStatus"


class RackSimulator:
    """Simulates one BMU rack sending CAN frames at two rates."""

    def __init__(
        self,
        bus: can.BusABC,
        db: cantools.Database,
        rack_index: int,
        cluster_index: int,
        base_can_id: int,
        fast_cycle_s: float,
        slow_cycle_s: float,
        num_cells: int = 16,
        num_temps: int = 8,
        verbose: bool = False,
        fault_cfg: dict | None = None,
        tuning_cfg: dict | None = None,
    ) -> None:
        self.bus: can.BusABC = bus
        self.db: cantools.Database = db
        self.rack_index: int = rack_index
        self.cluster_index: int = cluster_index
        self.fast_cycle_s: float = fast_cycle_s
        self.slow_cycle_s: float = slow_cycle_s
        self.num_cells: int = num_cells
        self.num_temps: int = num_temps
        self.verbose: bool = verbose
        self.signals: SignalGenerator = SignalGenerator(rack_index, cluster_index, tuning=tuning_cfg)
        # CAN ID base for this rack: base + cluster * 0x1000 + rack * 0x10
        self.rack_base_id: int = base_can_id + cluster_index * 0x1000 + rack_index * 0x10

        # Fault injection configuration
        fc: dict = fault_cfg or {}
        self.frame_drop_rate: float = fc.get("frame_drop_rate", 0.0)
        self.corrupt_data: bool = fc.get("corrupt_data", False)
        self.corrupt_rate: float = fc.get("corrupt_rate", 0.02)
        self.stale_timeout_ms: int = fc.get("stale_timeout_ms", 0)
        self.stale_rack_index: int = fc.get("stale_rack_index", 0)

        # Compute stale suppression deadline
        if self.stale_timeout_ms > 0 and rack_index == self.stale_rack_index:
            self._stale_until: float = time.monotonic() + self.stale_timeout_ms / 1000.0
        else:
            self._stale_until: float = 0.0

    def _send_frame(self, msg_name: str, msg_offset: int, signals: dict[str, float | int]) -> None:
        """Encode signals via DBC and send on the CAN bus."""
        # Fault: stale rack suppression
        if self._stale_until > 0.0 and time.monotonic() < self._stale_until:
            return

        # Fault: frame drop
        if self.frame_drop_rate > 0 and random.random() < self.frame_drop_rate:
            log.debug("FAULT: dropped frame %s for rack %d/%d", msg_name, self.cluster_index, self.rack_index)
            return

        data: bytes = self.db.encode_message(msg_name, signals)

        # Fault: corrupt data bytes
        if self.corrupt_data and random.random() < self.corrupt_rate:
            data_mut: bytearray = bytearray(data)
            idx: int = random.randint(0, len(data_mut) - 1)
            data_mut[idx] = random.randint(0, 255)
            data = bytes(data_mut)
            log.debug("FAULT: corrupted byte %d in %s for rack %d/%d", idx, msg_name, self.cluster_index, self.rack_index)

        can_id: int = self.rack_base_id + msg_offset
        msg: can.Message = can.Message(
            arbitration_id=can_id,
            data=data,
            is_extended_id=True,
        )
        self.bus.send(msg)
        if self.verbose:
            log.info(
                "Rack %d/%d %s ID=0x%08X data=%s",
                self.cluster_index,
                self.rack_index,
                msg_name,
                can_id,
                data.hex(),
            )

    def _send_fast_cycle(self) -> None:
        """Send pack summary + cell voltage messages."""
        # Pack summary (offset 0x00)
        self._send_frame(
            MSG_PACK_SUMMARY,
            0x00,
            {
                "pack_v": self.signals.pack_voltage(),
                "pack_i": self.signals.pack_current(),
                "pack_soc": self.signals.pack_soc(),
                "pack_soh": self.signals.pack_soh(),
                "fault_code": 0,
            },
        )
        # Cell voltage groups (offsets 0x01-0x07)
        num_groups: int = (self.num_cells + 3) // 4  # ceil division
        for group_idx in range(min(num_groups, 7)):
            sig_dict: dict[str, float] = {}
            for cell_in_group in range(4):
                cell_num: int = group_idx * 4 + cell_in_group + 1
                sig_name: str = f"cell_v_{cell_num:02d}"
                if cell_num <= self.num_cells:
                    sig_dict[sig_name] = self.signals.cell_voltage(cell_num - 1)
                else:
                    sig_dict[sig_name] = 0.0
            self._send_frame(MSG_CELL_VOLTAGE[group_idx], group_idx + 1, sig_dict)

    def _send_slow_cycle(self) -> None:
        """Send cell temperature + rack status messages."""
        # Cell temperature (offset 0x08)
        temp_dict: dict[str, float] = {}
        for i in range(8):
            sig_name: str = f"cell_t_{i + 1:02d}"
            if i < self.num_temps:
                temp_dict[sig_name] = self.signals.cell_temperature(i)
            else:
                temp_dict[sig_name] = -40.0  # offset zero
        self._send_frame(MSG_CELL_TEMPERATURE, 0x08, temp_dict)

        # Rack status (offset 0x09)
        min_v: float
        max_v: float
        avg_v: float
        min_v, max_v, avg_v = self.signals.min_max_avg_cell_v(self.num_cells)
        self._send_frame(
            MSG_RACK_STATUS,
            0x09,
            {
                "online": 1,
                "balancing_count": 0,
                "min_cell_v": min_v,
                "max_cell_v": max_v,
                "avg_cell_v": avg_v,
            },
        )

    async def run_fast(self) -> None:
        """Drift-corrected periodic loop at fast cycle rate."""
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        next_time: float = loop.time() + self.fast_cycle_s
        while True:
            self._send_fast_cycle()
            now: float = loop.time()
            sleep_time: float = max(0, next_time - now)
            await asyncio.sleep(sleep_time)
            next_time += self.fast_cycle_s

    async def run_slow(self) -> None:
        """Drift-corrected periodic loop at slow cycle rate."""
        loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        next_time: float = loop.time() + self.slow_cycle_s
        while True:
            self._send_slow_cycle()
            now: float = loop.time()
            sleep_time: float = max(0, next_time - now)
            await asyncio.sleep(sleep_time)
            next_time += self.slow_cycle_s

    async def run(self) -> None:
        """Run both fast and slow cycles concurrently."""
        await asyncio.gather(self.run_fast(), self.run_slow())
