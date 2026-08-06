# Phase 11: Communications - Research

**Researched:** 2026-03-14
**Domain:** CAN DBC decode (C/SocketCAN), Modbus RTU polling (Python/pymodbus), device lifecycle management, RTDB integration
**Confidence:** HIGH

## Summary

Phase 11 implements the L2 Communications layer: a C process for CAN DBC decode (BMS telemetry) and a Python process for Modbus RTU polling (PCS, BTMS, Meter, DG, PV). Both write decoded data to the RTDB shared memory using seqlock, and publish comm fault/recovery events via ZMQ.

The project already has substantial infrastructure: RTDB structs with seqlocks, ZMQ IPC contracts, working CAN and Modbus simulators, device config YAMLs with schemas, a synthetic DBC file, and a synthetic PCS register map. The C process follows the safety_manager pattern (SocketCAN + mpack + ZMQ, RTDB attach via rtdb_lifecycle.h). The Python process uses pymodbus AsyncModbusSerialClient with per-port async tasks.

**Primary recommendation:** Build the C CAN decode process first (crash-isolated, single-purpose), then the Python Modbus orchestrator with per-port polling loops and shared device health state machine, then integration tests against existing simulators.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Bus scheduling: One async task per physical RS485 port, round-robin by priority within port
- Priority order: PCS > Meter > BTMS > DG > PV (within same port)
- Timeout handling: Per-device timeout from config YAML; skip on timeout, free bus immediately
- Cross-port parallelism: Independent async tasks per port, poll concurrently
- Poll rates: Per-device from config YAML (PCS 500ms, Meter 1000ms, BTMS/DG 2000ms, PV 1000ms)
- Contention avoidance: async/await serializes within a port naturally -- no mutex needed
- Never block startup for any device -- always start, mark unreachable as offline in RTDB
- Mandatory devices (PCS, BMS) log ERROR; optional devices log WARNING -- behavior identical
- comm_manager's job is to poll and report, not make control decisions
- Discovery: sequential per port (one probe per device with configured timeout), parallel across ports
- Exponential backoff: 1s -> 2s -> 4s -> 8s -> 30s cap, never remove device from polling loop
- Events: comm_fault on online->offline, comm_recovery on offline->online (state transitions only)
- CAN-to-Python boundary: RTDB only -- no separate C-to-Python pipe
- Per-rack health: online flag + last_update_ms in ems_rack_t (existing)
- Per-bus health: New small struct in ems_rtdb_t: bus_state, tx_error_count, rx_error_count, last_error_frame_ms per CAN interface
- Process lifecycle: Independent systemd services: comm_manager_c (CAN) + comm_manager (Python/Modbus)
- C pushes events directly to ZMQ logger socket using mpack + length-prefixed framing
- State transitions only for comm_fault/comm_recovery -- no per-poll noise
- Modbus exception codes 01-03 fire immediately as comm_exception events
- Modbus exception 04 fires both exception event AND triggers offline flow
- CRC failures count toward offline threshold, no separate event
- Recovery events include offline_duration_ms
- comm_fault payload: {device_id, device_address, port, fault_type, last_seen_ms, consecutive_failures}

### Claude's Discretion
- CAN C process internal architecture (thread model, SocketCAN read loop, DBC parsing strategy)
- Modbus Python orchestrator internal structure (class hierarchy, task management)
- Exponential backoff exact parameters (within 1s min -> 30s cap)
- Per-bus health struct exact layout (within defined fields)
- Startup discovery overall timeout cap value
- pymodbus client configuration (serial port settings, framer selection)
- Register map YAML parsing and value scaling implementation
- CAN DBC parsing approach (manual from existing DBC file vs library)

