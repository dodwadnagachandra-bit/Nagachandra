# Phase 13: Integration and Hardening - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Cross-module startup, crash recovery, and end-to-end validation. All 5 core modules run together with correct startup ordering, crash recovery, and validated performance under realistic load. No new requirements — validates all Phase 9-12 requirements in integration.

</domain>

<decisions>
## Implementation Decisions

### Crash Recovery Test Scenarios

| Module | SIGKILL Test | SIGTERM Test | Expected Recovery |
|--------|-------------|-------------|-------------------|
| config_manager | Yes | Yes | Restart, reload configs from disk, serve queries |
| data_manager | Yes | Yes | Restart, detect stale shm, re-attach RTDB |
| safety_manager | Yes | Yes | Restart, re-open GPIO, resume scan loop |
| comm_manager (Python) | Yes | Yes | Restart, reconnect Modbus, resume polling |
| comm_manager_c (CAN) | Yes | Yes | Restart, reopen SocketCAN, resume decode |
| logger | Yes | Yes | Restart, recover partial Parquet (.tmp cleanup), resume ingestion |

#### Recovery Criteria (all must pass within 10 seconds)

| Criterion | Measurement |
|-----------|-------------|
| Process alive | `systemctl is-active` returns active |
| RTDB updated | Module's RTDB section `last_update_ms` within 2 seconds of wall clock |
| ZMQ flowing | Telemetry subscriber receives data from restarted module |

#### Double-Fault Scenarios

| Double-Fault | Why Test It | Expected Behavior |
|-------------|------------|-------------------|
| comm_manager + logger | Most likely pair — shared ZMQ dependency, backpressure cascade | Both restart independently, no data corruption, safety unaffected |
| data_manager + comm_manager | RTDB recreation while writer is also restarting | data_manager recreates first (systemd ordering), comm attaches after |

Key rules:
- SIGKILL is the real test — simulates OOM killer, segfault, kernel panic recovery
- SIGTERM validates graceful cleanup handlers
- RTDB shared memory survives data_manager crash — re-attach on restart, only recreate if corrupted (bad magic/version)
- Safety outputs remain asserted during safety_manager restart gap — verify no gap in GPIO
- Safety_manager crash tested to verify correct restart, but excluded from double-fault (watchdog reboot is correct behavior)
- systemd RestartSec=5 already configured for all modules

### Load Profile for Performance Validation

#### Test Topologies

| Profile | Topology | Config Source | Purpose |
|---------|----------|--------------|---------|
| Residential | 1 cluster × 4 racks × 8 modules × 16 cells × 8 temps | `config/profiles/residential/` | Baseline — first deployment target |
| Container | 4 clusters × 16 racks × 20 modules × 108 cells × 40 temps | `config/profiles/container/` | Stress test — architecture ceiling |

#### Test Passes (per topology)

| Pass | CAN Sim | Modbus Sim | GPIO Harness | Duration | Purpose |
|------|---------|-----------|--------------|----------|---------|
| Clean | All racks, no drops | All devices, no timeouts | All DI normal | 10 min | Baseline — happy path end-to-end |
| Fault injection | frame_drop_rate=0.05, stale_rack=1 | DG timeout, BTMS exception_code=4 | DI-1 flood stuck high | 10 min | Degraded operation, backoff, fault events, safety response |

#### Performance Metrics

| Metric | Measurement Method | Pass Threshold | Fail Indicates |
|--------|-------------------|----------------|---------------|
| Safety GPIO response | GPIO harness: set DI, measure DO assert time | <100ms (p99) | Safety scan loop too slow under load |
| RTDB write latency | Timestamp delta: CAN frame RX → RTDB `last_update_ms` | <10ms (p99) | Seqlock contention or scheduling delay |
| ZMQ telemetry lag | Publisher seq number vs subscriber received seq | <5 messages behind | ZMQ HWM hit, backpressure |
| Logger write rate | Parquet rows written per second vs expected | ≥95% of expected | Logger can't keep up with ingestion |
| Process RSS growth | Memory usage over 10 minutes | <10% growth after first minute | Memory leak |

Key rules:
- Both topologies tested to catch both single-cluster edge cases and scale ceiling issues
- Clean pass first, then fault injection — separates real bugs from expected degraded behavior
- 10 minutes per pass = 600 telemetry snapshots, enough for one cleanup check cycle and memory leak detection
- Total runtime: ~40 minutes for all 4 passes (2 topologies × 2 passes)
- All metrics measurable without hardware — simulators provide stimulus, Python scripts collect metrics

### Pass/Fail Criteria and Test Automation

| Aspect | Decision |
|--------|----------|
| Automation | Fully automated via pytest, `make test-integration` target |
| Pass criteria | Measurable thresholds only — not log cleanliness |
| E2E verification | Known simulator data → DuckDB query → assert values match ±1% |
| Pipeline coverage | Simulator → comm → RTDB → ZMQ → logger → Parquet → DuckDB query |
| CI | Unit tests in CI, integration tests locally only (40+ min runtime) |
| Startup test | All 6 services active within 30 seconds |
| Recovery test | Process alive + RTDB fresh + ZMQ flowing within 10 seconds |
| Log errors | Expected during fault injection — NOT treated as failures |
| Makefile target | `make test-integration` separate from `make test` (unit tests) |

