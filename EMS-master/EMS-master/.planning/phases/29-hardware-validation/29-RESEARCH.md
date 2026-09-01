# Phase 29: Hardware Validation - Research

**Researched:** 2026-03-16
**Domain:** Embedded hardware bring-up — ECU-1170-552A (TI AM6548 ARM64), Yocto Linux boot, driver verification, safety GPIO timing, 24-hour soak test
**Confidence:** HIGH (project codebase fully readable; external claims verified against existing code and tooling)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**5-Stage Validation Sequence**

| Stage | Test | Duration | Pass Criteria |
|-------|------|----------|---------------|
| 1. Boot | Yocto image boots, all 14 services start | 5 min | `systemctl is-active` for all services within 60s |
| 2. Drivers | CAN, RS485, GPIO, HDMI individually tested | 30 min | Each driver sends/receives correctly |
| 3. Data Path | CAN sim → RTDB → ZMQ → Parquet pipeline on ARM64 | 30 min | Data matches x86 reference output |
| 4. Safety Timing | GPIO response time with oscilloscope | 30 min | DI edge → DO assert <100ms (p99) |
| 5. Soak Test | Full system under simulated load | 24 hours | No crashes, no memory growth, no data loss |

Key rules:
- Stage 1-3 can run without real BMS/PCS hardware — simulators provide stimulus
- Stage 4 requires real GPIO pins + oscilloscope — measures actual hardware latency, not simulated
- Stage 5 uses CAN/Modbus simulators running on a separate machine connected via physical CAN/RS485 cables
- All tests scripted where possible — manual steps documented in a test procedure
- Results logged to a hardware validation report (not just pass/fail — actual measurements)

**Per-Driver Test Plan**

| Driver | Test Method | Stimulus | Verification |
|--------|-----------|----------|-------------|
| CAN0 | `cansend`/`candump` | Send known frame from laptop | ECU receives and decodes correctly |
| CAN1 | Same as CAN0 | Separate laptop on CAN1 bus | Independent bus operation |
| RS485-1 (PCS) | `minicom` + Modbus sim | PCS simulator on USB-RS485 adapter | ECU polls and decodes registers |
| RS485-2 (Meter) | Same pattern | Meter simulator | ECU reads meter telemetry |
| RS485-3 (BTMS) | Same pattern | BTMS simulator | ECU reads thermal data |
| RS485-4 (DG/PV) | Same pattern | DG simulator | ECU reads DG status |
| GPIO DI | External signal generator | Toggle DI pins with jumper wires | RTDB DI values change |
| GPIO DO | Multimeter on output pins | safety_manager asserts outputs | Voltage high on commanded pins |
| HDMI | Connect 10" touch panel | HMI frontend displayed | React screens render, touch works |

**6 Performance Benchmarks on ARM64**

| Benchmark | Method | Target | x86 Reference |
|-----------|--------|--------|--------------|
| Safety GPIO response | Oscilloscope: DI edge → DO assert | <100ms (p99) | <10ms (measured in M1) |
| 1Hz control loop jitter | Tick-to-tick interval over 1 hour | ±10ms (1 sigma) | <1ms on x86 |
| RTDB seqlock write latency | Timestamp before/after write, 10K samples | <1ms (p99) | <0.1ms on x86 |
| Parquet write throughput | Rows/second for container topology (64 racks) | ≥1 row/sec sustained | ~10 rows/sec on x86 |
| DuckDB query latency | time_series query over 24h Parquet data | <5s | <1s on x86 |
| WebSocket latency | ZMQ PUB → WebSocket client receive | <100ms | <10ms on x86 |

### Claude's Discretion

- Test script implementation (bash scripts vs pytest on the ECU)
- Oscilloscope measurement methodology (trigger setup, sample count)
- Soak test monitoring (how to detect memory growth, data loss)
- Hardware validation report format (markdown, PDF, or structured data)
- Whether to run QEMU ARM64 validation before physical hardware
- Network connectivity test (ETH0 WAN, ETH1 LAN)

### Deferred Ideas (OUT OF SCOPE)

- 72-hour extended soak test (IEC 61131-2 recommendation)
- EMC/EMI testing
- Temperature chamber testing
- Power cycle endurance
- Production manufacturing test fixture
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROD-07 | ECU-1170-552A hardware validation — boot test, driver verification (CAN, RS485, GPIO, HDMI), performance benchmarks on ARM64 | 5-stage progressive test plan; existing simulators used as stimulus; oscilloscope for GPIO timing; MetricsCollector pattern reused from integration tests; hardware validation report documents actual measurements |
</phase_requirements>

---

## Summary

Phase 29 is the first phase in this project requiring physical ECU hardware. It validates the Yocto image built in Phase 27 and the hardened services from Phase 28 on the actual Advantech ECU-1170-552A target. The core challenge is not writing new application logic — all 14 services already work on x86 — but verifying that the ARM64 binary artifacts, Yocto BSP configuration, and hardware drivers all function correctly together.