### Deferred Ideas (OUT OF SCOPE)
- COMM-14: CAN frame loss rate tracking
- COMM-15: Modbus QoS metrics
- COMM-16: Auto-detection of CAN bitrate
- COMM-17: Graceful device hot-swap
- COMM-18: CAN FD support
- Register map YAMLs for BTMS/Meter/DG/PV -- may need stub maps if real vendor docs unavailable
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| COMM-01 | CAN DBC decode via SocketCAN C thread reads all 12 L2 message types and writes to RTDB BMS sections with seqlock | SocketCAN C API pattern, DBC signal layout from bms_layer2.dbc, seqlock usage from seqlock.h, RTDB rack struct from rtdb.h |
| COMM-02 | CAN heartbeat timeout detects per-rack BMU silence (900ms default), sets rack offline, publishes comm_fault | CLOCK_MONOTONIC timing, last_update_ms field in ems_rack_t, ZMQ event pattern from safety_event.c |
| COMM-03 | CAN bus-off and error-frame handling reads SocketCAN error frames, logs TEC/REC counts, escalates bus-off | CAN_ERR_FLAG, CAN_RAW_ERR_FILTER socket option, error frame data byte layout (data[6]=TEC, data[7]=REC), CAN_ERR_BUSOFF |
| COMM-04 | Multi-cluster CAN support runs one C thread per CAN interface for parallel bus operation | pthread per CAN interface, each thread has own socket, independent read loops |
| COMM-05 | Modbus RTU PCS polling reads telemetry registers and writes setpoints at ~500ms cycle via pymodbus async | AsyncModbusSerialClient API, register map YAML parsing, seqlock write via ctypes |
| COMM-06 | Modbus timeout and reconnect with exponential backoff | pymodbus reconnect_delay/reconnect_delay_max params, custom backoff logic on top |
| COMM-07 | Modbus CRC and exception code handling maps to structured events | pymodbus isError()/exception_code, ModbusIOException for CRC/timeout |
| COMM-08 | GPIO monitoring via libgpiod -- ALREADY HANDLED by safety_manager (Phase 10) | COMM-08 reads DI signals and writes to RTDB gpio section -- safety_manager already does this per SAFE-08. Phase 11 may only need to verify this is operational. |
| COMM-09 | All comm data writes use seqlock, publish on ZMQ telemetry with MessagePack envelope | seqlock API from seqlock.h, IPC envelope from ipc_defs.h/ipc.py, mpack for C / msgpack for Python |
| COMM-10 | Comm fault events pushed to logger via ZMQ PUSH | safety_event.c pattern for C, ipc.py encode_event for Python |
| COMM-11 | Startup device discovery polls each configured device, marks reachable/unreachable in RTDB | Sequential per-port probe with configured timeout, parallel across ports |
| COMM-12 | Modbus RTU polling for BTMS, Meter, DG, PV uses same pattern as PCS with device-specific register map YAML | Generic Modbus device class with register map loader, stub register maps needed for BTMS/Meter/DG/PV |
| COMM-13 | Hybrid C+Python architecture with separate C process for CAN | Independent systemd services, RTDB as communication channel, no direct IPC |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SocketCAN (kernel) | Linux 6.x | CAN bus access from C | Kernel-native CAN interface, zero-copy frame delivery, error frame support |
| pymodbus | >=3.7 (pinned in dev deps) | Async Modbus RTU client | Industry standard Python Modbus library, AsyncModbusSerialClient with reconnect |
| cantools | >=39.0 (dev dep) | DBC file parsing (development/test only) | NOT used in C production code -- C uses manual DBC decode for zero-dependency |
| python-can | >=4.0 (dev dep) | CAN bus access from Python (test/sim only) | Used by CAN simulator, NOT by production comm_manager |
| mpack | 1.1.1 (vendored) | MessagePack encoding in C | Already vendored, used by safety_manager pattern |
| msgpack | >=1.0 | MessagePack in Python | Already in ems_common, used by ipc.py |
| pyzmq | (via ems-common) | ZMQ bindings for Python | Already in stack, event publishing |
| libzmq | system pkg | ZMQ for C process | Already used by safety_manager |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| PyYAML | >=6.0 | Parse device config and register map YAMLs | Python comm_manager startup, config loading |
| ctypes (stdlib) | Python 3.12 | RTDB shared memory access from Python | Already established via ems_common/rtdb.py |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual C DBC decode | cantools (Python) | C process needs zero Python dependency; manual decode is ~200 lines for 10 known message types with fixed signal layouts |
| pymodbus | minimalmodbus | pymodbus has async support and is already in the stack; minimalmodbus is sync-only |
| Per-device pymodbus client | Single shared client | pymodbus AsyncModbusSerialClient is NOT thread safe and serializes internally -- one client per port is correct |

**Installation:**
```bash
# Python deps -- already in workspace via ems-common dependency
uv sync --all-packages

# C deps -- already available via system packages
# libzmq-dev (already installed for safety_manager)
# No additional C deps needed (SocketCAN is kernel-native)
```

## Architecture Patterns

### Recommended Project Structure
```
src/comm_manager/
  c/
    CMakeLists.txt
    src/
      main.c              # Entry point, signal handlers, RTDB attach, thread spawn
      can_reader.h/.c     # SocketCAN read loop, error frame handling
      can_decode.h/.c     # DBC signal decode (manual, no library dependency)
      can_health.h/.c     # Heartbeat timeout detection, per-bus health tracking
      comm_event.h/.c     # ZMQ event publishing (reuse safety_event pattern)
    tests/
      test_can_decode.c   # Pure decode logic tests (no hardware)
  python/
    src/ems_comm_manager/
      __init__.py
      __main__.py         # Entry point, asyncio.run
      orchestrator.py     # Top-level lifecycle: discovery, polling loops, shutdown
      modbus_device.py    # Generic Modbus device with health state machine
      register_map.py     # YAML register map loader + value scaling
      pcs_device.py       # PCS-specific register mapping to RTDB fields
      generic_device.py   # BTMS/Meter/DG/PV mapping to RTDB fields
      health.py           # Device health state machine (online/offline/backoff)
      events.py           # ZMQ event publishing (comm_fault, comm_recovery, comm_exception)
    tests/
      test_register_map.py
      test_health.py
      test_modbus_device.py
      test_orchestrator.py
```

