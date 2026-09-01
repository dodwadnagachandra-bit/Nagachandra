# Phase 20: Scheduler - Research

**Researched:** 2026-03-15
**Domain:** Python async service -- time-based schedule evaluation, ZMQ command dispatch
**Confidence:** HIGH

## Summary

The scheduler is a pure Python async service that evaluates schedule_config.yaml at 1Hz and sends setpoint commands to control_manager via ZMQ REQ. It has no RTDB access -- it is a command-only module. The codebase already has all infrastructure needed: the stub package exists at `src/scheduler/`, the config file and schema are defined, the ZMQ IPC contract is established, and two prior modules (control_manager, alarm_manager) demonstrate the exact patterns for async loops, config hot-reload, and telemetry publishing.

The scheduler is architecturally simple compared to control_manager or alarm_manager. It needs: (1) a config loader with JSON Schema validation (identical pattern to `load_control_config`), (2) a 1Hz async loop that evaluates time windows or curve index, (3) a ZMQ REQ client to send commands on state change only, (4) a ZMQ SUB socket for config hot-reload, and (5) a ZMQ PUB socket for telemetry. All five patterns are proven in existing code.

**Primary recommendation:** Follow the alarm_manager architecture exactly -- `config.py` (loader), `loop.py` (SchedulerLoop class), `__main__.py` (entry point with signal handling). The scheduler is simpler because it has no RTDB access and no REP server.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Command channel: ZMQ REQ on `ipc:///run/ems/control_cmd.sock` (SOCK_CONTROL_CMD)
- Commands used: `manual_setpoint` (power_kw), `source_priority` (mode), `mode_change` (standby/idle)
- Frequency: On state change only (not every 1Hz tick)
- Error handling: If REQ times out (5s), retry next evaluation tick (1Hz). Log WARNING.
- Scheduler is a ZMQ client of control_manager -- sends commands, does NOT write RTDB
- Two-step process: set `source_priority` to MANUAL before sending `manual_setpoint`
- "manual" mode = scheduler sends nothing, operator controls via HMI
- Three modes: manual, time_of_day, curve
- time_of_day: find window containing current time, first match wins, half-open intervals [start, end)
- curve: `index = hour * 4 + minute // 15`, step interpolation (no linear), positive=discharge, negative=charge
- Midnight-wrapping windows supported (start > end)
- Commands sent only on state change (window transition, curve index change, day/night transition)
- Evaluate immediately on startup
- Day/night switching independent of schedule mode (runs even in "manual")
- Day/night sends `source_priority {mode: "day"}` or `source_priority {mode: "night"}`
- In time_of_day/curve modes, scheduler sends MANUAL source_priority (overrides DAY/NIGHT)
- When schedule mode changes to "manual", restore DAY/NIGHT based on current time

### Claude's Discretion
- Scheduler class architecture (single SchedulerLoop class vs separate evaluators)
- Time parsing and comparison implementation (datetime vs manual hour/minute math)
- ZMQ REQ socket lifecycle (create on startup vs create per command)
- Hot-reload mechanism (subscribe to SOCK_CONFIG_PUB like control/alarm managers)
- Telemetry publishing format for SCHED-07 (what fields in the schedule state message)
- Test strategy (mock ZMQ REQ socket, mock system clock for time-based tests)

