---
phase: 02-configuration-schema
plan: 02
subsystem: config
tags: [jsonschema, yaml, draft-2020-12, json-schema, validation, modbus, hot-reload, gpio, mqtt, hmi]

# Dependency graph
requires:
  - phase: 02-configuration-schema
    plan: 01
    provides: validate_config.py, 3 foundation schemas (system/bms/pcs), residential profile (3 files), 12 pytest tests

provides:
  - config/schemas/{btms,meter,dg,pv,control,alarms,schedule,cloud,network,gpio,hmi}_config.schema.json — 11 new schemas
  - config/profiles/residential/ — complete 14-file residential profile
  - config/profiles/commercial/ — 14-file commercial profile (2 clusters, 8 racks, 500 kWh)
  - config/profiles/container/ — 14-file container profile (4 clusters, 16 racks, 6+ MWh)
  - config/{btms,meter,dg,pv,control,alarms,schedule,cloud,network,gpio,hmi}_config.yaml — 11 active configs
  - tests/test_config_validation.py — 23 tests (9 new) covering all PLAT-06 criteria

affects: [03-rtdb-shm-layout, 05-can-simulator, 06-modbus-simulator, M1-config_manager, M1-alarm_manager, M1-control_manager]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Hot-reload schema pattern: x-hot-reload: true at root + x-mutable annotations on setpoints
    - GPIO safety pattern: DI-0..DI-7 / DO-0..DO-7 as named object properties (not arrays)
    - Modbus device schema pattern: if/then RTU/TCP conditional (shared across btms/meter/dg/pv)
    - Alarm rule pattern: named fixed properties (not patternProperties) for 9 well-known alarms
    - Power curve pattern: 96-point array (minItems/maxItems: 96) for 15-minute schedule
    - Recursive schema walker pattern: walk_schema_objects() + walk_schema_properties() for test enforcement

key-files:
  created:
    - config/schemas/btms_config.schema.json
    - config/schemas/meter_config.schema.json
    - config/schemas/dg_config.schema.json
    - config/schemas/pv_config.schema.json
    - config/schemas/control_config.schema.json
    - config/schemas/alarms_config.schema.json
    - config/schemas/schedule_config.schema.json
    - config/schemas/cloud_config.schema.json
    - config/schemas/network_config.schema.json
    - config/schemas/gpio_config.schema.json
    - config/schemas/hmi_config.schema.json
    - config/profiles/residential/btms_config.yaml
    - config/profiles/residential/meter_config.yaml
    - config/profiles/residential/dg_config.yaml
    - config/profiles/residential/pv_config.yaml
    - config/profiles/residential/control_config.yaml
    - config/profiles/residential/alarms_config.yaml
    - config/profiles/residential/schedule_config.yaml
    - config/profiles/residential/cloud_config.yaml
    - config/profiles/residential/network_config.yaml
    - config/profiles/residential/gpio_config.yaml
    - config/profiles/residential/hmi_config.yaml
    - config/profiles/commercial/ (14 files)
    - config/profiles/container/ (14 files)
    - config/btms_config.yaml
    - config/meter_config.yaml
    - config/dg_config.yaml
    - config/pv_config.yaml
    - config/control_config.yaml
    - config/alarms_config.yaml
    - config/schedule_config.yaml
    - config/cloud_config.yaml
    - config/network_config.yaml
    - config/gpio_config.yaml
    - config/hmi_config.yaml
  modified:
    - config/schemas/pcs_config.schema.json (add x-unit to slave_id, stopbits, port)
    - config/schemas/btms_config.schema.json (add x-unit to slave_id, stopbits)
    - config/schemas/meter_config.schema.json (add x-unit to slave_id, stopbits)
    - config/schemas/dg_config.schema.json (add x-unit to slave_id, stopbits)
    - config/schemas/pv_config.schema.json (add x-unit to slave_id, stopbits)
    - tests/test_config_validation.py (added 9 new tests + 2 helper functions)

key-decisions:
  - "alarms_config uses fixed named properties (not patternProperties) for 9 alarm rules — avoids complexity, covers all key BESS alarms, additionalProperties: false works cleanly"
  - "power_curve in schedule_config enforces minItems: 96, maxItems: 96 — 15-minute interval coverage validated by schema"
  - "gpio_config uses named properties DI-0..DI-7, DO-0..DO-7 — strict enum on pin keys, additionalProperties: false prevents mis-configuration"
  - "Commercial uses liquid BTMS and CT ratio 1000, container uses CT ratio 6000 — realistic scaling for each tier"

# Metrics
duration: 12min
completed: 2026-02-26
---

# Phase 2 Plan 2: Configuration Schema Completion Summary

**Complete 14-file JSON Schema system with 11 new schemas (Modbus devices, hot-reloadable app configs, infrastructure), full 3-profile YAML library (residential/commercial/container), populated active configs, and 23-test pytest suite enforcing all schema policies.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-02-26T12:23:14Z
- **Completed:** 2026-02-26T12:35:44Z
- **Tasks:** 3
- **Files modified:** 75+

## Accomplishments

- All 14 JSON Schema files now exist — complete coverage for every EMS config file
- 3 hot-reloadable schemas (control, alarms, schedule) with `x-hot-reload: true` and `x-mutable` annotations on all runtime-adjustable setpoints
- 4 Modbus device schemas (btms, meter, dg, pv) using the if/then RTU/TCP conditional pattern from pcs_config
- GPIO safety schema with exact DI-0..DI-7 / DO-0..DO-7 named property structure matching hardware pin map
- All 3 deployment profiles (residential 50 kWh, commercial 500 kWh, container 6+ MWh) fully populated with 14 validated YAML files each
- make validate exits 0: `OK: All 14 config files are valid.`
- 23 pytest tests pass — includes recursive schema walkers that enforce x-unit on all numeric fields and additionalProperties: false on all nested objects

