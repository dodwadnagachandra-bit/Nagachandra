# ECU-1170-552A Hardware Validation Report

**Date:** [DATE]
**ECU Serial:** [SERIAL]
**Yocto Image:** [IMAGE_VERSION]
**Tester:** [NAME]

---

## Executive Summary

[PASS/FAIL] — [N/5] stages passed.

| Stage | Name | Result |
|-------|------|--------|
| 1 | Boot Validation | [PASS/FAIL] |
| 2 | Driver Verification | [PASS/FAIL] |
| 3 | Data Path Validation | [PASS/FAIL] |
| 4 | Safety GPIO Timing | [PASS/FAIL] |
| 5 | 24-Hour Soak Test | [PASS/FAIL] |

---

## Stage 1: Boot Validation

| Service | Status | Notes |
|---------|--------|-------|
| data_manager | [PASS/FAIL] | |
| ems-data-manager-python | [PASS/FAIL] | |
| config_manager | [PASS/FAIL] | |
| safety_manager | [PASS/FAIL] | |
| comm_manager_c | [PASS/FAIL] | |
| comm_manager | [PASS/FAIL] | |
| logger | [PASS/FAIL] | |
| control_manager | [PASS/FAIL] | |
| alarm_manager | [PASS/FAIL] | |
| scheduler | [PASS/FAIL] | |
| diagnostics | [PASS/FAIL] | |
| cloud_manager | [PASS/FAIL] | |
| ota_manager | [PASS/FAIL] | |
| hmi_server | [PASS/FAIL] | |

**Boot time:** [N]s (target: <60s)
**Result:** [PASS/FAIL]

---

## Stage 2: Driver Verification

### CAN

| Interface | Test | Result | Notes |
|-----------|------|--------|-------|
| CAN0 | Interface exists | [PASS/FAIL] | |
| CAN0 | Loopback send/recv | [PASS/FAIL] | |
| CAN1 | Interface exists | [PASS/FAIL] | |
| CAN1 | Loopback send/recv | [PASS/FAIL] | |

### RS485

| Port | Device Path | UART Exists | Modbus Poll | Notes |
|------|-------------|-------------|------------|-------|
| RS485-1 (PCS) | /dev/ttyS? | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |
| RS485-2 (Meter) | /dev/ttyS? | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |
| RS485-3 (BTMS) | /dev/ttyS? | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |
| RS485-4 (DG/PV) | /dev/ttyS? | [PASS/FAIL] | [PASS/FAIL/XFAIL] | |

### GPIO

| Test | Result | Notes |
|------|--------|-------|
| gpiochip detected | [PASS/FAIL] | Chip: [NAME] |
| DI-0..7 readable (8 lines) | [PASS/FAIL] | |
| DO safe write test | [PASS/FAIL] | Tested: DO-3 RUNNING_LAMP, DO-7 SPARE_DO7 |

### Network

| Interface | Name | Link | Result |
|-----------|------|------|--------|
| ETH0 (WAN) | [ethN] | [UP/DOWN] | [PASS/FAIL] |
| ETH1 (LAN) | [ethN] | [UP/DOWN] | [PASS/FAIL] |

### HDMI

| Test | Result | Notes |
|------|--------|-------|
| DRM subsystem present | [PASS/FAIL] | |
| Display detected | [YES/NO] | |

**Result:** [PASS/FAIL]

---

## Stage 3: Data Path Validation

| Pipeline Step | Result | Notes |
|---------------|--------|-------|
| RTDB shm exists (/dev/shm/ems_rtdb) | [PASS/FAIL] | |
| ZMQ telemetry publishing (topic: telemetry) | [PASS/FAIL] | |
| Parquet files created in /data/ | [PASS/FAIL] | |
| Parquet rows growing (1Hz writes) | [PASS/FAIL] | |
| DuckDB query over 24h data | [PASS/FAIL] | |

**Result:** [PASS/FAIL]

---

## Stage 4: Safety GPIO Timing

*Procedure: tools/hw-validation/stage4-gpio-timing.md*
*Measured with oscilloscope: CH1 = DI-6 (ESTOP_NO), CH2 = DO-5 (PCS_STOP)*

| Measurement | Value | Target | Result |
|-------------|-------|--------|--------|
| DI-6 -> DO-5 p50 | [N] ms | — | |
| DI-6 -> DO-5 p95 | [N] ms | — | |
| DI-6 -> DO-5 p99 | [N] ms | <100 ms | [PASS/FAIL] |
| Standard deviation (σ) | [N] ms | — | |
| Samples collected | [N] | >=100 | [PASS/FAIL] |
| SCHED_FIFO active | [YES/NO] | YES | [PASS/FAIL] |
| PREEMPT_RT kernel | [YES/NO] | YES | [PASS/FAIL] |

**Dual-channel cross-check:**

| Test | Expected | Actual | Result |
|------|----------|--------|--------|
| Both channels (DI-6 high + DI-7 low) | DO-5 asserts | | [PASS/FAIL] |
| DI-6 only (DI-7 high) | DO-5 no-assert | | [PASS/FAIL] |
| DI-7 only (DI-6 low) | DO-5 no-assert | | [PASS/FAIL] |

**Result:** [PASS/FAIL]

---

## Stage 5: 24-Hour Soak Test

| Metric | Value | Target | Result |
|--------|-------|--------|--------|
| Duration | [N] h | 24 h | |
| Service restarts | [N] | 0 | [PASS/FAIL] |
| Max RSS growth | [N] % | <10% | [PASS/FAIL] |
| Data gaps (Parquet stale >10s) | [N] | 0 | [PASS/FAIL] |
| Disk usage at end | [N] % | <80% | |

**Pre-soak checks:** CAN traffic [PASS/FAIL], Modbus response [PASS/FAIL]
**Result:** [PASS/FAIL]

---

## ARM64 Performance Benchmarks

*Benchmarks run with full system load (all 14 services active, simulators running).*
*Reference values measured on x86_64 development machine during M1-M4.*

| Benchmark | Measured | Target | x86 Ref | Result |
|-----------|----------|--------|---------|--------|
| Control loop jitter (1-sigma) | [N] ms | <10 ms | <1 ms | [PASS/FAIL] |
| RTDB write latency (p99) | [N] ms | <1 ms | <0.1 ms | [PASS/FAIL] |
| Parquet throughput | [N] rows/s | >=1 row/s | ~10 rows/s | [PASS/FAIL] |
| DuckDB query (24h dataset) | [N] s | <5 s | <1 s | [PASS/FAIL] |
| WebSocket latency (p99) | [N] ms | <100 ms | <10 ms | [PASS/FAIL] |

---

## Hardware Configuration

| Item | Value |
|------|-------|
| Kernel | [uname -r] |
| Device tree blob | [DTB filename from /boot] |
| U-Boot defconfig | [defconfig used in Yocto] |
| GPIO chip | [gpiodetect output] |
| CAN interfaces | [ip link show type can] |
| RS485 UART paths | [ls /dev/ttyS*] |
| Ethernet interfaces | [ip -br link] |
| fw_env.config offset | [verified offset — 0x3E0000 or hardware-confirmed] |
| ECU SSD mount | /data (ext4, Samsung 850 EVO or equivalent) |
| EMS venv path | /opt/ems/python/.venv |
| Yocto image layer | meta-ems |

---

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Engineer | | | |
| Reviewer | | | |
