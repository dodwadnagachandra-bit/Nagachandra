# Phase 15: Alarm Engine - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

IEC 62682 alarm evaluation engine: 1Hz RTDB signal monitoring against configurable thresholds, 5-state alarm lifecycle, hysteresis + delay filtering, event publishing, and query API. Covers ALM-01, ALM-02, ALM-03, ALM-04, ALM-05, ALM-06, ALM-07, ALM-10. Pure Python module.

Note: Protection action dispatch to control_manager (ALM-08) and hot-reload (ALM-09) are Phase 16 scope.

</domain>

<decisions>
## Implementation Decisions

### Signal Path Resolution

How does a config rule like `signal: "bms.cell_voltage_max"` map to an actual RTDB field value at runtime?

**Decision:** A resolver function maps dotted signal paths to RTDB struct field reads. The resolver is a dictionary built at startup from the RTDB topology, not a dynamic attribute walker.

| Signal Path | RTDB Source | Resolution |
|-------------|-----------|------------|
| `bms.cell_voltage_max` | `max(rack.max_cell_v for rack in clusters[*].racks[*] if rack.online)` | Aggregate across all online racks |
| `bms.cell_voltage_min` | `min(rack.min_cell_v for rack in clusters[*].racks[*] if rack.online)` | Aggregate across all online racks |
| `bms.cell_temp_max` | `max(rack.max_cell_t for rack in clusters[*].racks[*] if rack.online)` | Aggregate across all online racks |
| `bms.cell_temp_min` | `min(rack.min_cell_t for rack in clusters[*].racks[*] if rack.online)` | Aggregate across all online racks |
| `bms.soc_pct` | `avg(rack.pack_soc for rack in clusters[*].racks[*] if rack.online)` | Weighted average system SOC |
| `bms.bus_voltage_v` | `rtdb.pcs.dc_voltage` | PCS DC bus voltage (closest proxy) |
| `pcs.internal_temp_c` | `rtdb.pcs.temperature` | PCS temperature field |

Key rules:
- Signal paths are a fixed set — not arbitrary RTDB field access. Only paths used by alarm rules need resolvers.
- Offline racks (rack.online == 0) are excluded from aggregates — a dead rack shouldn't trigger system-wide alarms.
- Resolution runs once per 1Hz tick, before all alarm rules evaluate. Values cached for the tick.
- Invalid signal path in config → log ERROR at startup, disable that alarm rule (fail-open, not fail-closed).

**Rationale:** A dictionary-based resolver is simple, testable, and avoids the fragility of dynamic attribute access on ctypes structs. The fixed set matches the 9 alarm rules in config — no need for a general-purpose RTDB query language. Offline rack exclusion follows standard BESS practice (IEC 62619 — battery health monitoring excludes disconnected modules).

### Alarm Lifecycle State Machine (IEC 62682)

How does each alarm instance track its lifecycle from activation to clearance?

**Decision:** 5-state IEC 62682 lifecycle per alarm instance with timestamps at each transition:

| State | Meaning | Entry Condition | Exit Condition |
|-------|---------|----------------|----------------|
| NORMAL | Signal within limits | Initial state, or RTN acknowledged | Signal exceeds threshold + delay |
| ACTIVE_UNACKED | Alarm active, operator not yet aware | Signal exceeded threshold for delay_ms | Operator sends acknowledge command |
| ACTIVE_ACKED | Alarm active, operator aware | Acknowledge received while signal still exceeding | Signal returns within limits (with hysteresis) |
| CLEARED_UNACKED | Signal returned to normal, but operator hasn't confirmed | Signal back in limits while in ACTIVE_UNACKED | Operator sends acknowledge command |
| RTN (Return to Normal) | Alarm cycle complete | Acknowledge received in CLEARED_UNACKED, or signal clears from ACTIVE_ACKED | Auto-transition to NORMAL |