### Pattern 1: CAN C Process Architecture
**What:** Single-threaded or multi-threaded C process reading SocketCAN frames, decoding via manual DBC logic, writing to RTDB.
**When to use:** CAN decode for BMS telemetry (COMM-01, COMM-02, COMM-03, COMM-04).
**Recommendation:** One pthread per CAN interface (supports multi-cluster). Each thread:
1. Opens CAN_RAW socket with CAN_ERR_FLAG filter enabled
2. Blocking read() in loop (SocketCAN handles efficient blocking)
3. Extract cluster/rack index from CAN ID: `(can_id - base_id) >> 4` for rack, `(can_id - base_id) >> 12` for cluster
4. Decode message based on offset: `(can_id - base_id) & 0x0F`
5. Write decoded values to RTDB rack struct via seqlock
6. Update last_update_ms with CLOCK_MONOTONIC
7. Separate timer thread checks last_update_ms for heartbeat timeout (900ms)

**Example (SocketCAN setup with error frames):**
```c
// Source: Linux kernel SocketCAN documentation
#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/can/error.h>

int can_socket_init(const char *interface)
{
    int s = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    struct ifreq ifr;
    struct sockaddr_can addr;

    strncpy(ifr.ifr_name, interface, IFNAMSIZ - 1);
    ioctl(s, SIOCGIFINDEX, &ifr);

    addr.can_family = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;
    bind(s, (struct sockaddr *)&addr, sizeof(addr));

    /* Enable error frame reception (COMM-03) */
    can_err_mask_t err_mask = CAN_ERR_MASK;  /* all error types */
    setsockopt(s, SOL_CAN_RAW, CAN_RAW_ERR_FILTER,
               &err_mask, sizeof(err_mask));

    return s;
}
```

**Example (CAN ID decomposition):**
```c
// CAN ID scheme: base_id + cluster * 0x1000 + rack * 0x10 + msg_offset
// base_id comes from bms_config.yaml (0x98FF0003 with 29-bit mask = 0x18FF0003)

#define CAN_29BIT_MASK 0x1FFFFFFFU

static void decode_can_id(uint32_t can_id, uint32_t base_id,
                          int *cluster, int *rack, int *msg_offset)
{
    uint32_t id = can_id & CAN_29BIT_MASK;
    uint32_t delta = id - (base_id & CAN_29BIT_MASK);
    *cluster   = (delta >> 12) & 0x0F;
    *rack      = (delta >> 4) & 0x0F;
    *msg_offset = delta & 0x0F;
}
```

### Pattern 2: Manual DBC Decode (C, no library)
**What:** Decode CAN frame data bytes using known signal bit positions from the DBC file, without a DBC parsing library.
**When to use:** Production C code for COMM-01. The DBC defines 10 message types with fixed signal layouts.
**Why manual:** Zero external dependency for the C process. The DBC has only 10 message types with well-defined signals. Manual decode is ~200 lines and compiles to tight code.

**Example (PackSummary decode):**
```c
// Source: config/bms_layer2.dbc -- PackSummary message
// All signals are little-endian (Intel byte order)

static void decode_pack_summary(const uint8_t data[8], ems_rack_t *rack)
{
    /* pack_v: bits 0-15, unsigned, scale 0.1, offset 0 */
    uint16_t raw_v = (uint16_t)data[0] | ((uint16_t)data[1] << 8);
    rack->pack_v = raw_v * 0.1f;

    /* pack_i: bits 16-31, signed, scale 0.1, offset 0 */
    int16_t raw_i = (int16_t)((uint16_t)data[2] | ((uint16_t)data[3] << 8));
    rack->pack_i = raw_i * 0.1f;

    /* pack_soc: bits 32-39, unsigned, scale 0.5, offset 0 */
    rack->pack_soc = data[4] * 0.5f;

    /* pack_soh: bits 40-47, unsigned, scale 0.5, offset 0 */
    rack->pack_soh = data[5] * 0.5f;

    /* fault_code: bits 48-63, unsigned, scale 1, offset 0 */
    rack->fault_code = (uint32_t)data[6] | ((uint32_t)data[7] << 8);
}
```

### Pattern 3: Error Frame Handling (COMM-03)
**What:** Parse SocketCAN error frames to detect bus-off, error-passive, and TEC/REC values.
**When to use:** CAN bus health monitoring.

