# Phase 17: Integration and Hardening - Research

**Researched:** 2026-03-15
**Domain:** Integration testing — multi-module systemd startup, end-to-end protection/dispatch flows, crash recovery, hot-reload
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Protection Flow Test Methodology:** Scripted test using CAN simulator signal manipulation + alarm_manager + control_manager + PCS Modbus simulator. Verify at each hop in the chain (8 steps with individual timeouts).

| Step | Action | Verification | Timeout |
|------|--------|-------------|---------|
| 1 | Start all M1 modules + control_manager + alarm_manager + simulators | All services active, RTDB valid | 30s |
| 2 | Set control_manager to STANDBY (PCS ON) | RTDB `control_state` == STANDBY, PCS state == RUNNING | 15s |
| 3 | Modify CAN simulator to send cell_voltage_min = 2.7V (below 2.8V threshold) | RTDB `min_cell_v` < 2.8V within 2 seconds | 5s |
| 4 | Wait for alarm delay (5000ms) | alarm_manager publishes `cell_voltage_low` alarm with severity=protection | 7s |
| 5 | Verify control_manager receives protection alarm | RTDB `control_state` transitions to FAULT | 3s |
| 6 | Verify PCS receives shutdown command | PCS simulator registers power=0 and off command | 5s |
| 7 | Reset CAN simulator to normal voltages | RTDB `min_cell_v` > 2.8V | 3s |
| 8 | Wait for cooldown (60s) or send manual fault_reset | RTDB `control_state` transitions to IDLE | 5s |

Key rules:
- Validates full chain: simulator → comm_manager → RTDB → alarm_manager → ZMQ PUB → control_manager → RTDB setpoint → comm_manager → PCS Modbus
- The 5-second alarm delay is part of the test (validates ALM-05 delay timer)
- PCS stop verification uses the Modbus simulator's state machine (already built in M0)
- The 60-second cooldown can be shortened via configurable parameter, or use manual fault_reset

**Dispatch Flow Test Methodology:** Deterministic test with known simulator values and expected PCS register writes.

| Test Scenario | Setup | Expected Setpoint | Verification |
|--------------|-------|------------------|-------------|
| Normal discharge | SOC=50%, grid offline (DI-0=0), NIGHT mode | Discharge at max_discharge_kw (25 kW) | PCS register 0x500E = 250 |
| SOC cutoff | SOC=10% (at discharge_cutoff_pct), NIGHT mode | Zero (stop discharge) | PCS register 0x500E = 0, state → IDLE |
| Temperature derating | SOC=50%, BMS cell temp=45°C, NIGHT mode | Derated: 25 × 0.5 = 12.5 kW | PCS register 0x500E = 125 |
| Manual override | MANUAL mode, operator sets 15 kW | 15 kW | PCS register 0x500E = 150 |
| No source available | Grid offline, SOC at cutoff, no DG | Zero, state → IDLE | PCS register 0x500E = 0 |

Key rules:
- Use residential profile (25 kW max) for deterministic calculations
- CAN simulator provides controlled SOC values (set via signal generator seed)
- GPIO harness controls DI-0 (ACDB feedback) to simulate grid availability
- PCS Modbus simulator's register state is the ground truth for setpoint delivery
- Temperature derating test requires CAN simulator to set cell_temp_max above 40°C

**Hot-Reload Validation:** Modify config files while modules are running, verify behavior changes within 2 seconds.

| Config Change | Module | Expected Behavior Change | Verification |
|--------------|--------|------------------------|-------------|
| Change `discharge_cutoff_pct` from 10% to 20% | control_manager | System stops discharging when SOC drops to 20% | Set SOC=15%, verify state → IDLE |
| Change `cell_voltage_high` threshold from 3.65V to 3.50V | alarm_manager | Alarm activates at 3.50V | Set cell_v=3.55V, verify alarm fires |
| Disable `soc_low` alarm (enabled: false) | alarm_manager | No alarm when SOC drops to 3% | Set SOC=3%, verify no alarm event |
| Change `max_discharge_kw` from 25 to 15 | control_manager | Setpoint clamped to 15 kW | Request 25 kW, verify PCS gets 150 |

Key rules:
- Modify YAML files on disk — config_manager's inotify watcher detects changes
- Wait 1 second for debounce (500ms) + validation + swap
- Verify via RTDB state and ZMQ events within 2 seconds of file save
- Hot-reload does NOT require restarting any module

**Crash Recovery:** Reuse Phase 13 crash recovery patterns. Additions:
- control_manager: always starts in IDLE on restart (never resumes previous state)
- alarm_manager: fires alarms immediately on restart if thresholds currently exceeded (no delay on restart)
- Startup ordering: M1 modules first → control_manager → alarm_manager
- All 8+ services active within 30 seconds
- Recovery criteria: process alive + RTDB section updated + ZMQ flowing, within 10 seconds
- Add control_manager and alarm_manager to the existing CRASH_MATRIX parametrized test

