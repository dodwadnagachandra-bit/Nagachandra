# Phase 14: Control State Machine - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning

<domain>
## Phase Boundary

1Hz control loop with state transitions, RTDB reads/writes, PCS command dispatch, and ZMQ command API. Covers CTRL-01, CTRL-02, CTRL-03, CTRL-07, CTRL-10, CTRL-12. Pure Python module (no C hot path needed at 1Hz).

</domain>

<decisions>
## Implementation Decisions

### State Transition Rules

| Transition | Valid? | Via | Notes |
|------------|--------|-----|-------|
| INIT → IDLE | Yes | Startup complete, PCS OFF confirmed | Automatic on boot |
| IDLE → STANDBY | Yes | PCS on/off sequence (10s wait) | Triggered by schedule or command |
| STANDBY → CHARGING | Yes | Set positive power setpoint | Immediate (PCS already ON) |
| STANDBY → DISCHARGING | Yes | Set negative power setpoint | Immediate |
| CHARGING → STANDBY | Yes | Ramp to zero | Required before direction change |
| DISCHARGING → STANDBY | Yes | Ramp to zero | Required before direction change |
| CHARGING → DISCHARGING | **No** | Must pass through STANDBY | Prevents polarity reversal transient |
| DISCHARGING → CHARGING | **No** | Must pass through STANDBY | Same reason |
| STANDBY → IDLE | Yes | PCS off sequence (10s wait) | When no longer needed |
| Any → FAULT | Yes | PCS fault word non-zero | Auto-retry or manual reset |
| FAULT → IDLE | Yes | Fault cleared (retry or manual) | PCS returns to OFF after fault reset |
| Any → EMERGENCY | Yes | Safety outputs asserted in RTDB | Control_manager stops all commands |
| EMERGENCY → IDLE | Yes | Safety latch cleared (safety_reset) | Control_manager monitors RTDB gpio |
| Any → MAINTENANCE | Yes | ZMQ maintenance_enter command | Operator lockout |
| MAINTENANCE → IDLE | Yes | ZMQ maintenance_exit command | Only way out |

#### State Definitions

| State | PCS Power | PCS On/Off | When |
|-------|-----------|------------|------|
| INIT | Off | OFF | Boot — reading config, attaching RTDB |
| IDLE | Off | OFF (0x0291=0) | System powered but not dispatching |
| STANDBY | Zero | ON (0x0291=1) | PCS energized, ready to charge/discharge |
| CHARGING | Positive | ON | Actively charging battery |
| DISCHARGING | Negative | ON | Actively discharging battery |
| FAULT | Zero | Faulted | PCS reported fault — auto-retry or manual reset |
| EMERGENCY | N/A | Stopped via GPIO | Safety_manager owns response — control_manager hands off |
| MAINTENANCE | Off | OFF | Operator lockout — no automatic dispatch |

Key rules:
- CHARGING → DISCHARGING is forbidden — must pass through STANDBY to prevent DC bus transients
- IDLE vs STANDBY: IDLE = PCS OFF (saves energy), STANDBY = PCS ON at zero power (instant dispatch)
- FAULT = PCS-reported fault (recoverable), EMERGENCY = safety_manager triggered (not recoverable by control)
- MAINTENANCE persists across restarts (written to RTDB), only cleared by explicit ZMQ command
- Control_manager detects EMERGENCY by reading RTDB gpio section (safety outputs asserted)

### PCS Command Path Mechanics

| Aspect | Decision |
|--------|----------|
| Setpoint path | control_manager → RTDB `system.active_setpoint_kw` → comm_manager → PCS register 0x500E |
| Command path | control_manager → RTDB `system.pcs_command` + `pcs_command_seq` → comm_manager → PCS registers 0x0291/0x5064 |
| Command dedup | Monotonic `pcs_command_seq` counter — comm_manager only acts on increment |
| RTDB struct change | Add `pcs_command` (uint8), `pcs_command_seq` (uint32), `active_derating_pct` (float) to `ems_system_t` |
| Comm_manager changes | Add `write_setpoint()` and `process_command()` to PcsDevice, called in existing poll loop |
| Crash safety | Stale setpoint in RTDB is safe — comm_manager keeps sending last known value |
| Single-writer rule | Preserved — control_manager writes system section, comm_manager writes PCS Modbus |

