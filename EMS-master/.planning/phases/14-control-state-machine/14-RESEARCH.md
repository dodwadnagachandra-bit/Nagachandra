# Phase 14: Control State Machine - Research

**Researched:** 2026-03-15
**Domain:** Python async state machine, RTDB seqlock write, ZMQ REQ/REP, PCS Modbus command dispatch
**Confidence:** HIGH — all findings verified against project source code; no external library unknowns

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**State Transitions**

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

**State Definitions**

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

**PCS Command Path Mechanics**

| Aspect | Decision |
|--------|----------|
| Setpoint path | control_manager → RTDB `system.active_setpoint_kw` → comm_manager → PCS register 0x500E |
| Command path | control_manager → RTDB `system.pcs_command` + `pcs_command_seq` → comm_manager → PCS registers 0x0291/0x5064 |
| Command dedup | Monotonic `pcs_command_seq` counter — comm_manager only acts on increment |
| RTDB struct change | Add `pcs_command` (uint8), `pcs_command_seq` (uint32), `active_derating_pct` (float) to `ems_system_t` |
| Comm_manager changes | Add `write_setpoint()` and `process_command()` to PcsDevice, called in existing poll loop |
| Crash safety | Stale setpoint in RTDB is safe — comm_manager keeps sending last known value |
| Single-writer rule | Preserved — control_manager writes system section, comm_manager writes PCS Modbus |

**PCS On/Off Sequencing**

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

**ZMQ Command API**

| Command | Request Params | Success Response | Error Response |
|---------|---------------|-----------------|----------------|
| `mode_change` | `{target_state: "standby"\|"idle"}` | `{status: "ok", from: "idle", to: "standby"}` | `{status: "error", error_msg: "invalid transition from FAULT"}` |
| `manual_setpoint` | `{power_kw: float}` | `{status: "ok", accepted_kw: 25.0}` | `{status: "error", error_msg: "not in STANDBY/CHARGING/DISCHARGING"}` |
| `source_priority` | `{mode: "day"\|"night"\|"manual"}` | `{status: "ok", mode: "manual"}` | `{status: "error", error_msg: "invalid mode"}` |
| `fault_reset` | `{}` | `{status: "ok", from: "fault", to: "idle"}` | `{status: "error", error_msg: "not in FAULT state"}` |
| `maintenance_enter` | `{}` | `{status: "ok", from: "standby", to: "maintenance"}` | `{status: "error", error_msg: "already in MAINTENANCE"}` |
| `maintenance_exit` | `{}` | `{status: "ok", from: "maintenance", to: "idle"}` | `{status: "error", error_msg: "not in MAINTENANCE"}` |

Command handling by state:
- Stable states: process immediately
- STARTING/STOPPING sub-states: reject with remaining time in error_msg
- EMERGENCY: reject all
- MAINTENANCE: reject all except maintenance_exit
- FAULT: accept fault_reset even during retry countdown

### Claude's Discretion

- State machine class design (single class vs strategy pattern)
- Internal sub-state tracking data structures
- 1Hz loop implementation (asyncio sleep vs timer)
- RTDB read pattern (read all sections once per tick vs on-demand)
- ZMQ REP polling integration with async control loop
- Startup sequence (config load, RTDB attach, ZMQ bind order)
- Test strategy for state transitions and PCS sequencing

### Deferred Ideas (OUT OF SCOPE)