The biggest technical uncertainty is the am65xx-ems machine configuration in `yocto/meta-ems/conf/machine/am65xx-ems.conf`. That file has two explicit TODOs deferred to Phase 29: (1) the correct U-Boot defconfig for the ECU-1170 board, and (2) the correct Device Tree Blob (DTB) — it currently uses the TI AM65xx EVM base board DTB (`k3-am654-base-board.dtb`) as a placeholder. Resolving these requires either the Advantech BSP package or the Advantech-provided kernel/U-Boot source. This is the single highest-risk item.

The remaining work is procedural: scripted validation following the 5-stage plan from CONTEXT.md, measurement collection using patterns already proven in `tests/integration/test_performance.py`, and producing a hardware validation report. The QEMU ARM64 pre-flight is recommended to catch binary incompatibilities before shipping to hardware.

**Primary recommendation:** Resolve the am65xx DTB/U-Boot defconfig gap with Advantech BSP documentation first (Wave 1), then run QEMU ARM64 pre-flight (Wave 2), then execute the 5-stage hardware validation on the physical ECU (Waves 3-4), producing a structured validation report throughout.

---

## Standard Stack

### Core (all already in repo)

| Tool/Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `can-utils` (cansend/candump) | system package | CAN frame injection/capture from laptop | Used in M0 simulator tooling; `verify-dev-env.sh` checks for it |
| `python-can` | >=4.0 (in pyproject.toml dev deps) | Python CAN bus interface for laptop-side simulator | M0 CAN simulator already uses it |
| `pymodbus` | >=3.7 (in pyproject.toml dev deps) | Modbus simulator on laptop via USB-RS485 | M0 Modbus simulator uses it |
| `libgpiod` v2 | system package (HAVE_LIBGPIOD compile flag) | Real GPIO driver on ECU | `gpio.c` already implements libgpiod v2 backend with vtable |
| `psutil` | >=7.2.2 (in pyproject.toml dev deps) | RSS memory growth monitoring | MetricsCollector in `tests/integration/conftest.py` already uses it |
| `pyarrow` | >=23.0.1 | Parquet write throughput measurement | Already in dev deps; `count_parquet_rows()` pattern exists |
| `duckdb` | >=1.5.0 | Query latency benchmarking | Already in dev deps |
| `pytest` + `pytest-timeout` | >=8.0 / >=2.4.0 | Test framework for scripted validation | Project standard; all integration tests use it |
| QEMU (`qemu-system-aarch64`) | latest stable | ARM64 pre-flight emulation | Catches binary ABI and Python venv issues before physical hardware |

### Test Equipment (external, not code)

| Equipment | Purpose | Notes |
|-----------|---------|-------|
| USB-CAN adapter (e.g., Peak PCAN-USB) | Laptop → CAN0/CAN1 bus | Same tooling as production commissioning |
| USB-RS485 adapter × 4 | Laptop → RS485-1 through RS485-4 | Same as field deployment |
| Digital oscilloscope (2-channel min) | GPIO DI → DO timing measurement | Required for hardware-truth GPIO timing — software timestamps miss kernel scheduling latency |
| 10" HDMI touch panel | HMI validation | Must match ECU-1170 HDMI output |
| Jumper wires | GPIO DI manual stimulus | Pin mapping from `gpio_config.yaml` |
| Multimeter | GPIO DO voltage verification | Confirm output levels |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Oscilloscope for GPIO timing | Software timestamps | Software-only timing misses kernel scheduling latency on ARM — oscilloscope is the only ground truth for <100ms safety claim |
| pytest scripts on laptop | Bash scripts on ECU | pytest provides structured pass/fail reporting and integrates with CI; bash is simpler for on-device commands without Python available |
| Markdown validation report | PDF / JSON | Markdown is version-controlled and human-readable; JSON adds machine parseability but adds tooling; Markdown is the existing doc format for this project |

---

## Architecture Patterns

### Recommended Project Structure

```
tools/
├── hw-validation/
│   ├── stage1-boot.sh           # systemctl is-active checks for all 14 services
│   ├── stage2-drivers.sh        # Per-driver test invocations
│   ├── stage3-datapath.py       # Pipeline validation: CAN → RTDB → ZMQ → Parquet
│   ├── stage4-gpio-timing.md    # Oscilloscope procedure (manual steps)
│   ├── stage5-soak.sh           # Launch soak test, monitor in background
│   ├── benchmark.py             # ARM64 performance benchmark harness
│   ├── soak_monitor.py          # Continuous RSS/crash/data-loss monitoring
│   └── hw_validation_report.py  # Collect results, render markdown report
tests/
└── hw/
    ├── test_boot.py             # pytest wrapper for stage 1 (ssh to ECU)
    ├── test_drivers.py          # pytest wrapper for stage 2
    ├── test_datapath.py         # pytest wrapper for stage 3
    └── test_benchmarks.py       # pytest wrapper for benchmarks
docs/
└── hardware-validation-report.md  # Final output artifact
```

### Pattern 1: QEMU ARM64 Pre-flight

**What:** Boot the Yocto image in QEMU before touching physical hardware, confirming all 14 services start and the Python venv resolves correctly.

**When to use:** Before shipping the Yocto SD/eMMC image to the physical ECU. Catches binary ABI issues, missing shared libs, and venv path mismatches.

