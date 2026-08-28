"""Section staleness monitor at 1Hz.

Checks each RTDB section's last_update_ms against CLOCK_MONOTONIC.
Publishes stale warnings on the telemetry socket with topic "system.health".
Per locked decision: zero last_update_ms means "no data received yet" -- do NOT warn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import zmq
import zmq.asyncio

from ems_common.ipc import (
    encode_event,
    SEVERITY_WARNING,
)
from ems_common.rtdb import EmsRtdb

logger = logging.getLogger(__name__)

# Sections to monitor: (name, accessor_function)
_SECTION_NAMES: list[str] = ["pcs", "gpio", "meter", "btms", "system"]


class HealthMonitor:
    """Monitors RTDB section staleness and publishes warnings.

    Does NOT own the PUB socket -- receives a shared socket from
    TelemetryPublisher via constructor injection. Does not bind or
    close the socket.

    Args:
        rtdb: Attached EmsRtdb ctypes struct (mapped to shm).
        pub_socket: Shared ZMQ PUB socket (owned by TelemetryPublisher).
        stale_threshold_ms: Milliseconds after which a section is considered stale.
    """

    def __init__(
        self,
        rtdb: EmsRtdb,
        pub_socket: zmq.Socket | zmq.asyncio.Socket,
        stale_threshold_ms: int = 5000,
    ) -> None:
        self._rtdb = rtdb
        self._stale_threshold_ms = stale_threshold_ms
        self._pub = pub_socket

    def _get_now_ms(self) -> int:
        """Get current CLOCK_MONOTONIC in milliseconds."""
        return time.monotonic_ns() // 1_000_000

    def check_once(self) -> list[str]:
        """Check all sections for staleness once. Returns list of stale section names."""
        now_ms = self._get_now_ms()
        stale_sections: list[str] = []

        # Check non-BMS sections
        for name in _SECTION_NAMES:
            section = getattr(self._rtdb, name)
            last_ms: int = section.last_update_ms
            if last_ms == 0:
                # No data received yet -- do not warn
                continue
            if (now_ms - last_ms) > self._stale_threshold_ms:
                stale_sections.append(name)
                self._publish_stale_warning(name, last_ms, now_ms)

        # Check BMS racks
        cluster_count = self._rtdb.cluster_count
        racks_per_cluster = self._rtdb.racks_per_cluster
        for c in range(cluster_count):
            for r in range(racks_per_cluster):
                rack = self._rtdb.clusters[c].racks[r]
                last_ms = rack.last_update_ms
                if last_ms == 0:
                    continue
                if (now_ms - last_ms) > self._stale_threshold_ms:
                    rack_name = f"bms.rack.{c}.{r}"
                    stale_sections.append(rack_name)
                    self._publish_stale_warning(rack_name, last_ms, now_ms)

        return stale_sections

    def _publish_stale_warning(self, section: str, last_ms: int, now_ms: int) -> None:
        """Publish a stale warning event for a section."""
        age_ms = now_ms - last_ms
        ts = time.monotonic_ns() // 1_000_000
        event_data: dict[str, Any] = {
            "section": section,
            "last_update_ms": last_ms,
            "age_ms": age_ms,
            "threshold_ms": self._stale_threshold_ms,
        }
        msg = encode_event(
            timestamp_ms=ts,
            source="data_manager",
            severity=SEVERITY_WARNING,
            event_type="stale_section",
            message=f"RTDB section '{section}' is stale (age: {age_ms}ms, threshold: {self._stale_threshold_ms}ms)",
            data=event_data,
        )
        topic = "system.health"
        self._pub.send_string(topic, zmq.SNDMORE)
        self._pub.send(msg)
        logger.warning("Stale section: %s (age: %dms)", section, age_ms)

    async def monitor_loop(self) -> None:
        """Check all sections for staleness at 1Hz indefinitely."""
        while True:
            self.check_once()
            await asyncio.sleep(1.0)

    def close(self) -> None:
        """No-op -- HealthMonitor does not own the PUB socket."""
        pass
