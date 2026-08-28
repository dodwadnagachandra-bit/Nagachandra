# Technology Stack -- M2 Control & Alarms

**Project:** EMS v3.0 (M2 Milestone)
**Researched:** 2026-03-14
**Scope:** control_manager and alarm_manager -- NEW dependencies only

## What Already Exists (DO NOT ADD)

M1 delivered everything these modules need. Listed to prevent duplication.

| Category | Already In Place |
|----------|-----------------|
| Python common | `ems_common` package (rtdb.py, ipc.py) with RTDB attach, seqlock reader, ZMQ helpers, MessagePack encode/decode |
| RTDB struct | `EmsRtdb` ctypes mirror with `EmsPcs`, `EmsGpio`, `EmsCluster`, `EmsRack`, `EmsSystem` sections |
| IPC sockets | `SOCK_TELEMETRY` (PUB/SUB), `SOCK_CONTROL_CMD` (REQ/REP), `SOCK_ALARM_CMD` (REQ/REP), `SOCK_LOGGER` (PUSH/PULL) |
| Config schemas | `control_config.schema.json`, `alarms_config.schema.json` with full validation |
| Config YAML | `control_config.yaml` (SOC limits, power limits, source priority, state machine), `alarms_config.yaml` (9 alarm rules with thresholds) |
| Config profiles | residential, commercial, container profiles for both configs |
| Config hot-reload | config_manager watches control_config.yaml and alarms_config.yaml via inotify with 500ms debounce |
| ZMQ libraries | pyzmq >= 26.0, msgpack >= 1.0 in workspace |
| YAML/Schema | pyyaml >= 6.0, jsonschema >= 4.23 in workspace |
| Simulators | CAN simulator (BMS telemetry), Modbus PCS simulator (register map), GPIO harness |
| Logging | Logger with PUSH/PULL event ingestion, Parquet telemetry, JSONL events |
| comm_manager | CAN decode writing to RTDB BMS sections, Modbus PCS polling writing to RTDB PCS section |

## Recommended Stack Additions

### Python Libraries (per-module pyproject.toml)

| Library | Version | Purpose | Module | Why |
|---------|---------|---------|--------|-----|
| (none needed) | -- | -- | -- | Both modules use only ems_common + stdlib |

**No new dependencies are required.** Both control_manager and alarm_manager are pure Python modules that consume RTDB data (via ems_common.rtdb), communicate via ZMQ (via ems_common.ipc), and use standard library features (asyncio, enum, dataclasses, time, logging). This is a deliberate architectural choice -- L4 Application modules should not introduce new system dependencies.

### Optional: numpy for Derating Curves

| Library | Version | Purpose | Module | Why NOT to add |
|---------|---------|---------|--------|----------------|
| `numpy` | >= 1.26 | Piecewise-linear interpolation for SOC/temperature derating | control_manager | `numpy.interp()` is convenient but stdlib `bisect` + linear math achieves the same result for 5-10 point curves. Adding numpy pulls in ~30 MB for a single interpolation function. Defer unless derating curves exceed 20 breakpoints. |

**Decision:** Do NOT add numpy. Implement derating with stdlib `bisect.bisect_right()` and linear interpolation between breakpoints. The derating curves in control_config.yaml will have 5-10 points maximum.

## Per-Module Dependency Map

### control_manager (Python only, L4)

**`src/control_manager/python/pyproject.toml` dependencies:**
```toml
dependencies = [
    "ems-common",          # RTDB attach, ZMQ helpers, IPC contracts
]
```

**Key Python stdlib modules used:**
- `asyncio` -- 1Hz control loop with `loop.call_later()` for precise timing
- `enum` -- State machine states (IDLE, STANDBY, CHARGING, DISCHARGING, FAULT)
- `dataclasses` -- Immutable snapshots of RTDB readings per control cycle
- `bisect` -- Piecewise-linear derating curve interpolation
- `time` -- `time.monotonic()` for loop timing, `time.time()` for event timestamps
- `logging` -- Structured logging to stdout (systemd journal captures)
- `signal` -- Graceful shutdown on SIGTERM

