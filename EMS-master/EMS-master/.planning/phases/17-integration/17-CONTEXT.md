# Phase 17: Integration and Hardening - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Cross-module startup, alarm-to-control protection flow, dispatch flow, crash recovery, and hot-reload validation. All M1 modules + control_manager + alarm_manager run together. No new requirements — validates all Phase 14-16 requirements in integration.

</domain>

<decisions>
## Implementation Decisions

### Protection Flow Test Methodology

How to verify the end-to-end flow: BMS cell voltage drops → alarm fires → control_manager transitions to FAULT → PCS stops?

**Decision:** Scripted test using CAN simulator signal manipulation + alarm_manager + control_manager + PCS Modbus simulator. Verify at each hop in the chain.

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all M1 modules + control_manager + alarm_manager + simulators | All services active, RTDB valid | 30s |
| 2 | Set control_manager to STANDBY (PCS ON) | RTDB `control_state` == STANDBY, PCS state == RUNNING | 15s |
| 3 | Modify CAN simulator to send cell_voltage_min = 2.7V (below 2.8V protection threshold) | RTDB `min_cell_v` < 2.8V within 2 seconds | 5s |
| 4 | Wait for alarm delay (5000ms) | alarm_manager publishes `cell_voltage_low` alarm with severity=protection | 7s |
| 5 | Verify control_manager receives protection alarm | RTDB `control_state` transitions to FAULT | 3s |
| 6 | Verify PCS receives shutdown command | PCS simulator registers power=0 and off command | 5s |
| 7 | Reset CAN simulator to normal voltages | RTDB `min_cell_v` > 2.8V | 3s |
| 8 | Wait for cooldown (60s) or send manual fault_reset | RTDB `control_state` transitions to IDLE | 5s |

Key rules:
- This test validates the entire chain: simulator → comm_manager → RTDB → alarm_manager → ZMQ PUB → control_manager → RTDB setpoint → comm_manager → PCS Modbus
- The 5-second alarm delay is part of the test — it validates ALM-05 (delay timer)
- PCS stop verification uses the Modbus simulator's state machine (already built in M0)
- The 60-second cooldown (Phase 16) can be shortened for tests via configurable parameter, or use manual fault_reset to speed up

**Rationale:** End-to-end protection flow is the highest-value integration test — if this works, the alarm-to-control chain is proven. The step-by-step approach with timeouts at each hop isolates failures: if step 4 fails, it's alarm_manager; if step 5 fails, it's the PUB/SUB wiring; if step 6 fails, it's the PCS command path.

### Dispatch Flow Test Methodology

How to verify: simulators provide SOC + PCS telemetry → control_manager computes setpoint → PCS simulator receives correct power command?

**Decision:** Deterministic test with known simulator values and expected PCS register writes.

| Test Scenario | Setup | Expected Setpoint | Verification |
|--------------|-------|------------------|-------------|
| Normal discharge | SOC=50%, grid offline (DI-0=0), NIGHT mode | Discharge at max_discharge_kw (25 kW) | PCS register 0x500E = 250 (25.0 × 10) |
| SOC cutoff | SOC=10% (at discharge_cutoff_pct), NIGHT mode | Zero (stop discharge) | PCS register 0x500E = 0, state → IDLE |
| Temperature derating | SOC=50%, BMS cell temp=45°C, NIGHT mode | Derated: 25 × 0.5 = 12.5 kW | PCS register 0x500E = 125 |
| Manual override | MANUAL mode, operator sets 15 kW | 15 kW | PCS register 0x500E = 150 |
| No source available | Grid offline, SOC at cutoff, no DG | Zero, state → IDLE | PCS register 0x500E = 0 |

Key rules:
- Use residential profile (25 kW max) for deterministic calculations
- CAN simulator provides controlled SOC values (set via signal generator seed)
- GPIO harness controls DI-0 (ACDB feedback) to simulate grid availability
- PCS Modbus simulator's register state is the ground truth for setpoint delivery
- Temperature derating test requires CAN simulator to set cell_temp_max above 40°C threshold

**Rationale:** These 5 scenarios cover the dispatch decision space: normal operation, SOC limits (CTRL-05), temperature derating (CTRL-06), manual override (CTRL-10), and source exhaustion (CTRL-04). Each maps to a specific requirement being validated in integration.

### Hot-Reload Validation

How to verify config changes apply without restart?

**Decision:** Modify config files while modules are running, then verify behavior changes within 2 seconds.

| Config Change | Module | Expected Behavior Change | Verification |
|--------------|--------|------------------------|-------------|
| Change `discharge_cutoff_pct` from 10% to 20% | control_manager | System stops discharging when SOC drops to 20% (was 10%) | Set SOC=15%, verify state → IDLE |
| Change `cell_voltage_high` threshold from 3.65V to 3.50V | alarm_manager | Alarm activates at 3.50V (was 3.65V) | Set cell_v=3.55V, verify alarm fires |
| Disable `soc_low` alarm (enabled: false) | alarm_manager | No alarm when SOC drops to 3% | Set SOC=3%, verify no alarm event |
| Change `max_discharge_kw` from 25 to 15 | control_manager | Setpoint clamped to 15 kW (was 25 kW) | Request 25 kW, verify PCS gets 150 (15.0 × 10) |