**Key commands:**
```bash
# Install QEMU ARM64 emulation support
sudo apt-get install qemu-system-aarch64

# Boot Yocto ext4 rootfs under QEMU (no DTB required for emulation)
qemu-system-aarch64 \
  -machine virt \
  -cpu cortex-a53 \
  -m 2048 \
  -kernel <zImage-from-Yocto-build> \
  -append "root=/dev/vda rw console=ttyAMA0" \
  -drive if=virtio,file=<core-image-ems.ext4>,format=raw \
  -nographic

# Inside QEMU: check all services
systemctl is-active ems.target
systemctl list-units 'ems-*' --state=active
```

**Limitations:** QEMU virt machine does not emulate CAN, RS485, or GPIO hardware — only validates boot sequence and Python venv, not drivers.

### Pattern 2: Boot Validation Script

**What:** SSH to ECU, run `systemctl is-active` for all 14 services, assert all active within 60 seconds.

**When to use:** Stage 1 of the hardware validation procedure. Scripted, automated, < 5 min.

```bash
#!/usr/bin/env bash
# tools/hw-validation/stage1-boot.sh
# Validates all 14 EMS services start within 60 seconds on the ECU.
# Usage: bash stage1-boot.sh [ECU_IP]

ECU_IP="${1:-192.168.1.100}"
TIMEOUT=60
PASS=0
FAIL=0

SERVICES=(
    data_manager
    ems-data-manager-python
    config_manager
    safety_manager
    comm_manager_c
    comm_manager
    logger
    control_manager
    alarm_manager
    scheduler
    diagnostics
    cloud_manager
    ota_manager
    hmi_server
)

deadline=$(($(date +%s) + TIMEOUT))
for svc in "${SERVICES[@]}"; do
    while true; do
        state=$(ssh root@"$ECU_IP" "systemctl is-active $svc 2>/dev/null")
        if [[ "$state" == "active" ]]; then
            echo "[PASS] $svc: active"
            PASS=$((PASS + 1))
            break
        fi
        if [[ $(date +%s) -ge $deadline ]]; then
            echo "[FAIL] $svc: not active within ${TIMEOUT}s (state: $state)"
            FAIL=$((FAIL + 1))
            break
        fi
        sleep 1
    done
done

echo ""
echo "Boot validation: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
```

### Pattern 3: Performance Benchmark Harness (reuse from test_performance.py)

**What:** Adapt the `MetricsCollector` and metric-collection functions from `tests/integration/test_performance.py` for ARM64 hardware context. The existing code already measures GPIO latency, RTDB write latency, ZMQ lag, logger throughput, and RSS growth — the same 5 metrics needed on hardware.

**When to use:** Stage 3 (data path) and ARM64 performance benchmarks. Run from a laptop with SSH access to the ECU, or directly on the ECU if Python is available via the Yocto venv.

**Key insight:** The RTDB GPIO vtable in `gpio.c` is already written for dual-mode operation. On ECU with `HAVE_LIBGPIOD` compiled, `gpio_ops_libgpiod` is used. The RTDB backend (`gpio_ops_rtdb`) is for CI only. The `EMS_GPIO_BACKEND=rtdb` env var in `test_performance.py` forces RTDB mode; on real hardware, the libgpiod backend is selected by default.

### Pattern 4: Soak Test Monitoring

**What:** During the 24-hour Stage 5 soak test, continuously poll for: process crashes (systemd service restarts), RSS growth (>10% over baseline), and data loss (Parquet file gaps at 1Hz).

**When to use:** Stage 5. Run as a background process on the ECU or via SSH from a monitoring laptop.

```python
# Key monitoring loop pattern (adapted from MetricsCollector)
import subprocess
import time
import psutil

SERVICES = ["safety_manager", "data_manager", "control_manager", ...]
RSS_BASELINE: dict[str, int] = {}
RESTART_COUNTS: dict[str, int] = {}
LOG_PATH = "/data/soak_monitor.jsonl"

def check_service_restarts() -> dict[str, int]:
    """Return restart counts via systemctl show."""
    counts = {}
    for svc in SERVICES:
        result = subprocess.run(
            ["systemctl", "show", svc, "--property=NRestarts"],
            capture_output=True, text=True
        )
        counts[svc] = int(result.stdout.split("=")[1].strip())
    return counts

def check_parquet_freshness(data_dir: str, max_gap_s: float = 5.0) -> bool:
    """Return True if most recent Parquet file was modified within max_gap_s."""
    import glob, os
    files = sorted(glob.glob(f"{data_dir}/**/*.parquet", recursive=True))
    if not files:
        return False
    newest_mtime = max(os.path.getmtime(f) for f in files)
    return (time.time() - newest_mtime) < max_gap_s
```

### Pattern 5: Oscilloscope GPIO Timing Procedure

**What:** Manual measurement procedure for Stage 4. Connect oscilloscope CH1 to DI-6 (ESTOP_NO, pin N on ECU-1170 GPIO header), CH2 to DO-5 (PCS_STOP). Trigger on CH1 rising edge, measure CH2 propagation delay.