### Deferred Ideas (OUT OF SCOPE)
- SCHED-08: Calendar-based scheduling (weekday/weekend)
- SCHED-09: Tariff-aware scheduling
- SCHED-10: Forecast-based scheduling
- Linear interpolation between curve points
- Scheduler status dashboard in HMI
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SCHED-01 | 1Hz evaluation loop sends setpoint commands to control_manager via ZMQ REQ on control_cmd socket | Async loop pattern from alarm_manager/control_manager; ZMQ REQ client with `encode_command_request` from ipc.py |
| SCHED-02 | Three scheduling modes: manual, time_of_day, curve | Config schema defines enum `["manual", "time_of_day", "curve"]`; mode field in schedule_config.yaml |
| SCHED-03 | time_of_day evaluates current time against windows, sends charge/discharge/idle + power_kw | Config defines time_windows array with start/end/action/power_kw; half-open interval matching |
| SCHED-04 | Curve reads 96-point array, calculates index from clock, sends setpoint | Config defines power_curve array with exactly 96 items; index = hour*4 + minute//15 |
| SCHED-05 | Day/night switching sends source_priority at day_start/night_start | Config defines day_night.day_start/night_start; control_manager accepts `source_priority` command with mode "day"/"night" |
| SCHED-06 | Hot-reload of schedule_config.yaml without restart | SUB on SOCK_CONFIG_PUB, filter for name="schedule_config", re-read from disk pattern (alarm_manager/control_manager) |
| SCHED-07 | Publishes current schedule state on ZMQ telemetry | PUB socket with encode_telemetry envelope, topic "schedule", multipart [topic, body] |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyzmq | (workspace) | ZMQ REQ/SUB/PUB sockets | Already used by all Python modules |
| msgpack | (workspace) | Message serialization | EMS standard via ems_common.ipc |
| pyyaml | (workspace) | Config file loading | Already used by config loaders |
| jsonschema | >=4.23 | Schema validation | Already used by control_manager, alarm_manager |
| ems-common | workspace | IPC contract, encode/decode helpers | Shared library |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio | stdlib | Async event loop | Main loop, signal handling |
| datetime | stdlib | Time parsing/comparison | HH:MM string parsing, current time |
| logging | stdlib | Structured logging | Standard Python logging |

**Installation:**
```bash
cd src/scheduler && uv add pyzmq msgpack pyyaml jsonschema
```

Note: `ems-common` is already a workspace dependency in pyproject.toml.

## Architecture Patterns

### Recommended Project Structure
```
src/scheduler/
  pyproject.toml                         # Already exists (add deps)
  src/ems_scheduler/
    __init__.py                          # Already exists (v0.1.0)
    __main__.py                          # Entry point (alarm_manager pattern)
    config.py                            # load_schedule_config() with JSON Schema
    loop.py                              # SchedulerLoop class (main logic)
```

### Pattern 1: Single SchedulerLoop Class (Recommended)
**What:** One class containing the 1Hz loop, time evaluation, ZMQ I/O, and config hot-reload. No separate evaluator classes needed -- the logic is simple enough for a single class.
**When to use:** Always (scheduler has ~4 methods of evaluation logic).
**Rationale:** alarm_manager and control_manager both use a single loop class. The scheduler is simpler than both -- adding evaluator abstractions would be over-engineering.

**Key internal state:**
```python
class SchedulerLoop:
    _config: dict[str, Any]          # Current schedule_config
    _config_path: Path | None        # For re-reading on hot-reload
    _mode: str                       # "manual" | "time_of_day" | "curve"
    _last_window_index: int | None   # Track active window for change detection
    _last_curve_index: int | None    # Track 96-point index for change detection
    _last_day_night: str | None      # "day" | "night" for transition detection
    _stop_event: asyncio.Event       # Graceful shutdown signal
    _tel_seq: int                    # Telemetry sequence counter
    _zmq_ctx: zmq.Context
    _req: zmq.Socket                 # REQ -> control_manager
    _config_sub: zmq.Socket          # SUB <- config_manager
    _pub: zmq.Socket                 # PUB -> telemetry
```

### Pattern 2: Config Loader (copy control_manager/config.py)
**What:** `load_schedule_config(path, schema_path)` function that loads YAML + validates against JSON Schema.
**Source:** Exact copy of `ems_control_manager.config.load_control_config` with paths changed.

```python
# Source: src/control_manager/python/src/ems_control_manager/config.py
_DEFAULT_SCHEMA_PATH: Path = Path("config/schemas/schedule_config.schema.json")

def load_schedule_config(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    resolved_schema_path: Path = schema_path or _DEFAULT_SCHEMA_PATH
    # ... identical YAML + JSON Schema validation pattern ...
```

