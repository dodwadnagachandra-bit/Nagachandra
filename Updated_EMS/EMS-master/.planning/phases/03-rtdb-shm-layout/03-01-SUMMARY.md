# Phase 03-01 Summary: RTDB Foundation

**Executed:** 2026-03-05
**Status:** Complete — all tasks passed

## What Was Built

### seqlock.h — Lock-free concurrency primitive
- `ems_seqlock_t` with `_Atomic uint32_t sequence` counter
- 5 inline functions: `init`, `write_begin`, `write_end`, `read_begin`, `read_retry`
- Odd = write in progress, even = write complete protocol
- Release/acquire memory ordering for cross-core visibility

### rtdb.h — Hierarchical RTDB struct definitions
- 9 struct types: `ems_module_t`, `ems_rack_t`, `ems_cluster_t`, `ems_pcs_t`, `ems_gpio_t`, `ems_meter_t`, `ems_btms_t`, `ems_system_t`, `ems_rtdb_t`
- Compile-time MAX_* constants from system_config.schema.json: 8/16/20/108/40
- Pre-aggregated data at every hierarchy level (module, rack, cluster, system)
- Per-section seqlocks (one per rack + one per non-BMS section)
- `RTDB_MAGIC` (0x454D5352) and `RTDB_VERSION` (1) in header
- Topology count fields for consumers to know valid array entries
- `_Static_assert` for size validation at compile time
- All numeric fields have unit comments

### ems_types.h — Shared enumerations
- `ems_control_state_t` — 8 states (INIT through MAINTENANCE)
- `ems_pcs_state_t` — 4 states (OFF, STANDBY, RUNNING, FAULT)
- `ems_source_priority_t` — 3 modes (DAY, NIGHT, MANUAL)

### Python ctypes mirror (ems_common/rtdb.py)
- 10 `ctypes.Structure` classes matching every C struct exactly
- All constants mirrored with type annotations

### Tests
- **C test** (`tests/c/test_rtdb.c`): 7 tests — magic/version, struct sizes, residential/container populate, seqlock, non-BMS sections, topology counts
- **Python test** (`tests/test_rtdb.py`): 8 tests — constants, module size, size limit, populate/readback, seqlock fields, max-index access, C size match, field offsets

## Struct Sizes

| Struct | Size (bytes) |
|--------|-------------|
| ems_module_t | 700 |
| ems_rack_t | 14,064 |
| ems_cluster_t | 225,064 |
| ems_pcs_t | 56 |
| ems_gpio_t | 32 |
| ems_meter_t | 48 |
| ems_btms_t | 32 |
| ems_system_t | 48 |
| **ems_rtdb_t** | **1,800,744 (~1.72 MB)** |

C and Python sizes match exactly.

## Files Created/Modified

| File | Action |
|------|--------|
| `src/common/c/include/seqlock.h` | Created |
| `src/common/c/include/rtdb.h` | Created |
| `src/common/c/include/ems_types.h` | Updated (added 3 enums) |
| `src/common/python/src/ems_common/rtdb.py` | Created |
| `tests/c/test_rtdb.c` | Created |
| `tests/c/CMakeLists.txt` | Created |
| `tests/test_rtdb.py` | Created |
| `CMakeLists.txt` | Updated (enabled tests/c subdirectory) |

## Verification

- `cmake -B build && cmake --build build` — compiles clean with `-Wall -Wextra -Werror`
- `ctest --test-dir build -R rtdb` — 1/1 passed
- `uv run pytest tests/test_rtdb.py` — 8/8 passed
- `make test` — all 35 tests pass (including prior config validation tests)