- Source priority dispatch (DAY/NIGHT/MANUAL) — Phase 16 (CTRL-04)
- SOC charge/discharge cutoff limits — Phase 16 (CTRL-05)
- Temperature derating curves — Phase 16 (CTRL-06)
- Configurable power ramp rate — Phase 16 (CTRL-08, Phase 14 uses simple 2s zero-then-off)
- Interlock checks (safety state + PCS online) — Phase 16 (CTRL-09)
- Hot-reload of control_config — Phase 16 (CTRL-11)
- Grid code compliance — future milestone (CTRL-13)
- Multi-PCS master/slave — future milestone (CTRL-14)
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CTRL-01 | 1Hz control loop reads RTDB (BMS, PCS, system sections), evaluates state machine, computes power setpoint, and writes to RTDB system section via seqlock | `_seqlock_read_section()` pattern in publisher.py; seqlock write pattern in pcs_device.py; `asyncio.sleep(1.0)` timing loop in publisher.py |
| CTRL-02 | State machine implements 8 states with validated transitions and ZMQ state_change events | `ems_control_state_t` already defined in ems_types.h with all 8 values; transition table fully specified in CONTEXT.md; `encode_event()` in ipc.py for state_change PUSH |
| CTRL-03 | PCS command dispatch writes active power setpoint (0x500E) via comm_manager's Modbus client, with on/off (0x0291) and fault reset (0x5064) sequencing | RTDB `system` section is single-writer (control_manager writes); new RTDB fields `pcs_command`/`pcs_command_seq` route commands through comm_manager; PcsDevice already has `_write_to_rtdb` seqlock pattern to extend |
| CTRL-07 | PCS fault handling reads fault words (0x1700-0x1707), transitions to FAULT state, supports auto-retry (configurable count) or manual reset via ZMQ | `control_config.yaml` has `state_machine.fault_retry_count` (0-10, default 3); `pcs.fault_code` already in RTDB EmsPcs |
| CTRL-10 | ZMQ REQ/REP command API on control_cmd socket accepts mode_change, manual_setpoint, source_priority_override, fault_reset, maintenance_enter/exit | `SOCK_CONTROL_CMD = "ipc:///run/ems/control_cmd.sock"` defined in ipc.py; `encode_command_request/response` helpers ready; `decode_command_request` returns (action, params) |
| CTRL-12 | Control state and active setpoint published on ZMQ telemetry at 1Hz | `TOPIC_CONTROL_STATE = "control.state"` and `TOPIC_SYSTEM = "system"` defined; `encode_telemetry()` ready; control_manager publishes on PUB socket or PUSH to logger |
</phase_requirements>

---

## Summary

Phase 14 builds the control_manager Python module from its current stub (v0.1.0) into a fully functional 1Hz state machine. The module is pure Python — no C hot path is needed at 1Hz. It reads RTDB sections via seqlock, evaluates state transitions, writes commands back to the RTDB system section (also via seqlock), and serves a ZMQ REQ/REP command API.

All infrastructure is already in place from M1: the RTDB struct, seqlock primitives, ZMQ socket paths, IPC encode/decode helpers, and the PCS Modbus simulator. The phase's primary engineering challenges are (1) coordinating the RTDB struct update across C and Python at M2 start, (2) implementing the non-blocking 10-second PCS on/off sequencing using timestamp comparison inside the 1Hz loop, and (3) integrating the ZMQ REP poll with the asyncio control loop without blocking.

The phase explicitly defers source priority logic, SOC limits, derating, and ramping to Phase 16 — the state machine in Phase 14 accepts manual setpoints only and uses a simplified 2-second zero-then-off ramp for PCS stop.

**Primary recommendation:** Model the state machine as a single `ControlStateMachine` class with an explicit `_SubState` enum for internal STARTING/STOPPING tracking, driven by a `ControlLoop` class that owns the asyncio event loop, RTDB handle, and ZMQ sockets. This mirrors the `CommOrchestrator` pattern from comm_manager.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyzmq | >=27.1.0 | ZMQ REQ/REP + PUB sockets | Already in workspace dev deps; all M1 modules use it |
| msgpack | >=1.0 | IPC serialization | Project-wide standard; `ems_common.ipc` wraps it |
| pyyaml | >=6.0 | Load control_config.yaml | Used across all M1 config loading |
| asyncio | stdlib | 1Hz loop + signal handling | Established pattern in data_manager, comm_manager |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ctypes | stdlib | RTDB struct access via shared memory | Required — RTDB is a C struct in POSIX shm |
| multiprocessing.shared_memory | stdlib | Attach to C-owned RTDB shm | Used via `ems_common.rtdb.attach_rtdb()` |
| jsonschema | >=4.23 | Validate control_config.yaml at load | Already used in config_manager |

### Installation
```bash
# No new deps needed — all are in root pyproject.toml workspace dev deps
# control_manager/python/pyproject.toml already depends on ems-common
uv sync --all-packages
```

---

## Architecture Patterns

