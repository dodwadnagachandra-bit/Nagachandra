# Phase 6: Modbus PCS Simulator - Research

**Researched:** 2026-03-13
**Domain:** Modbus RTU/TCP simulation (pymodbus async server, socat PTY pairs, PCS state machine)
**Confidence:** HIGH

## Summary

The Modbus PCS simulator requires a pymodbus async server acting as a Modbus slave, a PCS state machine (STANDBY/STARTING/RUNNING/STOPPING/FAULT), a synthetic register map YAML (~30 registers), and dual transport support (RTU via socat PTY pairs, TCP for CI). pymodbus 3.12.1 (latest stable, Feb 2026) provides mature async server APIs with `StartAsyncSerialServer` and `StartAsyncTcpServer`, sparse datastore support via `ModbusSparseDataBlock`, and write interception via subclassing datablock `setValues()`.

The architecture follows the CAN simulator pattern: a Python package at `tools/simulators/modbus_sim/` with CLI + importable module. The key difference from the CAN simulator is that Modbus is request/response (server waits for client polls) rather than broadcast, so the simulator runs a pymodbus server in the foreground with a background asyncio task updating register values (telemetry drift). Write hooks on setpoint/control registers trigger state machine transitions.

socat creates linked PTY pairs for RTU testing: `socat -d -d pty,raw,echo=0,link=/tmp/ems_pcs_sim pty,raw,echo=0,link=/tmp/ems_pcs_client`. The simulator manages socat as a subprocess, parses the symlink paths, and cleans up on shutdown. For CI, TCP mode avoids the socat dependency entirely.

**Primary recommendation:** Use pymodbus >= 3.7 with `ModbusSparseDataBlock` for the register map, `StartAsyncSerialServer`/`StartAsyncTcpServer` for dual transport, a subclassed datablock for write callbacks, and an asyncio background task for telemetry updates. Manage socat via `subprocess.Popen` with `link=` symlinks for deterministic PTY paths.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Dual transport: RTU default (socat PTY pair auto-created/destroyed), TCP for CI (`--transport rtu|tcp`)
- Register map: `config/pcs_register_map.yaml` -- ~30 registers, synthetic V1.24, swappable when real spec arrives
- Register map is comprehensive: AC (V/I/freq/PF/power per phase), DC (V/I/power), thermal, status, setpoints, energy
- 3-phase support: L1/L2/L3 registers, single-phase uses L1 only (L2/L3 zero)
- PCS state machine: STANDBY -> STARTING -> RUNNING -> STOPPING -> FAULT with configurable delays
- Power ramp: output ramps toward setpoint at configurable rate (e.g., 10%/s)
- Coherent telemetry: current = power / voltage, power factor derived, physically consistent
- Telemetry visibility: AC voltage/frequency/DC bus voltage visible in ALL states; current/power zero until RUNNING
- Temperature: ambient in STANDBY, rises under load in RUNNING (simple linear model)
- Fault injection deferred to Phase 8 SIM-06
- Runtime: CLI + importable at `tools/simulators/modbus_sim/`
- CLI: `uv run python -m tools.simulators.modbus_sim --transport rtu --config config/pcs_config.yaml`
- CLI flags: `--transport rtu|tcp`, `--config`, `--tcp-port` (default 5020), `--verbose`
- Graceful shutdown on SIGINT/SIGTERM
- Existing files to modify: `pyproject.toml`, `pcs_config.yaml`, `config/schemas/pcs_config.schema.json`, profile configs, `Makefile`

### Claude's Discretion
- Exact register addresses and scaling factors (must be internally consistent)
- pymodbus server class choice (ModbusSerialServer vs StartAsyncSerialServer, etc.)
- Whether socat is managed via subprocess or a wrapper helper
- Register map YAML schema structure (flat list vs grouped by category)
- How configurable delays are stored (in register map YAML vs separate simulator config section)
- Test structure: how many test functions, what fixtures
- Whether the state machine is a separate class or embedded in the simulator
- Signal drift implementation (can reuse or adapt from can_sim/signals.py)