### Pattern 3: Entry Point (__main__.py -- copy alarm_manager)
**What:** argparse + asyncio.run + signal handlers pattern.
**Source:** `src/alarm_manager/src/ems_alarm_manager/__main__.py`

```python
# Source: src/alarm_manager/src/ems_alarm_manager/__main__.py
async def run(args: argparse.Namespace) -> None:
    config = load_schedule_config(args.config)
    loop_obj = SchedulerLoop(config, config_path=args.config,
                             req_endpoint=os.environ.get("EMS_CONTROL_CMD_ENDPOINT"),
                             config_sub_endpoint=os.environ.get("EMS_CONFIG_SUB_ENDPOINT"),
                             pub_endpoint=os.environ.get("EMS_TELEMETRY_PUB_ENDPOINT"))
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

### Pattern 4: Config Hot-Reload (SUB on SOCK_CONFIG_PUB)
**What:** Subscribe to `config_reload` topic on SOCK_CONFIG_PUB. When event with `name="schedule_config"` arrives, re-read from disk and swap config atomically.
**Source:** Both `control_manager/loop.py:_poll_config_reload()` and `alarm_manager/loop.py:_poll_config_reload()` use identical pattern.

```python
# Source: src/alarm_manager/src/ems_alarm_manager/loop.py:318-359
def _poll_config_reload(self) -> None:
    while True:
        try:
            frames = self._config_sub.recv_multipart(zmq.NOBLOCK)
        except zmq.Again:
            break
        data = msgpack.unpackb(frames[1], raw=False)
        if data.get("name") != "schedule_config":
            continue
        new_config = load_schedule_config(self._config_path)
        self._config = new_config
        self._mode = new_config["mode"]
        # Reset tracking state to force re-evaluation
        self._last_window_index = None
        self._last_curve_index = None
```

### Pattern 5: ZMQ REQ Client for Commands
**What:** Scheduler connects to SOCK_CONTROL_CMD as REQ client. Send command, wait for reply with timeout.
**Key detail:** control_manager binds REP on this socket. Scheduler connects as client (same as HMI will do).

```python
# Source: src/common/python/src/ems_common/ipc.py
from ems_common.ipc import encode_command_request, decode_command_response, SOCK_CONTROL_CMD

def _send_command(self, action: str, params: dict) -> bool:
    """Send command to control_manager. Returns True on success."""
    raw = encode_command_request(action, params)
    self._req.send(raw)
    if self._req.poll(5000):  # 5s timeout
        reply = decode_command_response(self._req.recv())
        if reply["status"] == "ok":
            return True
        logger.warning("Command %s rejected: %s", action, reply.get("error_msg"))
        return False
    else:
        logger.warning("Command %s timed out (5s)", action)
        # Must close and recreate REQ socket after timeout (ZMQ REQ/REP lockstep)
        self._req.close()
        self._req = self._zmq_ctx.socket(zmq.REQ)
        self._req.setsockopt(zmq.LINGER, 0)
        self._req.connect(self._req_endpoint)
        return False
```

### Pattern 6: Telemetry PUB (multipart topic + envelope)
**What:** Publish schedule state as multipart ZMQ message: [topic_string, msgpack_envelope].
**Source:** Both data_manager/publisher.py and control_manager/loop.py use this pattern.

```python
# Source: src/data_manager/python/src/ems_data_manager/publisher.py:233-238
TOPIC_SCHEDULE: str = "schedule"  # New topic for scheduler

def _publish_state(self) -> None:
    self._tel_seq += 1
    ts_ms = int(time.time() * 1000)
    payload = {
        "mode": self._mode,
        "active_window": self._describe_active_window(),
        "curve_index": self._last_curve_index,
        "day_night": self._last_day_night,
        "next_transition": self._next_transition_time(),
    }
    body = encode_telemetry(ts_ms, self._tel_seq, "scheduler", TOPIC_SCHEDULE, payload)
    self._pub.send_string(TOPIC_SCHEDULE, zmq.SNDMORE | zmq.NOBLOCK)
    self._pub.send(body, zmq.NOBLOCK)
