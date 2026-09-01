---
phase: 02-configuration-schema
plan: 01
subsystem: config
tags: [jsonschema, yaml, pyyaml, draft-2020-12, json-schema, validation, modbus, can]

# Dependency graph
requires:
  - phase: 01-project-scaffold-build-system
    provides: pyproject.toml workspace root, Makefile, pr-check.yml CI, tests/ directory

provides:
  - tools/validate_config.py — validation CLI with Draft202012Validator, friendly errors, --config-dir
  - config/schemas/system_config.schema.json — topology range constraints (clusters/racks/modules/cells/temps)
  - config/schemas/bms_config.schema.json — CAN interface, bitrate enum, timing constraints
  - config/schemas/pcs_config.schema.json — if/then RTU/TCP conditional pattern
  - config/profiles/residential/ — 3 heavily-commented residential profile YAMLs
  - config/{system,bms,pcs}_config.yaml — active configs pre-populated with residential values
  - tests/test_config_validation.py — 12 pytest tests covering all PLAT-06 criteria
  - make validate target and CI pr-check.yml "Validate config files" step

affects: [02-02-configuration-schema, 03-rtdb-shm-layout, 05-can-simulator, 06-modbus-simulator, M1-config_manager]

# Tech tracking
tech-stack:
  added:
    - jsonschema>=4.23 (Draft202012Validator, iter_errors)
    - pyyaml>=6.0 (yaml.safe_load)
  patterns:
    - JSON Schema Draft 2020-12 with $schema/$id/$ref
    - additionalProperties: false at every object level (strict mode)
    - x-unit metadata on all numeric fields
    - if/then/else conditional schema for protocol-dependent required fields
    - validate_file() fail-fast pattern (break after first error per file)
    - --config-dir argument for profile directory validation

key-files:
  created:
    - tools/validate_config.py
    - config/schemas/system_config.schema.json
    - config/schemas/bms_config.schema.json
    - config/schemas/pcs_config.schema.json
    - config/profiles/residential/system_config.yaml
    - config/profiles/residential/bms_config.yaml
    - config/profiles/residential/pcs_config.yaml
    - config/system_config.yaml
    - config/bms_config.yaml
    - config/pcs_config.yaml
    - tests/test_config_validation.py
  modified:
    - pyproject.toml (added jsonschema>=4.23, pyyaml>=6.0 to dev group)
    - uv.lock (updated lockfile)
    - Makefile (added validate target to .PHONY and targets)
    - .github/workflows/pr-check.yml (added "Validate config files" step)

key-decisions:
  - "Renamed validate-config.py to validate_config.py (underscores) so tests can import functions directly via importlib without hacks — avoids hyphen import problem"
  - "validate_file() uses fail-fast break after first error per file — keeps error output signal:noise high for operators"
  - "Test suite tests validate_file() directly (not subprocess CLI) for exit-code tests to avoid tmp_path schema path resolution issues with fixed SCHEMAS_DIR constant"
  - "additionalProperties: false enforced at every object level in all 3 foundation schemas — strict mode is a locked decision"

patterns-established:
  - "Schema pattern: $schema 2020-12 + $id + additionalProperties: false + x-unit on all numerics"
  - "Profile pattern: config/profiles/{tier}/{config_name}.yaml with heavy inline comments (valid range, unit, purpose)"
  - "Active config pattern: config/{config_name}.yaml ships pre-populated with residential profile values"
  - "Conditional pattern: if/then/else in pcs_config connection for RTU vs TCP required fields"
  - "Test pattern: import validate_config.py via importlib.util.spec_from_file_location at module top-level"

requirements-completed: [PLAT-06]

# Metrics
duration: 6min
completed: 2026-02-26
---

# Phase 2 Plan 1: Configuration Schema Foundation Summary

**End-to-end JSON Schema validation pipeline: 3 Draft 2020-12 schemas (system/bms/pcs) with topology range enforcement, if/then RTU/TCP conditional, residential YAML profiles, validate_config.py CLI, make validate, CI gate, and 12-test pytest suite.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-26T12:14:28Z
- **Completed:** 2026-02-26T12:20:00Z
- **Tasks:** 3
- **Files modified:** 15

## Accomplishments

- Full validation pipeline from YAML files through JSON Schema to pytest tests, Makefile target, and CI gate
- 3 foundation JSON Schemas with all required patterns: topology range constraints (1-8 clusters, 1-16 racks, 3-20 modules, 16-108 cells, 4-40 temps), if/then RTU/TCP conditional for PCS, additionalProperties: false at all object levels, x-unit metadata on all numeric fields
- 12 pytest tests pass: schema existence, draft 2020-12 parseability, valid/invalid topology, RTU/TCP conditional, additionalProperties rejection, friendly error format, validate_file exit behavior

