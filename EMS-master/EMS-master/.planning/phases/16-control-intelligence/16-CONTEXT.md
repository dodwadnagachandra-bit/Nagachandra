# Phase 16: Control Intelligence - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Source priority dispatch, SOC limits, temperature derating, power ramping, interlock logic, alarm-to-control protection flow, and hot-reload for both control_config and alarms_config. Covers CTRL-04, CTRL-05, CTRL-06, CTRL-08, CTRL-09, CTRL-11, ALM-08, ALM-09. Extends the control_manager from Phase 14 and wires it to alarm_manager from Phase 15.

</domain>

<decisions>
## Implementation Decisions

### Source Priority Dispatch Algorithm

How does the control_manager decide what power source to use based on DAY/NIGHT/MANUAL mode?

**Decision:** Waterfall evaluation — iterate the priority array, check each source's availability in RTDB, use the first available source.

| Source | Available When | RTDB Check | Action When Selected |
|--------|---------------|-----------|---------------------|
| solar | PV power > threshold (1 kW) | `rtdb.pcs.active_power > 0` or PV meter | Use solar, charge BESS if excess |
| grid | ACDB feedback active (DI-0 = 1) | `rtdb.gpio.di[0] == 1` | Draw from grid, charge BESS if scheduled |
| bess | SOC > discharge_cutoff_pct AND not in FAULT | `rtdb.system.total_soc > cutoff` | Discharge BESS at scheduled power |
| dg | DG online in RTDB | `rtdb.system.dg_available` (future) | DG provides power (read-only, no auto-start in M2) |

#### DAY Mode Dispatch (solar > grid > bess > dg)

| Solar | Grid | BESS SOC OK | DG | Action |
|-------|------|-------------|-----|--------|
| Yes | Yes | Yes | - | Use solar, excess → charge BESS |
| Yes | No | Yes | - | Use solar, BESS supplements if needed |
| No | Yes | Yes | - | Grid powers load, charge BESS if scheduled |
| No | Yes | No | - | Grid powers load, BESS at cutoff (no discharge) |
| No | No | Yes | Yes | Discharge BESS, DG supplements |
| No | No | Yes | No | Discharge BESS only |
| No | No | No | Yes | DG only |
| No | No | No | No | No source available → IDLE, log WARNING |

Key rules:
- Source priority only determines WHERE power comes from, not HOW MUCH. The setpoint (from scheduler or manual command) determines the magnitude.
- DAY/NIGHT mode selection is driven by `schedule_config.yaml` day_night section (day_start/night_start times), compared against system clock.
- MANUAL mode bypasses priority — operator-set power directly dispatched to PCS (from Phase 14 `manual_setpoint` command).
- DG is read-only in M2 — alarm_manager can see it's available, but control_manager does not send start/stop commands. DG auto-start deferred to future.
- If no source available, transition to IDLE and log WARNING — do not stay in STANDBY with zero setpoint indefinitely.

**Rationale:** Waterfall priority is the standard BESS dispatch pattern. The priority array is configurable in control_config.yaml (DAY and NIGHT arrays). This matches the original requirements (Section 8 — Power Source Priority). The truth table covers all realistic combinations for residential/commercial BESS. DG auto-start requires a control protocol not yet specified, so it's correctly deferred.

### Temperature Derating Curves

How does temperature derating reduce max power, and at what thresholds?

**Decision:** Piecewise linear derating with two trigger points per thermal zone. Three thermal zones monitored independently — the most restrictive derating wins.

| Thermal Zone | Signal | Start Derating | Full Cutoff | Source |
|-------------|--------|----------------|-------------|--------|
| BMS Cell Temp | `max(rack.max_cell_t)` | 40°C → 80% power | 50°C → 0% power | IEC 62619 lithium battery safety |
| PCS Internal Temp | `rtdb.pcs.temperature` | 65°C → 80% power | 80°C → 0% power | PCS V1.24 thermal limits |
| BMS Cell Temp Low | `min(rack.min_cell_t)` | 5°C → 50% power | 0°C → 0% power | Lithium plating risk below 0°C |

#### Derating Formula

