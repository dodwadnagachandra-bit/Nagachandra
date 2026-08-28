"""gpio-sim kernel module backend -- creates virtual GPIO chip via configfs."""

from __future__ import annotations

import atexit
import logging
from pathlib import Path

from tools.simulators.gpio_harness.backend import GpioBackend

logger: logging.Logger = logging.getLogger(__name__)

CONFIGFS_BASE: Path = Path("/sys/kernel/config/gpio-sim")


class GpioSimBackend(GpioBackend):
    """GPIO backend using Linux gpio-sim kernel module for realistic libgpiod testing.

    Creates a virtual GPIO chip with 16 lines:
    - Lines 0-7: DI (digital inputs)
    - Lines 8-15: DO (digital outputs)

    Requires: modprobe gpio-sim (kernel >= 5.17)
    """

    def __init__(
        self,
        chip_name: str = "ems-gpio",
        num_lines: int = 16,
        line_names: dict[int, str] | None = None,
    ) -> None:
        self._chip_name: str = chip_name
        self._num_lines: int = num_lines
        self._line_names: dict[int, str] = line_names or {}
        self._device_dir: Path = CONFIGFS_BASE / chip_name
        self._sysfs_path: Path | None = None
        self._live: bool = False

        self.setup()
        atexit.register(self.teardown)

    def setup(self) -> None:
        """Create virtual GPIO chip via configfs."""
        # Create device directory
        self._device_dir.mkdir(parents=True, exist_ok=True)
        bank_dir: Path = self._device_dir / "bank0"
        bank_dir.mkdir(exist_ok=True)

        # Set number of lines
        (bank_dir / "num_lines").write_text(str(self._num_lines))

        # Create line directories with names
        for line_num in range(self._num_lines):
            line_dir: Path = bank_dir / f"line{line_num}"
            line_dir.mkdir(exist_ok=True)
            if line_num in self._line_names:
                (line_dir / "name").write_text(self._line_names[line_num])

        # Activate the chip
        (self._device_dir / "live").write_text("1")
        self._live = True

        # Read chip name and find sysfs path
        chip_name_file: Path = bank_dir / "chip_name"
        if chip_name_file.exists():
            actual_chip: str = chip_name_file.read_text().strip()
            logger.info("gpio-sim chip created: %s", actual_chip)

        # Locate sysfs path
        for p in Path("/sys/devices/platform").glob("gpio-sim.*"):
            self._sysfs_path = p
            break

        if self._sysfs_path is None:
            logger.warning("Could not locate gpio-sim sysfs path")

    def set_di(self, pin: int, value: int) -> None:
        """Set DI pin by writing pull configuration to sysfs."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DI pin must be 0-7, got {pin}")
        if self._sysfs_path is None:
            raise RuntimeError("gpio-sim sysfs path not found")
        pull: str = "pull-up" if value else "pull-down"
        sim_gpio: Path = self._sysfs_path / f"sim_gpio{pin}" / "pull"
        sim_gpio.write_text(pull)

    def get_di(self, pin: int) -> int:
        """Read DI pin value from sysfs."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DI pin must be 0-7, got {pin}")
        if self._sysfs_path is None:
            raise RuntimeError("gpio-sim sysfs path not found")
        value_path: Path = self._sysfs_path / f"sim_gpio{pin}" / "value"
        return int(value_path.read_text().strip())

    def get_do(self, pin: int) -> int:
        """Read DO pin value from sysfs (DO offset by 8)."""
        if not 0 <= pin <= 7:
            raise ValueError(f"DO pin must be 0-7, got {pin}")
        if self._sysfs_path is None:
            raise RuntimeError("gpio-sim sysfs path not found")
        value_path: Path = self._sysfs_path / f"sim_gpio{pin + 8}" / "value"
        return int(value_path.read_text().strip())

    def set_di_multi(self, values: dict[int, int]) -> None:
        """Set multiple DI pins (no seqlock needed for sysfs)."""
        for pin, value in values.items():
            self.set_di(pin, value)

    def teardown(self) -> None:
        """Remove virtual GPIO chip from configfs."""
        if not self._live:
            return
        try:
            # Deactivate
            live_path: Path = self._device_dir / "live"
            if live_path.exists():
                live_path.write_text("0")
            self._live = False

            # Remove line dirs (reverse order)
            bank_dir: Path = self._device_dir / "bank0"
            for line_num in reversed(range(self._num_lines)):
                line_dir: Path = bank_dir / f"line{line_num}"
                if line_dir.exists():
                    # Remove name file first if exists
                    name_file: Path = line_dir / "name"
                    if name_file.exists():
                        name_file.unlink()
                    try:
                        line_dir.rmdir()
                    except OSError:
                        pass

            # Remove bank and device dirs
            try:
                bank_dir.rmdir()
            except OSError:
                pass
            try:
                self._device_dir.rmdir()
            except OSError:
                pass

            logger.info("gpio-sim chip %s removed", self._chip_name)
        except Exception:
            logger.exception("Error during gpio-sim teardown")

    def close(self) -> None:
        """Release resources by tearing down the virtual chip."""
        self.teardown()
