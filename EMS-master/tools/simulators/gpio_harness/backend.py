"""GPIO backend abstract base class and factory."""

from __future__ import annotations

import abc
from pathlib import Path


class GpioBackend(abc.ABC):
    """Abstract base class for GPIO test harness backends."""

    @abc.abstractmethod
    def set_di(self, pin: int, value: int) -> None:
        """Set a digital input pin value (0 or 1)."""

    @abc.abstractmethod
    def get_di(self, pin: int) -> int:
        """Read a digital input pin value."""

    @abc.abstractmethod
    def get_do(self, pin: int) -> int:
        """Read a digital output pin value."""

    @abc.abstractmethod
    def set_di_multi(self, values: dict[int, int]) -> None:
        """Set multiple DI pins atomically (single seqlock acquisition)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources."""

    def __enter__(self) -> GpioBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _gpio_sim_available() -> bool:
    """Check if gpio-sim kernel module is available."""
    return Path("/sys/kernel/config/gpio-sim").exists()


def detect_backend(
    preference: str = "auto",
    *,
    shm_name: str = "ems_rtdb",
    fault_cfg: dict | None = None,
) -> GpioBackend:
    """Factory function to create the appropriate GPIO backend.

    Args:
        preference: "auto" (try gpio-sim, fallback to RTDB), "rtdb", or "gpio-sim".
        shm_name: POSIX shared memory segment name (RTDB backend only).
        fault_cfg: Optional fault injection configuration dict.

    Returns:
        An initialized GpioBackend instance.

    Raises:
        RuntimeError: If gpio-sim requested but unavailable.
    """
    if preference == "rtdb":
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        return RtdbBackend(shm_name=shm_name, fault_cfg=fault_cfg)

    if preference == "gpio-sim":
        if not _gpio_sim_available():
            raise RuntimeError(
                "gpio-sim kernel module not available. "
                "Load it with: sudo modprobe gpio-sim"
            )
        from tools.simulators.gpio_harness.gpio_sim_backend import GpioSimBackend

        return GpioSimBackend()

    # auto: try gpio-sim first, fallback to RTDB
    if _gpio_sim_available():
        from tools.simulators.gpio_harness.gpio_sim_backend import GpioSimBackend

        return GpioSimBackend()

    from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

    return RtdbBackend(shm_name=shm_name, fault_cfg=fault_cfg)