**Measurement setup:**
- Sample rate: ≥1 MSa/s (to resolve sub-ms transitions)
- Trigger: CH1 rising edge, level = 1.65V (50% of 3.3V GPIO)
- Measure: CH1 rise → CH2 rise, statistics mode, p99 over ≥100 events
- Expected: <100ms p99 (AM6548 A53 with PREEMPT_RT kernel running SCHED_FIFO)

**Why oscilloscope is mandatory:** The safety_manager runs at `SCHED_FIFO` priority with a target <100ms response. Software timestamps via `time.monotonic()` on ARM do not capture: (a) kernel tick-to-tick scheduling jitter (can be 1-5ms with PREEMPT_RT), (b) interrupt latency for the GPIO edge, (c) context switch overhead. The x86 development measurements (<10ms) used the RTDB backend which is pure POSIX shm — not real GPIO. Oscilloscope is the ground truth for the safety requirement.

### Anti-Patterns to Avoid

- **Using software GPIO timestamps for safety claim:** The RTDB backend latency (<10ms on x86) does not represent real hardware GPIO latency. Only oscilloscope measurements on actual ECU GPIO pins satisfy the PROD-07 safety timing requirement.
- **Skipping QEMU pre-flight:** The Yocto image bundles a Python venv at a fixed path (`/opt/ems/venv`). If the venv was staged incorrectly (wrong wheel paths, wrong Python version), all Python services fail silently — catch this in QEMU before touching the ECU.
- **Running soak test without monitoring:** A crash 20 hours in is undetectable without continuous polling. The soak test must have an active monitor writing results to `/data/soak_monitor.jsonl` to prove the 24-hour window was clean.
- **Single-channel GPIO timing:** E-Stop is dual-channel (DI-6 NO, DI-7 NC) — measure both channels on the oscilloscope to confirm cross-monitoring logic fires correctly.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CAN frame injection on laptop | Custom CAN sender | `cansend` / `python-can` + USB-CAN adapter | `can-utils` is the standard embedded CAN debug tool; `python-can` is already in pyproject.toml dev deps |
| Modbus simulator on laptop | New Modbus test fixture | Existing `tools/simulators/modbus_sim.py` via USB-RS485 | M0 simulator already handles all 4 RS485 buses |
| GPIO DI stimulus | Custom GPIO signal generator | Jumper wires + 3.3V supply or USB signal generator | DI pins need simple logic-level switching; jumper wires are sufficient for v1.0 functional test |
| ARM64 performance measurement | Custom timing framework | Reuse `tests/integration/test_performance.py` `MetricsCollector` | The exact metrics needed (GPIO latency, RTDB latency, ZMQ lag, RSS growth, Parquet rows) are already implemented |
| Service health monitoring | Custom daemon | `systemctl is-active` + `systemctl show --property=NRestarts` | systemd provides authoritative service state including restart counts |
| Memory leak detection | Valgrind / custom tracker | `psutil.Process.memory_info().rss` via the existing MetricsCollector pattern | Valgrind is unusable on ARM embedded with full system load; psutil RSS growth tracking is already proven in test_performance.py |

**Key insight:** Phase 29 is almost entirely test procedure and measurement, not new code. The application logic (all 14 services) already exists. The primary deliverable is a validated hardware report, not a software artifact.

---

## Common Pitfalls

### Pitfall 1: Wrong DTB / U-Boot Defconfig

**What goes wrong:** ECU-1170-552A boots with the TI AM65xx EVM base board DTB (`k3-am654-base-board.dtb`). The ECU board has different peripheral mappings — CAN, RS485 UART, GPIO chip paths may all be wrong. Result: CAN and RS485 interfaces don't enumerate, GPIO chip offset is wrong, and safety_manager `gpiod_chip_open()` fails.

**Why it happens:** `am65xx-ems.conf` explicitly defers DTB and U-Boot defconfig validation to Phase 29 (noted in file with TODO comments). The correct DTB requires the Advantech BSP package or ECU-1170-552A hardware documentation.

**How to avoid:** Before running any driver tests, verify `ls /dev/gpiochip*`, `ip link show can0`, and `ls /dev/ttyS*` match the expected mappings. Update `am65xx-ems.conf` and rebuild the Yocto image if mappings are wrong.

**Warning signs:** `gpiod_chip_open(/dev/gpiochip0) failed` in safety_manager log; CAN0/CAN1 interfaces absent from `ip link`; RS485 UARTs at wrong `/dev/ttyS*` paths.

### Pitfall 2: Python Venv Path Mismatch

**What goes wrong:** All Python services fail to start with `No such file or directory: /opt/ems/venv/bin/python`. The Yocto image installs the venv at a specific path set in the recipe; the systemd unit files must reference the same path.

**Why it happens:** Phase 27 (`ems-python-venv_0.1.0.bb`) stages the venv at `/opt/ems/venv`. If the unit files in `deploy/systemd/` use a different path (e.g., `/usr/local/ems/venv`), all Python services fail.

**How to avoid:** QEMU pre-flight catches this before physical hardware. In QEMU, `systemctl status config_manager` will show the exact ExecStart path error immediately.