### Claude's Discretion

- Test infrastructure reuse from Phase 13 (conftest.py, ModuleProcess, MetricsCollector)
- CAN simulator signal manipulation for controlled test scenarios
- GPIO harness integration for grid availability simulation
- Makefile target naming (extend `test-integration` or new `test-integration-m2`)
- Test duration and CI considerations

### Deferred Ideas (OUT OF SCOPE)

- Performance profiling under M2 load (control + alarm overhead on top of M1 modules)
- Long-duration soak test (hours, not minutes) — deferred to pre-production (M5)
- Hardware-in-the-loop with ECU-1170-552A — blocked on PLAT-01
- Multi-PCS dispatch flow testing — blocked on Decision #7.3
- Scheduler integration tests — deferred to M3
</user_constraints>

---

## Summary

Phase 17 is an integration and hardening phase: no new modules, no new requirements. The goal is to prove that all Phase 14-16 components (control_manager, alarm_manager) work correctly when running together with the M1 modules (data_manager, config_manager, safety_manager, comm_manager, logger) under realistic simulated conditions.

The testing approach builds directly on the Phase 13 integration test infrastructure already in `tests/integration/`. Four test files exist there today: `test_startup.py`, `test_crash_recovery.py`, `test_e2e_pipeline.py`, and `test_performance.py`. Each has well-established patterns (ModuleProcess subprocess wrapper, wait_for_criteria polling, CRASH_MATRIX parametrization) that Phase 17 extends rather than replaces. The new test file will be `tests/integration/test_m2_integration.py`, which adds control_manager and alarm_manager specs to the startup order and CRASH_MATRIX, then builds four new test scenarios: protection flow, dispatch flow, crash recovery for M2 modules, and hot-reload validation.

The key technical challenge is test orchestration: the protection flow test crosses 5 module boundaries with timing constraints (5-second alarm delay, 60-second cooldown), and the dispatch flow test requires deterministic simulator seeding. The CAN simulator's SignalGenerator already supports tuning parameters; the Modbus simulator's PCSStateMachine already tracks register writes at `0x500E` and `0x0291` — both are directly inspectable for verification. The GPIO harness's RtdbBackend is already used in `test_crash_recovery.py` for DI/DO manipulation.

**Primary recommendation:** Write `tests/integration/test_m2_integration.py` using the existing conftest infrastructure. Add control_manager and alarm_manager to `_MODULE_SPECS` and `STARTUP_ORDER` in the crash recovery test. Extend the Makefile `test-integration` target or add `test-integration-m2`. All test tooling exists; this phase is purely about writing integration scenarios.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | project-installed | Test runner and parametrization | Already used across all M0-M2 tests |
| pytest-timeout | project-installed | Per-test timeout enforcement | Already applied as `pytestmark` in all integration tests |
| zmq (pyzmq) | project-installed | ZMQ REQ/REP/PUB/SUB verification | Same IPC transport as production modules |
| msgpack | project-installed | Payload encode/decode in tests | Same serialization as production |
| psutil | project-installed | RSS tracking in ModuleProcess | Already used in conftest.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio | stdlib | Async sleep for timing-sensitive tests | Not needed — tests use synchronous wait_for_criteria pattern |
| time.monotonic() | stdlib | Elapsed-time assertions | Already standard in conftest wait_for_criteria |
| subprocess.Popen | stdlib | Launching modules via ModuleProcess | Via ModuleProcess wrapper only |
| threading.Event | stdlib | Background polling threads (hot-reload test) | For concurrent file-write + behavior-verify scenarios |
| shutil / pathlib | stdlib | Config file manipulation for hot-reload | Write YAML files under test |
| signal | stdlib | SIGKILL/SIGTERM in crash recovery | Already in CRASH_MATRIX |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| ModuleProcess subprocess wrapper | pytest-subprocess mock | subprocess mock cannot test actual inter-process RTDB sharing — real processes required |
| wait_for_criteria polling | asyncio.wait_for | Synchronous polling is simpler and consistent with existing test style |
| CAN simulator signal seeding | Direct RTDB write | RTDB direct write would bypass comm_manager, not testing the full chain |

**Installation:** No new dependencies — all required libraries are already in the project workspace.

---

## Architecture Patterns

### Recommended Test File Structure
```
tests/integration/
├── conftest.py                  # Existing: ModuleProcess, MetricsCollector, helpers
├── test_startup.py              # Existing: M1 startup sequence (extend to add M2 modules)
├── test_crash_recovery.py       # Existing: CRASH_MATRIX (extend to add M2 modules)
├── test_e2e_pipeline.py         # Existing: M1 data pipeline
├── test_performance.py          # Existing: M1 performance thresholds
└── test_m2_integration.py       # NEW: protection flow, dispatch flow, hot-reload
```

