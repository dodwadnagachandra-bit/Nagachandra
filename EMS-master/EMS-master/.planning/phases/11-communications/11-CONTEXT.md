# Phase 11: Communications - Context

**Gathered:** 2026-03-14
**Status:** Ready for planning

<domain>
## Phase Boundary

CAN DBC decode, Modbus RTU polling, and device health monitoring. EMS receives live telemetry from BMS (CAN) and PCS/BTMS/Meter/DG/PV (Modbus) and writes decoded data to RTDB for all consumers. Covers COMM-01 through COMM-13. Hybrid C+Python architecture: separate C process for CAN decode (crash isolation), Python for Modbus polling and lifecycle orchestration.

</domain>

<decisions>
## Implementation Decisions

### Device Polling Priority and Bus Contention

| Aspect | Decision |
|--------|----------|
| Bus scheduling | One async task per physical RS485 port, round-robin by priority within port |
| Priority order | PCS → Meter → BTMS → DG → PV (within same port) |
| Timeout handling | Per-device timeout from config YAML; skip on timeout, free bus immediately for next device |
| Cross-port parallelism | Independent async tasks per port, poll concurrently (electrically independent buses) |
| Poll rates | Per-device from config YAML (PCS 500ms, Meter 1000ms, BTMS/DG 2000ms, PV 1000ms) |
| Contention avoidance | async/await serializes within a port naturally — no mutex needed |

Key rules:
- RS485 is half-duplex — one transaction at a time per bus, async event loop handles serialization
- Per-port polling loop checks each device's last-poll timestamp and only polls if its interval has elapsed
- When a device times out, skip immediately and move to next device — never let a slow device block faster ones
- Exponential backoff applies to the timed-out device only, other devices keep polling normally
- Devices on different physical ports poll in parallel (separate asyncio tasks)

### Startup Discovery and Degraded Operation

| Device Type | Unreachable at Startup | Goes Offline Later | Recovery |
|-------------|----------------------|-------------------|----------|
| PCS | Start, mark offline, log ERROR, keep polling | Exponential backoff, comm_fault event | Auto-recover on response, reset backoff |
| BMS (CAN) | Start, mark racks offline, log ERROR | Heartbeat timeout (900ms), mark offline | Auto-recover on frame received |
| BTMS | Start, mark offline, log WARNING | Exponential backoff | Auto-recover on response |
| Meter | Start, mark offline, log WARNING | Exponential backoff | Auto-recover on response |
| DG (optional) | Start, mark offline, log WARNING | Exponential backoff | Auto-recover on response |
| PV (optional) | Start, mark offline, log WARNING | Exponential backoff | Auto-recover on response |

Key rules:
- Never block startup for any device — always start, mark unreachable as offline in RTDB
- Mandatory devices (PCS, BMS) log ERROR; optional devices (DG, PV, BTMS, Meter) log WARNING — behavior is identical
- comm_manager's job is to poll and report, not make control decisions — control_manager (M2) decides whether to dispatch power
- Discovery: sequential per port (one probe per device with configured timeout), parallel across ports
- Post-startup offline: exponential backoff (1s → 2s → 4s → 8s → 30s cap), never remove device from polling loop
- Events: `comm_fault` on online→offline, `comm_recovery` on offline→online

### CAN-to-Python Boundary

| Aspect | Decision |
|--------|----------|
| Health channel | RTDB only — no separate C-to-Python pipe |
| Per-rack health | Existing fields: `online` flag + `last_update_ms` in `ems_rack_t` |
| Per-bus health | New small struct in `ems_rtdb_t`: `bus_state`, `tx_error_count`, `rx_error_count`, `last_error_frame_ms` per CAN interface |
| Process lifecycle | Independent systemd services: `comm_manager_c` (CAN) + `comm_manager` (Python/Modbus) |
| Stale detection | Python reads `last_update_ms` — if stale, publishes warning (DATA-06 from Phase 9) |
| Event logging | C pushes directly to ZMQ logger socket — no Python relay |
| C crash detection | RTDB `last_update_ms` goes stale → data_manager health monitoring catches it |