## Task Commits

Each task was committed atomically:

1. **Task 1: Validation infrastructure — deps, script, Makefile, CI** - `3b6240c` (feat)
2. **Task 2: Foundation schemas — system_config, bms_config, pcs_config + residential profiles** - `2b68167` (feat)
3. **Task 3: Test suite for config validation** - `e63c7d1` (feat)

**Plan metadata:** *(final docs commit — see below)*

## Files Created/Modified

- `tools/validate_config.py` — Validation CLI: loads YAML via yaml.safe_load, validates with Draft202012Validator, prints friendly "ERROR: In {file}, field '{path}' is set to {value} but {message}" prose, fail-fast per file, --config-dir for profile dirs, exit 0/1
- `config/schemas/system_config.schema.json` — System/topology schema: 5 topology fields range-constrained, additionalProperties: false, x-unit on all
- `config/schemas/bms_config.schema.json` — BMS CAN schema: bitrate enum [250k/500k/1M], timing ms bounds, additionalProperties: false
- `config/schemas/pcs_config.schema.json` — PCS Modbus schema: if/then RTU requires device+baud_rate+parity, else TCP requires host+port
- `config/profiles/residential/system_config.yaml` — Residential: 1 cluster, 4 racks, 8 modules/rack, 16 cells, 8 temps; heavily commented
- `config/profiles/residential/bms_config.yaml` — vcan0, 500kbps, DBC path, 300/2000/5000ms timing
- `config/profiles/residential/pcs_config.yaml` — RTU /dev/ttyUSB0 9600N1, 0x500E/0x0291 registers, 500ms poll
- `config/{system,bms,pcs}_config.yaml` — Active configs pre-populated with residential profile values
- `tests/test_config_validation.py` — 12 tests: 4 existence/validity, 4 constraint tests, 2 CLI behavior, 1 format, 1 additionalProperties
- `pyproject.toml` — Added jsonschema>=4.23, pyyaml>=6.0 to dev group
- `Makefile` — Added validate to .PHONY, added `uv run python tools/validate_config.py` target
- `.github/workflows/pr-check.yml` — Added "Validate config files" step after Python deps install, before pytest

## Decisions Made

- **Renamed validate-config.py to validate_config.py**: Hyphens in Python filenames prevent direct import. Underscore filename allows `importlib.util.spec_from_file_location` cleanly. Updated Makefile and CI to match.
- **Fail-fast per file in validate_file()**: Only first error per file reported (break after first). Reduces noise for operators — one clear error rather than cascading secondary errors from the same root cause.
- **Test via module import, not subprocess**: Tests 11/12 import validate_file directly rather than running subprocess, because the fixed SCHEMAS_DIR constant ("config/schemas") resolves relative to CWD, not tmp_path. Direct import gives full coverage without sys.path hacks.

## Deviations from Plan

None — plan executed exactly as written.

Note: The plan mentioned renaming the script from `validate-config.py` to `validate_config.py` as an option within Task 3. Since I created `validate_config.py` with underscores from the start in Task 1 (correct choice per the plan's guidance), there was no rename needed — the script was created with the correct name immediately.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Foundation validation pipeline complete: schema + YAML + script + Makefile + CI + tests
- Plan 02-02 can follow this exact pattern for the remaining 11 schemas (btms, meter, dg, pv, control, alarms, schedule, cloud, network, gpio, hmi) without rework risk
- Residential profile directory structure and comment convention established — Plan 02-02 fills in the remaining profile YAMLs
- CI gate will enforce config validity on every PR once all schemas exist (currently exits 1 for missing schemas; will exit 0 once 02-02 completes)

---
*Phase: 02-configuration-schema*
*Completed: 2026-02-26*

## Self-Check: PASSED

All key files verified present:
- tools/validate_config.py
- config/schemas/system_config.schema.json
- config/schemas/bms_config.schema.json
- config/schemas/pcs_config.schema.json
- config/profiles/residential/system_config.yaml
- tests/test_config_validation.py
- .planning/phases/02-configuration-schema/02-01-SUMMARY.md

All task commits verified in git log:
- 3b6240c feat(02-01): add config validation infrastructure
- 2b68167 feat(02-01): add foundation schemas, residential profiles, active configs
- e63c7d1 feat(02-01): add comprehensive pytest suite for config validation