**Example:**
```c
// Source: linux/can/error.h
static void handle_error_frame(const struct can_frame *frame,
                               can_bus_health_t *health)
{
    if (frame->can_id & CAN_ERR_BUSOFF)
    {
        health->bus_state = CAN_BUS_OFF;
        /* Publish critical event */
    }

    if (frame->can_id & CAN_ERR_CRTL)
    {
        if (frame->data[1] & (CAN_ERR_CRTL_RX_PASSIVE | CAN_ERR_CRTL_TX_PASSIVE))
        {
            health->bus_state = CAN_BUS_ERROR_PASSIVE;
        }
        else if (frame->data[1] & (CAN_ERR_CRTL_RX_WARNING | CAN_ERR_CRTL_TX_WARNING))
        {
            health->bus_state = CAN_BUS_ERROR_WARNING;
        }
    }

    /* TEC in data[6], REC in data[7] */
    health->tx_error_count = frame->data[6];
    health->rx_error_count = frame->data[7];
    health->last_error_frame_ms = clock_monotonic_ms();
}
```

### Pattern 4: Python Modbus Orchestrator
**What:** Async Python process with one task per RS485 port, round-robin polling within port.
**When to use:** All Modbus devices (COMM-05, COMM-06, COMM-07, COMM-11, COMM-12).

**Example (per-port polling loop):**
```python
# Source: pymodbus AsyncModbusSerialClient + project CONTEXT.md decisions
from pymodbus.client import AsyncModbusSerialClient
from pymodbus.framer import FramerType

async def port_polling_loop(
    port: str,
    devices: list[ModbusDevice],
    rtdb: EmsRtdb,
) -> None:
    """One async task per physical RS485 port."""
    client = AsyncModbusSerialClient(
        port=port,
        framer=FramerType.RTU,
        baudrate=9600,
        parity="N",
        stopbits=1,
        timeout=3,
        reconnect_delay=0,  # We handle reconnect ourselves
    )
    await client.connect()

    while True:
        now_ms = monotonic_ms()
        for device in devices:  # sorted by priority
            if not device.should_poll(now_ms):
                continue
            try:
                result = await asyncio.wait_for(
                    device.poll(client),
                    timeout=device.timeout_s,
                )
                device.on_success(result, rtdb)
            except (asyncio.TimeoutError, ModbusIOException):
                device.on_failure()
        await asyncio.sleep(0.050)  # 50ms minimum loop period
```

### Pattern 5: Device Health State Machine
**What:** Per-device state tracking: ONLINE, OFFLINE, BACKOFF with exponential backoff.
**When to use:** All comm devices for fault detection and recovery.

**Example:**
```python
class DeviceHealth:
    """Tracks device online/offline state with exponential backoff."""

    BACKOFF_MIN_S: float = 1.0
    BACKOFF_MAX_S: float = 30.0

    def __init__(self, device_id: str, offline_threshold: int = 3) -> None:
        self.device_id: str = device_id
        self.online: bool = False
        self.consecutive_failures: int = 0
        self.offline_threshold: int = offline_threshold
        self.backoff_s: float = self.BACKOFF_MIN_S
        self.last_success_ms: int = 0
        self.went_offline_ms: int = 0

    def record_success(self, now_ms: int) -> bool:
        """Returns True if state changed (recovery event needed)."""
        was_offline = not self.online
        self.online = True
        self.consecutive_failures = 0
        self.backoff_s = self.BACKOFF_MIN_S
        self.last_success_ms = now_ms
        return was_offline  # True = publish comm_recovery

    def record_failure(self, now_ms: int) -> bool:
        """Returns True if state changed (fault event needed)."""
        self.consecutive_failures += 1
        if self.online and self.consecutive_failures >= self.offline_threshold:
            self.online = False
            self.went_offline_ms = now_ms
            self.backoff_s = self.BACKOFF_MIN_S
            return True  # publish comm_fault
        if not self.online:
            self.backoff_s = min(self.backoff_s * 2, self.BACKOFF_MAX_S)
        return False
```

### Pattern 6: Register Map YAML Loader
**What:** Generic register map loader that works for any Modbus device type.
**When to use:** PCS and all other Modbus devices (COMM-05, COMM-12).

**Example:**
```python
# Source: existing tools/simulators/modbus_sim/register_map.py pattern
import yaml
from dataclasses import dataclass

@dataclass
class RegisterDef:
    address: int
    name: str
    scale: int
    unit: str
    access: str  # 'r' or 'rw'
    signed: bool
    rtdb_field: str | None = None  # maps to RTDB struct field name

def load_register_map(path: str) -> list[RegisterDef]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [RegisterDef(**{k: reg[k] for k in RegisterDef.__dataclass_fields__ if k in reg})
            for reg in data["registers"]]

def scale_value(raw: int, reg: RegisterDef) -> float:
    if reg.signed and raw > 32767:
        raw -= 65536
    return raw / reg.scale
```

