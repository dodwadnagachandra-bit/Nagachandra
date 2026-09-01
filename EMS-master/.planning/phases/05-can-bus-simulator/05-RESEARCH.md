# Phase 5: CAN Bus Simulator - Research

**Researched:** 2026-03-05
**Domain:** CAN bus simulation (python-can, cantools, DBC format, vcan)
**Confidence:** HIGH

## Summary

The CAN bus simulator requires three deliverables: a synthetic DBC file defining BMU Layer 2 messages, a Python simulator that encodes and sends those messages on vcan, and a pytest test suite verifying frame correctness. The ecosystem is mature and well-documented. python-can (v4.6.1) provides the transport layer with native SocketCAN/vcan support, while cantools (v41.2.0) handles DBC parsing and signal encoding/decoding. Both libraries are actively maintained and widely used in automotive/industrial CAN tooling.

The architecture is straightforward: cantools loads the DBC file and encodes signal dictionaries into raw CAN frame bytes, python-can sends those bytes on vcan via SocketCAN. Multiple processes can send on the same vcan interface simultaneously without locking -- SocketCAN is designed as a network device model that supports concurrent access. The Linux kernel handles all arbitration and buffering.

**Primary recommendation:** Use cantools for DBC-based signal encoding and python-can for vcan transport. Use asyncio with periodic tasks (not BCM send_periodic) for fine-grained control over multi-rate cycling. Structure as a Python package under `tools/simulators/can_sim/`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- DBC file at `config/bms_layer2.dbc` -- synthetic, swappable, based on RTDB struct fields
- DBC message layout follows real BMS convention (pack summary, cell voltage groups, cell temps, rack status)
- CAN ID scheme: `base_id + (cluster_index * 0x100 + rack_index)` with 10 messages per rack (0x00-0x09 offset)
- Little-endian (Intel) byte order throughout
- Fast cycle 300ms: pack summary (0x00) + cell voltages (0x01-0x07)
- Slow cycle 2000ms: cell temps (0x08) + rack status (0x09)
- Data behavior: sinusoidal drift + Gaussian noise within normal operating ranges
- Fault injection deferred to Phase 8
- Runtime: CLI + importable module at `tools/simulators/can_sim/`
- CLI flags: `--interface`, `--config`, `--racks`, `--verbose`
- Process model: one process per rack/cluster to simulate independent BMUs
- Dependencies: `python-can>=4.0` and `cantools>=39.0` in dev deps

### Claude's Discretion
- Package structure (single file vs package directory)
- Whether to wrap python-can Bus or use directly
- Whether rack processes use multiprocessing or asyncio tasks
- Test structure and fixtures
- DBC file complexity (minimal vs full attributes/comments)
- vcan interface setup handling (assume pre-configured vs auto-create)

### Deferred Ideas (OUT OF SCOPE)
- Fault injection modes -- Phase 8 SIM-06
- YAML configurability for simulator parameters -- Phase 8 SIM-06
- CAN FD mode support
- DBC-to-RTDB code generation -- M1 comm_manager
- Replay from recorded candump logs
- Performance testing with 64 racks
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SIM-01 | CAN bus simulator generates realistic BMU-to-EMS Layer 2 traffic on vcan at configurable rates (300ms/2000ms cycles) | python-can Bus + asyncio periodic tasks; cantools encode_message for DBC-accurate frames; vcan kernel module for virtual CAN |
| SIM-02 | CAN simulator replays all L2 message IDs (0x98FF0003-0x98FF0903) with DBC-accurate signal encoding | Synthetic DBC file with 10 messages per rack; cantools handles scaling/offset/byte-order encoding; extended CAN IDs via python-can |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-can | 4.6.1 | CAN bus transport (send frames on vcan) | De facto Python CAN library; native SocketCAN support; 4K+ GitHub stars |
| cantools | 41.2.0 | DBC parsing and signal encoding/decoding | Standard DBC tooling; encodes signal dicts to raw bytes; CLI decode for debugging |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pyyaml | 6.0+ | Parse bms_config.yaml and system_config.yaml | Already in dev deps; read timing and topology config |
| asyncio | stdlib | Periodic task scheduling | Multi-rate message cycling without threads |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| asyncio tasks | python-can send_periodic (BCM) | BCM is kernel-level efficient but harder to coordinate multi-rate cycles with data updates; asyncio gives full control over data generation per cycle |
| asyncio tasks | multiprocessing per rack | True process isolation matches real hardware but adds IPC complexity; asyncio tasks are simpler for a dev tool and sufficient for vcan |
| cantools encode | Manual struct.pack | DBC file becomes dead documentation instead of executable spec; cantools guarantees DBC-accurate encoding |