### Recommended Project Structure
```
src/control_manager/python/src/ems_control_manager/
├── __init__.py           # version string only (already exists)
├── __main__.py           # entry point: parse args, asyncio.run(main())
├── loop.py               # ControlLoop — owns asyncio loop, RTDB, ZMQ sockets, 1Hz tick
├── state_machine.py      # ControlStateMachine — pure state logic, no I/O
├── config.py             # load_control_config() — YAML load + validate
└── tests/
    ├── __init__.py
    ├── test_state_machine.py   # unit tests for all transitions
    ├── test_loop.py            # asyncio tests for tick timing, ZMQ poll
    └── test_config.py          # config load and validation
```

### Pattern 1: 1Hz Loop with Timing Correction
**What:** asyncio.sleep-based loop that corrects for processing time drift
**When to use:** All 1Hz loops in the project use this pattern

```python
# Source: src/data_manager/python/src/ems_data_manager/publisher.py (publish_loop)
async def _control_loop(self) -> None:
    """Run state machine at configured interval, correcting for processing time."""
    interval_s: float = self._config["state_machine"]["loop_interval_ms"] / 1000.0
    while not self._stop_event.is_set():
        tick_start: float = time.monotonic()
        self._tick()                          # one state machine evaluation
        elapsed: float = time.monotonic() - tick_start
        sleep_s: float = max(0.0, interval_s - elapsed)
        await asyncio.sleep(sleep_s)
```

### Pattern 2: RTDB Seqlock Read (Python)
**What:** Read an RTDB section atomically using the seqlock protocol
**When to use:** Every time control_manager reads from RTDB (BMS, PCS, GPIO sections)

```python
# Source: src/data_manager/python/src/ems_data_manager/publisher.py (_seqlock_read_section)
def _seqlock_read_section(section: ctypes.Structure) -> ctypes.Structure:
    section_type = type(section)
    copy = section_type()
    for _ in range(100):  # _SEQLOCK_MAX_RETRIES
        seq1 = section.lock.sequence
        if seq1 & 1:        # odd = write in progress
            continue
        ctypes.memmove(ctypes.addressof(copy), ctypes.addressof(section),
                       ctypes.sizeof(section_type))
        seq2 = section.lock.sequence
        if seq1 == seq2:
            return copy
    return copy             # fallback (extremely rare)
```

### Pattern 3: RTDB Seqlock Write (Python)
**What:** Write to the system section atomically using seqlock protocol
**When to use:** control_manager is the single writer for the RTDB system section

```python
# Source: src/comm_manager/python/src/ems_comm_manager/pcs_device.py (_write_to_rtdb)
# Adapted for system section write:
def _write_system(rtdb: EmsRtdb, state: int, setpoint_kw: float, ...) -> None:
    sys = rtdb.system
    sys.lock.sequence += 1          # begin write (odd)
    sys.control_state = state
    sys.active_setpoint_kw = setpoint_kw
    sys.last_update_ms = now_ms
    sys.lock.sequence += 1          # end write (even)
```

### Pattern 4: ZMQ REP Non-Blocking Poll in Async Loop
**What:** Poll a REP socket without blocking the asyncio event loop
**When to use:** Command handling inside the 1Hz loop

```python
# Source: established pattern from config_manager / safety_manager ZMQ REP
# Use zmq.asyncio with NOBLOCK or poll with timeout=0
async def _poll_commands(self) -> None:
    """Check REP socket once; process at most one command per tick."""
    try:
        raw = self._rep_socket.recv(zmq.NOBLOCK)
        action, params = decode_command_request(raw)
        response = self._handle_command(action, params)
        self._rep_socket.send(response)
    except zmq.Again:
        pass  # no command waiting — normal
```

### Pattern 5: Non-Blocking Timer for PCS Sequencing
**What:** Record timestamp on transition start, compare on each tick
**When to use:** IDLE→STANDBY (10s wait) and STANDBY→IDLE (2s zero + 10s wait in Phase 14)

```python
# Source: CONTEXT.md decision — "Non-blocking — record timestamp, check elapsed on each 1Hz tick"
class _InternalSubState(enum.Enum):
    NONE = "none"
    STARTING = "starting"    # IDLE→STANDBY: waiting for PCS RUNNING
    STOPPING = "stopping"    # STANDBY→IDLE: waiting for PCS OFF

# In state machine:
def _tick_starting(self, now_s: float, pcs_state: int) -> None:
    elapsed = now_s - self._transition_start_s
    if pcs_state == PCS_STATE_RUNNING:
        self._enter_state(EMS_STATE_STANDBY)
    elif elapsed >= _PCS_STARTUP_TIMEOUT_S:
        self._enter_fault("PCS startup timeout")
    # else: still waiting, remain in STARTING sub-state
```