### Deferred Ideas (OUT OF SCOPE)
- Modbus fault injection (timeout responses, exception codes, CRC errors) -- Phase 8 SIM-06
- YAML configurability for simulator parameters -- Phase 8 SIM-06
- Physics-lite thermal model (derating at threshold) -- future
- Efficiency curve modeling -- future
- Frequency droop response -- future
- Multiple PCS units on same RS485 bus (multi-slave) -- deferred
- Auto-generate comm_manager Modbus polling code from register map -- M1
- Replay from recorded Modbus traffic logs -- future
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pymodbus | 3.12.1 | Async Modbus server (RTU + TCP) | De facto Python Modbus library; 2K+ GitHub stars; production-stable; full FC support |
| pyyaml | 6.0+ | Parse register map and config YAML | Already in dev deps |
| asyncio | stdlib | Event loop, background tasks, signal handling | Standard async runtime |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| socat | system | Create linked PTY pairs for RTU transport | RTU mode only; not needed for TCP |
| pyserial | 3.5+ | Serial port abstraction (pymodbus dependency) | Pulled in automatically by pymodbus[serial] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pymodbus async server | Manual socket + struct.pack for Modbus frames | Enormous effort; pymodbus handles CRC, framing, FC dispatch |
| ModbusSparseDataBlock | ModbusSequentialDataBlock(0, [0]*0x6000) | Wastes ~24KB for sparse register map; sparse is cleaner |
| socat PTY pairs | Python `pty.openpty()` | pty.openpty() creates raw FDs, not symlinked paths; socat `link=` gives deterministic paths and handles raw/echo settings |
| Subclassed datablock callbacks | pymodbus `trace_pdu` hooks | trace_pdu is lower-level (PDU bytes); datablock setValues override is cleaner for register-level write detection |

**Installation:**
```bash
uv add --dev "pymodbus>=3.7"
```

Note: pymodbus pulls in pyserial automatically for serial support. socat is a system package, already in the `make setup` apt-get install list.

## Architecture Patterns

### Recommended Package Structure
```
tools/simulators/modbus_sim/
    __init__.py          # exports ModbusSimulator
    __main__.py          # CLI entry point (python -m tools.simulators.modbus_sim)
    simulator.py         # ModbusSimulator class (server lifecycle + background updater)
    state_machine.py     # PCSStateMachine (STANDBY/STARTING/RUNNING/STOPPING/FAULT)
    register_map.py      # Load register map YAML, build ModbusSparseDataBlock
    pty_pair.py          # socat PTY pair management (create/cleanup)
config/
    pcs_register_map.yaml  # Synthetic PCS V1.24 register map
tests/
    test_modbus_simulator.py  # pytest suite
```

### Pattern 1: Async Modbus Server with Background Updater
**What:** pymodbus runs the Modbus server as an async coroutine. A second asyncio task runs alongside, updating register values at 1Hz to simulate live telemetry.
**When to use:** Default pattern for this simulator.
**Example:**
```python
# Source: pymodbus 3.7+ server_updating.py example pattern
import asyncio
from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSlaveContext,
    ModbusSparseDataBlock,
)
from pymodbus.server import StartAsyncTcpServer

async def updating_task(context: ModbusServerContext) -> None:
    """Background task that updates telemetry registers at 1Hz."""
    slave_id = 0x01
    fc_hr = 0x03  # holding registers function code
    while True:
        # Read current state, compute new telemetry values
        values = context[slave_id].getValues(fc_hr, address=0x0001, count=1)
        # ... compute new voltage, current, etc. based on state machine
        new_voltage = 230  # example
        context[slave_id].setValues(fc_hr, address=0x0001, values=[new_voltage])
        await asyncio.sleep(1.0)

async def run_server() -> None:
    # Build sparse datablock with initial register values
    hr_block = ModbusSparseDataBlock({
        0x0001: 2300,   # AC voltage L1 (x10)
        0x0002: 0,      # AC current L1 (x10)
        0x0003: 500,    # frequency (x10 = 50.0 Hz)
        0x0291: 0,      # on/off control
        0x500E: 0,      # power setpoint
    })
    store = ModbusSlaveContext(
        di=ModbusSparseDataBlock({0: 0}),
        co=ModbusSparseDataBlock({0: 0}),
        hr=hr_block,
        ir=ModbusSparseDataBlock({0: 0}),
        zero_mode=True,  # address 0 maps to address 0 (not 1)
    )
    context = ModbusServerContext(slaves=store, single=True)

    # Start background updater
    updater = asyncio.create_task(updating_task(context))

    try:
        await StartAsyncTcpServer(
            context=context,
            address=("0.0.0.0", 5020),
        )
    finally:
        updater.cancel()
```

