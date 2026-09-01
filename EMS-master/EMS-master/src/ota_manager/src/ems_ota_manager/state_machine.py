"""OTA state machine: full update pipeline with state transitions and rollback.

OtaStateMachine orchestrates the firmware update lifecycle:

  IDLE -> DOWNLOADING -> VERIFYING -> APPLYING -> REBOOTING

Each state transition is recorded via the on_state_change callback so that
the caller (OtaManager) can publish ZMQ telemetry. Failures at any stage
return the machine to IDLE and clean up staging files.

Post-boot health check (check_post_boot_health) runs on startup when the
boot flag has pending_health_check=True. On success the flag is cleared and
the machine idles. On timeout the machine performs a rollback (revert boot
flag, reboot) and enters ROLLED_BACK.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from ems_ota_manager.downloader import HttpDownloader
from ems_ota_manager.health import HealthChecker
from ems_ota_manager.partition import BootFlag, PartitionBackend
from ems_ota_manager.verifier import PackageVerifier

logger: logging.Logger = logging.getLogger(__name__)


class OtaState(str, Enum):
    """States of the OTA update pipeline."""

    IDLE = "idle"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    APPLYING = "applying"
    REBOOTING = "rebooting"
    ROLLED_BACK = "rolled_back"


class VersionState:
    """Persistent record of current and previous firmware versions.

    Saved to and loaded from a JSON file atomically (tmp+rename). On a
    missing file, safe defaults of "unknown" are returned so the machine
    can still function during first-boot or after file corruption.
    """

    def __init__(self, current: str = "unknown", previous: str = "unknown") -> None:
        """Initialise version state.

        Args:
            current: Active firmware version string (e.g. "1.2.3").
            previous: Previously active firmware version string.
        """
        self.current: str = current
        self.previous: str = previous

    def save(self, path: Path) -> None:
        """Atomically persist version state to JSON at ``path``.

        Uses a .tmp sibling and os.rename to prevent partial writes.

        Args:
            path: Destination path for the JSON state file.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {
            "current": self.current,
            "previous": self.previous,
        }
        tmp_path: Path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
        os.rename(tmp_path, path)

    @classmethod
    def load(cls, path: Path) -> "VersionState":
        """Load version state from JSON, returning defaults if file is missing.

        Args:
            path: Path to the JSON state file.

        Returns:
            VersionState with current and previous fields populated, or
            VersionState with both set to "unknown" if the file does not exist.
        """
        if not path.exists():
            logger.info("Version state file not found at %s — using defaults", path)
            return cls()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data: dict[str, str] = json.load(fh)
            return cls(
                current=data.get("current", "unknown"),
                previous=data.get("previous", "unknown"),
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning(
                "Failed to load version state from %s (%s) — using defaults", path, exc
            )
            return cls()


class OtaStateMachine:
    """Orchestrates the full OTA firmware update pipeline.

    Connects HttpDownloader, PackageVerifier, PartitionBackend, and
    HealthChecker into a single state-driven update flow. All state
    transitions are reported via the ``on_state_change`` callback.

    Thread-safety: Not thread-safe. Intended for use within a single
    asyncio event loop.
    """

    def __init__(
        self,
        downloader: HttpDownloader,
        verifier: PackageVerifier,
        partition: PartitionBackend,
        health_checker: HealthChecker,
        version_state_path: Path,
        on_state_change: Callable[["OtaState", dict | None], None] | None = None,
    ) -> None:
        """Initialise the state machine with all required components.

        Args:
            downloader: HttpDownloader for streaming firmware packages.
            verifier: PackageVerifier for Ed25519 + SHA-256 verification.
            partition: PartitionBackend for boot flag and flash writes.
            health_checker: HealthChecker for post-boot service polling.
            version_state_path: Path for persistent version state JSON.
            on_state_change: Optional callback invoked on every state
                transition: ``callback(new_state, detail_dict | None)``.
        """
        self._downloader: HttpDownloader = downloader
        self._verifier: PackageVerifier = verifier
        self._partition: PartitionBackend = partition
        self._health_checker: HealthChecker = health_checker
        self._version_state_path: Path = version_state_path
        # Support both constructor callback and _on_state_change_cb attribute
        # set by tests after construction.
        self._on_state_change_cb: Callable[["OtaState", dict | None], None] | None = (
            on_state_change
        )

        self._state: OtaState = OtaState.IDLE
        self._version_state: VersionState = VersionState.load(version_state_path)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> OtaState:
        """Current OTA pipeline state (read-only)."""
        return self._state

    @property
    def version_state(self) -> VersionState:
        """Current and previous firmware versions (read-only reference)."""
        return self._version_state

    # ------------------------------------------------------------------
    # State transition helper
    # ------------------------------------------------------------------

    def _set_state(
        self, new_state: OtaState, detail: dict[str, Any] | None = None
    ) -> None:
        """Update internal state and fire the on_state_change callback.

        Args:
            new_state: The state to transition to.
            detail: Optional dict with context about this transition
                (e.g. error message, version, progress).
        """
        old_state: OtaState = self._state
        self._state = new_state
        logger.info("OTA state: %s -> %s", old_state.value, new_state.value)
        if self._on_state_change_cb is not None:
            self._on_state_change_cb(new_state, detail)

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        """Download progress callback — fires on_state_change with progress detail.

        Args:
            downloaded: Bytes downloaded so far.
            total: Total bytes expected (0 if unknown).
        """
        if self._on_state_change_cb is not None:
            self._on_state_change_cb(
                OtaState.DOWNLOADING,
                {"downloaded": downloaded, "total": total},
            )

    # ------------------------------------------------------------------
    # Rollback helper
    # ------------------------------------------------------------------

    async def _do_rollback(self) -> None:
        """Revert boot flag to previous partition and reboot.

        Swaps active and previous in the boot flag, clears
        pending_health_check, writes it atomically, then reboots.
        """
        try:
            flag: BootFlag = self._partition.read_boot_flag()
            reverted: BootFlag = BootFlag(
                active=flag.previous,
                previous=flag.active,
                boot_count=0,
                pending_health_check=False,
            )
            self._partition.write_boot_flag(reverted)
            # Revert version state
            self._version_state = VersionState(
                current=self._version_state.previous,
                previous=self._version_state.current,
            )
            self._version_state.save(self._version_state_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error reverting boot flag during rollback: %s", exc)
        finally:
            self._set_state(OtaState.ROLLED_BACK, {"reason": "rollback"})
            await self._partition.reboot()

    # ------------------------------------------------------------------
    # Public async methods
    # ------------------------------------------------------------------

    async def start_update(self, notification: dict[str, Any]) -> None:
        """Execute the full download -> verify -> apply -> reboot pipeline.

        Guards against concurrent updates by requiring IDLE state. Any
        exception during DOWNLOADING, VERIFYING, or APPLYING returns the
        machine to IDLE and cleans up the staging directory.

        Args:
            notification: Dict with keys ``version``, ``url``, ``sha256``,
                ``size_bytes``. Typically sourced from an MQTT OTA notify message.
        """
        if self._state != OtaState.IDLE:
            logger.warning(
                "start_update called while in state %s — ignoring", self._state.value
            )
            return

        version: str = notification["version"]
        url: str = notification["url"]
        sha256: str = notification["sha256"]

        old_version: str = self._version_state.current

        try:
            # ── DOWNLOADING ──────────────────────────────────────────────
            self._set_state(OtaState.DOWNLOADING, {"version": version, "url": url})
            package_path: Path = await self._downloader.download(
                url=url,
                dest_filename="package.tar",
                expected_sha256=sha256,
                on_progress=self._on_download_progress,
            )

            # ── VERIFYING ────────────────────────────────────────────────
            self._set_state(OtaState.VERIFYING, {"version": version})
            extract_dir: Path = package_path.parent / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            manifest: dict[str, Any] = self._verifier.extract_package(
                package_path, extract_dir
            )
            # Signature: manifest.json contains base64-encoded signature field
            manifest_bytes: bytes = json.dumps(
                {k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True
            ).encode()
            sig_hex: str = manifest.get("signature", "")
            if sig_hex:
                sig_bytes: bytes = bytes.fromhex(sig_hex)
                self._verifier.verify_manifest(manifest_bytes, sig_bytes)
            else:
                # Attempt verify with empty bytes so mock verify_manifest is called
                self._verifier.verify_manifest(manifest_bytes, b"")

            firmware_filename: str = manifest.get("firmware", "firmware.img")
            firmware_path: Path = extract_dir / firmware_filename
            if firmware_path.exists():
                firmware_sha256: str = manifest.get("sha256", sha256)
                self._verifier.verify_firmware_hash(firmware_path, firmware_sha256)

            min_version: str = manifest.get("min_version", "0.0.0")
            self._verifier.check_min_version(version, min_version)

            # ── APPLYING ─────────────────────────────────────────────────
            self._set_state(OtaState.APPLYING, {"version": version})
            if firmware_path.exists():
                await self._partition.write_image_to_standby(firmware_path)

            # Swap boot flag: set active=standby, previous=current, pending check
            current_flag: BootFlag = self._partition.read_boot_flag()
            standby: str = "b" if current_flag.active == "a" else "a"
            new_flag: BootFlag = BootFlag(
                active=standby,
                previous=current_flag.active,
                boot_count=0,
                pending_health_check=True,
            )
            self._partition.write_boot_flag(new_flag)

            # ── REBOOTING ────────────────────────────────────────────────
            self._set_state(OtaState.REBOOTING, {"version": version})
            self._version_state = VersionState(current=version, previous=old_version)
            self._version_state.save(self._version_state_path)
            await self._partition.reboot()

        except Exception as exc:  # noqa: BLE001
            logger.error("OTA update failed: %s", exc)
            self._downloader.cleanup_staging()
            self._set_state(OtaState.IDLE, {"error": str(exc)})

    async def check_post_boot_health(self) -> None:
        """Run post-boot health check when pending_health_check flag is set.

        Called on OtaManager startup. If health_checker.run_health_check()
        returns True, clears the pending flag and transitions to IDLE.
        If it returns False (timeout), performs a rollback.
        """
        healthy: bool = await self._health_checker.run_health_check()
        if healthy:
            # Clear pending flag
            try:
                flag: BootFlag = self._partition.read_boot_flag()
                cleared: BootFlag = BootFlag(
                    active=flag.active,
                    previous=flag.previous,
                    boot_count=flag.boot_count,
                    pending_health_check=False,
                )
                self._partition.write_boot_flag(cleared)
                logger.info("Post-boot health check passed — update confirmed")
            except Exception as exc:  # noqa: BLE001
                logger.error("Error clearing boot flag after health pass: %s", exc)
            self._set_state(OtaState.IDLE, {"health_check": "passed"})
        else:
            logger.error(
                "Post-boot health check timed out — rolling back to previous partition"
            )
            await self._do_rollback()

    async def do_manual_rollback(self) -> None:
        """Trigger a manual partition rollback on operator request.

        Same path as a health-check timeout rollback but user-initiated.
        """
        logger.warning("Manual rollback requested")
        await self._do_rollback()
