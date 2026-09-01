"""Unit tests for ota_manager foundation components.

Tests cover:
- config.py: load_ota_config with schema validation
- verifier.py: PackageVerifier Ed25519 signatures, hash verification, tar extraction, version checks
- partition.py: BootFlag round-trip, atomic write, PartitionBackend standby selection
- downloader.py: HttpDownloader streaming, resume, SHA-256, size limit, progress
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ed25519_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    """Generate a fresh Ed25519 key pair for each test.

    Returns:
        Tuple of (private_key, public_key_bytes).
    """
    private_key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
    public_key_bytes: bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_key_bytes


@pytest.fixture
def public_key_hex(ed25519_keypair: tuple) -> str:
    """Return hex-encoded public key for PackageVerifier construction."""
    _, pub_bytes = ed25519_keypair
    return pub_bytes.hex()


@pytest.fixture
def ota_config_dict() -> dict[str, Any]:
    """Valid OTA config dict matching ota_config.yaml structure."""
    return {
        "_schema_version": "1.0",
        "staging": {
            "dir": "/tmp/ems-ota",
            "max_package_mb": 500,
        },
        "partition": {
            "boot_flag_path": "/var/lib/ems/boot_flag.json",
            "active_device": "/dev/mmcblk0p2",
            "standby_device": "/dev/mmcblk0p3",
        },
        "security": {
            "public_key_hex": "a" * 64,
        },
        "download": {
            "timeout_s": 300,
            "chunk_size": 65536,
            "progress_interval_s": 5,
        },
        "health_check": {
            "timeout_s": 300,
            "poll_interval_s": 10,
            "services": [
                "safety_manager",
                "comm_manager",
                "data_manager",
                "control_manager",
                "alarm_manager",
                "cloud_manager",
            ],
        },
        "version": {
            "state_path": "/var/lib/ems/ota_state.json",
        },
    }


@pytest.fixture
def ota_config_yaml_path(tmp_path: Path, ota_config_dict: dict) -> Path:
    """Write a valid ota_config.yaml to a temp file and return path."""
    config_path: Path = tmp_path / "ota_config.yaml"
    config_path.write_text(yaml.dump(ota_config_dict))
    return config_path


@pytest.fixture
def schema_path() -> Path:
    """Path to the real OTA config JSON Schema."""
    return Path("config/schemas/ota_config.schema.json")


# ---------------------------------------------------------------------------
# Task 1: Config loader tests
# ---------------------------------------------------------------------------


class TestLoadOtaConfig:
    """Tests for config.load_ota_config."""

    def test_load_ota_config_valid(
        self,
        ota_config_yaml_path: Path,
        schema_path: Path,
    ) -> None:
        """load_ota_config returns dict with all required top-level keys."""
        from ems_ota_manager.config import load_ota_config

        result: dict[str, Any] = load_ota_config(ota_config_yaml_path, schema_path)
        assert isinstance(result, dict)
        assert "staging" in result
        assert "partition" in result
        assert "security" in result
        assert "download" in result
        assert "health_check" in result
        assert "version" in result
        assert result["staging"]["dir"] == "/tmp/ems-ota"

    def test_load_ota_config_invalid_rejects(
        self,
        tmp_path: Path,
        schema_path: Path,
    ) -> None:
        """load_ota_config raises ValueError when required field is missing."""
        from ems_ota_manager.config import load_ota_config

        # Missing 'partition' section entirely
        bad_config: dict[str, Any] = {
            "_schema_version": "1.0",
            "staging": {"dir": "/tmp/ems-ota", "max_package_mb": 500},
            "security": {"public_key_hex": "a" * 64},
            "download": {"timeout_s": 300, "chunk_size": 65536, "progress_interval_s": 5},
            "health_check": {
                "timeout_s": 300,
                "poll_interval_s": 10,
                "services": ["safety_manager"],
            },
            "version": {"state_path": "/var/lib/ems/ota_state.json"},
        }
        bad_path: Path = tmp_path / "bad.yaml"
        bad_path.write_text(yaml.dump(bad_config))
        with pytest.raises(ValueError, match="validation failed"):
            load_ota_config(bad_path, schema_path)


# ---------------------------------------------------------------------------
# Task 1: PackageVerifier tests
# ---------------------------------------------------------------------------


class TestPackageVerifierSignature:
    """Tests for Ed25519 signature verification."""

    def test_ed25519_verify_valid(
        self,
        ed25519_keypair: tuple,
        public_key_hex: str,
    ) -> None:
        """verify_manifest accepts valid signature — no exception raised."""
        from ems_ota_manager.verifier import PackageVerifier

        private_key, _ = ed25519_keypair
        manifest_bytes: bytes = b'{"version":"1.0.0","sha256":"abc123"}'
        signature_bytes: bytes = private_key.sign(manifest_bytes)

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        # Should not raise
        verifier.verify_manifest(manifest_bytes, signature_bytes)

    def test_ed25519_verify_invalid_raises(
        self,
        ed25519_keypair: tuple,
        public_key_hex: str,
    ) -> None:
        """verify_manifest raises InvalidSignatureError for tampered manifest."""
        from ems_ota_manager.verifier import InvalidSignatureError, PackageVerifier

        private_key, _ = ed25519_keypair
        manifest_bytes: bytes = b'{"version":"1.0.0","sha256":"abc123"}'
        signature_bytes: bytes = private_key.sign(manifest_bytes)
        tampered: bytes = b'{"version":"1.0.0","sha256":"TAMPERED"}'

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        with pytest.raises(InvalidSignatureError):
            verifier.verify_manifest(tampered, signature_bytes)


class TestPackageVerifierHash:
    """Tests for firmware SHA-256 verification."""

    def test_verify_firmware_hash_match(self, tmp_path: Path, public_key_hex: str) -> None:
        """verify_firmware_hash returns None when hash matches."""
        from ems_ota_manager.verifier import PackageVerifier

        firmware_path: Path = tmp_path / "firmware.img"
        content: bytes = b"firmware binary content 12345"
        firmware_path.write_bytes(content)
        expected: str = hashlib.sha256(content).hexdigest()

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        # Should not raise
        verifier.verify_firmware_hash(firmware_path, expected)

    def test_verify_firmware_hash_mismatch_raises(
        self, tmp_path: Path, public_key_hex: str
    ) -> None:
        """verify_firmware_hash raises ValueError on hash mismatch."""
        from ems_ota_manager.verifier import PackageVerifier

        firmware_path: Path = tmp_path / "firmware.img"
        firmware_path.write_bytes(b"firmware binary content 12345")
        wrong_hash: str = "a" * 64

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            verifier.verify_firmware_hash(firmware_path, wrong_hash)


class TestPackageVerifierExtract:
    """Tests for tar OTA package extraction."""

    def _make_tar(
        self,
        tmp_path: Path,
        members: dict[str, bytes],
    ) -> Path:
        """Helper: create a tar.gz file with given filename->content members."""
        tar_path: Path = tmp_path / "package.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            for name, content in members.items():
                info: tarfile.TarInfo = tarfile.TarInfo(name=name)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
        return tar_path

    def test_extract_ota_package(self, tmp_path: Path, public_key_hex: str) -> None:
        """extract_package extracts members and returns parsed manifest dict."""
        from ems_ota_manager.verifier import PackageVerifier

        firmware_content: bytes = b"\x00" * 1024
        firmware_hash: str = hashlib.sha256(firmware_content).hexdigest()
        manifest: dict[str, Any] = {
            "version": "1.2.3",
            "sha256": firmware_hash,
            "min_version": "1.0.0",
        }
        manifest_bytes: bytes = json.dumps(manifest).encode()

        tar_path: Path = self._make_tar(
            tmp_path,
            {
                "manifest.json": manifest_bytes,
                "firmware.img": firmware_content,
            },
        )
        extract_dir: Path = tmp_path / "extracted"
        extract_dir.mkdir()

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        result: dict[str, Any] = verifier.extract_package(tar_path, extract_dir)

        assert result["version"] == "1.2.3"
        assert (extract_dir / "firmware.img").exists()
        assert (extract_dir / "manifest.json").exists()

    def test_extract_rejects_path_traversal(
        self, tmp_path: Path, public_key_hex: str
    ) -> None:
        """extract_package raises ValueError for tar members with '../' path traversal."""
        from ems_ota_manager.verifier import PackageVerifier

        tar_path: Path = tmp_path / "evil.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            info: tarfile.TarInfo = tarfile.TarInfo(name="../etc/passwd")
            content: bytes = b"malicious"
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        extract_dir: Path = tmp_path / "extracted"
        extract_dir.mkdir()

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        with pytest.raises(ValueError, match="path traversal"):
            verifier.extract_package(tar_path, extract_dir)


class TestPackageVerifierMinVersion:
    """Tests for minimum version checking."""

    def test_min_version_passes(self, public_key_hex: str) -> None:
        """check_min_version passes when current >= min_version."""
        from ems_ota_manager.verifier import PackageVerifier

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        verifier.check_min_version("1.2.0", "1.0.0")  # Should not raise
        verifier.check_min_version("1.0.0", "1.0.0")  # Equal also passes

    def test_min_version_blocks_downgrade(self, public_key_hex: str) -> None:
        """check_min_version raises ValueError when current < min_version."""
        from ems_ota_manager.verifier import PackageVerifier

        verifier: PackageVerifier = PackageVerifier(public_key_hex)
        with pytest.raises(ValueError, match="min_version"):
            verifier.check_min_version("0.9.0", "1.0.0")


# ---------------------------------------------------------------------------
# Task 1: PartitionBackend and BootFlag tests
# ---------------------------------------------------------------------------


class TestBootFlag:
    """Tests for BootFlag and PartitionBackend."""

    def _make_backend(self, tmp_path: Path) -> Any:
        """Helper: create a PartitionBackend with temp boot_flag_path."""
        from ems_ota_manager.partition import PartitionBackend

        config: dict[str, Any] = {
            "partition": {
                "boot_flag_path": str(tmp_path / "boot_flag.json"),
                "active_device": "/dev/mmcblk0p2",
                "standby_device": "/dev/mmcblk0p3",
            }
        }
        return PartitionBackend(config)

    def test_boot_flag_roundtrip(self, tmp_path: Path) -> None:
        """Write a BootFlag then read it back — fields must match."""
        from ems_ota_manager.partition import BootFlag, PartitionBackend

        backend: PartitionBackend = self._make_backend(tmp_path)
        flag: BootFlag = BootFlag(
            active="a",
            previous="b",
            boot_count=3,
            pending_health_check=True,
        )
        backend.write_boot_flag(flag)
        result: BootFlag = backend.read_boot_flag()

        assert result.active == "a"
        assert result.previous == "b"
        assert result.boot_count == 3
        assert result.pending_health_check is True

    def test_boot_flag_atomic_write(self, tmp_path: Path) -> None:
        """Writing boot flag must not leave a .tmp file behind."""
        from ems_ota_manager.partition import BootFlag, PartitionBackend

        backend: PartitionBackend = self._make_backend(tmp_path)
        flag: BootFlag = BootFlag(active="a", previous="b", boot_count=1)
        backend.write_boot_flag(flag)

        # No .tmp file should remain
        tmp_files: list[Path] = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

        # Real file should exist
        boot_flag_path: Path = Path(backend._boot_flag_path)
        assert boot_flag_path.exists()

    def test_partition_get_standby_a(self, tmp_path: Path) -> None:
        """get_standby_partition returns 'b' when active partition is 'a'."""
        from ems_ota_manager.partition import BootFlag, PartitionBackend

        backend: PartitionBackend = self._make_backend(tmp_path)
        backend.write_boot_flag(BootFlag(active="a", previous="b", boot_count=1))
        assert backend.get_standby_partition() == "b"

    def test_partition_get_standby_b(self, tmp_path: Path) -> None:
        """get_standby_partition returns 'a' when active partition is 'b'."""
        from ems_ota_manager.partition import BootFlag, PartitionBackend

        backend: PartitionBackend = self._make_backend(tmp_path)
        backend.write_boot_flag(BootFlag(active="b", previous="a", boot_count=2))
        assert backend.get_standby_partition() == "a"


# ---------------------------------------------------------------------------
# Task 2: HttpDownloader tests
# ---------------------------------------------------------------------------


class TestHttpDownloader:
    """Tests for HttpDownloader streaming download with SHA-256 and resume."""

    FIRMWARE_CONTENT: bytes = b"firmware binary data " * 100  # 2100 bytes

    @property
    def firmware_sha256(self) -> str:
        """Expected SHA-256 of the test firmware content."""
        return hashlib.sha256(self.FIRMWARE_CONTENT).hexdigest()

    def _make_mock_transport(
        self,
        content: bytes,
        *,
        status_code: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.MockTransport:
        """Create a MockTransport that serves fixed content."""
        headers: dict[str, str] = {
            "content-length": str(len(content)),
        }
        if extra_headers:
            headers.update(extra_headers)

        def handler(request: httpx.Request) -> httpx.Response:
            range_header: str | None = request.headers.get("range")
            if range_header and range_header.startswith("bytes="):
                start: int = int(range_header.split("=")[1].split("-")[0])
                sliced: bytes = content[start:]
                return httpx.Response(
                    206,
                    content=sliced,
                    headers={
                        "content-length": str(len(sliced)),
                        "content-range": f"bytes {start}-{len(content)-1}/{len(content)}",
                    },
                )
            return httpx.Response(status_code, content=content, headers=headers)

        return httpx.MockTransport(handler)

    @pytest.mark.asyncio
    async def test_http_download_sha256_ok(self, tmp_path: Path) -> None:
        """Successful download renames .partial to final dest and verifies SHA-256."""
        from ems_ota_manager.downloader import HttpDownloader

        transport: httpx.MockTransport = self._make_mock_transport(self.FIRMWARE_CONTENT)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=tmp_path,
            transport=transport,
        )
        result: Path = await downloader.download(
            url="http://example.com/firmware.img",
            dest_filename="firmware.img",
            expected_sha256=self.firmware_sha256,
        )
        assert result == tmp_path / "firmware.img"
        assert result.exists()
        assert result.read_bytes() == self.FIRMWARE_CONTENT
        # No .partial file left behind
        assert not (tmp_path / "firmware.img.partial").exists()

    @pytest.mark.asyncio
    async def test_download_sha256_mismatch(self, tmp_path: Path) -> None:
        """SHA-256 mismatch raises ValueError and deletes .partial file."""
        from ems_ota_manager.downloader import HttpDownloader

        transport: httpx.MockTransport = self._make_mock_transport(self.FIRMWARE_CONTENT)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=tmp_path,
            transport=transport,
        )
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            await downloader.download(
                url="http://example.com/firmware.img",
                dest_filename="firmware.img",
                expected_sha256="wrong" * 10 + "aaaa",
            )
        # .partial file must be cleaned up
        assert not (tmp_path / "firmware.img.partial").exists()

    @pytest.mark.asyncio
    async def test_download_resume_range_header(self, tmp_path: Path) -> None:
        """Resume: partial file causes Range header; full content SHA-256 passes."""
        from ems_ota_manager.downloader import HttpDownloader

        # Pre-write first half of firmware as .partial
        partial_path: Path = tmp_path / "firmware.img.partial"
        half: int = len(self.FIRMWARE_CONTENT) // 2
        partial_path.write_bytes(self.FIRMWARE_CONTENT[:half])

        transport: httpx.MockTransport = self._make_mock_transport(self.FIRMWARE_CONTENT)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=tmp_path,
            transport=transport,
        )
        result: Path = await downloader.download(
            url="http://example.com/firmware.img",
            dest_filename="firmware.img",
            expected_sha256=self.firmware_sha256,
        )
        assert result.exists()
        assert result.read_bytes() == self.FIRMWARE_CONTENT

    @pytest.mark.asyncio
    async def test_download_size_limit_rejected(self, tmp_path: Path) -> None:
        """Content-Length > max_package_mb raises ValueError before downloading body."""
        from ems_ota_manager.downloader import HttpDownloader

        # 1 byte over limit: max=1MB, Content-Length=2MB
        oversized_content: bytes = b"x" * (2 * 1024 * 1024)
        transport: httpx.MockTransport = self._make_mock_transport(oversized_content)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=tmp_path,
            max_package_mb=1,
            transport=transport,
        )
        with pytest.raises(ValueError, match="too large"):
            await downloader.download(
                url="http://example.com/firmware.img",
                dest_filename="firmware.img",
                expected_sha256="doesnotmatter",
            )

    @pytest.mark.asyncio
    async def test_download_progress_callback(self, tmp_path: Path) -> None:
        """on_progress callback is called at least once during download."""
        from ems_ota_manager.downloader import HttpDownloader

        transport: httpx.MockTransport = self._make_mock_transport(self.FIRMWARE_CONTENT)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=tmp_path,
            transport=transport,
        )
        calls: list[tuple[int, int]] = []

        def on_progress(downloaded: int, total: int) -> None:
            calls.append((downloaded, total))

        await downloader.download(
            url="http://example.com/firmware.img",
            dest_filename="firmware.img",
            expected_sha256=self.firmware_sha256,
            on_progress=on_progress,
        )
        assert len(calls) >= 1
        # Last call should have downloaded == total bytes
        assert calls[-1][0] == len(self.FIRMWARE_CONTENT)

    @pytest.mark.asyncio
    async def test_download_creates_staging_dir(self, tmp_path: Path) -> None:
        """Downloader creates staging_dir if it does not exist."""
        from ems_ota_manager.downloader import HttpDownloader

        staging: Path = tmp_path / "nonexistent" / "staging"
        assert not staging.exists()

        transport: httpx.MockTransport = self._make_mock_transport(self.FIRMWARE_CONTENT)
        downloader: HttpDownloader = HttpDownloader(
            staging_dir=staging,
            transport=transport,
        )
        result: Path = await downloader.download(
            url="http://example.com/firmware.img",
            dest_filename="firmware.img",
            expected_sha256=self.firmware_sha256,
        )
        assert staging.exists()
        assert result.exists()


# ---------------------------------------------------------------------------
# IPC constants smoke test
# ---------------------------------------------------------------------------


class TestIpcConstants:
    """Verify OTA IPC constants are present in ems_common.ipc."""

    def test_ota_ipc_constants_exist(self) -> None:
        """SOCK_OTA_PUB, SOCK_OTA_CMD, TOPIC_OTA must exist in ipc module."""
        from ems_common import ipc

        assert hasattr(ipc, "SOCK_OTA_PUB")
        assert hasattr(ipc, "SOCK_OTA_CMD")
        assert hasattr(ipc, "TOPIC_OTA")
        assert ipc.SOCK_OTA_PUB == "ipc:///run/ems/ota_pub.sock"
        assert ipc.SOCK_OTA_CMD == "ipc:///run/ems/ota_cmd.sock"
        assert ipc.TOPIC_OTA == "ota"


# ---------------------------------------------------------------------------
# Task 1 (Plan 02): HealthChecker tests
# ---------------------------------------------------------------------------


class TestHealthChecker:
    """Tests for HealthChecker service health polling."""

    @pytest.mark.asyncio
    async def test_health_check_passes(self) -> None:
        """check_services_active returns (True, []) when all services are active."""
        from ems_ota_manager.health import HealthChecker

        async def mock_check(svc: str) -> bool:
            return True

        checker: HealthChecker = HealthChecker(
            services=["safety_manager", "comm_manager"],
            check_fn=mock_check,
        )
        all_active, failed = await checker.check_services_active()
        assert all_active is True
        assert failed == []

    @pytest.mark.asyncio
    async def test_health_check_service_down(self) -> None:
        """check_services_active returns (False, ['comm_manager']) when one is inactive."""
        from ems_ota_manager.health import HealthChecker

        async def mock_check(svc: str) -> bool:
            return svc != "comm_manager"

        checker: HealthChecker = HealthChecker(
            services=["safety_manager", "comm_manager"],
            check_fn=mock_check,
        )
        all_active, failed = await checker.check_services_active()
        assert all_active is False
        assert "comm_manager" in failed

    @pytest.mark.asyncio
    async def test_health_check_run_health_check_passes(self) -> None:
        """run_health_check returns True when all services pass immediately."""
        from ems_ota_manager.health import HealthChecker

        async def mock_check(svc: str) -> bool:
            return True

        checker: HealthChecker = HealthChecker(
            services=["safety_manager"],
            timeout_s=5.0,
            poll_interval_s=0.01,
            check_fn=mock_check,
        )
        result: bool = await checker.run_health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_timeout_returns_false(self) -> None:
        """run_health_check returns False when timeout expires without services being healthy."""
        from ems_ota_manager.health import HealthChecker

        async def mock_check(svc: str) -> bool:
            return False  # never healthy

        checker: HealthChecker = HealthChecker(
            services=["safety_manager"],
            timeout_s=0.05,
            poll_interval_s=0.01,
            check_fn=mock_check,
        )
        result: bool = await checker.run_health_check()
        assert result is False


# ---------------------------------------------------------------------------
# Task 1 (Plan 02): OtaStateMachine tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_downloader() -> Any:
    """AsyncMock downloader that returns a fake path."""
    from unittest.mock import AsyncMock, MagicMock

    m: Any = MagicMock()
    m.download = AsyncMock(return_value=Path("/tmp/ems-ota/package.tar"))
    m.cleanup_staging = MagicMock()
    return m


@pytest.fixture
def mock_verifier() -> Any:
    """MagicMock verifier returning a valid manifest."""
    from unittest.mock import MagicMock

    m: Any = MagicMock()
    m.extract_package = MagicMock(
        return_value={
            "version": "1.2.3",
            "sha256": "abc123",
            "min_version": "1.0.0",
            "firmware": "firmware.img",
        }
    )
    m.verify_manifest = MagicMock()
    m.verify_firmware_hash = MagicMock()
    m.check_min_version = MagicMock()
    return m


@pytest.fixture
def mock_partition(tmp_path: Path) -> Any:
    """MagicMock partition backend with a real boot flag file."""
    from unittest.mock import AsyncMock, MagicMock

    from ems_ota_manager.partition import BootFlag, PartitionBackend

    config: dict[str, Any] = {
        "partition": {
            "boot_flag_path": str(tmp_path / "boot_flag.json"),
            "active_device": "/dev/mmcblk0p2",
            "standby_device": "/dev/mmcblk0p3",
        }
    }
    backend: PartitionBackend = PartitionBackend(config)
    # Write initial boot flag
    backend.write_boot_flag(BootFlag(active="a", previous="b", boot_count=1))

    m: Any = MagicMock(wraps=backend)
    m.write_image_to_standby = AsyncMock()
    m.reboot = AsyncMock()
    return m


@pytest.fixture
def mock_health_checker() -> Any:
    """AsyncMock health checker that passes by default."""
    from unittest.mock import AsyncMock, MagicMock

    m: Any = MagicMock()
    m.run_health_check = AsyncMock(return_value=True)
    return m


@pytest.fixture
def state_machine(
    tmp_path: Path,
    mock_downloader: Any,
    mock_verifier: Any,
    mock_partition: Any,
    mock_health_checker: Any,
) -> Any:
    """Build an OtaStateMachine with all mocked dependencies."""
    from ems_ota_manager.state_machine import OtaStateMachine

    version_state_path: Path = tmp_path / "ota_version_state.json"
    return OtaStateMachine(
        downloader=mock_downloader,
        verifier=mock_verifier,
        partition=mock_partition,
        health_checker=mock_health_checker,
        version_state_path=version_state_path,
    )


class TestOtaStateMachineHappyPath:
    """Tests for successful OTA update flow through all states."""

    @pytest.mark.asyncio
    async def test_state_machine_happy_path(self, state_machine: Any) -> None:
        """idle -> downloading -> verifying -> applying -> rebooting on valid update."""
        from ems_ota_manager.state_machine import OtaState

        states_seen: list[OtaState] = []

        def on_change(new_state: OtaState, detail: dict | None) -> None:
            states_seen.append(new_state)

        state_machine._on_state_change_cb = on_change

        assert state_machine.state == OtaState.IDLE
        notification: dict[str, Any] = {
            "version": "1.2.3",
            "url": "http://example.com/firmware.tar",
            "sha256": "abc123",
            "size_bytes": 1024,
        }
        await state_machine.start_update(notification)

        assert OtaState.DOWNLOADING in states_seen
        assert OtaState.VERIFYING in states_seen
        assert OtaState.APPLYING in states_seen
        assert OtaState.REBOOTING in states_seen

    @pytest.mark.asyncio
    async def test_state_machine_records_state_changes(self, state_machine: Any) -> None:
        """Each transition calls the on_state_change callback."""
        from ems_ota_manager.state_machine import OtaState

        transitions: list[tuple[OtaState, Any]] = []

        def on_change(new_state: OtaState, detail: dict | None) -> None:
            transitions.append((new_state, detail))

        state_machine._on_state_change_cb = on_change

        await state_machine.start_update({
            "version": "1.2.3",
            "url": "http://example.com/fw.tar",
            "sha256": "abc",
            "size_bytes": 512,
        })

        assert len(transitions) >= 4


class TestOtaStateMachineFailures:
    """Tests for state machine failure paths."""

    @pytest.mark.asyncio
    async def test_state_machine_download_failure_returns_idle(
        self,
        tmp_path: Path,
        mock_verifier: Any,
        mock_partition: Any,
        mock_health_checker: Any,
    ) -> None:
        """Download failure returns state to IDLE."""
        from unittest.mock import AsyncMock, MagicMock

        from ems_ota_manager.state_machine import OtaState, OtaStateMachine

        bad_downloader: Any = MagicMock()
        bad_downloader.download = AsyncMock(side_effect=RuntimeError("network error"))
        bad_downloader.cleanup_staging = MagicMock()

        sm: OtaStateMachine = OtaStateMachine(
            downloader=bad_downloader,
            verifier=mock_verifier,
            partition=mock_partition,
            health_checker=mock_health_checker,
            version_state_path=tmp_path / "vs.json",
        )

        await sm.start_update({
            "version": "1.2.3",
            "url": "http://example.com/fw.tar",
            "sha256": "abc",
            "size_bytes": 512,
        })
        assert sm.state == OtaState.IDLE

    @pytest.mark.asyncio
    async def test_state_machine_verify_failure_returns_idle(
        self,
        tmp_path: Path,
        mock_downloader: Any,
        mock_partition: Any,
        mock_health_checker: Any,
    ) -> None:
        """InvalidSignatureError during VERIFYING returns state to IDLE."""
        from unittest.mock import MagicMock

        from ems_ota_manager.state_machine import OtaState, OtaStateMachine
        from ems_ota_manager.verifier import InvalidSignatureError

        bad_verifier: Any = MagicMock()
        bad_verifier.extract_package = MagicMock(return_value={
            "version": "1.2.3", "sha256": "abc", "min_version": "1.0.0", "firmware": "fw.img"
        })
        bad_verifier.verify_manifest = MagicMock(
            side_effect=InvalidSignatureError("bad sig")
        )
        bad_verifier.verify_firmware_hash = MagicMock()
        bad_verifier.check_min_version = MagicMock()

        sm: OtaStateMachine = OtaStateMachine(
            downloader=mock_downloader,
            verifier=bad_verifier,
            partition=mock_partition,
            health_checker=mock_health_checker,
            version_state_path=tmp_path / "vs.json",
        )

        await sm.start_update({
            "version": "1.2.3",
            "url": "http://example.com/fw.tar",
            "sha256": "abc",
            "size_bytes": 512,
        })
        assert sm.state == OtaState.IDLE

    @pytest.mark.asyncio
    async def test_min_version_blocks_update(
        self,
        tmp_path: Path,
        mock_downloader: Any,
        mock_partition: Any,
        mock_health_checker: Any,
    ) -> None:
        """State machine rejects update when min_version is higher than current."""
        from unittest.mock import MagicMock

        from ems_ota_manager.state_machine import OtaState, OtaStateMachine

        bad_verifier: Any = MagicMock()
        bad_verifier.extract_package = MagicMock(return_value={
            "version": "1.2.3", "sha256": "abc", "min_version": "2.0.0", "firmware": "fw.img"
        })
        bad_verifier.verify_manifest = MagicMock()
        bad_verifier.verify_firmware_hash = MagicMock()
        bad_verifier.check_min_version = MagicMock(
            side_effect=ValueError("Firmware version 1.2.3 is below min_version 2.0.0")
        )

        sm: OtaStateMachine = OtaStateMachine(
            downloader=mock_downloader,
            verifier=bad_verifier,
            partition=mock_partition,
            health_checker=mock_health_checker,
            version_state_path=tmp_path / "vs.json",
        )

        await sm.start_update({
            "version": "1.2.3",
            "url": "http://example.com/fw.tar",
            "sha256": "abc",
            "size_bytes": 512,
        })
        assert sm.state == OtaState.IDLE


class TestOtaStateMachineHealthAndRollback:
    """Tests for post-boot health check and rollback logic."""

    @pytest.mark.asyncio
    async def test_health_check_timeout_rollback(
        self,
        tmp_path: Path,
        mock_downloader: Any,
        mock_verifier: Any,
        mock_health_checker: Any,
    ) -> None:
        """Health check timeout triggers partition rollback (flag revert + reboot)."""
        from unittest.mock import AsyncMock, MagicMock

        from ems_ota_manager.partition import BootFlag, PartitionBackend
        from ems_ota_manager.state_machine import OtaState, OtaStateMachine

        # Boot flag has pending_health_check=True (simulates post-update startup)
        config: dict[str, Any] = {
            "partition": {
                "boot_flag_path": str(tmp_path / "boot_flag.json"),
                "active_device": "/dev/mmcblk0p2",
                "standby_device": "/dev/mmcblk0p3",
            }
        }
        backend: PartitionBackend = PartitionBackend(config)
        backend.write_boot_flag(BootFlag(
            active="b", previous="a", boot_count=1, pending_health_check=True
        ))
        mock_part: Any = MagicMock(wraps=backend)
        mock_part.write_image_to_standby = AsyncMock()
        mock_part.reboot = AsyncMock()

        # Health checker fails
        failing_hc: Any = MagicMock()
        failing_hc.run_health_check = AsyncMock(return_value=False)

        sm: OtaStateMachine = OtaStateMachine(
            downloader=mock_downloader,
            verifier=mock_verifier,
            partition=mock_part,
            health_checker=failing_hc,
            version_state_path=tmp_path / "vs.json",
        )

        await sm.check_post_boot_health()

        # Partition reboot must have been called (rollback)
        mock_part.reboot.assert_called_once()
        # State should be ROLLED_BACK
        assert sm.state == OtaState.ROLLED_BACK

    @pytest.mark.asyncio
    async def test_post_boot_health_passes_clears_flag(
        self,
        tmp_path: Path,
        mock_downloader: Any,
        mock_verifier: Any,
        mock_health_checker: Any,
    ) -> None:
        """Successful health check clears pending_health_check and sets IDLE."""
        from unittest.mock import AsyncMock, MagicMock

        from ems_ota_manager.partition import BootFlag, PartitionBackend
        from ems_ota_manager.state_machine import OtaState, OtaStateMachine

        config: dict[str, Any] = {
            "partition": {
                "boot_flag_path": str(tmp_path / "boot_flag.json"),
                "active_device": "/dev/mmcblk0p2",
                "standby_device": "/dev/mmcblk0p3",
            }
        }
        backend: PartitionBackend = PartitionBackend(config)
        backend.write_boot_flag(BootFlag(
            active="b", previous="a", boot_count=1, pending_health_check=True
        ))
        mock_part: Any = MagicMock(wraps=backend)
        mock_part.write_image_to_standby = AsyncMock()
        mock_part.reboot = AsyncMock()

        sm: OtaStateMachine = OtaStateMachine(
            downloader=mock_downloader,
            verifier=mock_verifier,
            partition=mock_part,
            health_checker=mock_health_checker,
            version_state_path=tmp_path / "vs.json",
        )

        await sm.check_post_boot_health()

        assert sm.state == OtaState.IDLE
        # Reboot should NOT have been called
        mock_part.reboot.assert_not_called()
        # pending_health_check must be cleared in the boot flag
        flag = backend.read_boot_flag()
        assert flag.pending_health_check is False


class TestVersionStatePersistence:
    """Tests for version state JSON persistence."""

    def test_version_state_persistence(self, tmp_path: Path) -> None:
        """Write version state then read it back -- current and previous match."""
        from ems_ota_manager.state_machine import VersionState

        path: Path = tmp_path / "ota_version_state.json"
        vs: VersionState = VersionState(current="1.2.3", previous="1.0.0")
        vs.save(path)

        loaded: VersionState = VersionState.load(path)
        assert loaded.current == "1.2.3"
        assert loaded.previous == "1.0.0"

    def test_version_state_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """Loading from a non-existent file returns default 'unknown' versions."""
        from ems_ota_manager.state_machine import VersionState

        path: Path = tmp_path / "does_not_exist.json"
        vs: VersionState = VersionState.load(path)
        assert vs.current == "unknown"
        assert vs.previous == "unknown"


# ---------------------------------------------------------------------------
# Task 2 (Plan 02): OtaManager loop tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_state_machine(tmp_path: Path) -> Any:
    """AsyncMock OtaStateMachine for loop tests."""
    from unittest.mock import AsyncMock, MagicMock, PropertyMock

    from ems_ota_manager.state_machine import OtaState, VersionState

    m: Any = MagicMock()
    type(m).state = PropertyMock(return_value=OtaState.IDLE)
    vs: VersionState = VersionState(current="1.0.0", previous="0.9.0")
    type(m).version_state = PropertyMock(return_value=vs)
    m.start_update = AsyncMock()
    m.do_manual_rollback = AsyncMock()
    m.check_post_boot_health = AsyncMock()
    # _on_state_change_cb settable
    return m


@pytest.fixture
def mock_partition_no_pending(tmp_path: Path) -> Any:
    """MagicMock partition with pending_health_check=False."""
    from unittest.mock import MagicMock

    from ems_ota_manager.partition import BootFlag, PartitionBackend

    config: dict[str, Any] = {
        "partition": {
            "boot_flag_path": str(tmp_path / "boot_flag_loop.json"),
            "active_device": "/dev/mmcblk0p2",
            "standby_device": "/dev/mmcblk0p3",
        }
    }
    backend: PartitionBackend = PartitionBackend(config)
    backend.write_boot_flag(BootFlag(active="a", previous="b", boot_count=1))
    m: Any = MagicMock(wraps=backend)
    return m


@pytest.fixture
def mock_partition_pending(tmp_path: Path) -> Any:
    """MagicMock partition with pending_health_check=True."""
    from unittest.mock import MagicMock

    from ems_ota_manager.partition import BootFlag, PartitionBackend

    config: dict[str, Any] = {
        "partition": {
            "boot_flag_path": str(tmp_path / "boot_flag_pending.json"),
            "active_device": "/dev/mmcblk0p2",
            "standby_device": "/dev/mmcblk0p3",
        }
    }
    backend: PartitionBackend = PartitionBackend(config)
    backend.write_boot_flag(
        BootFlag(active="b", previous="a", boot_count=1, pending_health_check=True)
    )
    m: Any = MagicMock(wraps=backend)
    return m


@pytest.fixture
def ota_loop_config(ota_config_dict: dict[str, Any]) -> dict[str, Any]:
    """Minimal OTA config dict for loop tests."""
    return ota_config_dict


class TestOtaManagerStatusPublish:
    """Tests for ZMQ PUB status publishing."""

    @pytest.mark.asyncio
    async def test_status_published_on_state_change(
        self,
        mock_state_machine: Any,
        mock_partition_no_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """_on_state_change publishes ZMQ telemetry with topic 'ota' and state+version."""
        import zmq
        import zmq.asyncio

        from ems_common.ipc import TOPIC_OTA, decode_telemetry
        from ems_ota_manager.loop import OtaManager
        from ems_ota_manager.state_machine import OtaState

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            # Use inproc to avoid port allocation; bind PUB first
            pub_ep: str = "inproc://test_ota_pub_status"
            sub: zmq.asyncio.Socket = ctx.socket(zmq.SUB)
            pub: zmq.asyncio.Socket = ctx.socket(zmq.PUB)
            pub.bind(pub_ep)
            sub.connect(pub_ep)
            sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_OTA)
            await asyncio.sleep(0.05)  # slow-joiner

            manager: OtaManager = OtaManager(
                config=ota_loop_config,
                state_machine=mock_state_machine,
                partition=mock_partition_no_pending,
                ota_pub_socket=pub,
            )

            manager._on_state_change(OtaState.DOWNLOADING, {"version": "1.2.3"})
            await asyncio.sleep(0.05)

            topic_bytes, body = await sub.recv_multipart()
            assert topic_bytes.decode() == TOPIC_OTA
            decoded: dict[str, Any] = decode_telemetry(body)
            assert decoded["payload"]["state"] == "downloading"
            assert "version_current" in decoded["payload"]
        finally:
            sub.close()
            pub.close()
            ctx.term()


class TestOtaManagerZmqCommands:
    """Tests for ZMQ REP command handling."""

    @pytest.mark.asyncio
    async def test_version_query_zmq_rep(
        self,
        mock_state_machine: Any,
        mock_partition_no_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """get_version command returns current and previous versions."""
        import zmq
        import zmq.asyncio

        from ems_common.ipc import (
            decode_command_response,
            encode_command_request,
        )
        from ems_ota_manager.loop import OtaManager

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            rep: zmq.asyncio.Socket = ctx.socket(zmq.REP)
            port: int = rep.bind_to_random_port("tcp://127.0.0.1")
            bound_ep: str = f"tcp://127.0.0.1:{port}"

            manager: OtaManager = OtaManager(
                config=ota_loop_config,
                state_machine=mock_state_machine,
                partition=mock_partition_no_pending,
                ota_rep_socket=rep,
            )

            req: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req.connect(bound_ep)
            await req.send(encode_command_request("get_version", {}))
            # Give ZMQ time to deliver the message to the REP socket
            await asyncio.sleep(0.05)

            # Simulate one command loop iteration
            await manager._handle_one_command()

            resp_bytes: bytes = await asyncio.wait_for(req.recv(), timeout=2.0)
            resp: dict[str, Any] = decode_command_response(resp_bytes)
            assert resp["status"] == "ok"
            assert resp["result"]["current"] == "1.0.0"
            assert resp["result"]["previous"] == "0.9.0"
        finally:
            req.close(linger=0)
            rep.close(linger=0)
            ctx.term()

    @pytest.mark.asyncio
    async def test_rollback_command_zmq_rep(
        self,
        mock_state_machine: Any,
        mock_partition_no_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """rollback command triggers state_machine.do_manual_rollback."""
        import zmq
        import zmq.asyncio

        from ems_common.ipc import decode_command_response, encode_command_request
        from ems_ota_manager.loop import OtaManager

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            rep: zmq.asyncio.Socket = ctx.socket(zmq.REP)
            port: int = rep.bind_to_random_port("tcp://127.0.0.1")
            bound_ep: str = f"tcp://127.0.0.1:{port}"

            manager: OtaManager = OtaManager(
                config=ota_loop_config,
                state_machine=mock_state_machine,
                partition=mock_partition_no_pending,
                ota_rep_socket=rep,
            )

            req: zmq.asyncio.Socket = ctx.socket(zmq.REQ)
            req.connect(bound_ep)
            await req.send(encode_command_request("rollback", {}))
            # Give ZMQ time to deliver the message to the REP socket
            await asyncio.sleep(0.05)

            await manager._handle_one_command()

            resp_bytes: bytes = await asyncio.wait_for(req.recv(), timeout=2.0)
            resp: dict[str, Any] = decode_command_response(resp_bytes)
            assert resp["status"] == "ok"
            mock_state_machine.do_manual_rollback.assert_called_once()
        finally:
            req.close(linger=0)
            rep.close(linger=0)
            ctx.term()


class TestOtaManagerStartup:
    """Tests for OtaManager startup behavior."""

    @pytest.mark.asyncio
    async def test_startup_health_check_triggered(
        self,
        mock_state_machine: Any,
        mock_partition_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """Pending health check flag on startup triggers check_post_boot_health."""
        from ems_ota_manager.loop import OtaManager

        manager: OtaManager = OtaManager(
            config=ota_loop_config,
            state_machine=mock_state_machine,
            partition=mock_partition_pending,
        )
        await manager._maybe_run_post_boot_health()
        mock_state_machine.check_post_boot_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_no_health_check_when_not_pending(
        self,
        mock_state_machine: Any,
        mock_partition_no_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """No health check triggered when pending_health_check=False."""
        from ems_ota_manager.loop import OtaManager

        manager: OtaManager = OtaManager(
            config=ota_loop_config,
            state_machine=mock_state_machine,
            partition=mock_partition_no_pending,
        )
        await manager._maybe_run_post_boot_health()
        mock_state_machine.check_post_boot_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_shutdown(
        self,
        mock_state_machine: Any,
        mock_partition_no_pending: Any,
        ota_loop_config: dict[str, Any],
    ) -> None:
        """stop_event.set() causes run() to exit cleanly."""
        import zmq
        import zmq.asyncio

        from ems_ota_manager.loop import OtaManager

        ctx: zmq.asyncio.Context = zmq.asyncio.Context()
        try:
            pub: zmq.asyncio.Socket = ctx.socket(zmq.PUB)
            pub.bind("tcp://127.0.0.1:0")
            rep: zmq.asyncio.Socket = ctx.socket(zmq.REP)
            rep.bind("tcp://127.0.0.1:0")

            manager: OtaManager = OtaManager(
                config=ota_loop_config,
                state_machine=mock_state_machine,
                partition=mock_partition_no_pending,
                ota_pub_socket=pub,
                ota_rep_socket=rep,
            )

            # Schedule stop immediately
            async def _stop_soon() -> None:
                await asyncio.sleep(0.05)
                manager.stop_event.set()

            asyncio.create_task(_stop_soon())
            # run() should return rather than hang
            await asyncio.wait_for(manager.run(), timeout=2.0)
        finally:
            pub.close(linger=0)
            rep.close(linger=0)
            ctx.term()


class TestOtaManagerEntryPoint:
    """Smoke tests for __main__.py imports and module structure."""

    def test_main_module_importable(self) -> None:
        """ems_ota_manager.__main__ can be imported without errors."""
        import importlib

        mod = importlib.import_module("ems_ota_manager.__main__")
        assert hasattr(mod, "main")
        assert hasattr(mod, "run")
        assert hasattr(mod, "parse_args")

    def test_init_exports(self) -> None:
        """OtaState, OtaStateMachine, and OtaManager exported from package."""
        from ems_ota_manager import OtaManager, OtaState, OtaStateMachine

        assert OtaState.IDLE is not None
        assert OtaStateMachine is not None
        assert OtaManager is not None


# ---------------------------------------------------------------------------
# UBootPartitionBackend tests
# ---------------------------------------------------------------------------


@pytest.fixture
def uboot_config() -> dict[str, Any]:
    """Valid partition config dict for UBootPartitionBackend."""
    return {
        "partition": {
            "active_device": "/dev/mmcblk0p2",
            "standby_device": "/dev/mmcblk0p3",
            "fw_env_config": "/etc/fw_env.config",
        }
    }


class TestUBootPartitionBackend:
    """Tests for UBootPartitionBackend using fw_printenv/fw_setenv."""

    def _make_backend(self, uboot_config: dict[str, Any]) -> Any:
        """Helper: create a UBootPartitionBackend from config dict."""
        from ems_ota_manager.partition import UBootPartitionBackend

        return UBootPartitionBackend(uboot_config)

    def test_uboot_backend_read_active_slot(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """read_boot_flag calls fw_printenv for ems_active_slot and ems_boot_count."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            mock.returncode = 0
            if "ems_active_slot" in cmd:
                mock.stdout = b"a\n"
            elif "ems_boot_count" in cmd:
                mock.stdout = b"1\n"
            elif "ems_pending_health_check" in cmd:
                mock.stdout = b"0\n"
            else:
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            flag: BootFlag = backend.read_boot_flag()

        assert flag.active == "a"
        assert flag.previous == "b"
        assert flag.boot_count == 1
        assert flag.pending_health_check is False

    def test_uboot_backend_read_slot_b(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """read_boot_flag with active=b derives previous=a."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            mock.returncode = 0
            if "ems_active_slot" in cmd:
                mock.stdout = b"b\n"
            elif "ems_boot_count" in cmd:
                mock.stdout = b"2\n"
            elif "ems_pending_health_check" in cmd:
                mock.stdout = b"1\n"
            else:
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            flag: BootFlag = backend.read_boot_flag()

        assert flag.active == "b"
        assert flag.previous == "a"
        assert flag.boot_count == 2
        assert flag.pending_health_check is True

    def test_uboot_backend_set_active_slot(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """write_boot_flag calls fw_setenv for ems_active_slot, ems_boot_count, ems_pending_health_check."""
        from unittest.mock import call, patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)
        flag: BootFlag = BootFlag(
            active="b", previous="a", boot_count=0, pending_health_check=True
        )

        mock_run: MagicMock = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            backend.write_boot_flag(flag)

        calls: list[call] = mock_run.call_args_list
        # Expect 3 fw_setenv calls
        assert len(calls) == 3

        # Extract the command lists
        cmds: list[list[str]] = [c[0][0] for c in calls]

        # Verify each fw_setenv call
        assert ["fw_setenv", "ems_active_slot", "b"] in cmds
        assert ["fw_setenv", "ems_boot_count", "0"] in cmds
        assert ["fw_setenv", "ems_pending_health_check", "1"] in cmds

    def test_uboot_backend_set_active_slot_false_health_check(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """write_boot_flag with pending_health_check=False sets ems_pending_health_check to 0."""
        from unittest.mock import call, patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)
        flag: BootFlag = BootFlag(
            active="a", previous="b", boot_count=0, pending_health_check=False
        )

        mock_run: MagicMock = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            backend.write_boot_flag(flag)

        cmds: list[list[str]] = [c[0][0] for c in mock_run.call_args_list]
        assert ["fw_setenv", "ems_pending_health_check", "0"] in cmds

    def test_uboot_backend_get_standby(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """get_standby_partition returns opposite of active slot."""
        from unittest.mock import patch

        from ems_ota_manager.partition import UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run_a(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            mock.returncode = 0
            if "ems_active_slot" in cmd:
                mock.stdout = b"a\n"
            elif "ems_boot_count" in cmd:
                mock.stdout = b"0\n"
            elif "ems_pending_health_check" in cmd:
                mock.stdout = b"0\n"
            else:
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run_a):
            assert backend.get_standby_partition() == "b"

        def fake_run_b(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            mock.returncode = 0
            if "ems_active_slot" in cmd:
                mock.stdout = b"b\n"
            elif "ems_boot_count" in cmd:
                mock.stdout = b"0\n"
            elif "ems_pending_health_check" in cmd:
                mock.stdout = b"0\n"
            else:
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run_b):
            assert backend.get_standby_partition() == "a"

    def test_uboot_backend_read_default_slot(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """When fw_printenv returns empty/error for boot_count, defaults to 0."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            if "ems_active_slot" in cmd:
                mock.returncode = 0
                mock.stdout = b"a\n"
            elif "ems_boot_count" in cmd:
                # Simulate key not set (non-zero exit)
                mock.returncode = 1
                mock.stdout = b""
            elif "ems_pending_health_check" in cmd:
                mock.returncode = 1
                mock.stdout = b""
            else:
                mock.returncode = 0
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            flag: BootFlag = backend.read_boot_flag()

        assert flag.boot_count == 0
        assert flag.pending_health_check is False

    def test_uboot_backend_fw_setenv_failure(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """RuntimeError raised when fw_setenv returns non-zero exit code."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)
        flag: BootFlag = BootFlag(active="b", previous="a", boot_count=0)

        mock_run: MagicMock = MagicMock()
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b"Error: can't open /dev/mtd0\n"

        with patch("subprocess.run", mock_run):
            with pytest.raises(RuntimeError, match="fw_setenv"):
                backend.write_boot_flag(flag)

    def test_uboot_backend_fw_printenv_failure(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """RuntimeError raised when fw_printenv returns non-zero for ems_active_slot."""
        from unittest.mock import patch

        from ems_ota_manager.partition import UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            # ems_active_slot always fails
            mock.returncode = 1
            mock.stdout = b""
            mock.stderr = b"## Error: \"ems_active_slot\" not defined\n"
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="fw_printenv"):
                backend.read_boot_flag()

    def test_uboot_backend_rollback_on_high_boot_count(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """When boot_count > 2, read_boot_flag returns flag as-is (rollback handled by U-Boot)."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            mock: MagicMock = MagicMock()
            mock.returncode = 0
            if "ems_active_slot" in cmd:
                mock.stdout = b"b\n"
            elif "ems_boot_count" in cmd:
                mock.stdout = b"3\n"
            elif "ems_pending_health_check" in cmd:
                mock.stdout = b"1\n"
            else:
                mock.stdout = b""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            flag: BootFlag = backend.read_boot_flag()

        # Python side just reads and returns; rollback is U-Boot's responsibility
        assert flag.boot_count == 3
        assert flag.active == "b"

    def test_uboot_backend_clear_boot_count(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """write_boot_flag with boot_count=0 calls fw_setenv ems_boot_count 0."""
        from unittest.mock import patch

        from ems_ota_manager.partition import BootFlag, UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)
        flag: BootFlag = BootFlag(
            active="a", previous="b", boot_count=0, pending_health_check=False
        )

        mock_run: MagicMock = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            backend.write_boot_flag(flag)

        cmds: list[list[str]] = [c[0][0] for c in mock_run.call_args_list]
        assert ["fw_setenv", "ems_boot_count", "0"] in cmds

    def test_original_partition_backend_preserved(self, tmp_path: Path) -> None:
        """PartitionBackend class still works with JSON file (backward compatible)."""
        from ems_ota_manager.partition import BootFlag, PartitionBackend

        config: dict[str, Any] = {
            "partition": {
                "boot_flag_path": str(tmp_path / "boot_flag.json"),
                "active_device": "/dev/mmcblk0p2",
                "standby_device": "/dev/mmcblk0p3",
            }
        }
        backend: PartitionBackend = PartitionBackend(config)
        flag: BootFlag = BootFlag(
            active="a", previous="b", boot_count=1, pending_health_check=False
        )
        backend.write_boot_flag(flag)
        result: BootFlag = backend.read_boot_flag()
        assert result.active == "a"
        assert result.previous == "b"
        assert result.boot_count == 1

    def test_uboot_backend_config_defaults(self) -> None:
        """UBootPartitionBackend initialises with optional fw_env_config defaulting to /etc/fw_env.config."""
        from ems_ota_manager.partition import UBootPartitionBackend

        # Config without fw_env_config key
        config: dict[str, Any] = {
            "partition": {
                "active_device": "/dev/mmcblk0p2",
                "standby_device": "/dev/mmcblk0p3",
            }
        }
        backend: UBootPartitionBackend = UBootPartitionBackend(config)
        assert backend._fw_env_config == "/etc/fw_env.config"

    @pytest.mark.asyncio
    async def test_uboot_backend_write_image_to_standby(
        self, uboot_config: dict[str, Any], tmp_path: Path
    ) -> None:
        """write_image_to_standby uses dd to flash image to standby device."""
        from unittest.mock import AsyncMock, patch

        from ems_ota_manager.partition import UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)
        image_path: Path = tmp_path / "firmware.img"
        image_path.write_bytes(b"fake firmware")

        mock_proc: MagicMock = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await backend.write_image_to_standby(image_path)

        # Verify dd was called with standby device
        call_args: tuple = mock_exec.call_args[0]
        assert call_args[0] == "dd"
        assert f"of=/dev/mmcblk0p3" in call_args

    @pytest.mark.asyncio
    async def test_uboot_backend_reboot(
        self, uboot_config: dict[str, Any]
    ) -> None:
        """reboot() calls systemctl reboot."""
        from unittest.mock import AsyncMock, patch

        from ems_ota_manager.partition import UBootPartitionBackend

        backend: UBootPartitionBackend = self._make_backend(uboot_config)

        mock_proc: MagicMock = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await backend.reboot()

        call_args: tuple = mock_exec.call_args[0]
        assert call_args[0] == "systemctl"
        assert "reboot" in call_args