```

### Anti-Patterns to Avoid
- **Sending commands every tick:** Only send on state change (window transition, curve index change, day/night). Control_manager persists the last setpoint.
- **Writing to RTDB:** Scheduler has no RTDB section. It is command-only via ZMQ REQ.
- **Blocking ZMQ calls in async loop:** Use NOBLOCK for SUB/PUB. For REQ, use poll() with timeout.
- **Not recreating REQ after timeout:** ZMQ REQ/REP is lockstep. If recv times out, the socket is stuck. Must close and recreate.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Config loading + validation | Custom YAML parser | `load_schedule_config()` mirroring `load_control_config()` | jsonschema + pyyaml, proven pattern |
| Message encoding | Custom msgpack wrappers | `encode_command_request`, `encode_telemetry` from ems_common.ipc | Contract consistency across modules |
| Time zone handling | Custom TZ logic | `datetime.datetime.now().strftime("%H:%M")` for local time | System clock is authoritative, no TZ conversion needed |
| Async loop timing | `time.sleep` / busy loop | `asyncio.sleep` with timing correction (control_manager pattern) | Accurate 1Hz without drift |

## Common Pitfalls

### Pitfall 1: ZMQ REQ/REP Lockstep After Timeout
**What goes wrong:** If scheduler sends a REQ but does not receive a REP (timeout), the socket enters a broken state. Next send() will raise EFSM error.
**Why it happens:** ZMQ REQ socket enforces strict send-recv-send-recv alternation.
**How to avoid:** After a poll() timeout, close the socket and create a new one. Do NOT try to send again on the same socket.
**Warning signs:** `zmq.error.ZMQError: Operation cannot be accomplished in current state`

### Pitfall 2: Midnight-Wrapping Time Windows
**What goes wrong:** A window from "22:00" to "06:00" fails simple `start <= now < end` check because 22:00 > 06:00.
**Why it happens:** Off-peak charging windows commonly span midnight.
**How to avoid:** If `start > end`, the window wraps midnight. Match condition: `now >= start OR now < end`.
**Warning signs:** Off-peak charging window never activates.

### Pitfall 3: Config Reload Stale State
**What goes wrong:** After hot-reload changes the mode or windows, the scheduler continues using cached tracking state (last_window_index, last_curve_index) and misses the first evaluation.
**Why it happens:** Tracking state from old config is meaningless in new config context.
**How to avoid:** Reset all tracking state (`_last_window_index = None`, `_last_curve_index = None`) after config swap. This forces re-evaluation and command send on next tick.

### Pitfall 4: Two-Step Source Priority Before Setpoint
**What goes wrong:** Sending `manual_setpoint` without first sending `source_priority {mode: "manual"}` gets rejected by control_manager (only accepts manual setpoints in MANUAL mode).
**Why it happens:** Control_manager Phase 14 requires MANUAL source priority for external setpoints.
**How to avoid:** On mode transition to time_of_day or curve, first send `source_priority {mode: "manual"}`, then send `manual_setpoint`. Track whether MANUAL priority has been set.

### Pitfall 5: Day/Night vs Schedule Mode Interaction
**What goes wrong:** Scheduler in time_of_day mode sends `source_priority {mode: "manual"}` which overrides DAY/NIGHT. When mode switches to "manual", DAY/NIGHT should be restored but is stuck on MANUAL.
**Why it happens:** Day/night and schedule mode use the same `source_priority` command.
**How to avoid:** On schedule mode change to "manual", immediately evaluate current time and send appropriate DAY/NIGHT source_priority. Track `_schedule_owns_priority: bool` to know whether scheduler has overridden DAY/NIGHT.

### Pitfall 6: PUB Socket Needs Bind, Not Connect
**What goes wrong:** Using `connect()` on PUB socket means no subscribers can receive messages.
**Why it happens:** Confusing bind/connect roles. The publisher typically binds.
**How to avoid:** Scheduler should bind its own PUB socket on a dedicated endpoint (like control_manager does with `SOCK_CONTROL_PUB`). OR connect to SOCK_TELEMETRY if data_manager has a PULL/XPUB relay. Based on the codebase, each module binds its own PUB. Define `SOCK_SCHEDULER_PUB = "ipc:///run/ems/scheduler_pub.sock"` or publish on the SOCK_TELEMETRY PUB directly. Looking at the code: data_manager binds SOCK_TELEMETRY as its PUB; control_manager binds its own SOCK_CONTROL_PUB. Scheduler should bind its own dedicated PUB socket.

## Code Examples

### Time Window Matching
```python
def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' to (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])