#### Pass/Fail by Test Category

| Test Category | Pass Criteria | NOT a Failure |
|---------------|-------------|---------------|
| Startup sequence | All 6 services reach `active` within 30 seconds | Transient "connecting..." log lines during startup |
| End-to-end pipeline | DuckDB query returns data matching simulator output (±1% tolerance) | Logger buffering delay (up to 2 seconds) |
| Crash recovery | All 3 recovery criteria met within 10 seconds per module | Log ERROR from the crash itself |
| Safety under load | GPIO response <100ms p99 | Warning logs from stale RTDB sections during comm fault injection |
| Performance metrics | All 5 metrics within thresholds | High CPU during container-scale test |

#### End-to-End Pipeline Verification Steps

| Step | Action | Assertion |
|------|--------|-----------|
| 1 | Start all modules + simulators with known seed values | All services active |
| 2 | Wait 30 seconds for data to flow through pipeline | — |
| 3 | Query `time_series` for pack_soc via ZMQ REQ/REP | Returns ≥25 data points |
| 4 | Compare returned SOC values against simulator output | Values match within ±1% |
| 5 | Query `event_log` for comm_fault events | Returns expected events from fault injection |
| 6 | Verify Parquet files exist in `data/{year}/{month}/{day}/` | At least 1 cluster file + 1 system file |
| 7 | Read Parquet directly with PyArrow, verify row count and schema | Row count ≥25, all expected columns present |
| 8 | Verify JSONL events file exists and is valid JSON per line | File parseable, events have required fields |

### Claude's Discretion

- pytest fixture design for launching/killing modules as subprocesses
- Simulator seed configuration for deterministic test data
- Timing measurement implementation (GPIO harness instrumentation)
- ZMQ lag measurement approach (sequence number tracking)
- RSS monitoring implementation (psutil or /proc)
- Test report format and output
- Makefile target structure for test-integration

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tools/simulators/can_sim/` — CAN simulator with fault injection (frame_drop_rate, stale_rack)
- `tools/simulators/modbus_sim/` — Modbus simulator with fault injection (response_timeout, exception_code)
- `tools/simulators/gpio_harness/` — GPIO harness with RTDB + gpio-sim backends, timing measurement
- `config/profiles/residential/` — Full residential config set (1×4×8×16×8)
- `config/profiles/commercial/` — Commercial config set
- `config/profiles/container/` — Container config set (4×16×20×108×40)
- `deploy/systemd/` — All service files ready
- `tests/` — 119+ existing tests (pytest), established patterns for subprocess testing
- `Makefile` — 16 dev targets, pattern for adding `test-integration`
- `src/common/python/src/ems_common/ipc.py` — encode/decode helpers for ZMQ assertions
- `src/common/python/src/ems_common/rtdb.py` — attach_rtdb() for RTDB state assertions

### Established Patterns
- pytest with subprocess launching (used in M0 integration tests — Phase 8)
- Atomic file writes (.tmp → rename) for crash recovery verification
- Seqlock sequence validation in tests
- GPIO harness CLI for scripted DI/DO manipulation
- Virtual CAN (vcan0) for CAN tests without hardware
- MessagePack envelope decode for ZMQ message assertions

### Integration Points
- All 5 core modules from Phases 9-12 must be complete
- systemd ordering: data_manager → config_manager → safety_manager → comm_manager → logger
- RTDB shared memory at `/run/ems/ems_rtdb`
- ZMQ sockets: telemetry (PUB/SUB), logger (PUSH/PULL), control_cmd (REQ/REP), config (REQ/REP)
- Parquet output: `data/{year}/{month}/{day}/telemetry_{cluster}_{hour}.parquet`
- JSONL output: `data/events/{year}/{month}/events_{YYYYMMDD}.jsonl`
- DuckDB query API: 6 predefined query types via ZMQ REQ/REP

</code_context>

<specifics>
## Specific Ideas

- Phase 8 (M0 integration validation) established the pattern — Phase 13 extends it to real modules instead of stubs
- GPIO harness timing measurement already exists — reuse for safety response time assertions
- SignalGenerator in CAN sim produces deterministic patterns (sinusoidal drift) — use known seeds for E2E assertions
- Container-scale test will be the first time 64 racks write to RTDB simultaneously — watch for seqlock contention
- ZMQ HWM default is 1000 — container at 1Hz with 64 rack topics = 64 messages/second, well within limit

</specifics>

<deferred>
## Deferred Ideas

- CI integration tests (nightly job) — deferred until team grows or CI budget allows
- Hardware-in-the-loop testing with ECU-1170-552A — deferred to PLAT-01 resolution
- Watchdog reboot recovery test — requires /dev/watchdog hardware
- Multi-day soak test — deferred to pre-production hardening (M5)
- Performance profiling and optimization — only if metrics fail thresholds

</deferred>

---

*Phase: 13-integration*
*Context gathered: 2026-03-14*
