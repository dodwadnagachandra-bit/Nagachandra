# Phase 4: IPC Contracts - Context

**Gathered:** 2026-03-05
**Status:** Ready for planning

<domain>
## Phase Boundary

ZeroMQ topic definitions, MessagePack payload schemas, socket path constants, and C/Python interop validation tests. Contracts only — no runtime IPC code, no module integration, no ZeroMQ socket lifecycle. Modules consume these contracts starting in M1.

Output: topic registry, payload schema definitions, socket path constants (C header + Python module), interop test (mpack ↔ msgpack roundtrip).

</domain>

<decisions>
## Implementation Decisions

### Topic naming convention
- Dotted hierarchy style: `bms.rack`, `pcs`, `gpio.di`, `alarm.new`, `control.state`
- Per-section granularity (not per-device indexed) — one topic per RTDB section
- Subscribers filter internally if they only need a subset (e.g., one rack out of 16)
- Prefix matching for category subscription: subscribe to `bms.` gets all BMS topics
- Rationale: consistent with MQTT/SCADA conventions, readable in debug tools

### REQ/REP command pattern
- REQ/REP is point-to-point — no topic filtering needed (ZeroMQ REQ/REP doesn't support it)
- Command type is encoded in the payload, not in a topic prefix
- Each target module has its own REP socket — commands are routed by connecting to the correct socket

### PUSH/PULL logger events
- Typed event prefix on each message: `alarm`, `state_change`, `comm_fault`, `config_reload`
- Logger can categorize events without unpacking the full payload
- All modules PUSH to a single logger PULL socket

### Message payload — PUB/SUB telemetry
- Full section snapshots — each message contains the complete RTDB section (e.g., all rack data with aggregates)
- Idempotent — subscribers always have full state, no missed-update problem
- At 1 Hz and ~14 KB per rack, bandwidth is trivial on ipc:// (local Unix domain socket)
- No delta/change-only messages — simplicity over optimization

### Message envelope — standard header
- Every message includes: `{timestamp_ms: uint64, seq: uint32, source: string}`
- `timestamp_ms`: CLOCK_MONOTONIC milliseconds (matches RTDB last_update_ms)
- `seq`: per-publisher sequence counter (monotonic, wraps at uint32 max)
- `source`: publisher module name string (e.g., "data_manager", "control_manager")
- Enables staleness detection, gap detection, and debug correlation

### REQ/REP command payload format
- Request: `{action: string, params: {key: value, ...}}`
- Response: `{status: "ok"|"error", result: {...}, error_msg: string|null}`
- Action names are snake_case verbs: `set_mode`, `set_setpoint`, `start_pcs`, `ack_alarm`
- Generic, extensible — new commands add new action strings, no schema changes

### PUSH/PULL event payload format
- Structured event: `{timestamp_ms: uint64, source: string, severity: string, event_type: string, message: string, data: {...}}`
- Severity levels: `info`, `warning`, `error`, `critical` (maps to IEC 62682 tiers)
- event_type matches the PUSH prefix: `alarm`, `state_change`, `comm_fault`, `config_reload`
- data dict carries event-specific payload (e.g., alarm details, old/new state values)
- Maps directly to JSONL log format for logger persistence

### Socket topology
- Hybrid layout: central PUB/SUB bus + per-module REP sockets + single PUSH/PULL for logger
- PUB/SUB: data_manager binds PUB socket, all consumers connect as SUB (single fan-out point)
- REQ/REP: each command-accepting module binds its own REP socket (control_manager, alarm_manager, etc.)
- PUSH/PULL: logger binds PULL socket, all modules connect as PUSH
- data_manager is the natural RTDB owner — it creates shm, reads seqlock changes, publishes updates

### Socket paths
- Base directory: `/run/ems/` (standard Linux runtime dir, cleaned on reboot)
- Naming: `ipc:///run/ems/{purpose}.sock`
- Defined sockets:
  - `ipc:///run/ems/telemetry.sock` — PUB/SUB bus (data_manager binds PUB)
  - `ipc:///run/ems/control_cmd.sock` — REQ/REP (control_manager binds REP)
  - `ipc:///run/ems/alarm_cmd.sock` — REQ/REP (alarm_manager binds REP)
  - `ipc:///run/ems/logger.sock` — PUSH/PULL (logger binds PULL)
- Additional REP sockets can be added for hmi_server, cloud_manager as needed in M3/M4
- systemd tmpfiles.d or service ExecStartPre creates /run/ems/ directory

### Path enforcement — shared constants
- C header: `src/common/c/include/ipc_defs.h` — socket path defines, topic string defines
- Python module: `src/common/python/src/ems_common/ipc.py` — same constants as Python strings
- All modules import from these — no magic strings, single source of truth
- Pattern matches rtdb.h/rtdb.py approach from Phase 3

### Claude's Discretion
- Exact MessagePack field encoding order (map vs array, key naming style)
- Whether to use msgpack ext types for timestamps or plain uint64
- Test framework for C interop (mpack encode → msgpack decode roundtrip)
- Whether topic strings are defined as #define macros or const char* in C
- How to structure the topic registry documentation (markdown table, YAML, or code comments)
- Whether the interop test uses subprocess (compile C, run, pipe to Python) or shared buffer

</decisions>

<specifics>
## Specific Ideas

- The topic registry should feel like a protocol spec — any developer can look at it and know exactly what messages flow between modules
- Socket paths in shared constants mean grep for `EMS_SOCK_TELEMETRY` finds every module that uses the telemetry bus
- The interop test is the most important artifact — it proves C mpack and Python msgpack agree on encoding/decoding before any module integration
- Envelope fields (timestamp_ms, seq, source) are cheap insurance for debugging production IPC issues
- Full section snapshots simplify subscriber code: no state accumulation, no "what if I missed an update" edge cases

</specifics>

<code_context>
## Codebase Integration Points

### Existing files to modify
- `src/common/c/include/ems_types.h` — may need additional enums for IPC message types or severity levels
- `src/common/python/src/ems_common/__init__.py` — add ipc module import
- `CMakeLists.txt` — may need mpack dependency for C interop test
- `Makefile` — test target already wired (ctest + pytest)

### New files
- `src/common/c/include/ipc_defs.h` — socket paths, topic strings, message type constants
- `src/common/python/src/ems_common/ipc.py` — Python mirror of ipc_defs.h constants + MessagePack schema helpers
- `tests/c/test_ipc_interop.c` — C-side mpack encode/decode test
- `tests/test_ipc_contracts.py` — Python msgpack roundtrip + C↔Python interop validation

### Dependencies to add
- `mpack` (C library) — for MessagePack encode/decode in C interop test
- `msgpack` (Python) — for MessagePack encode/decode in Python tests
- These are test-time dependencies for Phase 4; runtime use begins in M1

### Downstream consumers (future phases, not modified now)
- `src/data_manager/` — will bind PUB socket, publish RTDB snapshots (M1)
- `src/comm_manager/` — will subscribe to nothing, pushes events to logger (M1)
- `src/control_manager/` — will subscribe to telemetry, bind REP for commands (M2)
- `src/alarm_manager/` — will subscribe to telemetry, bind REP for commands (M2)
- `src/logger/` — will bind PULL socket, receive events from all modules (M1)
- `src/hmi_server/` — will subscribe to telemetry, send commands via REQ (M3)
- `src/cloud_manager/` — will subscribe to telemetry, forward to MQTT (M4)

### Related artifacts
- `src/common/c/include/rtdb.h` — RTDB struct definitions (payload content for telemetry messages)
- `src/common/python/src/ems_common/rtdb.py` — Python ctypes mirror (used to construct test payloads)
- `config/schemas/system_config.schema.json` — topology dimensions affect message sizes

</code_context>

<deferred>
## Deferred Ideas

- Runtime ZeroMQ socket lifecycle (bind, connect, reconnect, cleanup) — M1 data_manager
- ZeroMQ high-water mark tuning — M1 when real message volumes are measured
- RTDB change detection (seqlock polling) to trigger PUB messages — M1 data_manager
- Command authorization/authentication on REQ/REP — M3 HMI or M4 cloud
- Message compression for cloud_manager MQTT relay — M4
- IPC health monitoring (heartbeat topic, dead module detection) — M2 or diagnostics phase
- Socket path configuration via YAML (currently hardcoded constants) — only if deployment requires it

</deferred>

---

*Phase: 04-ipc-contracts*
*Context gathered: 2026-03-05*
