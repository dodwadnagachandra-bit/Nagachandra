#!/usr/bin/env python3
"""Hardware Validation Report Generator for ECU-1170-552A.

Collects results from all 5 hardware validation stages and renders a single
structured markdown report. Reads benchmark JSON from Plan 02, soak JSONL
from Stage 5, and accepts manual oscilloscope data via CLI args for Stage 4.

Usage:
    python3 hw_validation_report.py [--ecu-ip IP] [--gpio-p50 MS] [--gpio-p95 MS]
                                    [--gpio-p99 MS] [--gpio-samples N]
                                    [--output PATH] [--offline]
                                    [--ecu-serial SERIAL] [--image-version VER]
                                    [--tester NAME]

Exit codes:
    0 — All stages that have data report PASS
    1 — One or more stages FAIL

Examples:
    # Collect live data from ECU + enter oscilloscope measurements:
    python3 hw_validation_report.py --ecu-ip 192.168.1.100 \\
        --gpio-p50 12 --gpio-p95 28 --gpio-p99 45 --gpio-samples 100

    # Offline mode (use cached data files only, no SSH):
    python3 hw_validation_report.py --offline \\
        --gpio-p50 12 --gpio-p95 28 --gpio-p99 45 --gpio-samples 100
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ECU_IP: str = "192.168.1.100"
DEFAULT_ECU_USER: str = "root"
DATA_DIR: str = "/data"
SOAK_JSONL_PATH: str = f"{DATA_DIR}/soak_monitor.jsonl"
BENCHMARK_JSON_PATH: str = f"{DATA_DIR}/benchmark_results.json"
DEFAULT_OUTPUT: str = "docs/hardware-validation-report.md"

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

PASS: str = "PASS"
FAIL: str = "FAIL"
NA: str = "N/A"


# ---------------------------------------------------------------------------
# SSH helper
# ---------------------------------------------------------------------------


def _ssh(ecu_ip: str, ecu_user: str, command: str) -> tuple[int, str]:
    """Run a shell command on the ECU via SSH.

    Returns:
        (returncode, stdout) — stdout is stripped of leading/trailing whitespace.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            f"{ecu_user}@{ecu_ip}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip()