Key rules:
- PCS commands go through RTDB, not direct Modbus — preserves single-writer-per-section
- Comm_manager reads `active_setpoint_kw` on each 500ms poll and writes register 0x500E
- Comm_manager reads `pcs_command`/`pcs_command_seq` — only executes when seq increments
- New RTDB fields: `pcs_command` (uint8 enum: NONE=0, ON=1, OFF=2, FAULT_RESET=3), `pcs_command_seq` (uint32), `active_derating_pct` (float)
- RTDB struct update requires C (`rtdb.h`) and Python (`rtdb.py`) ctypes mirror change at start of M2

### PCS On/Off Sequencing

| Aspect | Decision |
|--------|----------|
| Timer approach | Non-blocking — record timestamp, check elapsed on each 1Hz tick |
| Sub-states | Internal only (STARTING, STOPPING) — not visible in RTDB or ZMQ |
| PCS ON sequence | Write pcs_command=ON → wait 10s → verify PCS state==RUNNING → transition to STANDBY |
| PCS OFF sequence | Ramp setpoint to zero → confirm near-zero → write pcs_command=OFF → wait 10s → verify PCS state==OFF → transition to IDLE |
| Fault during wait | Immediate → FAULT, cancel pending timer |
| Emergency during wait | Immediate → EMERGENCY, safety_manager handles hardware |
| Startup timeout | 10 seconds + 1 retry, then FAULT |
| Phase 14 ramp shortcut | Set to zero, wait 2s, send OFF (full ramp logic in Phase 16) |

#### Fault/Emergency During Transition

| Scenario | During | Action |
|----------|--------|--------|
| PCS fault word non-zero | STARTING (IDLE→STANDBY) | Cancel start → FAULT, do NOT send OFF |
| PCS fault word non-zero | STOPPING (STANDBY→IDLE) | Cancel stop → FAULT, PCS already shutting down |
| Safety emergency detected | Any sub-state | → EMERGENCY immediately |
| ZMQ maintenance_enter | STARTING or STOPPING | Cancel timer → MAINTENANCE, send OFF |
| PCS goes offline (comm timeout) | STARTING | Cancel start → FAULT (PCS unreachable) |

Key rules:
- 1Hz loop never blocks — sub-states use timestamp comparison, not sleep
- Sub-states (STARTING, STOPPING) are internal — RTDB and ZMQ show parent state only
- Faults always interrupt transitions immediately — don't wait for timer to complete
- Phase 14 simplifies ramp-to-zero as "set zero, wait 2s" — full configurable ramp in Phase 16 (CTRL-08)

### ZMQ Command API Surface

| Command | Request Params | Success Response | Error Response |
|---------|---------------|-----------------|----------------|
| `mode_change` | `{target_state: "standby"\|"idle"}` | `{status: "ok", from: "idle", to: "standby"}` | `{status: "error", error_msg: "invalid transition from FAULT"}` |
| `manual_setpoint` | `{power_kw: float}` | `{status: "ok", accepted_kw: 25.0}` | `{status: "error", error_msg: "not in STANDBY/CHARGING/DISCHARGING"}` |
| `source_priority` | `{mode: "day"\|"night"\|"manual"}` | `{status: "ok", mode: "manual"}` | `{status: "error", error_msg: "invalid mode"}` |
| `fault_reset` | `{}` | `{status: "ok", from: "fault", to: "idle"}` | `{status: "error", error_msg: "not in FAULT state"}` |
| `maintenance_enter` | `{}` | `{status: "ok", from: "standby", to: "maintenance"}` | `{status: "error", error_msg: "already in MAINTENANCE"}` |
| `maintenance_exit` | `{}` | `{status: "ok", from: "maintenance", to: "idle"}` | `{status: "error", error_msg: "not in MAINTENANCE"}` |

#### Command Handling by State

| State Machine State | Command Handling |
|-------------------|-----------------|
| Stable (IDLE, STANDBY, CHARGING, etc.) | Process immediately |
| Transitioning (STARTING, STOPPING sub-states) | Reject: `{status: "error", error_msg: "transition in progress: STARTING (8s remaining)"}` |
| EMERGENCY | Reject all: `{status: "error", error_msg: "in EMERGENCY — wait for safety_reset"}` |
| MAINTENANCE | Reject all except maintenance_exit |
| FAULT | Accept fault_reset even during retry timer |

