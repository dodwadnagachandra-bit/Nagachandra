#!/opt/ems/python/.venv/bin/python3
"""Continuous soak test monitor for ECU-1170-552A 24-hour hardware validation.

Runs on the ECU for a configurable duration (default 24 hours). Every 60 seconds,
checks service restart counts, RSS memory growth, Parquet file freshness, and disk
space. Writes results as JSONL to /data/soak_monitor.jsonl.

Usage (on ECU):
    python3 soak_monitor.py [--duration SECONDS] [--output PATH]

Exit code:
    0 — PASS (all checks passed for full duration)
    1 — FAIL (restart detected, RSS growth >10%, or data gap)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DURATION_S: int = 86400  # 24 hours
CHECK_INTERVAL_S: int = 60       # check every 60 seconds
RSS_BASELINE_SAMPLES: int = 60   # 1 hour of samples before computing baseline
RSS_GROWTH_THRESHOLD: float = 1.10  # 10% growth triggers a flag
PARQUET_FRESHNESS_S: int = 10    # parquet file must be <10s old
DISK_WARN_PCT: float = 80.0      # warn if /data >80% full
DATA_DIR: str = "/data"

SERVICES: list[str] = [
    "data_manager",
    "ems-data-manager-python",
    "config_manager",
    "safety_manager",
    "comm_manager_c",
    "comm_manager",
    "logger",
    "control_manager",
    "alarm_manager",
    "scheduler",
    "diagnostics",
    "cloud_manager",
    "ota_manager",
    "hmi_server",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: str) -> tuple[int, str]:
    """Run a shell command and return (returncode, stdout)."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        cmd, shell=True, capture_output=True, text=True
    )
    return result.returncode, result.stdout.strip()


def _now_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _get_service_nrestarts(service: str) -> int:
    """Return NRestarts property for a systemd service, or -1 on error."""
    rc, out = _run(f"systemctl show {service} --property=NRestarts")
    if rc != 0 or not out.startswith("NRestarts="):
        return -1
    try:
        return int(out.split("=", 1)[1])
    except ValueError:
        return -1


def _get_service_pid(service: str) -> int:
    """Return MainPID for a systemd service, or -1 on error."""
    rc, out = _run(f"systemctl show {service} --property=MainPID")
    if rc != 0 or not out.startswith("MainPID="):
        return -1
    try:
        return int(out.split("=", 1)[1])
    except ValueError:
        return -1


