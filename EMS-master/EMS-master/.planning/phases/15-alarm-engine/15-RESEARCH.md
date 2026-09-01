# Phase 15: Alarm Engine - Research

**Researched:** 2026-03-15
**Domain:** IEC 62682 alarm management, Python async state machine, ZMQ IPC, RTDB signal resolution
**Confidence:** HIGH

## Summary

Phase 15 builds the `alarm_manager` module: a pure Python service that evaluates 9 configurable alarm rules at 1Hz against RTDB signals, runs each alarm through a 5-state IEC 62682 lifecycle, publishes events to the logger via ZMQ PUSH, and serves a 3-query ZMQ REP/REQ command API. The entire design is already fully specified in 15-CONTEXT.md — locked decisions cover signal resolution, the lifecycle state machine, severity-action mapping (publish-only in this phase), and the query surface. Claude's discretion covers internal architecture choices.

The module follows the exact same architecture as `control_manager` (Phase 14): class-based async Python, `attach_rtdb()` for RTDB access, `encode_event`/`encode_command_response` from `ems_common.ipc`, ZMQ PUSH + REP sockets, and `asyncio.Event` for graceful shutdown. The primary new complexity vs. control_manager is the per-alarm delay timer (entry time tracking per alarm instance) and the 5-state lifecycle transitions.

**Primary recommendation:** Model `AlarmLoop` directly on `ControlLoop` — same socket lifecycle, same seqlock read pattern, same SIGTERM wiring. The alarm-specific logic lives in `AlarmEvaluator` (signal resolution + lifecycle) and `AlarmInstance` (dataclass per rule). Tests follow the `MockRtdb` + `patch(attach_rtdb)` pattern established in Phase 14.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Signal Path Resolution**
A resolver function maps dotted signal paths to RTDB struct field reads. The resolver is a dictionary built at startup from the RTDB topology, not a dynamic attribute walker.

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
- Signal paths are a fixed set — not arbitrary RTDB field access.
- Offline racks (`rack.online == 0`) excluded from aggregates.
- Resolution runs once per 1Hz tick, values cached for the tick.
- Invalid signal path in config → log ERROR at startup, disable that alarm rule (fail-open, not fail-closed).

**Alarm Lifecycle State Machine (IEC 62682)**
5-state lifecycle per alarm instance:

| State | Entry Condition | Exit Condition |
|-------|----------------|----------------|
| NORMAL | Initial state, or RTN acknowledged | Signal exceeds threshold + delay |
| ACTIVE_UNACKED | Signal exceeded threshold for delay_ms | Operator sends acknowledge command |
| ACTIVE_ACKED | Acknowledge received while signal exceeding | Signal returns within limits (with hysteresis) |
| CLEARED_UNACKED | Signal back in limits while ACTIVE_UNACKED | Operator sends acknowledge command |
| RTN | Acknowledge received in CLEARED_UNACKED, or clears from ACTIVE_ACKED | Auto-transition to NORMAL |

Key rules:
- Each alarm rule gets one lifecycle instance (not per-rack).
- Timestamps recorded at every transition: `activated_at`, `acknowledged_at`, `cleared_at`, `rtn_at`.
- RTN auto-transitions to NORMAL after publishing the RTN event.
- Alarm instances persist in memory only — no disk persistence.
- Acknowledge command arrives via ZMQ REQ on SOCK_ALARM_CMD with `{action: "acknowledge", alarm_id: "cell_voltage_high"}`.

**Severity-to-Action Mapping (Phase 15 scope)**
Phase 15 publishes events only. Protection actions deferred to Phase 16 (ALM-08).

| Severity | Phase 15 Action |
|----------|----------------|
| warning | Publish alarm event to logger (PUSH) |
| action | Publish alarm event to logger (PUSH) |
| protection | Publish alarm event to logger (PUSH) |

- Phase 15 alarm_manager ONLY publishes events.
- All three severity levels publish on ZMQ PUSH to logger and PUB on telemetry (topic: "alarm") for subscribers.

**Alarm Query API Surface**
3 query types via ZMQ REQ/REP:

| Query | Request | Response |
|-------|---------|----------|
| `get_active_alarms` | `{action: "get_active_alarms"}` | `{status: "ok", alarms: [{alarm_id, signal, severity, state, value, threshold, activated_at, acknowledged_at}]}` |
| `acknowledge` | `{action: "acknowledge", alarm_id: "cell_voltage_high"}` | `{status: "ok", alarm_id, from_state, to_state}` or `{status: "error", error_msg}` |
| `get_alarm_config` | `{action: "get_alarm_config"}` | `{status: "ok", rules: [...]}` |