```
For each zone:
  if signal < start_derating:
    zone_factor = 100%
  elif signal >= full_cutoff:
    zone_factor = 0%
  else:
    zone_factor = 100% - (signal - start_derating) / (full_cutoff - start_derating) * (100% - min_factor)

active_derating_pct = min(zone_factor_1, zone_factor_2, zone_factor_3)
effective_max_power = max_power_kw * active_derating_pct / 100
```

Key rules:
- Linear interpolation between start and cutoff — simple, predictable, no lookup table needed.
- Three zones evaluated independently — most restrictive (lowest percentage) wins.
- `active_derating_pct` written to RTDB `system.active_derating_pct` for HMI display.
- Derating thresholds are NOT in control_config.yaml yet — they should be added as a `derating` section with x-mutable: true fields. This requires a schema update.
- Derating applies to both charge and discharge equally (symmetric).
- Cold temperature derating (BMS Cell Temp Low) is critical — charging below 0°C causes permanent lithium plating damage.

**Rationale:** Piecewise linear is the industry standard for thermal derating in BESS (used by Tesla Megapack, BYD Cube, Sungrow). Two breakpoints (start, cutoff) per zone is sufficient — more complex curves add precision that sensor accuracy doesn't support. The 40°C/50°C BMS thresholds align with IEC 62619 (secondary lithium cells). The 65°C/80°C PCS thresholds are conservative margins below typical IGBT junction limits. Cold derating at 5°C/0°C follows lithium chemistry fundamentals (graphite anode plating onset).

### Alarm-to-Control Protection Flow

How do protection-severity alarms from alarm_manager reach control_manager and trigger power reduction or shutdown?

**Decision:** alarm_manager publishes protection events on ZMQ PUB (topic "alarm"). control_manager subscribes and filters for protection severity. No REQ/REP — fire-and-forget prevents blocking.

| Alarm Severity | control_manager Response | Mechanism |
|---------------|------------------------|-----------|
| warning | No action — informational only | control_manager ignores warning-severity alarm events |
| action | Reduce power to 50% of current setpoint | control_manager reads alarm event, applies 50% factor for 60 seconds, then re-evaluates |
| protection | Transition to FAULT, ramp to zero, PCS OFF | control_manager treats protection alarm like a PCS fault — same FAULT state handling |

#### Cooldown and Oscillation Prevention

| Aspect | Decision |
|--------|----------|
| Cooldown after protection | 60-second hold in FAULT before auto-retry (prevents oscillation) |
| Action severity reduction | 50% power for 60 seconds, then re-evaluate — if alarm still active, hold reduction |
| Multiple simultaneous alarms | Most severe wins (protection > action > warning) |
| Alarm cleared during response | Complete the cooldown period before returning to normal — don't snap back |

Key rules:
- PUB/SUB (not REQ/REP) for alarm-to-control — avoids blocking dependencies. If control_manager is down, alarm events are simply not consumed (they still go to logger via PUSH).
- control_manager subscribes to telemetry PUB topic "alarm" — same socket as other telemetry, no new channel.
- 60-second cooldown prevents oscillation: alarm fires → control reduces power → signal recovers → alarm clears → control restores power → signal exceeds again → alarm fires (loop). The cooldown breaks this cycle.
- Action-severity 50% reduction is a conservative default — the idea is "reduce stress without full shutdown." The percentage could be configurable in future.

**Rationale:** PUB/SUB decoupling follows the research finding that blocking dependencies between alarm and control create deadlock risk. The 60-second cooldown is standard in SCADA (prevents "chattering" control responses). Protection severity triggers the same FAULT path as PCS faults — reusing the Phase 14 state machine logic rather than adding a parallel path.

### Hot-Reload Integration

How do both modules detect and apply config changes without restart?

**Decision:** Both modules subscribe to config_manager's ZMQ PUB `config_reload` event (already published by config_manager on hot-reload). On receiving a reload event for their config file, they re-read and validate the new config, then swap atomically.

| Module | Config File | What Changes | What Doesn't Change |
|--------|------------|-------------|-------------------|
| control_manager | control_config.yaml | SOC limits, power limits, source_priority, fault_retry_count | loop_interval_ms (structural) |
| alarm_manager | alarms_config.yaml | All thresholds, hysteresis, delays, enable/disable flags | (everything is mutable) |