def _get_rss_kb(pid: int) -> int:
    """Read RSS (kB) from /proc/<pid>/status, or -1 on error."""
    try:
        status_path: Path = Path(f"/proc/{pid}/status")
        for line in status_path.read_text().splitlines():
            if line.startswith("VmRSS:"):
                # Format: "VmRSS:   12345 kB"
                parts: list[str] = line.split()
                return int(parts[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return -1


def _get_parquet_freshness() -> bool:
    """Return True if the most recent .parquet file in /data was modified <10s ago."""
    try:
        data_path: Path = Path(DATA_DIR)
        parquet_files: list[Path] = list(data_path.glob("**/*.parquet"))
        if not parquet_files:
            return False
        most_recent: float = max(f.stat().st_mtime for f in parquet_files)
        age_s: float = time.time() - most_recent
        return age_s < PARQUET_FRESHNESS_S
    except OSError:
        return False


def _get_disk_pct() -> float:
    """Return /data partition usage as a percentage (0.0–100.0), or -1.0 on error."""
    try:
        stat: os.statvfs_result = os.statvfs(DATA_DIR)
        total: int = stat.f_blocks * stat.f_frsize
        available: int = stat.f_bavail * stat.f_frsize
        if total == 0:
            return -1.0
        used_pct: float = (total - available) / total * 100.0
        return round(used_pct, 1)
    except OSError:
        return -1.0


# ---------------------------------------------------------------------------
# SoakMonitor class
# ---------------------------------------------------------------------------


class SoakMonitor:
    """Continuous soak test monitor.

    Checks service restarts, RSS memory growth, Parquet freshness, and disk
    space every 60 seconds. Writes JSONL results to an output file. Handles
    SIGTERM/SIGINT gracefully by writing a summary before exiting.
    """

    def __init__(self, duration_s: int, output_path: str) -> None:
        self.duration_s: int = duration_s
        self.output_path: str = output_path
        self.start_time: float = time.time()
        self.start_epoch: float = self.start_time

        # Baseline restart counts captured at start
        self.baseline_restarts: dict[str, int] = {}

        # RSS samples per service: {service: [rss_kb, ...]}
        self.rss_samples: dict[str, list[int]] = {svc: [] for svc in SERVICES}
        # RSS baselines (set after RSS_BASELINE_SAMPLES samples)
        self.rss_baselines: dict[str, Optional[int]] = {svc: None for svc in SERVICES}

        # Cumulative counters
        self.total_restarts: int = 0
        self.data_gaps: int = 0
        self.max_rss_growth_pct: float = 0.0
        self.check_count: int = 0

        # Termination flag (set by signal handler)
        self._terminate: bool = False

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Set termination flag when SIGTERM or SIGINT received."""
        self._terminate = True

    def _capture_baseline_restarts(self) -> None:
        """Capture NRestarts baseline for all services at monitor start."""
        for svc in SERVICES:
            restarts: int = _get_service_nrestarts(svc)
            self.baseline_restarts[svc] = restarts if restarts >= 0 else 0

    def _write_jsonl(self, record: dict[str, object]) -> None:
        """Append a JSON record as a single line to the output file."""
        with open(self.output_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()

    def _check_services(self) -> tuple[int, int]:
        """Check service restart counts.

        Returns:
            (services_ok, new_restarts_this_check)
        """
        services_ok: int = 0
        new_restarts: int = 0

        for svc in SERVICES:
            current: int = _get_service_nrestarts(svc)
            baseline: int = self.baseline_restarts.get(svc, 0)

            if current < 0:
                # Service not found or systemctl error — count as problem
                continue

            if current > baseline:
                delta: int = current - baseline
                new_restarts += delta
                self.total_restarts += delta
                # Update baseline so we don't double-count
                self.baseline_restarts[svc] = current
                print(
                    f"  [RESTART] {svc}: {delta} restart(s) detected "
                    f"(total for service: {current})",
                    flush=True,
                )
            else:
                services_ok += 1

        return services_ok, new_restarts

    def _check_rss(self) -> float:
        """Check RSS growth for all services.

        Returns:
            max_growth_pct across all services this check (0.0 if no growth)
        """
        max_growth: float = 0.0

        for svc in SERVICES:
            pid: int = _get_service_pid(svc)
            if pid <= 0:
                continue

            rss_kb: int = _get_rss_kb(pid)
            if rss_kb < 0:
                continue

            samples: list[int] = self.rss_samples[svc]
            samples.append(rss_kb)

            # Set baseline after RSS_BASELINE_SAMPLES samples (1 hour)
            if self.rss_baselines[svc] is None and len(samples) >= RSS_BASELINE_SAMPLES:
                # Use median of first 60 samples as baseline
                sorted_samples: list[int] = sorted(samples[:RSS_BASELINE_SAMPLES])
                self.rss_baselines[svc] = sorted_samples[RSS_BASELINE_SAMPLES // 2]
                print(
                    f"  [BASELINE] {svc}: RSS baseline set to "
                    f"{self.rss_baselines[svc]} kB",
                    flush=True,
                )

            baseline_kb: Optional[int] = self.rss_baselines[svc]
            if baseline_kb is not None and baseline_kb > 0:
                growth_ratio: float = rss_kb / baseline_kb
                if growth_ratio > RSS_GROWTH_THRESHOLD:
                    growth_pct: float = (growth_ratio - 1.0) * 100.0
                    max_growth = max(max_growth, growth_pct)
                    print(
                        f"  [RSS_GROWTH] {svc}: {rss_kb} kB "
                        f"(+{growth_pct:.1f}% above baseline {baseline_kb} kB)",
                        flush=True,
                    )

        self.max_rss_growth_pct = max(self.max_rss_growth_pct, max_growth)
        return max_growth

    def _run_check(self) -> None:
        """Run one full check cycle and write a heartbeat JSONL record."""
        self.check_count += 1
        print(
            f"[CHECK {self.check_count}] {_now_iso()} — "
            f"{(time.time() - self.start_epoch) / 3600:.1f}h elapsed",
            flush=True,
        )

        services_ok, new_restarts = self._check_services()
        max_rss_growth_pct: float = self._check_rss()
        parquet_fresh: bool = _get_parquet_freshness()
        disk_pct: float = _get_disk_pct()

        if not parquet_fresh:
            self.data_gaps += 1
            print(f"  [DATA_GAP] No fresh Parquet file in {DATA_DIR}", flush=True)

        if disk_pct > DISK_WARN_PCT:
            print(f"  [DISK_WARN] {DATA_DIR} at {disk_pct:.1f}%", flush=True)

        record: dict[str, object] = {
            "timestamp": _now_iso(),
            "check": "heartbeat",
            "services_ok": services_ok,
            "restarts": new_restarts,
            "max_rss_growth_pct": round(max_rss_growth_pct, 2),
            "parquet_fresh": parquet_fresh,
            "disk_pct": disk_pct,
        }
        self._write_jsonl(record)

    def _write_summary(self) -> None:
        """Write the final summary JSONL record."""
        elapsed_s: float = time.time() - self.start_epoch
        result: str = "PASS"

        # Determine PASS/FAIL
        if self.total_restarts > 0:
            result = "FAIL"
            print(
                f"[FAIL] {self.total_restarts} service restart(s) detected", flush=True
            )
        if self.max_rss_growth_pct >= (RSS_GROWTH_THRESHOLD - 1.0) * 100.0:
            result = "FAIL"
            print(
                f"[FAIL] Max RSS growth {self.max_rss_growth_pct:.1f}% exceeds 10%",
                flush=True,
            )
        if self.data_gaps > 0:
            result = "FAIL"
            print(f"[FAIL] {self.data_gaps} Parquet data gap(s) detected", flush=True)

        summary: dict[str, object] = {
            "timestamp": _now_iso(),
            "check": "summary",
            "duration_s": round(elapsed_s),
            "total_restarts": self.total_restarts,
            "max_rss_growth_pct": round(self.max_rss_growth_pct, 2),
            "data_gaps": self.data_gaps,
            "total_checks": self.check_count,
            "result": result,
        }
        self._write_jsonl(summary)
        print(f"\n[SOAK] Completed. Result: {result}", flush=True)
        print(f"  Duration:       {elapsed_s / 3600:.2f} hours", flush=True)
        print(f"  Total restarts: {self.total_restarts}", flush=True)
        print(f"  Max RSS growth: {self.max_rss_growth_pct:.1f}%", flush=True)
        print(f"  Data gaps:      {self.data_gaps}", flush=True)
        print(f"  Output:         {self.output_path}", flush=True)

    def run(self) -> int:
        """Run the soak monitor loop.

        Returns:
            0 on PASS, 1 on FAIL.
        """
        print(
            f"[SOAK] Starting {self.duration_s / 3600:.0f}-hour soak monitor",
            flush=True,
        )
        print(f"  Output: {self.output_path}", flush=True)
        print(f"  Check interval: {CHECK_INTERVAL_S}s", flush=True)

        self._capture_baseline_restarts()
        end_time: float = self.start_time + self.duration_s
        next_check: float = self.start_time + CHECK_INTERVAL_S

        while time.time() < end_time and not self._terminate:
            now: float = time.time()
            if now >= next_check:
                self._run_check()
                next_check = now + CHECK_INTERVAL_S
            # Sleep in short intervals to remain responsive to signals
            sleep_s: float = min(1.0, next_check - time.time())
            if sleep_s > 0:
                time.sleep(sleep_s)

        if self._terminate:
            print("\n[SOAK] Terminated by signal — writing summary.", flush=True)

        self._write_summary()

        # Exit code
        failed: bool = (
            self.total_restarts > 0
            or self.max_rss_growth_pct >= (RSS_GROWTH_THRESHOLD - 1.0) * 100.0
            or self.data_gaps > 0
        )
        return 1 if failed else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="EMS 24-hour soak test monitor for ECU-1170-552A hardware validation"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_S,
        metavar="SECONDS",
        help=f"Monitoring duration in seconds (default: {DEFAULT_DURATION_S} = 24h)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=f"{DATA_DIR}/soak_monitor.jsonl",
        metavar="PATH",
        help="Output JSONL file path (default: /data/soak_monitor.jsonl)",
    )
    return parser.parse_args()


def main() -> int:
    """Entry point."""
    args: argparse.Namespace = _parse_args()
    monitor: SoakMonitor = SoakMonitor(
        duration_s=args.duration,
        output_path=args.output,
    )
    return monitor.run()


if __name__ == "__main__":
    sys.exit(main())