Key rules:
- RTDB is the single source of truth — no direct C-to-Python IPC pipe, no separate health channel
- C process (`comm_manager_c`) and Python orchestrator (`comm_manager`) are independent systemd services with `Restart=always`
- Python does NOT spawn or manage the C process — systemd handles both lifecycles independently
- If C dies, RTDB `last_update_ms` goes stale and data_manager's health monitoring (DATA-06) catches it
- C pushes events directly to ZMQ logger socket using mpack + length-prefixed framing — no Python relay needed
- Per-bus health struct is a small addition (~32 bytes per CAN interface) to ems_rtdb_t for bus-off/error frame tracking (COMM-03)

### Comm Fault Event Granularity

| Event Type | Trigger | Frequency | Severity |
|------------|---------|-----------|----------|
| `comm_fault` | Device online→offline (timeout, CRC, heartbeat) | State transition only | error |
| `comm_recovery` | Device offline→online (first successful response/frame) | State transition only | info |
| `comm_exception` | Modbus exception code received (01-03) | Every occurrence | warning |
| `comm_exception` | Modbus exception code 04 (Device Failure) | Every occurrence + triggers offline | error |
| `can_bus_error` | CAN error frame received (TEC/REC threshold, bus-off) | State transition only | error/critical |
| Individual timeouts | Poll with no response | No event — silent, visible via RTDB staleness | — |
| CRC failures | Bad Modbus CRC | No event — counts toward offline transition | — |

Key rules:
- State transitions only for comm_fault/comm_recovery — no per-poll noise (a flaky device could generate 7200 events/hour otherwise)
- Individual Modbus timeouts and CAN missed frames are silent — visible via RTDB `last_update_ms` staleness for poll-level detail
- Modbus exception codes (01-03) fire immediately because they're usually setup/config bugs that need visibility
- Modbus exception 04 (Device Failure) fires both an exception event AND triggers the offline flow
- CRC failures count toward offline threshold, no separate event
- Recovery events always include `offline_duration_ms` for diagnostics
- comm_fault event payload: `{device_id, device_address, port, fault_type, last_seen_ms, consecutive_failures}`
- Fault types: `timeout`, `crc_error`, `exception_code`, `heartbeat_timeout`, `bus_off`

### Claude's Discretion

