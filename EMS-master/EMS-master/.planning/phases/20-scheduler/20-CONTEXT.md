# Phase 20: Scheduler - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

Scheduler evaluates time windows and power curves, sends setpoint commands to control_manager, supports hot-reload and day/night mode switching. Covers SCHED-01 through SCHED-07. Pure Python module.

</domain>

<decisions>
## Implementation Decisions

### Command Dispatch Pattern

How does the scheduler send setpoint commands to control_manager — direct ZMQ REQ, or through RTDB?

**Decision:** ZMQ REQ on SOCK_CONTROL_CMD — same API that HMI uses. Not through RTDB.

| Aspect | Decision |
|--------|----------|
| Command channel | ZMQ REQ on `ipc:///run/ems/control_cmd.sock` |
| Commands used | `manual_setpoint` (power_kw), `source_priority` (mode), `mode_change` (standby/idle) |
| Frequency | On state change only (not every 1Hz tick) — send when window transitions or setpoint changes |
| Error handling | If REQ times out (5s), retry next evaluation tick (1Hz). Log WARNING. |
| Source priority | Scheduler sets MANUAL mode when sending setpoints — control_manager accepts manual setpoints only in MANUAL mode |

Key rules:
- Scheduler is a ZMQ client of control_manager, not a peer — it sends commands, control_manager decides whether to accept them.
- Scheduler sets `source_priority` to MANUAL before sending `manual_setpoint` — this is the two-step process established in Phase 14 CONTEXT.md.
- When scheduler mode is "manual" (no automatic dispatch), scheduler sends nothing — operator controls directly via HMI.
- Scheduler does NOT write to RTDB directly — it has no RTDB section. It's a command-only module.
- Day/night switching sends `source_priority` command with "day" or "night" mode at the configured transition time.

**Rationale:** Using the existing control_cmd API (Phase 14) avoids creating a second command path. The API is already tested, validated, and handles all edge cases (transition rejection, FAULT state, etc.). Direct RTDB write would violate single-writer-per-section (control_manager owns the system section). The two-step MANUAL + setpoint pattern is already locked from Phase 14.

### Schedule Evaluation Logic

How does the 1Hz loop determine what action to take based on current time and schedule_config?

**Decision:** Three modes evaluated independently. The active mode reads from schedule_config.yaml `mode` field.

| Mode | Evaluation Logic | Output |
|------|-----------------|--------|
| manual | No evaluation — scheduler is idle | No commands sent |
| time_of_day | Find the window containing current time (HH:MM). If in a window, send its action + power_kw. If between windows, send idle. | `mode_change` + `manual_setpoint` |
| curve | Calculate index = `(hour * 4) + (minute // 15)`. Read `power_curve[index]`. Send as setpoint. | `manual_setpoint` |

#### Time Window Matching

| Scenario | Behavior |
|----------|----------|
| Current time inside a window | Send window's action (charge/discharge/idle) + power_kw |
| Current time outside all windows | Send idle (setpoint = 0) |
| Overlapping windows | First matching window wins (top of list priority) |
| Window wraps midnight (22:00 → 06:00) | Handled by checking `start > end` and adjusting comparison |
| Exactly at window boundary (start time) | Include start, exclude end (half-open interval) |

#### 96-Point Curve Interpolation

| Aspect | Decision |
|--------|----------|
| Index calculation | `index = hour * 4 + minute // 15` (0-95) |
| Interpolation | Step (no linear interpolation between points) — hold value for full 15-min interval |
| Out of range | Wrap: index 96 = index 0 (next day) |
| Positive values | Discharge (kW export) |
| Negative values | Charge (kW import) |
| Zero values | Idle |