**Installation:**
```bash
uv add --dev python-can cantools
```

## Architecture Patterns

### Recommended Package Structure
```
tools/simulators/can_sim/
    __init__.py          # exports CANSimulator
    __main__.py          # CLI entry point (python -m tools.simulators.can_sim)
    simulator.py         # CANSimulator class (main orchestrator)
    rack.py              # RackSimulator (one per BMU, generates signals + sends frames)
    signals.py           # Signal data generation (drift, noise, SOC ramp)
config/
    bms_layer2.dbc       # Synthetic DBC file
tests/
    test_can_simulator.py  # pytest suite
```

### Pattern 1: Asyncio Task per Rack with Dual-Rate Cycling
**What:** Each rack is an asyncio task that runs two interleaved periodic loops -- fast (300ms) and slow (2000ms). Data generation happens inline before each send.
**When to use:** Default approach for this simulator.
**Example:**
```python
# Source: python-can docs + cantools docs
import asyncio
import can
import cantools
import math
import random
import time

class RackSimulator:
    def __init__(
        self,
        bus: can.Bus,
        db: cantools.Database,
        rack_id: int,
        base_can_id: int,
        fast_cycle_s: float,
        slow_cycle_s: float,
    ) -> None:
        self.bus = bus
        self.db = db
        self.rack_id = rack_id
        self.base_can_id = base_can_id
        self.fast_cycle_s = fast_cycle_s
        self.slow_cycle_s = slow_cycle_s
        self.start_time = time.monotonic()

    def _elapsed(self) -> float:
        return time.monotonic() - self.start_time

    def _gen_pack_summary(self) -> dict:
        t = self._elapsed()
        return {
            "pack_v": 52.0 + 2.0 * math.sin(t / 60.0),
            "pack_i": 50.0 * math.sin(t / 30.0),
            "pack_soc": 50.0 + 30.0 * math.sin(t / 300.0),
            "pack_soh": 98.0,
            "fault_code": 0,
        }

    async def run_fast_cycle(self) -> None:
        """Send pack summary + cell voltages at fast rate."""
        while True:
            # Encode and send pack summary (message offset 0x00)
            can_id = self.base_can_id + self.rack_id
            msg_name = f"BMU{self.rack_id}_PackSummary"
            data = self.db.encode_message(msg_name, self._gen_pack_summary())
            self.bus.send(can.Message(
                arbitration_id=can_id,
                data=data,
                is_extended_id=True,
            ))
            # ... encode and send cell voltage messages similarly ...
            await asyncio.sleep(self.fast_cycle_s)

    async def run_slow_cycle(self) -> None:
        """Send temperatures + rack status at slow rate."""
        while True:
            # ... encode and send temperature + status messages ...
            await asyncio.sleep(self.slow_cycle_s)

    async def run(self) -> None:
        """Run both cycles concurrently."""
        await asyncio.gather(
            self.run_fast_cycle(),
            self.run_slow_cycle(),
        )
```