def _time_to_minutes(hour: int, minute: int) -> int:
    """Convert (hour, minute) to minutes since midnight."""
    return hour * 60 + minute

def _in_window(now_min: int, start_min: int, end_min: int) -> bool:
    """Check if now_min is in [start, end) with midnight wrapping."""
    if start_min < end_min:
        return start_min <= now_min < end_min
    else:
        # Wraps midnight: 22:00-06:00 => now >= 22:00 OR now < 06:00
        return now_min >= start_min or now_min < end_min
```

### Command Dispatch Sequence (time_of_day)
```python
def _evaluate_time_of_day(self, now: datetime.datetime) -> None:
    now_min = now.hour * 60 + now.minute
    active_index: int | None = None

    for i, window in enumerate(self._config["time_windows"]):
        start_h, start_m = _parse_time(window["start"])
        end_h, end_m = _parse_time(window["end"])
        if _in_window(now_min, start_h * 60 + start_m, end_h * 60 + end_m):
            active_index = i
            break  # First match wins

    if active_index == self._last_window_index:
        return  # No change -- don't re-send

    self._last_window_index = active_index

    if active_index is not None:
        window = self._config["time_windows"][active_index]
        action = window["action"]
        power_kw = window["power_kw"]
        if action == "charge":
            self._send_command("manual_setpoint", {"power_kw": -abs(power_kw)})
        elif action == "discharge":
            self._send_command("manual_setpoint", {"power_kw": abs(power_kw)})
        else:  # idle
            self._send_command("manual_setpoint", {"power_kw": 0})
    else:
        # Outside all windows -- idle
        self._send_command("manual_setpoint", {"power_kw": 0})
```

### Curve Index Calculation
```python
def _evaluate_curve(self, now: datetime.datetime) -> None:
    index = now.hour * 4 + now.minute // 15  # 0-95
    if index == self._last_curve_index:
        return  # Same 15-min interval -- no change

    self._last_curve_index = index
    power_kw = self._config["power_curve"][index]
    self._send_command("manual_setpoint", {"power_kw": power_kw})
```

### Control_manager Command API (from loop.py:338-379)
The exact actions control_manager's `_dispatch_command` accepts:

| Action | Params | What It Does |
|--------|--------|-------------|
| `mode_change` | `{"target_state": "idle"}` | Request state machine transition |
| `manual_setpoint` | `{"power_kw": 50.0}` | Set power setpoint (positive=discharge) |
| `source_priority` | `{"mode": "day"}` | Switch to DAY source priority |
| `source_priority` | `{"mode": "night"}` | Switch to NIGHT source priority |
| `source_priority` | `{"mode": "manual"}` | Switch to MANUAL (for scheduler setpoints) |
| `fault_reset` | `{}` | Reset fault state |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Direct RTDB writes | ZMQ REQ command API | Phase 14 (control_manager) | Scheduler uses existing command API, no RTDB access |
| Custom config hot-reload | SUB on SOCK_CONFIG_PUB | Phase 16 (config_manager) | Standard reload pattern for all hot-reloadable configs |

## Open Questions

1. **Scheduler PUB socket endpoint**
   - What we know: Each module binds its own PUB (data_manager on SOCK_TELEMETRY, control_manager on SOCK_CONTROL_PUB, alarm_manager on SOCK_ALARM_PUB)
   - What's unclear: Should scheduler bind its own `SOCK_SCHEDULER_PUB` or use the SOCK_TELEMETRY PUB?
   - Recommendation: Bind a dedicated `ipc:///run/ems/scheduler_pub.sock` and add `SOCK_SCHEDULER_PUB` to ipc.py. Add `TOPIC_SCHEDULE = "schedule"` to ipc.py. This follows the established module-owns-its-PUB pattern.

