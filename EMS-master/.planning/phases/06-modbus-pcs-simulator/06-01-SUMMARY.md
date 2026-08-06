---
phase: 06-modbus-pcs-simulator
plan: 01
subsystem: simulators
tags: [modbus, pcs, pymodbus, simulator, state-machine]
dependency_graph:
  requires: [01-01, 02-01]
  provides: [modbus-sim, pcs-register-map]
  affects: [comm_manager, control_manager, hmi_server]
tech_stack:
  added: [pymodbus-3.12]
  patterns: [callback-datablock, zero-mode-device-context, socat-pty-pair]
key_files:
  created:
    - config/pcs_register_map.yaml
    - tools/simulators/modbus_sim/__init__.py
    - tools/simulators/modbus_sim/__main__.py
    - tools/simulators/modbus_sim/simulator.py
    - tools/simulators/modbus_sim/state_machine.py
    - tools/simulators/modbus_sim/register_map.py
    - tools/simulators/modbus_sim/pty_pair.py
    - tools/simulators/modbus_sim/signals.py
    - tests/test_modbus_simulator.py
  modified:
    - pyproject.toml
    - config/pcs_config.yaml
    - config/schemas/pcs_config.schema.json
    - config/profiles/residential/pcs_config.yaml
    - config/profiles/commercial/pcs_config.yaml
    - config/profiles/container/pcs_config.yaml
    - Makefile
decisions:
  - "pymodbus 3.12 removed zero_mode and slaves kwargs; created _ZeroModeDeviceContext subclass and used devices= param"
  - "client.close() is synchronous in pymodbus 3.12+; all test cleanup uses sync close"
metrics:
  duration: "12 min"
  completed: "2026-03-13"
  tasks: 3
  tests_added: 32
  tests_total: 92
  files_created: 9
  files_modified: 7
---

# Phase 6 Plan 1: Modbus PCS Simulator Summary

Modbus PCS simulator with 32-register V1.24 synthetic map, 5-state machine with power ramping, TCP/RTU transport, and 32-test suite covering register access through telemetry coherence.

## Task Results

### Task 1: Register Map YAML and Config Updates
**Commit:** 04f0ab6

- Created `config/pcs_register_map.yaml` with 32 registers in 6 groups (AC, DC, Thermal, Status, Energy, Control)
- Added `pymodbus>=3.7` to dev dependencies, `rtu` pytest marker
- Added `register_map_path` to pcs_config.yaml, JSON schema, and all 3 profile configs
- All 14 config files pass validation

### Task 2: Python Modbus Simulator Package
**Commit:** 64925ca

- `signals.py`: PCSSignalGenerator with sinusoidal voltage/frequency drift, load-dependent temperature
- `state_machine.py`: PCSStateMachine with STANDBY/STARTING/RUNNING/STOPPING/FAULT states, power ramping at configurable rate
- `register_map.py`: YAML loader, sparse datablock builder, CallbackDataBlock for FC06 write dispatch
- `pty_pair.py`: socat PTY lifecycle manager for RTU virtual serial
- `simulator.py`: ModbusSimulator orchestrator with TCP/RTU server, 1Hz telemetry loop, zero-mode addressing
- `__main__.py`: CLI with --transport, --config, --tcp-port, --verbose
- Makefile `sim-modbus` target added

### Task 3: Test Suite
**Commit:** 2afd215

- 32 tests total (31 pass, 1 RTU skipped without socat)
- Register map: 8 tests (load, count, uniqueness, contiguous groups, defaults, metadata)
- Callback datablock: 4 tests (start/stop/setpoint/fault_reset dispatch)
- State machine: 8 tests (start/stop sequences, fault reset, power ramp positive/negative, setpoint ignored in standby, load factor)
- Signal generator: 4 tests (voltage/frequency range, temperature load response, single-phase zeros)
- Uint16 conversion: 4 tests (positive, negative signed, clamped high/low)
- TCP integration: 3 tests (roundtrip read, write triggers state, telemetry coherence)
- RTU integration: 1 test (socat PTY, @pytest.mark.rtu, skipped if no socat)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] pymodbus 3.12 API changes from documented 3.7 API**
- **Found during:** Task 2/3 (simulator init and test execution)
- **Issue:** pymodbus 3.12 renamed `ModbusSlaveContext` to `ModbusDeviceContext`, removed `zero_mode` parameter, renamed `slaves=` to `devices=` in `ModbusServerContext`, and made `client.close()` synchronous
- **Fix:** Created `_ZeroModeDeviceContext` subclass to restore zero-mode addressing; used `devices=` kwarg; changed all `await client.close()` to `client.close()`
- **Files modified:** `tools/simulators/modbus_sim/simulator.py`, `tests/test_modbus_simulator.py`
- **Commit:** 2afd215

## Verification

All verification commands pass:
1. Register map loads with 32 registers
2. Config validation passes (all 14 files)
3. Module imports work: `ModbusSimulator`, `PCSState`, `PCSStateMachine`
4. Modbus tests: 31 passed, 1 skipped (RTU/socat)
5. Full suite: 91 passed, 1 skipped
6. ruff check: all passed
7. ruff format: all formatted