### Pattern 6: Signal Handling with asyncio Event
**What:** SIGTERM/SIGINT set a stop event that the loop checks
**When to use:** All Python services in this project

```python
# Source: src/data_manager/python/src/ems_data_manager/__main__.py
loop = asyncio.get_running_loop()
stop_event = asyncio.Event()

def _signal_handler() -> None:
    stop_event.set()

for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, _signal_handler)
```

### Anti-Patterns to Avoid

- **Blocking sleep inside the 1Hz loop:** Never use `time.sleep()` in the async loop — use `asyncio.sleep()` or timestamp comparison. A 10-second `time.sleep()` would block all asyncio tasks.
- **Direct Modbus writes from control_manager:** PCS commands must go through RTDB (system.pcs_command + pcs_command_seq) then comm_manager. Direct pymodbus calls from control_manager would break the single-writer-per-section rule.
- **Reading RTDB without seqlock:** Never read `rtdb.pcs.state` directly without `_seqlock_read_section()` — a write in progress would return torn data.
- **Blocking ZMQ recv:** Never call `rep_socket.recv()` without `zmq.NOBLOCK` in the async loop — it blocks the entire event loop.
- **Calling asyncio.get_event_loop() in signal handler:** The signal handler must be registered via `loop.add_signal_handler()`, not via `signal.signal()`, to work correctly with asyncio. The `signal.signal()` approach (as used in comm_manager) has a threading risk but works in practice — prefer `loop.add_signal_handler()`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| IPC encoding/decoding | Custom msgpack wrapper | `ems_common.ipc.encode_command_request/response`, `encode_telemetry`, `encode_event` | Already implemented, tested, and used by every M1 module |
| RTDB attachment | Custom shm open/mmap | `ems_common.rtdb.attach_rtdb()` / `detach_rtdb()` | Handles resource_tracker unregister, magic/version validation |
| RTDB seqlock read | Custom sync primitive | `_seqlock_read_section()` from publisher.py (copy-paste or move to ems_common) | Handles retries, fallback, thread-safe copy |
| Config YAML load | Direct yaml.safe_load | Load pattern from config_manager (load + jsonschema validate) | Schema already exists at config/schemas/control_config.schema.json |
| PCS state enum | Custom int constants | `PCS_STATE_OFF/STANDBY/RUNNING/FAULT` — match `ems_pcs_state_t` values from ems_types.h | C and Python must agree on numeric values |
| Control state enum | Custom string constants | `EMS_STATE_INIT=0` through `EMS_STATE_MAINTENANCE=7` — match `ems_control_state_t` from ems_types.h | Already defined in C; Python ctypes uses int values |

**Key insight:** The M1 infrastructure was specifically designed for M2 consumption. Every IPC pattern control_manager needs already has a working implementation in at least one M1 module.

---

## Common Pitfalls

### Pitfall 1: RTDB Struct Size Mismatch After Adding Fields
**What goes wrong:** Adding `pcs_command` (uint8), `pcs_command_seq` (uint32), `active_derating_pct` (float) to `ems_system_t` in C changes `sizeof(ems_rtdb_t)`. Python's `EmsSystem` ctypes mirror must match exactly — field order, types, and padding all matter.
**Why it happens:** ctypes struct layout is determined by field order and C alignment rules. Missing padding or wrong type widths cause all subsequent field offsets to be wrong.
**How to avoid:** Update `rtdb.h` first, rebuild the C size assertion test (`test_rtdb.c`), then update `rtdb.py` to match exactly. Run `test_rtdb.py::test_rtdb_size_matches_c` (currently hardcoded to `C_SIZEOF_RTDB = 1800808`) — this will fail until both are updated.
**Warning signs:** `test_rtdb.py` failures; wrong values read back from RTDB; system section seqlock value contamination.

