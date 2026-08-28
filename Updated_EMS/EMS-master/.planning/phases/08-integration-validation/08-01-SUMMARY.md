---
phase: 08-integration-validation
plan: 01
subsystem: simulators
tags: [fault-injection, signal-tuning, can, modbus, gpio, config-schema]
dependency_graph:
  requires: [05-01, 06-01, 07-01]
  provides: [fault-injection-config, signal-tuning-config]
  affects: [can_sim, modbus_sim, gpio_harness, config-schemas]
tech_stack:
  added: []
  patterns: [config-driven-faults, optional-schema-extension]
key_files:
  created: []
  modified:
    - config/schemas/bms_config.schema.json
    - config/schemas/pcs_config.schema.json
    - config/schemas/gpio_config.schema.json
    - tools/simulators/can_sim/simulator.py
    - tools/simulators/can_sim/rack.py
    - tools/simulators/can_sim/signals.py
    - tools/simulators/modbus_sim/simulator.py
    - tools/simulators/modbus_sim/register_map.py
    - tools/simulators/gpio_harness/rtdb_backend.py
    - tools/simulators/gpio_harness/backend.py
    - tools/simulators/gpio_harness/__main__.py
decisions:
  - "ExceptionResponse returned directly from CallbackDataBlock.getValues for fault registers"
  - "Bounce simulation uses daemon thread with 1ms toggle period"
  - "patternProperties used for gpio stuck_values to allow per-pin int mapping with additionalProperties: false"
metrics:
  duration: "~5 min"
  completed: "2026-03-13"
---

# Phase 8 Plan 1: Fault Injection and Signal Tuning Summary

YAML-configurable fault injection and signal tuning for CAN, Modbus, and GPIO simulators with backward-compatible schema extension.

## Changes Made

### Task 1: Schema Extensions (commit 44b8360)

Extended all 3 JSON schemas with optional fault_injection and signal_tuning properties:

- **bms_config.schema.json**: fault_injection (frame_drop_rate, corrupt_data, corrupt_rate, stale_timeout_ms, stale_rack_index) + signal_tuning (noise_sigma, drift_amplitude, drift_period_s, base_voltage)
- **pcs_config.schema.json**: fault_injection (response_timeout, timeout_duration_s, exception_code, exception_registers) + signal_tuning (ramp_rate_pct_per_s, startup_delay_s, voltage_noise)
- **gpio_config.schema.json**: fault_injection (stuck_pins, stuck_values, bounce_ms)

All 14 existing config files pass validation unchanged -- fully backward compatible.

### Task 2: Simulator Implementation (commit 8df4425)

**CAN Simulator (3 files):**
- `signals.py`: SignalGenerator accepts tuning dict; noise_sigma, drift_amplitude, drift_period_s, base_voltage replace hardcoded constants
- `rack.py`: RackSimulator accepts fault_cfg dict; frame_drop_rate drops frames probabilistically, corrupt_data garbles random bytes, stale_timeout_ms suppresses all frames for target rack until deadline
- `simulator.py`: CANSimulator extracts fault_injection and signal_tuning from bms_config YAML, passes to RackSimulator/SignalGenerator

**Modbus Simulator (2 files):**
- `register_map.py`: CallbackDataBlock accepts fault_cfg; getValues returns ExceptionResponse for configured exception_registers
- `simulator.py`: ModbusSimulator extracts fault_injection; response_timeout delays server startup via asyncio.sleep; voltage_noise adds Gaussian noise to AC voltage readings

**GPIO Harness (3 files):**
- `rtdb_backend.py`: RtdbBackend accepts fault_cfg; stuck_pins ignores writes, bounce_ms spawns daemon thread for rapid pin toggling
- `backend.py`: detect_backend passes fault_cfg through to RtdbBackend
- `__main__.py`: Reads fault_injection from gpio_config.yaml and passes to detect_backend

All fault configurations logged at INFO level on startup.

## Verification

- `uv run python tools/validate_config.py` -- All 14 configs pass
- `uv run pytest tests/test_can_simulator.py tests/test_modbus_simulator.py tests/test_gpio_harness.py -v` -- 67 passed, 1 skipped (RTU socat)

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **ExceptionResponse from getValues**: For Modbus fault injection, CallbackDataBlock.getValues returns a pymodbus ExceptionResponse object directly when a requested register is in the exception set. This is the cleanest approach for pymodbus 3.12.
2. **patternProperties for stuck_values**: Used JSON Schema patternProperties with `^[0-7]$` regex for the gpio stuck_values map, since additionalProperties: false is enforced everywhere.
3. **Daemon thread for bounce**: Contact bounce simulation uses a daemon thread to avoid blocking the caller, with 1ms toggle period matching real-world contact bounce timescales.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 44b8360 | Extend JSON schemas with fault_injection and signal_tuning |
| 2 | 8df4425 | Implement fault injection and signal tuning in all three simulators |
