"""RTDB shared memory backend -- reads/writes GPIO pins via POSIX shm."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from multiprocessing import resource_tracker
from multiprocessing.shared_memory import SharedMemory

from ems_common.rtdb import RTDB_MAGIC, RTDB_VERSION, EmsRtdb

from tools.simulators.gpio_harness.backend import GpioBackend

log: logging.Logger = logging.getLogger(__name__)


class RtdbBackend(GpioBackend):
    """GPIO backend that operates on the RTDB ems_gpio_t struct in shared memory.

    Supports seqlock-based atomic reads and writes for multi-pin operations.
    Creates the shm segment if it does not exist (standalone testing mode).
    """

    def __init__(self, shm_name: str = "ems_rtdb", fault_cfg: dict | None = None) -> None:
        self._shm_name: str = shm_name
        self._created: bool = False
        self._closed: bool = False

        # Fault injection
        fc: dict = fault_cfg or {}
        self._stuck_pins: set[int] = set(fc.get("stuck_pins", []))
        self._bounce_ms: int = fc.get("bounce_ms", 0)
        if self._stuck_pins:
            log.info("FAULT: stuck_pins=%s", sorted(self._stuck_pins))
        if self._bounce_ms > 0:
            log.info("FAULT: bounce_ms=%d", self._bounce_ms)

        # Try to attach to existing shm, or create new.
        # track=False on attach prevents Python resource_tracker from
        # unlinking shm we didn't create (critical for subprocess CLI usage).
        try:
            self._shm: SharedMemory = SharedMemory(
                name=shm_name, create=False, size=ctypes.sizeof(EmsRtdb),
            )
            # Unregister from resource_tracker to prevent it from unlinking
            # shm we didn't create (Python 3.12 lacks track=False kwarg).
            resource_tracker.unregister(
                f"/{shm_name}", "shared_memory"
            )
        except FileNotFoundError:
            self._shm = SharedMemory(
                name=shm_name, create=True, size=ctypes.sizeof(EmsRtdb)
            )
            self._created = True

        # Map ctypes struct onto shm buffer
        self._rtdb: EmsRtdb = EmsRtdb.from_buffer(self._shm.buf)

        # Initialize magic/version if we created the segment
        if self._created:
            self._rtdb.magic = RTDB_MAGIC
            self._rtdb.version = RTDB_VERSION

    def set_di(self, pin: int, value: int) -> None:
        """Set a single DI pin with seqlock write."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DI pin must be 0-7, got {pin}")

        # Fault: stuck pin -- ignore the write
        if pin in self._stuck_pins:
            log.debug("FAULT: ignoring write to stuck pin DI-%d", pin)
            return

        gpio = self._rtdb.gpio
        # Seqlock write: odd = write in progress, even = write complete
        gpio.lock.sequence += 1  # odd
        gpio.di[pin] = value & 0xFF
        gpio.lock.sequence += 1  # even

        # Fault: contact bounce simulation
        if self._bounce_ms > 0:
            original: int = value & 0xFF
            toggled: int = 1 - original

            def _bounce() -> None:
                end_time: float = time.monotonic() + self._bounce_ms / 1000.0
                while time.monotonic() < end_time:
                    for bounce_val in (toggled, original):
                        gpio.lock.sequence += 1
                        gpio.di[pin] = bounce_val
                        gpio.lock.sequence += 1
                        time.sleep(0.001)  # 1ms toggle
                # Settle to original value
                gpio.lock.sequence += 1
                gpio.di[pin] = original
                gpio.lock.sequence += 1

            t: threading.Thread = threading.Thread(target=_bounce, daemon=True)
            t.start()

    def get_di(self, pin: int) -> int:
        """Read a single DI pin with seqlock read."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DI pin must be 0-7, got {pin}")
        gpio = self._rtdb.gpio
        # Seqlock read: spin while odd (write in progress)
        while True:
            seq1: int = gpio.lock.sequence
            if seq1 % 2 != 0:
                continue
            val: int = gpio.di[pin]
            seq2: int = gpio.lock.sequence
            if seq1 == seq2:
                return val

    def get_do(self, pin: int) -> int:
        """Read a single DO pin with seqlock read."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DO pin must be 0-7, got {pin}")
        gpio = self._rtdb.gpio
        while True:
            seq1: int = gpio.lock.sequence
            if seq1 % 2 != 0:
                continue
            val: int = gpio.do_state[pin]
            seq2: int = gpio.lock.sequence
            if seq1 == seq2:
                return val

    def set_di_multi(self, values: dict[int, int]) -> None:
        """Set multiple DI pins atomically under a single seqlock write."""
        for pin in values:
            if not 0 <= pin <= 7:
                raise ValueError(f"DI pin must be 0-7, got {pin}")
        gpio = self._rtdb.gpio
        gpio.lock.sequence += 1  # odd (write in progress)
        for pin, value in values.items():
            gpio.di[pin] = value & 0xFF
        gpio.lock.sequence += 1  # even (write complete)

    def close(self) -> None:
        """Clean up shared memory. Must del ctypes struct before shm.close()."""
        if self._closed:
            return
        self._closed = True
        # CRITICAL: delete ctypes struct reference before closing shm buffer
        # to avoid BufferError: cannot close exported pointers exist
        del self._rtdb
        self._shm.close()
        if self._created:
            try:
                self._shm.unlink()
            except FileNotFoundError:
                pass