### Anti-Patterns to Avoid
- **Spawning C process from Python:** CONTEXT.md explicitly states systemd manages both lifecycles independently. Python must NOT use subprocess to launch comm_manager_c.
- **Direct C-to-Python IPC:** RTDB is the single communication channel. No ZMQ pipes, no Unix sockets between the two processes.
- **Per-poll events:** Individual Modbus timeouts and CAN missed frames must be silent. Only state transitions (online->offline, offline->online) generate events. A flaky device could generate 7200 events/hour otherwise.
- **Mutex for RS485 bus:** async/await naturally serializes within a port. Adding a mutex causes deadlock risk and is unnecessary.
- **Blocking the entire poll loop on one device timeout:** When a device times out, skip immediately and move to next device.
- **Using pymodbus reconnect_delay for backoff:** pymodbus has built-in reconnect but it reconnects the serial port, not the device. Set reconnect_delay=0 and handle device-level backoff manually.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Modbus RTU framing | Custom serial parser | pymodbus AsyncModbusSerialClient | CRC calculation, timing, exception handling are complex |
| CAN frame send/receive | Raw serial port access | SocketCAN kernel API | Kernel handles bitrate, error recovery, timestamping |
| MessagePack encoding (C) | Custom binary format | mpack 1.1.1 (vendored) | Already in use, tested with safety_manager |
| MessagePack encoding (Python) | Custom binary format | msgpack via ipc.py | Already in use, encode_event/encode_telemetry helpers exist |
| Shared memory access | Custom mmap wrapper | rtdb_lifecycle.h (C) / rtdb.py (Python) | Already tested, handles magic/version validation |
| ZMQ event publishing (C) | Custom socket code | Follow safety_event.c pattern | Proven pattern with length-prefixed framing, non-blocking sends |
| Two's complement decode | Manual bit manipulation | Standard C int16_t cast | Compiler handles it correctly for little-endian signals |

**Key insight:** The project already has working implementations of every IPC pattern needed. The comm_manager should reuse these patterns (safety_event.c for C ZMQ, ipc.py for Python ZMQ, rtdb.py for RTDB access, register_map.py pattern for YAML loading) rather than inventing new ones.

## Common Pitfalls

### Pitfall 1: CAN Extended Frame ID Flag
**What goes wrong:** SocketCAN sets bit 31 (0x80000000) for extended frame IDs. The raw `can_frame.can_id` includes this flag.
**Why it happens:** The DBC uses 29-bit extended CAN IDs (0x18FF0003 range), but SocketCAN ORs `CAN_EFF_FLAG` (0x80000000) into the id.
**How to avoid:** Always mask with `CAN_EFF_MASK` (0x1FFFFFFF) before comparing to base_id.
**Warning signs:** CAN ID comparisons fail; no messages decoded despite frames being received.

### Pitfall 2: pymodbus Client is NOT Thread Safe
**What goes wrong:** Concurrent read_holding_registers calls from multiple asyncio tasks on the same client corrupt request/response pairing.
**Why it happens:** AsyncModbusSerialClient uses a single transport. Concurrent awaits interleave requests on the wire.
**How to avoid:** One client per RS485 port. Within a port, serialize all polls through a single async task.
**Warning signs:** Mismatched slave IDs in responses, CRC errors, garbled data.

### Pitfall 3: Modbus Zero-Mode Addressing
**What goes wrong:** Register address N returns data for address N+1.
**Why it happens:** pymodbus 3.12 removed the zero_mode parameter. Default behavior adds +1 offset.
**How to avoid:** Use the `_ZeroModeDeviceContext` subclass pattern from the existing Modbus simulator. For client reads, be aware that pymodbus addresses are 0-based and the offset is applied server-side.
**Warning signs:** All register values shifted by one position.

### Pitfall 4: Seqlock Writer Must Be Single per Section
**What goes wrong:** Two writers updating the same RTDB section corrupt the seqlock sequence counter, causing readers to spin forever.
**Why it happens:** Seqlock assumes single-writer-per-section.
**How to avoid:** C process is sole writer for BMS rack sections. Python process is sole writer for PCS, Meter, BTMS sections. Document ownership clearly.
**Warning signs:** Reader processes hang, RTDB values stale.

### Pitfall 5: RTDB Struct Size Mismatch Between C and Python
**What goes wrong:** Adding the per-bus health struct to ems_rtdb_t changes sizeof(ems_rtdb_t). If Python ctypes mirror is not updated, all field offsets after the new struct are wrong.
**Why it happens:** ctypes.Structure relies on exact field-by-field match.
**How to avoid:** Add new struct to BOTH rtdb.h AND rtdb.py. Run sizeof assertion test.
**Warning signs:** Garbage values in PCS/Meter/BTMS/System sections after RTDB struct change.

### Pitfall 6: Signed Modbus Register Values
**What goes wrong:** Negative power values (discharge) appear as large positive numbers.
**Why it happens:** Modbus registers are uint16. Signed values use two's complement. pymodbus returns unsigned int.
**How to avoid:** Check `signed` flag in register map YAML. If True and raw > 32767, subtract 65536.
**Warning signs:** PCS shows 65236 kW instead of -300 kW (0xFF2C interpreted unsigned).