**Warning signs:** All Python services `failed` state on first boot; `systemctl status <service>` shows `(code=exited, status=127)`.

### Pitfall 3: SCHED_FIFO Capability on Yocto

**What goes wrong:** `safety_manager` starts but cannot set `SCHED_FIFO` scheduling, silently falling back to `SCHED_OTHER`. The <100ms GPIO response is not guaranteed without real-time scheduling.

**Why it happens:** Phase 28 (PROD-06) preserves `NoNewPrivileges=no` and uses `AmbientCapabilities=CAP_SYS_NICE` for the safety_manager. On Yocto with a PREEMPT_RT kernel, the ambient capability must be granted. If the PREEMPT_RT kernel patch isn't applied to the Yocto build, `SCHED_FIFO` may not be available at the required priority.

**How to avoid:** Check `uname -r` on the ECU — it should contain `rt` (e.g., `6.6.x-rt`). Check safety_manager logs for `SCHED_FIFO set: priority 80` vs `SCHED_FIFO failed`. Run oscilloscope test only after confirming RT scheduling is active.

**Warning signs:** `sched_setscheduler: Operation not permitted` in safety_manager log; oscilloscope measurements show >100ms p99 latency; `uname -r` does not contain `rt`.

### Pitfall 4: fw_env.config Offset Mismatch (A/B Partition)

**What goes wrong:** `ota_manager` uses `fw_env.config` with offset `0x3E0000` to read/write U-Boot environment variables. If the physical ECU uses a different eMMC layout than assumed, the ota_manager writes to wrong flash offset and corrupts U-Boot env.

**Why it happens:** Phase 27 notes: "fw_env.config offset 0x3E0000 must be verified against physical hardware." The TI AM65xx EVM reference offset may not match the Advantech ECU-1170-552A eMMC partition table.

**How to avoid:** Before running soak test (Stage 5), verify U-Boot env offset by running `fw_printenv` on the ECU — if it outputs garbage or errors, the offset is wrong. Only proceed with OTA manager active after verifying this.

**Warning signs:** `fw_printenv` shows garbled output or `Warning: Bad CRC`; ota_manager log shows env write errors.

### Pitfall 5: eMMC vs NVMe DuckDB Performance Gap

**What goes wrong:** DuckDB query over 24-hour Parquet data takes >30s on ECU (eMMC storage) vs <1s on x86 (NVMe SSD). This exceeds the 5s soft target significantly.

**Why it happens:** ECU-1170-552A uses eMMC for storage (typically 50-200 MB/s sequential read vs 3000+ MB/s NVMe). DuckDB `time_series` queries scan large Parquet files. The 10x ARM64 vs x86 budget was estimated assuming similar storage I/O — eMMC vs NVMe is a separate factor.

**How to avoid:** Benchmark DuckDB queries during Stage 3 data path validation (before 24-hour soak), not after. If query latency exceeds 5s, pre-aggregate nightly summaries as tech debt (per CONTEXT.md decision).

**Warning signs:** DuckDB query in benchmark takes >5s; `iostat` shows eMMC at 100% utilization during query; Parquet files accumulate without being queried.

### Pitfall 6: CAN Socket-CAN Driver Not Loaded

**What goes wrong:** `ip link show can0` shows no CAN interfaces even though the ECU has CAN hardware. The SocketCAN driver for the AM65xx DCAN or MCAN controller must be loaded.

**Why it happens:** The AM65xx SoC has M_CAN (Bosch MCAN) controllers. The Yocto kernel config must include `CONFIG_CAN_M_CAN` and `CONFIG_CAN_M_CAN_PLATFORM`. Without the correct kernel config and DTB entries for the MCAN nodes, `ip link` shows no `can*` interfaces.

**How to avoid:** After boot, run `dmesg | grep -i can` — look for `m_can` driver binding messages. If absent, the DTB MCAN node is missing or disabled.

**Warning signs:** `ip link show` has no `can0`/`can1`; `dmesg` shows no CAN driver messages; `modprobe m_can` fails.

---

## Code Examples

Verified patterns from project source:

### Boot Validation (14 services — mapped to deploy/systemd/)

```bash
# Source: /home/overlord/EMS/deploy/systemd/ (15 service files + ems.target)
# 14 runtime services (excluding legacy ems-data-manager.service alias):
SERVICES=(
    data_manager
    ems-data-manager-python
    config_manager
    safety_manager
    comm_manager_c
    comm_manager
    logger
    control_manager
    alarm_manager
    scheduler
    diagnostics
    cloud_manager
    ota_manager
    hmi_server
)
# Check all active:
for svc in "${SERVICES[@]}"; do
    systemctl is-active --quiet "$svc" && echo "OK: $svc" || echo "FAIL: $svc"
done
```

### GPIO Chip Path Verification (libgpiod v2)

```bash
# Source: /home/overlord/EMS/src/safety_manager/src/gpio.c — libgpiod_init()
# Verify the chip path before trusting gpio_config.yaml:
gpiodetect                          # List all gpiochip devices
gpioinfo /dev/gpiochip0             # Show all lines on chip 0
# gpio_config.yaml uses DI-0..7 and DO-0..7 — offsets must match chip line numbers
```