- CAN C process internal architecture (thread model, SocketCAN read loop, DBC parsing strategy)
- Modbus Python orchestrator internal structure (class hierarchy, task management)
- Exponential backoff exact parameters (within 1s min → 30s cap constraint)
- Per-bus health struct exact layout (within the defined fields)
- Startup discovery overall timeout cap value
- pymodbus client configuration (serial port settings, framer selection)
- Register map YAML parsing and value scaling implementation
- CAN DBC parsing approach (manual from existing DBC file vs library)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/comm_manager/c/src/main.c` — Stub C executable, CMakeLists.txt links `ems_common_c`
- `src/comm_manager/python/src/ems_comm_manager/__init__.py` — Stub Python package (v0.1.0)
- `config/bms_config.yaml` — CAN interface, bitrate, DBC path, base CAN ID, timing, fault injection
- `config/bms_layer2.dbc` — 10 message types per rack (PackSummary, CellVoltage_01-07, CellTemperature, RackStatus)
- `config/pcs_config.yaml` — Modbus RTU connection, register map path, control registers, timing
- `config/pcs_register_map.yaml` — 46+ holding registers with address/name/scale/unit/access/signed/default
- `config/btms_config.yaml`, `config/meter_config.yaml`, `config/dg_config.yaml`, `config/pv_config.yaml` — Per-device Modbus configs
- `config/schemas/` — JSON Schema for all device configs (bms, pcs, btms, meter, dg, pv)
- `config/profiles/*/` — Per-deployment profile overrides for all device configs
- `tools/simulators/can_sim/` — Full CAN simulator (dual-rate cycling, fault injection, signal generation, per-rack tasks)
- `tools/simulators/modbus_sim/` — Full Modbus simulator (PCS state machine, register map loader, control register interception, signal generation)
- `src/common/c/include/rtdb.h` — ems_rack_t (modules, pack aggregates, online, fault_code), ems_pcs_t, ems_meter_t, ems_btms_t
- `src/common/c/include/ipc_defs.h` — Topics: `bms.rack`, `pcs`, `meter`, `btms`, `comm_fault`; socket paths; envelope keys
- `src/common/python/src/ems_common/ipc.py` — Python IPC mirror with encode/decode helpers
- `src/common/c/include/seqlock.h` — Lock-free seqlock for RTDB writes
- `deploy/systemd/comm_manager.service` — Service file, depends on safety_manager.service

### Established Patterns
- CAN ID computation: `base_id + cluster * 0x1000 + rack * 0x10 + msg_offset` (offsets 0x00-0x09)
- Modbus zero-mode addressing: `_ZeroModeDeviceContext` subclass (register N maps to key N)
- mpack v1.1.1 vendored amalgamation for C-side MessagePack
- Length-prefixed framing (4-byte BE uint32) for C ZMQ interop
- MessagePack envelope: `{ts, seq, src, topic, payload}` for telemetry
- Event envelope: `{ts, src, severity, event_type, message, data}`
- All DBC signals: little-endian (Intel byte order)
- PCS control via FC06 write single register: 0x0291 (on/off), 0x0292 (fault_reset), 0x500E (power_setpoint)
- Signed values via two's complement in uint16 Modbus registers

### Integration Points
- Phase 9 (Foundation) must be complete: config_manager serves device configs, data_manager owns RTDB lifecycle
- Phase 10 (Safety) must be complete: safety_manager operational before PCS communication starts
- RTDB struct needs small addition: per-CAN-interface health struct (bus_state, error counts)
- Systemd ordering: `After=ems-safety-manager.service` for comm_manager
- Separate service file needed for `comm_manager_c` (CAN C process)
- ZMQ telemetry PUB on `ipc:///run/ems/telemetry.sock` (data_manager binds, comm publishes? — Phase 9 decides publisher model)
- ZMQ logger PUSH to `ipc:///run/ems/logger.sock` for both C and Python event publishing
- GPIO harness available for integration testing (safety outputs during comm faults)

</code_context>

<specifics>
## Specific Ideas

- CAN simulator and Modbus simulator from M0 are the primary test fixtures — no hardware needed for development
- PCS register map is synthetic V1.24 — will be replaced when real vendor document arrives (tech debt PLAT-01)
- Vendor DBC file pending (Decision #4.2) — CAN decode uses synthetic DBC for now
- pymodbus async serial timeout issue (#1654) needs hands-on validation during implementation
- BTMS, Meter, DG, PV do not have register map YAMLs yet — only PCS has `pcs_register_map.yaml`; other device register maps need to be created during this phase or stubbed

</specifics>

<deferred>
## Deferred Ideas

- **COMM-14**: CAN frame loss rate tracking (expected vs received frames per rack per cycle) — future requirement
- **COMM-15**: Modbus QoS metrics (per-device response time histogram, timeout rate, CRC error rate) — future requirement
- **COMM-16**: Auto-detection of CAN bitrate for field commissioning — future requirement
- **COMM-17**: Graceful device hot-swap (rack added/removed without restart) — future requirement
- **COMM-18**: CAN FD support (pending BMS vendor confirmation) — future requirement
- Register map YAMLs for BTMS/Meter/DG/PV devices — may need stub maps if real vendor docs unavailable

</deferred>

---

*Phase: 11-communications*
*Context gathered: 2026-03-14*
