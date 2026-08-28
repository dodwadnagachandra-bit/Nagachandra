"""Tests for GPIO test harness -- SIM-05 coverage."""

from __future__ import annotations

import ctypes
import os
import subprocess
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import pytest

from ems_common.rtdb import RTDB_MAGIC, RTDB_VERSION, EmsRtdb, EmsSeqlock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_shm_name() -> str:
    """Unique shm name per test to avoid collisions."""
    return f"ems_rtdb_test_{os.getpid()}_{id(object())}"


@pytest.fixture()
def rtdb_backend(test_shm_name: str):
    """Create an RtdbBackend backed by a fresh test shm segment."""
    from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

    backend = RtdbBackend(shm_name=test_shm_name)
    yield backend
    backend.close()


@pytest.fixture()
def gpio_config() -> "GpioConfig":
    """Load GpioConfig from project config."""
    from tools.simulators.gpio_harness.config import GpioConfig

    config_path = Path(__file__).parent.parent / "config" / "gpio_config.yaml"
    return GpioConfig(config_path=config_path)


# ---------------------------------------------------------------------------
# RTDB Backend Tests
# ---------------------------------------------------------------------------

class TestRtdbBackendDI:
    """Test DI set/get via RTDB shared memory."""

    def test_set_di_all_pins(self, rtdb_backend) -> None:
        """RtdbBackend.set_di(pin, 1) for each pin 0-7, then get_di(pin) returns 1."""
        for pin in range(8):
            rtdb_backend.set_di(pin, 1)
            assert rtdb_backend.get_di(pin) == 1, f"DI pin {pin} should be 1"

    def test_set_di_clear(self, rtdb_backend) -> None:
        """Setting a pin to 1 then 0 reads back as 0."""
        rtdb_backend.set_di(0, 1)
        assert rtdb_backend.get_di(0) == 1
        rtdb_backend.set_di(0, 0)
        assert rtdb_backend.get_di(0) == 0


class TestRtdbBackendDO:
    """Test DO read from RTDB shared memory."""

    def test_get_do_all_pins(self, rtdb_backend) -> None:
        """Manually write do_state[pin]=1 in RTDB, then get_do(pin) returns 1."""
        for pin in range(8):
            # Directly poke the RTDB do_state array (simulating safety_manager output)
            rtdb_backend._rtdb.gpio.do_state[pin] = 1
            assert rtdb_backend.get_do(pin) == 1, f"DO pin {pin} should be 1"


class TestMultiPin:
    """Test multi-pin atomic operations."""

    def test_estop_dual_channel(self, rtdb_backend) -> None:
        """set_di_multi({6: 1, 7: 0}) writes both atomically."""
        rtdb_backend.set_di_multi({6: 1, 7: 0})
        assert rtdb_backend.get_di(6) == 1, "ESTOP_NO (DI-6) should be 1"
        assert rtdb_backend.get_di(7) == 0, "ESTOP_NC (DI-7) should be 0"

    def test_fire_dual_confirm(self, rtdb_backend) -> None:
        """set_di_multi({3: 1, 4: 1}) writes both atomically."""
        rtdb_backend.set_di_multi({3: 1, 4: 1})
        assert rtdb_backend.get_di(3) == 1, "SMOKE_DETECTOR (DI-3) should be 1"
        assert rtdb_backend.get_di(4) == 1, "HEAT_DETECTOR (DI-4) should be 1"

    def test_flood_signal(self, rtdb_backend) -> None:
        """set_di(1, 1) then get_di(1) returns 1."""
        rtdb_backend.set_di(1, 1)
        assert rtdb_backend.get_di(1) == 1

    def test_acdb_feedback(self, rtdb_backend) -> None:
        """set_di(0, 1) then get_di(0) returns 1."""
        rtdb_backend.set_di(0, 1)
        assert rtdb_backend.get_di(0) == 1

    def test_multi_pin_atomic(self, rtdb_backend) -> None:
        """Seqlock sequence increments by 2 after set_di_multi."""
        seq_before: int = rtdb_backend._rtdb.gpio.lock.sequence
        rtdb_backend.set_di_multi({6: 1, 7: 0})
        seq_after: int = rtdb_backend._rtdb.gpio.lock.sequence
        assert seq_after == seq_before + 2, (
            f"Sequence should increment by 2: {seq_before} -> {seq_after}"
        )
        # Sequence should be even (write complete)
        assert seq_after % 2 == 0, "Sequence should be even after write"


