# Phase 16: Control Intelligence - Research

**Researched:** 2026-03-15
**Domain:** Python control loop extensions — source priority dispatch, thermal derating, power ramping, interlock guards, alarm-to-control protection flow, hot-reload for two configs
**Confidence:** HIGH (all findings derived from project source code and established project patterns)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Source Priority Dispatch:** Waterfall evaluation — iterate the priority array, check each source's availability in RTDB, use the first available source.

| Source | Available When | RTDB Check | Action When Selected |
|--------|---------------|-----------|---------------------|
| solar | PV power > threshold (1 kW) | `rtdb.pcs.active_power > 0` or PV meter | Use solar, charge BESS if excess |
| grid | ACDB feedback active (DI-0 = 1) | `rtdb.gpio.di[0] == 1` | Draw from grid, charge BESS if scheduled |
| bess | SOC > discharge_cutoff_pct AND not in FAULT | `rtdb.system.total_soc > cutoff` | Discharge BESS at scheduled power |
| dg | DG online in RTDB | `rtdb.system.dg_available` (future) | DG provides power (read-only, no auto-start in M2) |

DAY mode: solar > grid > bess > dg. NIGHT mode: grid > bess > dg. MANUAL mode bypasses priority entirely. If no source available, transition to IDLE and log WARNING.

**Temperature Derating:** Piecewise linear derating with two trigger points per thermal zone. Three zones monitored independently — most restrictive wins.

| Thermal Zone | Signal | Start Derating | Full Cutoff |
|-------------|--------|----------------|-------------|
| BMS Cell Temp High | `max(rack.max_cell_t)` | 40°C → 80% power | 50°C → 0% |
| PCS Internal Temp | `rtdb.pcs.temperature` | 65°C → 80% power | 80°C → 0% |
| BMS Cell Temp Low | `min(rack.min_cell_t)` | 5°C → 50% power | 0°C → 0% |

Formula: linear interpolation between start and cutoff. Most restrictive zone_factor wins. Written to `system.active_derating_pct`.

**Alarm-to-Control Protection Flow:** alarm_manager publishes protection events on ZMQ PUB (topic "alarm"). control_manager subscribes and filters for protection severity. Fire-and-forget (no REQ/REP).

| Alarm Severity | control_manager Response |
|---------------|------------------------|
| warning | No action |
| action | Reduce power to 50% of current setpoint for 60 seconds |
| protection | Transition to FAULT, ramp to zero, PCS OFF |

