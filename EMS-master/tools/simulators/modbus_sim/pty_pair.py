"""PTY pair management via socat for Modbus RTU transport."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

log: logging.Logger = logging.getLogger(__name__)

# Default PTY symlink paths
DEFAULT_SIM_PATH: str = "/tmp/ems_pcs_sim"
DEFAULT_CLIENT_PATH: str = "/tmp/ems_pcs_client"


class PTYPair:
    """Manages a socat PTY pair for virtual serial communication.

    Creates two linked pseudo-terminals:
    - sim_path: for the Modbus server (simulator)
    - client_path: for the Modbus client (tests or comm_manager)

    socat is launched as a subprocess and torn down on close().
    """

    def __init__(
        self,
        sim_path: str = DEFAULT_SIM_PATH,
        client_path: str = DEFAULT_CLIENT_PATH,
    ) -> None:
        self.sim_path: str = sim_path
        self.client_path: str = client_path
        self._process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        """Start socat to create the PTY pair. Blocks until PTYs are ready."""
        # Clean up stale symlinks
        for p in (self.sim_path, self.client_path):
            path: Path = Path(p)
            if path.exists() or path.is_symlink():
                path.unlink()

        cmd: list[str] = [
            "socat",
            f"pty,raw,echo=0,link={self.sim_path}",
            f"pty,raw,echo=0,link={self.client_path}",
        ]
        log.info("Starting socat: %s", " ".join(cmd))
        self._process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        # Wait for PTY symlinks to appear
        deadline: float = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if Path(self.sim_path).exists() and Path(self.client_path).exists():
                log.info("PTY pair ready: sim=%s client=%s", self.sim_path, self.client_path)
                return
            time.sleep(0.05)

        self.close()
        msg: str = f"Timeout waiting for PTY pair: {self.sim_path}, {self.client_path}"
        raise RuntimeError(msg)

    def close(self) -> None:
        """Terminate socat and clean up PTY symlinks."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None
            log.info("socat terminated")

        for p in (self.sim_path, self.client_path):
            path: Path = Path(p)
            if path.exists() or path.is_symlink():
                path.unlink()

    @property
    def is_open(self) -> bool:
        """True if socat process is running."""
        return self._process is not None and self._process.poll() is None

    def __enter__(self) -> PTYPair:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
