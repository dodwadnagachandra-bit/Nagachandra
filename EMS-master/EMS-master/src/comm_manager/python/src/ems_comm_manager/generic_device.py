"""GenericDevice -- Modbus device subclass for BTMS, Meter, DG, PV.

Maps register values to the appropriate RTDB section via seqlock write.
Devices without a dedicated RTDB struct (DG, PV) skip RTDB writes and
log values only until RTDB is extended in a future phase.
"""

from __future__ import annotations

import ctypes
import logging

from ems_comm_manager.modbus_device import ModbusDevice
from ems_comm_manager.register_map import scale_value

logger = logging.getLogger(__name__)

# Priority constants per CONTEXT.md (PCS=0 is in pcs_device.py)
PRIORITY_METER: int = 1
PRIORITY_BTMS: int = 2
PRIORITY_DG: int = 3
PRIORITY_PV: int = 4


class GenericDevice(ModbusDevice):
    """Modbus device for BTMS, Meter, DG, and PV subsystems.

    Writes decoded register values to the named RTDB section via seqlock.
    If the RTDB object or section is unavailable, writes are silently
    skipped (log-only mode for DG/PV until RTDB is extended).

    Args:
        device_id: Unique device identifier (e.g., "btms", "meter").
        device_type: Device type string matching config section names.
        slave_id: Modbus slave address.
        port: Serial port path.
        register_map_path: Path to device register map YAML.
        poll_interval_ms: Polling interval in milliseconds.
        timeout_s: Per-request timeout in seconds.
        priority: Polling priority (lower = higher priority).
        push_socket: ZMQ PUSH socket for event publishing.
        rtdb: Attached RTDB object (EmsRtdb ctypes struct), or None.
        rtdb_section_name: RTDB section name ("btms", "meter", etc.).
    """

    def __init__(
        self,
        device_id: str,
        device_type: str,
        slave_id: int,
        port: str,
        register_map_path: str,
        poll_interval_ms: int,
        timeout_s: float,
        priority: int,
        push_socket: object,
        rtdb: object = None,
        rtdb_section_name: str = "",
        protocol: str = "rtu",
        host: str = "127.0.0.1",
        tcp_port: int = 502,
    ) -> None:
        super().__init__(
            device_id=device_id,
            device_type=device_type,
            slave_id=slave_id,
            port=port,
            register_map_path=register_map_path,
            poll_interval_ms=poll_interval_ms,
            timeout_s=timeout_s,
            priority=priority,
            push_socket=push_socket,
            protocol=protocol,
            host=host,
            tcp_port=tcp_port,
        )
        self._rtdb = rtdb
        self._rtdb_section_name: str = rtdb_section_name

    def _write_to_rtdb(self, raw_values: list[int], now_ms: int) -> None:
        """Write decoded register values to the named RTDB section via seqlock.

        Uses register ``name`` as the RTDB struct field name (register maps
        are designed with names matching ctypes struct fields).

        If rtdb is None or the named section does not exist on the RTDB
        object, this method returns without error (log-only mode).

        Args:
            raw_values: Raw register values from poll().
            now_ms: Current monotonic timestamp in milliseconds.
        """
        if self._rtdb is None:
            return

        section = getattr(self._rtdb, self._rtdb_section_name, None)
        if section is None:
            return

        # Seqlock write begin: increment sequence to odd (write in progress)
        if hasattr(section, "lock"):
            section.lock.sequence += 1

        # Map register values to RTDB fields using register name
        for i, reg in enumerate(self.register_map):
            if i >= len(raw_values):
                break
            field_name: str = reg.rtdb_field if reg.rtdb_field is not None else reg.name
            if hasattr(section, field_name):
                scaled: float = scale_value(raw_values[i], reg)
                try:
                    setattr(section, field_name, scaled)
                except TypeError:
                    # ctypes integer fields (c_uint8, c_int) require int
                    setattr(section, field_name, int(scaled))

        section.last_update_ms = now_ms

        # Seqlock write end: increment sequence to even (write complete)
        if hasattr(section, "lock"):
            section.lock.sequence += 1