### Pitfall 2: Seqlock Write Not Atomic Due to Padding
**What goes wrong:** If `ems_system_t` has implicit padding between fields, a ctypes write that sets fields individually may leave the seqlock window open longer than needed, or the sequence counter placement relative to new fields may be wrong.
**Why it happens:** New fields added after `ems_uptime_s` may introduce or remove padding depending on alignment. `uint8_t pcs_command` after `uint32_t ems_uptime_s` may not need padding; `uint32_t pcs_command_seq` is naturally aligned; `float active_derating_pct` is 4-byte aligned — no padding issue expected but must verify.
**How to avoid:** Use the `_Static_assert(sizeof(ems_system_t) == N)` pattern after adding fields. Confirm Python struct size with `ctypes.sizeof(EmsSystem)`.

### Pitfall 3: ZMQ REP Socket "Must Reply" Protocol
**What goes wrong:** ZMQ REP pattern requires alternating recv/send. If the 1Hz loop receives a command but an exception prevents sending the reply, the socket enters an invalid state — subsequent recvs block forever.
**Why it happens:** ZMQ REP is a strict request-reply protocol. Any exception between recv and send leaves the socket stuck.
**How to avoid:** Wrap the entire recv-process-send cycle in a try/except that always sends an error response, even if processing fails.

### Pitfall 4: Asyncio and ZMQ Socket Thread Safety
**What goes wrong:** Using a synchronous `zmq.Socket` directly in asyncio code that also runs coroutines can cause event loop blocking if the socket operations take time (e.g., slow consumers).
**Why it happens:** Sync zmq sockets block the OS thread; asyncio runs on the same thread.
**How to avoid:** Use `zmq.asyncio.Context()` and async sockets for the telemetry PUB (which may have back-pressure). For the REP socket polled with `NOBLOCK`, a sync socket is fine — it returns immediately with `zmq.Again`.

### Pitfall 5: MAINTENANCE State Across Restarts
**What goes wrong:** MAINTENANCE state must persist across control_manager restarts (per CONTEXT.md). If MAINTENANCE is only tracked in memory, a crash and restart drops the lockout — hardware may dispatch unexpectedly.
**Why it happens:** In-memory state is lost on restart.
**How to avoid:** Write MAINTENANCE state to RTDB system section via seqlock on every state change. On startup (INIT), read `rtdb.system.control_state` and if it is MAINTENANCE, remain in MAINTENANCE rather than transitioning to IDLE.

### Pitfall 6: Fault Retry Timer Not Cancellable
**What goes wrong:** If fault_reset ZMQ command arrives during an active retry countdown, the retry timer must be cancelled and the fault cleared immediately (per CONTEXT.md: "fault_reset accepted even during retry countdown — operator override always works").
**Why it happens:** Simple sleep-based timers cannot be interrupted.
**How to avoid:** Track retry state with a timestamp (`_retry_start_s`), retry count (`_retry_count`), and a flag (`_fault_reset_requested`). The ZMQ command handler sets the flag; the tick function checks it before sleeping to the next retry.

### Pitfall 7: PCS Command Dedup Sequence Counter Rollover
**What goes wrong:** `pcs_command_seq` is uint32 (0–4294967295). After ~136 years at 1 command/second this wraps to 0. comm_manager must handle the wrap.
**Why it happens:** Naive `if new_seq != last_seq` comparison breaks at wrap.
**How to avoid:** Use `if (new_seq - last_seq) & 0xFFFFFFFF != 0` (unsigned difference) in comm_manager's process_command(). At 1Hz control rate, rollover is theoretical but should be handled correctly.

---

## Code Examples

### RTDB New Fields (C — rtdb.h ems_system_t addition)
```c
/* Source: src/common/c/include/rtdb.h — ems_system_t, fields to ADD at M2 start */
typedef struct
{
    ems_seqlock_t          lock;
    uint64_t               last_update_ms;
    ems_control_state_t    control_state;
    ems_source_priority_t  source_priority;
    float                  active_setpoint_kw;
    float                  total_soc;
    float                  total_power_kw;
    float                  total_energy_kwh;
    uint32_t               ems_uptime_s;
    /* NEW FIELDS — Phase 14 M2 start */
    uint8_t                pcs_command;        /* 0=NONE,1=ON,2=OFF,3=FAULT_RESET */
    uint8_t                _pad_cmd[3];        /* align pcs_command_seq to 4 bytes */
    uint32_t               pcs_command_seq;    /* monotonic counter — cm acts on increment */
    float                  active_derating_pct; /* 0.0–100.0 — Phase 16 uses this */
} ems_system_t;
```

