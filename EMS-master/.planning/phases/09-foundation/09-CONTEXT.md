# Phase 9: Foundation - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Config validation/serving and RTDB shared memory lifecycle. All modules can load validated configuration and read/write RTDB shared memory at startup. Covers CONF-01 through CONF-08 and DATA-01 through DATA-08.

</domain>

<decisions>
## Implementation Decisions

### Config Startup Behavior
- Fail-fast on any schema validation error — entire EMS refuses to start if any config file fails validation
- Device configs (dg_config, pv_config, btms_config, meter_config) are optional if that hardware is not declared in system_config topology; core configs (system_config, gpio_config, control_config, alarms_config, schedule_config, bms_config, pcs_config, network_config, cloud_config, hmi_config) must always be present
- system_config.yaml declares which subsystems are present (e.g., `has_dg`, `has_pv`); config_manager only loads device configs for declared subsystems
- All 14 YAML files validated against JSON Schema at startup — no partial startup, no degraded mode

### RTDB Ownership and Lifecycle
- Graceful shutdown: data_manager explicitly unlinks shm via systemd ExecStop
- Startup safety net: detect stale shm (check magic/version), destroy and recreate if found
- Topology change (system_config edit) requires full EMS restart — config_manager rejects hot-reload for system_config
- RTDB zero-filled on creation (memset 0); readers treat zero values + stale `last_update_ms` as "no data received yet"

### Hot-Reload Semantics
- `config_reload` ZMQ event includes the full validated config (not a diff); diff logged separately for audit per CONF-08
- Consumers trust config_manager's validation — no re-validation on receive, normal defensive coding only
- Failed hot-reload validation: reject the change, keep current running config, publish detailed error event with field path, expected vs actual, and reason — not silent
- inotify debounce: 500ms after last IN_CLOSE_WRITE before validate-and-apply

### Config Query API
- Minimal surface: `get_config(name)` and `get_value(name, path)` only — no `list_configs()`, no `get_schema()`, no remote validate
- Missing path in `get_value()` returns error response with descriptive message, not null
- All queries served from in-memory cache loaded at startup, updated atomically on hot-reload — no disk re-reads per request
- Remote validation not needed — CLI tool `ems-config validate` covers field service use (CONF-07)

### Claude's Discretion
- Profile overlay merge strategy (full replacement vs deep merge)
- RTDB ownership model (long-running C process, hybrid C+Python, or other architecture)
- data_manager internal architecture for ZMQ telemetry publishing and health monitoring

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `config/schemas/` — All 14 JSON Schema files (Draft 2020-12) with `additionalProperties: false`, `x-unit` annotations, conditional logic
- `config/` — 14 active YAML config files, pre-populated with residential defaults
- `config/profiles/` — Three deployment profiles (residential, commercial, container) with all 14 files each
- `tools/validate_config.py` — Existing validator with `validate_file(name, config_dir)` and friendly error formatting
- `src/common/c/include/rtdb.h` — Complete RTDB struct definition (~1.8 MB max, seqlock per rack + per section)
- `src/common/c/include/seqlock.h` — Lock-free seqlock primitive with acquire/release semantics
- `src/common/python/src/ems_common/rtdb.py` — Full ctypes mirror of C RTDB struct
- `src/common/python/src/ems_common/ipc.py` — IPC socket paths, topic constants, encode/decode helpers (msgpack)
- `src/common/c/include/ipc_defs.h` — C-side IPC definitions matching Python

### Established Patterns
- JSON Schema validation via `jsonschema` library with Draft 2020-12
- ctypes struct mirroring for C/Python interop (EmsRtdb matches ems_rtdb_t exactly)
- MessagePack envelope format: `{ts, seq, src, topic, payload}` for telemetry
- Single-writer-per-section enforced by convention for RTDB seqlock
- uv workspace with 12 Python packages; shared types via `ems_common`

### Integration Points
- `src/config_manager/` — Stub package ready for implementation
- `src/data_manager/python/` — Stub package ready for implementation
- `src/data_manager/c/src/main.c` — Skeleton C executable ready for RTDB shm creation
- systemd service files expect `After=ems-data-manager.service` ordering
- ZMQ sockets: `ipc:///run/ems/telemetry.sock` (PUB), `ipc:///run/ems/logger.sock` (PULL)
- Tests: 21 config tests, 9 RTDB tests, IPC contract tests — all passing

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Key constraint is that this is embedded (ECU-1170-552A, 64GB eMMC, 4GB DDR4) so solutions should be lightweight.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 09-foundation*
*Context gathered: 2026-03-13*
