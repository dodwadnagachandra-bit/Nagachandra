# Architecture Patterns -- M2 Control & Alarms

**Domain:** BESS Control Manager (1Hz state machine) + Alarm Manager (IEC 62682)
**Researched:** 2026-03-14
**Confidence:** HIGH (based on M1 completed infrastructure, existing RTDB/IPC contracts, architecture spec v3.4)

## Recommended Architecture

M2 adds two L4 Application modules on top of M1's completed L1-L3 stack. Both are pure Python, run at 1Hz, and communicate through the existing RTDB shared memory and ZMQ IPC infrastructure. They are independent systemd services that can crash and restart without affecting safety (L1) or communications (L2).

### System Overview (M2 additions highlighted)

```
                        M1 Infrastructure (running)
┌──────────────────────────────────────────────────────────────────┐
│  config_manager  data_manager  safety_manager  comm_manager  logger
│  (Python)        (C+Python)    (C)             (C+Python)    (Python)
│  YAML/hot-rel    RTDB/PUB      GPIO/WDT        CAN/Modbus    Parquet/JSONL
└──────────────────────────────────────────────────────────────────┘
         |              |              |              |             |
         v              v              v              v             v
    config_reload   RTDB (shm)    gpio di/do      bms/pcs data   event sink
         |              |              |              |             |
         +------+-------+--------------+--------------+            |
                |                                                  |
    ┌───────────v───────────┐    ┌────────────────────┐           |
    │   *** alarm_manager *** │    │ *** control_manager *** │           |
    │   (Python, L4)        │    │ (Python, L4)          │           |
    │                       │    │                       │           |
    │  1Hz evaluate rules   │    │  1Hz state machine    │           |
    │  Hysteresis + delay   │    │  Source priority       │           |
    │  IEC 62682 lifecycle  │    │  PCS commands          │           |
    │  ACK/shelve via ZMQ   │    │  SOC/temp derating     │           |
    │                       │    │  Safety interlocks     │           |
    │  Reads: RTDB bms/pcs  │    │  Reads: RTDB all       │           |
    │  Writes: none (events │    │  Writes: RTDB system   │           |
    │          via ZMQ)     │    │  Commands: REQ/REP     │           |
    └───────────┬───────────┘    └───────────┬───────────┘           |
                │ alarm events                │ state events          |
                │ (ZMQ PUSH)                  │ (ZMQ PUSH)           |
                +────────────────+────────────+───────────────>──────+
                                 │
                          ZMQ PUB/SUB
                     (alarm_manager publishes
                      protection alarms;
                      control_manager subscribes)
```

## Component Boundaries

| Component | Responsibility | Writes To | Reads From | Communicates With |
|-----------|---------------|-----------|------------|-------------------|
| control_manager | State machine, dispatch, PCS commands, derating | RTDB system section | RTDB all sections, ZMQ telemetry SUB, ZMQ alarm SUB | comm_manager (via RTDB), alarm_manager (via ZMQ SUB), HMI/cloud (ZMQ REQ/REP), logger (ZMQ PUSH) |
| alarm_manager | Threshold evaluation, alarm lifecycle, ACK | None (in-memory alarm state) | RTDB bms/pcs/meter, ZMQ telemetry SUB | control_manager (via ZMQ PUB), HMI (ZMQ REQ/REP), logger (ZMQ PUSH) |

## New Components

### 1. control_manager

#### State Machine