### Pattern 1: M2 Module Specs (Extend STARTUP_ORDER)

Add to `_MODULE_SPECS` in `test_crash_recovery.py` and `STARTUP_ORDER` in both files:

```python
# Source: tests/integration/test_crash_recovery.py (existing pattern)
"control_manager": {
    "cmd": lambda: [
        "uv", "run", "python", "-m", "ems_control_manager",
        "--config", str(CONFIG_DIR / "profiles" / "residential" / "control_config.yaml"),
    ],
    "ready_check": lambda: True,  # delay_ready(2.0) in test_startup.py style
    "requires_c": False,
    "requires_vcan": False,
},
"alarm_manager": {
    "cmd": lambda: [
        "uv", "run", "python", "-m", "ems_alarm_manager",
        "--config", str(CONFIG_DIR / "profiles" / "residential" / "alarms_config.yaml"),
    ],
    "ready_check": lambda: True,
    "requires_c": False,
    "requires_vcan": False,
},
```

Updated `STARTUP_ORDER` (after logger):
```python
STARTUP_ORDER: list[str] = [
    "data_manager_c",
    "data_manager_python",
    "config_manager",
    "safety_manager",
    "comm_manager_c",
    "comm_manager_python",
    "logger",
    "control_manager",    # After all M1 modules
    "alarm_manager",      # After control_manager (needs control_cmd socket)
]
```

### Pattern 2: Protection Flow Test Structure

```python
# Source: tests/integration/test_m2_integration.py (new)
class TestProtectionFlow:
    """End-to-end: BMS cell voltage low → alarm fires → control transitions to FAULT → PCS stops."""

    @pytest.fixture(scope="class")
    def m2_system(self) -> Generator[dict[str, Any], None, None]:
        """Launch all M1 + M2 modules + simulators. Class-scoped for shared system."""
        # ... ModuleProcess for each module, Popen for CAN sim + Modbus sim
        # ... yield {"modules": ..., "can_sim": ..., "modbus_sim": ..., "config_dir": ...}
        pass

    def test_step1_startup_healthy(self, m2_system: dict[str, Any]) -> None:
        """All 8+ services active, RTDB valid within 30s."""
        checks = {
            "rtdb_exists": check_rtdb_exists,
            "control_manager_alive": lambda: m2_system["modules"]["control_manager"].is_alive,
            "alarm_manager_alive": lambda: m2_system["modules"]["alarm_manager"].is_alive,
        }
        result = wait_for_criteria(checks, timeout=30.0)
        assert all(result.values())

    def test_step2_to_step6_protection_chain(self, m2_system: dict[str, Any]) -> None:
        """Drive cell voltage below threshold, verify FAULT state and PCS stop."""
        # Step 2: command control_manager to STANDBY via ZMQ REQ
        # Step 3: tune CAN sim to emit cell_voltage_min = 2.7V
        # Step 4: wait 7s (5s alarm delay + 2s margin), check alarm event on PUSH/PUB
        # Step 5: check RTDB control_state == FAULT within 3s
        # Step 6: check PCS simulator 0x500E == 0 and 0x0291 == 0 within 5s
        pass
```

### Pattern 3: wait_for_criteria with RTDB Read Check

```python
# Source: tests/integration/conftest.py (existing pattern)
def check_control_state(target_state: int) -> Callable[[], bool]:
    """Return check callable that reads RTDB control_state."""
    def _check() -> bool:
        try:
            shm, rtdb = attach_rtdb()
            state: int = rtdb.system.control_state
            detach_rtdb(shm)
            return state == target_state
        except Exception:
            return False
    return _check

# Usage:
state = wait_for_criteria(
    {"fault_state": check_control_state(STATE_FAULT)},
    timeout=3.0,
)
assert state["fault_state"], "control_manager did not transition to FAULT within 3s"
```

### Pattern 4: Hot-Reload Test — File Modification + Behavior Verify

```python
# Source: tests/integration/test_m2_integration.py (new)
import shutil
import yaml

def _modify_yaml_key(path: Path, *keys: str, value: Any) -> None:
    """Read YAML, set nested key, write back atomically."""
    with open(path) as f:
        cfg = yaml.safe_load(f)
    obj = cfg
    for k in keys[:-1]:
        obj = obj[k]
    obj[keys[-1]] = value
    # Atomic write: write to .tmp then rename (avoids partial-read by inotify)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        yaml.dump(cfg, f)
    tmp.rename(path)

# In test:
_modify_yaml_key(
    config_dir / "alarms_config.yaml",
    "rules", "cell_voltage_high", "high_threshold",
    value=3.50,
)
time.sleep(1.5)  # debounce (500ms) + validate + swap + 1 tick
# Then verify behavior changed...
```