### Pattern 2: Write Callback via Subclassed DataBlock
**What:** Subclass `ModbusSparseDataBlock` and override `setValues()` to detect writes to control registers (on/off, power setpoint, fault reset) and trigger state machine transitions.
**When to use:** When the simulator needs to react to client writes (e.g., start/stop PCS, change power setpoint).
**Example:**
```python
# Source: pymodbus server_callback.py pattern
from pymodbus.datastore import ModbusSparseDataBlock
import logging

log = logging.getLogger(__name__)

class CallbackDataBlock(ModbusSparseDataBlock):
    """Sparse datablock that fires callbacks on register writes."""

    def __init__(
        self,
        values: dict[int, int | list[int]],
        state_machine: "PCSStateMachine",
        mutable: bool = True,
    ) -> None:
        super().__init__(values, mutable=mutable)
        self.state_machine = state_machine

    def setValues(self, address: int, values: list[int],
                  use_as_default: bool = False) -> None:
        super().setValues(address, values, use_as_default=use_as_default)
        log.debug("Register write: addr=0x%04X values=%s", address, values)

        # Dispatch to state machine based on register address
        if address == 0x0291:  # on/off control
            if values[0] == 1:
                self.state_machine.command_start()
            elif values[0] == 0:
                self.state_machine.command_stop()
        elif address == 0x500E:  # power setpoint
            self.state_machine.set_power_setpoint(values[0])
        elif address == 0x5010:  # fault reset
            if values[0] == 1:
                self.state_machine.command_fault_reset()
```

### Pattern 3: socat PTY Pair Management
**What:** Launch socat as a subprocess with `link=` flags for deterministic symlink paths. Parse output for confirmation, clean up on shutdown.
**When to use:** RTU transport mode.
**Example:**
```python
# Source: socat man page + project pattern
import subprocess
import os
import signal
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SIM_PTY = "/tmp/ems_pcs_sim"     # simulator end
CLIENT_PTY = "/tmp/ems_pcs_client"  # client/comm_manager end

class PTYPair:
    """Manages a socat PTY pair for Modbus RTU simulation."""

    def __init__(
        self,
        sim_path: str = SIM_PTY,
        client_path: str = CLIENT_PTY,
    ) -> None:
        self.sim_path: str = sim_path
        self.client_path: str = client_path
        self._process: subprocess.Popen | None = None

    def start(self) -> tuple[str, str]:
        """Create PTY pair via socat. Returns (sim_path, client_path)."""
        cmd = [
            "socat", "-d", "-d",
            f"pty,raw,echo=0,link={self.sim_path}",
            f"pty,raw,echo=0,link={self.client_path}",
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait briefly for socat to create symlinks
        for _ in range(20):  # 2 seconds max
            if (Path(self.sim_path).exists()
                    and Path(self.client_path).exists()):
                break
            import time
            time.sleep(0.1)
        else:
            raise RuntimeError(
                f"socat failed to create PTY links: {self.sim_path}, {self.client_path}"
            )

        log.info("PTY pair created: sim=%s client=%s", self.sim_path, self.client_path)
        return self.sim_path, self.client_path

    def stop(self) -> None:
        """Terminate socat and clean up symlinks."""
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        # Clean up symlinks
        for path in (self.sim_path, self.client_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        log.info("PTY pair cleaned up")
```