```
                                    ┌──────────────┐
                          startup   │    IDLE      │
                       +----------->│ (no PCS)     │
                       │            └──────┬───────┘
                       │                   │ PCS online + no faults
                       │            ┌──────v───────┐
                       │            │   STANDBY    │
                       │            │ (PCS off,    │
                       │            │  monitoring) │
                       │            └──┬───────┬───┘
                       │   charge cmd  │       │ discharge cmd
                       │         ┌─────v──┐  ┌─v────────┐
                       │         │ PCS_   │  │ PCS_      │
                       │         │ STARTING│  │ STARTING  │
                       │         │ (10s)  │  │ (10s)     │
                       │         └───┬────┘  └──┬────────┘
                       │             │ timer     │ timer
                       │      ┌──────v──┐  ┌────v──────┐
                       │      │CHARGING │  │DISCHARGING│
                       │      │ (power  │  │ (power    │
                       │      │  active)│  │  active)  │
                       │      └──┬──────┘  └──┬────────┘
                       │         │             │
                       │    stop/limit/fault   │
                       │         │             │
                       │      ┌──v─────────────v──┐
                       │      │    PCS_STOPPING    │
                       │      │ (ramp to 0 + 10s) │
                       │      └──────┬─────────────┘
                       │             │ timer
                       │             v
                       │         (back to STANDBY)
                       │
                       │ unrecoverable fault
                       │         ┌──────────┐
                       └─────────│  FAULT   │
                                 │ (latched)│
                                 └──────────┘
                                    ^ operator reset
                                    | -> IDLE
```

**States (enum):**

| State | Entry Condition | Exit Condition | PCS State | Power |
|-------|----------------|----------------|-----------|-------|
| IDLE | Startup, or PCS not online | PCS comes online, no faults | Off | 0 |
| STANDBY | PCS online, no active protection alarms, no safety events | Charge/discharge command or schedule trigger | Off | 0 |
| PCS_STARTING | Transition from STANDBY when power requested | 10s timer expires | Turning on (0x0291=1 sent) | 0 |
| CHARGING | PCS started, negative setpoint | SOC limit, protection alarm, safety event, stop command | On | < 0 kW |
| DISCHARGING | PCS started, positive setpoint | SOC limit, protection alarm, safety event, stop command | On | > 0 kW |
| PCS_STOPPING | Ramp power to zero, then turn PCS off after 10s | 10s timer after power reaches 0 | Turning off | Ramping to 0 |
| FAULT | PCS fault persists after fault_retry_count attempts, or unrecoverable error | Operator fault_reset command | Off | 0 |

**Transition guards (checked every cycle):**

```python
class TransitionGuard:
    """Pre-condition checks for state transitions."""

    def can_enter_standby(self, ctx: ControlContext) -> tuple[bool, str]:
        """IDLE -> STANDBY requires PCS online and no safety events."""
        if not ctx.pcs_online:
            return False, "PCS offline"
        if ctx.safety_active:
            return False, "Safety event active"
        if ctx.protection_alarm_active:
            return False, "Protection alarm active"
        return True, ""

    def can_start_pcs(self, ctx: ControlContext) -> tuple[bool, str]:
        """STANDBY -> PCS_STARTING requires no interlocks."""
        if ctx.estop_active:
            return False, "E-Stop active"
        if ctx.fire_active:
            return False, "Fire detected"
        if ctx.flood_active:
            return False, "Flood detected"
        if not ctx.acdb_closed:
            return False, "Grid disconnected (ACDB open)"
        return True, ""
```

#### 1Hz Control Loop Structure

```python
async def control_loop(self) -> None:
    """Main 1Hz control loop. Never exits unless shutdown."""
    while self._running:
        cycle_start = time.monotonic()

        # 1. Read RTDB snapshot (all sections, seqlock-safe)
        ctx = self._read_rtdb_snapshot()

        # 2. Check for config changes
        self._check_config_reload()

        # 3. Evaluate safety interlocks
        self._evaluate_safety(ctx)

        # 4. Run state machine (transitions + actions)
        self._run_state_machine(ctx)

        # 5. Calculate power setpoint (dispatch + derating)
        setpoint = self._calculate_setpoint(ctx)

        # 6. Write setpoint to RTDB system section
        self._write_rtdb_setpoint(setpoint)

        # 7. Publish state if changed
        if self._state_changed:
            self._publish_state_event()

        # 8. Sleep remainder of cycle
        elapsed = time.monotonic() - cycle_start
        sleep_time = max(0, self._interval_s - elapsed)
        if elapsed > self._interval_s * 0.5:
            logger.warning("Control cycle took %.1fms", elapsed * 1000)
        await asyncio.sleep(sleep_time)
```