class TestGpioConfig:
    """Test pin name resolution and active_low polarity."""

    def test_pin_name_resolution(self, gpio_config) -> None:
        """GpioConfig.resolve_di resolves names and DI-N syntax."""
        assert gpio_config.resolve_di("ESTOP_NO") == 6
        assert gpio_config.resolve_di("DI-6") == 6
        assert gpio_config.resolve_do("PCS_STOP") == 5
        assert gpio_config.resolve_do("DO-5") == 5

    def test_logical_polarity(self, gpio_config) -> None:
        """apply_active_low_di inverts value for active_low pins."""
        # DI-7 (ESTOP_NC) is active_low: logical True -> raw 0
        assert gpio_config.apply_active_low_di(7, logical=True) == 0
        # DI-6 (ESTOP_NO) is NOT active_low: logical True -> raw 1
        assert gpio_config.apply_active_low_di(6, logical=True) == 1

    def test_get_di_name(self, gpio_config) -> None:
        """get_di_name returns config name for pin number."""
        assert gpio_config.get_di_name(6) == "ESTOP_NO"
        assert gpio_config.get_do_name(5) == "PCS_STOP"

    def test_resolve_case_insensitive(self, gpio_config) -> None:
        """Pin name resolution is case-insensitive."""
        assert gpio_config.resolve_di("estop_no") == 6
        assert gpio_config.resolve_do("pcs_stop") == 5


class TestBackendAutodetect:
    """Test backend factory auto-detection."""

    def test_backend_autodetect_rtdb(self, test_shm_name: str) -> None:
        """detect_backend('auto') returns RtdbBackend when gpio-sim not available."""
        from tools.simulators.gpio_harness.backend import detect_backend
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        backend = detect_backend("auto", shm_name=test_shm_name)
        try:
            assert isinstance(backend, RtdbBackend)
        finally:
            backend.close()

    def test_backend_explicit_rtdb(self, test_shm_name: str) -> None:
        """detect_backend('rtdb') always returns RtdbBackend."""
        from tools.simulators.gpio_harness.backend import detect_backend
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        backend = detect_backend("rtdb", shm_name=test_shm_name)
        try:
            assert isinstance(backend, RtdbBackend)
        finally:
            backend.close()


class TestShmLifecycle:
    """Test shared memory creation/cleanup."""

    def test_shm_create_fallback(self) -> None:
        """RtdbBackend creates shm if it does not exist."""
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        name = f"ems_rtdb_create_{os.getpid()}"
        backend = RtdbBackend(shm_name=name)
        try:
            # Verify magic and version were initialized
            assert backend._rtdb.magic == RTDB_MAGIC
            assert backend._rtdb.version == RTDB_VERSION
        finally:
            backend.close()

    def test_shm_cleanup(self) -> None:
        """RtdbBackend.close() properly cleans up shm."""
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        name = f"ems_rtdb_cleanup_{os.getpid()}"
        backend = RtdbBackend(shm_name=name)
        backend.close()
        # Shm should be unlinked since we created it
        shm_path = Path(f"/dev/shm/{name}")
        assert not shm_path.exists(), f"Shm segment {name} should be unlinked after close"


# ---------------------------------------------------------------------------
# CLI Integration Tests
# ---------------------------------------------------------------------------

class TestCLI:
    """Test CLI subcommands via subprocess."""

    @pytest.fixture(autouse=True)
    def _setup_cli_shm(self, test_shm_name: str) -> None:
        """Store test shm name for CLI tests."""
        self.shm_name: str = test_shm_name

    @pytest.fixture(autouse=True)
    def _setup_backend(self, test_shm_name: str):
        """Create shm segment that CLI can attach to."""
        from tools.simulators.gpio_harness.rtdb_backend import RtdbBackend

        self._backend = RtdbBackend(shm_name=test_shm_name)
        yield
        self._backend.close()

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the GPIO harness CLI with test shm name."""
        import subprocess

        cmd: list[str] = [
            "uv", "run", "python", "-m", "tools.simulators.gpio_harness",
            "--backend", "rtdb",
            "--shm-name", self.shm_name,
            *args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=10,
        )

    def test_cli_set_get(self) -> None:
        """CLI set then get round-trip."""
        result = self._run_cli("set", "DI-6", "high")
        assert result.returncode == 0

        result = self._run_cli("get", "DI-6", "--raw")
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_cli_multi_pin(self) -> None:
        """CLI multi-pin set using PIN=VALUE syntax."""
        result = self._run_cli("set", "DI-6=high", "DI-7=low")
        assert result.returncode == 0

        result = self._run_cli("get", "DI-6", "--raw")
        assert result.stdout.strip() == "1"

        result = self._run_cli("get", "DI-7", "--raw")
        assert result.stdout.strip() == "0"

    def test_cli_get_all(self) -> None:
        """CLI get all prints 16 lines in raw mode."""
        result = self._run_cli("get", "all", "--raw")
        assert result.returncode == 0
        lines: list[str] = result.stdout.strip().split("\n")
        assert len(lines) == 16, f"Expected 16 lines, got {len(lines)}"

    def test_cli_named_pin(self) -> None:
        """CLI set using config name."""
        result = self._run_cli("set", "ESTOP_NO", "high")
        assert result.returncode == 0

        result = self._run_cli("get", "DI-6", "--raw")
        assert result.stdout.strip() == "1"

    def test_cli_logical_flag(self) -> None:
        """CLI --logical applies active_low polarity."""
        # DI-7 (ESTOP_NC) is active_low: --logical high -> writes raw 0
        result = self._run_cli("set", "DI-7", "high", "--logical")
        assert result.returncode == 0

        result = self._run_cli("get", "DI-7", "--raw")
        assert result.stdout.strip() == "0"