### RSS Growth Monitoring (from MetricsCollector pattern)

```python
# Source: /home/overlord/EMS/tests/integration/test_performance.py
# Pattern already proven for x86; adapts directly to ARM64 hardware:
import psutil, time

def monitor_rss_growth(service_pids: dict[str, int], duration_s: float = 86400.0) -> dict:
    """Monitor RSS for each service PID over soak test duration."""
    baseline: dict[str, float] = {}
    samples: dict[str, list[int]] = {name: [] for name in service_pids}
    deadline: float = time.monotonic() + duration_s

    while time.monotonic() < deadline:
        for name, pid in service_pids.items():
            try:
                proc = psutil.Process(pid)
                rss: int = proc.memory_info().rss
                samples[name].append(rss)
                if len(samples[name]) == 60:
                    baseline[name] = sum(samples[name]) / 60.0
            except psutil.NoSuchProcess:
                pass
        time.sleep(1.0)

    return {
        name: {
            "baseline_mb": baseline.get(name, 0) / 1024 / 1024,
            "final_mb": (sum(s[-60:]) / 60 / 1024 / 1024) if len(s) >= 60 else 0,
            "growth_pct": ((sum(s[-60:]) / 60 - baseline.get(name, 1)) /
                           baseline.get(name, 1) * 100) if name in baseline else 0,
        }
        for name, s in samples.items()
    }
```

### Parquet Write Rate Measurement

```python
# Source: /home/overlord/EMS/tests/integration/test_performance.py — count_parquet_rows()
# On ECU, Parquet files written to /data/telemetry/ (logger_config.yaml data_dir)
import pyarrow.parquet as pq
from pathlib import Path

def measure_parquet_throughput(data_dir: Path, duration_s: float = 300.0) -> float:
    """Returns rows/second sustained over duration_s."""
    import time
    rows_start: int = sum(
        pq.read_table(str(f)).num_rows
        for f in data_dir.rglob("telemetry_*.parquet")
    )
    t_start: float = time.monotonic()
    time.sleep(duration_s)
    rows_end: int = sum(
        pq.read_table(str(f)).num_rows
        for f in data_dir.rglob("telemetry_*.parquet")
    )
    elapsed: float = time.monotonic() - t_start
    return (rows_end - rows_start) / elapsed  # rows/sec; target >= 1.0
```

### DuckDB Query Latency Benchmark