- Uses existing `encode_command_request/response` from `ems_common/ipc.py`.
- `get_active_alarms` returns only non-NORMAL alarms.
- `acknowledge` validates: must be in ACTIVE_UNACKED or CLEARED_UNACKED.
- No `get_alarm_history` — historical alarms are in JSONL via logger.

### Claude's Discretion

- Alarm evaluation loop architecture (single async function vs class-based evaluator)
- AlarmInstance class internal design (dataclass vs dict)
- ZMQ socket initialization and async poller integration
- Startup sequence (config load, RTDB attach, ZMQ bind order)
- How to handle the 1Hz evaluation + ZMQ command polling in the same async loop
- Test fixtures for simulating RTDB signal changes

### Deferred Ideas (OUT OF SCOPE)

- **ALM-08**: Protection-severity alarms send power reduction/shutdown to control_manager — Phase 16
- **ALM-09**: Hot-reload of alarms_config.yaml — Phase 16
- **ALM-11**: Alarm shelving (temporary suppression with auto-restore) — future milestone
- **ALM-12**: Alarm grouping (suppress child alarms when parent active) — future milestone
- **ALM-13**: Alarm analytics (most frequent, longest active, first-out) — future milestone
- DO-2/DO-4 lamp integration — safety_manager owns GPIO, alarm_manager publishes events only
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ALM-01 | Alarm evaluation reads RTDB signal paths at 1Hz and compares against configurable thresholds from alarms_config.yaml | Signal resolver dict maps 7 paths → RTDB fields; resolver runs once per tick before rule evaluation |
| ALM-02 | Three-tier IEC 62682 severity (warning, action, protection) — Phase 15: publish only | Severity stored in AlarmInstance; event payload includes severity for Phase 16 filtering |
| ALM-03 | Alarm lifecycle: ACTIVE → ACKNOWLEDGED → CLEARED → RETURN-TO-NORMAL with timestamps | 5-state machine per AlarmInstance (dataclass); transitions driven by evaluator + acknowledge command |
| ALM-04 | Hysteresis prevents alarm chattering: activates at threshold, clears at threshold ± hysteresis_pct | Clear threshold computed from config at startup; hysteresis_pct is percentage of threshold value |
| ALM-05 | Delay timer: signal must exceed threshold for delay_ms before alarm activates | AlarmInstance tracks `exceeded_since_ms: float | None`; compared against current tick time |
| ALM-06 | Alarm events published on ZMQ PUSH to logger with full context | Use `encode_event()` from ems_common.ipc; PUSH send with NOBLOCK + catch zmq.Again |
| ALM-07 | Active alarm list maintained in memory, queryable via ZMQ REQ/REP (get_active_alarms, acknowledge_alarm, get_alarm_config) | REP socket polled non-blocking each tick; dispatch to 3 handler methods |
| ALM-10 | Per-alarm enable/disable flag in config — disabled alarms are suppressed | AlarmRule.enabled checked at evaluation time; disabled rules remain NORMAL, no events published |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyzmq | already in ems-common | ZMQ PUSH/REP sockets for events + command API | Project-wide IPC standard |
| msgpack | already in ems-common | MessagePack serialization for all IPC | Project-wide serialization standard |
| pyyaml | already in workspace | YAML config loading | Established in all Python modules |
| jsonschema | already in workspace | JSON Schema validation of alarms_config.yaml | Pattern from config_manager and control_manager |
| asyncio | stdlib | 1Hz loop + timing-corrected sleep | Pattern from control_manager |
| dataclasses | stdlib | AlarmInstance state container | Cleaner than dict, typed, IDE-friendly |
| time | stdlib | Monotonic timestamps for delay timers, wall-clock for event timestamps | Pattern from control_manager (_now_ms) |

### No New Dependencies
Phase 15 requires zero new pip/uv packages. All required libraries are already in `ems-common` or the Python stdlib. The `ems-alarm-manager` pyproject.toml currently lists only `ems-common` — this remains correct.

**Installation:** No new installations required.
```bash
# ems-common already provides: pyzmq, msgpack, pyyaml, jsonschema
# Verify workspace sync includes alarm_manager:
uv sync --all-packages
```

## Architecture Patterns

### Recommended Module Structure
```
src/alarm_manager/
├── pyproject.toml
└── src/ems_alarm_manager/
    ├── __init__.py         # version only (existing stub)
    ├── __main__.py         # entry point, argparse, asyncio.run(run(args))
    ├── config.py           # load_alarm_config() with JSON Schema validation
    ├── resolver.py         # SignalResolver: builds dict, resolves per tick
    ├── evaluator.py        # AlarmInstance (dataclass), AlarmEvaluator class
    └── loop.py             # AlarmLoop: 1Hz tick + ZMQ REP command polling
    tests/
    ├── __init__.py
    ├── test_config.py      # load_alarm_config() validation cases
    ├── test_resolver.py    # resolver: online racks, offline exclusion, fallback
    ├── test_evaluator.py   # lifecycle transitions, hysteresis, delay timers
    └── test_loop.py        # ZMQ sockets, RTDB mock, tick integration
```