**Key ems_common APIs used:**
- `ems_common.rtdb.attach_rtdb()` -- Attach to RTDB shared memory
- `ems_common.rtdb.EmsRtdb`, `EmsPcs`, `EmsRack`, `EmsSystem` -- Read RTDB sections
- `ems_common.ipc.SOCK_TELEMETRY` -- Subscribe to telemetry for change notifications
- `ems_common.ipc.SOCK_CONTROL_CMD` -- REQ/REP for mode changes, source priority overrides
- `ems_common.ipc.SOCK_LOGGER` -- PUSH events (state changes, dispatch decisions)
- `ems_common.ipc.encode_event()`, `encode_command_response()` -- Message encoding

**RTDB sections read:**
- `rtdb.clusters[C].racks[R]` -- SOC, pack voltage/current, cell min/max V/T, online status
- `rtdb.pcs` -- Active power, DC voltage/current, state, fault code, temperature
- `rtdb.gpio` -- DI/DO state (safety status: E-Stop, Fire, ACDB feedback)
- `rtdb.meter` -- Grid power, frequency, power factor (for grid-tie dispatch)
- `rtdb.btms` -- Inlet/outlet temp, cooling status (for temperature derating)

**RTDB sections written:**
- `rtdb.system` -- control_state, source_priority, active_setpoint_kw, total_soc, total_power_kw

**PCS command path:**
The control_manager does NOT write Modbus registers directly. It writes the desired setpoint to `rtdb.system.active_setpoint_kw`. The comm_manager's Modbus PCS poller reads this and writes register 0x500E. This separation preserves the single-writer-per-section rule and keeps comm_manager as the sole Modbus authority.

### alarm_manager (Python only, L4)

**`src/alarm_manager/pyproject.toml` dependencies:**
```toml
dependencies = [
    "ems-common",          # RTDB attach, ZMQ helpers, IPC contracts
]
```

**Key Python stdlib modules used:**
- `asyncio` -- 1Hz alarm evaluation loop
- `enum` -- Alarm states (NORMAL, ACTIVE_UNACKED, ACTIVE_ACKED, CLEARED_UNACKED, RTN)
- `dataclasses` -- Per-alarm rule state (threshold, hysteresis, delay timer, current state)
- `time` -- Monotonic timestamps for delay timers
- `logging` -- Structured logging

**Key ems_common APIs used:**
- `ems_common.rtdb.attach_rtdb()` -- Attach to RTDB shared memory
- `ems_common.ipc.SOCK_TELEMETRY` -- Subscribe to know when fresh data arrives
- `ems_common.ipc.SOCK_ALARM_CMD` -- REQ/REP for alarm acknowledgement, shelving
- `ems_common.ipc.SOCK_LOGGER` -- PUSH alarm events (activate, acknowledge, clear, RTN)
- `ems_common.ipc.encode_event()` -- Event encoding

**RTDB sections read:**
- `rtdb.clusters[C].racks[R]` -- Cell voltages, temperatures, SOC for alarm evaluation
- `rtdb.pcs` -- PCS temperature, fault code, DC bus voltage
- `rtdb.meter` -- Grid frequency, voltage for grid alarm rules

**RTDB sections written:**
- None directly. Alarm state is maintained in-process (Python dict/dataclass) and published via ZMQ. The RTDB `system` section's alarm fields (if needed) would be written by control_manager based on alarm events received via ZMQ SUB.

## PCS Command Flow (Architecture)

The PCS command path is a critical design decision:

