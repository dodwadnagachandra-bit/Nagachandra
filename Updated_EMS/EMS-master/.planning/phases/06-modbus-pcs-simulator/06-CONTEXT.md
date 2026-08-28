# Phase 6: Modbus PCS Simulator - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

A pymodbus slave simulator that responds to Modbus FC03 (read) and FC06 (write) with realistic PCS behavior, driven by a synthetic register map. Supports both RTU (socat PTY pairs) and TCP transport. Includes a basic PCS state machine (STANDBY→STARTING→RUNNING→STOPPING→FAULT) with configurable delays and coherent telemetry.

Output: synthetic register map YAML, Python simulator package (CLI + importable), pytest test suite.

Not in scope: comm_manager Modbus driver (M1), real PCS hardware integration, fault injection (Phase 8 SIM-06), RTDB writes, physics-lite modeling (thermal derating, efficiency curves, frequency droop).

</domain>

<decisions>
## Implementation Decisions

### Serial transport — dual mode (RTU default, TCP for CI)
- **RTU mode (default):** Simulator auto-creates a socat PTY pair on startup, tears it down on shutdown. No manual setup required.
- **TCP mode:** Simulator listens on localhost for CI environments where socat may not be available.
- **CLI flag:** `--transport rtu|tcp` (RTU default)
- **CI strategy:** Tests run in TCP mode (no socat dependency in required CI checks). RTU+socat for local dev.
- **Makefile:** `sim-modbus` target creates socat pair, launches simulator, tears down on exit.
- **Rationale for RTU testing:** RS485 in the field faces CRC errors from EMI, frame boundary violations from timing bugs, half-duplex collisions, baud/parity mismatches. TCP hides all of these. RTU framing matters most for M1 comm_manager development — Phase 6 simulator needs to support it so comm_manager can be tested against realistic framing.

### Register map — comprehensive, separate data file, swappable
- **File:** `config/pcs_register_map.yaml` — single source of truth for PCS Modbus protocol definition
- **Pattern:** Parallels `config/bms_layer2.dbc` — protocol definition, not deployment config
- **Scope:** ~30 registers covering all categories a real PCS V1.24 exposes:
  - **AC side:** voltage (L1/L2/L3), current (L1/L2/L3), frequency, power factor, apparent power, reactive power, active power
  - **DC side:** DC bus voltage, DC current, DC power
  - **Thermal:** heatsink temperature, ambient temperature
  - **Status:** operating state (enum), fault code word (bitmap), warning code word (bitmap)
  - **Setpoints (writable):** active power setpoint, reactive power setpoint, power factor setpoint, on/off control, fault reset
  - **Energy:** cumulative kWh charge, kWh discharge
- **Synthetic:** All register addresses, scaling factors, and data types are synthesized. Designed to be swapped with the real V1.24 register map when the document arrives — same file format, drop-in replacement.
- **3-phase and single-phase:** Register map includes per-phase (L1/L2/L3) registers. Single-phase sites use L1 only; L2/L3 read zero. Determined by a phase_count field in the register map or pcs_config.
- **pcs_config.yaml stays lean:** Connection settings, timing, and a `register_map_path` reference (like bms_config references `dbc_path`). No register addresses in deployment config.

### PCS behavioral simulation — Level 2 state machine
- **States:** `STANDBY → STARTING → RUNNING → STOPPING → FAULT`
- **Write on/off=1:** Transitions STANDBY → STARTING (configurable delay, e.g., 2s) → RUNNING
- **Write on/off=0:** Transitions RUNNING → STOPPING (configurable delay) → STANDBY
- **Power setpoint:** Only takes effect in RUNNING state. Ignored/rejected in other states.
- **Power ramp:** Output power ramps toward setpoint at a configurable rate (e.g., 10%/s) rather than jumping instantly.
- **Configurable delays:** startup_delay_s, shutdown_delay_s, ramp_rate_pct_per_s defined in the register map YAML or a simulator config section.
- **Coherent telemetry:** Current = power / voltage (not random). Power factor derived from active/apparent power. Values are physically consistent.
- **Fault state:** Writing fault_reset=1 when in FAULT clears the fault code and transitions to STANDBY.

### Telemetry behavior — realistic drift with state-dependent values
- **AC voltage:** Slow sinusoidal drift around nominal (230V ±5V) with Gaussian noise. Visible in ALL states (PCS metering is always live when connected to grid).
- **AC frequency:** Slow drift around 50Hz (±0.2Hz) with noise. Visible in ALL states.
- **AC current/power:** Zero in STANDBY/STARTING/STOPPING. Ramps to setpoint-derived value in RUNNING.
- **DC bus voltage:** Visible in ALL states (reads battery pack voltage). ~48V residential, ~400-800V container, with small drift.
- **DC current/power:** Zero in STANDBY. Follows AC power / efficiency factor in RUNNING.
- **Temperature:** Ambient (~25-35°C) in STANDBY. Rises under load in RUNNING (simple linear model, not physics-based).
- **Rationale:** Metering registers live in all states confirms "PCS is connected and alive" to operators via HMI. Power/current zero until RUNNING matches real hardware behavior.

