# Phase 8: Integration Validation - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

All M0 deliverables (CAN simulator, Modbus PCS simulator, GPIO test harness, platform scaffold) working together with YAML-configurable fault injection, signal tuning, and CI integration smoke tests. Validates that the platform is ready for M1 module development.

Output: Fault injection + signal tuning config sections (3 simulators), sim-all launcher script, CI integration test, updated JSON schemas.

Not in scope: comm_manager (M1), safety_manager (M1), control_manager (M2), new simulators, physics-based modeling.

</domain>

<decisions>
## Implementation Decisions

### Fault injection design — config-driven, active at startup
- Faults defined as `fault_injection:` section in each simulator's existing config (bms_config.yaml, pcs_config.yaml, gpio_config.yaml)
- Optional section — absent means no faults (backward compatible with existing configs)
- Faults active at startup from config — no runtime injection protocol, no IPC needed
- Simple, deterministic, CI-friendly — reproducible by sharing the config file

### CAN fault modes
- `frame_drop_rate: 0.05` — percentage of frames silently dropped (simulates loose connectors)
- `corrupt_data: true` — occasional garbled signal values (simulates EMI on CAN bus)
- `stale_timeout_ms: 5000` — stop sending for one rack to simulate comm loss (dead BMU)
- These three cover the failure modes comm_manager must handle in M1

### Modbus fault modes
- `response_timeout: true` — simulator stops responding for configurable duration (RS485 cable disconnect)
- `exception_code: 0x02` — illegal data address on specific registers (PCS firmware bugs)

### GPIO fault modes
- `stuck_pins: [6]` — pin ignores writes, stays at configured value (relay weld/failure)
- `bounce_ms: 5` — pin toggles rapidly for N ms after set (contact wear)

### Sim-all orchestration — shell script with process management
- `tools/sim-all.sh` backgrounds all 3 simulators, stores PIDs, traps SIGINT for clean teardown
- Parallel start — all 3 launch simultaneously (independent, no ordering dependency)
- Health check polling after launch: CAN checks vcan0 up, Modbus checks PTY/TCP port listening, GPIO checks shm created. 5-second timeout with retries.
- Per-simulator log files (`logs/sim-can.log`, `logs/sim-modbus.log`, `logs/sim-gpio.log`) with combined stdout summary line
- `--profile` flag defaulting to residential. `make sim-all PROFILE=commercial` passes profile configs to all 3 simulators consistently

### CI integration test — lightweight smoke test
- Start all 3 sims, exercise one operation each, verify output, tear down (~30s total)
- CAN: send one frame, verify decode
- Modbus: one read/write round-trip, verify register values
- GPIO: set one DI pin, read it back
- One fault per simulator quick validation — proves fault injection mechanism works
- Test file: `tests/test_integration.py` with `@pytest.mark.integration` marker
- Add `integration-test` job to existing `pr-check.yml` with `needs: build-and-test`

### YAML configurability — extend existing configs and schemas
- `fault_injection:` optional section in bms_config, pcs_config, gpio_config
- `signal_tuning:` optional section for noise amplitude, drift range, base values
  - CAN: `noise_sigma`, `drift_amplitude`, `drift_period_s`, `base_voltage`
  - Modbus: `ramp_rate_pct_per_s`, `startup_delay_s`, `voltage_noise`
  - GPIO: `default_di_state` per pin
- Profiles (residential/commercial/container) stay clean — no fault injection in profiles
- Faults are opt-in overlays: developer adds section to active config when testing
- Extend existing JSON schemas — required by Phase 2 `additionalProperties: false` enforcement
- Validation pipeline already covers all configs — new sections validated automatically

### Claude's Discretion
- Exact fault injection config field names and value ranges
- Health check implementation details (polling mechanism, retry logic)
- sim-all.sh internal structure (functions vs linear script)
- Integration test fixture design (how sims are started/stopped in pytest)
- Log file rotation or size limits for sim logs
- Whether signal_tuning values have min/max validation in schemas

</decisions>

<specifics>
## Specific Ideas

- sim-all stdout summary should give at-a-glance status: "CAN: running (vcan0, 2 racks) | Modbus: running (RTU, /dev/pts/3) | GPIO: running (RTDB)"
- Fault injection configs should be shareable — "here's a config that reproduces the comm loss bug" via file sharing
- Integration test in CI adds <30s to existing ~5 min pipeline — keep it lightweight
- Profile selection via `--profile` flag ensures all 3 simulators use consistent topology (no mixing residential CAN with container Modbus)
- The sim-all launcher is a developer productivity tool — quick way to get a full simulated environment running for HMI demos and M1 development

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tools/simulators/can_sim/signals.py` — Signal drift/noise generation (reusable pattern for signal_tuning)
- `tools/simulators/modbus_sim/state_machine.py` — PCS state machine with configurable delays (already parameterized)
- `tools/simulators/gpio_harness/backend.py` — Backend ABC with detect_backend factory (extensible for fault wrapping)
- `config/profiles/` — 3 complete profiles (residential, commercial, container) ready for `--profile` flag

### Established Patterns
- All simulators: CLI + importable, `tools/simulators/` package, `__main__.py` entry point
- Makefile targets: `sim-can`, `sim-modbus`, `sim-gpio` — `sim-all` follows naturally
- Config schemas: `additionalProperties: false` at every level — new sections must be in schema
- pytest markers: `rtu`, `gpio_sim` — `integration` marker follows same convention
- Simulator launch: `uv run python -m tools.simulators.{name}` pattern

### Integration Points
- `Makefile` — add `sim-all` target calling `tools/sim-all.sh`
- `config/schemas/*.schema.json` — extend bms, pcs, gpio schemas with fault_injection + signal_tuning
- `.github/workflows/pr-check.yml` — add integration-test job
- `pyproject.toml` — add `integration` marker to pytest config
- Each simulator's `__main__.py` or `simulator.py` — read and apply fault_injection config

</code_context>

<deferred>
## Deferred Ideas

- Performance testing with 64 racks (128 BMUs) at full cell count — future stress testing
- Runtime fault injection via CLI commands to running simulators — future enhancement if needed
- Timed fault scenario sequences (scripted test playbooks) — future enhancement
- Docker-based simulator environment for CI — unnecessary, native processes work fine
- Wiring fault scenario library (pre-built test sequences) — M1 safety_manager testing
- CAN bus-off simulation, error frames, arbitration loss — requires kernel-level support
- Modbus register corruption (random values on read) — rare edge case, M1 hardening
- GPIO chip unresponsive simulation — rare edge case, M1 hardening

</deferred>

---

*Phase: 08-integration-validation*
*Context gathered: 2026-03-13*
