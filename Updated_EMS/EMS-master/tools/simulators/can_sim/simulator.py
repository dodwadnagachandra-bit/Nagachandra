"""CANSimulator -- top-level orchestrator for BMU CAN bus simulation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import can
import cantools
import yaml

from tools.simulators.can_sim.rack import RackSimulator

log: logging.Logger = logging.getLogger(__name__)

# Mask to extract 29-bit CAN ID from config (which may include DBC extended flag)
_CAN_ID_29BIT_MASK: int = 0x1FFFFFFF


class CANSimulator:
    """Orchestrates multiple RackSimulators on a shared CAN bus."""

    def __init__(
        self,
        interface: str,
        config_path: str,
        system_config_path: str | None = None,
        num_racks: int | None = None,
        verbose: bool = False,
    ) -> None:
        with open(config_path) as f:
            bms_cfg: dict = yaml.safe_load(f)

        self.interface: str = interface
        self.base_can_id: int = bms_cfg["can"]["base_id"] & _CAN_ID_29BIT_MASK
        self.dbc_path: str = bms_cfg["can"]["dbc_path"]
        self.fast_cycle_s: float = bms_cfg["timing"]["fast_cycle_ms"] / 1000.0
        self.slow_cycle_s: float = bms_cfg["timing"]["slow_cycle_ms"] / 1000.0
        self.verbose: bool = verbose

        self.db: cantools.Database = cantools.database.load_file(self.dbc_path)

        # Determine topology
        self.cluster_count: int = 1
        self.racks_per_cluster: int = 4

        if system_config_path and Path(system_config_path).exists():
            with open(system_config_path) as f:
                sys_cfg: dict = yaml.safe_load(f)
            topo: dict = sys_cfg.get("topology", {})
            self.cluster_count = topo.get("cluster_count", 1)
            self.racks_per_cluster = topo.get("racks_per_cluster", 4)

        if num_racks is not None:
            self.cluster_count = 1
            self.racks_per_cluster = num_racks

        # Optional fault injection and signal tuning from config
        self._fault_cfg: dict = bms_cfg.get("fault_injection", {})
        self._tuning_cfg: dict = bms_cfg.get("signal_tuning", {})

        if self._fault_cfg:
            log.info(
                "FAULT: %s",
                ", ".join(f"{k}={v}" for k, v in self._fault_cfg.items()),
            )
        if self._tuning_cfg:
            log.info(
                "TUNING: %s",
                ", ".join(f"{k}={v}" for k, v in self._tuning_cfg.items()),
            )

        self.bus: can.BusABC | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def run(self) -> None:
        """Create bus, spawn rack simulators, run until cancelled."""
        self.bus = can.Bus(interface="socketcan", channel=self.interface)
        try:
            total: int = 0
            for cluster_idx in range(self.cluster_count):
                for rack_idx in range(self.racks_per_cluster):
                    rack: RackSimulator = RackSimulator(
                        bus=self.bus,
                        db=self.db,
                        rack_index=rack_idx,
                        cluster_index=cluster_idx,
                        base_can_id=self.base_can_id,
                        fast_cycle_s=self.fast_cycle_s,
                        slow_cycle_s=self.slow_cycle_s,
                        verbose=self.verbose,
                        fault_cfg=self._fault_cfg if self._fault_cfg else None,
                        tuning_cfg=self._tuning_cfg if self._tuning_cfg else None,
                    )
                    self._tasks.append(asyncio.create_task(rack.run()))
                    total += 1

            log.info(
                "CAN Simulator: %d rack(s) on %s, fast=%dms slow=%dms",
                total,
                self.interface,
                int(self.fast_cycle_s * 1000),
                int(self.slow_cycle_s * 1000),
            )
            await asyncio.gather(*self._tasks)
        finally:
            if self.bus is not None:
                self.bus.shutdown()

    def stop(self) -> None:
        """Cancel all running tasks for clean shutdown."""
        for task in self._tasks:
            task.cancel()
