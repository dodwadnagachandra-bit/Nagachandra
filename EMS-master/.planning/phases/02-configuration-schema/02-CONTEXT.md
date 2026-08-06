# Phase 2: Configuration Schema - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Define JSON Schema for all 14 YAML config files and a validation command that rejects invalid configs before any module consumes them. Schemas only — runtime hot-reload logic belongs in config_manager (M1). Example config profiles ship with the repo.

</domain>

<decisions>
## Implementation Decisions

### Schema Strictness
- `additionalProperties: false` on all schemas — reject unknown fields, catch typos immediately
- Full range enforcement on topology fields: clusters 1–8, racks 1–16, modules 3–20, cells 16–108, temps_per_module 4–40
- JSON Schema conditionals (if/then/else or oneOf) for cross-field dependencies (e.g., protocol 'rtu' requires baud/parity, 'tcp' requires host/port)
- Strict enums on all fields with known valid values (protocols, severities, modes, states)

### Validation UX
- `make validate` as the primary invocation — consistent with Phase 1 Makefile convention
- Fail-fast per file — stop at first error in each config file, report, move to next file
- Friendly prose error messages: "ERROR: In system_config.yaml, the field 'racks_per_cluster' in cluster 0 is set to 25, but must be between 1 and 16."
- Added to pr-check.yml as a CI gate — invalid config changes block merge

### Config File Defaults
- Three profiles: config/profiles/residential/, config/profiles/commercial/, config/profiles/container/ matching the 3 scale tiers
- config/ is the active directory (modules consume from here), profiles are reference templates — developers copy a profile to config/ to activate
- config/ ships pre-populated with residential profile — make build and make test work out of the box with zero setup
- Heavily commented YAML — each field has inline comments explaining purpose, valid range, and unit

### Hot-Reload & Schema Metadata
- Schemas only in this phase — runtime file-watch + re-validate + notify logic deferred to config_manager (M1)
- `x-hot-reload: true` metadata on the 3 hot-reloadable schemas (control_config, alarms_config, schedule_config) — config_manager can discover reload capability from schema
- `x-mutable: true/false` on individual fields within hot-reloadable configs — marks which fields can change during runtime reload vs require restart
- `x-unit` metadata on all numeric fields (e.g., "V", "°C", "kW", "ms", "%") — self-documenting schemas, HMI can auto-generate labels from metadata

### Claude's Discretion
- JSON Schema draft version (2020-12 vs draft-07)
- Python validation library choice (jsonschema, pydantic, or custom)
- Schema file naming convention and directory layout
- Which fields in hot-reloadable configs get x-mutable: true vs false (use domain judgment — thresholds/setpoints are mutable, structural fields are not)

</decisions>

<specifics>
## Specific Ideas

- Error messages should be readable by someone who doesn't know JSON Schema — "field X is set to Y but must be between A and B" not "schema validation failed: maxValue constraint"
- The 14 config files are listed in requirements Section 3 (system, bms, pcs, devices, control, alarms, schedule, cloud, gpio, hmi + 4 more TBD from architecture)
- Topology constraints come from requirements Section 1.1 scale table

</specifics>

<deferred>
## Deferred Ideas

- Runtime hot-reload pipeline (inotify watch + ZMQ notify) — config_manager in M1
- Config versioning / migration between schema versions — future phase
- HMI config editor UI — M3 hmi_server phase

</deferred>

---

*Phase: 02-configuration-schema*
*Context gathered: 2026-02-26*
