# Phase 10: Safety - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning

<domain>
## Phase Boundary

Independent safety_manager with <100ms GPIO response and hardware watchdog. Covers SAFE-01 through SAFE-11. Pure C process with SCHED_FIFO real-time priority, libgpiod for GPIO, seqlock writes to RTDB, ZMQ for events/logging.

</domain>

<decisions>
## Implementation Decisions

### Safety Response Matrix

Full input-to-output mapping for all DI events:

| Input | Trigger | DO-0 ACDB Trip | DO-1 Extinguisher | DO-2 Warning | DO-4 Fault | DO-5 PCS Stop | DO-6 Siren |
|-------|---------|----------------|-------------------|--------------|------------|---------------|------------|
| E-Stop (DI-6+7) | Dual-channel confirm | YES | - | - | YES | YES | YES |
| Fire (DI-3+4) | Dual-confirm (both) | YES | YES | - | YES | YES | YES |
| Flood (DI-1) | Single sensor | YES | - | - | YES | YES | YES |
| ACDB Feedback (DI-0) | Loss-of-signal | - | - | - | YES | YES | YES |
| Door Open (DI-2) | Single sensor | - | - | YES | - | - | - |
| Spare (DI-5) | Monitor only | - | - | - | - | - | - |

Key rules:
- E-Stop dual-channel: DI-6 (NO) + DI-7 (NC) must both confirm before triggering; single-channel discrepancy = wiring fault (log CRITICAL, do NOT trigger E-Stop response)
- Fire dual-confirm: both DI-3 (Smoke) AND DI-4 (Heat) must be active before extinguisher fires; single sensor alone triggers Warning Lamp + log only
- Flood de-energizes AC path (ACDB trip) and stops PCS — matches IEC 62485-2 for stationary batteries
- ACDB feedback loss: PCS stop + fault indication, but do NOT re-trip ACDB (it's already open)
- Door open: warning lamp only — no shutdown, technicians need live access for maintenance
- Spare DI-5: read and publish to RTDB/ZMQ, trigger nothing — reserved for site-specific future use
- Spare DO-7: never asserted by safety_manager — reserved for site-specific future use

### Failure Mode Behavior

| Failure | Response | Recovery |
|---------|----------|----------|
| Single DI read error | Assume that input's worst case, trigger its response matrix row | Auto-recover when line reads successfully again |
| GPIO chip open failure | Full emergency — assert ALL safety outputs | Auto-recover when chip reopens |
| Single DO write error | Log CRITICAL, retry next scan cycle, don't block other outputs | Auto-recover on successful write |
| Multiple DI errors | Each triggers independently per response matrix | Each recovers independently |

Key rules:
- "Fail-safe per channel" principle (IEC 61508): each input fails independently into its own safe state
- GPIO chip failure (all lines lost): assert ALL safety outputs — complete loss of sensing = assume worst case for everything
- Keep kicking watchdog during GPIO chip failure — process is alive and making conscious safety decisions, uncontrolled reboot would be worse
- DO write failures: never block other outputs, always retry — outputs are independent physical circuits
- GPIO failures are self-recovering (not latching) — transient errors should not require a site visit to clear
- Latching fault acknowledgement belongs in alarm_manager (M2), not safety_manager

### Safety State Recovery

| Event | Recovery Mode | Reset Mechanism | Pre-condition for Reset |
|-------|--------------|-----------------|------------------------|
| E-Stop | Manual latch | ZMQ `safety_reset` cmd | DI-6 + DI-7 both normal |
| Fire | Manual latch | ZMQ `safety_reset` cmd | DI-3 + DI-4 both inactive |
| Flood | Manual latch | ZMQ `safety_reset` cmd | DI-1 inactive |
| ACDB Feedback loss | Auto-recover | — | DI-0 returns active |
| Door Open | Auto-recover | — | DI-2 closes |
| GPIO failure | Auto-recover | — | Line reads successfully |

Key rules:
- All events that trigger ACDB trip (E-Stop, Fire, Flood) require manual reset — IEC 60204-1 mandates deliberate restart after emergency stop
- Manual reset via ZMQ REQ/REP on existing `control_cmd` socket: `{cmd: "safety_reset", source: "hmi"|"operator"}`
- Reset command is validated: safety_manager checks that all triggering inputs have actually cleared before accepting; if inputs still active, reject with error response
- Auto-recover events (Door, ACDB feedback, GPIO failure) clear automatically when the condition resolves
- PCS restart after fault clearance is NOT safety_manager's responsibility — control_manager (M2) handles "is it safe to restart"
- Physical reset button support: future DI can be wired and treated as equivalent to ZMQ reset command

### Indicator Lamp Behavior

| Lamp | Output | State | ON When | OFF When |
|------|--------|-------|---------|----------|
| Running (DO-3) | Solid green | Normal | No faults active, system healthy | Any fault condition active |
| Warning (DO-2) | Solid amber | Advisory | Door open, spare DI edge, sensor recovery | Condition clears (auto-recover) |
| Fault (DO-4) | Solid red | Fault | Any protective output asserted | Manual reset accepted |
| Siren (DO-6) | On | Alert | E-Stop, Fire, Flood, ACDB loss | Manual reset or auto-recover |

Key rules:
- "Dark panel" philosophy: normal operation = solid green Running lamp only, all others OFF
- On/off only for Phase 10 — no blink/pulse patterns (avoid complexity in <100ms safety scan loop)
- Blink patterns deferred to M2 via separate lamp-driver thread if operators request during field testing
- Running and Fault are mutually exclusive: `DO-3 = !any_fault_active` (IEC 60073 — green + red simultaneously is prohibited)
- Warning and Fault CAN coexist (e.g., door open during a fire event)
- Fault lamp follows the "any protective output" rule: if DO-0, DO-1, DO-5, or DO-6 is asserted, Fault lamp is ON

### Claude's Discretion

- Main loop architecture (single thread with scan cycle vs multi-thread with dedicated GPIO/watchdog threads)
- libgpiod API usage (edge events vs polling, line request grouping)
- ZMQ message formatting for safety events (within existing envelope contract)
- SCHED_FIFO priority levels for main thread vs watchdog feed thread
- mlockall placement and stack pre-fault strategy
- Startup self-test sequence before entering main loop
- Internal state tracking data structures

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/safety_manager/src/main.c` — Stub executable, CMakeLists.txt links `ems_common_c`
- `src/common/c/include/rtdb.h` — `ems_gpio_t` struct: seqlock + last_update_ms + di[8] + do_state[8]
- `src/common/c/include/seqlock.h` — Lock-free seqlock with acquire/release semantics
- `src/common/c/include/ipc_defs.h` — ZMQ socket paths, topic constants (`EMS_TOPIC_GPIO`), message envelope keys
- `src/common/c/include/ems_types.h` — Severity enum, control state enum
- `src/common/python/src/ems_common/rtdb.py` — Python ctypes mirror of GPIO struct
- `config/gpio_config.yaml` — Full pin mapping (8 DI + 8 DO), active_low, debounce_ms
- `config/schemas/gpio_config.schema.json` — JSON Schema with fault_injection section
- `config/profiles/*/gpio_config.yaml` — Per-deployment profile overrides
- `tools/simulators/gpio_harness/` — Dual-backend harness (RTDB shm + gpio-sim kernel module)
- `deploy/systemd/safety_manager.service` — Service file with RT scheduling comments
- `tests/test_gpio_harness.py` — 20+ tests covering E-Stop, fire, fault injection, seqlock

### Established Patterns
- C executables named `{module}_c` for hybrid modules (but safety_manager is pure C — just `safety_manager`)
- mpack v1.1.1 vendored as amalgamation for C-side MessagePack
- Length-prefixed framing (4-byte BE uint32) for C ZMQ interop
- MessagePack envelope: `{ts, seq, src, topic, payload}` for telemetry
- Single-writer-per-section enforced by convention for RTDB seqlock
- GPIO harness `set_di_multi()` for atomic multi-pin operations in tests

### Integration Points
- RTDB must exist before safety_manager starts — systemd `After=ems-data-manager.service`
- Safety events publish on `ipc:///run/ems/telemetry.sock` (PUB) with topic `gpio`
- Safety events push to `ipc:///run/ems/logger.sock` (PUSH) for persistence
- Reset commands arrive on `ipc:///run/ems/control_cmd.sock` (REQ/REP)
- GPIO harness provides test stimulus — no hardware needed for development
- Phase 9 (Foundation) must be complete: config_manager serves gpio_config, data_manager owns RTDB lifecycle

</code_context>

<specifics>
## Specific Ideas

- Safety_manager is the most critical module in the system — correctness over cleverness
- Target scan cycle well under 100ms (measured by GPIO harness timing in tests)
- Watchdog feed thread at higher SCHED_FIFO priority than scan thread to prevent starvation (SAFE-07)
- Config is read once at startup from gpio_config.yaml (loaded by config_manager) — no hot-reload for safety config
- Safety_manager must work even if config_manager, logger, and all other modules are dead — independence is absolute (SAFE-10)

</specifics>

<deferred>
## Deferred Ideas

- **SAFE-12**: Safety state machine with explicit transitions (NORMAL→ESTOP→RECOVERY→NORMAL) and audit trail — future requirement, noted in REQUIREMENTS.md
- **SAFE-13**: Channel discrepancy detection for E-Stop wiring fault — noted as future, but basic single-channel detection is in scope (log CRITICAL on DI-6/DI-7 mismatch)
- **SAFE-14**: GPIO debounce with configurable per-DI timing — debounce_ms exists in config schema, but implementation deferred (fire/E-Stop use debounce_ms=0)
- **SAFE-15**: Safety event black-box ring buffer in shm — deferred to future milestone
- Blink/pulse patterns for indicator lamps — deferred to M2 lamp-driver thread
- Physical reset button via DI — deferred until hardware is specified

</deferred>

---

*Phase: 10-safety*
*Context gathered: 2026-03-13*
