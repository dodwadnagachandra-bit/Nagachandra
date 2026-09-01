# Phase 7: GPIO Test Harness - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

A GPIO test harness that lets safety_manager DI/DO logic be tested without physical wiring. Hybrid approach: RTDB shared-memory injection works everywhere, gpio-sim kernel module used opportunistically for realistic libgpiod testing. Covers all 8 DI + 8 DO signals. Triggerable from CLI commands or Python API.

Output: Python harness package (CLI + importable), pytest test suite.

Not in scope: safety_manager itself (M1), debounce/bounce simulation (Phase 8 SIM-06), fault injection (Phase 8 SIM-06), RTDB creation (Phase 3 delivered this).

</domain>

<decisions>
## Implementation Decisions

### Mock technology — hybrid (RTDB + gpio-sim)
- **RTDB-only mode (default fallback):** Harness writes DI values directly into `ems_gpio_t.di[8]` via Python ctypes RTDB bindings (Phase 3). Reads DO state from `ems_gpio_t.do_state[8]`. No kernel dependencies. Works on any dev machine and in CI.
- **gpio-sim mode:** Uses Linux gpio-sim kernel module (kernel >= 5.17) to create virtual GPIO chips. safety_manager C code uses real libgpiod calls against virtual chips. Harness controls DI via sysfs, reads DO via sysfs.
- **Backend selection:** Auto-detect at startup with explicit override: `--backend gpio-sim|rtdb|auto` (default `auto`). Auto probes for gpio-sim module, falls back to RTDB-only silently.
- **Rationale:** RTDB-only gives portable testing everywhere. gpio-sim gives realistic libgpiod coverage when available. Auto-detect means one CLI invocation works in all environments.

### RTDB ownership split — harness writes DI, safety_manager writes DO
- Harness writes `ems_gpio_t.di[8]` (simulating sensor inputs) and reads `ems_gpio_t.do_state[8]` (verifying actuator outputs)
- safety_manager (M1) will write `do_state[8]` and read `di[8]`
- Clean split — no ownership conflict on the same fields
- Uses existing Python ctypes RTDB bindings and seqlock from Phase 3
- Direct `shm_open` + write — no intermediate files, pipes, or daemons needed for RTDB mode

