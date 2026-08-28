"""Data models for TUI process configuration and runtime state."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml


class ProcessStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    CRASHED = "crashed"
    STOPPING = "stopping"
    DONE = "done"  # oneshot completed successfully


@dataclass
class HealthCheckConfig:
    type: Literal["command", "tcp_port", "shm_exists"]
    command: str | None = None
    port: int | None = None
    path: str | None = None
    timeout_sec: float = 5.0


@dataclass
class ProcessConfig:
    id: str
    name: str
    command: str
    type: Literal["daemon", "oneshot"] = "daemon"
    ignore_failure: bool = False
    requires_sudo: bool = False
    health_check: HealthCheckConfig | None = None


@dataclass
class PhaseConfig:
    name: str
    order: int
    parallel: bool = True
    auto_start: bool = True
    processes: list[ProcessConfig] = field(default_factory=list)


@dataclass
class AutoRestartConfig:
    enabled: bool = True
    max_retries: int = 5
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 30.0


@dataclass
class TuiConfig:
    phases: list[PhaseConfig]
    profiles: list[str] = field(
        default_factory=lambda: ["residential", "commercial", "container"]
    )
    default_profile: str = "residential"
    auto_restart: AutoRestartConfig = field(default_factory=AutoRestartConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> TuiConfig:
        with open(path) as f:
            data = yaml.safe_load(f)

        ar_data = data.get("auto_restart", {})
        auto_restart = AutoRestartConfig(**ar_data)

        phases: list[PhaseConfig] = []
        for phase_data in data.get("phases", []):
            procs: list[ProcessConfig] = []
            for p in phase_data.get("processes", []):
                hc_data = p.pop("health_check", None)
                hc = HealthCheckConfig(**hc_data) if hc_data else None
                procs.append(ProcessConfig(**p, health_check=hc))
            phase_data.pop("processes", None)
            phases.append(PhaseConfig(**phase_data, processes=procs))

        return cls(
            phases=phases,
            profiles=data.get("profiles", ["residential", "commercial", "container"]),
            default_profile=data.get("default_profile", "residential"),
            auto_restart=auto_restart,
        )


class ProcessState:
    """Mutable runtime state for a process."""

    def __init__(self, config: ProcessConfig) -> None:
        self.config: ProcessConfig = config
        self.status: ProcessStatus = ProcessStatus.STOPPED
        self.pid: int | None = None
        self.start_time: float | None = None
        self.crash_time: float | None = None
        self.restart_count: int = 0
        self.exit_code: int | None = None
        self.log_lines: deque[str] = deque(maxlen=10_000)
        self.stderr_lines: deque[str] = deque(maxlen=50)

    @property
    def uptime(self) -> str:
        if self.start_time is None or self.status not in (
            ProcessStatus.RUNNING,
            ProcessStatus.STARTING,
        ):
            return "--"
        elapsed = int(time.time() - self.start_time)
        if elapsed < 60:
            return f"{elapsed}s"
        if elapsed < 3600:
            return f"{elapsed // 60}m{elapsed % 60:02d}s"
        hours = elapsed // 3600
        mins = (elapsed % 3600) // 60
        return f"{hours}h{mins:02d}m"

    def reset_for_start(self) -> None:
        self.status = ProcessStatus.STARTING
        self.pid = None
        self.start_time = time.time()
        self.crash_time = None
        self.exit_code = None
        self.log_lines.clear()
        self.stderr_lines.clear()