### Pitfall 7: CAN Heartbeat False Positive at Startup
**What goes wrong:** All racks immediately trigger heartbeat timeout at startup because last_update_ms is 0.
**Why it happens:** RTDB is zeroed on creation. Heartbeat check fires before any CAN frame arrives.
**How to avoid:** Initialize last_update_ms to current CLOCK_MONOTONIC on startup, OR skip heartbeat check until first frame received per rack (use a "first_frame_seen" flag).
**Warning signs:** All racks briefly flash offline at startup, generating spurious comm_fault events.

### Pitfall 8: SystemD Service File Mismatch
**What goes wrong:** Existing comm_manager.service says "C component launched as subprocess by Python orchestrator" -- this contradicts the CONTEXT.md decision of independent systemd services.
**Why it happens:** Service file was created during M0 scaffolding before architecture decisions were finalized.
**How to avoid:** Update comm_manager.service to remove subprocess comment. Create separate comm_manager_c.service. Both should have Restart=always and After=ems-safety-manager.service.
**Warning signs:** Only one process starts, the other is never launched.

## Code Examples

### RTDB Per-Bus Health Struct Addition (COMM-03)
```c
// Addition to rtdb.h
#define MAX_CAN_INTERFACES 2

typedef enum
{
    CAN_BUS_ACTIVE      = 0,
    CAN_BUS_ERROR_WARNING = 1,
    CAN_BUS_ERROR_PASSIVE = 2,
    CAN_BUS_OFF         = 3,
} ems_can_bus_state_t;

typedef struct
{
    ems_seqlock_t          lock;
    uint64_t               last_update_ms;
    ems_can_bus_state_t    bus_state;
    uint8_t                tx_error_count;
    uint8_t                rx_error_count;
    uint64_t               last_error_frame_ms;
} ems_can_health_t;

// Add to ems_rtdb_t before ems_pcs_t:
//   ems_can_health_t  can_health[MAX_CAN_INTERFACES];
```

### Modbus Register Read + Scale + RTDB Write
```python
# Source: project patterns from register_map.py + rtdb.py
async def poll_pcs(
    client: AsyncModbusSerialClient,
    slave_id: int,
    registers: list[RegisterDef],
    rtdb: EmsRtdb,
) -> None:
    """Read PCS telemetry registers and write to RTDB."""
    # Read contiguous block: 0x0001 to 0x000C (12 registers)
    result = await client.read_holding_registers(
        address=0x0001, count=12, slave=slave_id,
    )
    if result.isError():
        raise ModbusDeviceError(f"PCS read error: {result}")

    # Scale and write to RTDB
    pcs = rtdb.pcs
    seqlock_write_begin(pcs.lock)
    pcs.ac_voltage = scale_value(result.registers[0], registers[0])  # 0x0001
    pcs.ac_current = scale_value(result.registers[3], registers[3])  # 0x0004
    pcs.frequency = scale_value(result.registers[6], registers[6])   # 0x0007
    pcs.active_power = scale_value(result.registers[7], registers[7])  # 0x0008
    # ... etc
    pcs.last_update_ms = monotonic_ms()
    seqlock_write_end(pcs.lock)
```

### Python Seqlock Write via ctypes
```python
# Source: ems_common/rtdb.py EmsSeqlock struct
import ctypes

def seqlock_write_begin(lock: EmsSeqlock) -> None:
    """Begin seqlock write section (increment to odd)."""
    seq = lock.sequence
    lock.sequence = seq + 1
    ctypes.memmove(0, 0, 0)  # compiler barrier -- not needed with ctypes

def seqlock_write_end(lock: EmsSeqlock) -> None:
    """End seqlock write section (increment to even)."""
    seq = lock.sequence
    lock.sequence = seq + 1
```
**Note:** Python ctypes writes to shared memory are not truly atomic. However, since there is a single writer per section (enforced by design), this is safe. The seqlock protects readers from seeing torn writes across multi-field updates.