#### Source Priority Dispatch Algorithm

```python
def _calculate_setpoint(self, ctx: ControlContext) -> float:
    """Calculate PCS power setpoint based on source priority and limits."""
    if self._state not in (State.CHARGING, State.DISCHARGING):
        return 0.0

    # Determine load requirement (from meter or schedule)
    load_kw = ctx.meter_active_power  # Positive = import from grid

    # Walk priority list
    priority = self._config.source_priority.day_order if ctx.is_daytime \
               else self._config.source_priority.night_order

    remaining_load = load_kw
    bess_setpoint = 0.0

    for source in priority:
        if remaining_load <= 0:
            break
        if source == "solar" and ctx.solar_available:
            remaining_load -= ctx.solar_power_kw
        elif source == "grid" and ctx.grid_available:
            remaining_load = 0  # Grid absorbs remainder
        elif source == "bess":
            # BESS discharges to cover remaining load
            bess_setpoint = min(remaining_load, self._max_discharge_kw(ctx))
            remaining_load -= bess_setpoint
        elif source == "dg" and ctx.dg_available:
            remaining_load = 0  # DG covers remainder

    # Apply limits: SOC, temperature, BMS current, ramp rate
    bess_setpoint = self._apply_derating(bess_setpoint, ctx)
    bess_setpoint = self._apply_ramp_rate(bess_setpoint)

    return bess_setpoint
```

#### SOC Derating Curve (piecewise-linear)

```
Power %
100% |████████████████████────────────
     |                    \
 75% |                     \
     |                      \
 50% |                       \
     |                        \
 25% |                         \
     |                          \
  0% |───────────────────────────\──
     0%   10%  20%  ...  80%  90% 95% 100%  SOC

Charge derating:
  SOC < 80%: 100% power
  SOC 80-95%: linear ramp from 100% to 0%
  SOC >= 95%: 0% (cutoff)

Discharge derating:
  SOC > 20%: 100% power
  SOC 20-10%: linear ramp from 100% to 0%
  SOC <= 10%: 0% (cutoff)
```

Implementation with stdlib `bisect`:

```python
def _apply_soc_derating(self, power_kw: float, soc: float,
                         is_charging: bool) -> float:
    """Apply SOC-based derating using piecewise-linear interpolation."""
    if is_charging:
        # Charge derating breakpoints: (SOC%, power_factor)
        breakpoints = self._config.charge_derating  # [(80, 1.0), (95, 0.0)]
    else:
        breakpoints = self._config.discharge_derating  # [(20, 1.0), (10, 0.0)]

    soc_points = [bp[0] for bp in breakpoints]
    power_factors = [bp[1] for bp in breakpoints]

    # Linear interpolation
    idx = bisect.bisect_right(soc_points, soc)
    if idx == 0:
        factor = power_factors[0]
    elif idx >= len(soc_points):
        factor = power_factors[-1]
    else:
        x0, x1 = soc_points[idx - 1], soc_points[idx]
        y0, y1 = power_factors[idx - 1], power_factors[idx]
        factor = y0 + (y1 - y0) * (soc - x0) / (x1 - x0)

    return power_kw * max(0.0, min(1.0, factor))
```

#### Temperature Derating (LFP cells)

| Condition | Charge Derating | Discharge Derating |
|-----------|-----------------|-------------------|
| T < -10C | 0% (no charge) | 25% |
| -10C <= T < 0C | 0% (no charge -- lithium plating risk) | 50% |
| 0C <= T < 5C | 25% | 75% |
| 5C <= T < 10C | 50% | 100% |
| 10C <= T < 45C | 100% | 100% |
| 45C <= T < 50C | 50% | 75% |
| 50C <= T < 55C | 25% | 50% |
| T >= 55C | 0% (protection) | 0% (protection) |

**Critical:** Charging LFP cells below 0C causes lithium plating -- irreversible damage. This must be a hard interlock, not just derating.

### 2. alarm_manager