2. **Charge sign convention**
   - What we know: power_curve uses positive=discharge, negative=charge. time_of_day windows have separate `action` field (charge/discharge/idle) with unsigned `power_kw`.
   - What's unclear: Does `manual_setpoint` expect signed power (negative=charge) or does action determine sign?
   - Recommendation: Looking at control_manager `request_manual_setpoint(power_kw)`, it takes a single float. Convention from control_config: positive=discharge, negative=charge. Scheduler should negate power_kw for charge windows.

3. **Telemetry payload fields for SCHED-07**
   - Recommendation: `mode`, `active_window` (object or null), `curve_index` (int or null), `curve_power_kw` (float or null), `day_night` ("day"/"night"), `next_transition` (ISO string or null). Keep it simple -- HMI Settings screen (Phase 19) needs to display current schedule state.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (workspace dev dependency) |
| Config file | `pyproject.toml` (workspace root) |
| Quick run command | `uv run pytest src/scheduler/tests/ -x -q` |
| Full suite command | `uv run pytest src/scheduler/tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCHED-01 | 1Hz loop sends REQ commands | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_1hz_sends_command -x` | -- Wave 0 |
| SCHED-02 | Three modes evaluated correctly | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_mode_manual_no_commands -x` | -- Wave 0 |
| SCHED-03 | time_of_day window matching + commands | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_time_of_day_window_match -x` | -- Wave 0 |
| SCHED-04 | Curve index calculation + setpoint | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_curve_index_calculation -x` | -- Wave 0 |
| SCHED-05 | Day/night source_priority switching | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_day_night_transition -x` | -- Wave 0 |
| SCHED-06 | Hot-reload applies new config | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_config_reload -x` | -- Wave 0 |
| SCHED-07 | Telemetry PUB publishes state | unit | `uv run pytest src/scheduler/tests/test_loop.py::test_telemetry_publish -x` | -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest src/scheduler/tests/ -x -q`
- **Per wave merge:** `uv run pytest src/scheduler/tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/scheduler/tests/` directory -- does not exist
- [ ] `src/scheduler/tests/conftest.py` -- ZMQ test fixtures (mock REP server for SOCK_CONTROL_CMD, mock PUB for SOCK_CONFIG_PUB, mock SUB for telemetry capture)
- [ ] `src/scheduler/tests/test_config.py` -- config loader tests
- [ ] `src/scheduler/tests/test_loop.py` -- main loop evaluation tests
- [ ] Dependencies: `uv add --dev pyzmq msgpack pyyaml jsonschema` in scheduler package

## Sources

### Primary (HIGH confidence)
- `src/common/python/src/ems_common/ipc.py` -- socket paths, topic constants, encode/decode helpers
- `src/control_manager/python/src/ems_control_manager/loop.py` -- command dispatch API (line 338-379), config reload pattern, telemetry PUB pattern
- `src/control_manager/python/src/ems_control_manager/config.py` -- config loader with JSON Schema validation
- `src/alarm_manager/src/ems_alarm_manager/__main__.py` -- async entry point pattern
- `src/alarm_manager/src/ems_alarm_manager/loop.py:318-365` -- config hot-reload SUB pattern
- `src/data_manager/python/src/ems_data_manager/publisher.py` -- telemetry PUB multipart format
- `config/schedule_config.yaml` -- default config structure
- `config/schemas/schedule_config.schema.json` -- full schema with x-hot-reload, x-mutable
- `deploy/systemd/scheduler.service` -- service configuration (After=control_manager, config_manager)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in workspace, patterns proven
- Architecture: HIGH -- direct copy of alarm_manager/control_manager patterns
- Pitfalls: HIGH -- derived from actual code review of ZMQ REQ/REP and time logic
- Integration: HIGH -- command API verified from control_manager dispatch code

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable -- no external dependencies changing)