### Pattern 2: CANSimulator Orchestrator
**What:** Top-level class loads config, creates bus, spawns rack tasks, handles shutdown.
**Example:**
```python
import asyncio
import signal
import can
import cantools
import yaml

class CANSimulator:
    def __init__(self, interface: str, config_path: str, dbc_path: str) -> None:
        self.interface = interface
        self.db = cantools.database.load_file(dbc_path)
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.bus: can.Bus | None = None

    async def run(self, num_racks: int) -> None:
        self.bus = can.Bus(interface="socketcan", channel=self.interface)
        try:
            fast_s = self.config["timing"]["fast_cycle_ms"] / 1000.0
            slow_s = self.config["timing"]["slow_cycle_ms"] / 1000.0
            base_id = self.config["can"]["base_id"]

            tasks = []
            for rack_idx in range(num_racks):
                rack = RackSimulator(
                    bus=self.bus,
                    db=self.db,
                    rack_id=rack_idx,
                    base_can_id=base_id,
                    fast_cycle_s=fast_s,
                    slow_cycle_s=slow_s,
                )
                tasks.append(asyncio.create_task(rack.run()))

            # Wait until cancelled
            await asyncio.gather(*tasks)
        finally:
            self.bus.shutdown()
```

### Pattern 3: DBC Message Naming Convention
**What:** DBC messages named by function with rack index as a parameter in the CAN ID, not in the message name. One set of message definitions serves all racks.
**When to use:** When all racks send identical message structures (just different CAN IDs).
**Example:**
```
BO_ 2566914051 PackSummary: 8 BMU
 SG_ pack_v : 0|16@1+ (0.1,0) [0|1000] "V" EMS
 SG_ pack_i : 16|16@1- (0.1,0) [-3000|3000] "A" EMS
 SG_ pack_soc : 32|8@1+ (0.5,0) [0|100] "%" EMS
 SG_ pack_soh : 40|8@1+ (0.5,0) [0|100] "%" EMS
 SG_ fault_code : 48|16@1+ (1,0) [0|65535] "" EMS
```
Then at runtime, encode with the generic message name but send with the rack-specific CAN ID:
```python
data = db.encode_message("PackSummary", signals)
msg = can.Message(arbitration_id=base_id + rack_offset, data=data, is_extended_id=True)
bus.send(msg)
```

### Anti-Patterns to Avoid
- **One DBC message per rack:** Do not create PackSummary_Rack0, PackSummary_Rack1, etc. -- this does not scale. Define one message type and vary the CAN ID at runtime.
- **Using send_periodic for data that changes:** BCM send_periodic sends the same data repeatedly. Since our signals drift/change every cycle, we need to re-encode each time. Use manual asyncio loops instead.
- **Blocking sleep in async context:** Use `await asyncio.sleep()`, never `time.sleep()`.
- **Opening one Bus per rack:** All racks share a single `can.Bus` instance. Opening multiple sockets to the same vcan is wasteful.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CAN signal encoding (scale, offset, bit packing) | Manual struct.pack with bit shifting | `cantools.database.encode_message()` | Bit-level packing with mixed endianness, signed/unsigned, arbitrary bit positions is extremely error-prone |
| DBC file parsing | Custom regex parser | `cantools.database.load_file()` | DBC format has many edge cases (multiplexing, attributes, comments, value tables) |
| CAN frame transport | Raw socket programming | `python-can Bus.send()` | Handles SocketCAN setup, error handling, extended IDs, FD frames |
| Periodic scheduling | Thread + time.sleep loop | asyncio.sleep in async loop | No thread management, clean cancellation, no GIL concerns |

**Key insight:** The entire CAN simulation stack (DBC parse -> signal encode -> frame send) is 3 library calls. The complexity is in the DBC file design and data generation logic, not in the transport.

## Common Pitfalls

### Pitfall 1: Extended CAN ID Encoding in DBC Files
**What goes wrong:** DBC files store extended (29-bit) CAN IDs with bit 31 set. If you write `BO_ 0x98FF0003` in the DBC, cantools will treat it as a standard 11-bit ID. For extended IDs, you must OR with 0x80000000.
**Why it happens:** DBC format convention from Vector -- the MSB flags extended vs standard frame.
**How to avoid:** In DBC file, use decimal value of `CAN_ID | 0x80000000`. For 0x98FF0003: `0x98FF0003 | 0x80000000 = 0x98FF0003` (already has bit 31 set since 0x98FF0003 > 0x7FFFFFFF). Convert to decimal: 2566914051.
**Warning signs:** cantools decode shows wrong message names or "unknown message" errors.