#### Alarm Lifecycle State Machine (per IEC 62682)

```
                        ┌────────────────────┐
                        │      NORMAL        │
                        │  (no alarm active) │
                        └─────────┬──────────┘
                                  │ threshold exceeded
                                  │ + delay timer expired
                        ┌─────────v──────────┐
                        │  ACTIVE_UNACKED    │
                        │  (alarm raised,    │
                        │   not acknowledged)│
                        └──┬──────────────┬──┘
              operator ACK │              │ signal returns to normal
                     ┌─────v────┐  ┌──────v───────────┐
                     │  ACTIVE_ │  │ CLEARED_UNACKED  │
                     │  ACKED   │  │ (condition gone,  │
                     │          │  │  still needs ACK) │
                     └─────┬────┘  └──────┬────────────┘
          signal returns   │              │ operator ACK
          to normal        │              │
                     ┌─────v────┐         │
                     │  RTN     │<────────┘
                     │ (return  │
                     │  to norm)│
                     └─────┬────┘
                           │ auto-clear (after brief hold)
                           v
                        NORMAL
```

**States per alarm instance (enum):**

| State | Meaning | Indicator | Needs ACK |
|-------|---------|-----------|-----------|
| NORMAL | Signal within limits | None | No |
| ACTIVE_UNACKED | Threshold exceeded, operator not yet aware | Flashing (HMI) | Yes |
| ACTIVE_ACKED | Threshold exceeded, operator acknowledged | Steady (HMI) | No |
| CLEARED_UNACKED | Signal returned to normal but operator never acknowledged | Dim (HMI) | Yes |
| RTN | Return-to-normal, transitioning back | Brief (HMI) | No |

#### Alarm Rule Evaluator

```python
@dataclass
class AlarmRule:
    """Configuration for a single alarm rule."""
    alarm_id: str                    # e.g., "cell_voltage_high"
    signal_path: str                 # e.g., "bms.cell_voltage_max"
    severity: AlarmSeverity          # WARNING, ACTION, PROTECTION
    high_threshold: float | None     # Trigger above this value
    low_threshold: float | None      # Trigger below this value
    hysteresis: float                # Deadband for clear (absolute, not %)
    delay_ms: int                    # Activation delay in milliseconds
    enabled: bool                    # Can be disabled at runtime

@dataclass
class AlarmState:
    """Runtime state for a single alarm instance."""
    rule: AlarmRule
    state: AlarmLifecycle = AlarmLifecycle.NORMAL
    activated_at: float | None = None
    acknowledged_at: float | None = None
    cleared_at: float | None = None
    current_value: float = 0.0
    delay_timer_start: float | None = None  # When threshold was first exceeded
    shelved_until: float | None = None       # Shelve expiry time (monotonic)
```

#### Threshold Evaluation with Hysteresis and Delay

```python
def evaluate(self, value: float, now_mono: float) -> AlarmLifecycle | None:
    """Evaluate one alarm rule. Returns new state if changed, None if no change."""
    self.current_value = value

    if self.shelved_until and now_mono < self.shelved_until:
        return None  # Shelved, skip evaluation

    threshold_exceeded = self._is_threshold_exceeded(value)
    threshold_clear = self._is_threshold_clear(value)

    if self.state == AlarmLifecycle.NORMAL:
        if threshold_exceeded:
            if self.delay_timer_start is None:
                self.delay_timer_start = now_mono  # Start delay timer
            elif (now_mono - self.delay_timer_start) * 1000 >= self.rule.delay_ms:
                self.delay_timer_start = None
                self.state = AlarmLifecycle.ACTIVE_UNACKED
                self.activated_at = time.time()
                return self.state
        else:
            self.delay_timer_start = None  # Reset delay timer

    elif self.state == AlarmLifecycle.ACTIVE_UNACKED:
        if threshold_clear:
            self.state = AlarmLifecycle.CLEARED_UNACKED
            self.cleared_at = time.time()
            return self.state

    elif self.state == AlarmLifecycle.ACTIVE_ACKED:
        if threshold_clear:
            self.state = AlarmLifecycle.RTN
            self.cleared_at = time.time()
            return self.state

    elif self.state == AlarmLifecycle.CLEARED_UNACKED:
        pass  # Waits for operator ACK

    elif self.state == AlarmLifecycle.RTN:
        # Auto-transition to NORMAL after brief hold
        self.state = AlarmLifecycle.NORMAL
        self._reset()
        return self.state

    return None

def _is_threshold_exceeded(self, value: float) -> bool:
    """Check if value exceeds threshold (high or low)."""
    if self.rule.high_threshold is not None and value > self.rule.high_threshold:
        return True
    if self.rule.low_threshold is not None and value < self.rule.low_threshold:
        return True
    return False

def _is_threshold_clear(self, value: float) -> bool:
    """Check if value has returned within hysteresis band."""
    if self.rule.high_threshold is not None:
        return value < (self.rule.high_threshold - self.rule.hysteresis)
    if self.rule.low_threshold is not None:
        return value > (self.rule.low_threshold + self.rule.hysteresis)
    return True
```