```python
# Source: Pattern derived from existing DuckDB usage in project
import duckdb, time

def benchmark_duckdb_query(data_dir: str) -> float:
    """Returns query latency in seconds. Target <5s on ARM64 eMMC."""
    conn = duckdb.connect()
    conn.execute(f"CREATE VIEW telemetry AS SELECT * FROM read_parquet('{data_dir}/**/*.parquet')")
    t_start: float = time.monotonic()
    conn.execute(
        "SELECT timestamp, AVG(soc_pct) FROM telemetry "
        "WHERE timestamp > NOW() - INTERVAL '24 hours' "
        "GROUP BY timestamp ORDER BY timestamp"
    ).fetchall()
    return time.monotonic() - t_start
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Test GPIO via software RTDB timestamps | Oscilloscope DI → DO measurement | Phase 29 (first hardware phase) | Ground truth for <100ms safety claim; software measurements from M1 not valid for hardware sign-off |
| vcan0 virtual CAN for all development | Physical USB-CAN adapter to real CAN bus | Phase 29 | Tests actual CAN transceiver and cable topology |
| Modbus TCP simulator on localhost | Modbus RTU over USB-RS485 adapter | Phase 29 | Tests actual UART hardware, RS485 driver, and cable bus |
| x86 performance baselines | ARM64 measurements with 3-5x slowdown | Phase 29 | Cortex-A53 single-threaded ~3-5x slower than modern x86; eMMC storage additional constraint |

**Deprecated/outdated for this phase:**
- `EMS_GPIO_BACKEND=rtdb` env var: Valid in CI; on ECU hardware, do NOT set this — the libgpiod backend must be used
- `vcan0` interface: Only used in QEMU pre-flight simulation; physical ECU uses real CAN interfaces

---

## Open Questions

1. **Advantech ECU-1170-552A BSP / DTB availability**
   - What we know: `am65xx-ems.conf` has two explicit TODOs deferred to Phase 29 — the U-Boot defconfig and DTB. The file uses `am65x_evm_a53_defconfig` and `k3-am654-base-board.dtb` as placeholders.
   - What's unclear: Whether Advantech provides a Yocto BSP or just kernel patches. The correct DTB must have the ECU-1170 peripheral mapping (CAN, UART, GPIO chip paths).
   - Recommendation: Wave 1 must obtain either the Advantech BSP package or the ECU-1170 hardware reference manual with device tree documentation. Contact Advantech support with part number ECU-1170-552A. Without this, Stage 2 (driver verification) cannot succeed.

2. **PREEMPT_RT kernel availability for Yocto Scarthgap on AM65xx**
   - What we know: safety_manager requires `SCHED_FIFO` for <100ms GPIO response. The Yocto build currently extends `am65xx-evm.conf` from meta-ti-bsp which provides a standard TI kernel.
   - What's unclear: Whether meta-ti-bsp Scarthgap includes a PREEMPT_RT kernel variant for AM65xx. If not, the Yocto image must add a PREEMPT_RT kernel recipe.
   - Recommendation: After boot, check `uname -r` for `rt` suffix. If absent, add `PREFERRED_PROVIDER_virtual/kernel = "linux-ti-rt"` to `am65xx-ems.conf` if available, or apply RT patches manually.

3. **GPIO chip number and line offsets on ECU-1170-552A**
   - What we know: `gpio_config.yaml` defines DI-0..7 and DO-0..7 with specific names (ESTOP_NO, ACDB_TRIP, etc.). The `libgpiod_init()` call in `gpio.c` opens `/dev/gpiochip0` with configured offsets.
   - What's unclear: The exact gpiochip number and line offsets for the ECU-1170-552A GPIO header. The TI AM65xx has multiple GPIO banks (GPIO0, GPIO1, WKUP_GPIO0).
   - Recommendation: Run `gpiodetect && gpioinfo` on first boot to map physical header pins to gpiochip/offset, then update `gpio_config.yaml` if offsets differ from the current configuration.

4. **ETH0/ETH1 network interface naming on Yocto**
   - What we know: ECU-1170-552A has 2 Ethernet ports (ETH0 WAN, ETH1 LAN). CONTEXT.md identifies network connectivity test as Claude's discretion.
   - What's unclear: Whether systemd-networkd predictable interface naming assigns `eth0`/`eth1` or Yocto-specific names (e.g., `end0`, `enp1s0`).
   - Recommendation: Include a simple network test (ping default gateway, ping ETH1 static address) in Stage 2 driver verification. Check `ip link` on first boot to confirm interface names.

---

## Validation Architecture

> nyquist_validation is enabled (key present and true in .planning/config.json).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-timeout 2.4.0 |
| Config file | `/home/overlord/EMS/pyproject.toml` — `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/hw/ -x --timeout=120` |
| Full suite command | `uv run pytest tests/hw/ -v --timeout=3600` |

Note: `tests/hw/` is a new directory (Wave 0 gap). It holds pytest wrappers that SSH to the ECU and assert pass/fail on hardware validation stages.

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROD-07 | Yocto image boots, all 14 services active within 60s | integration/smoke | `uv run pytest tests/hw/test_boot.py -x` | ❌ Wave 0 |
| PROD-07 | CAN0/CAN1 drivers send/receive correctly | integration | `uv run pytest tests/hw/test_drivers.py::test_can -x` | ❌ Wave 0 |
| PROD-07 | RS485 × 4 drivers poll Modbus correctly | integration | `uv run pytest tests/hw/test_drivers.py::test_rs485 -x` | ❌ Wave 0 |
| PROD-07 | GPIO DI reads change state on ECU | integration | `uv run pytest tests/hw/test_drivers.py::test_gpio_di -x` | ❌ Wave 0 |
| PROD-07 | GPIO DO asserts voltage on command | manual-only | Oscilloscope + multimeter; no automated equivalent | N/A |
| PROD-07 | CAN → RTDB → ZMQ → Parquet data path correct on ARM64 | integration | `uv run pytest tests/hw/test_datapath.py -x` | ❌ Wave 0 |
| PROD-07 | Safety GPIO response <100ms p99 | manual-only | Oscilloscope measurement; software timestamps insufficient | N/A |
| PROD-07 | 1Hz control loop jitter ±10ms | integration | `uv run pytest tests/hw/test_benchmarks.py::test_control_jitter -x` | ❌ Wave 0 |
| PROD-07 | RTDB seqlock write latency <1ms p99 ARM64 | integration | `uv run pytest tests/hw/test_benchmarks.py::test_rtdb_latency -x` | ❌ Wave 0 |
| PROD-07 | Parquet write throughput ≥1 row/sec on eMMC | integration | `uv run pytest tests/hw/test_benchmarks.py::test_parquet_throughput -x` | ❌ Wave 0 |
| PROD-07 | DuckDB query latency <5s on ARM64 eMMC | integration | `uv run pytest tests/hw/test_benchmarks.py::test_duckdb_latency -x` | ❌ Wave 0 |
| PROD-07 | WebSocket latency <100ms on ARM64 | integration | `uv run pytest tests/hw/test_benchmarks.py::test_websocket_latency -x` | ❌ Wave 0 |
| PROD-07 | 24-hour soak: no crashes, <10% RSS growth, no data loss | integration/slow | `uv run pytest tests/hw/test_soak.py --timeout=90000` (25h) | ❌ Wave 0 |

**Manual-only tests rationale:** GPIO DO voltage verification and oscilloscope GPIO timing are hardware measurements that cannot be automated without specialized test equipment interfaces. These are documented as procedure steps with required measurements in the hardware validation report.

### Sampling Rate

- **Per task commit:** `uv run pytest tests/hw/test_boot.py -x --timeout=120` (Stage 1 only — 5 min max)
- **Per wave merge:** `uv run pytest tests/hw/ -v --ignore=tests/hw/test_soak.py --timeout=3600` (all automated stages except soak)
- **Phase gate:** All automated tests pass + manual oscilloscope measurements documented in hardware-validation-report.md + 24-hour soak completes before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/hw/__init__.py` — package init
- [ ] `tests/hw/conftest.py` — ECU SSH connection fixture, shared constants (ECU_IP, DATA_DIR)
- [ ] `tests/hw/test_boot.py` — Stage 1: service health checks over SSH
- [ ] `tests/hw/test_drivers.py` — Stage 2: CAN, RS485, GPIO driver tests
- [ ] `tests/hw/test_datapath.py` — Stage 3: data pipeline integrity on ARM64
- [ ] `tests/hw/test_benchmarks.py` — ARM64 performance benchmarks (reuses MetricsCollector patterns)
- [ ] `tests/hw/test_soak.py` — 24-hour soak test with continuous monitoring
- [ ] `tools/hw-validation/stage1-boot.sh` — bash wrapper for Stage 1
- [ ] `tools/hw-validation/stage2-drivers.sh` — bash wrapper for Stage 2
- [ ] `tools/hw-validation/stage4-gpio-timing.md` — oscilloscope procedure document
- [ ] `tools/hw-validation/stage5-soak.sh` — soak test launcher + monitor
- [ ] `tools/hw-validation/benchmark.py` — standalone benchmark harness for on-ECU execution
- [ ] `tools/hw-validation/hw_validation_report.py` — collect results → markdown report
- [ ] `docs/hardware-validation-report.md` — final report template + placeholder