### Process model — independent, consistent with other simulators
- Harness and safety_manager run as independent processes (safety_manager doesn't exist yet — M1)
- Matches CAN sim (doesn't launch comm_manager) and Modbus sim (doesn't launch comm_manager)
- Pytest fixtures can wire up integration tests in M1 when safety_manager exists
- No speculative integration code in Phase 7

### CLI interaction model — one-shot commands, daemon only for gpio-sim
- **RTDB-only mode:** One-shot commands. `gpio-harness set DI-6 high` opens shm, writes, exits. No persistent process needed.
- **gpio-sim mode:** `gpio-harness daemon` starts a long-running process to hold the virtual GPIO chip alive. CLI `set`/`get` commands write to gpio-sim sysfs directly (no IPC protocol to the daemon — sysfs is the shared state).
- **Result:** No Unix socket, no client/server protocol. Simplest possible architecture for a developer tool.

### Pin addressing — both numbers and config names
- Both `DI-6` and `ESTOP_NO` accepted in CLI and Python API
- Name resolution loads `gpio_config.yaml` when a non-DI/DO-N pattern is passed
- Developers remember names (`ESTOP_NO`) better than pin numbers for safety-critical signals
- Pin number is always the canonical internal representation

### Multi-pin atomic writes — supported
- Single-pin: `gpio-harness set DI-6 high`
- Multi-pin atomic: `gpio-harness set DI-6=high DI-7=low` writes both under one seqlock acquisition
- Needed for realistic E-Stop testing (dual-channel DI-6 NO + DI-7 NC must change together)
- Single-pin mode useful for testing discrepancy detection (wiring fault simulation)

### Polarity handling — raw by default, --logical flag
- **DI writes:** `set DI-6 high` writes raw electrical level 1. `set DI-6 high --logical` means "asserted/active" — harness consults `active_low` from gpio_config and inverts if needed.
- **DO reads:** `get DO-5` returns raw `0` or `1`. `get DO-5 --logical` returns `active`/`inactive` after applying `active_low` inversion.
- **Rationale:** Raw default prevents silent polarity bugs in tests. `--logical` is opt-in convenience. Same pattern for both DI and DO.

### Output format — labeled default, --raw for scripting
- `get DO-5` prints `DO-5 (PCS_STOP): 1` (labeled, human-readable)
- `get DO-5 --raw` prints `1` (minimal, machine-parseable)
- `get all` prints a table with pin, name, value
- `get all --raw` prints a column of values for shell scripting

### Debounce/bounce simulation — deferred to Phase 8
- Phase 7 delivers clean transitions only
- Contact bounce is signal corruption — falls under SIM-06 fault injection (Phase 8)
- Debounce logic in safety_manager can be unit-tested by mocking time between reads (M1)
- Phase 7 harness architecture should not prevent adding bounce simulation later

### Fault injection — deferred to Phase 8
- Phase 7 delivers normal operation only
- SIM-06 (YAML configurability + fault injection) is explicitly assigned to Phase 8 per roadmap
- Fault modes (stuck pins, bouncing inputs, GPIO chip unresponsive) will be Phase 8 scope

### Runtime model — CLI + importable module
- Python package at `tools/simulators/gpio_harness/` (follows `can_sim/` and `modbus_sim/` pattern)
- Importable: `from tools.simulators.gpio_harness import set_di, get_do` for test integration
- CLI: `uv run python -m tools.simulators.gpio_harness set DI-6 high`
- CLI subcommands: `set`, `get`, `daemon` (gpio-sim mode only)
- CLI flags: `--backend auto|gpio-sim|rtdb`, `--config`, `--logical`, `--raw`
- Graceful shutdown on SIGINT/SIGTERM for daemon mode

### Claude's Discretion
- Exact Python package structure within `tools/simulators/gpio_harness/`
- gpio-sim sysfs interaction details (chip naming, line numbering)
- Whether daemon uses a PID file or other lifecycle management
- Test structure: how many test functions, what fixtures
- Whether `get all` uses a formatted table library or manual formatting
- How config loading is cached (per-invocation for one-shot is fine)

</decisions>

<specifics>
## Specific Ideas

- The harness is a developer tool — optimize for clarity and debuggability over performance
- `gpio-harness get all` should give an at-a-glance view of the full safety I/O state — useful during HMI demos and manual testing
- Named pin support makes test code self-documenting: `set_di("ESTOP_NO", 1)` reads better than `set_di(6, 1)`
- Atomic multi-pin writes are essential for realistic dual-channel E-Stop testing in M1
- The RTDB-only mode means CI never needs kernel module loading — keeps CI simple and fast

</specifics>

<code_context>
## Codebase Integration Points

### Existing files to modify
- `Makefile` — add `sim-gpio` target (or similar)

### New files
- `tools/simulators/gpio_harness/` — Python GPIO harness package (CLI + importable)
- `tests/test_gpio_harness.py` — pytest tests for DI injection, DO readback, pin naming, polarity

### Existing assets consumed
- `config/gpio_config.yaml` — pin names, active_low, debounce_ms, initial_state
- `config/schemas/gpio_config.schema.json` — validation schema
- `config/profiles/*/gpio_config.yaml` — profile-specific pin configs (all identical currently)
- `src/common/python/src/ems_common/rtdb.py` — Python ctypes RTDB bindings
- `src/common/c/include/rtdb.h` — `ems_gpio_t` struct definition (di[8], do_state[8], seqlock)

### Downstream consumers (future phases, not modified now)
- `src/safety_manager/` — M1 will read DI from RTDB (or libgpiod with gpio-sim), write DO
- Phase 8 Integration — harness YAML configurability and fault injection (bounce, stuck pins)
- HMI demos — harness provides controllable safety I/O state for dashboard testing

</code_context>

<deferred>
## Deferred Ideas

- Contact bounce simulation (configurable bounce duration per pin) — Phase 8 SIM-06
- Stuck pin fault injection (pin locked high/low, ignoring writes) — Phase 8 SIM-06
- YAML configurability for harness parameters (default pin states, auto-sequences) — Phase 8 SIM-06
- GPIO chip unresponsive simulation (libgpiod errors) — Phase 8 SIM-06
- safety_manager integration pytest fixtures (launch safety_manager + harness together) — M1
- GPIO event edge detection simulation (rising/falling edge callbacks) — M1 when safety_manager uses events
- Wiring fault scenario library (pre-built test sequences for common failure modes) — future enhancement

</deferred>

---

*Phase: 07-gpio-test-harness*
*Context gathered: 2026-03-13*