### RTDB New Fields (Python — rtdb.py EmsSystem addition)
```python
# Source: src/common/python/src/ems_common/rtdb.py — EmsSystem, fields to ADD at M2 start
class EmsSystem(ctypes.Structure):
    _fields_ = [
        ("lock", EmsSeqlock),
        ("last_update_ms", ctypes.c_uint64),
        ("control_state", ctypes.c_int),
        ("source_priority", ctypes.c_int),
        ("active_setpoint_kw", ctypes.c_float),
        ("total_soc", ctypes.c_float),
        ("total_power_kw", ctypes.c_float),
        ("total_energy_kwh", ctypes.c_float),
        ("ems_uptime_s", ctypes.c_uint32),
        # NEW FIELDS — Phase 14 M2 start
        ("pcs_command", ctypes.c_uint8),
        ("_pad_cmd", ctypes.c_uint8 * 3),
        ("pcs_command_seq", ctypes.c_uint32),
        ("active_derating_pct", ctypes.c_float),
    ]
```

### PcsDevice write_setpoint and process_command (comm_manager addition)
```python
# Source: to be added to src/comm_manager/python/src/ems_comm_manager/pcs_device.py
def write_setpoint(self) -> None:
    """Write active_setpoint_kw from RTDB to PCS register 0x500E."""
    if self._rtdb is None or self._client is None:
        return
    sys_copy = _seqlock_read_section(self._rtdb.system)
    setpoint_kw: float = sys_copy.active_setpoint_kw
    # PCS V1.24: 0x500E, scale = 10 (raw = kW * 10), signed 16-bit
    raw: int = int(round(setpoint_kw * 10))
    if raw < 0:
        raw = raw + 65536   # two's complement
    raw = max(0, min(65535, raw))
    self._client.write_register(0x500E, raw, slave=self._slave_id)

def process_command(self) -> None:
    """Check pcs_command_seq; if incremented, execute pcs_command."""
    if self._rtdb is None or self._client is None:
        return
    sys_copy = _seqlock_read_section(self._rtdb.system)
    new_seq: int = sys_copy.pcs_command_seq
    if (new_seq - self._last_cmd_seq) & 0xFFFFFFFF == 0:
        return  # no new command
    self._last_cmd_seq = new_seq
    cmd: int = sys_copy.pcs_command
    if cmd == 1:    # ON
        self._client.write_register(0x0291, 1, slave=self._slave_id)
    elif cmd == 2:  # OFF
        self._client.write_register(0x0291, 0, slave=self._slave_id)
    elif cmd == 3:  # FAULT_RESET
        self._client.write_register(0x5064, 1, slave=self._slave_id)
```

### ZMQ REP Command Handler (safe recv-process-send)
```python
# Source: pattern from ems_common/ipc.py + comm_manager ZMQ usage
def _poll_command_socket(self) -> None:
    """Poll REP socket once; always send a reply to avoid ZMQ stuck state."""
    try:
        raw: bytes = self._rep.recv(zmq.NOBLOCK)
    except zmq.Again:
        return   # nothing waiting
    try:
        action, params = decode_command_request(raw)
        result_bytes = self._dispatch_command(action, params)
    except Exception as exc:
        result_bytes = encode_command_response(
            STATUS_ERROR, error_msg=f"internal error: {exc}"
        )
    self._rep.send(result_bytes)  # always send
```