### ZMQ Comm Fault Event (Python)
```python
# Source: ems_common/ipc.py
from ems_common.ipc import encode_event, SOCK_LOGGER, SEVERITY_ERROR, TOPIC_COMM_FAULT

def publish_comm_fault(
    push_socket: zmq.Socket,
    device_id: str,
    device_address: int,
    port: str,
    fault_type: str,
    last_seen_ms: int,
    consecutive_failures: int,
) -> None:
    event_data = encode_event(
        timestamp_ms=monotonic_ms(),
        source="comm_manager",
        severity=SEVERITY_ERROR,
        event_type=TOPIC_COMM_FAULT,
        message=f"{device_id} offline: {fault_type}",
        data={
            "device_id": device_id,
            "device_address": device_address,
            "port": port,
            "fault_type": fault_type,
            "last_seen_ms": last_seen_ms,
            "consecutive_failures": consecutive_failures,
        },
    )
    push_socket.send(event_data, zmq.NOBLOCK)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pymodbus zero_mode=True | _ZeroModeDeviceContext subclass | pymodbus 3.12 | Must use subclass for correct addressing (already done in modbus_sim) |
| pymodbus sync client | AsyncModbusSerialClient | pymodbus 3.x | Async is required for multi-device polling without blocking |
| pymodbus closes on timeout | Fixed in PR #1672 | pymodbus 3.4+ | Issue #1654 resolved -- client no longer disconnects on timeout |
| Manual CAN socket code | SocketCAN kernel API | Linux 2.6.25+ | Standard, well-documented, supports error frames |

**Deprecated/outdated:**
- pymodbus `zero_mode` parameter: Removed in 3.12. Use `_ZeroModeDeviceContext` subclass instead.
- pymodbus `ModbusSerialClient` (sync): Use `AsyncModbusSerialClient` for non-blocking multi-device polling.

## COMM-08 Note: GPIO Already Implemented

COMM-08 states "GPIO monitoring via libgpiod reads 8 DI signals using edge detection and writes state to RTDB gpio section." This is **already fully implemented** by safety_manager in Phase 10 (SAFE-08). The safety_manager reads all 8 DI signals via libgpiod edge events and writes to rtdb->gpio.di[] on every edge event.

Phase 11 should verify this is operational but does NOT need to re-implement GPIO monitoring. If COMM-08 requires any additional GPIO behavior beyond what safety_manager provides, it should be documented as an integration concern.

## Register Map YAMLs Needed

Only PCS has `config/pcs_register_map.yaml`. BTMS, Meter, DG, and PV need stub register maps. These should follow the same format:

```yaml
# Example stub: config/btms_register_map.yaml
metadata:
  version: "synthetic-1.0"
  device_type: btms

registers:
  - address: 0x0001
    name: inlet_temp
    scale: 10
    unit: degC
    access: r
    signed: true
    default: 250
    rtdb_field: inlet_temp

  - address: 0x0002
    name: outlet_temp
    scale: 10
    unit: degC
    access: r
    signed: true
    default: 280
    rtdb_field: outlet_temp

  - address: 0x0003
    name: fan_speed
    scale: 10
    unit: "%"
    access: r
    signed: false
    default: 0
    rtdb_field: fan_speed_pct

  - address: 0x0004
    name: cooling_active
    scale: 1
    unit: ""
    access: r
    signed: false
    default: 0
    rtdb_field: cooling_active