**Important detail on this project's CAN IDs:** The base ID 0x98FF0003 = 2566914051 in decimal. Since bit 31 (0x80000000 = 2147483648) is already set in 0x98FF0003, these are already flagged as extended IDs in the DBC format. The actual 29-bit CAN ID is `0x98FF0003 & 0x1FFFFFFF = 0x18FF0003`. In the DBC file, use the full 32-bit decimal value (2566914051) which includes the extended frame flag.

### Pitfall 2: Asyncio Sleep Drift
**What goes wrong:** `await asyncio.sleep(0.3)` drifts over time because it does not account for the time spent encoding and sending.
**Why it happens:** Sleep is relative to when it starts, not to a fixed clock.
**How to avoid:** Calculate next target time from a fixed epoch and sleep the remaining delta:
```python
async def periodic_loop(interval: float, callback) -> None:
    next_time = asyncio.get_event_loop().time() + interval
    while True:
        await callback()
        now = asyncio.get_event_loop().time()
        sleep_time = max(0, next_time - now)
        await asyncio.sleep(sleep_time)
        next_time += interval
```
**Warning signs:** Frame rate measured by candump diverges from configured rate over minutes.

### Pitfall 3: vcan Not Available
**What goes wrong:** Tests fail on CI or fresh dev machines because vcan0 is not configured.
**Why it happens:** vcan requires kernel module load and ip link setup, which needs root.
**How to avoid:** Tests should use python-can's `virtual` interface (in-process, no kernel module needed) for unit tests. Only integration tests need real vcan. Check for vcan availability and skip gracefully:
```python
import pytest
import subprocess

def vcan_available() -> bool:
    result = subprocess.run(["ip", "link", "show", "vcan0"],
                          capture_output=True)
    return result.returncode == 0

requires_vcan = pytest.mark.skipif(
    not vcan_available(), reason="vcan0 not available"
)
```
**Warning signs:** `OSError: [Errno 19] No such device` when creating Bus.

### Pitfall 4: Signed Signal Encoding
**What goes wrong:** Pack current (signed, can be negative for discharge) encodes incorrectly because the signal is defined as unsigned in the DBC.
**Why it happens:** Forgetting the `-` suffix in the DBC signal definition.
**How to avoid:** Use `@1-` (little-endian signed) for pack_i. Verify with a negative test value:
```python
data = db.encode_message("PackSummary", {"pack_i": -25.5})
decoded = db.decode_message("PackSummary", data)
assert decoded["pack_i"] == pytest.approx(-25.5, abs=0.1)
```
**Warning signs:** Negative currents show up as large positive values (unsigned wrap).

### Pitfall 5: Single Bus Instance Thread Safety
**What goes wrong:** If using multiprocessing, each process needs its own `can.Bus` instance. Sharing a Bus across processes causes socket errors.
**Why it happens:** File descriptors are not shared across process boundaries.
**How to avoid:** Use asyncio tasks (not multiprocessing) so all racks share one Bus in one event loop. If multiprocessing is chosen, each process must create its own `can.Bus`. Multiple processes CAN send to the same vcan interface -- SocketCAN supports this natively.

## Code Examples

### Creating and Sending a CAN Frame
```python
# Source: python-can 4.6.1 docs
import can

bus = can.Bus(interface="socketcan", channel="vcan0")
msg = can.Message(
    arbitration_id=0x98FF0003,
    data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
    is_extended_id=True,
)
bus.send(msg)
bus.shutdown()
```

### Loading DBC and Encoding a Message
```python
# Source: cantools 41.2.0 docs
import cantools

db = cantools.database.load_file("config/bms_layer2.dbc")

# Encode signal values to raw CAN bytes
data = db.encode_message("PackSummary", {
    "pack_v": 52.5,      # 0.1V resolution -> raw 525
    "pack_i": -10.0,     # 0.1A resolution, signed -> raw -100
    "pack_soc": 75.0,    # 0.5% resolution -> raw 150
    "pack_soh": 98.0,    # 0.5% resolution -> raw 196
    "fault_code": 0,
})
# data is bytes(8) ready to send

# Decode back to verify
signals = db.decode_message("PackSummary", data)
# signals = {"pack_v": 52.5, "pack_i": -10.0, "pack_soc": 75.0, ...}
```