### State Change Event (PUSH to logger)
```python
# Source: ems_common/ipc.py encode_event + SOCK_LOGGER
from ems_common.ipc import encode_event, SOCK_LOGGER, SEVERITY_INFO, TOPIC_STATE_CHANGE

def _publish_state_change(
    push: zmq.Socket, from_state: str, to_state: str, ts_ms: int
) -> None:
    msg = encode_event(
        timestamp_ms=ts_ms,
        source="control_manager",
        severity=SEVERITY_INFO,
        event_type=TOPIC_STATE_CHANGE,
        message=f"State: {from_state} -> {to_state}",
        data={"from": from_state, "to": to_state},
    )
    push.send(msg)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Blocking state machine loops | asyncio with timestamp-based sub-state tracking | M1 established asyncio baseline | Non-blocking 1Hz loop supports concurrent ZMQ poll |
| Direct hardware writes | RTDB as command bus (single-writer-per-section) | M1 architectural decision | control_manager never touches Modbus directly |
| C state machine for speed | Pure Python at 1Hz | M2 decision (CONTEXT.md) | No C hot path needed; Python is sufficient |
| PCS states as raw ints | `ems_pcs_state_t` enum in ems_types.h | M1 (Phase 11) | Python must use matching int values |

---

## Open Questions

1. **RTDB size assertion value after new fields**
   - What we know: Current `C_SIZEOF_RTDB = 1800808` in `tests/test_rtdb.py`
   - What's unclear: Exact new size after adding `pcs_command`(1) + `_pad`(3) + `pcs_command_seq`(4) + `active_derating_pct`(4) = 12 bytes → new size should be 1800820
   - Recommendation: Rebuild the C test binary after `rtdb.h` update to get authoritative size; update `test_rtdb.py` constant accordingly. This is a Wave 0 task.

2. **ZMQ PUB socket ownership for control.state telemetry**
   - What we know: `data_manager` binds the PUB socket (`SOCK_TELEMETRY`); control_manager currently has no PUB socket
   - What's unclear: Should control_manager bind its own PUB (second PUB endpoint), or should it write to RTDB and let data_manager publish control.state at 1Hz alongside system?
   - Recommendation: Write control state to RTDB system section (already done as part of seqlock write); data_manager already publishes the system topic at 1Hz which includes `control_state` and `active_setpoint_kw`. For the additional `control.state` topic required by CTRL-12, control_manager should bind a separate PUB socket on a new endpoint (e.g., `ipc:///run/ems/control_pub.sock`) or use the PUSH-to-logger pattern. The simplest approach: control_manager publishes directly on SOCK_TELEMETRY as a second publisher (ZMQ PUB fan-out supports multiple publishers connecting to the same endpoint if data_manager connects rather than binds — but data_manager currently binds). Cleaner: add `SOCK_CONTROL_PUB` endpoint.
   - **Resolved for planning:** Use the existing `system` topic already published by data_manager (reads from RTDB) for RTDB-sourced 1Hz data, and add a direct control_manager PUB on `TOPIC_CONTROL_STATE` for state-change-triggered telemetry. Both satisfy CTRL-12.

3. **Comm_manager write API: FC06 (single register) vs FC16 (multiple registers)**
   - What we know: PCS Modbus V1.24 uses FC06 writes for 0x500E (power setpoint) and 0x0291 (on/off). pymodbus `AsyncModbusSerialClient` supports both.
   - What's unclear: `PcsDevice` currently uses only `read_holding_registers` (FC03). Adding `write_register` (FC06) requires the client reference to be available in `write_setpoint()`. In the current architecture, the Modbus client is created and owned by `CommOrchestrator` and passed to `poll()` — not stored on the device.
   - Recommendation: Phase 14 adds a `_client` reference stored on `PcsDevice` during `poll()` calls (or passed to dedicated `write_setpoint()` / `process_command()` methods). This requires a small refactor of `CommOrchestrator` to call these methods after each poll cycle.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.3.x |
