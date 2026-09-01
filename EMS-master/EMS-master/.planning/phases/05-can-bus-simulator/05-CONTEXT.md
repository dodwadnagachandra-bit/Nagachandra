# Phase 5: CAN Bus Simulator - Context

**Gathered:** 2026-03-05
**Status:** Ready for planning

<domain>
## Phase Boundary

A vcan-based CAN bus simulator that replays realistic BMU→EMS Layer 2 frames with DBC-accurate signal encoding. Includes a synthetic DBC file, a Python simulator (CLI + importable), and tests that verify frame correctness via candump/cantools decode.

Output: synthetic DBC file, Python simulator module (CLI + importable), pytest test suite.

Not in scope: comm_manager CAN driver (M1), real BMS hardware integration, fault injection (Phase 8 SIM-06), RTDB writes.

</domain>

<decisions>
## Implementation Decisions

### DBC file — synthetic, swappable
- Create a synthetic DBC file at `config/bms_layer2.dbc` based on RTDB struct fields
- Designed to be easily replaced with the real vendor DBC when available
- All config profiles already reference `config/bms_layer2.dbc` — no path changes needed
- cantools library parses DBC and handles all signal encoding/decoding
- DBC file is the single source of truth for CAN signal layout — simulator and future comm_manager both consume it

### DBC message layout — real BMS convention
- Based on common BMS CAN protocols (Heltec, Emus, CATL-style patterns):
  - **Message 0x00 offset**: Pack summary — pack_v (16-bit, 0.1V), pack_i (16-bit signed, 0.1A), pack_soc (8-bit, 0.5%), pack_soh (8-bit, 0.5%), fault_code (16-bit bitmap)
  - **Messages 0x01–0x07 offset**: Cell voltage groups — 4 cells per 8-byte frame (16-bit each, 0.001V resolution), spanning up to 28 cells per group across 7 messages
  - **Message 0x08 offset**: Cell temperature group — 8 temperatures per frame (8-bit each, offset -40°C, 1°C resolution)
  - **Message 0x09 offset**: Rack status — online flag, balancing active count, min/max/avg cell voltage (16-bit, 0.001V), alarm flags
- Each rack/BMU uses CAN ID = `base_id + (cluster_index * 0x100 + rack_index)` per existing schema
- Standard CAN 2.0B frames (8 bytes each), no CAN FD needed for this layout
- Little-endian byte order (Intel format) — standard for industrial BMS protocols
- Signal naming follows RTDB field names: `pack_v`, `pack_i`, `cell_v_01`, `cell_t_01`, etc.

### Cycle assignment — fast and slow
- **Fast cycle (300ms)**: Pack summary (0x00) + cell voltage groups (0x01–0x07) — electrical signals need fast updates for protection and control
- **Slow cycle (2000ms)**: Cell temperature (0x08) + rack status (0x09) — thermal data changes slowly, status/diagnostic data is non-critical
- Matches common BMS convention: voltage/current/SOC at high frequency, temperature/status at low frequency
- Cycle rates read from `bms_config.yaml` timing section

### Data behavior — gentle drift with noise
- Cell voltages: base ~3.35V with slow sinusoidal drift (±0.15V over ~60s period) plus Gaussian noise (σ=0.005V)
- Cell temperatures: base ~32°C with slow drift (±5°C over ~120s period) plus noise (σ=0.5°C)
- Pack current: slow sine wave ±50A simulating charge/discharge cycling
- SOC: derived from integrated current (or simulated ramp 20%→80%→20% over ~10 min cycle)
- SOH: static 98% (degradation is a long-term phenomenon)
- Each rack gets slightly different base values (rack_offset * small delta) to look realistic in multi-rack mode
- All values stay within normal operating ranges — no out-of-bounds unless fault injection (Phase 8)

### Fault injection — deferred to Phase 8
- Phase 5 delivers normal operation only
- SIM-06 (YAML configurability + fault injection) is explicitly assigned to Phase 8 per roadmap
- Fault modes (cell overvoltage, comm loss, frame drops, corrupt data) will be Phase 8 scope
- Phase 5 simulator architecture should not prevent adding fault injection later (clean separation of data generation from frame sending)