### DBC File Structure for This Project
```dbc
VERSION ""

NS_ :

BS_:

BU_: BMU EMS

BO_ 2566914051 PackSummary: 8 BMU
 SG_ pack_v : 0|16@1+ (0.1,0) [0|1000] "V" EMS
 SG_ pack_i : 16|16@1- (0.1,0) [-3000|3000] "A" EMS
 SG_ pack_soc : 32|8@1+ (0.5,0) [0|100] "%" EMS
 SG_ pack_soh : 40|8@1+ (0.5,0) [0|100] "%" EMS
 SG_ fault_code : 48|16@1+ (1,0) [0|65535] "" EMS

BO_ 2566914052 CellVoltage_01: 8 BMU
 SG_ cell_v_01 : 0|16@1+ (0.001,0) [0|5] "V" EMS
 SG_ cell_v_02 : 16|16@1+ (0.001,0) [0|5] "V" EMS
 SG_ cell_v_03 : 32|16@1+ (0.001,0) [0|5] "V" EMS
 SG_ cell_v_04 : 48|16@1+ (0.001,0) [0|5] "V" EMS

CM_ BO_ 2566914051 "Pack-level summary: voltage, current, SOC, SOH, faults. Sent at fast cycle (300ms).";
CM_ BO_ 2566914052 "Cell voltages group 1 (cells 1-4). Sent at fast cycle (300ms).";
```

**CAN ID Calculation:**
- Base ID: 0x98FF0003 = 2566914051 decimal (bit 31 already set = extended frame)
- Rack 0: 2566914051 + 0 = 2566914051
- Rack 1: 2566914051 + 1 = 2566914052 -- COLLISION with CellVoltage_01!

**CRITICAL DESIGN NOTE:** The DBC uses message offset (0x00 for PackSummary, 0x01 for CellVoltage_01, etc.) which conflicts with per-rack ID offsets. The correct scheme needs rack offset to be multiplied by a stride >= 0x10 (16) to leave room for 10 message types per rack:
- Rack 0: base_id + 0x00..0x09 (messages 0-9)
- Rack 1: base_id + 0x10..0x19
- Rack N: base_id + (N * 0x10)...(N * 0x10 + 0x09)

Or follow the existing schema: `base_id + (cluster_index * 0x100 + rack_index)` which gives each rack a 0x100-wide ID space, leaving ample room for the 10 message offsets within each rack's ID range.

**Corrected CAN ID scheme:**
- Cluster 0, Rack 0: base_id + 0x000 + msg_offset (0x00-0x09)
- Cluster 0, Rack 1: base_id + 0x001 ... wait, this still collides.

**Resolution:** The schema says `base_id + (cluster * 0x100 + rack)`. This gives each rack a unique base. The 10 message types need sub-offsets. The cleanest approach:
- Each rack's CAN ID range: `base_id + (cluster * 0x100 + rack) * 0x10 + msg_offset`
- Or define DBC messages at fixed offsets from a rack base, and compute rack base at runtime.

In the DBC file, define messages at offsets 0x00-0x09 from a base. At runtime, compute the actual CAN ID as `base_id + (cluster * 0x100 + rack) + msg_offset * some_stride`. The CONTEXT.md implies `base_id + (cluster_index * 0x100 + rack_index)` is the rack base, and each of the 10 messages within a rack uses a small sub-offset.

**Recommended approach for DBC + runtime:**
1. DBC file defines 10 "template" messages with arbitrary base IDs (e.g., 0x00-0x09).
2. At runtime, the simulator computes actual CAN IDs per rack and uses `db.encode_message()` by message name only (not by ID), then sends with the computed CAN ID.
3. This avoids needing N*10 message definitions in the DBC file.