| Config file | `pyproject.toml` (root) — `[tool.pytest.ini_options]` |
| Quick run command | `cd /home/overlord/EMS && uv run pytest src/control_manager/python/tests/ -x -q` |
| Full suite command | `cd /home/overlord/EMS && uv run pytest tests/ src/control_manager/python/tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CTRL-01 | 1Hz loop reads RTDB sections, writes system section via seqlock | unit | `uv run pytest src/control_manager/python/tests/test_loop.py -x -q` | Wave 0 |
| CTRL-01 | RTDB system section has new fields after struct update | unit | `uv run pytest tests/test_rtdb.py -x -q` | Exists (needs update) |
| CTRL-02 | All 8 states implemented; invalid transitions rejected | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py -x -q` | Wave 0 |
| CTRL-02 | State change events published on ZMQ PUSH | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_state_change_event -x -q` | Wave 0 |
| CTRL-03 | PCS command written to RTDB with incremented seq | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_pcs_command_dispatch -x -q` | Wave 0 |
| CTRL-03 | comm_manager write_setpoint / process_command methods | unit | `uv run pytest src/comm_manager/python/tests/test_pcs_device.py -x -q` | Exists (needs new tests) |
| CTRL-07 | FAULT on PCS fault_code non-zero; auto-retry count | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_fault_handling -x -q` | Wave 0 |
| CTRL-07 | manual fault_reset cancels retry countdown | unit | `uv run pytest src/control_manager/python/tests/test_state_machine.py::test_fault_reset_override -x -q` | Wave 0 |
| CTRL-10 | ZMQ REP handles all 6 commands with correct responses | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_zmq_command_api -x -q` | Wave 0 |
| CTRL-10 | Commands rejected during transitions with remaining time | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_command_rejected_during_transition -x -q` | Wave 0 |
| CTRL-12 | Control state + setpoint published at 1Hz on ZMQ | unit | `uv run pytest src/control_manager/python/tests/test_loop.py::test_telemetry_publish -x -q` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest src/control_manager/python/tests/ -x -q`
- **Per wave merge:** `uv run pytest tests/ src/control_manager/python/tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/control_manager/python/tests/__init__.py` — test package init
- [ ] `src/control_manager/python/tests/test_state_machine.py` — covers CTRL-02, CTRL-03, CTRL-07
- [ ] `src/control_manager/python/tests/test_loop.py` — covers CTRL-01, CTRL-10, CTRL-12
- [ ] `src/control_manager/python/tests/test_config.py` — covers config load and validation
- [ ] Update `tests/test_rtdb.py::C_SIZEOF_RTDB` constant after rtdb.h struct change
- [ ] Update `src/comm_manager/python/tests/test_pcs_device.py` with write_setpoint/process_command tests

---

## Sources

### Primary (HIGH confidence)
- `src/common/c/include/ems_types.h` — ems_control_state_t, ems_pcs_state_t, ems_source_priority_t enum values
- `src/common/c/include/rtdb.h` — ems_system_t struct layout (current); ems_rtdb_t top-level
- `src/common/python/src/ems_common/rtdb.py` — ctypes mirror, attach_rtdb, seqlock read pattern
- `src/common/python/src/ems_common/ipc.py` — all ZMQ socket paths, topic strings, encode/decode helpers
- `src/data_manager/python/src/ems_data_manager/publisher.py` — seqlock read pattern, 1Hz asyncio loop, encode_telemetry usage
- `src/comm_manager/python/src/ems_comm_manager/pcs_device.py` — seqlock write pattern, existing PCS RTDB write structure
- `src/comm_manager/python/src/ems_comm_manager/__main__.py` — signal handling, ZMQ context/socket lifecycle, module entry point pattern
- `tools/simulators/modbus_sim/state_machine.py` — PCSState enum (STANDBY=0, STARTING=1, RUNNING=2, STOPPING=3, FAULT=4); matches pcs_register_map for 0x500E/0x0291/0x5064
- `config/control_config.yaml` — state_machine.fault_retry_count (0-10, default 3), loop_interval_ms (default 1000)
- `config/schemas/control_config.schema.json` — schema structure, x-mutable annotations
- `pyproject.toml` (root) — workspace members, dev deps, pytest configuration
- `.planning/phases/14-control-state-machine/14-CONTEXT.md` — all locked decisions

### Secondary (MEDIUM confidence)
- `src/data_manager/python/src/ems_data_manager/__main__.py` — asyncio gather + stop_event + loop.add_signal_handler pattern
- `src/comm_manager/python/tests/test_orchestrator.py` — pytest-asyncio test patterns, AsyncMock usage for device tests

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified against root pyproject.toml and existing module deps
- Architecture: HIGH — all patterns verified against 3+ existing M1 modules
- RTDB struct change: HIGH — field layout derived from C standards; padding manually verified for new field types
- PCS command path: HIGH — derived directly from CONTEXT.md locked decisions and existing PcsDevice code
- Pitfalls: HIGH — all based on verified code inspection, not speculation
- ZMQ PUB ownership (open question #2): MEDIUM — architectural ambiguity; resolution path is clear

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable stack — no external library changes expected)