### Pattern 4: PCS State Machine (Separate Class)
**What:** Encapsulate PCS behavior in a dedicated class with clear state transitions, configurable delays, and power ramping.
**When to use:** Always -- keeps state logic separate from Modbus transport.
**Example:**
```python
import asyncio
import enum
import logging
import time

log = logging.getLogger(__name__)

class PCSState(enum.Enum):
    STANDBY = 0
    STARTING = 1
    RUNNING = 2
    STOPPING = 3
    FAULT = 4

class PCSStateMachine:
    """PCS behavioral simulation with state transitions and power ramping."""

    def __init__(
        self,
        startup_delay_s: float = 2.0,
        shutdown_delay_s: float = 1.5,
        ramp_rate_pct_per_s: float = 10.0,
        rated_power_kw: float = 50.0,
    ) -> None:
        self.state: PCSState = PCSState.STANDBY
        self.startup_delay_s = startup_delay_s
        self.shutdown_delay_s = shutdown_delay_s
        self.ramp_rate_pct_per_s = ramp_rate_pct_per_s
        self.rated_power_kw = rated_power_kw

        self.power_setpoint_kw: float = 0.0
        self.current_power_kw: float = 0.0
        self.fault_code: int = 0
        self._transition_start: float = 0.0

    def command_start(self) -> None:
        if self.state == PCSState.STANDBY:
            self.state = PCSState.STARTING
            self._transition_start = time.monotonic()
            log.info("PCS: STANDBY -> STARTING")

    def command_stop(self) -> None:
        if self.state == PCSState.RUNNING:
            self.state = PCSState.STOPPING
            self._transition_start = time.monotonic()
            log.info("PCS: RUNNING -> STOPPING")

    def command_fault_reset(self) -> None:
        if self.state == PCSState.FAULT:
            self.fault_code = 0
            self.state = PCSState.STANDBY
            log.info("PCS: FAULT -> STANDBY (fault cleared)")

    def set_power_setpoint(self, raw_value: int) -> None:
        """Set power setpoint from Modbus register value."""
        if self.state == PCSState.RUNNING:
            self.power_setpoint_kw = raw_value / 10.0  # scaling factor
            log.debug("Power setpoint: %.1f kW", self.power_setpoint_kw)

    def tick(self, dt_s: float) -> None:
        """Advance state machine by dt_s seconds. Call at 1Hz."""
        elapsed = time.monotonic() - self._transition_start

        if self.state == PCSState.STARTING:
            if elapsed >= self.startup_delay_s:
                self.state = PCSState.RUNNING
                log.info("PCS: STARTING -> RUNNING")

        elif self.state == PCSState.STOPPING:
            if elapsed >= self.shutdown_delay_s:
                self.current_power_kw = 0.0
                self.state = PCSState.STANDBY
                log.info("PCS: STOPPING -> STANDBY")

        elif self.state == PCSState.RUNNING:
            # Ramp toward setpoint
            max_delta = self.ramp_rate_pct_per_s / 100.0 * self.rated_power_kw * dt_s
            diff = self.power_setpoint_kw - self.current_power_kw
            if abs(diff) <= max_delta:
                self.current_power_kw = self.power_setpoint_kw
            else:
                self.current_power_kw += max_delta if diff > 0 else -max_delta
```

### Anti-Patterns to Avoid
- **Contiguous datablock for sparse registers:** Do NOT use `ModbusSequentialDataBlock(0, [0]*0x6000)` to cover addresses 0x0001 through 0x500E. This wastes memory and obscures the register map. Use `ModbusSparseDataBlock` with explicit addresses.
- **Blocking sleep in async context:** Use `await asyncio.sleep()`, never `time.sleep()` in the updater task.
- **Putting state machine logic in the datablock callback:** The callback should dispatch to the state machine, not contain transition logic. Keep the datablock thin.
- **Hard-coded register addresses in the simulator:** Load addresses from `pcs_register_map.yaml`. The register map is the single source of truth.
- **Mixing sync and async pymodbus APIs:** Use `StartAsyncSerialServer`/`StartAsyncTcpServer` exclusively. The sync variants (`StartSerialServer`) are thin wrappers that add overhead.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Modbus RTU framing (CRC16, byte stuffing) | Manual CRC + struct.pack | `pymodbus.server.StartAsyncSerialServer` | RTU CRC is well-specified but error-prone to implement; pymodbus handles it |
| Modbus TCP framing (MBAP header) | Raw TCP socket + header packing | `pymodbus.server.StartAsyncTcpServer` | MBAP header management, connection lifecycle handled |
| Register datastore with FC dispatch | Dict + manual FC03/FC06 parsing | `ModbusSlaveContext` + `ModbusSparseDataBlock` | Handles all Modbus function codes, validation, address mapping |
| Virtual serial port pairs | Python `pty.openpty()` + manual raw mode | `socat pty,raw,echo=0,link=...` | socat handles raw mode, echo off, symlink creation, bidirectional pipe |
| Periodic background updates | Thread + time.sleep | `asyncio.create_task` + `asyncio.sleep` | No threading complexity, clean cancellation |

**Key insight:** The simulator's real complexity is in the PCS state machine and coherent telemetry generation, not in the Modbus transport layer. pymodbus handles all protocol concerns; the developer focuses on behavior simulation.

## Common Pitfalls