### Signal Data Generation with Drift and Noise
```python
# Source: project CONTEXT.md specifications
import math
import random

class SignalGenerator:
    def __init__(self, rack_index: int) -> None:
        self.rack_offset = rack_index * 0.02  # small per-rack delta

    def cell_voltage(self, cell_idx: int, elapsed_s: float) -> float:
        """Cell voltage with sinusoidal drift + Gaussian noise."""
        base = 3.35 + self.rack_offset
        drift = 0.15 * math.sin(2 * math.pi * elapsed_s / 60.0 + cell_idx * 0.1)
        noise = random.gauss(0, 0.005)
        return max(2.5, min(4.2, base + drift + noise))

    def cell_temperature(self, temp_idx: int, elapsed_s: float) -> float:
        """Cell temperature with slow drift + noise."""
        base = 32.0 + self.rack_offset * 50  # slight rack variation
        drift = 5.0 * math.sin(2 * math.pi * elapsed_s / 120.0 + temp_idx * 0.2)
        noise = random.gauss(0, 0.5)
        return max(-10, min(60, base + drift + noise))

    def pack_current(self, elapsed_s: float) -> float:
        """Pack current: charge/discharge cycling."""
        return 50.0 * math.sin(2 * math.pi * elapsed_s / 60.0)

    def pack_soc(self, elapsed_s: float) -> float:
        """SOC: ramp 20% -> 80% -> 20% over ~10 min."""
        cycle = elapsed_s % 600.0
        if cycle < 300.0:
            return 20.0 + 60.0 * (cycle / 300.0)
        else:
            return 80.0 - 60.0 * ((cycle - 300.0) / 300.0)
```

### CLI Entry Point Pattern
```python
# tools/simulators/can_sim/__main__.py
import argparse
import asyncio
import signal
import sys

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CAN Bus BMU Simulator")
    parser.add_argument("--interface", default="vcan0", help="CAN interface name")
    parser.add_argument("--config", default="config/bms_config.yaml",
                       help="Path to bms_config.yaml")
    parser.add_argument("--racks", type=int, default=None,
                       help="Number of racks (overrides system_config)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    sim = CANSimulator(
        interface=args.interface,
        config_path=args.config,
    )

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)

    try:
        loop.run_until_complete(sim.run(num_racks=args.racks or 4))
    finally:
        loop.close()

if __name__ == "__main__":
    main()
```

### Verifying with candump
```bash
# Terminal 1: Start simulator
uv run python -m tools.simulators.can_sim --interface vcan0 --racks 2 --verbose

# Terminal 2: Watch raw frames
candump vcan0

# Terminal 3: Decode frames with DBC
candump vcan0 | cantools decode config/bms_layer2.dbc
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| python-can 3.x callback-only | python-can 4.x asyncio + Notifier | 2022 (v4.0) | Clean async patterns, no manual threading |
| cantools < 39 limited DBC | cantools 39+ full DBC/ARXML | 2023 | Complete DBC attribute support |
| Manual CAN frame packing | DBC-driven encode/decode | Always available | DBC is single source of truth for signal layout |

**Deprecated/outdated:**
- python-can `socketcan_ctypes` and `socketcan_native` interfaces: merged into single `socketcan` interface in v4.0
- `can.interface.Bus()` long form: `can.Bus()` is the preferred constructor

## Open Questions

1. **CAN ID stride per rack**
   - What we know: Each rack has 10 message types (0x00-0x09 offset). The schema says `base_id + (cluster * 0x100 + rack)`.
   - What's unclear: Whether the 10 message offsets add to the rack-level ID or use a separate multiplier. With `cluster * 0x100 + rack`, rack 0 and message offset 1 collide with rack 1 and message offset 0.
   - Recommendation: Use `base_id + (cluster * 0x100 + rack) * 0x10 + msg_offset`. This gives each rack 16 ID slots (10 used, 6 reserved). Alternatively, `base_id + cluster * 0x1000 + rack * 0x10 + msg_offset` for even more spacing. Document the chosen scheme in the DBC file header comment.

2. **DBC message definitions: per-rack or template**
   - What we know: cantools encode_message can work by name (ignoring CAN ID in the DBC) or by ID.
   - What's unclear: Whether to define N*10 messages (one per rack per type) or 10 template messages and override the CAN ID at send time.
   - Recommendation: Define 10 template messages in the DBC. At runtime, encode by message name and override the CAN ID when creating the `can.Message`. This is cleaner, scales to any rack count, and the DBC file stays small. The future comm_manager decoder will need to map received CAN IDs to the correct message template -- document this mapping convention.

3. **python-can virtual interface for unit tests**
   - What we know: python-can has a `virtual` interface that works in-process without kernel modules.
   - What's unclear: Whether `virtual` interface supports all features needed for testing (extended IDs, 8-byte frames).
   - Recommendation: Use `virtual` for unit tests (encode/decode correctness), `vcan0` for integration tests (with skipif marker). Verify virtual interface works with extended IDs in Wave 0.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_can_simulator.py -x` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIM-01 | Simulator sends frames at configured rates on vcan | integration | `uv run pytest tests/test_can_simulator.py::test_frame_rate -x` | No -- Wave 0 |
