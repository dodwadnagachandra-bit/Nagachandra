"""GPIO Test Harness -- inject DI signals and read DO state for safety_manager testing."""

from __future__ import annotations

from pathlib import Path

from tools.simulators.gpio_harness.config import GpioConfig
from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend


def set_di(
    pin_or_name: str | int,
    value: int,
    *,
    shm_name: str = "ems_rtdb",
    config_path: str | None = None,
) -> None:
    """Set a DI pin value via RTDB shared memory.

    Args:
        pin_or_name: Pin number (int) or name string ("ESTOP_NO", "DI-6").
        value: Value to set (0 or 1).
        shm_name: POSIX shared memory segment name.
        config_path: Path to gpio_config.yaml (for name resolution).
    """
    pin: int = _resolve_di(pin_or_name, config_path)
    backend: RtdbBackend = RtdbBackend(shm_name=shm_name)
    try:
        backend.set_di(pin, value)
    finally:
        backend.close()


def get_di(
    pin_or_name: str | int,
    *,
    shm_name: str = "ems_rtdb",
    config_path: str | None = None,
) -> int:
    """Read a DI pin value from RTDB shared memory.

    Args:
        pin_or_name: Pin number (int) or name string.
        shm_name: POSIX shared memory segment name.
        config_path: Path to gpio_config.yaml.

    Returns:
        Pin value (0 or 1).
    """
    pin: int = _resolve_di(pin_or_name, config_path)
    backend: RtdbBackend = RtdbBackend(shm_name=shm_name)
    try:
        return backend.get_di(pin)
    finally:
        backend.close()


def get_do(
    pin_or_name: str | int,
    *,
    shm_name: str = "ems_rtdb",
    config_path: str | None = None,
) -> int:
    """Read a DO pin value from RTDB shared memory.

    Args:
        pin_or_name: Pin number (int) or name string.
        shm_name: POSIX shared memory segment name.
        config_path: Path to gpio_config.yaml.

    Returns:
        Pin value (0 or 1).
    """
    pin: int = _resolve_do(pin_or_name, config_path)
    backend: RtdbBackend = RtdbBackend(shm_name=shm_name)
    try:
        return backend.get_do(pin)
    finally:
        backend.close()


def set_di_multi(
    values: dict[str | int, int],
    *,
    shm_name: str = "ems_rtdb",
    config_path: str | None = None,
) -> None:
    """Set multiple DI pins atomically via RTDB shared memory.

    Args:
        values: Dict of pin (number or name) -> value (0 or 1).
        shm_name: POSIX shared memory segment name.
        config_path: Path to gpio_config.yaml.
    """
    resolved: dict[int, int] = {}
    for pin_or_name, value in values.items():
        resolved[_resolve_di(pin_or_name, config_path)] = value

    backend: RtdbBackend = RtdbBackend(shm_name=shm_name)
    try:
        backend.set_di_multi(resolved)
    finally:
        backend.close()


def _resolve_di(pin_or_name: str | int, config_path: str | None) -> int:
    """Resolve a DI pin identifier to a number."""
    if isinstance(pin_or_name, int):
        return pin_or_name
    cfg: GpioConfig = GpioConfig(
        config_path=Path(config_path) if config_path else None
    )
    return cfg.resolve_di(pin_or_name)


def _resolve_do(pin_or_name: str | int, config_path: str | None) -> int:
    """Resolve a DO pin identifier to a number."""
    if isinstance(pin_or_name, int):
        return pin_or_name
    cfg: GpioConfig = GpioConfig(
        config_path=Path(config_path) if config_path else None
    )
    return cfg.resolve_do(pin_or_name)


__all__: list[str] = ["set_di", "get_di", "get_do", "set_di_multi"]