### Runtime model — CLI + importable module
- Python module at `tools/simulators/can_simulator.py` (or `tools/simulators/can_sim/`)
- Importable: `from tools.simulators.can_sim import CANSimulator` for test integration
- CLI: `uv run python -m tools.simulators.can_sim --interface vcan0 --config config/bms_config.yaml`
- CLI flags: `--interface`, `--config`, `--racks` (override rack count), `--verbose`
- Graceful shutdown on SIGINT/SIGTERM

### Process model — one process per rack/cluster
- Each rack spawns as a separate process (or async task) to simulate independent BMUs
- Multi-rack mode: CLI `--racks N` or read from system_config.yaml topology
- Each BMU process sends frames independently on the shared vcan interface with its own CAN ID range
- This matches real hardware: each physical BMU is an independent CAN node
- For dev convenience, a single launcher starts all rack processes

### Claude's Discretion
- Exact Python package structure (single file vs package directory under tools/simulators/)
- Whether to use python-can Bus class directly or wrap it
- Whether rack processes use multiprocessing or asyncio tasks
- Test structure: how many test functions, what fixtures
- Whether the DBC file uses standard DBC format features (value tables, comments, attributes) or minimal syntax
- How to handle vcan interface setup (assume pre-configured vs auto-create)

</decisions>

<specifics>
## Specific Ideas

- The DBC file should be human-readable and well-commented — developers will reference it when building comm_manager in M1
- `candump vcan0 | cantools decode config/bms_layer2.dbc` should produce clear, labeled output for debugging
- The simulator is a developer tool, not production code — optimize for clarity and debuggability over performance
- Multi-rack mode is essential for testing: residential (4 racks), commercial (8 racks), container (64 racks on 4 clusters)
- The drift/noise behavior makes HMI charts look alive during demos and catches edge cases in alarm threshold logic

</specifics>

<code_context>
## Codebase Integration Points

### Existing files to modify
- `pyproject.toml` — add `python-can>=4.0` and `cantools>=39.0` to dev dependencies
- `Makefile` — consider adding `sim-can` target for quick launch

### New files
- `config/bms_layer2.dbc` — synthetic DBC file for BMU Layer 2 protocol
- `tools/simulators/can_sim/` — Python CAN simulator package (CLI + importable)
- `tests/test_can_simulator.py` — pytest tests for DBC correctness, frame encoding, multi-rack

### Existing assets consumed
- `config/bms_config.yaml` — CAN interface, bitrate, dbc_path, timing
- `config/profiles/*/bms_config.yaml` — profile-specific settings (residential 500kbps, container 1Mbps)
- `config/profiles/*/system_config.yaml` — topology dimensions (cluster_count, racks_per_cluster)
- `src/common/c/include/rtdb.h` — RTDB struct fields define what signals the DBC must encode

### Downstream consumers (future phases, not modified now)
- `src/comm_manager/` — M1 CAN driver will decode these same DBC messages and write to RTDB
- Phase 8 Integration — simulator YAML configurability and fault injection
- HMI demos — simulator provides live data for dashboard testing

</code_context>

<deferred>
## Deferred Ideas

- Fault injection modes (cell overvoltage, comm timeout, frame drops, corrupt CRC) — Phase 8 SIM-06
- YAML configurability for simulator parameters (noise amplitude, drift speed, base values) — Phase 8 SIM-06
- CAN FD mode support — not needed until BMS vendor confirms FD capability
- DBC-to-RTDB code generation (auto-generate comm_manager decode from DBC) — M1 comm_manager
- Replay from recorded candump logs (pcap/asc format) — future diagnostics tool
- Performance testing with 64 racks (128 BMUs) at full cell count — Phase 8 integration

</deferred>

---

*Phase: 05-can-bus-simulator*
*Context gathered: 2026-03-05*