#### Signal Reading from RTDB

The alarm_manager must map signal paths (from alarms_config.yaml) to RTDB field reads:

```python
SIGNAL_READERS: dict[str, Callable[[EmsRtdb], float]] = {
    "bms.cell_voltage_max": lambda rtdb: _max_across_racks(
        rtdb, lambda rack: rack.max_cell_v),
    "bms.cell_voltage_min": lambda rtdb: _min_across_racks(
        rtdb, lambda rack: rack.min_cell_v),
    "bms.cell_temp_max": lambda rtdb: _max_across_racks(
        rtdb, lambda rack: rack.max_cell_t),
    "bms.cell_temp_min": lambda rtdb: _min_across_racks(
        rtdb, lambda rack: rack.min_cell_t),
    "bms.soc_pct": lambda rtdb: _avg_across_racks(
        rtdb, lambda rack: rack.pack_soc),
    "pcs.internal_temp_c": lambda rtdb: rtdb.pcs.temperature,
    "bms.bus_voltage_v": lambda rtdb: rtdb.pcs.dc_voltage,
}
```

## Data Flow -- Complete M2 Picture

```
BMS (CAN)  PCS (Modbus)  GPIO  Meter  Config files
    |          |           |      |        |
    v          v           v      v        v
comm_manager  comm_manager  safety  comm   config_manager
(CAN C)       (Modbus Py)   (C)    (Py)   (Python)
    |          |           |      |        |
    +----------+-----------+------+        |
               |                           |
           RTDB (shm)                config_reload
           +------+                    events
           |      |                      |
     +-----+  +--+---+            +-----+------+
     |        |      |            |            |
alarm_manager  control_manager    |            |
     |              |             |            |
     |  1. Read RTDB signals      |            |
     |  2. Evaluate thresholds    |            |
     |  3. Manage lifecycle       |            |
     |              |             |            |
     |  1. Read RTDB all          |            |
     |  2. Check alarm state      |            |
     |  3. Run state machine      |            |
     |  4. Calculate setpoint     |            |
     |  5. Write RTDB system      |            |
     |              |             |            |
     |     ZMQ PUB (protection    |            |
     |     alarms) ------>        |            |
     |              |             |            |
     |     ZMQ PUSH (events) --> logger        |
     |              |                          |
     |     RTDB system.active_setpoint_kw      |
     |              |                          |
     |     comm_manager reads setpoint         |
     |     writes Modbus 0x500E to PCS         |
```

## Patterns to Follow

### Pattern 1: RTDB Snapshot per Control Cycle

**What:** Read the entire RTDB state into an immutable dataclass at the start of each 1Hz cycle. All decisions within the cycle use this snapshot, not live RTDB reads.

**Why:** Prevents inconsistent state within a single cycle (e.g., SOC changes between checking limit and calculating setpoint). Makes the control logic deterministic and testable.

