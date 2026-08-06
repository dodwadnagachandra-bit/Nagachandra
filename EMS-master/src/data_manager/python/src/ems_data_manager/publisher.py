"""1Hz ZMQ PUB telemetry loop reading RTDB via seqlock.

Publishes all RTDB sections (BMS racks, PCS, GPIO, meter, BTMS, system)
as MessagePack envelopes on ZMQ PUB socket at 1Hz.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import time
from typing import Any

import zmq
import zmq.asyncio

from ems_common.ipc import (
    SOCK_TELEMETRY,
    TOPIC_BMS_RACK,
    TOPIC_BTMS,
    TOPIC_GPIO,
    TOPIC_METER,
    TOPIC_PCS,
    TOPIC_SYSTEM,
    encode_telemetry,
)
from ems_common.rtdb import (
    EmsBtms,
    EmsCluster,
    EmsGpio,
    EmsMeter,
    EmsPcs,
    EmsRack,
    EmsRtdb,
    EmsSystem,
)

logger = logging.getLogger(__name__)

# Maximum seqlock retry attempts before giving up on a section
_SEQLOCK_MAX_RETRIES: int = 100


def _seqlock_read_section(section: ctypes.Structure) -> ctypes.Structure:
    """Read an RTDB section using the seqlock pattern with ctypes.memmove.

    Copies the section data into a local buffer while the seqlock sequence is
    even and stable. Returns a fresh ctypes instance (not aliased to shm).

    Args:
        section: A ctypes Structure with a .lock.sequence field.

    Returns:
        A copy of the section as a new ctypes instance of the same type.
    """
    section_type = type(section)
    copy = section_type()

    for _ in range(_SEQLOCK_MAX_RETRIES):
        seq1 = section.lock.sequence
        if seq1 & 1:
            # Odd = write in progress, retry
            continue

        # Copy entire section via memmove (minimizes time in critical section)
        ctypes.memmove(ctypes.addressof(copy), ctypes.addressof(section), ctypes.sizeof(section_type))

        seq2 = section.lock.sequence
        if seq1 == seq2:
            return copy

    # Fallback: return whatever we got (should be extremely rare)
    logger.warning("Seqlock read exceeded max retries for %s", section_type.__name__)
    return copy


def _rack_to_dict(rack: EmsRack) -> dict[str, Any]:
    """Extract rack telemetry fields to a dict."""
    return {
        "last_update_ms": rack.last_update_ms,
        "pack_v": rack.pack_v,
        "pack_i": rack.pack_i,
        "pack_soc": rack.pack_soc,
        "pack_soh": rack.pack_soh,
        "min_cell_v": rack.min_cell_v,
        "max_cell_v": rack.max_cell_v,
        "avg_cell_v": rack.avg_cell_v,
        "min_cell_t": rack.min_cell_t,
        "max_cell_t": rack.max_cell_t,
        "avg_cell_t": rack.avg_cell_t,
        "fault_code": rack.fault_code,
        "online": rack.online,
    }


def _pcs_to_dict(pcs: EmsPcs) -> dict[str, Any]:
    """Extract PCS telemetry fields to a dict."""
    return {
        "last_update_ms": pcs.last_update_ms,
        "ac_voltage": pcs.ac_voltage,
        "ac_current": pcs.ac_current,
        "active_power": pcs.active_power,
        "reactive_power": pcs.reactive_power,
        "dc_voltage": pcs.dc_voltage,
        "dc_current": pcs.dc_current,
        "frequency": pcs.frequency,
        "temperature": pcs.temperature,
        "state": pcs.state,
        "fault_code": pcs.fault_code,
    }


def _gpio_to_dict(gpio: EmsGpio) -> dict[str, Any]:
    """Extract GPIO state to a dict."""
    return {
        "last_update_ms": gpio.last_update_ms,
        "di": list(gpio.di),
        "do_state": list(gpio.do_state),
    }


def _meter_to_dict(meter: EmsMeter) -> dict[str, Any]:
    """Extract meter readings to a dict."""
    return {
        "last_update_ms": meter.last_update_ms,
        "voltage": meter.voltage,
        "current": meter.current,
        "active_power": meter.active_power,
        "reactive_power": meter.reactive_power,
        "frequency": meter.frequency,
        "power_factor": meter.power_factor,
        "energy_import": meter.energy_import,
        "energy_export": meter.energy_export,
    }


def _btms_to_dict(btms: EmsBtms) -> dict[str, Any]:
    """Extract BTMS telemetry to a dict."""
    return {
        "last_update_ms": btms.last_update_ms,
        "inlet_temp": btms.inlet_temp,
        "outlet_temp": btms.outlet_temp,
        "fan_speed_pct": btms.fan_speed_pct,
        "cooling_active": btms.cooling_active,
    }


def _system_to_dict(system: EmsSystem) -> dict[str, Any]:
    """Extract system-level aggregates to a dict."""
    return {
        "last_update_ms": system.last_update_ms,
        "control_state": system.control_state,
        "source_priority": system.source_priority,
        "active_setpoint_kw": system.active_setpoint_kw,
        "total_soc": system.total_soc,
        "total_power_kw": system.total_power_kw,
        "total_energy_kwh": system.total_energy_kwh,
        "ems_uptime_s": system.ems_uptime_s,
        "pcs_command": system.pcs_command,
        "pcs_command_seq": system.pcs_command_seq,
        "active_derating_pct": system.active_derating_pct,
    }


class TelemetryPublisher:
    """Reads all RTDB sections via seqlock and publishes on ZMQ PUB.

    Args:
        rtdb: Attached EmsRtdb ctypes struct (mapped to shm).
        zmq_ctx: ZMQ Context (sync or async).
        endpoint: Optional override for PUB socket bind address.
    """

    def __init__(
        self,
        rtdb: EmsRtdb,
        zmq_ctx: zmq.Context | zmq.asyncio.Context,
        endpoint: str | None = None,
    ) -> None:
        self._rtdb = rtdb
        self._seq: int = 0
        self._endpoint = endpoint or SOCK_TELEMETRY

        self._pub = zmq_ctx.socket(zmq.PUB)
        self._pub.bind(self._endpoint)

    @property
    def endpoint(self) -> str:
        """Return the PUB socket endpoint."""
        return self._endpoint

    @property
    def pub_socket(self) -> zmq.Socket:
        """Return the PUB socket for sharing with other components."""
        return self._pub

    def publish_once(self) -> None:
        """Publish all RTDB sections once (synchronous)."""
        ts = time.monotonic_ns() // 1_000_000
        rtdb = self._rtdb

        # BMS racks
        cluster_count = rtdb.cluster_count
        racks_per_cluster = rtdb.racks_per_cluster
        for c in range(cluster_count):
            for r in range(racks_per_cluster):
                rack_copy = _seqlock_read_section(rtdb.clusters[c].racks[r])
                topic = f"{TOPIC_BMS_RACK}.{c}.{r}"
                payload = _rack_to_dict(rack_copy)
                self._send(topic, ts, payload)

        # PCS
        pcs_copy = _seqlock_read_section(rtdb.pcs)
        self._send(TOPIC_PCS, ts, _pcs_to_dict(pcs_copy))

        # GPIO
        gpio_copy = _seqlock_read_section(rtdb.gpio)
        self._send(TOPIC_GPIO, ts, _gpio_to_dict(gpio_copy))

        # Meter
        meter_copy = _seqlock_read_section(rtdb.meter)
        self._send(TOPIC_METER, ts, _meter_to_dict(meter_copy))

        # BTMS
        btms_copy = _seqlock_read_section(rtdb.btms)
        self._send(TOPIC_BTMS, ts, _btms_to_dict(btms_copy))

        # System
        system_copy = _seqlock_read_section(rtdb.system)
        self._send(TOPIC_SYSTEM, ts, _system_to_dict(system_copy))

    def _send(self, topic: str, ts: int, payload: dict[str, Any]) -> None:
        """Send a multipart ZMQ message: [topic, encoded_envelope]."""
        self._seq += 1
        envelope = encode_telemetry(ts, self._seq, "data_manager", topic, payload)
        self._pub.send_string(topic, zmq.SNDMORE)
        self._pub.send(envelope)

    async def publish_loop(self) -> None:
        """Publish all RTDB sections at 1Hz indefinitely."""
        while True:
            self.publish_once()
            await asyncio.sleep(1.0)

    def close(self) -> None:
        """Close the PUB socket."""
        if not self._pub.closed:
            self._pub.close()
