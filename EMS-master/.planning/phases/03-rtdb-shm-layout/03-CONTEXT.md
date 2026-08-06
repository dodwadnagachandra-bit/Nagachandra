# Phase 3: RTDB Foundation - Context

**Gathered:** 2026-03-05
**Status:** Ready for planning

<domain>
## Phase Boundary

C header definitions for the hierarchical RTDB struct layout, seqlock concurrency primitive, and a test program that validates the struct. Headers only — runtime data_manager (shm_open, mmap, lifecycle) is M1. Python ctypes mirror included to validate cross-language compatibility and enable Python-based tests.

</domain>

<decisions>
## Implementation Decisions

### RTDB data fields — BMS hierarchy
- Pre-aggregated data at every level of the hierarchy
- Module level: `cell_v[K]`, `cell_t[T]`, `balancing_status[K]` (per-cell data from BMS)
- Rack level: `pack_v`, `pack_i`, `pack_soc`, `pack_soh`, `min_cell_v`, `max_cell_v`, `avg_cell_v`, `min_cell_t`, `max_cell_t`, `avg_cell_t` (rack aggregates — written by comm_manager after DBC decode)
- Cluster level: same aggregate pattern as rack (cluster-level rollup)
- System level: `total_soc`, `total_power_kw`, `total_energy_kwh` (top-level aggregates)
- Rationale: pre-aggregation avoids every consumer (control_manager, HMI, alarm_manager) scanning all cells on every 1Hz cycle

### RTDB data fields — non-BMS sections
- Unified RTDB with BMS hierarchy + flat sections for other devices
- PCS section: voltage, current, active_power, reactive_power, frequency, pcs_state, fault_code (flat struct, one per PCS)
- GPIO section: `di_state[8]`, `do_state[8]` (boolean arrays for safety I/O)
- Meter section: grid voltage, current, power, energy import/export
- BTMS section: inlet_temp, outlet_temp, fan_speed per rack
- System section: control_state (enum), active_setpoint_kw, source_priority, ems_uptime_s
- Single shm segment, single struct — one `shm_open("/ems_rtdb")` call

### Timestamps and staleness
- Timestamp per rack/device granularity
- Each rack gets `last_update_ms` (monotonic clock) — multi-BMU deployments can detect single-rack staleness
- PCS, GPIO, meter, BTMS sections each get their own `last_update_ms`
- Consumers compare against threshold to detect stale data (e.g., >3s = stale for BMS, >2s for PCS)
- Use `CLOCK_MONOTONIC` (not wall clock) — immune to NTP jumps

### Sizing strategy — compile-time max arrays
- All arrays sized to schema maximums: `MAX_CLUSTERS=8`, `MAX_RACKS_PER_CLUSTER=16`, `MAX_MODULES_PER_RACK=20`, `MAX_CELLS_PER_MODULE=108`, `MAX_TEMPS_PER_MODULE=40`
- Config values stored in the RTDB header tell readers how many entries are actually valid
- Every deployment allocates the same struct size (~800KB max)
- Residential (1×4×8×16) wastes most of the buffer — acceptable, 800KB is trivial on 4GB DDR4
- Rationale: simplicity — no runtime size calculation, no pointer arithmetic, all offsets known at compile time, ctypes mirror is trivial

### Seqlock — included in Phase 3
- `seqlock.h` ships alongside `rtdb.h` — they're consumed together
- One seqlock per rack (not per module — too many locks, not per cluster — too coarse)
- Plus one seqlock each for PCS, GPIO, meter, BTMS, system sections
- Inline functions: `seqlock_write_begin()`, `seqlock_write_end()`, `seqlock_read_begin()`, `seqlock_read_retry()`
- Sequence number is `_Atomic uint32_t` — odd value means write in progress, reader retries
- No blocking, no mutexes, no priority inversion — lock-free readers

### Python ctypes mirror — included in Phase 3
- `ems_common` Python package gets ctypes.Structure classes matching every C struct
- Enables Python roundtrip test: create struct in C, read from Python (and vice versa)
- Validates field offsets and total size match between C and Python
- Located in `src/common/python/src/ems_common/rtdb.py`

### Test program
- C unit test (ctest): allocate struct, populate sample data for residential/commercial/container topologies, read back, verify values
- Size validation: assert `sizeof(ems_rtdb_t)` matches documented expected size, print breakdown per level
- Python roundtrip: ctypes test creates struct, writes via Python, reads from C (or vice versa via shared buffer), verifies field alignment
- Document exact memory footprint for all 3 profiles in test output

### Byte alignment and portability
- Use `__attribute__((packed))` sparingly — only if padding would cause C/Python mismatch
- Prefer natural alignment (no packing) for performance on ARM64
- Add `_Static_assert` on struct sizes to catch compiler/platform alignment surprises
- Both dev workstation (x86_64) and ECU (ARM64 A53) use LP64 — same sizes for int/long/pointer
- Include a `RTDB_VERSION` magic number in the header struct for future compatibility

### Claude's Discretion
- Exact field ordering within each struct level (optimize for cache line alignment if beneficial)
- Whether BTMS fields live inside the rack struct or as a separate top-level section
- Naming conventions for struct types and field names (snake_case per C99 convention)
- Test framework details (Unity, custom assert macros, or plain assert)
- Whether `_Static_assert` uses C11 or a C99-compatible macro wrapper

</decisions>

<specifics>
## Specific Ideas

- RTDB should feel like a SCADA point database — flat reads for any signal, no traversal logic needed by consumers
- sizeof(ems_rtdb_t) should be documented in a comment at the top of rtdb.h with breakdown per level
- The 3 profile configs (residential/commercial/container) serve as test fixtures for size validation
- `RTDB_VERSION` field lets data_manager detect struct mismatches after software updates

</specifics>

<code_context>
## Codebase Integration Points

### Existing files to modify
- `src/common/c/include/ems_types.h` — has TODO comments for rtdb types, may need base types (e.g., ems_control_state_t enum)
- `src/common/c/CMakeLists.txt` — currently INTERFACE library, new headers auto-included
- `src/common/python/src/ems_common/__init__.py` — stub, will get rtdb.py import

### New files
- `src/common/c/include/rtdb.h` — hierarchical RTDB struct definitions
- `src/common/c/include/seqlock.h` — seqlock primitive (inline functions)
- `src/common/python/src/ems_common/rtdb.py` — ctypes.Structure mirror
- `tests/test_rtdb.c` — C unit test for struct layout and size
- `tests/test_rtdb.py` — Python ctypes roundtrip test

### Config files consumed
- `config/schemas/system_config.schema.json` — topology max values define MAX_* constants
- `config/profiles/*/system_config.yaml` — test fixtures for size validation

### Downstream consumers (future phases, not modified now)
- `src/data_manager/c/` — will shm_open + mmap the RTDB (M1)
- `src/comm_manager/` — will write BMS/PCS/GPIO sections (M1)
- `src/safety_manager/` — will read GPIO section, write safety state (M1)
- `src/control_manager/` — will read all sections (M2)

</code_context>

<deferred>
## Deferred Ideas

- Runtime RTDB lifecycle (shm_open, mmap, cleanup) — data_manager in M1 (RTDB-01)
- Single-writer-per-path enforcement mechanism — M1 (RTDB-05)
- RTDB struct migration between software versions — future phase
- DBC-to-struct code generation (auto-generate module fields from .dbc file) — Phase 5 or M1
- RTDB memory-mapped file persistence for crash recovery — not in M0 scope

</deferred>

---

*Phase: 03-rtdb-shm-layout*
*Context gathered: 2026-03-05*