Key rules:
- Uses existing `encode_command_request/response` from `ems_common/ipc.py`
- `manual_setpoint` requires MANUAL source_priority mode — rejected in DAY/NIGHT
- `accepted_kw` in response shows clamped value (no silent truncation)
- No authentication — unix domain socket is local-only, any module can send
- No command queue — reject during transitions with descriptive error including remaining time
- `fault_reset` accepted even during retry countdown — operator override always works

### Claude's Discretion

- State machine class design (single class vs strategy pattern)
- Internal sub-state tracking data structures
- 1Hz loop implementation (asyncio sleep vs timer)
- RTDB read pattern (read all sections once per tick vs on-demand)
- ZMQ REP polling integration with async control loop
- Startup sequence (config load, RTDB attach, ZMQ bind order)
- Test strategy for state transitions and PCS sequencing

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/control_manager/python/` — Stub package (v0.1.0)
- `src/control_manager/c/src/main.c` — Stub C executable (not needed for Phase 14)
- `config/control_config.yaml` — SOC limits, power limits, source priority, state machine params
- `config/schemas/control_config.schema.json` — Full schema with x-mutable annotations
- `deploy/systemd/control_manager.service` — Service file (After dm, cm, cfg)
- `src/common/c/include/ems_types.h` — ems_control_state_t (8 states), ems_pcs_state_t, ems_source_priority_t
- `src/common/c/include/rtdb.h` — ems_system_t struct (needs 3 new fields)
- `src/common/c/include/ipc_defs.h` — SOCK_CONTROL_CMD, TOPIC_CONTROL_STATE, TOPIC_STATE_CHANGE
- `src/common/python/src/ems_common/ipc.py` — encode/decode command request/response helpers
- `src/common/python/src/ems_common/rtdb.py` — EmsSystem ctypes mirror (needs update)

### Established Patterns
- Async Python modules with SIGTERM/SIGINT handlers (config_manager, comm_manager, logger)
- ZMQ REP socket with non-blocking poll for command handling (safety_reset pattern)
- RTDB seqlock write: increment seq (odd) → write fields → increment seq (even)
- MessagePack envelope: {action, params} for requests, {status, result/error_msg} for responses
- Config loading via config_manager or direct YAML (safety_manager uses compiled defaults)
- 1Hz loop pattern: asyncio.sleep(interval) with timing correction

### Integration Points
- RTDB must exist before control_manager starts — systemd After=ems-data-manager.service
- Comm_manager must be running for PCS command execution — systemd After=ems-comm-manager.service
- RTDB struct change (ems_system_t) affects: rtdb.h, rtdb.py, data_manager publisher, logger Parquet schema
- Comm_manager PcsDevice needs write_setpoint() and process_command() methods added
- ZMQ telemetry PUB on topic "control.state" and "system" for logger and HMI
- ZMQ PUSH to logger for state_change events

</code_context>

<specifics>
## Specific Ideas

- Control_manager is the first module that WRITES commands to hardware (via RTDB → comm_manager) — all M1 modules were read-only
- PCS simulator (tools/simulators/modbus_sim) already handles 0x500E/0x0291/0x5064 writes — test infrastructure ready
- The RTDB struct change at M2 start is a coordinated C+Python update — plan this as the first task
- control_config.yaml already has state_machine.fault_retry_count (0-10, default 3) — use for auto-retry logic
- Phase 14 defers source priority logic, SOC limits, derating, and ramping to Phase 16 — keep the state machine clean

</specifics>

<deferred>
## Deferred Ideas

- Source priority dispatch (DAY/NIGHT/MANUAL) — Phase 16 (CTRL-04)
- SOC charge/discharge cutoff limits — Phase 16 (CTRL-05)
- Temperature derating curves — Phase 16 (CTRL-06)
- Configurable power ramp rate — Phase 16 (CTRL-08, Phase 14 uses simple 2s zero-then-off)
- Interlock checks (safety state + PCS online) — Phase 16 (CTRL-09)
- Hot-reload of control_config — Phase 16 (CTRL-11)
- Grid code compliance (frequency droop, voltage ride-through) — future milestone (CTRL-13)
- Multi-PCS master/slave — future milestone (CTRL-14)

</deferred>

---

*Phase: 14-control-state-machine*
*Context gathered: 2026-03-15*
