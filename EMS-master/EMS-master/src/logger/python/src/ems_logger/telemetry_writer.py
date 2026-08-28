"""Parquet telemetry writer with hourly rotation and ZMQ SUB consumer.

ParquetRotatingWriter manages a single Parquet file stream with atomic
.tmp -> .parquet rename on rotation/close. TelemetryWriter subscribes
to the ZMQ PUB telemetry socket and routes topics to per-cluster or
system Parquet writers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import msgpack
import pyarrow as pa
import pyarrow.parquet as pq
import zmq
import zmq.asyncio

from ems_logger.parquet_schema import (
    build_cluster_schema,
    build_system_schema,
    build_topology_metadata,
)

logger: logging.Logger = logging.getLogger(__name__)


class ParquetRotatingWriter:
    """Manages a single Parquet file with hourly rotation and atomic rename.

    Files are written with a .tmp extension and renamed to .parquet on
    rotation or close, ensuring no partial files are visible to readers.

    Directory structure: data_dir/{year}/{month:02d}/{day:02d}/{prefix}_{hour:02d}.parquet

    Args:
        schema: PyArrow schema for the Parquet file.
        data_dir: Base data directory path.
        file_prefix: Filename prefix (e.g. "telemetry_0", "telemetry_system").
        metadata: Dict of string key-value pairs stored in Parquet file metadata.
    """

    def __init__(
        self,
        schema: pa.Schema,
        data_dir: Path,
        file_prefix: str,
        metadata: dict[str, str],
    ) -> None:
        self._schema: pa.Schema = schema
        self._data_dir: Path = Path(data_dir)
        self._file_prefix: str = file_prefix
        self._metadata: dict[str, str] = metadata

        # Merge user metadata with schema metadata
        existing_meta: dict[bytes, bytes] = dict(schema.metadata or {})
        for k, v in metadata.items():
            existing_meta[k.encode()] = v.encode()
        self._full_schema: pa.Schema = schema.with_metadata(existing_meta)

        self._writer: pq.ParquetWriter | None = None
        self._current_hour: int | None = None
        self._current_tmp_path: Path | None = None
        self._current_final_path: Path | None = None

    def write_row(self, row_dict: dict) -> None:
        """Write a single row to the Parquet file.

        Determines the hour from row_dict["ts"] (ms since epoch) and
        opens/rotates the file as needed.

        Args:
            row_dict: Dict with keys matching schema field names. Must include "ts".
        """
        ts_ms: int = row_dict["ts"]
        dt: datetime = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        hour: int = dt.hour

        # Check if we need to rotate
        if self._current_hour is not None and hour != self._current_hour:
            self._close_current()

        # Open new file if needed
        if self._writer is None:
            self._open_new(ts_ms)

        # Write one row as a RecordBatch
        batch: pa.RecordBatch = pa.RecordBatch.from_pydict(
            {name: [row_dict[name]] for name in self._full_schema.names},
            schema=self._full_schema,
        )
        self._writer.write_batch(batch)

    def _open_new(self, ts_ms: int) -> None:
        """Open a new Parquet file for the hour containing ts_ms.

        Creates the day directory if needed. If a stale .tmp file exists
        from a previous crash, it is deleted first.

        Args:
            ts_ms: Timestamp in milliseconds since epoch.
        """
        dt: datetime = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        self._current_hour = dt.hour

        day_dir: Path = (
            self._data_dir
            / str(dt.year)
            / f"{dt.month:02d}"
            / f"{dt.day:02d}"
        )
        day_dir.mkdir(parents=True, exist_ok=True)

        filename: str = f"{self._file_prefix}_{dt.hour:02d}"
        self._current_tmp_path = day_dir / f"{filename}.tmp"
        self._current_final_path = day_dir / f"{filename}.parquet"

        # Remove stale .tmp from crash
        if self._current_tmp_path.exists():
            logger.warning("Removing stale .tmp file: %s", self._current_tmp_path)
            os.remove(self._current_tmp_path)

        self._writer = pq.ParquetWriter(
            str(self._current_tmp_path),
            self._full_schema,
            compression="snappy",
        )
        logger.info("Opened Parquet writer: %s", self._current_tmp_path)

    def _close_current(self) -> None:
        """Close current writer and atomically rename .tmp -> .parquet."""
        if self._writer is None:
            return

        self._writer.close()
        self._writer = None

        if self._current_tmp_path is not None and self._current_final_path is not None:
            os.rename(self._current_tmp_path, self._current_final_path)
            logger.info("Rotated: %s -> %s", self._current_tmp_path, self._current_final_path)

        self._current_tmp_path = None
        self._current_final_path = None
        self._current_hour = None

    def close(self) -> None:
        """Finalize and close the current file with atomic rename."""
        self._close_current()


# Topic prefix for BMS rack messages
_BMS_RACK_PREFIX: str = "bms.rack."

# System-level topics that go to the system Parquet file
_SYSTEM_TOPICS: frozenset[str] = frozenset({"pcs", "gpio", "meter", "btms", "system"})

# Collect window timeout in seconds
_COLLECT_WINDOW_S: float = 0.05  # Short for testing; production uses run() loop timing


class TelemetryWriter:
    """Subscribes to ZMQ PUB telemetry and writes Parquet files.

    Routes BMS rack topics to per-cluster writers and system topics
    (PCS, GPIO, meter, BTMS, system) to a single system writer.
    Buffers all messages within a 1-second window, then writes one row
    per active writer.

    Args:
        config: LoggerConfig with storage.data_dir and parquet settings.
        zmq_ctx: ZMQ async context.
        topology: Dict with cluster_count, racks_per_cluster, modules_per_rack,
                  cells_per_module, temps_per_module.
        endpoint: Optional ZMQ endpoint override (for testing).
    """

    def __init__(
        self,
        config: Any,  # LoggerConfig (duck-typed for testability)
        zmq_ctx: zmq.asyncio.Context,
        topology: dict[str, int],
        endpoint: str | None = None,
    ) -> None:
        self._config = config
        self._topology = topology
        self._data_dir: Path = Path(config.storage.data_dir)

        # Build topology metadata
        topo_meta: dict[str, str] = build_topology_metadata(
            cluster_count=topology["cluster_count"],
            racks_per_cluster=topology["racks_per_cluster"],
            modules_per_rack=topology["modules_per_rack"],
            cells_per_module=topology["cells_per_module"],
        )

        # Create per-cluster writers
        self._cluster_writers: dict[int, ParquetRotatingWriter] = {}
        for c in range(topology["cluster_count"]):
            cluster_schema: pa.Schema = build_cluster_schema(
                racks_per_cluster=topology["racks_per_cluster"],
                modules_per_rack=topology["modules_per_rack"],
                cells_per_module=topology["cells_per_module"],
                temps_per_module=topology["temps_per_module"],
            )
            self._cluster_writers[c] = ParquetRotatingWriter(
                schema=cluster_schema,
                data_dir=self._data_dir,
                file_prefix=f"telemetry_{c}",
                metadata=topo_meta,
            )

        # Create system writer
        system_schema: pa.Schema = build_system_schema()
        self._system_writer: ParquetRotatingWriter = ParquetRotatingWriter(
            schema=system_schema,
            data_dir=self._data_dir,
            file_prefix="telemetry_system",
            metadata=topo_meta,
        )

        # ZMQ SUB socket
        self._sub: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)
        zmq_endpoint: str = endpoint or "ipc:///run/ems/telemetry.sock"
        self._sub.connect(zmq_endpoint)

        # Subscribe to all telemetry topics
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "bms.rack")
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "pcs")
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "gpio")
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "meter")
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "btms")
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "system")

    async def _collect_window(self, timeout_s: float = _COLLECT_WINDOW_S) -> dict[str, dict]:
        """Receive all ZMQ messages within a time window.

        Returns a dict mapping topic string to decoded payload dict.
        Multiple messages on the same topic: last one wins.

        Args:
            timeout_s: Time window in seconds to collect messages.

        Returns:
            Dict of {topic: payload} collected during the window.
        """
        collected: dict[str, dict] = {}
        deadline: float = asyncio.get_event_loop().time() + timeout_s

        while True:
            remaining: float = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break

            try:
                # Poll with remaining time
                events = await self._sub.poll(timeout=int(remaining * 1000))
                if not events:
                    break

                parts = await self._sub.recv_multipart()
                if len(parts) != 2:
                    continue

                topic_bytes, envelope_bytes = parts
                topic: str = topic_bytes.decode("utf-8")
                envelope: dict = msgpack.unpackb(envelope_bytes, raw=False)
                collected[topic] = envelope

            except zmq.ZMQError:
                break

        return collected

    def _build_cluster_row(
        self,
        cluster_idx: int,
        rack_payloads: dict[int, dict],
        ts_ms: int,
    ) -> dict[str, Any]:
        """Build a row dict for a cluster Parquet file from rack payloads.

        Args:
            cluster_idx: Cluster index.
            rack_payloads: Dict mapping rack index to payload dict.
            ts_ms: Timestamp in ms since epoch.

        Returns:
            Dict with keys matching cluster schema field names.
        """
        racks: int = self._topology["racks_per_cluster"]
        modules: int = self._topology["modules_per_rack"]
        cells: int = self._topology["cells_per_module"]
        temps: int = self._topology["temps_per_module"]

        row: dict[str, Any] = {"ts": ts_ms}

        for r in range(racks):
            prefix: str = f"rack{r}_"
            payload: dict = rack_payloads.get(r, {})

            row[f"{prefix}pack_v"] = float(payload.get("pack_v", 0.0))
            row[f"{prefix}pack_i"] = float(payload.get("pack_i", 0.0))
            row[f"{prefix}soc"] = float(payload.get("soc", 0.0))
            row[f"{prefix}soh"] = float(payload.get("soh", 0.0))
            row[f"{prefix}min_cell_v"] = float(payload.get("min_cell_v", 0.0))
            row[f"{prefix}max_cell_v"] = float(payload.get("max_cell_v", 0.0))
            row[f"{prefix}avg_cell_v"] = float(payload.get("avg_cell_v", 0.0))
            row[f"{prefix}min_cell_t"] = float(payload.get("min_cell_t", 0.0))
            row[f"{prefix}max_cell_t"] = float(payload.get("max_cell_t", 0.0))
            row[f"{prefix}avg_cell_t"] = float(payload.get("avg_cell_t", 0.0))
            row[f"{prefix}fault_code"] = int(payload.get("fault_code", 0))
            row[f"{prefix}online"] = int(payload.get("online", 0))

            for m in range(modules):
                mod_prefix: str = f"{prefix}mod{m}_"
                row[f"{mod_prefix}cell_v"] = payload.get(
                    f"mod{m}_cell_v", [0.0] * cells
                )
                row[f"{mod_prefix}cell_t"] = payload.get(
                    f"mod{m}_cell_t", [0.0] * temps
                )

        return row

    def _build_system_row(
        self,
        section_payloads: dict[str, dict],
        ts_ms: int,
    ) -> dict[str, Any]:
        """Build a row dict for the system Parquet file.

        Args:
            section_payloads: Dict mapping section name to payload dict.
            ts_ms: Timestamp in ms since epoch.

        Returns:
            Dict with keys matching system schema field names.
        """
        pcs: dict = section_payloads.get("pcs", {})
        meter: dict = section_payloads.get("meter", {})
        btms: dict = section_payloads.get("btms", {})
        gpio: dict = section_payloads.get("gpio", {})
        system: dict = section_payloads.get("system", {})

        return {
            "ts": ts_ms,
            # PCS
            "pcs_ac_voltage": float(pcs.get("ac_voltage", 0.0)),
            "pcs_ac_current": float(pcs.get("ac_current", 0.0)),
            "pcs_active_power": float(pcs.get("active_power", 0.0)),
            "pcs_reactive_power": float(pcs.get("reactive_power", 0.0)),
            "pcs_dc_voltage": float(pcs.get("dc_voltage", 0.0)),
            "pcs_dc_current": float(pcs.get("dc_current", 0.0)),
            "pcs_frequency": float(pcs.get("frequency", 0.0)),
            "pcs_temperature": float(pcs.get("temperature", 0.0)),
            "pcs_state": int(pcs.get("state", 0)),
            "pcs_fault_code": int(pcs.get("fault_code", 0)),
            # Meter
            "meter_voltage": float(meter.get("voltage", 0.0)),
            "meter_current": float(meter.get("current", 0.0)),
            "meter_active_power": float(meter.get("active_power", 0.0)),
            "meter_reactive_power": float(meter.get("reactive_power", 0.0)),
            "meter_frequency": float(meter.get("frequency", 0.0)),
            "meter_power_factor": float(meter.get("power_factor", 0.0)),
            "meter_energy_import": float(meter.get("energy_import", 0.0)),
            "meter_energy_export": float(meter.get("energy_export", 0.0)),
            # BTMS
            "btms_inlet_temp": float(btms.get("inlet_temp", 0.0)),
            "btms_outlet_temp": float(btms.get("outlet_temp", 0.0)),
            "btms_fan_speed_pct": float(btms.get("fan_speed_pct", 0.0)),
            "btms_cooling_active": int(btms.get("cooling_active", 0)),
            # GPIO
            "gpio_di": list(gpio.get("di", [0] * 8)),
            "gpio_do": list(gpio.get("do_state", [0] * 8)),
            # System
            "system_control_state": int(system.get("control_state", 0)),
            "system_source_priority": int(system.get("source_priority", 0)),
            "system_active_setpoint_kw": float(system.get("active_setpoint_kw", 0.0)),
            "system_total_soc": float(system.get("total_soc", 0.0)),
            "system_total_power_kw": float(system.get("total_power_kw", 0.0)),
            "system_total_energy_kwh": float(system.get("total_energy_kwh", 0.0)),
            "system_ems_uptime_s": int(system.get("ems_uptime_s", 0)),
        }

    async def collect_and_write_once(self) -> None:
        """Collect one window of ZMQ messages and write rows.

        This is the single-iteration equivalent of the run() loop,
        suitable for testing without an infinite loop.
        """
        collected: dict[str, dict] = await self._collect_window()

        if not collected:
            return

        # Determine timestamp from first message
        ts_ms: int = 0
        for envelope in collected.values():
            ts_ms = envelope.get("ts", 0)
            break

        # Route BMS rack topics to cluster writers
        cluster_racks: dict[int, dict[int, dict]] = {}
        has_system_data: bool = False
        system_sections: dict[str, dict] = {}

        for topic, envelope in collected.items():
            payload: dict = envelope.get("payload", {})

            if topic.startswith(_BMS_RACK_PREFIX):
                # Parse "bms.rack.{c}.{r}"
                parts: list[str] = topic.split(".")
                if len(parts) == 4:
                    c: int = int(parts[2])
                    r: int = int(parts[3])
                    if c not in cluster_racks:
                        cluster_racks[c] = {}
                    cluster_racks[c][r] = payload
            elif topic in _SYSTEM_TOPICS:
                system_sections[topic] = payload
                has_system_data = True

        # Write cluster rows
        for c, rack_payloads in cluster_racks.items():
            if c in self._cluster_writers:
                row: dict[str, Any] = self._build_cluster_row(c, rack_payloads, ts_ms)
                self._cluster_writers[c].write_row(row)

        # Write system row
        if has_system_data:
            sys_row: dict[str, Any] = self._build_system_row(system_sections, ts_ms)
            self._system_writer.write_row(sys_row)

    async def run(self) -> None:
        """Main loop: collect 1-second windows and write Parquet rows."""
        logger.info("TelemetryWriter started")
        try:
            while True:
                await self.collect_and_write_once()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("TelemetryWriter cancelled")
        finally:
            self.close()

    def close(self) -> None:
        """Close all writers and the ZMQ socket."""
        for writer in self._cluster_writers.values():
            writer.close()
        self._system_writer.close()

        if not self._sub.closed:
            self._sub.close()
        logger.info("TelemetryWriter closed")