### Fault injection — deferred to Phase 8
- Phase 6 delivers normal operation + basic FAULT state only
- SIM-06 (YAML configurability + fault injection) is explicitly assigned to Phase 8 per roadmap
- Fault modes (Modbus timeout, exception responses, register corruption) will be Phase 8 scope
- Phase 6 architecture should not prevent adding fault injection later (clean separation of state machine from transport layer)

### Runtime model — CLI + importable module
- Python package at `tools/simulators/modbus_sim/` (follows `can_sim/` pattern)
- Importable: `from tools.simulators.modbus_sim import ModbusSimulator` for test integration
- CLI: `uv run python -m tools.simulators.modbus_sim --transport rtu --config config/pcs_config.yaml`
- CLI flags: `--transport rtu|tcp`, `--config`, `--tcp-port` (default 5020), `--verbose`
- Graceful shutdown on SIGINT/SIGTERM
- socat PTY pair auto-created/destroyed in RTU mode

### Claude's Discretion
- Exact register addresses and scaling factors in the synthetic register map (must be internally consistent but specific values are flexible)
- pymodbus server class choice (ModbusSerialServer vs StartAsyncSerialServer, etc.)
- Whether socat is managed via subprocess or a wrapper helper
- Register map YAML schema structure (flat list vs grouped by category)
- How configurable delays are stored (in register map YAML vs separate simulator config section)
- Test structure: how many test functions, what fixtures
- Whether the state machine is a separate class or embedded in the simulator
- Signal drift implementation (can reuse or adapt from can_sim/signals.py)

</decisions>

<specifics>
## Specific Ideas

- The register map YAML should be well-documented — field engineers will cross-reference it against the PCS manual when the real V1.24 arrives
- `modbus_cli` or pymodbus REPL should be able to poll the simulator and get coherent PCS telemetry for debugging
- The state machine makes HMI demos realistic — operator presses "start PCS," sees STARTING state for 2 seconds, then RUNNING with power ramping up
- Multi-phase support (1-phase vs 3-phase) lets residential and container profiles exercise different code paths
- The simulator is a developer tool — optimize for clarity and debuggability over performance
- Register map as YAML enables future tooling: auto-generate comm_manager Modbus polling code from the map

</specifics>

<code_context>
## Codebase Integration Points

### Existing files to modify
- `pyproject.toml` — add `pymodbus>=3.6` to dev dependencies
- `pcs_config.yaml` — add `register_map_path` field pointing to `config/pcs_register_map.yaml`
- `config/schemas/pcs_config.schema.json` — add `register_map_path` property to schema
- `config/profiles/*/pcs_config.yaml` — add `register_map_path` to all profile configs
- `Makefile` — add `sim-modbus` target

### New files
- `config/pcs_register_map.yaml` — synthetic PCS V1.24 register map (~30 registers)
- `tools/simulators/modbus_sim/` — Python Modbus simulator package (CLI + importable)
- `tests/test_modbus_simulator.py` — pytest tests for register access, state machine, transport modes

### Existing assets consumed
- `config/pcs_config.yaml` — connection settings, timing, register_map_path
- `config/profiles/*/pcs_config.yaml` — profile-specific PCS settings
- `tools/simulators/can_sim/signals.py` — signal drift/noise generation (reusable pattern or import)

### Downstream consumers (future phases, not modified now)
- `src/comm_manager/` — M1 Modbus driver will read/write these same registers
- `src/control_manager/` — M2 state machine sends setpoints via comm_manager
- Phase 8 Integration — simulator YAML configurability and fault injection
- HMI demos — simulator provides live PCS data for dashboard testing

</code_context>

<deferred>
## Deferred Ideas

- Modbus fault injection (timeout responses, exception codes, CRC errors) — Phase 8 SIM-06
- YAML configurability for simulator parameters (noise amplitude, drift speed, ramp rate override) — Phase 8 SIM-06
- Physics-lite thermal model (temperature rises under load, derating at threshold) — future enhancement
- Efficiency curve modeling (DC power > AC power by 2-5%) — future enhancement
- Frequency droop response simulation — future enhancement
- Multiple PCS units on same RS485 bus (multi-slave) — deferred until multi-PCS architecture decided
- Auto-generate comm_manager Modbus polling code from register map YAML — M1 comm_manager
- Replay from recorded Modbus traffic logs — future diagnostics tool

</deferred>

---

*Phase: 06-modbus-pcs-simulator*
*Context gathered: 2026-03-13*