### Pattern 1: AlarmInstance as Dataclass
**What:** Each alarm rule gets one AlarmInstance tracking lifecycle state, timestamps, and delay timer.
**When to use:** Always — one instance per rule name, stored in a dict keyed by alarm_id.

```python
# Source: project conventions (ems_control_manager/state_machine.py pattern adapted)
from __future__ import annotations
import dataclasses
import time

# Lifecycle state constants (mirror IEC 62682 names as strings for readability)
STATE_NORMAL: str = "NORMAL"
STATE_ACTIVE_UNACKED: str = "ACTIVE_UNACKED"
STATE_ACTIVE_ACKED: str = "ACTIVE_ACKED"
STATE_CLEARED_UNACKED: str = "CLEARED_UNACKED"
STATE_RTN: str = "RTN"


@dataclasses.dataclass
class AlarmInstance:
    """Per-alarm IEC 62682 lifecycle tracker."""
    alarm_id: str
    signal: str
    severity: str
    high_threshold: float | None
    low_threshold: float | None
    hysteresis_pct: float
    delay_ms: int
    enabled: bool

    # Runtime state
    state: str = STATE_NORMAL
    exceeded_since_ms: float | None = None  # monotonic ms when threshold first crossed
    current_value: float = 0.0

    # Timestamps (wall-clock ms, 0 = not yet set)
    activated_at: int = 0
    acknowledged_at: int = 0
    cleared_at: int = 0
    rtn_at: int = 0
```

### Pattern 2: AlarmLoop follows ControlLoop exactly
**What:** Class with `__init__` (attach RTDB, bind sockets), `run()` (1Hz asyncio loop), `cleanup()`.
**When to use:** Always — this is the established module pattern.

```python
# Source: ems_control_manager/loop.py (established project pattern)
class AlarmLoop:
    def __init__(self, config: dict[str, Any], ...) -> None:
        self._shm, self._rtdb = attach_rtdb()
        # REP socket: alarm_cmd
        self._rep: zmq.Socket = self._zmq_ctx.socket(zmq.REP)
        self._rep.setsockopt(zmq.LINGER, 0)
        self._rep.bind(rep_endpoint or SOCK_ALARM_CMD)
        # PUSH socket: logger events
        self._push: zmq.Socket = self._zmq_ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.LINGER, 0)
        self._push.connect(push_endpoint or SOCK_LOGGER)
        # PUB socket: telemetry topic "alarm"
        self._pub: zmq.Socket = self._zmq_ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 0)
        self._pub.bind(pub_endpoint or SOCK_ALARM_PUB)
        self._stop_event: asyncio.Event = asyncio.Event()
        self._evaluator: AlarmEvaluator = AlarmEvaluator(config)

    async def run(self) -> None:
        while not self._stop_event.is_set():
            tick_start: float = time.monotonic()
            self._poll_commands()
            self._tick(tick_start)
            elapsed: float = time.monotonic() - tick_start
            await asyncio.sleep(max(0.0, 1.0 - elapsed))
```

### Pattern 3: Signal Resolver Dict (built at startup)
**What:** Dict mapping signal path string → callable that takes EmsRtdb and returns float.
**When to use:** Called once per tick before alarm rule evaluation. Uses seqlock-safe copies.

```python
# Source: 15-CONTEXT.md locked decision — dictionary resolver, not attribute walker
from ems_common.rtdb import EmsRtdb, MAX_CLUSTERS, MAX_RACKS_PER_CLUSTER

def _build_resolver() -> dict[str, Callable[[EmsRtdb], float | None]]:
    """Build signal path → RTDB field resolver dict at startup."""

    def bms_cell_voltage_max(rtdb: EmsRtdb) -> float | None:
        vals: list[float] = [
            rtdb.clusters[c].racks[r].max_cell_v
            for c in range(MAX_CLUSTERS)
            for r in range(MAX_RACKS_PER_CLUSTER)
            if rtdb.clusters[c].racks[r].online
        ]
        return max(vals) if vals else None

    def bms_soc_pct(rtdb: EmsRtdb) -> float | None:
        vals: list[float] = [
            rtdb.clusters[c].racks[r].pack_soc
            for c in range(MAX_CLUSTERS)
            for r in range(MAX_RACKS_PER_CLUSTER)
            if rtdb.clusters[c].racks[r].online
        ]
        return sum(vals) / len(vals) if vals else None

    return {
        "bms.cell_voltage_max": bms_cell_voltage_max,
        "bms.cell_voltage_min": lambda rtdb: ...,  # min() variant
        "bms.cell_temp_max":    lambda rtdb: ...,
        "bms.cell_temp_min":    lambda rtdb: ...,
        "bms.soc_pct":          bms_soc_pct,
        "bms.bus_voltage_v":    lambda rtdb: rtdb.pcs.dc_voltage,
        "pcs.internal_temp_c":  lambda rtdb: rtdb.pcs.temperature,
    }
```