Key rules:
- Modify YAML files on disk — config_manager's inotify watcher detects changes
- Wait 1 second for debounce (500ms) + validation + swap
- Verify via RTDB state and ZMQ events within 2 seconds of file save
- Hot-reload does NOT require restarting any module
- Test both control_config and alarms_config changes in the same test run

**Rationale:** Hot-reload is a key operational requirement — field engineers need to tune thresholds without downtime. Testing it in integration (not just unit tests) validates the full chain: inotify → config_manager → validate → ZMQ config_reload event → module receives → applies. The 4 scenarios cover both modules and test the most operationally relevant changes (cutoff, threshold, enable/disable, power limit).

### Crash Recovery and Startup

**Decision:** Reuse Phase 13 crash recovery patterns with additions for control_manager and alarm_manager.

| Module | SIGKILL Recovery | Expected Behavior |
|--------|-----------------|-------------------|
| control_manager | Restart within 10s | State returns to IDLE (safe default), PCS setpoint in RTDB persists, comm_manager keeps sending last setpoint |
| alarm_manager | Restart within 10s | All alarms reset to NORMAL (re-evaluate on first tick), active alarm list cleared, re-populates from RTDB within 1 tick |

| Aspect | Decision |
|--------|----------|
| Startup ordering | M1 modules first → control_manager → alarm_manager (matches systemd After= deps) |
| Startup test | All 8+ services active within 30 seconds |
| Recovery criteria | Process alive + RTDB section updated + ZMQ flowing, all within 10 seconds |
| control_manager crash safety | On restart, reads RTDB and determines safe starting state (IDLE). Does NOT resume previous state — always starts fresh in IDLE. |
| alarm_manager crash safety | On restart, evaluates all alarm rules against current RTDB values. If any threshold exceeded, alarm fires immediately (no delay — delay only applies to first activation). |

Key rules:
- control_manager always starts in IDLE (PCS OFF) on restart — never resumes CHARGING/DISCHARGING to avoid stale-state dispatch
- alarm_manager fires alarms immediately on restart if thresholds are currently exceeded — the delay timer is for transient filtering, not for startup masking
- Crash recovery tests reuse Phase 13 `ModuleProcess` and `MetricsCollector` infrastructure
- Add control_manager and alarm_manager to the existing CRASH_MATRIX parametrized test

**Rationale:** Starting control_manager in IDLE (not resuming previous state) is the safe default — if it crashed mid-dispatch, the PCS might be in an unknown state. IDLE forces a clean re-negotiation through the state machine. Alarm_manager skipping delay on restart ensures that ongoing threshold violations are immediately re-detected — the 5-second delay is for filtering transient spikes, not for masking real conditions after a crash.

### Claude's Discretion

- Test infrastructure reuse from Phase 13 (conftest.py, ModuleProcess, MetricsCollector)
- CAN simulator signal manipulation for controlled test scenarios
- GPIO harness integration for grid availability simulation
- Makefile target naming (extend `test-integration` or new `test-integration-m2`)
- Test duration and CI considerations

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/integration/conftest.py` — ModuleProcess, MetricsCollector, check_rtdb helpers (Phase 13)
- `tests/integration/test_crash_recovery.py` — CRASH_MATRIX pattern, SIGKILL/SIGTERM parametrization
- `tests/integration/test_e2e_pipeline.py` — E2E pipeline test pattern with simulator fixtures
- `tests/integration/test_startup.py` — build_start_order pattern
- `tools/simulators/can_sim/` — Signal generator with controllable seed values
- `tools/simulators/modbus_sim/` — PCS state machine with register inspection
- `tools/simulators/gpio_harness/` — DI/DO manipulation for grid availability simulation
- `Makefile` — `test-integration` target (extend for M2)

### Established Patterns
- pytest parametrize for crash recovery matrix (module × signal pairs)
- ModuleProcess subprocess wrapper with health check, signal, RSS tracking
- wait_for_criteria with timeout for async verification
- tcp://127.0.0.1 random ports for ZMQ test isolation

### Integration Points
- Phase 14 control_manager: state machine, PCS commands, ZMQ control_cmd API
- Phase 15 alarm_manager: alarm evaluation, lifecycle, ZMQ alarm_cmd API
- Phase 16: source priority, derating, protection flow, hot-reload
- M1 modules: data_manager, config_manager, safety_manager, comm_manager, logger — all running simultaneously
- Simulators: CAN sim, Modbus sim, GPIO harness — provide test stimulus

</code_context>

<specifics>
## Specific Ideas

- Protection flow test is the most complex and highest-value — it crosses 5 module boundaries
- The PCS Modbus simulator already tracks register writes (state_machine.py) — can inspect 0x500E for setpoint verification
- CAN simulator's SignalGenerator can be seeded for deterministic voltage/temperature values
- Phase 13 test infrastructure is directly reusable — just add new module specs to build_start_order
- Hot-reload test validates the entire config_manager → module chain — this is operationally critical

</specifics>

<deferred>
## Deferred Ideas

- Performance profiling under M2 load (control + alarm overhead on top of M1 modules)
- Long-duration soak test (hours, not minutes) — deferred to pre-production (M5)
- Hardware-in-the-loop with ECU-1170-552A — blocked on PLAT-01
- Multi-PCS dispatch flow testing — blocked on Decision #7.3
- Scheduler integration tests — deferred to M3

</deferred>

---

*Phase: 17-integration*
*Context gathered: 2026-03-15*
