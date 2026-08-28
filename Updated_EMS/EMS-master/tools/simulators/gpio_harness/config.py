"""GPIO pin name resolution from gpio_config.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml


class GpioConfig:
    """Resolves pin names (ESTOP_NO, PCS_STOP) to pin numbers and applies active_low polarity.

    Loads gpio_config.yaml once at construction. Provides bidirectional lookups
    for both DI and DO pins by name or "DI-N"/"DO-N" syntax.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path("config/gpio_config.yaml")

        with open(config_path) as f:
            data: dict = yaml.safe_load(f)

        # Build name -> pin number maps
        self._di_names: dict[str, int] = {}
        self._di_active_low: dict[int, bool] = {}
        self._di_pin_to_name: dict[int, str] = {}

        for key, entry in data.get("digital_inputs", {}).items():
            pin: int = int(key.split("-")[1])
            name: str = entry["name"]
            self._di_names[name.upper()] = pin
            self._di_active_low[pin] = entry.get("active_low", False)
            self._di_pin_to_name[pin] = name

        self._do_names: dict[str, int] = {}
        self._do_active_low: dict[int, bool] = {}
        self._do_pin_to_name: dict[int, str] = {}

        for key, entry in data.get("digital_outputs", {}).items():
            pin = int(key.split("-")[1])
            name = entry["name"]
            self._do_names[name.upper()] = pin
            self._do_active_low[pin] = entry.get("active_low", False)
            self._do_pin_to_name[pin] = name

    def resolve_di(self, pin_or_name: str) -> int:
        """Resolve a DI pin name or 'DI-N' string to a pin number.

        Args:
            pin_or_name: Pin name (e.g. "ESTOP_NO") or "DI-6" syntax.

        Returns:
            Pin number (0-7).

        Raises:
            ValueError: If name not found.
        """
        if pin_or_name.upper().startswith("DI-"):
            return int(pin_or_name.split("-")[1])
        upper: str = pin_or_name.upper()
        if upper in self._di_names:
            return self._di_names[upper]
        raise ValueError(f"Unknown DI pin name: {pin_or_name}")

    def resolve_do(self, pin_or_name: str) -> int:
        """Resolve a DO pin name or 'DO-N' string to a pin number.

        Args:
            pin_or_name: Pin name (e.g. "PCS_STOP") or "DO-5" syntax.

        Returns:
            Pin number (0-7).

        Raises:
            ValueError: If name not found.
        """
        if pin_or_name.upper().startswith("DO-"):
            return int(pin_or_name.split("-")[1])
        upper: str = pin_or_name.upper()
        if upper in self._do_names:
            return self._do_names[upper]
        raise ValueError(f"Unknown DO pin name: {pin_or_name}")

    def apply_active_low_di(self, pin: int, logical: bool) -> int:
        """Convert logical value to raw value considering active_low polarity.

        For active_low pins: logical True -> raw 0, logical False -> raw 1
        For normal pins: logical True -> raw 1, logical False -> raw 0

        Args:
            pin: DI pin number (0-7).
            logical: Logical (intended) state.

        Returns:
            Raw value to write to the pin (0 or 1).
        """
        is_active_low: bool = self._di_active_low.get(pin, False)
        if is_active_low:
            return 0 if logical else 1
        return 1 if logical else 0

    def apply_active_low_do(self, pin: int, raw_value: int) -> str:
        """Interpret a raw DO value considering active_low polarity.

        Args:
            pin: DO pin number (0-7).
            raw_value: Raw value read from DO pin (0 or 1).

        Returns:
            "active" or "inactive" string.
        """
        is_active_low: bool = self._do_active_low.get(pin, False)
        if is_active_low:
            return "active" if raw_value == 0 else "inactive"
        return "active" if raw_value == 1 else "inactive"

    def get_di_name(self, pin: int) -> str:
        """Get the config name for a DI pin number."""
        if pin in self._di_pin_to_name:
            return self._di_pin_to_name[pin]
        return f"DI-{pin}"

    def get_do_name(self, pin: int) -> str:
        """Get the config name for a DO pin number."""
        if pin in self._do_pin_to_name:
            return self._do_pin_to_name[pin]
        return f"DO-{pin}"