### Pattern 5: CAN Simulator Signal Injection

The CAN simulator's `SignalGenerator` uses tuning parameters. To inject specific values in tests, the sim must be launched with a tuning config or the sim process must be replaced by direct RTDB writes for test isolation. However, per locked decision, the test MUST go through the full chain (simulator → comm_manager → RTDB). The approach:

```python
# Option A: Launch CAN sim with fixed seed and low-voltage tuning config
# The CAN sim supports --config which points at bms_config.yaml
# bms_config.yaml has a fault_injection section for per-signal overrides

# Option B: Write a test-specific bms_config.yaml with tuned voltage values
# base_voltage: 2.65 (below 2.8V threshold)

# Recommended: option B — write a test fixture config file in tmpdir
```

The SignalGenerator `base_voltage` + `drift_amplitude` parameters control voltage output. Setting `base_voltage=2.65` and `drift_amplitude=0.0` produces constant ~2.65V (below the 2.8V protection threshold). This is the correct seeding approach for the protection flow test.

For temperature derating (45°C+), set the `base_voltage` analog for temperature: the `cell_temperature()` method uses `base: float = 32.0 + self.rack_offset * 50`. With rack_index=0 cluster_index=0, base=32°C. To reach 45°C, the drift component (`5.0 * sin(...)`) needs to be near +13°C, which is not reliably controllable via seeding. Instead, write a fixture CAN config with higher base temperature (e.g., 48°C base) so it stays above 45°C throughout the test.

### Anti-Patterns to Avoid

- **Sharing module state across test classes:** Use class-scoped fixtures (`scope="class"`) for module processes. Module teardown must happen in fixture finally block, not in test methods.
- **Hardcoding ipc:// paths in tests:** Integration tests use `tcp://127.0.0.1:{random_port}` per Phase 13 key decision. Pass ZMQ endpoints via environment variables to module processes.
- **Fixed sleep instead of wait_for_criteria:** Use `wait_for_criteria` for all asynchronous assertions. Fixed sleeps make tests fragile and slow.
- **Not restoring config files after hot-reload tests:** Save original content before modification and restore in fixture teardown (even on failure).
- **Testing against production /run/ems/ sockets:** Integration tests must use TCP endpoints to avoid /run/ems dependency — already established in Phase 13.
- **Skipping cleanup on test failure:** Module processes must be cleaned up in fixture `finally` blocks, not in test teardown hooks that may not run on failure.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Subprocess lifecycle | Custom Popen wrapper | `ModuleProcess` in conftest.py | Already handles start, ready_check, kill, restart, RSS, cleanup |
| Async polling with timeout | asyncio.wait_for or threading.Timer | `wait_for_criteria()` in conftest.py | Already handles multi-criteria, timeout, exception safety |
| RTDB attachment in tests | Direct ctypes mmap | `attach_rtdb() / detach_rtdb()` from ems_common.rtdb | Handles magic/version validation, detach safety |
| ZMQ REQ/REP in tests | Raw zmq.socket | Follow existing test pattern from test_e2e_pipeline.py | Handles RCVTIMEO, SNDTIMEO, socket cleanup, context term |
| CAN signal injection | Custom CAN frame writer | CAN simulator with fixture config (base_voltage tuning) | Preserves full chain: simulator → comm_manager → RTDB |
| PCS register inspection | Custom Modbus client | PCS simulator's `state_machine.py` register tracking | `PCSStateMachine._registers` dict is already queryable |
| Config file manipulation | In-place sed/regex | yaml.safe_load + yaml.dump + atomic rename | YAML round-trip preserves structure; atomic write avoids inotify partial reads |

**Key insight:** All integration test tooling for EMS was built in Phase 13. Phase 17 is about composing new test scenarios from existing primitives, not building new infrastructure.

---

## Common Pitfalls

### Pitfall 1: Alarm Delay Timer in Protection Flow Test
**What goes wrong:** Test checks for FAULT state 2 seconds after injecting low voltage — fails because alarm_manager's 5-second delay hasn't fired yet.
**Why it happens:** The `delay_ms=5000` in `alarms_config.yaml` is per-rule default. The `cell_voltage_low` rule uses the default.
**How to avoid:** After injecting low voltage (step 3), wait at least 7 seconds before asserting FAULT (5s delay + 2s margin for ZMQ delivery and control_manager tick). The CONTEXT.md step-by-step already documents the correct 7-second timeout for step 4.
**Warning signs:** Test timeout at step 4 check (alarm not published) rather than step 5 (FAULT not set).