### Pitfall 1: ModbusSparseDataBlock Zero Mode
**What goes wrong:** Modbus protocol uses 1-based addressing by default. If you configure register 0x0001 in the datablock but a client reads address 0x0001, pymodbus maps it to internal address 0x0002 (off-by-one).
**Why it happens:** `ModbusSlaveContext` default `zero_mode=False` adds +1 offset to match Modbus convention.
**How to avoid:** Set `zero_mode=True` in `ModbusSlaveContext` constructor so address N maps to internal address N. This matches the register map YAML addresses directly.
**Warning signs:** Client reads wrong register values; register 0x0000 returns data from 0x0001.

### Pitfall 2: socat Not Installed or Permissions
**What goes wrong:** `FileNotFoundError` when spawning socat, or PTY symlinks not created due to /tmp permissions.
**Why it happens:** socat may not be installed on CI runners; some systems restrict PTY creation.
**How to avoid:** Check for socat availability at startup, fall back to TCP mode with a warning. The `make setup` target already installs socat. In CI, use `--transport tcp`.
**Warning signs:** `FileNotFoundError: [Errno 2] No such file or directory: 'socat'`

### Pitfall 3: socat Process Leak on Crash
**What goes wrong:** If the simulator crashes without cleanup, socat keeps running and symlinks persist, blocking the next launch.
**Why it happens:** No atexit or signal handler to terminate socat.
**How to avoid:** Use `atexit.register(pty_pair.stop)` as a safety net. Check for stale symlinks on startup and warn/clean up. Handle SIGINT/SIGTERM to call stop().
**Warning signs:** "Address already in use" or stale symlinks in /tmp on restart.

### Pitfall 4: pymodbus Serial Server Blocks Event Loop
**What goes wrong:** `StartAsyncSerialServer` runs in the event loop but serial I/O can block if not properly configured.
**Why it happens:** pymodbus serial transport uses pyserial underneath, which is synchronous. pymodbus wraps it in asyncio but relies on proper event loop configuration.
**How to avoid:** Use `asyncio.new_event_loop()` (not `asyncio.get_event_loop()`). Ensure the serial port (socat PTY) is in raw mode (socat `raw,echo=0` handles this). Keep the background updater task lightweight (no CPU-intensive work).
**Warning signs:** Slow response times, updater task stalls during Modbus transactions.

### Pitfall 5: Register Value Overflow
**What goes wrong:** Modbus registers are 16-bit unsigned (0-65535). Writing a negative value or value > 65535 causes silent truncation or error.
**Why it happens:** Telemetry calculations produce floats that must be scaled and clamped to uint16 range.
**How to avoid:** Apply scaling (e.g., voltage * 10) and clamp to [0, 65535] before writing to datastore. For signed quantities (like power direction), use Modbus convention: either two's complement in a single register or split into sign + magnitude registers. Document the convention in the register map YAML.
**Warning signs:** Negative power shows as 65535 or similar large positive value.

### Pitfall 6: Sparse DataBlock Validation Failure
**What goes wrong:** Client reads a range of registers that spans a gap in the sparse datablock. pymodbus returns an exception response (illegal data address).
**Why it happens:** `ModbusSparseDataBlock.validate()` checks that ALL requested addresses exist. A multi-register read (FC03 with count > 1) across a gap fails.
**How to avoid:** Group related registers contiguously in the register map. For comm_manager polling, read each group separately (e.g., AC registers as one block, DC as another, status as another). Document valid read ranges in the register map YAML.
**Warning signs:** Modbus exception code 0x02 (Illegal Data Address) when reading register blocks.

## Code Examples

### Creating an Async Modbus TCP Server
```python
# Source: pymodbus 3.7+ official examples
import asyncio
from pymodbus import FramerType
from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSlaveContext,
    ModbusSparseDataBlock,
)
from pymodbus.server import StartAsyncTcpServer

async def run_tcp_server() -> None:
    # Sparse holding registers -- only the addresses we use
    hr = ModbusSparseDataBlock({
        0x0001: 2300,   # AC voltage L1 (x10 = 230.0V)
        0x0002: 0,      # AC current L1 (x10)
        0x0003: 500,    # AC frequency (x10 = 50.0 Hz)
        0x0291: 0,      # on/off control (writable)
        0x500E: 0,      # power setpoint (writable)
    })
    store = ModbusSlaveContext(
        di=ModbusSparseDataBlock({0: 0}),
        co=ModbusSparseDataBlock({0: 0}),
        hr=hr,
        ir=ModbusSparseDataBlock({0: 0}),
        zero_mode=True,
    )
    context = ModbusServerContext(slaves=store, single=True)

    await StartAsyncTcpServer(
        context=context,
        address=("0.0.0.0", 5020),
    )
```