def _scp_from_ecu(
    ecu_ip: str, ecu_user: str, remote_path: str, local_path: Path
) -> bool:
    """Copy a file from the ECU to a local path.

    Returns:
        True if successful, False otherwise.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"{ecu_user}@{ecu_ip}:{remote_path}",
            str(local_path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------


def _collect_stage1_live(ecu_ip: str, ecu_user: str) -> dict[str, object]:
    """Collect Stage 1 boot validation data via SSH."""
    service_results: dict[str, str] = {}

    for svc in SERVICES:
        rc, out = _ssh(ecu_ip, ecu_user, f"systemctl is-active {svc}")
        service_results[svc] = PASS if out.strip() == "active" else FAIL

    # Boot time from systemd-analyze
    rc, analyze_out = _ssh(ecu_ip, ecu_user, "systemd-analyze time 2>/dev/null || echo N/A")
    boot_time_s: float = -1.0
    if "Startup finished" in analyze_out:
        # Parse: "Startup finished in ...s = Xs"
        try:
            for part in analyze_out.split():
                if part.endswith("s") and part[:-1].replace(".", "").isdigit():
                    boot_time_s = float(part[:-1])
                    break
        except (ValueError, IndexError):
            pass

    all_pass: bool = all(v == PASS for v in service_results.values())
    return {
        "services": service_results,
        "boot_time_s": boot_time_s,
        "result": PASS if all_pass else FAIL,
    }


def _collect_stage2_live(ecu_ip: str, ecu_user: str) -> dict[str, object]:
    """Collect Stage 2 driver verification data via SSH."""
    results: dict[str, object] = {}

    # CAN
    rc0, _ = _ssh(ecu_ip, ecu_user, "ip link show can0")
    rc1, _ = _ssh(ecu_ip, ecu_user, "ip link show can1")
    results["can0_exists"] = PASS if rc0 == 0 else FAIL
    results["can1_exists"] = PASS if rc1 == 0 else FAIL

    # GPIO
    rc_g, gpio_out = _ssh(ecu_ip, ecu_user, "gpiodetect 2>/dev/null || echo ''")
    results["gpio_chip"] = gpio_out.splitlines()[0] if gpio_out else ""
    results["gpio_detected"] = PASS if gpio_out else FAIL

    # Network
    rc_net, net_out = _ssh(ecu_ip, ecu_user, "ip -br link")
    eth_links: list[str] = [
        line for line in net_out.splitlines() if line.startswith("eth") or "eth" in line
    ]
    results["eth_count"] = len(eth_links)
    results["network_ok"] = PASS if len(eth_links) >= 2 else FAIL

    # HDMI / DRM
    rc_drm, _ = _ssh(ecu_ip, ecu_user, "ls /sys/class/drm/ 2>/dev/null | head -1")
    results["drm_ok"] = PASS if rc_drm == 0 else FAIL

    all_pass: bool = all(
        str(v) == PASS
        for k, v in results.items()
        if isinstance(v, str) and v in (PASS, FAIL)
    )
    results["result"] = PASS if all_pass else FAIL
    return results


def _collect_stage3_live(ecu_ip: str, ecu_user: str) -> dict[str, object]:
    """Collect Stage 3 data path validation data via SSH."""
    results: dict[str, str] = {}

    rc, _ = _ssh(ecu_ip, ecu_user, "ls /dev/shm/ems_rtdb 2>/dev/null")
    results["rtdb_shm"] = PASS if rc == 0 else FAIL

    rc, zmq_out = _ssh(ecu_ip, ecu_user, "ss -x | grep ems_telemetry 2>/dev/null || echo ''")
    results["zmq_pub"] = PASS if zmq_out.strip() else FAIL

    rc, parquet_out = _ssh(ecu_ip, ecu_user, "ls /data/*.parquet 2>/dev/null | head -1")
    results["parquet_exists"] = PASS if rc == 0 and parquet_out.strip() else FAIL

    # DuckDB query check (if duckdb installed)
    rc, ddb_out = _ssh(
        ecu_ip,
        ecu_user,
        "python3 -c \""
        "import duckdb, glob, os; "
        "files = glob.glob('/data/*.parquet'); "
        "print('OK' if files and duckdb.query('SELECT COUNT(*) FROM read_parquet(' + repr(files[0]) + ')').fetchone()[0] > 0 else 'EMPTY')"
        "\" 2>/dev/null || echo N/A",
    )
    results["duckdb_ok"] = PASS if "OK" in ddb_out else FAIL

    all_pass: bool = all(v == PASS for v in results.values())
    return {"checks": results, "result": PASS if all_pass else FAIL}


def _collect_soak_results(
    ecu_ip: str, ecu_user: str, offline: bool
) -> dict[str, object]:
    """Read Stage 5 soak test results from JSONL summary line."""
    # Try to find summary line in local cache first, then SSH
    local_cache: Path = Path("/tmp/soak_monitor.jsonl")

    if not offline:
        _scp_from_ecu(ecu_ip, ecu_user, SOAK_JSONL_PATH, local_cache)

    source: Path = local_cache if local_cache.exists() else Path(SOAK_JSONL_PATH)
    if not source.exists():
        return {"result": NA, "error": "No soak results file found"}

    # Read last summary line
    summary: Optional[dict[str, object]] = None
    try:
        with open(source) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record: dict[str, object] = json.loads(line)
                    if record.get("check") == "summary":
                        summary = record
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        return {"result": NA, "error": str(exc)}

    if summary is None:
        return {"result": NA, "error": "No summary record found in soak JSONL"}

    return summary


def _collect_benchmarks(
    ecu_ip: str, ecu_user: str, offline: bool
) -> dict[str, object]:
    """Read ARM64 benchmark results from benchmark_results.json."""
    local_cache: Path = Path("/tmp/benchmark_results.json")

    if not offline:
        _scp_from_ecu(ecu_ip, ecu_user, BENCHMARK_JSON_PATH, local_cache)

    source: Path = local_cache if local_cache.exists() else Path(BENCHMARK_JSON_PATH)
    if not source.exists():
        return {"error": "No benchmark_results.json found — run Plan 02 benchmarks first"}

    try:
        with open(source) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}


def _collect_hw_config_live(ecu_ip: str, ecu_user: str) -> dict[str, str]:
    """Collect hardware configuration details via SSH."""
    config: dict[str, str] = {}

    _, out = _ssh(ecu_ip, ecu_user, "uname -r")
    config["kernel"] = out or "[unknown]"

    _, out = _ssh(ecu_ip, ecu_user, "gpiodetect 2>/dev/null | head -1 || echo ''")
    config["gpio_chip"] = out or "[unknown]"

    _, out = _ssh(ecu_ip, ecu_user, "ip link show type can 2>/dev/null || echo ''")
    config["can_interfaces"] = out.replace("\n", " ") if out else "[none]"

    _, out = _ssh(ecu_ip, ecu_user, "ls /dev/ttyS* 2>/dev/null || echo ''")
    config["rs485_uarts"] = out.replace("\n", " ") if out else "[none]"

    _, out = _ssh(ecu_ip, ecu_user, "ip -br link 2>/dev/null || echo ''")
    config["eth_interfaces"] = out.replace("\n", " ") if out else "[unknown]"

    _, out = _ssh(ecu_ip, ecu_user, "ls /boot/*.dtb 2>/dev/null | head -1 || echo ''")
    config["dtb"] = Path(out).name if out else "[unknown]"

    return config


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------


def _pf(value: object) -> str:
    """Format a value as PASS, FAIL, or N/A for display."""
    if value is None:
        return NA
    s: str = str(value).upper()
    if s in (PASS, FAIL, NA):
        return s
    return str(value)


class ReportRenderer:
    """Renders the hardware validation markdown report from collected data."""

    def __init__(
        self,
        args: argparse.Namespace,
        stage1: dict[str, object],
        stage2: dict[str, object],
        stage3: dict[str, object],
        soak: dict[str, object],
        benchmarks: dict[str, object],
        hw_config: dict[str, str],
    ) -> None:
        self.args: argparse.Namespace = args
        self.stage1: dict[str, object] = stage1
        self.stage2: dict[str, object] = stage2
        self.stage3: dict[str, object] = stage3
        self.soak: dict[str, object] = soak
        self.benchmarks: dict[str, object] = benchmarks
        self.hw_config: dict[str, str] = hw_config
        self.now: str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _stage4_result(self) -> str:
        """Compute Stage 4 result from oscilloscope args."""
        p99: Optional[float] = self.args.gpio_p99
        samples: Optional[int] = self.args.gpio_samples
        if p99 is None:
            return NA
        if p99 < 100.0 and (samples is None or samples >= 100):
            return PASS
        return FAIL

    def _count_stage_passes(self) -> tuple[int, int]:
        """Count how many stages have PASS results. Returns (passes, total)."""
        results: list[str] = [
            str(self.stage1.get("result", NA)),
            str(self.stage2.get("result", NA)),
            str(self.stage3.get("result", NA)),
            self._stage4_result(),
            str(self.soak.get("result", NA)),
        ]
        passes: int = sum(1 for r in results if r == PASS)
        valid: int = sum(1 for r in results if r in (PASS, FAIL))
        return passes, valid

    def render(self) -> str:
        """Render the full markdown report as a string."""
        lines: list[str] = []
        s1r: str = _pf(self.stage1.get("result", NA))
        s2r: str = _pf(self.stage2.get("result", NA))
        s3r: str = _pf(self.stage3.get("result", NA))
        s4r: str = self._stage4_result()
        s5r: str = _pf(self.soak.get("result", NA))
        passes, total = self._count_stage_passes()
        overall: str = PASS if passes == total == 5 else FAIL

        lines += [
            "# ECU-1170-552A Hardware Validation Report",
            "",
            f"**Date:** {self.now}",
            f"**ECU Serial:** {self.args.ecu_serial or '[SERIAL]'}",
            f"**Yocto Image:** {self.args.image_version or '[IMAGE_VERSION]'}",
            f"**Tester:** {self.args.tester or '[NAME]'}",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            f"{overall} — {passes}/{total} stages passed.",
            "",
            "| Stage | Name | Result |",
            "|-------|------|--------|",
            f"| 1 | Boot Validation | {s1r} |",
            f"| 2 | Driver Verification | {s2r} |",
            f"| 3 | Data Path Validation | {s3r} |",
            f"| 4 | Safety GPIO Timing | {s4r} |",
            f"| 5 | 24-Hour Soak Test | {s5r} |",
            "",
            "---",
            "",
        ]

        # ---- Stage 1 ----
        lines += [
            "## Stage 1: Boot Validation",
            "",
            "| Service | Status | Notes |",
            "|---------|--------|-------|",
        ]
        services: dict[str, str] = dict(
            self.stage1.get("services", {k: NA for k in SERVICES})  # type: ignore[arg-type]
        )
        for svc in SERVICES:
            status: str = _pf(services.get(svc, NA))
            lines.append(f"| {svc} | {status} | |")

        boot_time: float = float(self.stage1.get("boot_time_s", -1))
        boot_time_str: str = f"{boot_time:.1f}s" if boot_time > 0 else "[N/A]"
        lines += [
            "",
            f"**Boot time:** {boot_time_str} (target: <60s)",
            f"**Result:** {s1r}",
            "",
            "---",
            "",
        ]

        # ---- Stage 2 ----
        lines += [
            "## Stage 2: Driver Verification",
            "",
            "### CAN",
            "",
            "| Interface | Test | Result | Notes |",
            "|-----------|------|--------|-------|",
            f"| CAN0 | Interface exists | {_pf(self.stage2.get('can0_exists', NA))} | |",
            f"| CAN0 | Loopback send/recv | {NA} | Run stage2-drivers.sh for loopback result |",
            f"| CAN1 | Interface exists | {_pf(self.stage2.get('can1_exists', NA))} | |",
            f"| CAN1 | Loopback send/recv | {NA} | Run stage2-drivers.sh for loopback result |",
            "",
            "### RS485",
            "",
            "| Port | Device Path | UART Exists | Modbus Poll | Notes |",
            "|------|-------------|-------------|------------|-------|",
            "| RS485-1 (PCS) | /dev/ttyS1 | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |",
            "| RS485-2 (Meter) | /dev/ttyS2 | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |",
            "| RS485-3 (BTMS) | /dev/ttyS3 | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |",
            "| RS485-4 (DG/PV) | /dev/ttyS4 | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |",
            "",
            "### GPIO",
            "",
            "| Test | Result | Notes |",
            "|------|--------|-------|",
            f"| gpiochip detected | {_pf(self.stage2.get('gpio_detected', NA))} | Chip: {self.stage2.get('gpio_chip', '[unknown]')} |",
            "| DI-0..7 readable (8 lines) | [PASS/FAIL] | |",
            "| DO safe write test | [PASS/FAIL] | Tested: DO-3 RUNNING_LAMP, DO-7 SPARE_DO7 |",
            "",
            "### Network",
            "",
            "| Interface | Name | Link | Result |",
            "|-----------|------|------|--------|",
            "| ETH0 (WAN) | [ethN] | [UP/DOWN] | [PASS/FAIL] |",
            "| ETH1 (LAN) | [ethN] | [UP/DOWN] | [PASS/FAIL] |",
            "",
            "### HDMI",
            "",
            "| Test | Result | Notes |",
            "|------|--------|-------|",
            f"| DRM subsystem present | {_pf(self.stage2.get('drm_ok', NA))} | |",
            "| Display detected | [YES/NO] | |",
            "",
            f"**Result:** {s2r}",
            "",
            "---",
            "",
        ]

        # ---- Stage 3 ----
        checks: dict[str, str] = dict(
            self.stage3.get("checks", {})  # type: ignore[arg-type]
        )
        lines += [
            "## Stage 3: Data Path Validation",
            "",
            "| Pipeline Step | Result | Notes |",
            "|---------------|--------|-------|",
            f"| RTDB shm exists (/dev/shm/ems_rtdb) | {_pf(checks.get('rtdb_shm', NA))} | |",
            f"| ZMQ telemetry publishing (topic: telemetry) | {_pf(checks.get('zmq_pub', NA))} | |",
            f"| Parquet files created in /data/ | {_pf(checks.get('parquet_exists', NA))} | |",
            "| Parquet rows growing (1Hz writes) | [PASS/FAIL] | Run test_data_path.py |",
            f"| DuckDB query over 24h data | {_pf(checks.get('duckdb_ok', NA))} | |",
            "",
            f"**Result:** {s3r}",
            "",
            "---",
            "",
        ]

        # ---- Stage 4 ----
        p50: Optional[float] = self.args.gpio_p50
        p95: Optional[float] = self.args.gpio_p95
        p99: Optional[float] = self.args.gpio_p99
        samples: Optional[int] = self.args.gpio_samples
        p50_str: str = f"{p50} ms" if p50 is not None else "[N/A — enter manually]"
        p95_str: str = f"{p95} ms" if p95 is not None else "[N/A — enter manually]"
        p99_str: str = f"{p99} ms" if p99 is not None else "[N/A — enter manually]"
        samples_str: str = str(samples) if samples is not None else "[N/A]"
        p99_result: str = (
            (PASS if p99 < 100.0 else FAIL) if p99 is not None else NA
        )
        samples_result: str = (
            (PASS if samples >= 100 else FAIL) if samples is not None else NA
        )

        lines += [
            "## Stage 4: Safety GPIO Timing",
            "",
            "*Procedure: tools/hw-validation/stage4-gpio-timing.md*",
            "*Measured with oscilloscope: CH1 = DI-6 (ESTOP_NO), CH2 = DO-5 (PCS_STOP)*",
            "",
            "| Measurement | Value | Target | Result |",
            "|-------------|-------|--------|--------|",
            f"| DI-6 -> DO-5 p50 | {p50_str} | — | |",
            f"| DI-6 -> DO-5 p95 | {p95_str} | — | |",
            f"| DI-6 -> DO-5 p99 | {p99_str} | <100 ms | {p99_result} |",
            "| Standard deviation (σ) | [N] ms | — | |",
            f"| Samples collected | {samples_str} | >=100 | {samples_result} |",
            "| SCHED_FIFO active | [YES/NO] | YES | [PASS/FAIL] |",
            "| PREEMPT_RT kernel | [YES/NO] | YES | [PASS/FAIL] |",
            "",
            "**Dual-channel cross-check:**",
            "",
            "| Test | Expected | Actual | Result |",
            "|------|----------|--------|--------|",
            "| Both channels (DI-6 high + DI-7 low) | DO-5 asserts | | [PASS/FAIL] |",
            "| DI-6 only (DI-7 high) | DO-5 no-assert | | [PASS/FAIL] |",
            "| DI-7 only (DI-6 low) | DO-5 no-assert | | [PASS/FAIL] |",
            "",
            f"**Result:** {s4r}",
            "",
            "---",
            "",
        ]

        # ---- Stage 5 ----
        soak_duration_h: str = (
            f"{int(self.soak.get('duration_s', 0)) / 3600:.2f} h"
            if self.soak.get("duration_s")
            else "[N/A]"
        )
        soak_restarts: object = self.soak.get("total_restarts", NA)
        soak_rss: object = self.soak.get("max_rss_growth_pct", NA)
        soak_gaps: object = self.soak.get("data_gaps", NA)

        soak_restarts_result: str = (
            PASS if soak_restarts == 0 else (FAIL if isinstance(soak_restarts, int) else NA)
        )
        soak_rss_result: str = (
            PASS
            if isinstance(soak_rss, (int, float)) and float(soak_rss) < 10.0
            else (FAIL if isinstance(soak_rss, (int, float)) else NA)
        )
        soak_gaps_result: str = (
            PASS if soak_gaps == 0 else (FAIL if isinstance(soak_gaps, int) else NA)
        )

        lines += [
            "## Stage 5: 24-Hour Soak Test",
            "",
            "| Metric | Value | Target | Result |",
            "|--------|-------|--------|--------|",
            f"| Duration | {soak_duration_h} | 24 h | |",
            f"| Service restarts | {soak_restarts} | 0 | {soak_restarts_result} |",
            f"| Max RSS growth | {soak_rss}% | <10% | {soak_rss_result} |",
            f"| Data gaps (Parquet stale >10s) | {soak_gaps} | 0 | {soak_gaps_result} |",
            "| Disk usage at end | [N]% | <80% | |",
            "",
            "**Pre-soak checks:** CAN traffic [PASS/FAIL], Modbus response [PASS/FAIL]",
            f"**Result:** {s5r}",
            "",
            "---",
            "",
        ]

        # ---- Benchmarks ----
        def _bench(key: str, fallback: str = "[N/A]") -> str:
            """Extract benchmark value from results dict."""
            val: object = self.benchmarks.get(key)
            if val is None:
                return fallback
            return str(val)

        lines += [
            "## ARM64 Performance Benchmarks",
            "",
            "*Benchmarks run with full system load (all 14 services active, simulators running).*",
            "*Reference values measured on x86_64 development machine during M1-M4.*",
            "",
            "| Benchmark | Measured | Target | x86 Ref | Result |",
            "|-----------|----------|--------|---------|--------|",
            f"| Control loop jitter (1-sigma) | {_bench('control_loop_jitter_ms')} ms | <10 ms | <1 ms | [PASS/FAIL] |",
            f"| RTDB write latency (p99) | {_bench('rtdb_write_p99_ms')} ms | <1 ms | <0.1 ms | [PASS/FAIL] |",
            f"| Parquet throughput | {_bench('parquet_rows_per_s')} rows/s | >=1 row/s | ~10 rows/s | [PASS/FAIL] |",
            f"| DuckDB query (24h dataset) | {_bench('duckdb_query_s')} s | <5 s | <1 s | [PASS/FAIL] |",
            f"| WebSocket latency (p99) | {_bench('websocket_p99_ms')} ms | <100 ms | <10 ms | [PASS/FAIL] |",
            "",
            "---",
            "",
        ]

        # ---- Hardware Configuration ----
        lines += [
            "## Hardware Configuration",
            "",
            "| Item | Value |",
            "|------|-------|",
            f"| Kernel | {self.hw_config.get('kernel', '[unknown]')} |",
            f"| Device tree blob | {self.hw_config.get('dtb', '[unknown]')} |",
            f"| U-Boot defconfig | {self.hw_config.get('uboot_defconfig', '[unknown]')} |",
            f"| GPIO chip | {self.hw_config.get('gpio_chip', '[unknown]')} |",
            f"| CAN interfaces | {self.hw_config.get('can_interfaces', '[unknown]')} |",
            f"| RS485 UART paths | {self.hw_config.get('rs485_uarts', '[unknown]')} |",
            f"| Ethernet interfaces | {self.hw_config.get('eth_interfaces', '[unknown]')} |",
            "| fw_env.config offset | [verified offset — 0x3E0000 or hardware-confirmed] |",
            "| ECU SSD mount | /data (ext4) |",
            "| EMS venv path | /opt/ems/python/.venv |",
            "| Yocto image layer | meta-ems |",
            "",
            "---",
            "",
        ]

        # ---- Sign-Off ----
        lines += [
            "## Sign-Off",
            "",
            "| Role | Name | Date | Signature |",
            "|------|------|------|-----------|",
            "| Engineer | | | |",
            "| Reviewer | | | |",
            "",
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="EMS Hardware Validation Report Generator for ECU-1170-552A",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--ecu-ip",
        type=str,
        default=DEFAULT_ECU_IP,
        metavar="IP",
        help=f"ECU IP address for live data collection (default: {DEFAULT_ECU_IP})",
    )
    parser.add_argument(
        "--gpio-p50",
        type=float,
        default=None,
        metavar="MS",
        help="Stage 4: DI-6 to DO-5 propagation delay p50 in milliseconds (oscilloscope)",
    )
    parser.add_argument(
        "--gpio-p95",
        type=float,
        default=None,
        metavar="MS",
        help="Stage 4: DI-6 to DO-5 propagation delay p95 in milliseconds (oscilloscope)",
    )
    parser.add_argument(
        "--gpio-p99",
        type=float,
        default=None,
        metavar="MS",
        help="Stage 4: DI-6 to DO-5 propagation delay p99 in milliseconds (oscilloscope)",
    )
    parser.add_argument(
        "--gpio-samples",
        type=int,
        default=None,
        metavar="N",
        help="Stage 4: Number of oscilloscope samples collected (target: >=100)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Output path for rendered report (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cached data files only — no SSH to ECU",
    )
    parser.add_argument(
        "--ecu-serial",
        type=str,
        default=None,
        metavar="SERIAL",
        help="ECU serial number for report header",
    )
    parser.add_argument(
        "--image-version",
        type=str,
        default=None,
        metavar="VER",
        help="Yocto image version for report header",
    )
    parser.add_argument(
        "--tester",
        type=str,
        default=None,
        metavar="NAME",
        help="Tester name for report sign-off",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point."""
    args: argparse.Namespace = _parse_args()
    ecu_ip: str = args.ecu_ip
    ecu_user: str = DEFAULT_ECU_USER
    offline: bool = args.offline

    print("[hw_validation_report] Collecting data...", flush=True)

    if offline:
        print("  Mode: offline (using cached files, no SSH)", flush=True)
        stage1: dict[str, object] = {"result": NA, "services": {}, "boot_time_s": -1.0}
        stage2: dict[str, object] = {"result": NA}
        stage3: dict[str, object] = {"result": NA, "checks": {}}
        hw_config: dict[str, str] = {}
    else:
        print(f"  Collecting Stage 1 boot data from {ecu_ip}...", flush=True)
        stage1 = _collect_stage1_live(ecu_ip, ecu_user)

        print(f"  Collecting Stage 2 driver data from {ecu_ip}...", flush=True)
        stage2 = _collect_stage2_live(ecu_ip, ecu_user)

        print(f"  Collecting Stage 3 data path data from {ecu_ip}...", flush=True)
        stage3 = _collect_stage3_live(ecu_ip, ecu_user)

        print(f"  Collecting hardware config from {ecu_ip}...", flush=True)
        hw_config = _collect_hw_config_live(ecu_ip, ecu_user)

    print("  Reading Stage 5 soak results...", flush=True)
    soak: dict[str, object] = _collect_soak_results(ecu_ip, ecu_user, offline)

    print("  Reading benchmark results...", flush=True)
    benchmarks: dict[str, object] = _collect_benchmarks(ecu_ip, ecu_user, offline)

    # Render report
    renderer: ReportRenderer = ReportRenderer(
        args=args,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        soak=soak,
        benchmarks=benchmarks,
        hw_config=hw_config if not offline else {},
    )
    report: str = renderer.render()

    # Write output
    output_path: Path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\n[hw_validation_report] Report written to: {output_path}", flush=True)

    # Determine exit code
    passes, total = renderer._count_stage_passes()
    if passes == total and total > 0:
        print(f"[hw_validation_report] Result: PASS ({passes}/{total} stages)", flush=True)
        return 0
    else:
        print(
            f"[hw_validation_report] Result: FAIL or incomplete ({passes}/{total} stages passed)",
            flush=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
