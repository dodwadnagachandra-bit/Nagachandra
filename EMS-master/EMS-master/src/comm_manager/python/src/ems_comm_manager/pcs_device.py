"""PcsDevice -- Modbus device subclass for the PCS inverter.

Maps PCS register values to RTDB EmsPcs fields via seqlock write.
Also implements write_setpoint() and process_command() for M2 command path.
"""

from __future__ import annotations

import ctypes
import logging

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusIOException

from ems_comm_manager.modbus_device import ModbusDevice
from ems_comm_manager.register_map import scale_value

# Device type constants
DEVICE_PCS: str = "pcs"
PCS_PRIORITY: int = 0  # Highest priority (polled first)

# PCS command enum values (must match PCS_CMD_* in rtdb.h)
PCS_CMD_NONE: int = 0
PCS_CMD_ON: int = 1
PCS_CMD_OFF: int = 2
PCS_CMD_FAULT_RESET: int = 3

# PCS V1.24 register addresses for command writes
_REG_PCS_POWER_SETPOINT: int = 0x500E   # Active power setpoint (scale x10, signed int16)
_REG_PCS_ON_OFF: int = 0x0291           # Start/stop register (1=start, 0=stop)
_REG_PCS_FAULT_RESET: int = 0x5064     # Fault reset register (write 1 to reset)

logger: logging.Logger = logging.getLogger(__name__)

# Maximum seqlock retry attempts before giving up on a section
_SEQLOCK_MAX_RETRIES: int = 100


def _seqlock_read_section(section: ctypes.Structure) -> ctypes.Structure:
    """Read an RTDB section using the seqlock pattern.

    Copies section data into a local buffer while the seqlock sequence is
    even and stable. Returns a fresh ctypes instance (not aliased to shm).

    Args:
        section: A ctypes Structure with a .lock.sequence field.

    Returns:
        A copy of the section as a new ctypes instance of the same type.
    """
    section_type: type = type(section)
    copy: ctypes.Structure = section_type()

    for _ in range(_SEQLOCK_MAX_RETRIES):
        seq1: int = section.lock.sequence
        if seq1 & 1:
            # Odd sequence = write in progress, retry
            continue

        ctypes.memmove(
            ctypes.addressof(copy),
            ctypes.addressof(section),
            ctypes.sizeof(section_type),
        )

        seq2: int = section.lock.sequence
        if seq1 == seq2:
            return copy

    logger.warning("Seqlock read exceeded max retries for %s", section_type.__name__)
    return copy