60-second cooldown in FAULT before auto-retry. Complete cooldown before returning to normal (don't snap back).

**Hot-Reload Integration:** Both modules subscribe to config_manager's ZMQ PUB `config_reload` event (TOPIC_CONFIG_RELOAD). On receiving a reload event for their config file, re-read from disk and swap atomically.

| Module | Config File | What Changes |
|--------|------------|-------------|
| control_manager | control_config.yaml | SOC limits, power limits, source_priority, fault_retry_count |
| alarm_manager | alarms_config.yaml | All thresholds, hysteresis, delays, enable/disable flags |

### Claude's Discretion

- Source priority evaluator class design (strategy pattern vs switch statement)
- Derating curve implementation (inline formula vs lookup class)
- How to add derating thresholds to control_config.yaml schema (new section vs extend existing)
- ZMQ SUB integration for alarm events in the control loop (poll alongside REP socket)
- Hot-reload swap mechanism (replace dataclass instance vs update fields)
- Test strategy for derating curves (parameterized tests with edge cases)

### Deferred Ideas (OUT OF SCOPE)

- CTRL-13: Grid code compliance (frequency droop, voltage ride-through)
- CTRL-14: Multi-PCS master/slave coordination
- CTRL-15: Reactive power control
- CTRL-16: Off-grid mode
- DG auto-start/stop
- Action-severity power reduction percentage (50%) as configurable parameter
- More granular derating curves (non-linear, per-cell, charge vs discharge asymmetric)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CTRL-04 | Source priority evaluates DAY/NIGHT/MANUAL modes from control_config.yaml, with MANUAL override via ZMQ command | Waterfall evaluator reads `source_priority.day_order`/`night_order` from existing config; day/night boundary from `schedule_config.yaml day_night` section; MANUAL already stubbed in Phase 14 `source_priority` command handler |
| CTRL-05 | SOC-based charge/discharge limits enforce charge_cutoff_pct and discharge_cutoff_pct, transitioning to IDLE when SOC limits are hit | `soc_limits` section exists in config schema; `system.total_soc` field exists in EmsSystem RTDB struct; guard logic integrates into `_tick()` before setpoint dispatch |
| CTRL-06 | Temperature derating reduces max power when BMS cell temps or PCS temps exceed configurable thresholds, with linear ramp-down curve | Derating thresholds need new `derating` section in control_config.yaml schema; signals available: `pcs.temperature` in EmsPcs and `rack.max_cell_t`/`min_cell_t` in EmsRack via seqlock copy |
| CTRL-08 | Power ramping limits setpoint change rate to configurable kW/s | New `ramping` section in control_config.yaml schema; ramp logic sits between desired setpoint and `active_setpoint_kw` RTDB write in `_tick()`; uses `time.monotonic()` for delta_t |
| CTRL-09 | Interlock checks verify safety_manager not in emergency and PCS is online before STANDBY→CHARGING/DISCHARGING transitions | `safety_emergency` already derived from `gpio.do_state[5]` in loop.py; PCS online = `pcs_state in (PCS_STATE_RUNNING,)`; interlock guard runs before state machine processes pending setpoint |
| CTRL-11 | Hot-reload of control_config.yaml applies new SOC limits, power limits, source priority, fault_retry_count without restarting | `TOPIC_CONFIG_RELOAD` exists in ipc.py; ControlLoop needs a SUB socket subscribed to `config_pub.sock` (or alarm_pub.sock); atomic swap of `self._config` reference on matching `config_name` |
| ALM-08 | Protection-severity alarms send power reduction or PCS shutdown requests to control_manager via ZMQ | Per CONTEXT.md decision: alarm_manager's AlarmLoop subscribes to its own PUB alarm events (or publishes to control_manager via REQ on `SOCK_CONTROL_CMD`); control_manager subscribes to `SOCK_ALARM_PUB` topic "alarm" |
| ALM-09 | Hot-reload of alarms_config.yaml applies new thresholds, hysteresis, delays, enable/disable flags without restarting | AlarmLoop needs SUB socket for config_reload events; on matching `alarms_config`, call `load_alarm_config()` then `build_alarm_instances()` and swap `self._evaluator._instances` atomically |
</phase_requirements>

---

## Summary

Phase 16 extends the Phase 14 `ControlLoop` and Phase 15 `AlarmLoop` with decision intelligence. All code lives in existing modules — no new Python packages required. The work is organized around five concerns: (1) source priority dispatch wired into `_tick()`, (2) SOC and interlock guards applied before the state machine accepts setpoint changes, (3) thermal derating computing an `effective_max_power` every tick, (4) power ramping limiting how fast the setpoint can move, and (5) hot-reload for two configs plus an alarm SUB socket in both loops.

The most architecturally significant addition is the ZMQ SUB socket in `ControlLoop` that subscribes to `SOCK_ALARM_PUB`. This is a new socket type for control_manager (it only had REP, PUB, PUSH before). The SUB must be polled non-blocking inside `_tick()` or `_poll_commands()` before the state machine runs, so alarm-driven protection actions feed into the same tick they arrive. This follows the `NOBLOCK` recv pattern already used on the REP socket.

The hot-reload SUB socket is a second new SUB socket on `ipc:///run/ems/config_pub.sock` (published by config_manager). Both ControlLoop and AlarmLoop need this. The swap mechanics are straightforward: replace `self._config` dict reference for ControlLoop; for AlarmLoop replace `self._evaluator._instances` after calling `build_alarm_instances()` on the new config.

**Primary recommendation:** Implement Phase 16 as three plans — (1) ControlIntelligence module (source priority, SOC limits, derating, ramping, interlocks — pure logic, no I/O), (2) ControlLoop wiring (alarm SUB socket, config reload SUB, protection action handling, RTDB writes for derating_pct), (3) AlarmLoop hot-reload (config reload SUB, atomic threshold swap).

---

## Standard Stack

### Core (no new dependencies required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| zmq (pyzmq) | already installed | SUB socket for alarm events and config reloads | All IPC uses ZMQ in this project |
| msgpack | already installed | Decode alarm events from AlarmLoop PUB | Existing serialization standard |
| jsonschema | already installed | Validate new derating/ramping config sections | Same validator used by all config loaders |
| yaml (pyyaml) | already installed | Re-read config files on hot-reload | Same loader used everywhere |
| time (stdlib) | stdlib | `time.monotonic()` for ramping delta_t | Already used in control loop |
| dataclasses (stdlib) | stdlib | ControlIntelligence result dataclass | Established pattern: TickResult |

### No New Packages

All intelligence logic uses Python stdlib + the libraries already present in `ems_control_manager` and `ems_alarm_manager`. No `uv add` needed.

---

## Architecture Patterns

### Recommended File Structure

```
src/control_manager/python/src/ems_control_manager/
├── config.py          (extend: add load helper for derating + ramping sections)
├── intelligence.py    (NEW: ControlIntelligence — pure logic, no I/O)
├── loop.py            (extend: alarm SUB, config SUB, call intelligence)
├── state_machine.py   (unchanged)
└── __main__.py        (unchanged)

src/alarm_manager/src/ems_alarm_manager/
├── config.py          (unchanged)
├── evaluator.py       (unchanged — no hot-reload logic here)
├── loop.py            (extend: config SUB, atomic threshold swap)
├── resolver.py        (unchanged)
└── __main__.py        (unchanged)

config/schemas/
├── control_config.schema.json  (extend: add derating + ramping sections)
└── (alarms_config.schema.json already covers all alarm hot-reload needs)

config/
└── control_config.yaml  (extend: add derating + ramping sections)
```

### Pattern 1: ControlIntelligence Pure Logic Class

The `intelligence.py` module follows the `ControlStateMachine` pattern exactly — pure logic, no I/O, no ZMQ, no asyncio. `ControlLoop._tick()` calls it after reading RTDB and before writing RTDB.

```python
# Source: established pattern from state_machine.py
@dataclasses.dataclass
class IntelligenceResult:
    effective_max_power_kw: float   # after derating applied
    desired_setpoint_kw: float      # after ramping applied
    active_derating_pct: float      # 0.0-100.0 — written to RTDB
    active_source: str              # "solar" | "grid" | "bess" | "dg" | "none"
    soc_cutoff_hit: bool            # True → force IDLE transition
    interlock_blocked: bool         # True → block STANDBY→dispatch transition
    protection_active: bool         # True → state machine should enter FAULT


class ControlIntelligence:
    """Pure intelligence logic: source priority, SOC limits, derating, ramping, interlocks."""

    def __init__(self, config: dict[str, Any]) -> None:
        # extract soc_limits, power_limits, source_priority, derating, ramping
        ...

    def update_config(self, new_config: dict[str, Any]) -> None:
        """Atomically swap all config fields on hot-reload."""
        ...

    def evaluate(
        self,
        now_s: float,
        raw_setpoint_kw: float,           # from state machine / operator
        pcs_state: int,
        safety_emergency: bool,
        gpio_di: list[int],               # RTDB gpio.di[0..7]
        total_soc: float,                 # RTDB system.total_soc
        bms_max_cell_t: float,            # max across all online racks
        bms_min_cell_t: float,            # min across all online racks
        pcs_temp_c: float,                # RTDB pcs.temperature
        alarm_severity: str | None,       # "warning" | "action" | "protection" | None
        current_state: int,               # current control state
        mode: str,                        # "DAY" | "NIGHT" | "MANUAL"
    ) -> IntelligenceResult:
        ...
```

**Key design choices:**
- `update_config()` replaces all internal fields atomically — no partial application risk
- `evaluate()` is called every tick and is pure: same inputs → same outputs
- `alarm_severity` is the highest-severity active alarm passed in by the loop (the loop holds the last alarm event)
- `mode` is computed by the loop from `schedule_config.yaml day_night` section and `datetime.now()`

### Pattern 2: ZMQ SUB Socket in ControlLoop

The ControlLoop gains two new SUB sockets. Both polled `NOBLOCK` before the SM tick:

```python
# In ControlLoop.__init__:
# Alarm SUB — subscribes to alarm_manager PUB
_alarm_sub_ep: str = alarm_sub_endpoint or SOCK_ALARM_PUB
self._alarm_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
self._alarm_sub.setsockopt(zmq.LINGER, 0)
self._alarm_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_ALARM)
self._alarm_sub.connect(_alarm_sub_ep)  # CONNECT not bind — alarm_manager binds

# Config reload SUB — subscribes to config_manager PUB
_config_sub_ep: str = config_sub_endpoint or SOCK_CONFIG_PUB
self._config_sub: zmq.Socket = self._zmq_ctx.socket(zmq.SUB)
self._config_sub.setsockopt(zmq.LINGER, 0)
self._config_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CONFIG_RELOAD)
self._config_sub.connect(_config_sub_ep)  # CONNECT — config_manager binds
```

The SOCK_ALARM_PUB endpoint `ipc:///run/ems/alarm_pub.sock` is already defined as a constant in `alarm_manager/loop.py`. For inter-module use, reference it as a string constant in ControlLoop's constructor params — don't import from alarm_manager (would create a circular dependency).

The config_manager PUB socket endpoint is `ipc:///run/ems/config_pub.sock` (verify against config_manager source — not `SOCK_CONFIG` which is REQ/REP).

```python
# Source: control_manager/loop.py pattern for non-blocking drain
def _poll_alarm_events(self) -> str | None:
    """Drain alarm SUB socket. Return highest severity seen this tick."""
    highest: str | None = None
    severity_rank: dict[str, int] = {"warning": 0, "action": 1, "protection": 2}
    while True:
        try:
            _topic: bytes = self._alarm_sub.recv(zmq.NOBLOCK)
            body: bytes = self._alarm_sub.recv(zmq.NOBLOCK)
            event: dict = msgpack.unpackb(body, raw=False)
            sev: str = event.get("severity", "warning")
            if highest is None or severity_rank.get(sev, 0) > severity_rank.get(highest, 0):
                highest = sev
        except zmq.Again:
            return highest
```

### Pattern 3: Derating Formula

Three zones evaluated independently, most restrictive wins:

```python
def _compute_derating_pct(
    bms_max_cell_t: float,
    bms_min_cell_t: float,
    pcs_temp_c: float,
    cfg: dict,  # derating section from control_config
) -> float:
    """Return active derating percentage (0.0-100.0). 100 = full power."""

    def _zone_factor(signal: float, start: float, cutoff: float, min_factor: float) -> float:
        if signal <= start:
            return 100.0
        if signal >= cutoff:
            return 0.0
        span = cutoff - start
        return 100.0 - (signal - start) / span * (100.0 - min_factor)

    def _zone_factor_low(signal: float, start: float, cutoff: float, min_factor: float) -> float:
        """For low-side zones: signal BELOW start triggers derating."""
        if signal >= start:
            return 100.0
        if signal <= cutoff:
            return 0.0
        span = start - cutoff
        return 100.0 - (start - signal) / span * (100.0 - min_factor)

    bms_high = _zone_factor(bms_max_cell_t, cfg["bms_high_start_c"], cfg["bms_high_cutoff_c"], 0.0)
    pcs_high = _zone_factor(pcs_temp_c, cfg["pcs_high_start_c"], cfg["pcs_high_cutoff_c"], 0.0)
    bms_low = _zone_factor_low(bms_min_cell_t, cfg["bms_low_start_c"], cfg["bms_low_cutoff_c"], 0.0)

    return min(bms_high, pcs_high, bms_low)
```

### Pattern 4: Power Ramping

Ramp logic runs every tick between the desired setpoint and the previous setpoint:

```python
def _apply_ramp(
    current_kw: float,
    desired_kw: float,
    ramp_rate_kw_s: float,
    delta_t_s: float,
) -> float:
    """Limit setpoint change to ramp_rate_kw_s * delta_t_s per tick."""
    max_step: float = ramp_rate_kw_s * delta_t_s
    delta: float = desired_kw - current_kw
    if abs(delta) <= max_step:
        return desired_kw
    return current_kw + (max_step if delta > 0 else -max_step)
```

`delta_t_s` is the actual elapsed time from the previous tick (use `time.monotonic()` diff), not the configured loop interval. This handles jitter correctly.

### Pattern 5: Hot-Reload Atomic Swap

```python
# In ControlLoop — config reload handler
def _handle_config_reload(self, config_name: str, new_config: dict) -> None:
    if config_name == "control_config":
        self._config = new_config          # atomic reference replacement
        self._intelligence.update_config(new_config)
        logger.info("control_config hot-reloaded: SOC limits and derating updated")

# In AlarmLoop — config reload handler
def _handle_config_reload(self, config_name: str, new_config: dict) -> None:
    if config_name == "alarms_config":
        self._config = new_config
        new_instances = build_alarm_instances(new_config)
        # Preserve lifecycle state for still-active alarms
        for alarm_id, inst in self._evaluator._instances.items():
            if alarm_id in new_instances and inst.state != "NORMAL":
                new_instances[alarm_id].state = inst.state
                new_instances[alarm_id].activated_at = inst.activated_at
                new_instances[alarm_id].acknowledged_at = inst.acknowledged_at
                new_instances[alarm_id].exceeded_since_ms = inst.exceeded_since_ms
        self._evaluator._instances = new_instances  # atomic
        logger.info("alarms_config hot-reloaded: %d rules updated", len(new_instances))
```

### Pattern 6: SOC Guard in ControlLoop._tick()

SOC limits are checked after reading RTDB but before calling the state machine. If hit, force IDLE transition:

```python
# In _tick(), after RTDB reads, before SM tick
system_copy = _seqlock_read_section(self._rtdb.system)
soc: float = system_copy.total_soc
charge_cutoff: float = self._config["soc_limits"]["charge_cutoff_pct"]
discharge_cutoff: float = self._config["soc_limits"]["discharge_cutoff_pct"]

if self._sm.state == STATE_CHARGING and soc >= charge_cutoff:
    logger.info("SOC charge cutoff reached (%.1f%% >= %.1f%%) — transitioning to IDLE", soc, charge_cutoff)
    self._sm.request_mode_change("idle")

if self._sm.state == STATE_DISCHARGING and soc <= discharge_cutoff:
    logger.info("SOC discharge cutoff reached (%.1f%% <= %.1f%%) — transitioning to IDLE", soc, discharge_cutoff)
    self._sm.request_mode_change("idle")
```

### Pattern 7: Interlock Guard

STANDBY→dispatch interlock is enforced in `_dispatch_command` for `manual_setpoint` and in `_tick()` for scheduler-driven setpoints. The guard rejects setpoint application if safety emergency or PCS not online:

```python
# In _tick() before applying any pending dispatch:
if self._sm.state == STATE_STANDBY and self._pending_dispatch_kw is not None:
    pcs_online: bool = pcs_copy.state == PCS_STATE_RUNNING
    if not pcs_online:
        logger.warning("Interlock: PCS not online — dispatch blocked")
        # Don't apply setpoint, stay in STANDBY
    elif safety_emergency:
        logger.warning("Interlock: safety emergency active — dispatch blocked")
    else:
        self._sm.request_manual_setpoint(self._pending_dispatch_kw)
        self._pending_dispatch_kw = None
```

### Anti-Patterns to Avoid

- **REQ/REP for alarm-to-control:** Blocking — if control_manager is slow, alarm_manager deadlocks waiting for reply. Use PUB/SUB only.
- **Mutating config dict in place on hot-reload:** Not atomic. Replace reference (`self._config = new_config`), don't do `self._config.update(new_config)`.
- **Importing SOCK_ALARM_PUB from alarm_manager:** Creates circular dependency between packages. Define the endpoint string as a constructor default in ControlLoop.
- **Applying ramp using configured loop_interval_ms:** Use actual `time.monotonic()` delta — tick jitter accumulates otherwise.
- **Calling `_seqlock_read_section` on clusters in ControlLoop:** Phase 14 ControlLoop only reads `pcs`, `gpio`, `system` sections. For derating, need to read cluster/rack data. Follow the AlarmLoop `_RtdbCopy/_ClusterCopy/_RackCopy` pattern exactly.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Config validation on reload | Custom YAML parser | `load_control_config()` / `load_alarm_config()` | Already handles schema validation, raises ValueError on failure |
| Alarm state serialization | Custom encode | `msgpack.unpackb(body, raw=False)` | AlarmLoop PUB emits raw msgpack (not wrapped in encode_event envelope) — see loop.py line 361 |
| Day/Night time comparison | Custom timezone logic | `datetime.now().time()` vs `time.fromisoformat()` | Simple HH:MM comparison using stdlib datetime; no timezone needed (local time) |
| Seqlock read for BMS temps | New helper function | Copy `_RackCopy/_ClusterCopy/_RtdbCopy` pattern from alarm_manager/loop.py | Already battle-tested with MAX_CLUSTERS/MAX_RACKS_PER_CLUSTER iteration |

**Key insight:** The alarm_manager already solved the "read BMS temps from all racks via seqlock" problem in its `_RtdbCopy` hierarchy. ControlIntelligence needs max/min cell temp across all online racks — this is the same computation as `bms.cell_temp_max` and `bms.cell_temp_min` in the alarm resolver. Reuse the pattern.

---

## Common Pitfalls

### Pitfall 1: AlarmLoop PUB Payload Is Raw msgpack, Not Wrapped Event

**What goes wrong:** Calling `decode_event()` on the alarm PUB message body fails with KeyError because `encode_event()` creates a wrapped envelope, but the AlarmLoop PUB sends `msgpack.packb(event, use_bin_type=True)` directly (see `alarm_manager/loop.py` line 361).
**Why it happens:** Two different serialization patterns coexist: `encode_event` for PUSH→logger, raw msgpack for PUB→subscribers.
**How to avoid:** In ControlLoop's alarm SUB drain, use `msgpack.unpackb(body, raw=False)` directly, not `decode_event()`.
**Warning signs:** KeyError on `msg["event_type"]` or `msg["src"]` when consuming alarm PUB messages.

### Pitfall 2: Config Reload SUB Socket Endpoint

**What goes wrong:** config_manager publishes reload events on `ipc:///run/ems/config_pub.sock`, not on `SOCK_CONFIG` (`ipc:///run/ems/config.sock`). `SOCK_CONFIG` is the REQ/REP query socket.
**Why it happens:** Two config_manager sockets exist — REQ/REP for queries, PUB for reload events. Only one is in `ipc.py`.
**How to avoid:** Check `config_manager/manager.py` for the actual PUB bind address. Define `SOCK_CONFIG_PUB` locally in each loop that needs it (following the SOCK_CONTROL_PUB / SOCK_ALARM_PUB precedent).
**Warning signs:** SUB socket receives nothing; config changes never trigger hot-reload in test.

### Pitfall 3: Cluster RTDB Read in ControlLoop

**What goes wrong:** ControlLoop currently only reads `pcs`, `gpio`, `system` sections. Thermal derating needs BMS rack temperatures. Adding a raw loop over `self._rtdb.clusters[c].racks[r]` without the seqlock copy pattern causes torn reads.
**Why it happens:** The existing `_tick()` is simple — three sections, three reads. Adding cluster iteration without the `_RackCopy` wrapper is easy to forget.
**How to avoid:** Create `_BmsThermalSnapshot` in loop.py (or intelligence.py) that replicates the `_RackCopy` iteration from alarm_manager. Only copy `online`, `max_cell_t`, `min_cell_t` — nothing else needed.
**Warning signs:** Intermittent NaN or wildly incorrect temperature readings; test flakiness when simulating concurrent writes.

### Pitfall 4: Protection Cooldown Oscillation

**What goes wrong:** Protection alarm fires → control goes to FAULT → signal recovers (because PCS is off) → alarm clears → control retries → signal exceeds again on next dispatch → alarm fires again. Loop every ~60 seconds.
**Why it happens:** The 60-second fault cooldown in the state machine already handles auto-retry timing, but if `alarm_severity` is consumed and cleared each tick, the protection state is lost on tick boundary.
**How to avoid:** ControlLoop must maintain `self._last_alarm_severity` as persistent state (not reset every tick). Only reset it after the 60-second cooldown expires and the FAULT state resolves.
**Warning signs:** Repeated FAULT→IDLE→FAULT cycling in logs at regular intervals.

### Pitfall 5: SOC Guard Race With State Machine Pending Commands

**What goes wrong:** SOC guard calls `request_mode_change("idle")` on the state machine while there is already a `_pending_target_state` set. The mode change is rejected ("PCS transition in progress"), and the system stays in CHARGING above the SOC cutoff.
**Why it happens:** The state machine uses a single `_pending_target_state` slot; a second request while sub-state is active is rejected.
**How to avoid:** SOC guard should only fire when SM is in a stable state (not STARTING/STOPPING sub-state). Check `self._sm._sub_state == _SubState.NONE` before calling. Alternatively, zero the setpoint and let the SM reach STANDBY naturally before forcing IDLE.
**Warning signs:** "Command rejected: PCS STARTING in progress" appears in logs at the SOC boundary.

### Pitfall 6: Day/Night Mode Computed Every Tick vs Cached

**What goes wrong:** Reading and parsing `schedule_config.yaml` every tick to determine DAY/NIGHT mode is unnecessary I/O in the hot path.
**Why it happens:** The day_night section is in `schedule_config.yaml`, not `control_config.yaml`. There is no hot-reload subscription for `schedule_config` in Phase 16 scope.
**How to avoid:** Cache `day_start`/`night_start` times on startup. Re-read from schedule_config only if a schedule_config reload event arrives (not in scope for Phase 16) or add them to the constructor. Use `datetime.now().time()` comparison against cached `time` objects each tick — pure Python, no I/O.
**Warning signs:** File handles accumulating; disk activity visible during 1Hz loop profiling.

---

## Code Examples

Verified patterns from project source code:

### RTDB Cluster Read (from alarm_manager/loop.py)

```python
# Source: src/alarm_manager/src/ems_alarm_manager/loop.py lines 377-423
class _RackCopy:
    __slots__ = ("online", "max_cell_t", "min_cell_t", ...)

    def __init__(self, rack: Any) -> None:
        rack_copy = _seqlock_read_section(rack)
        self.online: int = int(rack_copy.online)
        self.max_cell_t: float = float(rack_copy.max_cell_t)
        self.min_cell_t: float = float(rack_copy.min_cell_t)

class _ClusterCopy:
    def __init__(self, cluster: Any) -> None:
        self.racks: list[_RackCopy] = [
            _RackCopy(cluster.racks[r]) for r in range(MAX_RACKS_PER_CLUSTER)
        ]
```

### ZMQ NOBLOCK Drain (from control_manager/loop.py)

```python
# Source: src/control_manager/python/src/ems_control_manager/loop.py lines 229-234
while True:
    try:
        raw: bytes = self._rep.recv(zmq.NOBLOCK)
    except zmq.Again:
        return  # No more pending messages
```

### Config Reload Event Structure (from config_manager/manager.py)

The `config_reload` event published by config_manager has this data dict structure:
```python
# Source: src/config_manager/src/ems_config_manager/manager.py line 588
data={
    "name": name,           # "control_config" or "alarms_config"
    "config": new_data,     # full validated config dict
    "diff": diff,           # dict of changed keys
}
```
The `event_type` field on the outer envelope is `"config_reload"` matching `TOPIC_CONFIG_RELOAD`.

### AlarmLoop PUB Event Structure

```python
# Source: src/alarm_manager/src/ems_alarm_manager/loop.py line 361
pub_payload: bytes = msgpack.packb(event, use_bin_type=True)
# event dict keys: event_type, alarm_id, signal, severity, state, value, threshold
# Severity values: "warning" | "action" | "protection"
```

### Seqlock Write Pattern (from control_manager/loop.py)

```python
# Source: src/control_manager/python/src/ems_control_manager/loop.py lines 344-353
sys = self._rtdb.system
sys.lock.sequence += 1          # begin write (odd)
sys.control_state = result.state
sys.active_setpoint_kw = result.setpoint_kw
sys.active_derating_pct = derating_pct  # NEW in Phase 16
sys.last_update_ms = _now_ms()
sys.lock.sequence += 1          # end write (even)
```

### Config Schema Extension Pattern

```json
// Extend control_config.schema.json to add derating section
"derating": {
    "type": "object",
    "required": ["bms_high_start_c", "bms_high_cutoff_c",
                 "pcs_high_start_c", "pcs_high_cutoff_c",
                 "bms_low_start_c", "bms_low_cutoff_c"],
    "additionalProperties": false,
    "properties": {
        "bms_high_start_c":  {"type": "number", "x-mutable": true},
        "bms_high_cutoff_c": {"type": "number", "x-mutable": true},
        "pcs_high_start_c":  {"type": "number", "x-mutable": true},
        "pcs_high_cutoff_c": {"type": "number", "x-mutable": true},
        "bms_low_start_c":   {"type": "number", "x-mutable": true},
        "bms_low_cutoff_c":  {"type": "number", "x-mutable": true}
    }
},
"ramping": {
    "type": "object",
    "required": ["charge_ramp_kw_s", "discharge_ramp_kw_s"],
    "additionalProperties": false,
    "properties": {
        "charge_ramp_kw_s":    {"type": "number", "minimum": 0.1, "x-mutable": true},
        "discharge_ramp_kw_s": {"type": "number", "minimum": 0.1, "x-mutable": true}
    }
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Alarm-to-control via REQ/REP (blocking) | PUB/SUB fire-and-forget | Decided in CONTEXT.md Phase 16 | Eliminates deadlock risk between alarm and control loops |
| Source priority as hard-coded switch | Configurable waterfall array in control_config.yaml | Phase 14 config design | Operator can change dispatch order via config or ZMQ command |
| Derating thresholds as magic numbers | Configurable `derating` section in control_config.yaml with x-mutable: true | Phase 16 | Hot-reloadable at runtime without restart |

**RTDB fields already reserved for Phase 16:**

- `system.active_derating_pct` (c_float) — already in `EmsSystem._fields_` with the comment "used in Phase 16"
- `system.source_priority` (c_int) — already in `EmsSystem._fields_`

These fields exist in the RTDB struct today. Phase 16 is the first to write them.

---

## Open Questions

1. **Config Manager PUB Socket Endpoint**
   - What we know: config_manager publishes `config_reload` events; `SOCK_CONFIG` in ipc.py is the REQ/REP query socket
   - What's unclear: The exact endpoint string for config_manager's PUB socket (not visible in the files read)
   - Recommendation: In Wave 0/Plan 1, grep config_manager source for the PUB bind address. Likely `ipc:///run/ems/config_pub.sock` — define it locally in loop.py as `SOCK_CONFIG_PUB = "ipc:///run/ems/config_pub.sock"` following project precedent

2. **schedule_config.yaml Hot-Reload for Day/Night**
   - What we know: day/night boundary is in schedule_config.yaml; this file is hot-reloadable per CLAUDE.md; Phase 16 subscribes to config_reload events
   - What's unclear: Whether a schedule_config reload event should also update the day/night boundary cached in ControlLoop
   - Recommendation: Handle `config_name == "schedule_config"` in the config reload handler to update the cached `day_start`/`night_start` times — simple addition, avoids stale day/night mode after site config change

3. **RTDB system.dg_available Field**
   - What we know: Source priority waterfall checks `rtdb.system.dg_available` for DG source; DG is read-only in M2
   - What's unclear: Whether `dg_available` field exists in EmsSystem._fields_ or needs to be added
   - Recommendation: In Plan 1 Wave 0, check `EmsSystem._fields_` against `ems_types.h`. If absent, use `False` as constant sentinel for M2 (DG always unavailable in this phase)

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (confirmed: 76 control_manager tests, 61 alarm_manager tests passing) |
| Config file | `pyproject.toml` per workspace member (no pytest.ini) |
| Quick run command | `uv run --all-packages pytest src/control_manager/python/tests src/alarm_manager/tests -x -q` |
| Full suite command | `uv run --all-packages pytest src/control_manager src/alarm_manager -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CTRL-04 | DAY mode waterfall: solar > grid > bess > dg | unit | `pytest src/control_manager/python/tests/test_intelligence.py -x -k "source_priority"` | Wave 0 |
| CTRL-04 | NIGHT mode waterfall: grid > bess > dg | unit | `pytest ... -k "night_mode"` | Wave 0 |
| CTRL-04 | MANUAL mode bypasses priority | unit | `pytest ... -k "manual_mode"` | Wave 0 |
| CTRL-04 | No source available → force IDLE | unit | `pytest ... -k "no_source"` | Wave 0 |
| CTRL-05 | SOC charge cutoff → IDLE transition | unit | `pytest src/control_manager/python/tests/test_loop.py -k "soc_charge_cutoff"` | Wave 0 |
| CTRL-05 | SOC discharge cutoff → IDLE transition | unit | `pytest ... -k "soc_discharge_cutoff"` | Wave 0 |
| CTRL-06 | BMS high temp → linear derating | unit | `pytest ... -k "derating_bms_high"` | Wave 0 |
| CTRL-06 | PCS high temp → linear derating | unit | `pytest ... -k "derating_pcs_high"` | Wave 0 |
| CTRL-06 | BMS low temp → cold derating (lithium plating prevention) | unit | `pytest ... -k "derating_bms_low"` | Wave 0 |
| CTRL-06 | Most restrictive zone wins | unit | `pytest ... -k "derating_multi_zone"` | Wave 0 |
| CTRL-06 | active_derating_pct written to RTDB | unit | `pytest ... -k "derating_rtdb_write"` | Wave 0 |
| CTRL-08 | Setpoint change limited to ramp_rate_kw_s | unit | `pytest ... -k "ramp_charge"` | Wave 0 |
| CTRL-08 | Ramp-down to zero on FAULT | unit | `pytest ... -k "ramp_fault"` | Wave 0 |
| CTRL-09 | PCS not online → dispatch blocked | unit | `pytest ... -k "interlock_pcs_offline"` | Wave 0 |
| CTRL-09 | Safety emergency → dispatch blocked | unit | `pytest ... -k "interlock_emergency"` | Wave 0 |
| CTRL-11 | control_config reload → SOC limits updated in-loop | unit | `pytest ... -k "hot_reload_soc"` | Wave 0 |
| CTRL-11 | control_config reload → derating thresholds updated | unit | `pytest ... -k "hot_reload_derating"` | Wave 0 |
| ALM-08 | protection alarm → ControlLoop enters FAULT | unit | `pytest src/control_manager/python/tests/test_loop.py -k "alarm_protection"` | Wave 0 |
| ALM-08 | action alarm → 50% power reduction | unit | `pytest ... -k "alarm_action"` | Wave 0 |
| ALM-08 | 60-second cooldown prevents snap-back | unit | `pytest ... -k "alarm_cooldown"` | Wave 0 |
| ALM-09 | alarms_config reload → thresholds updated in-loop | unit | `pytest src/alarm_manager/tests/test_loop.py -k "hot_reload"` | Wave 0 |
| ALM-09 | alarms_config reload → active alarm lifecycle preserved | unit | `pytest ... -k "hot_reload_lifecycle"` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run --all-packages pytest src/control_manager/python/tests src/alarm_manager/tests -x -q`
- **Per wave merge:** `uv run --all-packages pytest src/control_manager src/alarm_manager -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `src/control_manager/python/tests/test_intelligence.py` — covers CTRL-04, CTRL-05, CTRL-06, CTRL-08, CTRL-09 (pure logic tests for ControlIntelligence class)
- [ ] Additional test methods in `src/control_manager/python/tests/test_loop.py` — covers CTRL-11, ALM-08
- [ ] Additional test methods in `src/alarm_manager/tests/test_loop.py` — covers ALM-09
- [ ] `derating` and `ramping` sections in `config/control_config.yaml` (test fixture dependency)
- [ ] Extended `control_config.schema.json` (required before config loader tests pass)

---

## Sources

### Primary (HIGH confidence — read from project source)

- `src/control_manager/python/src/ems_control_manager/loop.py` — ControlLoop architecture, ZMQ socket patterns, RTDB seqlock write, `_poll_commands()` NOBLOCK drain pattern
- `src/control_manager/python/src/ems_control_manager/state_machine.py` — TickResult dataclass, pure logic pattern, state constants
- `src/alarm_manager/src/ems_alarm_manager/loop.py` — AlarmLoop architecture, `_RtdbCopy` cluster read pattern, PUB payload format (raw msgpack)
- `src/alarm_manager/src/ems_alarm_manager/evaluator.py` — AlarmInstance, severity constants, IEC 62682 lifecycle states
- `src/common/python/src/ems_common/ipc.py` — TOPIC_CONFIG_RELOAD, TOPIC_ALARM, SOCK_ALARM_CMD, all IPC constants
- `src/common/python/src/ems_common/rtdb.py` — EmsSystem._fields_ with `active_derating_pct` and `source_priority` already present
- `config/control_config.yaml` — existing soc_limits, power_limits, source_priority sections
- `config/schedule_config.yaml` — day_night section with day_start/night_start
- `config/schemas/control_config.schema.json` — full schema with additionalProperties: false (must extend for derating + ramping)
- `config/alarms_config.yaml` — 9 alarm rules with severity, threshold, signal fields
- `src/config_manager/src/ems_config_manager/manager.py` — config_reload event data structure: `{name, config, diff}`
- `.planning/phases/16-control-intelligence/16-CONTEXT.md` — locked decisions, all architectural choices

### Secondary (MEDIUM confidence)

- `.planning/STATE.md` — key decisions from M0+M1+M2 (SOCK_ALARM_PUB defined locally in loop.py per precedent; _seqlock_read_section duplicated, not shared)
- `.planning/REQUIREMENTS.md` — CTRL-04 through CTRL-11, ALM-08, ALM-09 requirement text

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — no new libraries, all existing project patterns
- Architecture: HIGH — all patterns derived directly from project source code
- Pitfalls: HIGH — derived from actual code paths and established project decisions
- Derating thresholds: HIGH — specified in CONTEXT.md with IEC 62619 and PCS V1.24 references
- config_manager PUB socket endpoint: MEDIUM — endpoint string not confirmed by reading config_manager source; flagged as Open Question

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable project; all code is local, no external dependency churn)