Key rules:
- Each alarm rule gets one lifecycle instance (not per-rack — signal path resolution already aggregates).
- Timestamps recorded at every transition: `activated_at`, `acknowledged_at`, `cleared_at`, `rtn_at`.
- RTN auto-transitions to NORMAL after publishing the RTN event — no operator action needed for the final step.
- Alarm instances persist in memory only — no disk persistence. On restart, all alarms start in NORMAL (RTDB values re-evaluated on first tick).
- Acknowledge command arrives via ZMQ REQ on SOCK_ALARM_CMD with `{action: "acknowledge", alarm_id: "cell_voltage_high"}`.

**Rationale:** IEC 62682 Section 6.3 defines this 4+1 state model as the standard for process alarm management. The CLEARED_UNACKED state is critical — it ensures operators know an alarm occurred even if it cleared before they noticed. No disk persistence because alarms are derived state (re-computable from RTDB values on restart). Per-rule instances (not per-rack) keep the alarm count manageable for HMI display (9 alarms max, not 9 × 64 racks).

### Severity-to-Action Mapping

What concrete action does each severity level trigger in Phase 15 (before Phase 16 wires the control_manager response)?

**Decision:** Phase 15 publishes events only. Protection actions are deferred to Phase 16 (ALM-08).

| Severity | Phase 15 Action | Phase 16 Action (deferred) | DO Mapping |
|----------|----------------|---------------------------|------------|
| warning | Publish alarm event to logger (PUSH) | None — informational only | DO-2 (warning lamp) via safety_manager |
| action | Publish alarm event to logger (PUSH) | Request power reduction to control_manager | DO-2 (warning lamp) |
| protection | Publish alarm event to logger (PUSH) | Request PCS shutdown to control_manager | DO-4 (fault lamp) via safety_manager |

Key rules:
- Phase 15 alarm_manager ONLY publishes events — it does NOT send commands to control_manager or safety_manager.
- DO-2/DO-4 lamp control remains with safety_manager (Phase 10) — alarm_manager does not write GPIO directly.
- The alarm event payload includes severity, so Phase 16's control_manager subscriber can filter by severity and act.
- All three severity levels publish on ZMQ PUSH to logger and PUB on telemetry (topic: "alarm") for subscribers.

**Rationale:** Clean separation of concerns — alarm_manager evaluates thresholds and manages lifecycle, control_manager decides what to do about it. This follows the IEC 62682 principle that the alarm system informs, the control system acts. Connecting them in Phase 16 avoids circular dependencies during Phase 15 development/testing.

### Alarm Query API Surface

What queries should SOCK_ALARM_CMD support, and what should responses contain?

**Decision:** 3 query types via ZMQ REQ/REP:

| Query | Request | Response | Purpose |
|-------|---------|----------|---------|
| `get_active_alarms` | `{action: "get_active_alarms"}` | `{status: "ok", alarms: [{alarm_id, signal, severity, state, value, threshold, activated_at, acknowledged_at}]}` | HMI alarm list screen |
| `acknowledge` | `{action: "acknowledge", alarm_id: "cell_voltage_high"}` | `{status: "ok", alarm_id, from_state, to_state}` or `{status: "error", error_msg}` | Operator ACK from HMI |
| `get_alarm_config` | `{action: "get_alarm_config"}` | `{status: "ok", rules: [{alarm_id, signal, severity, high_threshold, low_threshold, hysteresis, delay_ms, enabled}]}` | HMI config display |

Key rules:
- Uses existing `encode_command_request/response` from `ems_common/ipc.py` — same envelope as control_cmd.
- `get_active_alarms` returns only non-NORMAL alarms (ACTIVE_UNACKED, ACTIVE_ACKED, CLEARED_UNACKED).
- `acknowledge` validates: alarm must be in ACTIVE_UNACKED or CLEARED_UNACKED state. Reject if already acknowledged or in NORMAL.
- `get_alarm_config` returns the current running config (reflects hot-reloaded values after Phase 16).
- No `get_alarm_history` — historical alarms are in JSONL via logger. HMI queries logger's `event_log` query type.