## Task Commits

Each task was committed atomically:

1. **Task 1: 11 JSON schemas (Modbus device + app + infrastructure)** - `65f6a17` (feat)
2. **Task 2: All YAML profiles — residential, commercial, container + active configs** - `2487150` (feat)
3. **Task 3: Extended test suite + schema deviation fixes** - `4f386b3` (feat)

**Plan metadata:** *(final docs commit — see below)*

## Files Created/Modified

- `config/schemas/btms_config.schema.json` — BTMS Modbus RTU/TCP schema with device mode (liquid/air) and temperature setpoint
- `config/schemas/meter_config.schema.json` — Energy meter schema with model enum (acrel/schneider/abb/generic) and CT ratio
- `config/schemas/dg_config.schema.json` — Generator schema with rated power, fuel type, auto-start
- `config/schemas/pv_config.schema.json` — PV inverter schema with rated power and MPPT count (1-12)
- `config/schemas/control_config.schema.json` — Hot-reloadable control schema: SOC limits, power limits, source priority arrays, state machine timing
- `config/schemas/alarms_config.schema.json` — Hot-reloadable alarms schema: IEC 62682 severity levels, 9 named alarm rules with threshold/hysteresis/delay
- `config/schemas/schedule_config.schema.json` — Hot-reloadable schedule schema: mode, time windows, day/night, 96-point power curve
- `config/schemas/cloud_config.schema.json` — MQTT/TLS broker, mTLS auth, telemetry interval, offline buffer
- `config/schemas/network_config.schema.json` — eth0/eth1 with static/dhcp if/then conditional
- `config/schemas/gpio_config.schema.json` — DI-0..DI-7 inputs and DO-0..DO-7 outputs with debounce and initial_state
- `config/schemas/hmi_config.schema.json` — HTTP/WebSocket server, bcrypt PIN hashes, display settings
- 11 residential profile YAMLs, 14 commercial profile YAMLs, 14 container profile YAMLs
- 11 active config YAMLs (copied from residential — ships pre-populated)
- `tests/test_config_validation.py` — Extended with 9 tests and 2 recursive helpers

## Decisions Made

- **Fixed named alarm properties vs. patternProperties**: Used 9 fixed alarm rule properties (cell_voltage_high, cell_voltage_low, etc.) instead of patternProperties. This keeps `additionalProperties: false` clean, avoids regex complexity, and covers all key BESS alarms defined in requirements.
- **power_curve enforces exactly 96 points**: `minItems: 96, maxItems: 96` validates that the full 24-hour schedule is provided when mode='curve'. No partial curves allowed.
- **GPIO uses object property names (not enum strings)**: DI-0..DI-7 as property keys (not a free-form string field) means the schema can validate individual pin configs with `additionalProperties: false`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Missing x-unit on slave_id, stopbits, port in 5 Modbus schemas**

- **Found during:** Task 3 test execution (test_x_unit_on_numeric_fields caught this)
- **Issue:** The locked decision "x-unit on all numeric fields" was not fully applied in the connection section of pcs_config (from 02-01) and the 4 new Modbus device schemas. `slave_id`, `stopbits`, and `port` (in pcs_config only) lacked x-unit annotations.
- **Fix:** Added `"x-unit": "id"` to slave_id fields, `"x-unit": "bits"` to stopbits fields, and `"x-unit": "port"` to the pcs_config TCP port field. All 5 affected schemas updated.
- **Files modified:** config/schemas/pcs_config.schema.json, btms_config.schema.json, meter_config.schema.json, dg_config.schema.json, pv_config.schema.json
- **Commit:** `4f386b3` (included with Task 3)

## Issues Encountered

None beyond the auto-fixed deviation above.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- PLAT-06 fully satisfied: 14 schemas, 14 active configs, 3 profiles, 23 tests, make validate and CI gate all green
- All config files for all 14 EMS modules are now specified and validated
- RTDB Phase 3 can reference these schema files to understand data shapes for each module
- config_manager module (M1) has complete schema coverage to implement hot-reload for control/alarms/schedule
- Alarm thresholds, power limits, and GPIO pin assignments are documented and validated — ready for safety_manager and alarm_manager implementation

---
*Phase: 02-configuration-schema*
*Completed: 2026-02-26*

## Self-Check: PASSED

All key files verified present:
- config/schemas/btms_config.schema.json (FOUND)
- config/schemas/control_config.schema.json (FOUND)
- config/schemas/alarms_config.schema.json (FOUND)
- config/schemas/schedule_config.schema.json (FOUND)
- config/schemas/gpio_config.schema.json (FOUND)
- config/profiles/commercial/system_config.yaml (FOUND)
- config/profiles/container/system_config.yaml (FOUND)
- config/profiles/residential/gpio_config.yaml (FOUND)
- .planning/phases/02-configuration-schema/02-02-SUMMARY.md (FOUND)

All task commits verified in git log:
- 65f6a17 feat(02-02): add 11 JSON schemas for all remaining config files
- 2487150 feat(02-02): add all YAML profiles for residential, commercial, container + active configs
- 4f386b3 feat(02-02): extend test suite for full 14-file and 3-profile validation