**Critical:** The resolver must use a seqlock-safe copy of RTDB sections. AlarmLoop reads BMS clusters and PCS sections via `_seqlock_read_section()` (same helper as ControlLoop) before passing to the evaluator. The resolver dict takes the copy, not the live shm pointer.

### Pattern 4: Hysteresis Threshold Computation
**What:** Clear threshold is computed from activation threshold and hysteresis_pct at AlarmInstance creation, not at evaluation time.
**When to use:** Pre-compute in config loading to avoid repeated float math during 1Hz tick.

```python
# Source: 15-CONTEXT.md specifics — percentage of threshold value
# For high_threshold alarm:
#   activate when value > high_threshold
#   clear when value < high_threshold - (high_threshold * hysteresis_pct / 100)
# For low_threshold alarm:
#   activate when value < low_threshold
#   clear when value > low_threshold + (low_threshold * hysteresis_pct / 100)

def _compute_clear_threshold(
    threshold: float,
    hysteresis_pct: float,
    is_high: bool,
) -> float:
    band: float = abs(threshold) * hysteresis_pct / 100.0
    return (threshold - band) if is_high else (threshold + band)
```

### Pattern 5: Delay Timer per Alarm Instance
**What:** `exceeded_since_ms` is set on the first tick where the signal exceeds threshold. Alarm activates only when `(now_ms - exceeded_since_ms) >= delay_ms`.
**When to use:** Always — per-alarm timer, not global.

```python
# Source: 15-CONTEXT.md specifics
def _evaluate_one(
    instance: AlarmInstance,
    value: float,
    now_ms: float,
) -> list[str]:  # returns list of lifecycle events to publish
    """Evaluate one alarm rule for one tick. Returns events to publish."""
    exceeds: bool = _check_threshold(instance, value)

    if instance.state == STATE_NORMAL:
        if exceeds:
            if instance.exceeded_since_ms is None:
                instance.exceeded_since_ms = now_ms  # start delay timer
            elif (now_ms - instance.exceeded_since_ms) >= instance.delay_ms:
                # Delay expired — activate alarm
                instance.state = STATE_ACTIVE_UNACKED
                instance.activated_at = int(time.time() * 1000)
                instance.exceeded_since_ms = None  # reset timer
                return ["alarm_activated"]
        else:
            instance.exceeded_since_ms = None  # reset timer if signal recovers
    ...
```

### Pattern 6: ZMQ PUSH Event Publishing
**What:** NOBLOCK send on PUSH socket; catch `zmq.Again` and drop (non-fatal).
**When to use:** All event publishes — alarm activated, acknowledged, cleared, RTN.

```python
# Source: ems_comm_manager/events.py (established project pattern)
def _publish_alarm_event(self, instance: AlarmInstance, event_type: str) -> None:
    data: dict = {
        "alarm_id": instance.alarm_id,
        "signal": instance.signal,
        "severity": instance.severity,
        "state": instance.state,
        "value": instance.current_value,
        "threshold": instance.high_threshold or instance.low_threshold,
    }
    msg: bytes = encode_event(
        timestamp_ms=int(time.time() * 1000),
        source="alarm_manager",
        severity=instance.severity,
        event_type=TOPIC_ALARM,
        message=f"Alarm {instance.alarm_id}: {event_type}",
        data=data,
    )
    try:
        self._push.send(msg, zmq.NOBLOCK)
    except zmq.Again:
        pass  # Drop on EAGAIN — logger backpressure, non-fatal
```

### Pattern 7: REP Command Dispatch
**What:** Non-blocking drain of REP socket each tick. Each request MUST get a reply (ZMQ REP protocol).
**When to use:** `_poll_commands()` called before `_tick()` — same as ControlLoop.

```python
# Source: ems_control_manager/loop.py (established pattern)
def _poll_commands(self) -> None:
    while True:
        try:
            raw: bytes = self._rep.recv(zmq.NOBLOCK)
        except zmq.Again:
            return
        try:
            action, params = decode_command_request(raw)
            reply = self._dispatch_command(action, params)
        except Exception as exc:
            reply = encode_command_response("error", error_msg=str(exc))
        self._rep.send(reply)  # MUST always reply

def _dispatch_command(self, action: str, params: dict) -> bytes:
    if action == "get_active_alarms":
        return self._handle_get_active_alarms()
    elif action == "acknowledge":
        return self._handle_acknowledge(params)
    elif action == "get_alarm_config":
        return self._handle_get_alarm_config()
    else:
        return encode_command_response("error", error_msg=f"Unknown action: {action!r}")
```