### Creating an Async Modbus RTU Server (Serial)
```python
# Source: pymodbus 3.7+ official examples
from pymodbus import FramerType
from pymodbus.server import StartAsyncSerialServer

async def run_rtu_server(context: ModbusServerContext, port: str) -> None:
    await StartAsyncSerialServer(
        context=context,
        port=port,           # e.g., "/tmp/ems_pcs_sim"
        framer=FramerType.RTU,
        baudrate=9600,
        parity="N",
        stopbits=1,
        bytesize=8,
    )
```

### Building a Sparse Register Map from YAML
```python
# Source: project-specific pattern
import yaml
from pymodbus.datastore import ModbusSparseDataBlock

def build_datablock_from_yaml(yaml_path: str) -> ModbusSparseDataBlock:
    """Load register map YAML and build a sparse datablock."""
    with open(yaml_path) as f:
        reg_map = yaml.safe_load(f)

    values: dict[int, int] = {}
    for register in reg_map["registers"]:
        addr = register["address"]
        default = register.get("default", 0)
        # Apply scaling to default value
        scale = register.get("scale", 1)
        if isinstance(default, (int, float)):
            raw = int(default * scale)
            raw = max(0, min(65535, raw))  # clamp to uint16
        else:
            raw = 0
        values[addr] = raw

    return ModbusSparseDataBlock(values)
```

### Register Map YAML Structure
```yaml
# config/pcs_register_map.yaml -- Synthetic PCS V1.24 register map
# This is a synthetic register map for simulator development.
# Replace with the real V1.24 map when the PCS datasheet arrives.
#
# address: Modbus register address (0-indexed, holding register)
# name: Human-readable register name
# scale: raw_value = real_value * scale (e.g., 230.0V * 10 = 2300 raw)
# unit: Engineering unit
# access: r (read-only) | rw (read-write)
# default: Initial value in engineering units
# description: Field engineer documentation

metadata:
  version: "V1.24-synthetic"
  description: "Synthetic PCS Modbus register map for EMS simulator"
  phase_count: 3  # 3-phase default; set to 1 for residential

registers:
  # -- AC Side (per-phase) --
  - address: 0x0001
    name: ac_voltage_l1
    scale: 10
    unit: V
    access: r
    default: 230.0
    description: "AC voltage phase L1"

  - address: 0x0002
    name: ac_voltage_l2
    scale: 10
    unit: V
    access: r
    default: 230.0

  - address: 0x0003
    name: ac_voltage_l3
    scale: 10
    unit: V
    access: r
    default: 230.0

  # ... (current, frequency, power registers in contiguous groups)

  # -- Control (writable) --
  - address: 0x0291
    name: on_off_control
    scale: 1
    unit: ""
    access: rw
    default: 0
    description: "1=start, 0=stop"

  - address: 0x500E
    name: active_power_setpoint
    scale: 10
    unit: kW
    access: rw
    default: 0
    description: "Active power setpoint in kW * 10"
```

### Concurrent Server + Background Updater
```python
# Source: pymodbus server_updating.py pattern adapted for PCS
import asyncio

async def run_simulator(
    context: ModbusServerContext,
    state_machine: PCSStateMachine,
    transport: str,
    port: str | None,
    tcp_port: int,
) -> None:
    """Run Modbus server and telemetry updater concurrently."""
    updater = asyncio.create_task(
        telemetry_loop(context, state_machine, interval_s=1.0)
    )
    try:
        if transport == "tcp":
            await StartAsyncTcpServer(
                context=context,
                address=("0.0.0.0", tcp_port),
            )
        else:
            await StartAsyncSerialServer(
                context=context,
                port=port,
                framer=FramerType.RTU,
                baudrate=9600,
                parity="N",
                stopbits=1,
                bytesize=8,
            )
    finally:
        updater.cancel()

async def telemetry_loop(
    context: ModbusServerContext,
    sm: PCSStateMachine,
    interval_s: float = 1.0,
) -> None:
    """Update telemetry registers at 1Hz based on state machine."""
    slave_id = 0x01
    fc_hr = 0x03
    while True:
        sm.tick(interval_s)
        # Update registers from state machine
        # AC voltage: always live (grid metering)
        voltage = int(sm.ac_voltage_l1 * 10)
        context[slave_id].setValues(fc_hr, 0x0001, [voltage])
        # AC current: zero unless RUNNING
        current = int(sm.ac_current_l1 * 10) if sm.state == PCSState.RUNNING else 0
        context[slave_id].setValues(fc_hr, 0x0010, [current])
        # ... update all other registers
        await asyncio.sleep(interval_s)
```