### Pitfall 2: Hot-Reload Config Atomicity
**What goes wrong:** inotify fires on partial file write, config_manager reads truncated YAML, validation fails, reload silently rejected.
**Why it happens:** Python's `open(path, 'w')` truncates before writing. If inotify fires between truncation and completion, config_manager reads empty/partial file.
**How to avoid:** Write to a `.tmp` sibling file, then `os.rename()` into place (atomic on Linux same-filesystem). The rename triggers a single inotify `IN_MOVED_TO` event on the final file.
**Warning signs:** Hot-reload test passes sometimes, fails sometimes (race condition signature).

### Pitfall 3: ZMQ REP Socket Port Conflict
**What goes wrong:** Second test in the suite fails to bind because previous test's module process still holds the SOCK_CONTROL_CMD or SOCK_ALARM_CMD socket.
**Why it happens:** Module process cleanup takes time. If the test fixture scope is too broad, sockets overlap.
**How to avoid:** Pass unique TCP ports via environment variables to each test fixture. Use random port selection (or a fixed port range per test class). Pattern already established in Phase 13: `tcp://127.0.0.1:{random_port}`. Add `EMS_CONTROL_CMD_ENDPOINT` and `EMS_ALARM_CMD_ENDPOINT` env vars to module launch commands.
**Warning signs:** `zmq.error.ZMQError: Address already in use` in test output.

### Pitfall 4: control_manager RTDB Field Path
**What goes wrong:** Test reads `rtdb.system.control_state` but the field is elsewhere or named differently.
**Why it happens:** RTDB layout is defined in C struct `ems_rtdb_t`. The Python ctypes wrapper must match exactly.
**How to avoid:** Verify the field name by reading `ems_common/rtdb.py` before writing test assertions. Based on Phase 14 decisions, the field is `rtdb.system.control_state` (written by ControlLoop after each tick).
**Warning signs:** `AttributeError: 'EmsSystem' object has no attribute 'control_state'` at test runtime.

### Pitfall 5: Fault Reset Timing in Protection Flow
**What goes wrong:** Protection flow test step 8 waits for `IDLE` state but control_manager has a 60-second cooldown timer before accepting fault_reset or auto-recovering.
**Why it happens:** Phase 16 alarm cooldown: 60 seconds after protection or action severity before `_last_alarm_severity` resets. In production, this prevents rapid re-dispatch after a fault.
**How to avoid:** In test, send an explicit `fault_reset` command via ZMQ REQ on `control_cmd` socket. The command bypasses the cooldown timer and transitions to IDLE immediately. Alternatively, make the cooldown duration configurable in `control_config.yaml` and set it to 5s for test fixture configs. Per CONTEXT.md, both approaches are valid.
**Warning signs:** Step 8 timeout (state stays FAULT for >10s after voltage restored).

### Pitfall 6: CAN Simulator Not on vcan0
**What goes wrong:** Protection flow test injects low-voltage values, but comm_manager_c is not running (vcan0 unavailable), so RTDB `min_cell_v` never changes.
**Why it happens:** CI machines may not have vcan0. The existing tests already handle this with `pytest.skip("vcan0 not available")`.
**How to avoid:** Check vcan0 availability at fixture setup. If unavailable, skip the protection flow test with a clear message. For the protection flow test specifically, consider an alternative: direct RTDB write for CI environments (but flag this as bypassing the full chain). The locked decision requires the full chain — so the correct action is to skip.
**Warning signs:** Step 3 verification fails (RTDB `min_cell_v` stays at normal levels).

### Pitfall 7: Module Startup Order Race
**What goes wrong:** alarm_manager starts before control_manager's ZMQ control_cmd socket is bound, causing alarm_manager's ZMQ REQ connect to fail silently on first protection dispatch attempt.
**Why it happens:** alarm_manager connects (not binds) to control_cmd. ZMQ connects are non-blocking and don't fail if the peer isn't up yet — but the first REQ will succeed because ZMQ queues. However, if control_manager hasn't started the REP socket yet, the first REQ may time out.
**How to avoid:** Keep the startup order from CONTEXT.md: control_manager starts before alarm_manager. Add a `_delay_ready(2.0)` ready_check to control_manager spec (matches the pattern in test_startup.py) so alarm_manager only starts after control_manager has had 2 seconds to bind its REP socket.
**Warning signs:** First protection dispatch fails, subsequent ones succeed (retry succeeds).

---

## Code Examples

Verified patterns from existing codebase:

### Inspecting PCS Simulator Register for Setpoint Verification
```python
# Source: tools/simulators/modbus_sim/state_machine.py
# PCSStateMachine tracks register writes internally.
# In tests, we need to reach into the running sim process.
# The sim process's register map is in tools/simulators/modbus_sim/register_map.py
# For integration tests, connect a Modbus client to the sim and read register 0x500E directly.

import pymodbus.client
client = pymodbus.client.ModbusTcpClient("127.0.0.1", port=502)
client.connect()
result = client.read_holding_registers(0x500E, count=1, slave=1)
setpoint_raw: int = result.registers[0]  # 250 = 25.0 kW (× 10 encoding)
```