| SIM-01 | Fast cycle messages sent at 300ms intervals | integration | `uv run pytest tests/test_can_simulator.py::test_fast_cycle_timing -x` | No -- Wave 0 |
| SIM-01 | Slow cycle messages sent at 2000ms intervals | integration | `uv run pytest tests/test_can_simulator.py::test_slow_cycle_timing -x` | No -- Wave 0 |
| SIM-02 | DBC file loads without errors | unit | `uv run pytest tests/test_can_simulator.py::test_dbc_loads -x` | No -- Wave 0 |
| SIM-02 | All 10 message types encode/decode correctly | unit | `uv run pytest tests/test_can_simulator.py::test_message_encode_decode -x` | No -- Wave 0 |
| SIM-02 | Signed signals (pack_i) encode correctly for negative values | unit | `uv run pytest tests/test_can_simulator.py::test_signed_signal -x` | No -- Wave 0 |
| SIM-02 | All L2 message IDs (0x98FF0003-0x98FF0903) are covered | unit | `uv run pytest tests/test_can_simulator.py::test_can_id_range -x` | No -- Wave 0 |
| SIM-02 | Multi-rack mode sends correct CAN IDs per rack | unit | `uv run pytest tests/test_can_simulator.py::test_multi_rack_ids -x` | No -- Wave 0 |
| SIM-02 | candump + cantools decode produces labeled output | integration | `uv run pytest tests/test_can_simulator.py::test_candump_decode -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_can_simulator.py -x`
- **Per wave merge:** `uv run pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_can_simulator.py` -- covers SIM-01, SIM-02
- [ ] `python-can` and `cantools` added to dev dependencies in pyproject.toml
- [ ] Verify python-can `virtual` interface supports extended CAN IDs

## Sources

### Primary (HIGH confidence)
- [python-can 4.6.1 Bus API](https://python-can.readthedocs.io/en/stable/bus.html) - Bus creation, send, send_periodic, shutdown
- [python-can 4.6.1 asyncio](https://python-can.readthedocs.io/en/stable/asyncio.html) - AsyncBufferedReader, Notifier with event loop
- [python-can 4.6.1 SocketCAN](https://python-can.readthedocs.io/en/stable/interfaces/socketcan.html) - vcan setup, BCM periodic, loopback behavior
- [cantools 41.2.0 docs](https://cantools.readthedocs.io/en/stable/) - load_file, encode_message, decode_message, Signal/Message properties
- [PyPI python-can](https://pypi.org/project/python-can/) - version 4.6.1, Aug 2025
- [PyPI cantools](https://pypi.org/project/cantools/) - version 41.2.0, Mar 2026

### Secondary (MEDIUM confidence)
- [CSS Electronics DBC intro](https://www.csselectronics.com/pages/can-dbc-file-database-intro) - DBC format syntax, signal definition, byte order
- [SocketCAN Wikipedia](https://en.wikipedia.org/wiki/SocketCAN) - multi-process concurrent access confirmation

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - both libraries verified on PyPI with current versions, official docs fetched
- Architecture: HIGH - asyncio + cantools encode pattern is well-documented and standard
- Pitfalls: HIGH - extended ID encoding, signed signals, vcan availability are well-known CAN development issues
- DBC design: MEDIUM - CAN ID stride scheme needs validation during implementation (Open Question #1)

**Research date:** 2026-03-05
**Valid until:** 2026-04-05 (stable domain, libraries mature)
