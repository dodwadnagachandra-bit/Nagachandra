"""OTA A/B partition backend: boot flag management and partition operations.

BootFlag is the persistent record of the current and previous partition slot.

Two backend implementations are provided:
- PartitionBackend: JSON-file-based backend for development/testing.
- UBootPartitionBackend: Production backend using fw_printenv/fw_setenv to
  read and write U-Boot environment variables for A/B slot management.

Both classes share a common base (BasePartitionBackend) for dd flashing and
systemctl reboot operations.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BootFlag:
    """Persistent record of A/B partition state.

    Written atomically to disk as JSON. The OTA state machine reads this on
    startup to determine whether a health check is pending.
    """

    active: str
    """Currently active partition slot: 'a' or 'b'."""

    previous: str
    """Previously active slot before last update: 'a' or 'b'."""

    boot_count: int
    """Number of times the current slot has been booted."""

    pending_health_check: bool = field(default=False)
    """True if the device rebooted into a new partition and health check is outstanding."""


class BasePartitionBackend:
    """Shared base for partition backends providing dd flash and systemctl reboot.

    Subclasses implement read_boot_flag, write_boot_flag, and
    get_standby_partition for their specific storage backend (JSON file or
    U-Boot environment).
    """

    def __init__(self, active_device: str, standby_device: str) -> None:
        """Initialise with device paths.

        Args:
            active_device: Block device for the currently active partition.
            standby_device: Block device for the standby (write target) partition.
        """
        self._active_device: str = active_device
        self._standby_device: str = standby_device

    def read_boot_flag(self) -> BootFlag:
        """Read the current boot flag. Implemented by subclass."""
        raise NotImplementedError

    def write_boot_flag(self, flag: BootFlag) -> None:
        """Write the boot flag. Implemented by subclass."""
        raise NotImplementedError

    def get_standby_partition(self) -> str:
        """Return the standby slot identifier. Implemented by subclass."""
        raise NotImplementedError

    async def write_image_to_standby(self, image_path: Path) -> None:
        """Flash a firmware image to the standby partition using dd.

        Uses asyncio.create_subprocess_exec to avoid blocking the event loop
        during the potentially long flash operation.

        Args:
            image_path: Path to the firmware image file to flash.

        Raises:
            RuntimeError: If dd exits with a non-zero return code.
        """
        standby: str = self._standby_device
        proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "dd",
            f"if={image_path}",
            f"of={standby}",
            "bs=4M",
            "conv=fsync",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            stderr_text: str = stderr_bytes.decode(errors="replace").strip()
            msg: str = (
                f"dd failed writing {image_path} to {standby} "
                f"(exit={proc.returncode}): {stderr_text}"
            )
            raise RuntimeError(msg)

    async def reboot(self) -> None:
        """Initiate a system reboot via systemctl.

        Uses asyncio.create_subprocess_exec to avoid blocking.
        """
        proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "systemctl",
            "reboot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()


class PartitionBackend(BasePartitionBackend):
    """JSON-file-based A/B partition backend for development and testing.

    All file I/O uses atomic tmp+rename to prevent corrupt boot flags on
    power loss during writes. Flash writes use dd via asyncio subprocess
    to avoid blocking the event loop.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise from the partition section of ota_config.

        Args:
            config: Full OTA config dict. Uses config["partition"] for paths.
        """
        partition_cfg: dict[str, Any] = config["partition"]
        self._boot_flag_path: str = partition_cfg["boot_flag_path"]
        super().__init__(
            active_device=partition_cfg["active_device"],
            standby_device=partition_cfg["standby_device"],
        )

    def read_boot_flag(self) -> BootFlag:
        """Read the boot flag from disk.

        Returns:
            Parsed BootFlag dataclass.

        Raises:
            FileNotFoundError: If the boot flag file does not exist.
            ValueError: If the JSON is malformed or missing required fields.
        """
        flag_path: Path = Path(self._boot_flag_path)
        if not flag_path.exists():
            msg: str = f"Boot flag file not found: {flag_path}"
            raise FileNotFoundError(msg)

        with flag_path.open("r", encoding="utf-8") as fh:
            try:
                data: dict[str, Any] = json.load(fh)
            except json.JSONDecodeError as exc:
                msg = f"Boot flag file is not valid JSON: {exc}"
                raise ValueError(msg) from exc

        return BootFlag(
            active=data["active"],
            previous=data["previous"],
            boot_count=int(data["boot_count"]),
            pending_health_check=bool(data.get("pending_health_check", False)),
        )

    def write_boot_flag(self, flag: BootFlag) -> None:
        """Atomically write the boot flag to disk via tmp+rename.

        Uses a .tmp sibling file and os.rename (POSIX atomic) to ensure the
        boot flag is never in a partially-written state.

        Args:
            flag: BootFlag to persist.
        """
        flag_path: Path = Path(self._boot_flag_path)
        tmp_path: Path = flag_path.with_suffix(".tmp")

        data: dict[str, Any] = {
            "active": flag.active,
            "previous": flag.previous,
            "boot_count": flag.boot_count,
            "pending_health_check": flag.pending_health_check,
        }

        # Ensure parent directory exists
        flag_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to .tmp then atomically rename
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()

        tmp_path.rename(flag_path)

    def get_standby_partition(self) -> str:
        """Return the standby partition slot identifier.

        Reads the current boot flag to determine which slot is active, then
        returns the opposite slot.

        Returns:
            "b" if active slot is "a", "a" if active slot is "b".

        Raises:
            ValueError: If the active slot is not "a" or "b".
        """
        flag: BootFlag = self.read_boot_flag()
        active: str = flag.active.lower()
        if active == "a":
            return "b"
        elif active == "b":
            return "a"
        else:
            msg: str = f"Invalid active partition slot: '{active}' — expected 'a' or 'b'"
            raise ValueError(msg)


class UBootPartitionBackend(BasePartitionBackend):
    """Production A/B partition backend using U-Boot environment variables.

    Reads and writes partition state via fw_printenv/fw_setenv subprocess
    calls, manipulating ems_active_slot, ems_boot_count, and
    ems_pending_health_check in the U-Boot environment. Sync subprocess.run
    is used for compatibility with the existing PartitionBackend interface.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialise from the partition section of ota_config.

        Args:
            config: Full OTA config dict. Uses config["partition"] for U-Boot
                env config path and device paths.
        """
        partition_cfg: dict[str, Any] = config["partition"]
        self._fw_env_config: str = partition_cfg.get(
            "fw_env_config", "/etc/fw_env.config"
        )
        super().__init__(
            active_device=partition_cfg["active_device"],
            standby_device=partition_cfg["standby_device"],
        )

    def _fw_getenv(self, key: str) -> tuple[int, str]:
        """Read a U-Boot environment variable via fw_printenv.

        Args:
            key: U-Boot environment variable name.

        Returns:
            Tuple of (returncode, value_stripped). Value is empty string
            when returncode is non-zero.
        """
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            ["fw_printenv", "-n", key],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        value: str = result.stdout.decode(errors="replace").strip()
        return result.returncode, value

    def _fw_setenv(self, key: str, value: str) -> None:
        """Write a U-Boot environment variable via fw_setenv.

        Args:
            key: U-Boot environment variable name.
            value: Value to set.

        Raises:
            RuntimeError: If fw_setenv exits with a non-zero return code.
        """
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            ["fw_setenv", key, value],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            stderr_text: str = result.stderr.decode(errors="replace").strip()
            msg: str = (
                f"fw_setenv {key}={value} failed "
                f"(exit={result.returncode}): {stderr_text}"
            )
            raise RuntimeError(msg)

    def read_boot_flag(self) -> BootFlag:
        """Read the A/B boot state from U-Boot environment variables.

        Reads ems_active_slot (required), ems_boot_count (defaults to 0),
        and ems_pending_health_check (defaults to False). Derives previous
        slot as the opposite of active.

        Returns:
            BootFlag populated from U-Boot environment.

        Raises:
            RuntimeError: If fw_printenv fails for ems_active_slot.
        """
        # Read active slot — required
        rc_active: int
        active: str
        rc_active, active = self._fw_getenv("ems_active_slot")
        if rc_active != 0:
            msg: str = (
                f"fw_printenv ems_active_slot failed (exit={rc_active}): "
                "slot not defined in U-Boot env"
            )
            raise RuntimeError(msg)

        # Read boot count — optional, default 0
        rc_count: int
        count_str: str
        rc_count, count_str = self._fw_getenv("ems_boot_count")
        boot_count: int = int(count_str) if rc_count == 0 and count_str else 0

        # Read pending health check flag — optional, default False
        rc_phc: int
        phc_str: str
        rc_phc, phc_str = self._fw_getenv("ems_pending_health_check")
        pending_health_check: bool = (
            rc_phc == 0 and phc_str.strip() not in ("", "0")
        )

        # Derive previous slot as opposite of active
        active_lower: str = active.lower()
        previous: str = "b" if active_lower == "a" else "a"

        return BootFlag(
            active=active_lower,
            previous=previous,
            boot_count=boot_count,
            pending_health_check=pending_health_check,
        )

    def write_boot_flag(self, flag: BootFlag) -> None:
        """Write A/B boot state to U-Boot environment variables.

        Sets ems_active_slot, ems_boot_count, and ems_pending_health_check
        via fw_setenv subprocess calls.

        Args:
            flag: BootFlag to persist to U-Boot env.

        Raises:
            RuntimeError: If any fw_setenv call fails.
        """
        health_check_val: str = "1" if flag.pending_health_check else "0"
        self._fw_setenv("ems_active_slot", flag.active)
        self._fw_setenv("ems_boot_count", str(flag.boot_count))
        self._fw_setenv("ems_pending_health_check", health_check_val)

    def get_standby_partition(self) -> str:
        """Return the standby partition slot identifier.

        Reads ems_active_slot from U-Boot env and returns the opposite slot.

        Returns:
            "b" if active slot is "a", "a" if active slot is "b".

        Raises:
            RuntimeError: If fw_printenv fails for ems_active_slot.
            ValueError: If the active slot is not "a" or "b".
        """
        flag: BootFlag = self.read_boot_flag()
        active: str = flag.active.lower()
        if active == "a":
            return "b"
        elif active == "b":
            return "a"
        else:
            msg: str = (
                f"Invalid active partition slot: '{active}' — expected 'a' or 'b'"
            )
            raise ValueError(msg)