**Rationale:** Three queries cover the HMI alarm screen needs: show active alarms, let operator acknowledge, display thresholds. History is already handled by the logger (Phase 12, LOG-04 + `event_log` query type). Adding history to alarm_manager would duplicate the logger's role. The query surface matches the pattern established by control_cmd (Phase 14).

### Claude's Discretion

- Alarm evaluation loop architecture (single async function vs class-based evaluator)
- AlarmInstance class internal design (dataclass vs dict)
- ZMQ socket initialization and async poller integration
- Startup sequence (config load, RTDB attach, ZMQ bind order)
- How to handle the 1Hz evaluation + ZMQ command polling in the same async loop
- Test fixtures for simulating RTDB signal changes

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/alarm_manager/` — Stub package (v0.1.0, depends on ems-common)
- `config/alarms_config.yaml` — 9 alarm rules with thresholds, hysteresis, delays, all enabled
- `config/schemas/alarms_config.schema.json` — Full schema with severity enum, x-mutable annotations
- `config/profiles/*/alarms_config.yaml` — Per-deployment threshold overrides
- `deploy/systemd/alarm_manager.service` — Service file (After=data_manager, control_manager)
- `src/common/python/src/ems_common/ipc.py` — SOCK_ALARM_CMD, TOPIC_ALARM, encode_event, encode_command_request/response
- `src/common/python/src/ems_common/rtdb.py` — EmsRack (min/max cell v/t, pack_soc), EmsPcs (temperature)
- `src/comm_manager/python/src/ems_comm_manager/events.py` — ZMQ PUSH event publishing pattern to follow

### Established Patterns
- Async Python modules with SIGTERM/SIGINT signal handlers (config_manager, comm_manager, logger)
- ZMQ REP socket with asyncio poller for command handling (safety_reset, logger query_server patterns)
- ZMQ PUSH with zmq.NOBLOCK + catch EAGAIN for event publishing (comm_manager events.py)
- Config loading via yaml.safe_load with JSON Schema validation (config_manager pattern)
- RTDB seqlock read via ems_common.rtdb.attach_rtdb()

### Integration Points
- RTDB must exist (systemd After=ems-data-manager.service)
- Control_manager should exist but not required for Phase 15 (alarm events published regardless)
- ZMQ PUSH to `ipc:///run/ems/logger.sock` for alarm events
- ZMQ PUB on `ipc:///run/ems/telemetry.sock` topic "alarm" for subscribers
- ZMQ REP on `ipc:///run/ems/alarm_cmd.sock` for queries
- Phase 16 will add: alarm_manager PUB → control_manager SUB for protection action dispatch

</code_context>

<specifics>
## Specific Ideas

- The 9 alarm rules are well-defined in config — no need to invent new ones
- Hysteresis is percentage-based: clear threshold = activation_threshold ± (threshold × hysteresis_pct / 100)
- Delay timer is per-alarm-instance, not global — each alarm tracks its own entry time
- alarms_config defaults section provides fallback hysteresis (2%) and delay (5000ms) if not specified per rule
- BMS signal paths require topology-aware aggregation (max/min across racks) — this is the most complex resolver

</specifics>

<deferred>
## Deferred Ideas

- **ALM-08**: Protection-severity alarms send power reduction/shutdown to control_manager — Phase 16
- **ALM-09**: Hot-reload of alarms_config.yaml — Phase 16
- **ALM-11**: Alarm shelving (temporary suppression with auto-restore) — future milestone
- **ALM-12**: Alarm grouping (suppress child alarms when parent active) — future milestone
- **ALM-13**: Alarm analytics (most frequent, longest active, first-out) — future milestone
- DO-2/DO-4 lamp integration — safety_manager owns GPIO, alarm_manager publishes events only

</deferred>

---

*Phase: 15-alarm-engine*
*Context gathered: 2026-03-15*