### Pattern 8: Config Loading (alarms_config.yaml)
**What:** Load YAML, validate against JSON Schema, return dict. Raise ValueError on failure (not ValidationError).
**When to use:** Always — same pattern as `load_control_config()`.

```python
# Source: ems_control_manager/config.py (established pattern)
def load_alarm_config(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Load and validate alarms_config.yaml.

    Raises:
        FileNotFoundError: Config or schema missing.
        ValueError: Invalid YAML or schema validation failure.
    """
    ...
    # Key: raise ValueError (not jsonschema.ValidationError)
    # for consistent error interface across all modules
```

### Pattern 9: __main__.py + SIGTERM wiring
**What:** `argparse` → load config → create AlarmLoop → wire SIGTERM/SIGINT → `asyncio.run()`.
**When to use:** Identical to control_manager's `__main__.py`.

```python
# Source: ems_control_manager/__main__.py (established pattern)
async def run(args: argparse.Namespace) -> None:
    config = load_alarm_config(args.config)
    loop_obj = AlarmLoop(config)
    asyncio_loop = asyncio.get_running_loop()
    def _signal_handler() -> None:
        loop_obj.stop_event.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, _signal_handler)
    try:
        await loop_obj.run()
    finally:
        loop_obj.cleanup()
```

### Anti-Patterns to Avoid