```
control_manager                    comm_manager (Modbus)
      |                                  |
      |  1. Calculate setpoint           |
      |  2. Write rtdb.system.           |
      |     active_setpoint_kw --------->|  3. Read rtdb.system.active_setpoint_kw
      |                                  |  4. Write Modbus 0x500E (power)
      |                                  |
      |  5. Write rtdb.system.           |
      |     pcs_command (ON/OFF) ------->|  6. Read pcs_command
      |                                  |  7. Write Modbus 0x0291 (on/off)
```

This requires extending the `EmsSystem` RTDB struct with new fields:

```python
# New fields needed in EmsSystem (rtdb.py)
("active_setpoint_kw", ctypes.c_float),      # Desired power setpoint [kW]
("pcs_command", ctypes.c_int),                # PCS_CMD_NONE/ON/OFF/FAULT_RESET
("pcs_command_ts", ctypes.c_uint64),          # Timestamp of last command
```

**Alternative considered:** ZMQ REQ/REP from control_manager to comm_manager for PCS commands. Rejected because:
- Adds IPC latency to the command path
- Creates a dependency (control_manager blocks waiting for comm_manager reply)
- RTDB path is consistent with existing write patterns
- comm_manager already reads RTDB every poll cycle

## Integration Points with M1

### Config Hot-Reload Consumer Pattern

Both modules need to react when config_manager publishes a `config_reload` event:

```python
# Subscribe to config_reload on telemetry PUB socket
sub_sock.subscribe(b"config_reload")

# In the main loop, check for config changes
async def check_config_reload(sub_sock):
    try:
        topic, body = await asyncio.wait_for(
            sub_sock.recv_multipart(), timeout=0.001
        )
        new_config = yaml.safe_load(open(config_path))
        # Validate and atomic-swap
    except asyncio.TimeoutError:
        pass  # No config change
```

### Existing ZMQ Socket Usage

| Socket | Pattern | control_manager | alarm_manager |
|--------|---------|-----------------|---------------|
| SOCK_TELEMETRY | PUB/SUB | SUB (bms, pcs, gpio topics) | SUB (bms, pcs, meter topics) |
| SOCK_CONTROL_CMD | REQ/REP | REP (binds, accepts commands) | -- |
| SOCK_ALARM_CMD | REQ/REP | -- | REP (binds, accepts ACK/shelve) |
| SOCK_LOGGER | PUSH/PULL | PUSH (state change events) | PUSH (alarm events) |

## Version Pinning

No new version pins needed. Both modules depend only on `ems-common` which pins its own dependencies.

## Confidence Assessment

| Item | Confidence | Basis |
|------|-----------|-------|
| No new dependencies needed | HIGH | Both modules are pure application logic over existing infrastructure |
| asyncio 1Hz loop timing | HIGH | Validated pattern: monotonic clock + sleep compensation. 130us/cycle = 7700x headroom |
| ctypes RTDB read | HIGH | Proven in M1 (data_manager, logger both use this) |
| ZMQ PUB/SUB + REQ/REP | HIGH | All patterns validated in M1 integration tests |
| RTDB command path for PCS | MEDIUM | New pattern not yet proven. Requires EmsSystem struct extension and comm_manager changes |

## Sources

- Codebase: `src/common/python/src/ems_common/rtdb.py` -- existing RTDB access API
- Codebase: `src/common/python/src/ems_common/ipc.py` -- existing IPC contracts with all socket paths
- Codebase: `config/control_config.yaml` -- existing control config structure
- Codebase: `config/alarms_config.yaml` -- existing alarm rules structure
- Codebase: `config/schemas/control_config.schema.json` -- validated schema
- Codebase: `config/schemas/alarms_config.schema.json` -- validated schema
- [pyzmq asyncio docs](https://pyzmq.readthedocs.io/en/latest/api/zmq.asyncio.html)
- [Python asyncio event loop docs](https://docs.python.org/3/library/asyncio-eventloop.html)
- [Feasibility Study for Python-Based Embedded Real-Time Control](https://www.mdpi.com/2079-9292/12/6/1426)