```

Each device config YAML already has a `connection` section but needs a `register_map_path` field pointing to its register map. These are synthetic/stub maps -- they will be replaced when real vendor documentation arrives.

## Open Questions

1. **COMM-08 ownership: who "owns" GPIO monitoring?**
   - What we know: safety_manager (Phase 10) already reads all 8 DI and writes to RTDB gpio section per SAFE-08.
   - What's unclear: Does COMM-08 expect comm_manager to do this independently, or is it satisfied by safety_manager's implementation?
   - Recommendation: Mark COMM-08 as satisfied by safety_manager. comm_manager verifies gpio section is being updated (last_update_ms is fresh) but does not duplicate GPIO reads.

2. **RTDB struct modification for CAN bus health**
   - What we know: Need to add ems_can_health_t to ems_rtdb_t. This changes sizeof(ems_rtdb_t).
   - What's unclear: Best placement in the struct to minimize impact on existing field offsets.
   - Recommendation: Add can_health[] after the existing `system` field (at end of struct) to avoid changing offsets of existing fields. Update both rtdb.h and rtdb.py atomically.

3. **Register map YAMLs for non-PCS devices**
   - What we know: Only PCS has a register map. BTMS/Meter/DG/PV configs exist but lack register maps.
   - What's unclear: Whether to create full synthetic register maps or minimal stubs.
   - Recommendation: Create minimal stub register maps (4-8 registers each) matching the RTDB struct fields. Deferred item in CONTEXT.md acknowledges this may need stubs.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework (C) | Custom assert-based (matching safety_manager pattern) |
| Framework (Python) | pytest + pytest-asyncio |
| Config file (C) | `src/comm_manager/c/tests/CMakeLists.txt` (Wave 0) |
| Config file (Python) | Root `pyproject.toml` (pytest section) |
| Quick run command (C) | `cd build && ctest -R comm_manager -j4 --output-on-failure` |
| Quick run command (Python) | `uv run pytest src/comm_manager/python/tests -x -q` |
| Full suite command | `cd build && ctest -j4 --output-on-failure && uv run pytest src/comm_manager/python/tests -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| COMM-01 | CAN DBC decode writes to RTDB | unit (C) | `ctest -R test_can_decode --output-on-failure` | Wave 0 |
| COMM-02 | Heartbeat timeout sets rack offline | unit (C) | `ctest -R test_can_health --output-on-failure` | Wave 0 |
| COMM-03 | Error frame parsing (TEC/REC/bus-off) | unit (C) | `ctest -R test_can_decode --output-on-failure` | Wave 0 |
| COMM-04 | Multi-cluster CAN (thread-per-interface) | integration | Manual -- requires 2 vcan interfaces | Wave 0 |
| COMM-05 | PCS Modbus polling + RTDB write | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | Wave 0 |
| COMM-06 | Timeout + exponential backoff | unit (Python) | `uv run pytest tests/test_health.py -x` | Wave 0 |
| COMM-07 | Exception code mapping | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | Wave 0 |
| COMM-08 | GPIO monitoring | N/A | Already tested in Phase 10 (SAFE-08) | Phase 10 |
| COMM-09 | Seqlock writes + ZMQ telemetry | integration | `uv run pytest tests/test_orchestrator.py -x` | Wave 0 |
| COMM-10 | Comm fault events via ZMQ PUSH | unit (Python) | `uv run pytest tests/test_events.py -x` | Wave 0 |
| COMM-11 | Startup discovery | unit (Python) | `uv run pytest tests/test_orchestrator.py -x` | Wave 0 |
| COMM-12 | Generic Modbus device polling | unit (Python) | `uv run pytest tests/test_modbus_device.py -x` | Wave 0 |
| COMM-13 | Hybrid C+Python architecture | integration | Manual -- verify both services start independently | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest src/comm_manager/python/tests -x -q` (Python) + `cd build && ctest -R comm -j4` (C)
- **Per wave merge:** Full suite (C + Python)
- **Phase gate:** Full suite green before verify-work

### Wave 0 Gaps
- [ ] `src/comm_manager/c/tests/CMakeLists.txt` -- test build config
- [ ] `src/comm_manager/c/tests/test_can_decode.c` -- CAN decode logic tests
- [ ] `src/comm_manager/python/tests/test_register_map.py` -- register map loader
- [ ] `src/comm_manager/python/tests/test_health.py` -- device health state machine
- [ ] `src/comm_manager/python/tests/test_modbus_device.py` -- Modbus device polling
- [ ] `src/comm_manager/python/tests/test_events.py` -- ZMQ event publishing
- [ ] `src/comm_manager/python/tests/test_orchestrator.py` -- orchestrator lifecycle
- [ ] `config/btms_register_map.yaml` -- stub BTMS register map
- [ ] `config/meter_register_map.yaml` -- stub Meter register map
- [ ] `config/dg_register_map.yaml` -- stub DG register map
- [ ] `config/pv_register_map.yaml` -- stub PV register map

## Sources

### Primary (HIGH confidence)
- `config/bms_layer2.dbc` -- DBC signal definitions, CAN ID scheme, message types
- `config/pcs_register_map.yaml` -- Register map format, scaling, signed values
- `src/common/c/include/rtdb.h` -- RTDB struct definitions, seqlock per section
- `src/common/c/include/ipc_defs.h` -- ZMQ socket paths, topic strings
- `src/common/python/src/ems_common/ipc.py` -- Python IPC encode/decode helpers
- `src/common/python/src/ems_common/rtdb.py` -- Python ctypes RTDB mirror
- `src/safety_manager/src/safety_event.c` -- C ZMQ event publishing pattern
- `tools/simulators/can_sim/` -- CAN simulator (cantools + python-can usage)
- `tools/simulators/modbus_sim/` -- Modbus simulator (pymodbus server, register map loader)
- [Linux SocketCAN documentation](https://docs.kernel.org/networking/can.html) -- CAN_RAW socket API, error frames
- [can-utils error.h](https://github.com/linux-can/can-utils/blob/master/include/linux/can/error.h) -- CAN error frame data byte layout

### Secondary (MEDIUM confidence)
- [PyModbus 3.12 Client docs](https://pymodbus.readthedocs.io/en/stable/source/client.html) -- AsyncModbusSerialClient parameters
- [PyModbus Issue #1654](https://github.com/pymodbus-dev/pymodbus/issues/1654) -- Async serial timeout fix (CLOSED, fixed in PR #1672)
- [CAN error frame TEC/REC layout](https://copperhilltech.com/blog/error-reporting-in-socketcan-with-specific-reference-to-the-mcp2515-can-controller/) -- Error counter byte positions

### Tertiary (LOW confidence)
- None -- all findings verified with official sources or codebase.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project, versions verified in pyproject.toml and CMakeLists
- Architecture: HIGH -- patterns established by safety_manager (C) and modbus_sim (Python), CONTEXT.md decisions lock key architecture choices
- Pitfalls: HIGH -- identified from codebase analysis (CAN ID masking, pymodbus addressing) and verified against official docs
- CAN error frame format: MEDIUM -- verified against kernel docs and error.h, but TEC/REC byte positions depend on driver implementation

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable domain, no fast-moving dependencies)