class PcsDevice(ModbusDevice):
    """PCS inverter Modbus device.

    Reads PCS telemetry registers and writes scaled values to the
    RTDB EmsPcs section via seqlock for thread-safe access.

    Args:
        device_id: Unique device identifier (default "pcs").
        slave_id: Modbus slave address.
        port: Serial port path.
        register_map_path: Path to PCS register map YAML.
        poll_interval_ms: Polling interval in milliseconds.
        timeout_s: Per-request timeout in seconds.
        push_socket: ZMQ PUSH socket for event publishing.
        rtdb: Attached RTDB object with .pcs field (EmsPcs struct).
    """

    def __init__(
        self,
        device_id: str = "pcs",
        slave_id: int = 1,
        port: str = "/dev/ttyUSB0",
        register_map_path: str = "config/pcs_register_map.yaml",
        poll_interval_ms: int = 500,
        timeout_s: float = 3.0,
        push_socket: object = None,
        rtdb: object = None,
        protocol: str = "rtu",
        host: str = "127.0.0.1",
        tcp_port: int = 502,
    ) -> None:
        super().__init__(
            device_id=device_id,
            device_type=DEVICE_PCS,
            slave_id=slave_id,
            port=port,
            register_map_path=register_map_path,
            poll_interval_ms=poll_interval_ms,
            timeout_s=timeout_s,
            priority=PCS_PRIORITY,
            push_socket=push_socket,
            protocol=protocol,
            host=host,
            tcp_port=tcp_port,
        )
        self._rtdb = rtdb
        self._last_cmd_seq: int = 0

    async def write_setpoint(self, client: AsyncModbusSerialClient | AsyncModbusTcpClient) -> None:
        """Write active power setpoint from RTDB to PCS register 0x500E.

        Reads system.active_setpoint_kw from RTDB via seqlock, converts to
        PCS V1.24 raw int16 (scale factor 10, two's complement for negative),
        and writes to register 0x500E via FC06.

        If the Modbus write fails, logs a warning and returns — a stale
        setpoint is safe (the PCS will continue at its last commanded value).

        Args:
            client: Connected pymodbus async serial client.
        """
        if self._rtdb is None:
            return

        from ems_common.rtdb import EmsSystem
        system: EmsSystem = _seqlock_read_section(self._rtdb.system)
        setpoint_kw: float = system.active_setpoint_kw

        # PCS V1.24 register 0x500E: signed int16, scale factor 10
        raw: int = int(round(setpoint_kw * 10))
        # Two's complement conversion for negative values
        if raw < 0:
            raw = raw + 65536
        # Clamp to valid uint16 range
        raw = max(0, min(65535, raw))

        try:
            await client.write_register(_REG_PCS_POWER_SETPOINT, raw, device_id=self.slave_id)
        except (ModbusIOException, Exception) as exc:
            logger.warning("Failed to write PCS power setpoint (%.1f kW, raw=%d): %s",
                           setpoint_kw, raw, exc)

    async def process_command(self, client: AsyncModbusSerialClient | AsyncModbusTcpClient) -> None:
        """Execute pending PCS command from RTDB command path.

        Reads system.pcs_command and system.pcs_command_seq from RTDB.
        Uses sequence number deduplication: only acts when seq has changed.
        Supports ON (reg 0x0291=1), OFF (reg 0x0291=0), and FAULT_RESET (reg 0x5064=1).

        Args:
            client: Connected pymodbus async serial client.
        """
        if self._rtdb is None:
            return

        from ems_common.rtdb import EmsSystem
        system: EmsSystem = _seqlock_read_section(self._rtdb.system)
        new_seq: int = system.pcs_command_seq
        cmd: int = system.pcs_command

        # Deduplication: act only when sequence has incremented (uint32 wrap-safe)
        if (new_seq - self._last_cmd_seq) & 0xFFFFFFFF == 0:
            return

        self._last_cmd_seq = new_seq

        if cmd == PCS_CMD_ON:
            logger.info("PCS command: ON (seq=%d)", new_seq)
            await client.write_register(_REG_PCS_ON_OFF, 1, device_id=self.slave_id)
        elif cmd == PCS_CMD_OFF:
            logger.info("PCS command: OFF (seq=%d)", new_seq)
            await client.write_register(_REG_PCS_ON_OFF, 0, device_id=self.slave_id)
        elif cmd == PCS_CMD_FAULT_RESET:
            logger.info("PCS command: FAULT_RESET (seq=%d)", new_seq)
            await client.write_register(_REG_PCS_FAULT_RESET, 1, device_id=self.slave_id)
        else:
            logger.warning("Unknown PCS command value %d (seq=%d) — ignored", cmd, new_seq)

    def _write_to_rtdb(self, raw_values: list[int], now_ms: int) -> None:
        """Write decoded PCS register values to RTDB EmsPcs via seqlock.

        Maps each register with an rtdb_field to the corresponding EmsPcs
        attribute. Uses seqlock write_begin/write_end for thread safety.

        Args:
            raw_values: Raw register values from poll().
            now_ms: Current monotonic timestamp in milliseconds.
        """
        if self._rtdb is None:
            return

        pcs = self._rtdb.pcs

        # Seqlock write begin: increment sequence to odd (write in progress)
        if hasattr(pcs, "lock"):
            pcs.lock.sequence += 1

        # Map register values to RTDB fields
        for i, reg in enumerate(self.register_map):
            if i >= len(raw_values):
                break
            if reg.rtdb_field is not None:
                scaled: float = scale_value(raw_values[i], reg)
                if hasattr(pcs, reg.rtdb_field):
                    setattr(pcs, reg.rtdb_field, scaled)

        pcs.last_update_ms = now_ms

        # Seqlock write end: increment sequence to even (write complete)
        if hasattr(pcs, "lock"):
            pcs.lock.sequence += 1