### Testing with pymodbus Client
```python
# Source: pymodbus client examples
import asyncio
from pymodbus.client import AsyncModbusTcpClient

async def test_read_registers() -> None:
    client = AsyncModbusTcpClient("127.0.0.1", port=5020)
    await client.connect()
    try:
        # Read AC voltage L1 (register 0x0001)
        result = await client.read_holding_registers(address=0x0001, count=1, slave=1)
        assert not result.isError()
        voltage = result.registers[0] / 10.0
        print(f"AC Voltage L1: {voltage}V")

        # Write on/off control (register 0x0291)
        result = await client.write_register(address=0x0291, value=1, slave=1)
        assert not result.isError()
    finally:
        client.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pymodbus 2.x `ModbusSerialServer` (sync) | pymodbus 3.x `StartAsyncSerialServer` (async) | 2023 (v3.0) | Full async support, no threads needed |
| pymodbus 2.x `Framer=ModbusRtuFramer` | pymodbus 3.x `framer=FramerType.RTU` | 2023 (v3.0) | Simplified enum-based framer selection |
| pymodbus 2.x callback via `on_*` hooks | pymodbus 3.x subclass `setValues()` or `trace_pdu` | 2023 (v3.0) | Cleaner interception pattern |
| Manual socat + hard-coded /dev/pts paths | socat `link=` symlinks + subprocess management | Always available | Deterministic paths, no parsing stderr |

**Deprecated/outdated:**
- `ModbusRtuFramer`, `ModbusSocketFramer` classes: replaced by `FramerType.RTU`, `FramerType.SOCKET` enum
- `StartSerialServer` (sync): wrapper around async; use `StartAsyncSerialServer` directly
- pymodbus 2.x `ModbusServerFactory`: removed in 3.x
- `twisted`/`tornado` reactor backends: removed in 3.x, asyncio only

## Open Questions

1. **ModbusSparseDataBlock multi-register read across gaps**
   - What we know: `validate()` checks ALL addresses in the requested range exist. A read of 3 registers starting at 0x0001 requires 0x0001, 0x0002, 0x0003 all to be populated.
   - What's unclear: Whether the comm_manager (future M1) will do single-register reads or multi-register block reads.
   - Recommendation: Group registers contiguously by category in the register map (AC side: 0x0001-0x000F, DC side: 0x0010-0x001F, status: 0x0020-0x002F, setpoints at their sparse addresses). Document valid read ranges. This avoids gaps within logical groups while keeping the PCS V1.24 setpoint addresses (0x0291, 0x500E) at their canonical locations.

2. **pymodbus serial server and socat PTY interaction**
   - What we know: socat creates PTY pairs in raw mode. pymodbus `StartAsyncSerialServer` opens the serial port via pyserial.
   - What's unclear: Whether pymodbus needs any special serial port configuration for PTY (vs real RS485). PTYs do not have real baud rate constraints -- data flows at memory speed.
   - Recommendation: Configure pymodbus with the same baud rate as the real PCS (9600) for API consistency, but expect data to flow faster in simulation. Verify in Wave 0 testing that RTU framing works correctly over PTY.

3. **Signed register values for bidirectional power**
   - What we know: Modbus registers are unsigned 16-bit. The PCS needs to represent both charging and discharging power.
   - What's unclear: Whether the real V1.24 uses two's complement, sign+magnitude, or separate charge/discharge registers.
   - Recommendation: Use two's complement (common in industrial Modbus devices). For the synthetic map, define a `signed: true` flag in the YAML and handle conversion in the register update logic. Document the convention clearly for when the real V1.24 arrives.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_modbus_simulator.py -x` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIM-03 | Register map YAML loads and builds valid datablock | unit | `uv run pytest tests/test_modbus_simulator.py::test_register_map_loads -x` | No -- Wave 0 |