### Reading RTDB control_state in Tests
```python
# Source: tests/integration/conftest.py (attach_rtdb/detach_rtdb pattern)
from ems_common.rtdb import attach_rtdb, detach_rtdb

def read_control_state() -> int:
    """Read current control_state from RTDB."""
    shm, rtdb = attach_rtdb()
    try:
        return int(rtdb.system.control_state)
    finally:
        del rtdb
        detach_rtdb(shm)
```

### Sending ZMQ Command to control_manager
```python
# Source: tests/integration/test_e2e_pipeline.py (ZMQ REQ/REP pattern)
# Source: ems_common/ipc.py (encode_command_request / decode_command_response)
from ems_common.ipc import encode_command_request, decode_command_response
import zmq

def send_control_command(endpoint: str, cmd: str, params: dict) -> dict:
    """Send a command to control_manager REP socket and return response."""
    ctx = zmq.Context()
    req = ctx.socket(zmq.REQ)
    req.setsockopt(zmq.RCVTIMEO, 3000)
    req.setsockopt(zmq.SNDTIMEO, 1000)
    req.connect(endpoint)
    try:
        req.send(encode_command_request(cmd, params))
        return decode_command_response(req.recv())
    finally:
        req.close()
        ctx.term()

# Example: request mode_change to STANDBY
resp = send_control_command("tcp://127.0.0.1:55100", "mode_change", {"target": "STANDBY"})
assert resp["status"] == "ok"
```

### Launching CAN Simulator with Custom Signal Tuning
```python
# Source: tools/simulators/can_sim/signals.py (SignalGenerator tuning)
# Approach: write a fixture bms_config.yaml with fault_injection settings
import yaml, tempfile, shutil

def _write_low_voltage_bms_config(src_config: Path, dest: Path) -> None:
    """Write a bms_config.yaml with base_voltage set to 2.65V (below 2.8V threshold)."""
    with open(src_config) as f:
        cfg = yaml.safe_load(f)
    # fault_injection section controls per-signal overrides in CANSimulator
    cfg.setdefault("fault_injection", {})
    cfg["fault_injection"]["cell_voltage_base"] = 2.65
    cfg["fault_injection"]["cell_voltage_drift_amplitude"] = 0.0
    with open(dest, "w") as f:
        yaml.dump(cfg, f)
```

