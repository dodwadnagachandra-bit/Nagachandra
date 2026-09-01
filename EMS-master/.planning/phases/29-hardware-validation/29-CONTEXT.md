# Phase 29: Hardware Validation - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

ECU-1170-552A hardware validation: boot test, driver verification (CAN, RS485, GPIO, HDMI), performance benchmarks, and 24-hour soak test. Covers PROD-07. Requires physical ECU hardware (PLAT-01).

</domain>

<decisions>
## Implementation Decisions

### Hardware Test Plan

What should be validated on the physical ECU-1170-552A?

**Decision:** 5-stage validation progressing from basic boot to full-system endurance.

| Stage | Test | Duration | Pass Criteria |
|-------|------|----------|---------------|
| 1. Boot | Yocto image boots, all 14 services start | 5 min | `systemctl is-active` for all services within 60s |
| 2. Drivers | CAN, RS485, GPIO, HDMI individually tested | 30 min | Each driver sends/receives correctly |
| 3. Data Path | CAN sim → RTDB → ZMQ → Parquet pipeline on ARM64 | 30 min | Data matches x86 reference output |
| 4. Safety Timing | GPIO response time with oscilloscope | 30 min | DI edge → DO assert <100ms (p99) |
| 5. Soak Test | Full system under simulated load | 24 hours | No crashes, no memory growth, no data loss |

Key rules:
- Stage 1-3 can run without real BMS/PCS hardware — simulators provide stimulus.
- Stage 4 requires real GPIO pins + oscilloscope — measures actual hardware latency, not simulated.
- Stage 5 uses CAN/Modbus simulators running on a separate machine connected via physical CAN/RS485 cables.
- All tests scripted where possible — manual steps documented in a test procedure.
- Results logged to a hardware validation report (not just pass/fail — actual measurements).

**Rationale:** Progressive validation catches issues early — a boot failure is found in stage 1 before investing time in driver tests. The 24-hour soak test is the industry standard for embedded system qualification (IEC 61131-2 recommends 72h, but 24h is sufficient for v1.0). Real GPIO timing with oscilloscope is the only way to validate the <100ms safety requirement on actual hardware — software measurements in M1 Phase 10 showed <10ms but don't include hardware latency.

### Driver Verification Tests

What specific tests validate each hardware driver?

**Decision:** Per-driver test with known stimulus and expected response.

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

Key rules:
- CAN tests use `python-can` on a laptop connected via USB-CAN adapter — same tooling as M0 CAN simulator.
- RS485 tests use the existing Modbus simulator running on a laptop with USB-RS485 adapter.
- GPIO tests use jumper wires (no test fixture needed for v1.0) — document pin mappings.
- HDMI test validates the full stack: FastAPI → WebSocket → React → touchscreen input.
- Each driver test is run independently first, then all together in stage 5 soak test.

**Rationale:** Using the existing simulators (M0) as test stimulus avoids building new test tooling. USB-CAN and USB-RS485 adapters are standard field commissioning tools — the same setup used for production deployment. Per-driver isolation before combined testing follows standard hardware bring-up practice.

### Performance Benchmarks

What performance metrics should be measured on ARM64?

**Decision:** 6 benchmarks covering the critical path from hardware input to persistent storage.

| Benchmark | Method | Target | x86 Reference |
|-----------|--------|--------|--------------|
| Safety GPIO response | Oscilloscope: DI edge → DO assert | <100ms (p99) | <10ms (measured in M1) |
| 1Hz control loop jitter | Measure tick-to-tick interval over 1 hour | ±10ms (1 sigma) | <1ms on x86 |
| RTDB seqlock write latency | Timestamp before/after write, 10K samples | <1ms (p99) | <0.1ms on x86 |
| Parquet write throughput | Rows/second for container topology (64 racks) | ≥1 row/sec sustained | ~10 rows/sec on x86 |
| DuckDB query latency | time_series query over 24h Parquet data | <5s | <1s on x86 |
| WebSocket latency | ZMQ PUB → WebSocket client receive | <100ms | <10ms on x86 |

Key rules:
- All benchmarks run with full system load (all 14 services active, simulators running).
- Compare against x86 reference values (measured during M1-M4 development) — ARM64 should be within 10x.
- Safety GPIO is the only hard requirement (<100ms) — all others are soft targets.
- If DuckDB query exceeds 5s on ARM64, consider query optimization or pre-aggregation as tech debt.
- Results documented in hardware validation report with actual measured values.

**Rationale:** The ECU-1170-552A has 4x Cortex-A53 cores — roughly 3-5x slower than a modern x86 for single-threaded workloads. The 10x margin accounts for this plus filesystem differences (eMMC vs NVMe SSD on dev machines). Safety GPIO is measured with oscilloscope because software timestamps don't capture kernel scheduling jitter on ARM.

### Claude's Discretion

- Test script implementation (bash scripts vs pytest on the ECU)
- Oscilloscope measurement methodology (trigger setup, sample count)
- Soak test monitoring (how to detect memory growth, data loss)
- Hardware validation report format (markdown, PDF, or structured data)
- Whether to run QEMU ARM64 validation before physical hardware
- Network connectivity test (ETH0 WAN, ETH1 LAN)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cmake/toolchains/aarch64-linux.cmake` — ARM64 cross-compile toolchain
- `tools/setup-dev-env.sh` — Dev environment reference
- `tools/verify-dev-env.sh` — Verification checks (adapt for ECU)
- All M0 simulators (CAN, Modbus, GPIO) — test stimulus for hardware validation
- Phase 13 MetricsCollector — RSS growth, timing measurement patterns
- `.github/workflows/master-merge.yml` — CI ARM64 cross-compile already validated

### Integration Points
- Yocto image from Phase 27 — the artifact being validated
- All 14 systemd services — must start and run on ARM64
- Physical interfaces: CAN0, CAN1, RS485×4, GPIO (DI×8, DO×8), HDMI, ETH×2
- External test equipment: USB-CAN adapter, USB-RS485 adapters, oscilloscope, 10" touch panel

</code_context>

<specifics>
## Specific Ideas

- This is the first phase that REQUIRES physical hardware — all prior phases worked with simulators
- ECU-1170-552A availability is PLAT-01 tech debt — this phase unblocks it
- The 24-hour soak test is the final gate before v1.0 production release
- Consider running a "pre-flight" on QEMU ARM64 before shipping to the physical ECU
- GPIO pin mappings must match the Advantech BSP documentation

</specifics>

<deferred>
## Deferred Ideas

- 72-hour extended soak test (IEC 61131-2 recommendation) — 24h sufficient for v1.0
- EMC/EMI testing — certification lab, not software scope
- Temperature chamber testing (-40°C to +70°C) — hardware qualification
- Power cycle endurance (10,000 cycles) — hardware qualification
- Production manufacturing test fixture — factory scope

</deferred>

---

*Phase: 29-hardware-validation*
*Context gathered: 2026-03-16*