```python
@dataclass(frozen=True)
class ControlContext:
    """Immutable snapshot of system state for one control cycle."""
    timestamp_mono: float
    # BMS
    aggregate_soc: float
    min_cell_v: float
    max_cell_v: float
    min_cell_t: float
    max_cell_t: float
    bms_charge_current_limit: float
    bms_discharge_current_limit: float
    any_rack_offline: bool
    # PCS
    pcs_online: bool
    pcs_active_power: float
    pcs_dc_voltage: float
    pcs_fault_code: int
    pcs_temperature: float
    # Safety
    estop_active: bool
    fire_active: bool
    flood_active: bool
    acdb_closed: bool
    # Grid/Solar
    grid_available: bool
    solar_power_kw: float
    meter_active_power: float
    is_daytime: bool
```

### Pattern 2: Command Processing via ZMQ REQ/REP

**What:** Both modules bind a REQ/REP socket and process commands asynchronously within the main loop.

**When:** HMI sends mode change, operator acknowledges alarm, cloud sends remote command.

```python
async def _process_commands(self) -> None:
    """Non-blocking check for pending commands."""
    while True:
        try:
            frames = await asyncio.wait_for(
                self._cmd_sock.recv_multipart(), timeout=0.001
            )
            action, params = decode_command_request(frames[0])
            result = self._handle_command(action, params)
            await self._cmd_sock.send(encode_command_response(
                status=STATUS_OK if result.success else STATUS_ERROR,
                result=result.data,
                error_msg=result.error,
            ))
        except asyncio.TimeoutError:
            break  # No pending commands
```

### Pattern 3: Alarm-to-Control Feedback via ZMQ PUB/SUB

**What:** alarm_manager publishes protection alarm activations on the telemetry PUB socket. control_manager subscribes and reacts.

**Why:** Loose coupling. alarm_manager does not need to know about control_manager. control_manager subscribes to alarm events the same way HMI or cloud would.