- **Dynamic attribute walking on ctypes structs:** Don't use `getattr(rtdb, path.split(".")[0])` for signal resolution. Use the pre-built resolver dict from `_build_resolver()`.
- **Disk persistence of alarm state:** Alarm instances are in-memory only. On restart, all alarms start NORMAL. Don't write alarm state to file.
- **Publishing events for disabled alarms:** Check `instance.enabled` before any evaluation. Disabled alarms stay NORMAL and produce no events, no log noise.
- **Blocking REP socket:** Never use `self._rep.recv()` without `zmq.NOBLOCK` in the main loop. A blocked REP will stall the 1Hz tick entirely.
- **Using SOCK_TELEMETRY (data_manager's PUB):** alarm_manager binds its own PUB socket. Use a new `SOCK_ALARM_PUB = "ipc:///run/ems/alarm_pub.sock"` (define locally in loop.py, same as SOCK_CONTROL_PUB in Phase 14).
- **Returning None from resolver without handling:** If all racks are offline, resolver returns `None`. The evaluator must skip threshold comparison and leave the alarm in NORMAL (can't alarm on missing data).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Message encoding | Custom framing | `encode_event`, `encode_command_request/response` from `ems_common.ipc` | Already implements msgpack envelope; consistent across all modules |
| RTDB access | Direct ctypes field access | `attach_rtdb()` + `_seqlock_read_section()` from `ems_common.rtdb` | Handles magic/version check, resource_tracker, seqlock retry |
| Config validation | Manual YAML key checks | `jsonschema.Draft202012Validator` against `alarms_config.schema.json` | Schema already written and covers all 9 rules |
| ZMQ context lifecycle | Custom context pool | Single `zmq.Context()` per process, `.term()` in `cleanup()` | Same pattern as ControlLoop; prevents fd leak on test teardown |
| Timestamp computation | datetime module | `int(time.time() * 1000)` for wall-clock, `time.monotonic()` for delay timers | Pattern from ControlLoop `_now_ms()` |

**Key insight:** The entire IPC contract (socket paths, topic strings, encode/decode helpers) is already defined in `ems_common/ipc.py`. SOCK_ALARM_CMD and TOPIC_ALARM are already present. Never re-implement what's in ems_common.

---

## Common Pitfalls

### Pitfall 1: Alarm Chattering Without Hysteresis
**What goes wrong:** Alarm flaps between ACTIVE and NORMAL when signal oscillates near the threshold, generating dozens of events per minute.
**Why it happens:** Naive implementation uses the same threshold for activation and clearance.
**How to avoid:** Compute `clear_threshold = high_threshold - (high_threshold * hysteresis_pct / 100.0)` at AlarmInstance creation. Alarm clears only when signal drops below `clear_threshold`, not below `high_threshold`.
**Warning signs:** Test that shows alarm activating at 3.65V and clearing at 3.65V — it should clear at 3.5770V (3.65 × 0.98 = 3.577).

### Pitfall 2: Delay Timer Not Resetting on Signal Recovery
**What goes wrong:** Signal exceeds threshold for 2s, drops back, then 3s later briefly exceeds again — alarm fires after only 3s total instead of requiring a fresh 5s window.
**Why it happens:** `exceeded_since_ms` not cleared when signal returns to normal.
**How to avoid:** In the NORMAL state handler: if signal is NOT exceeding, set `instance.exceeded_since_ms = None`. The timer only accumulates consecutive ticks above threshold.
**Warning signs:** Test with signal pattern: exceed for 2s, normal for 1s, exceed for 3s — should NOT activate with 5s delay. Should take 5 consecutive seconds above threshold.

### Pitfall 3: CLEARED_UNACKED State Missing
**What goes wrong:** Alarm clears before operator acknowledges, lifecycle jumps directly to NORMAL, operator never sees it happened.
**Why it happens:** IEC 62682 CLEARED_UNACKED state is easy to miss in naive implementations.
**How to avoid:** ACTIVE_UNACKED → signal clears → CLEARED_UNACKED (not NORMAL). Only transitions to RTN/NORMAL after operator acknowledgment or after acknowledge from CLEARED_UNACKED.
**Warning signs:** Test that verifies alarm in ACTIVE_UNACKED state, signal clears, state is CLEARED_UNACKED (not NORMAL).

### Pitfall 4: REP Socket Blocked by Missing Reply
**What goes wrong:** AlarmLoop stalls permanently because a REP socket request received no reply (exception before `self._rep.send(reply)` line).
**Why it happens:** Exception between `recv()` and `send()` leaves ZMQ REP state machine in broken state — next `recv()` returns the previous message again on some versions, or blocks.
**How to avoid:** Wrap entire command dispatch in `try/except` with guaranteed `self._rep.send()` in all paths. Pattern copied verbatim from `ems_control_manager/loop.py::_poll_commands()`.
**Warning signs:** Test that sends a malformed msgpack payload — loop should reply with error response, not hang.

### Pitfall 5: Resolver Returns None for All-Offline RTDB
**What goes wrong:** `max([])` raises `ValueError` when no racks are online. Or `None` is compared against a float threshold, raising `TypeError`.
**Why it happens:** Resolver functions use `max()` / `min()` on list comprehensions that may be empty.
**How to avoid:** Resolver returns `float | None`. Evaluator checks for `None` and skips threshold comparison — alarm stays NORMAL. This is correct behavior: missing data should not trigger alarms.
**Warning signs:** Test with `rtdb.clusters[*].racks[*].online = 0` — all BMS alarms should stay NORMAL.

### Pitfall 6: ctypes Struct Read Without Seqlock Copy
**What goes wrong:** Reading RTDB BMS cluster fields directly during a C writer's seqlock update causes torn reads — `max_cell_v` from one write half and `min_cell_v` from another.
**Why it happens:** BMS section is large (thousands of bytes across 8×16 racks) — memmove is not atomic.
**How to avoid:** Use `_seqlock_read_section()` on each cluster copy before passing to resolver. Note: clusters are iterated individually (one seqlock per EmsCluster is not present — only racks have seqlocks). Read the entire `EmsCluster` array using per-rack seqlocks.
**Warning signs:** Intermittent test failures with `max_cell_v < min_cell_v` from the same tick.

### Pitfall 7: Alarm PUB Socket Conflicts with data_manager
**What goes wrong:** alarm_manager tries to bind `SOCK_TELEMETRY` (owned by data_manager) and fails with "address already in use".
**Why it happens:** Confusion about which modules own which PUB sockets.
**How to avoid:** alarm_manager binds a dedicated PUB socket. Define `SOCK_ALARM_PUB = "ipc:///run/ems/alarm_pub.sock"` locally in loop.py (same as SOCK_CONTROL_PUB in ControlLoop — not added to ipc.py to avoid churn until the socket stabilizes).

---

## Code Examples

### Verified: seqlock read pattern (from ControlLoop)
```python
# Source: ems_control_manager/loop.py lines 67-97
def _seqlock_read_section(section: ctypes.Structure) -> ctypes.Structure:
    section_type = type(section)
    copy = section_type()
    for _ in range(100):  # _SEQLOCK_MAX_RETRIES
        seq1: int = section.lock.sequence
        if seq1 & 1:
            continue  # write in progress
        ctypes.memmove(ctypes.addressof(copy), ctypes.addressof(section), ctypes.sizeof(section_type))
        seq2: int = section.lock.sequence
        if seq1 == seq2:
            return copy
    return copy  # fallback with stale data after 100 retries
```

### Verified: PUSH send with NOBLOCK + EAGAIN drop (from comm_manager)
```python
# Source: ems_comm_manager/events.py lines 69-74
try:
    push_socket.send(msg, zmq.NOBLOCK)
except zmq.Again:
    pass  # Drop on EAGAIN — non-blocking send
```

### Verified: SOCK_ALARM_CMD in ems_common.ipc
```python
# Source: ems_common/ipc.py line 18
SOCK_ALARM_CMD: str = "ipc:///run/ems/alarm_cmd.sock"
TOPIC_ALARM: str = "alarm"  # line 38
```

### Verified: encode_command_response usage
```python
# Source: ems_common/ipc.py lines 124-135
reply = encode_command_response(
    status="ok",
    result={"alarms": [...]},
)
# Or on error:
reply = encode_command_response(
    status="error",
    error_msg="Alarm not in acknowledgeable state",
)
```

### Verified: RTDB field names for signal resolution
```python
# Source: ems_common/rtdb.py
# EmsRack fields: max_cell_v, min_cell_v, max_cell_t, min_cell_t, pack_soc, online
# EmsPcs fields: dc_voltage (for bus_voltage_v), temperature (for pcs.internal_temp_c)
# Topology: rtdb.clusters[c].racks[r] for c in range(MAX_CLUSTERS) for r in range(MAX_RACKS_PER_CLUSTER)
```

### Verified: Test pattern — mock RTDB with ctypes structs
```python
# Source: ems_control_manager/tests/test_loop.py lines 67-113
class MockRtdb:
    def __init__(self) -> None:
        self.pcs: EmsPcs = EmsPcs()
        self.gpio: EmsGpio = EmsGpio()
        self.system: EmsSystem = EmsSystem()

# In test:
with (
    patch("ems_alarm_manager.loop.attach_rtdb") as mock_attach,
    patch("ems_alarm_manager.loop.detach_rtdb"),
):
    mock_attach.return_value = (MagicMock(), mock_rtdb)
    loop = AlarmLoop(config=cfg, rep_endpoint="tcp://127.0.0.1:15600")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-rack alarm instances (one alarm per rack) | Per-rule instances with aggregate signal paths | Design decision in 15-CONTEXT.md | 9 alarms max vs 9×128 racks; manageable HMI list |
| IEC 62682 4-state (Active/Acked/Cleared/Normal) | 5-state with CLEARED_UNACKED added | Per 15-CONTEXT.md IEC 62682 Section 6.3 | Operator always sees alarm even if auto-cleared before ACK |
| Disk-persisted alarm history in alarm_manager | Alarms are derived state; JSONL history in logger | M1 logger phase | No duplication; alarm_manager restarts cleanly |
| Dynamic attribute walking (getattr chains) | Pre-built resolver dict | 15-CONTEXT.md locked decision | Testable, startup-validated, no runtime reflection errors |

**Deprecated/outdated:**
- `get_alarm_history` query: not in Phase 15 scope — use logger's `event_log` query type (Phase 12, LOG-04) for history.

---

## Open Questions

1. **PUB socket endpoint for alarm telemetry**
   - What we know: control_manager defined `SOCK_CONTROL_PUB` locally (not in ipc.py) to avoid churn. Phase 16 subscribes.
   - What's unclear: Should `SOCK_ALARM_PUB` be added to `ipc.py` now or defined locally in `loop.py`?
   - Recommendation: Define locally in `loop.py` as `SOCK_ALARM_PUB = "ipc:///run/ems/alarm_pub.sock"` per Phase 14 precedent. Phase 16 can promote it to ipc.py when wiring the subscription.

2. **Seqlock for BMS cluster sections**
   - What we know: Each `EmsRack` has a `.lock` seqlock. `EmsCluster` does not have its own lock. Only racks have per-section locks.
   - What's unclear: Should resolver read each rack individually with `_seqlock_read_section(rack)` or read the full cluster?
   - Recommendation: Read each rack individually via `_seqlock_read_section(rtdb.clusters[c].racks[r])` — this is the only seqlock available at the rack level. The cluster struct itself has no lock.

3. **ZMQ TCP vs IPC for test sockets**
   - What we know: control_manager tests use `tcp://127.0.0.1:155xx` ports.
   - What's unclear: Will the alarm_manager tests conflict on the same port range?
   - Recommendation: Use `tcp://127.0.0.1:156xx` range for alarm_manager tests (e.g., 15600–15699) to avoid port conflicts when both test suites run simultaneously.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (from root pyproject.toml) |
| Config file | `/home/overlord/EMS/pyproject.toml` — `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest src/alarm_manager/src/ems_alarm_manager/../../../tests/ -x` |
| Full suite command | `uv run pytest src/alarm_manager/ -v` |

**Note on test location:** Phase 14 put tests in `src/control_manager/python/tests/`. Alarm manager tests should follow: `src/alarm_manager/tests/` (flat, not nested under src/ems_alarm_manager).

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ALM-01 | 1Hz evaluation reads RTDB signals via resolver dict | unit | `uv run pytest src/alarm_manager/tests/test_resolver.py -x` | Wave 0 |
| ALM-01 | Disabled alarm rule is skipped (ALM-10 overlap) | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_disabled_rule_stays_normal -x` | Wave 0 |
| ALM-02 | Warning severity alarm publishes event on PUSH | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_warning_alarm_publishes_event -x` | Wave 0 |
| ALM-03 | NORMAL → ACTIVE_UNACKED transition | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_activate_transition -x` | Wave 0 |
| ALM-03 | ACTIVE_UNACKED → CLEARED_UNACKED on signal clear | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_cleared_unacked_transition -x` | Wave 0 |
| ALM-03 | CLEARED_UNACKED → RTN → NORMAL on acknowledge | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_rtn_transition -x` | Wave 0 |
| ALM-03 | ACTIVE_UNACKED → ACTIVE_ACKED on acknowledge | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_acknowledge_while_active -x` | Wave 0 |
| ALM-04 | Hysteresis: alarm stays ACTIVE until signal drops below clear_threshold | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_hysteresis_prevents_clear -x` | Wave 0 |
| ALM-05 | Delay timer: alarm does NOT activate before delay_ms elapses | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_delay_timer_prevents_early_activation -x` | Wave 0 |
| ALM-05 | Delay timer resets when signal recovers before expiry | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_delay_timer_resets_on_recovery -x` | Wave 0 |
| ALM-06 | Alarm activated event published on PUSH with correct fields | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_alarm_event_payload -x` | Wave 0 |
| ALM-07 | get_active_alarms returns only non-NORMAL alarms | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_get_active_alarms -x` | Wave 0 |
| ALM-07 | acknowledge command transitions ACTIVE_UNACKED → ACTIVE_ACKED | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_acknowledge_command -x` | Wave 0 |
| ALM-07 | acknowledge command rejects NORMAL alarm | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_acknowledge_normal_rejected -x` | Wave 0 |
| ALM-07 | get_alarm_config returns current rules | unit | `uv run pytest src/alarm_manager/tests/test_loop.py::test_get_alarm_config -x` | Wave 0 |
| ALM-10 | Disabled alarm suppressed, no event published | unit | `uv run pytest src/alarm_manager/tests/test_evaluator.py::test_disabled_alarm_no_event -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest src/alarm_manager/ -x -q`
- **Per wave merge:** `uv run pytest src/alarm_manager/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/alarm_manager/tests/__init__.py` — empty marker file
- [ ] `src/alarm_manager/tests/test_config.py` — covers config loading, schema validation
- [ ] `src/alarm_manager/tests/test_resolver.py` — covers signal resolution, offline rack exclusion
- [ ] `src/alarm_manager/tests/test_evaluator.py` — covers AlarmInstance lifecycle, hysteresis, delay
- [ ] `src/alarm_manager/tests/test_loop.py` — covers ZMQ integration, mock RTDB, command dispatch

---

## Sources

### Primary (HIGH confidence)
- `ems_common/ipc.py` — SOCK_ALARM_CMD, TOPIC_ALARM, encode_event, encode_command_request/response, decode_command_request
- `ems_common/rtdb.py` — EmsRack (max_cell_v, min_cell_v, max_cell_t, min_cell_t, pack_soc, online), EmsPcs (dc_voltage, temperature), EmsRtdb.clusters topology
- `ems_control_manager/loop.py` — ControlLoop architecture: seqlock helper, _poll_commands, NOBLOCK send, asyncio timing loop, cleanup
- `ems_control_manager/config.py` — load_control_config pattern: yaml.safe_load, Draft202012Validator, ValueError raises
- `ems_control_manager/__main__.py` — SIGTERM wiring, asyncio.run, argparse pattern
- `ems_comm_manager/events.py` — PUSH NOBLOCK + zmq.Again drop pattern
- `config/alarms_config.yaml` — 9 alarm rules with signal paths, thresholds, severities
- `config/schemas/alarms_config.schema.json` — Full JSON Schema for all 9 rules
- `15-CONTEXT.md` — All locked decisions: signal resolver, lifecycle states, severity actions, query API

### Secondary (MEDIUM confidence)
- `deploy/systemd/alarm_manager.service` — Startup ordering: After=data_manager + control_manager
- Root `pyproject.toml` `[tool.pytest.ini_options]` — Test infrastructure, markers, addopts

### Tertiary (LOW confidence)
- None — all findings grounded in project source code

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in workspace, zero new dependencies
- Architecture: HIGH — all patterns directly copied from Phase 14 (ControlLoop), no novel patterns
- Pitfalls: HIGH — all derived from actual code (seqlock pattern, NOBLOCK pattern, REP-must-reply contract)
- IEC 62682 lifecycle: HIGH — all states and transitions defined in 15-CONTEXT.md locked decisions

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable — no external dependencies to go stale)