Key rules:
- Subscribe to telemetry PUB topic "config_reload" — same channel already used by other modules.
- On config_reload event, check if `config_name` matches "control_config" or "alarms_config".
- Re-read config from disk (config_manager already validated it), swap internal config reference atomically.
- For alarm_manager: existing alarm instances retain their lifecycle state — only thresholds change. An active alarm with a new threshold may immediately clear or stay active depending on current signal value.
- For control_manager: SOC limit changes take effect on next 1Hz tick. Source priority changes take effect on next dispatch evaluation.
- No restart needed — the async loop checks config reference on each tick.

**Rationale:** Reuses the existing config_manager hot-reload infrastructure (Phase 9, CONF-02/08). The `config_reload` ZMQ event already carries the config name and diff — no new IPC channel needed. Atomic swap (replace reference, not mutate in place) prevents partial config application. This follows the pattern established by config_manager's own internal hot-reload handling.

### Claude's Discretion

- Source priority evaluator class design (strategy pattern vs switch statement)
- Derating curve implementation (inline formula vs lookup class)
- How to add derating thresholds to control_config.yaml schema (new section vs extend existing)
- ZMQ SUB integration for alarm events in the control loop (poll alongside REP socket)
- Hot-reload swap mechanism (replace dataclass instance vs update fields)
- Test strategy for derating curves (parameterized tests with edge cases)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 14 control_manager: state machine, PCS command dispatch, ZMQ command API (to be extended)
- Phase 15 alarm_manager: alarm evaluation, lifecycle, event publishing (publishes on PUB topic "alarm")
- `config/control_config.yaml` — SOC limits, power limits, source_priority arrays (day_order, night_order)
- `config/schedule_config.yaml` — day_night section (day_start, night_start times)
- `config/alarms_config.yaml` — 9 rules with thresholds, hysteresis, delays
- `src/common/python/src/ems_common/ipc.py` — TOPIC_CONFIG_RELOAD, PUB/SUB helpers
- `src/config_manager/src/ems_config_manager/manager.py` — config_reload event publishing pattern

### Established Patterns
- Config hot-reload via inotify + debounce + validate-then-swap (config_manager Phase 9)
- ZMQ PUB/SUB for telemetry fan-out (data_manager Phase 9)
- RTDB seqlock write for system section (control_manager Phase 14)
- Async event loop with multiple poller sources (logger Phase 12 — 4 tasks)

### Integration Points
- control_manager reads RTDB: clusters[].racks[] for SOC/temp, pcs for PCS state/temp, gpio for safety state
- control_manager writes RTDB: system section (active_setpoint_kw, active_derating_pct, source_priority)
- alarm_manager publishes on PUB topic "alarm" → control_manager subscribes
- config_reload events on PUB topic "config_reload" → both modules subscribe
- control_config.yaml schema needs `derating` section added (new fields for thermal thresholds)

</code_context>

<specifics>
## Specific Ideas

- Source priority arrays are already in config — implementation is just waterfall evaluation
- schedule_config.yaml day_night section provides the DAY/NIGHT switch time — control_manager reads this at startup and on hot-reload
- Derating needs new config fields — add a `derating` section to control_config.yaml with start/cutoff thresholds per zone
- The 50% power reduction for action-severity alarms is a placeholder — make it configurable later
- Cold temperature derating is the most safety-critical — lithium plating at 0°C is irreversible

</specifics>

<deferred>
## Deferred Ideas

- **CTRL-13**: Grid code compliance (frequency droop, voltage ride-through) — future milestone
- **CTRL-14**: Multi-PCS master/slave coordination — future milestone (Decision #7.3)
- **CTRL-15**: Reactive power control (PF, VAr setpoint) — future milestone
- **CTRL-16**: Off-grid mode with frequency/voltage regulation — future milestone
- DG auto-start/stop — requires DG control protocol specification
- Action-severity power reduction percentage (50%) as configurable parameter
- More granular derating curves (non-linear, per-cell, charge vs discharge asymmetric)

</deferred>

---

*Phase: 16-control-intelligence*
*Context gathered: 2026-03-15*