```python
# alarm_manager publishes:
topic = b"alarm.protection"
body = encode_event(
    timestamp_ms=int(time.time() * 1000),
    source="alarm_manager",
    severity=SEVERITY_CRITICAL,
    event_type="alarm_activate",
    message=f"Protection alarm: {alarm.rule.alarm_id}",
    data={"alarm_id": alarm.rule.alarm_id, "value": alarm.current_value},
)
await pub_sock.send_multipart([topic, body])

# control_manager subscribes:
sub_sock.subscribe(b"alarm.protection")
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Direct PCS Modbus Writes from control_manager

**What:** control_manager imports pymodbus and writes PCS registers directly.

**Why bad:** Violates the single-writer-per-section rule. Creates a timing conflict with comm_manager's polling cycle. If control_manager crashes mid-write, Modbus bus may be left in an undefined state.

**Instead:** control_manager writes setpoint to RTDB system section. comm_manager reads it and writes to PCS. Clean separation, crash-safe.

### Anti-Pattern 2: Alarm Manager Directly Stopping PCS

**What:** alarm_manager detects a protection condition and writes PCS off command directly.

**Why bad:** alarm_manager should evaluate thresholds and publish events. It should not contain dispatch logic. If alarm_manager has PCS control, there are two modules that can send conflicting commands.

**Instead:** alarm_manager publishes protection events. control_manager receives them and transitions to appropriate state (stop power, enter FAULT). safety_manager handles hardware-level emergency stops independently.

### Anti-Pattern 3: Polling RTDB for Alarm State from control_manager

**What:** control_manager reads alarm_manager's in-memory state via RTDB fields every cycle.

**Why bad:** alarm_manager's state is complex (lifecycle per alarm) and does not fit naturally in fixed RTDB structs. Adds RTDB fields that only one consumer reads.

**Instead:** alarm_manager publishes alarm events on ZMQ PUB. control_manager subscribes and maintains its own simplified view of active protection alarms (a set of alarm IDs).

### Anti-Pattern 4: Tight 1Hz Timer Using asyncio.sleep(1.0)

**What:** Using `await asyncio.sleep(1.0)` for the control loop interval.

**Why bad:** Does not account for the time spent processing the control cycle. If processing takes 50ms, the actual interval becomes 1.05s. Over time, drift accumulates.

**Instead:** Calculate sleep time as `interval - elapsed`. Record monotonic time at cycle start, compute remaining sleep at cycle end.

## RTDB Extensions Required

The `EmsSystem` struct needs new fields for M2:

```c
/* Additions to ems_system_t */
typedef struct {
    ems_seqlock_t lock;
    uint64_t last_update_ms;

    /* Existing fields */
    int32_t control_state;        /* ControlState enum */
    int32_t source_priority;      /* Current active priority mode */
    float active_setpoint_kw;     /* Desired PCS power setpoint */
    float total_soc;              /* Weighted average SOC across all racks */
    float total_power_kw;         /* Current actual power from PCS */
    float total_energy_kwh;       /* Cumulative energy */
    uint32_t ems_uptime_s;        /* System uptime */

    /* New M2 fields */
    int32_t pcs_command;          /* PCS_CMD_NONE=0, ON=1, OFF=2, FAULT_RESET=3 */
    uint64_t pcs_command_ts;      /* Timestamp of last PCS command */
    float charge_derating_pct;    /* Current charge derating factor 0-100% */
    float discharge_derating_pct; /* Current discharge derating factor 0-100% */
    uint32_t active_alarm_count;  /* Number of active alarms (all severities) */
    uint32_t protection_alarm_active; /* Bitfield: which protection alarms active */
} ems_system_t;
```

**Note:** RTDB struct changes require updating both C (`rtdb.h`) and Python (`rtdb.py`) mirrors, and rebuilding all C modules. This is a coordinated change at the start of M2.

## Scalability Considerations

| Concern | Residential (50 kWh, 4 racks) | Container (6 MWh, 128 racks) | Design Impact |
|---------|------------------------------|------------------------------|---------------|
| RTDB read time per cycle | ~10us (4 racks) | ~200us (128 racks with seqlock) | Well within 1Hz budget. No optimization needed |
| Alarm rules count | 9 (default) | 20-30 (additional per-cluster rules) | Linear evaluation. 30 rules * ~1us each = ~30us. Negligible |
| Aggregate SOC calculation | Simple average of 4 racks | Weighted average of 128 racks by capacity | Pre-compute per-cluster, then aggregate. Still <100us |
| State change events | ~10/hour | ~50/hour (more racks cycling) | ZMQ PUSH handles easily |
| Source priority evaluation | 4 sources | 4 sources (same) | No scaling impact |

## Sources

- Codebase: `src/common/python/src/ems_common/rtdb.py` (EmsRtdb, EmsSystem struct)
- Codebase: `src/common/python/src/ems_common/ipc.py` (SOCK_CONTROL_CMD, SOCK_ALARM_CMD)
- Codebase: `config/control_config.yaml` (state machine, SOC limits, source priority)
- Codebase: `config/alarms_config.yaml` (alarm rules, hysteresis, delay)
- Requirements v0.2 Section 6.2 (PCS register map, on/off sequencing)
- Requirements v0.2 Section 8 (source priority DAY/NIGHT)
- Architecture spec v3.4 ADR-001 (RTDB single-writer-per-section)
- [IEC 62682 Alarm Management Standard](https://webstore.iec.ch/en/publication/65543)
- [OPC UA IEC 62682 Mapping](https://reference.opcfoundation.org/Core/Part9/v105/docs/E)
- [Yokogawa IEC 62682 Implementation](https://blog.yokogawa.com/blog/implementing-alarm-management-per-iec-62682-standard)
- [pyzmq asyncio docs](https://pyzmq.readthedocs.io/en/latest/api/zmq.asyncio.html)
- [Li-ion Derating Guidelines (MDPI)](https://www.mdpi.com/1996-1073/11/12/3295)
- [EVReporter LFP Derating](https://evreporter.com/derating-of-lithium-ion-cells-relationship-between-soc-c-rate-and-temperature/)