### Atomic YAML Config Modification for Hot-Reload
```python
# Source: derived from config_manager inotify pattern + atomic write best practice
import os, yaml
from pathlib import Path

def modify_config_atomic(path: Path, updates: dict) -> None:
    """Modify a nested YAML config atomically (atomic rename avoids partial inotify read).

    Args:
        path: Path to the YAML file to modify.
        updates: Flat dict of dot-separated key paths to new values.
                 E.g., {"soc_limits.discharge_cutoff_pct": 20}
    """
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key_path, value in updates.items():
        keys = key_path.split(".")
        obj = cfg
        for k in keys[:-1]:
            obj = obj[k]
        obj[keys[-1]] = value
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        yaml.dump(cfg, f)
    os.rename(tmp, path)  # atomic on Linux same-filesystem
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Integration tests with fixed sleep() | wait_for_criteria() polling | Phase 13 | Tests are faster and more reliable |
| Single module crash tests | CRASH_MATRIX parametrize | Phase 13 | All modules covered with one test class |
| Manual startup ordering | build_start_order() factory | Phase 13 | Dependency order enforced in test |
| ipc:// ZMQ in tests | tcp://127.0.0.1 random ports | Phase 13 key decision | Tests work without /run/ems/ existing |

**Deprecated/outdated:**
- Using `scope="function"` for module process fixtures: too slow for integration tests. Use `scope="class"` (modules launched once per test class).
- Importing conftest helpers via sys.path manipulation (as done in test_e2e_pipeline.py): use `from tests.integration.conftest import ...` with proper package imports.

---

## Open Questions

1. **control_manager RTDB field name for control_state**
   - What we know: Phase 14 key decision states "ControlLoop owns all RTDB writes" and "TickResult is the tick output contract". The field is in `rtdb.system`.
   - What's unclear: Exact Python ctypes field name (`control_state` vs `ctrl_state` vs `state`).
   - Recommendation: Read `src/common/rtdb/include/ems_rtdb.h` or `ems_common/rtdb.py` EmsSystem struct definition before writing RTDB assertions in tests.

2. **PCS simulator register inspection API**
   - What we know: `PCSStateMachine` in `state_machine.py` tracks `_power_setpoint_kw` and `_fault_code`. The sim exposes a Modbus server via pymodbus.
   - What's unclear: Whether the test can read register 0x500E via Modbus TCP to the sim, or whether the sim needs to expose an inspection API.
   - Recommendation: Connect a pymodbus TCP client to the simulator in the test fixture. The simulator already runs as a Modbus TCP server (used in test_e2e_pipeline.py). This is the correct approach — read register 0x500E directly.

3. **CAN simulator fault_injection configuration schema**
   - What we know: The `CANSimulator.__init__` reads `bms_cfg.get("fault_injection", {})` but the schema for this section is not yet verified.
   - What's unclear: Which keys are supported in `fault_injection` for overriding per-signal base values.
   - Recommendation: Read `tools/simulators/can_sim/simulator.py` and `rack.py` fully to verify how `_fault_cfg` is consumed by `RackSimulator`. If direct signal override is not supported, the test must launch the CAN sim with a custom bms_config.yaml that has low base_voltage values instead.

4. **Port assignment for M2 module ZMQ sockets in tests**
   - What we know: Phase 13 established tcp://127.0.0.1 random ports for test isolation. control_manager uses `SOCK_CONTROL_CMD` and `SOCK_CONTROL_PUB`; alarm_manager uses `SOCK_ALARM_CMD` and `SOCK_ALARM_PUB`.
   - What's unclear: Whether control_manager and alarm_manager accept ZMQ endpoint env var overrides, or whether they need CLI flags for test endpoints.
   - Recommendation: Check `ems_control_manager/loop.py` and `ems_alarm_manager/loop.py` for env var support. If not present, add `--control-cmd-endpoint` and `--alarm-cmd-endpoint` flags or env var overrides during this phase. The existing modules already accept constructor endpoint overrides (used in unit tests) — the `__main__.py` just needs to pass them through.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (project-installed) |
| Config file | `pyproject.toml` (root workspace) |
| Quick run command | `uv run pytest tests/integration/test_m2_integration.py -v -m integration --timeout=300 -x` |
| Full suite command | `uv run pytest tests/integration/ -v -m integration --timeout=900` |

### Phase Requirements → Test Map

Phase 17 has no new requirements — it validates existing requirements in integration:

| Req ID | Behavior Under Test | Test Type | Automated Command | File Exists? |
|--------|---------------------|-----------|-------------------|-------------|
| CTRL-01 | 1Hz loop reads RTDB and writes setpoint (end-to-end) | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow -x` | ❌ Wave 0 |
| CTRL-02 | State machine transitions: STANDBY → FAULT → IDLE | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow -x` | ❌ Wave 0 |
| CTRL-03 | PCS command dispatch writes 0x500E (full chain) | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow::test_normal_discharge -x` | ❌ Wave 0 |
| CTRL-04 | Source priority NIGHT mode selects BESS | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow::test_night_mode_dispatch -x` | ❌ Wave 0 |
| CTRL-05 | SOC cutoff stops discharge | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow::test_soc_cutoff -x` | ❌ Wave 0 |
| CTRL-06 | Temperature derating reduces setpoint | integration | `uv run pytest tests/integration/test_m2_integration.py::TestDispatchFlow::test_temperature_derating -x` | ❌ Wave 0 |
| CTRL-09 | Interlock check: safety_manager state blocks transitions | integration | `uv run pytest tests/integration/test_m2_integration.py::TestStartupAndInterlocks -x` | ❌ Wave 0 |
| CTRL-10 | ZMQ command API: mode_change, fault_reset | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow -x` | ❌ Wave 0 |
| CTRL-11 | Hot-reload control_config.yaml applies without restart | integration | `uv run pytest tests/integration/test_m2_integration.py::TestHotReload::test_control_config_reload -x` | ❌ Wave 0 |
| CTRL-12 | control.state ZMQ telemetry at 1Hz | integration | `uv run pytest tests/integration/test_m2_integration.py::TestStartupAndInterlocks::test_control_telemetry_flowing -x` | ❌ Wave 0 |
| ALM-01 | Alarm evaluation reads RTDB at 1Hz | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow -x` | ❌ Wave 0 |
| ALM-02 | Protection severity triggers PCS shutdown | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow::test_protection_chain -x` | ❌ Wave 0 |
| ALM-05 | Delay timer (5s) prevents transient activation | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow::test_alarm_delay_respected -x` | ❌ Wave 0 |
| ALM-08 | Protection alarm sends shutdown to control_manager | integration | `uv run pytest tests/integration/test_m2_integration.py::TestProtectionFlow::test_protection_chain -x` | ❌ Wave 0 |
| ALM-09 | Hot-reload alarms_config.yaml applies without restart | integration | `uv run pytest tests/integration/test_m2_integration.py::TestHotReload::test_alarm_config_reload -x` | ❌ Wave 0 |