| SIM-03 | FC03 read holding registers returns correct values | unit | `uv run pytest tests/test_modbus_simulator.py::test_read_holding_registers -x` | No -- Wave 0 |
| SIM-03 | FC06 write single register updates datastore | unit | `uv run pytest tests/test_modbus_simulator.py::test_write_single_register -x` | No -- Wave 0 |
| SIM-03 | Sparse register map rejects reads to undefined addresses | unit | `uv run pytest tests/test_modbus_simulator.py::test_invalid_address_rejected -x` | No -- Wave 0 |
| SIM-04 | PCS state machine transitions: STANDBY->STARTING->RUNNING | unit | `uv run pytest tests/test_modbus_simulator.py::test_state_start_sequence -x` | No -- Wave 0 |
| SIM-04 | PCS state machine transitions: RUNNING->STOPPING->STANDBY | unit | `uv run pytest tests/test_modbus_simulator.py::test_state_stop_sequence -x` | No -- Wave 0 |
| SIM-04 | FAULT state clears on fault_reset write | unit | `uv run pytest tests/test_modbus_simulator.py::test_fault_reset -x` | No -- Wave 0 |
| SIM-04 | Power ramp approaches setpoint at configured rate | unit | `uv run pytest tests/test_modbus_simulator.py::test_power_ramp -x` | No -- Wave 0 |
| SIM-04 | Telemetry values are physically coherent (I = P / V) | unit | `uv run pytest tests/test_modbus_simulator.py::test_telemetry_coherence -x` | No -- Wave 0 |
| SIM-04 | Background updater modifies registers at 1Hz | integration | `uv run pytest tests/test_modbus_simulator.py::test_background_update -x` | No -- Wave 0 |
| SIM-03 | TCP server responds to pymodbus client reads/writes | integration | `uv run pytest tests/test_modbus_simulator.py::test_tcp_roundtrip -x` | No -- Wave 0 |
| SIM-03 | RTU server responds over socat PTY pair | integration | `uv run pytest tests/test_modbus_simulator.py::test_rtu_roundtrip -x` | No -- Wave 0 |
| SIM-04 | Write on/off=1 triggers STARTING state | integration | `uv run pytest tests/test_modbus_simulator.py::test_write_triggers_start -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_modbus_simulator.py -x`
- **Per wave merge:** `uv run pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_modbus_simulator.py` -- covers SIM-03, SIM-04
- [ ] `pymodbus>=3.7` added to dev dependencies in pyproject.toml
- [ ] Verify `ModbusSparseDataBlock` with `zero_mode=True` works as expected (address mapping)
- [ ] Verify pymodbus serial server works with socat PTY pairs (RTU framing over PTY)

## Sources

### Primary (HIGH confidence)
- [PyPI pymodbus](https://pypi.org/project/pymodbus/) - version 3.12.1, released Feb 2026, Python >= 3.10
- [pymodbus 3.7.4 Server docs](https://pymodbus.readthedocs.io/en/v3.7.4/source/server.html) - StartAsyncTcpServer, StartAsyncSerialServer, ModbusSerialServer signatures
- [pymodbus 3.7.4 Datastore docs](https://pymodbus.readthedocs.io/en/v3.7.4/source/library/datastore.html) - ModbusSparseDataBlock, ModbusSlaveContext, ModbusServerContext constructors and methods
- [pymodbus 3.7.4 Examples](https://pymodbus.readthedocs.io/en/v3.7.4/source/examples.html) - server_async.py, server_updating.py, server_callback.py patterns
- [pymodbus FramerType](https://pymodbus.readthedocs.io/en/v3.7.4/source/library/framer.html) - FramerType.RTU, FramerType.SOCKET enum values

### Secondary (MEDIUM confidence)
- [pymodbus GitHub server_async.py](https://github.com/pymodbus-dev/pymodbus/blob/dev/examples/server_async.py) - full async server example source (rate-limited, verified via docs)
- [socat man page](https://linux.die.net/man/1/socat) - PTY pair creation with `pty,raw,echo=0,link=` options
- Existing project CAN simulator (`tools/simulators/can_sim/`) - established patterns for CLI, signal generation, asyncio task structure

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pymodbus verified on PyPI with current version (3.12.1), official docs fetched and cross-referenced
- Architecture: HIGH - pymodbus async server + background updater is the documented pattern from official examples; CAN simulator provides proven project patterns
- Pitfalls: HIGH - zero_mode, sparse validation, socat lifecycle are well-documented pymodbus/system concerns
- Register map design: MEDIUM - synthetic addresses; real V1.24 may differ, but YAML format designed for drop-in replacement

**Research date:** 2026-03-13
**Valid until:** 2026-04-13 (stable domain, pymodbus 3.x API mature)
