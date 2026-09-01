"""Async process manager — subprocess lifecycle, log capture, health checks."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.tui.models import (
    AutoRestartConfig,
    HealthCheckConfig,
    PhaseConfig,
    ProcessConfig,
    ProcessState,
    ProcessStatus,
    TuiConfig,
)

logger = logging.getLogger(__name__)

StateCallback = Callable[[str, ProcessStatus, ProcessStatus], Any]
LogCallback = Callable[[str, str], Any]


class ProcessManager:
    """Manages subprocess lifecycle for all EMS processes.

    Pure async — no TUI dependency. Communicates via callbacks.
    """

    def __init__(
        self,
        config: TuiConfig,
        profile: str,
        working_dir: Path,
        on_state_change: StateCallback | None = None,
        on_log_line: LogCallback | None = None,
    ) -> None:
        self.config: TuiConfig = config
        self.profile: str = profile
        self.working_dir: Path = working_dir
        self.auto_restart: AutoRestartConfig = config.auto_restart
        self._on_state_change: StateCallback | None = on_state_change
        self._on_log_line: LogCallback | None = on_log_line

        # Build ordered list of phases and process states
        self.phases: list[PhaseConfig] = sorted(config.phases, key=lambda p: p.order)
        self.states: dict[str, ProcessState] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._reader_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._watcher_tasks: dict[str, asyncio.Task[None]] = {}
        self._restart_tasks: dict[str, asyncio.Task[None]] = {}

        for phase in self.phases:
            for proc_cfg in phase.processes:
                self.states[proc_cfg.id] = ProcessState(proc_cfg)

    def _resolve_command(self, command: str) -> str:
        """Substitute {profile} placeholder in command strings."""
        return command.replace("{profile}", self.profile)

    def _set_status(self, process_id: str, new_status: ProcessStatus) -> None:
        state = self.states[process_id]
        old_status = state.status
        state.status = new_status
        if self._on_state_change:
            self._on_state_change(process_id, old_status, new_status)

    def _append_log(self, process_id: str, line: str) -> None:
        state = self.states[process_id]
        state.log_lines.append(line)
        if self._on_log_line:
            self._on_log_line(process_id, line)

    def _append_stderr(self, process_id: str, line: str) -> None:
        self.states[process_id].stderr_lines.append(line)

    async def _read_stream(
        self,
        process_id: str,
        stream: asyncio.StreamReader,
        is_stderr: bool = False,
    ) -> None:
        """Read lines from a subprocess stream and append to log."""
        while True:
            try:
                line_bytes = await stream.readline()
            except (ValueError, ConnectionError):
                break
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            prefix = "[stderr] " if is_stderr else ""
            self._append_log(process_id, f"{prefix}{line}")
            if is_stderr:
                self._append_stderr(process_id, line)

    async def _run_health_check(self, hc: HealthCheckConfig) -> bool:
        """Run a single health check probe. Returns True if healthy."""
        if hc.type == "command":
            if hc.command is None:
                return True
            proc = await asyncio.create_subprocess_shell(
                hc.command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await proc.wait() == 0

        elif hc.type == "tcp_port":
            if hc.port is None:
                return True
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex(("127.0.0.1", hc.port))
                sock.close()
                return result == 0
            except OSError:
                return False

        elif hc.type == "shm_exists":
            if hc.path is None:
                return True
            return Path(hc.path).exists()

        return True

    async def _wait_healthy(self, process_id: str, hc: HealthCheckConfig) -> bool:
        """Poll health check until passing or timeout."""
        deadline = time.monotonic() + hc.timeout_sec
        while time.monotonic() < deadline:
            if await self._run_health_check(hc):
                return True
            await asyncio.sleep(0.3)
        self._append_log(process_id, f"[tui] Health check timed out after {hc.timeout_sec}s")
        return False

    async def _watch_process(self, process_id: str) -> None:
        """Wait for process exit and handle crash/restart."""
        proc = self._processes.get(process_id)
        if proc is None:
            return

        returncode = await proc.wait()
        state = self.states[process_id]
        state.exit_code = returncode
        state.pid = None

        # Clean up reader tasks and transport
        for task in self._reader_tasks.pop(process_id, []):
            task.cancel()
        self._close_transport(proc)
        self._processes.pop(process_id, None)

        if state.status == ProcessStatus.STOPPING:
            # Expected stop
            self._set_status(process_id, ProcessStatus.STOPPED)
            return

        if state.config.type == "oneshot":
            if returncode == 0 or state.config.ignore_failure:
                self._set_status(process_id, ProcessStatus.DONE)
            else:
                state.crash_time = time.time()
                self._set_status(process_id, ProcessStatus.CRASHED)
                self._append_log(
                    process_id, f"[tui] Oneshot failed with exit code {returncode}"
                )
            return

        # Daemon crashed
        state.crash_time = time.time()
        self._set_status(process_id, ProcessStatus.CRASHED)
        self._append_log(process_id, f"[tui] Process exited with code {returncode}")

        # Auto-restart if enabled
        if self.auto_restart.enabled and state.restart_count < self.auto_restart.max_retries:
            delay = min(
                self.auto_restart.backoff_base_sec * (2 ** state.restart_count),
                self.auto_restart.backoff_max_sec,
            )
            self._append_log(
                process_id,
                f"[tui] Auto-restart in {delay:.1f}s (attempt {state.restart_count + 1}/{self.auto_restart.max_retries})",
            )
            task = asyncio.create_task(self._delayed_restart(process_id, delay))
            self._restart_tasks[process_id] = task

    async def _delayed_restart(self, process_id: str, delay: float) -> None:
        """Wait then restart a crashed process."""
        try:
            await asyncio.sleep(delay)
            state = self.states[process_id]
            if state.status == ProcessStatus.CRASHED:
                state.restart_count += 1
                await self.start_process(process_id)
        except asyncio.CancelledError:
            pass

    def _check_prereq_satisfied(self, process_id: str) -> bool:
        """Check if a prerequisite process is already satisfied (skip sudo)."""
        if process_id == "vcan_setup":
            try:
                # Check if vcan0 interface already exists and is UP
                import subprocess

                result = subprocess.run(
                    ["ip", "link", "show", "vcan0"],
                    capture_output=True,
                    timeout=2,
                )
                if result.returncode == 0 and b"UP" in result.stdout:
                    self._append_log(process_id, "[tui] vcan0 already up — skipping setup")
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        elif process_id == "ipc_dir":
            if Path("/run/ems").is_dir():
                self._append_log(process_id, "[tui] /run/ems already exists — skipping")
                return True
        return False

    async def start_process(self, process_id: str) -> bool:
        """Start a single process. Returns True if launched successfully."""
        state = self.states[process_id]
        config = state.config

        # Cancel any pending restart
        if process_id in self._restart_tasks:
            self._restart_tasks.pop(process_id).cancel()

        # Don't start if already running
        if state.status in (ProcessStatus.RUNNING, ProcessStatus.STARTING):
            return True

        # Check if prerequisite is already satisfied (avoids sudo prompts)
        if config.type == "oneshot" and self._check_prereq_satisfied(process_id):
            state.status = ProcessStatus.DONE
            self._set_status(process_id, ProcessStatus.DONE)
            return True

        state.reset_for_start()
        self._set_status(process_id, ProcessStatus.STARTING)

        command = self._resolve_command(config.command)
        self._append_log(process_id, f"[tui] Starting: {command}")

        try:
            env = os.environ.copy()
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.working_dir),
                env=env,
                start_new_session=True,  # Own process group for clean kills
            )
        except OSError as e:
            self._append_log(process_id, f"[tui] Failed to start: {e}")
            state.crash_time = time.time()
            self._set_status(process_id, ProcessStatus.CRASHED)
            return False

        state.pid = proc.pid
        self._processes[process_id] = proc

        # Start reader tasks for stdout and stderr
        readers: list[asyncio.Task[None]] = []
        if proc.stdout:
            readers.append(asyncio.create_task(self._read_stream(process_id, proc.stdout)))
        if proc.stderr:
            readers.append(
                asyncio.create_task(self._read_stream(process_id, proc.stderr, is_stderr=True))
            )
        self._reader_tasks[process_id] = readers

        # Start watcher task
        self._watcher_tasks[process_id] = asyncio.create_task(self._watch_process(process_id))

        if config.type == "oneshot":
            # For oneshots, just let the watcher handle completion
            return True

        # For daemons, run health check or wait a beat
        if config.health_check:
            healthy = await self._wait_healthy(process_id, config.health_check)
            if healthy and state.status == ProcessStatus.STARTING:
                self._set_status(process_id, ProcessStatus.RUNNING)
                self._append_log(process_id, "[tui] Health check passed")
            elif state.status == ProcessStatus.STARTING:
                # Health check failed but process might still be alive
                self._set_status(process_id, ProcessStatus.RUNNING)
                self._append_log(process_id, "[tui] Health check failed, process may be unstable")
        else:
            # No health check — wait briefly then mark running if still alive
            await asyncio.sleep(0.5)
            if state.status == ProcessStatus.STARTING and proc.returncode is None:
                self._set_status(process_id, ProcessStatus.RUNNING)

        return True

    def _close_transport(self, proc: asyncio.subprocess.Process) -> None:
        """Close subprocess transport to prevent 'Event loop is closed' errors on GC."""
        transport = proc._transport  # type: ignore[attr-defined]
        if transport and not transport.is_closing():
            transport.close()

    async def stop_process(self, process_id: str) -> None:
        """Stop a single process gracefully (SIGTERM, then SIGKILL after 5s)."""
        # Cancel any pending restart
        if process_id in self._restart_tasks:
            self._restart_tasks.pop(process_id).cancel()

        proc = self._processes.get(process_id)
        state = self.states[process_id]

        if proc is None or proc.returncode is not None:
            # Close transport on already-dead processes
            if proc is not None:
                self._close_transport(proc)
                self._processes.pop(process_id, None)
            if state.status not in (ProcessStatus.STOPPED, ProcessStatus.DONE):
                self._set_status(process_id, ProcessStatus.STOPPED)
            return

        self._set_status(process_id, ProcessStatus.STOPPING)
        self._append_log(process_id, "[tui] Stopping (SIGTERM to process group)...")

        try:
            # Kill entire process group (handles forked children like uvicorn workers)
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._append_log(process_id, "[tui] SIGTERM timeout, sending SIGKILL")
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        except ProcessLookupError:
            pass

        # Close transport to prevent GC errors after loop closes
        self._close_transport(proc)
        self._processes.pop(process_id, None)

        state.pid = None
        self._set_status(process_id, ProcessStatus.STOPPED)
        self._append_log(process_id, "[tui] Stopped")

    async def restart_process(self, process_id: str) -> None:
        """Restart a single process."""
        await self.stop_process(process_id)
        self.states[process_id].restart_count = 0
        await self.start_process(process_id)

    async def start_all(self) -> None:
        """Start all processes in phase order."""
        for phase in self.phases:
            if not phase.auto_start:
                continue

            if phase.parallel:
                tasks = [self.start_process(p.id) for p in phase.processes]
                await asyncio.gather(*tasks)
            else:
                for proc_cfg in phase.processes:
                    await self.start_process(proc_cfg.id)

            # Brief pause between phases
            await asyncio.sleep(0.3)

    async def stop_all(self) -> None:
        """Stop all processes in reverse phase order."""
        for phase in reversed(self.phases):
            tasks = [self.stop_process(p.id) for p in phase.processes]
            await asyncio.gather(*tasks)

    async def cleanup(self) -> None:
        """Cancel all background tasks and stop all processes."""
        # Cancel all restart tasks
        for task in self._restart_tasks.values():
            task.cancel()
        self._restart_tasks.clear()

        await self.stop_all()

        # Cancel all watcher tasks
        for task in self._watcher_tasks.values():
            task.cancel()
        self._watcher_tasks.clear()

        # Close any remaining transports
        for proc in self._processes.values():
            self._close_transport(proc)
        self._processes.clear()

    def get_all_process_ids(self) -> list[str]:
        """Return all process IDs in phase order."""
        result: list[str] = []
        for phase in self.phases:
            for proc_cfg in phase.processes:
                result.append(proc_cfg.id)
        return result

    def get_phase_for_process(self, process_id: str) -> str:
        """Return the phase name for a given process ID."""
        for phase in self.phases:
            for proc_cfg in phase.processes:
                if proc_cfg.id == process_id:
                    return phase.name
        return "?"

    def get_profile_affected_ids(self) -> list[str]:
        """Return process IDs whose commands contain the {profile} placeholder."""
        return [pid for pid, st in self.states.items() if "{profile}" in st.config.command]