Crash recovery for M2 modules is validated by extending the existing `test_crash_recovery.py`:

| Req | Behavior | Test Type | Command | File Exists? |
|-----|----------|-----------|---------|-------------|
| SC-4 | control_manager SIGKILL recovery within 10s, starts in IDLE | integration | `uv run pytest tests/integration/test_crash_recovery.py -k "control_manager-SIGKILL" -x` | Extend existing ✅ |
| SC-4 | alarm_manager SIGKILL recovery within 10s, re-fires active alarms | integration | `uv run pytest tests/integration/test_crash_recovery.py -k "alarm_manager-SIGKILL" -x` | Extend existing ✅ |

Startup sequence for M2 modules is validated by extending `test_startup.py`:

| Req | Behavior | Test Type | Command | File Exists? |
|-----|----------|-----------|---------|-------------|
| SC-1 | Full 8+ module startup within 30s | integration | `uv run pytest tests/integration/test_startup.py -x` | Extend existing ✅ |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/integration/test_m2_integration.py -v -m integration --timeout=300 -x`
- **Per wave merge:** `uv run pytest tests/integration/ -v -m integration --timeout=900`
- **Phase gate:** Full integration suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/integration/test_m2_integration.py` — covers all new integration scenarios (protection flow, dispatch flow, hot-reload, M2 startup health)
- [ ] Extend `tests/integration/test_crash_recovery.py` — add `control_manager` and `alarm_manager` to `_MODULE_SPECS`, `STARTUP_ORDER`, and `CRASH_MATRIX`
- [ ] Extend `tests/integration/test_startup.py` — add `control_manager` and `alarm_manager` to `build_start_order()` return value

---

## Sources

### Primary (HIGH confidence)
- `/home/overlord/EMS/tests/integration/conftest.py` — ModuleProcess, MetricsCollector, wait_for_criteria, check_rtdb_exists patterns
- `/home/overlord/EMS/tests/integration/test_crash_recovery.py` — CRASH_MATRIX, _MODULE_SPECS, STARTUP_ORDER, crash recovery patterns
- `/home/overlord/EMS/tests/integration/test_startup.py` — build_start_order, _delay_ready, TestStartupSequence patterns
- `/home/overlord/EMS/tests/integration/test_e2e_pipeline.py` — full pipeline test, ZMQ REQ/REP pattern, simulator launch
- `/home/overlord/EMS/tools/simulators/can_sim/signals.py` — SignalGenerator tuning parameters
- `/home/overlord/EMS/tools/simulators/modbus_sim/state_machine.py` — PCSStateMachine register tracking
- `/home/overlord/EMS/tools/simulators/gpio_harness/rtdb_backend.py` — RtdbBackend DI/DO manipulation
- `/home/overlord/EMS/.planning/phases/17-integration/17-CONTEXT.md` — locked test methodology decisions
- `/home/overlord/EMS/.planning/STATE.md` — Phase 14-16 key decisions (ZMQ endpoint naming, RTDB field decisions)
- `/home/overlord/EMS/config/profiles/residential/control_config.yaml` — discharge_cutoff_pct=10%, max_discharge_kw=25
- `/home/overlord/EMS/config/profiles/residential/alarms_config.yaml` — cell_voltage_low=2.8V, delay_ms=5000

### Secondary (MEDIUM confidence)
- `/home/overlord/EMS/src/alarm_manager/src/ems_alarm_manager/loop.py` — AlarmLoop constructor ZMQ endpoint overrides (confirms test-injectable endpoints exist)
- `/home/overlord/EMS/src/control_manager/python/src/ems_control_manager/__main__.py` — CLI interface (confirms --config flag, no endpoint CLI flags yet)

### Tertiary (LOW confidence — needs verification during implementation)
- CAN simulator `fault_injection` config schema: `simulator.py` reads `bms_cfg.get("fault_injection", {})` but exact keys consumed by RackSimulator are not yet verified
- PCS Modbus simulator TCP port: assumed 502 (default) based on `--transport tcp` flag in test_e2e_pipeline.py

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies, all libraries already in project
- Architecture patterns: HIGH — directly derived from existing test files in the same codebase
- Pitfalls: HIGH — derived from key decisions in STATE.md (cooldown timer, seqlock pattern, ZMQ port conflicts established in Phase 13)
- Open questions: MEDIUM — 4 implementation-time verification items identified (RTDB field name, PCS register inspection port, CAN sim fault injection schema, ZMQ endpoint env vars)

**Research date:** 2026-03-15
**Valid until:** 2026-04-15 (stable infrastructure — no fast-moving dependencies)