---

## Sources

### Primary (HIGH confidence)

- Project source: `/home/overlord/EMS/src/safety_manager/src/gpio.c` — libgpiod v2 backend implementation, RTDB test backend, dual-mode vtable
- Project source: `/home/overlord/EMS/src/safety_manager/src/gpio.h` — GPIO vtable interface, `HAVE_LIBGPIOD` compile guard
- Project source: `/home/overlord/EMS/yocto/meta-ems/conf/machine/am65xx-ems.conf` — Phase 29 TODOs for DTB and U-Boot defconfig confirmed in file
- Project source: `/home/overlord/EMS/tests/integration/test_performance.py` — MetricsCollector, collect_gpio_latencies, collect_rtdb_write_latencies patterns (2400 lines, fully read)
- Project source: `/home/overlord/EMS/tools/verify-dev-env.sh` — tool inventory: can-utils, socat, vcan, gpio-sim
- Project source: `/home/overlord/EMS/tools/sim-all.sh` — simulator launcher pattern with PID tracking and cleanup
- Project source: `/home/overlord/EMS/config/gpio_config.yaml` — GPIO pin assignments (DI-0..7, DO-0..7) with names
- Project source: `/home/overlord/EMS/deploy/systemd/` — 15 service files confirm 14 runtime services + ems.target
- Project source: `/home/overlord/EMS/pyproject.toml` — test framework (pytest ≥8.0, pytest-timeout ≥2.4.0, psutil, pyarrow, duckdb) confirmed in dev deps

### Secondary (MEDIUM confidence)

- TI AM65xx SoC datasheet (knowledge): AM6548 has 4×Cortex-A53 + 2×R5F; M_CAN (Bosch MCAN) peripheral for CAN; multiple GPIO banks (GPIO0, GPIO1, WKUP_GPIO0)
- libgpiod v2 API (knowledge confirmed by project code): `gpiod_chip_open`, `gpiod_line_settings_new`, `gpiod_chip_request_lines`, `gpiod_line_request_get_values/set_values` — confirmed correct usage in gpio.c
- Cortex-A53 vs x86 performance (knowledge): ~3-5x single-threaded slowdown vs modern x86 is consistent with CONTEXT.md's 10x budget (accounts for eMMC vs NVMe)
- PREEMPT_RT on meta-ti-bsp (knowledge): meta-ti-bsp Scarthgap typically provides standard TI kernel; PREEMPT_RT variant (`linux-ti-rt`) may be available but requires verification on first boot

### Tertiary (LOW confidence — verify on first boot)

- GPIO chip number and line offsets for ECU-1170-552A: Assumed `/dev/gpiochip0` but actual chip number depends on Advantech BSP DTB — verify with `gpiodetect` on first boot
- U-Boot environment offset `0x3E0000`: Noted as unverified in Phase 27; must verify with `fw_printenv` before activating OTA manager
- ETH0/ETH1 interface names: Yocto predictable naming may differ from `eth0`/`eth1`

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all tools already present in pyproject.toml dev deps; CAN/RS485 tooling confirmed in verify-dev-env.sh
- Architecture: HIGH — 5-stage plan locked in CONTEXT.md; test patterns proven in test_performance.py (2400 lines read)
- Pitfalls: HIGH for items with evidence in codebase (DTB TODO, fw_env.config offset TODO); MEDIUM for RT kernel and GPIO offset (hardware-dependent, first-boot verification required)
- Validation architecture: HIGH — pytest infrastructure fully established; Wave 0 gaps are new test files only

**Research date:** 2026-03-16
**Valid until:** 2026-04-16 (hardware-dependent phase; Advantech BSP availability may change)
