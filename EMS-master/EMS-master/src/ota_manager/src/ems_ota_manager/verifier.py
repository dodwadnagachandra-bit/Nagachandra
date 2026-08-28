"""OTA package verifier: Ed25519 signature verification, SHA-256 integrity,
tar extraction with path traversal protection, and semver minimum version check.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class InvalidSignatureError(Exception):
    """Raised when Ed25519 manifest signature verification fails."""


class PackageVerifier:
    """Verifies OTA packages: signature, firmware hash, tar extraction, version.

    All verification methods raise descriptive exceptions on failure so the
    OTA state machine can log and roll back cleanly.
    """

    def __init__(self, public_key_hex: str) -> None:
        """Initialise with a hex-encoded 32-byte Ed25519 public key.

        Args:
            public_key_hex: Hex string of the 32-byte Ed25519 public key
                (64 hex characters). Obtained from ota_config.yaml security.public_key_hex.

        Raises:
            ValueError: If the hex string is invalid or the key bytes are wrong length.
        """
        try:
            key_bytes: bytes = bytes.fromhex(public_key_hex)
        except ValueError as exc:
            msg: str = f"public_key_hex is not valid hex: {exc}"
            raise ValueError(msg) from exc

        self._public_key: Ed25519PublicKey = Ed25519PublicKey.from_public_bytes(key_bytes)

    def verify_manifest(self, manifest_bytes: bytes, signature_bytes: bytes) -> None:
        """Verify Ed25519 signature over manifest bytes.

        Args:
            manifest_bytes: Raw bytes of the manifest (e.g. manifest.json content).
            signature_bytes: 64-byte Ed25519 signature produced by the signing key.

        Raises:
            InvalidSignatureError: If the signature does not match the manifest bytes.
        """
        try:
            self._public_key.verify(signature_bytes, manifest_bytes)
        except InvalidSignature as exc:
            msg: str = "OTA manifest signature verification failed — package may be tampered"
            raise InvalidSignatureError(msg) from exc

    def verify_firmware_hash(self, firmware_path: Path, expected_sha256: str) -> None:
        """Streaming SHA-256 verification of a firmware image file.

        Args:
            firmware_path: Absolute path to the firmware binary on disk.
            expected_sha256: Lowercase hex SHA-256 digest to compare against.

        Raises:
            FileNotFoundError: If firmware_path does not exist.
            ValueError: If the computed digest does not match expected_sha256.
        """
        if not firmware_path.exists():
            msg: str = f"Firmware file not found for hash check: {firmware_path}"
            raise FileNotFoundError(msg)

        hasher: hashlib._Hash = hashlib.sha256()
        with firmware_path.open("rb") as fh:
            while True:
                chunk: bytes = fh.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)

        actual: str = hasher.hexdigest()
        if actual != expected_sha256.lower():
            msg = (
                f"SHA-256 mismatch for {firmware_path.name}: "
                f"expected={expected_sha256} actual={actual}"
            )
            raise ValueError(msg)

    def extract_package(self, tar_path: Path, extract_dir: Path) -> dict[str, Any]:
        """Extract an OTA tar.gz package with path traversal protection.

        Validates every member name before extraction. Reads and returns the
        parsed manifest.json from the archive.

        Args:
            tar_path: Path to the .tar.gz OTA package.
            extract_dir: Directory to extract into (must already exist).

        Returns:
            Parsed manifest dict from manifest.json inside the archive.

        Raises:
            ValueError: If any member contains a path traversal sequence
                (e.g. "../") or if manifest.json is missing from the archive.
            FileNotFoundError: If tar_path does not exist.
        """
        if not tar_path.exists():
            msg: str = f"OTA package not found: {tar_path}"
            raise FileNotFoundError(msg)

        with tarfile.open(tar_path, "r:gz") as tf:
            # Safety check: reject any member whose name contains traversal sequences
            for member in tf.getmembers():
                # Normalise and check for traversal components
                member_path: Path = Path(member.name)
                for part in member_path.parts:
                    if part == "..":
                        msg = (
                            f"OTA package contains path traversal sequence in member: "
                            f"'{member.name}' — package rejected"
                        )
                        raise ValueError(msg)
                # Also reject absolute paths within the archive
                if member_path.is_absolute():
                    msg = (
                        f"OTA package contains absolute path member: "
                        f"'{member.name}' — package rejected"
                    )
                    raise ValueError(msg)

            # Extract all members
            tf.extractall(extract_dir, filter="data")  # safe after member validation above

            # Read and parse manifest.json
            manifest_path: Path = extract_dir / "manifest.json"
            if not manifest_path.exists():
                msg = "OTA package is missing manifest.json"
                raise ValueError(msg)

        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest: dict[str, Any] = json.load(fh)

        return manifest

    def check_min_version(self, current_version: str, min_version: str) -> None:
        """Semver minimum version guard — blocks firmware downgrades.

        Parses both versions as (major, minor, patch) integer tuples and
        compares them. Pre-release suffixes are ignored.

        Args:
            current_version: Version string of the incoming firmware (e.g. "1.2.3").
            min_version: Minimum acceptable version string (e.g. "1.0.0").

        Raises:
            ValueError: If current_version < min_version (downgrade blocked).
        """
        def _parse(v: str) -> tuple[int, int, int]:
            # Strip any pre-release suffix (e.g. "1.2.3-beta" -> "1.2.3")
            core: str = v.split("-")[0].split("+")[0]
            parts: list[str] = core.split(".")
            if len(parts) != 3:
                msg: str = f"Version '{v}' is not a valid semver X.Y.Z string"
                raise ValueError(msg)
            return (int(parts[0]), int(parts[1]), int(parts[2]))

        current_tuple: tuple[int, int, int] = _parse(current_version)
        min_tuple: tuple[int, int, int] = _parse(min_version)

        if current_tuple < min_tuple:
            msg = (
                f"Firmware version {current_version} is below min_version {min_version} "
                f"— downgrade rejected"
            )
            raise ValueError(msg)