Key rules:
- Scheduler only sends commands on state change: when the active window changes, when the curve index changes (every 15 min), or when day/night mode transitions.
- Between state changes, scheduler does NOT re-send the same command every tick — control_manager persists the last setpoint.
- On startup, scheduler evaluates immediately (don't wait for the next window boundary).
- Day/night mode switching (SCHED-05) is independent of the schedule mode — it always runs, even in "manual" mode, to keep source_priority correct.

**Rationale:** Step interpolation (not linear) for the curve matches the 15-minute settlement periods used in electricity markets. Sending commands only on state change (not every tick) reduces ZMQ traffic and follows the "don't repeat yourself" principle. Midnight wrapping for time windows is necessary because off-peak charging windows commonly span midnight (22:00 → 06:00).

### Day/Night Source Priority Switching

How does the scheduler manage DAY ↔ NIGHT transitions for source priority?

**Decision:** Compare current time against `schedule_config.yaml` `day_night.day_start` and `night_start`. Send `source_priority` command to control_manager on transition only (not every tick).

| Time | Mode | Source Priority | Command Sent |
|------|------|----------------|-------------|
| After day_start, before night_start | DAY | Solar > Grid > BESS > DG | `source_priority {mode: "day"}` |
| After night_start, before day_start | NIGHT | Grid > BESS > DG | `source_priority {mode: "night"}` |

Key rules:
- Transition detected by comparing current mode vs expected mode at current time. If different, send command.
- On startup, immediately evaluate and send the correct mode (don't wait for next transition).
- Day/night switching runs independently of schedule mode — even in "manual" schedule mode, source_priority transitions happen.
- If schedule mode is "time_of_day" or "curve", scheduler sends MANUAL source_priority (overrides DAY/NIGHT for dispatch). When schedule mode changes to "manual", restore DAY/NIGHT based on current time.
- The day_start/night_start values are strings ("06:00", "18:00") — parsed to hours/minutes, compared against system clock.

**Rationale:** Source priority switching is a separate concern from power dispatch — a system can be in DAY mode (solar priority) while the scheduler is idle (no active windows). Sending only on transitions avoids flooding control_manager with redundant source_priority commands. Startup immediate evaluation ensures correct mode even if scheduler starts mid-day.

### Claude's Discretion

- Scheduler class architecture (single SchedulerLoop class vs separate evaluators)
- Time parsing and comparison implementation (datetime vs manual hour/minute math)
- ZMQ REQ socket lifecycle (create on startup vs create per command)
- Hot-reload mechanism (subscribe to SOCK_CONFIG_PUB like control/alarm managers)
- Telemetry publishing format for SCHED-07 (what fields in the schedule state message)
- Test strategy (mock ZMQ REQ socket, mock system clock for time-based tests)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/scheduler/` — Stub package (v0.1.0, depends on ems-common)
- `config/schedule_config.yaml` — Three modes, time windows, day_night, 96-point power curve
- `config/schemas/schedule_config.schema.json` — Full schema with x-hot-reload: true, all fields x-mutable
- `config/profiles/*/schedule_config.yaml` — Per-deployment overrides (residential 10kW, commercial 200kW, container 1000kW)
- `deploy/systemd/scheduler.service` — Service file (After=control_manager, config_manager)
- `src/common/python/src/ems_common/ipc.py` — SOCK_CONTROL_CMD, encode_command_request/response

### Established Patterns
- ZMQ REQ for commands (HMI will use same pattern in Phase 18)
- Config hot-reload via SUB on SOCK_CONFIG_PUB (Phase 16 pattern)
- Async Python modules with signal handlers
- Schedule mode "manual" = no automatic dispatch (operator-only via HMI)

### Integration Points
- ZMQ REQ on SOCK_CONTROL_CMD — sends mode_change, manual_setpoint, source_priority commands
- ZMQ SUB on SOCK_CONFIG_PUB — receives schedule_config reload events
- ZMQ PUB on SOCK_TELEMETRY — publishes schedule state (topic: "schedule")
- Systemd: After=control_manager (scheduler needs control API to exist)

</code_context>

<specifics>
## Specific Ideas

- Scheduler is the simplest M3 module — no RTDB, no complex state machine, just time evaluation + ZMQ commands
- The 96-point curve is the same format used by utility dispatch systems (15-min settlement periods)
- Day/night switching happens at most twice per day — very low ZMQ traffic
- Hot-reload of schedule_config is critical for the HMI Settings screen (Phase 19) — operator edits schedule, scheduler picks it up

</specifics>

<deferred>
## Deferred Ideas

- **SCHED-08**: Calendar-based scheduling (weekday/weekend) — future requirement
- **SCHED-09**: Tariff-aware scheduling — future requirement
- **SCHED-10**: Forecast-based scheduling — future requirement
- Linear interpolation between curve points — step function is sufficient for 15-min intervals
- Scheduler status dashboard in HMI — Phase 19 Settings screen shows basic info

</deferred>

---

*Phase: 20-scheduler*
*Context gathered: 2026-03-15*
