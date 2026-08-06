# Phase 8: Integration Validation - Research

**Researched:** 2026-03-13
**Domain:** Simulator integration, fault injection, CI smoke tests, shell orchestration
**Confidence:** HIGH

## Summary

Phase 8 ties together the three simulators (CAN, Modbus, GPIO) built in phases 5-7, adding YAML-configurable fault injection, signal tuning, a unified launcher script, and CI integration tests. The work is primarily integration glue -- extending existing config schemas, adding fault injection logic to existing simulator classes, writing a shell orchestration script, and creating a pytest integration test.

All three simulators share identical patterns: CLI via `__main__.py`, config loaded from YAML, `uv run python -m tools.simulators.{name}` entry point, existing Makefile targets. The fault injection design is config-driven and startup-only (no runtime injection), which keeps complexity low. JSON schemas use `additionalProperties: false` at every level, so every new config section must be added to schemas before config validation passes.

**Primary recommendation:** Extend each simulator's existing classes to read optional `fault_injection` and `signal_tuning` config sections, modify the send/response paths to apply faults, extend JSON schemas with matching optional properties, create `tools/sim-all.sh` as the orchestrator, and add `tests/test_integration.py` with `@pytest.mark.integration`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Faults defined as `fault_injection:` section in each simulator's existing config (bms_config.yaml, pcs_config.yaml, gpio_config.yaml)
- Optional section -- absent means no faults (backward compatible with existing configs)
- Faults active at startup from config -- no runtime injection protocol, no IPC needed
- CAN fault modes: `frame_drop_rate`, `corrupt_data`, `stale_timeout_ms`
- Modbus fault modes: `response_timeout`, `exception_code`
- GPIO fault modes: `stuck_pins`, `bounce_ms`
- sim-all orchestration via `tools/sim-all.sh` with process management (PID tracking, SIGINT trap)
- Parallel start -- all 3 launch simultaneously
- Health check polling: CAN checks vcan0, Modbus checks PTY/TCP port, GPIO checks shm. 5s timeout.
- Per-simulator log files in `logs/` directory
- `--profile` flag defaulting to residential
- CI test: `tests/test_integration.py` with `@pytest.mark.integration` marker
- CI job: `integration-test` in `pr-check.yml` with `needs: build-and-test`
- Start all 3 sims, exercise one operation each, verify output, tear down (~30s total)
- `signal_tuning:` optional section for noise/drift parameters
- Profiles stay clean -- no fault injection in profile configs
- Extend existing JSON schemas with fault_injection + signal_tuning

### Claude's Discretion
- Exact fault injection config field names and value ranges
- Health check implementation details (polling mechanism, retry logic)
- sim-all.sh internal structure (functions vs linear script)
- Integration test fixture design (how sims are started/stopped in pytest)
- Log file rotation or size limits for sim logs
- Whether signal_tuning values have min/max validation in schemas

### Deferred Ideas (OUT OF SCOPE)
- Performance testing with 64 racks (128 BMUs) at full cell count
- Runtime fault injection via CLI commands to running simulators
- Timed fault scenario sequences (scripted test playbooks)
- Docker-based simulator environment for CI
- Wiring fault scenario library (pre-built test sequences)
- CAN bus-off simulation, error frames, arbitration loss
- Modbus register corruption (random values on read)
- GPIO chip unresponsive simulation
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SIM-06 | Simulators are configurable via YAML (fault injection, timing, multi-rack scaling) | All three simulators already read YAML config; fault_injection and signal_tuning are optional new sections. JSON schemas must be extended. sim-all.sh provides unified launch with `--profile` flag for topology/timing selection. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | (existing) | Integration test framework | Already used for all simulator tests |
| PyYAML | (existing) | Config loading in simulators | Already used in all three simulators |
| python-can | (existing) | CAN bus interaction in tests | Already used in CAN sim and tests |
| pymodbus | 3.12+ | Modbus client for integration tests | Already used in Modbus sim |
| jsonschema | (existing) | Config validation | Already used via validate_config.py |
| bash | system | sim-all.sh orchestration | Standard POSIX shell, no dependencies |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| subprocess | stdlib | Launch simulators from pytest fixtures | Integration test sim lifecycle |
| asyncio | stdlib | Async Modbus client in integration test | Already pattern in modbus_sim tests |
| random | stdlib | frame_drop_rate probability | CAN fault injection |
| time | stdlib | stale_timeout tracking | CAN comm loss simulation |

### Alternatives Considered
None -- all tools are already in the project. No new dependencies required.

## Architecture Patterns

### Recommended Project Structure
```
tools/
  sim-all.sh                    # NEW: unified launcher
  simulators/
    can_sim/
      simulator.py              # MODIFY: read fault_injection config
      rack.py                   # MODIFY: apply frame_drop, corrupt_data, stale_timeout
      signals.py                # MODIFY: apply signal_tuning params
    modbus_sim/
      simulator.py              # MODIFY: read fault_injection config
      register_map.py           # MODIFY: apply exception_code on specific registers
    gpio_harness/
      rtdb_backend.py           # MODIFY: apply stuck_pins, bounce_ms
      config.py                 # No changes needed
config/
  schemas/
    bms_config.schema.json      # EXTEND: fault_injection + signal_tuning
    pcs_config.schema.json      # EXTEND: fault_injection + signal_tuning
    gpio_config.schema.json     # EXTEND: fault_injection
tests/
  test_integration.py           # NEW: integration smoke tests
.github/workflows/
  pr-check.yml                  # EXTEND: add integration-test job
Makefile                        # EXTEND: add sim-all target
logs/                           # NEW: created by sim-all.sh (gitignored)
```

### Pattern 1: Config-Driven Fault Injection
**What:** Each simulator reads an optional `fault_injection:` section from its YAML config at startup. If absent, behavior is unchanged (backward compatible). Fault parameters are stored as instance attributes and checked in hot paths.
**When to use:** All three simulators.
**Example:**
```python
# In CANSimulator.__init__ or RackSimulator.__init__:
fault_cfg: dict = bms_cfg.get("fault_injection", {})
self.frame_drop_rate: float = fault_cfg.get("frame_drop_rate", 0.0)
self.corrupt_data: bool = fault_cfg.get("corrupt_data", False)
self.stale_timeout_ms: int = fault_cfg.get("stale_timeout_ms", 0)

# In RackSimulator._send_frame():
if self.frame_drop_rate > 0 and random.random() < self.frame_drop_rate:
    log.debug("FAULT: dropping frame %s for rack %d", msg_name, self.rack_index)
    return  # silently drop
```

### Pattern 2: Signal Tuning via Config
**What:** The `signal_tuning:` section lets developers override default signal generation parameters (noise sigma, drift amplitude, base voltage) without code changes.
**When to use:** CAN and Modbus simulators for adjusting realistic signal ranges.
**Example:**
```python
# In SignalGenerator.__init__:
tuning: dict = bms_cfg.get("signal_tuning", {})
self.noise_sigma: float = tuning.get("noise_sigma", 0.005)
self.drift_amplitude: float = tuning.get("drift_amplitude", 0.15)
self.drift_period_s: float = tuning.get("drift_period_s", 60.0)
self.base_voltage: float = tuning.get("base_voltage", 3.35)
```

### Pattern 3: Shell Process Orchestration
**What:** `sim-all.sh` backgrounds all three simulators, stores PIDs in an array, traps SIGINT/SIGTERM for clean shutdown.
**When to use:** Developer launcher and CI.
**Example:**
```bash
#!/usr/bin/env bash
set -euo pipefail
PIDS=()
cleanup() {
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    wait
}
trap cleanup EXIT INT TERM

PROFILE="${1:-residential}"
CONFIG_DIR="config/profiles/$PROFILE"

uv run python -m tools.simulators.can_sim --config "$CONFIG_DIR/bms_config.yaml" \
    > logs/sim-can.log 2>&1 &
PIDS+=($!)
# ... similar for modbus_sim and gpio_harness
```

### Pattern 4: Pytest Subprocess Fixtures for Integration Tests
**What:** pytest fixtures start simulator subprocesses, wait for health checks, yield, then terminate on cleanup.
**When to use:** `tests/test_integration.py`.
**Example:**
```python
@pytest.fixture(scope="module")
def can_sim_process():
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "tools.simulators.can_sim",
         "--config", "config/profiles/residential/bms_config.yaml",
         "--interface", "vcan0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(2)  # allow startup
    yield proc
    proc.terminate()
    proc.wait(timeout=5)
```

### Anti-Patterns to Avoid
- **Fault injection in profile configs:** Profiles (residential/commercial/container) must stay clean. Faults are developer overlays only.
- **Runtime fault injection via IPC:** Deferred. Config-at-startup is the chosen model.
- **Modifying existing test files for integration tests:** Keep integration tests in a separate file with the `integration` marker. Existing unit tests remain fast and isolated.
- **Long-running CI tests:** Integration smoke test must stay under 30s total. One operation per simulator, not full coverage.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process management in shell | Custom daemon framework | bash trap + background PIDs | POSIX standard, well-understood, no dependencies |
| Virtual CAN setup | Custom kernel module loading | `ip link add vcan0 type vcan` | Already used in sim-can Makefile target |
| Config validation | Custom validation code | JSON Schema + validate_config.py | Already enforced in CI pipeline |
| Modbus exception responses | Custom protocol handler | pymodbus ExceptionResponse | Built into pymodbus server framework |

## Common Pitfalls

### Pitfall 1: additionalProperties: false blocks new config sections
**What goes wrong:** Adding `fault_injection:` to a config YAML without updating the JSON schema causes validation failure. With `additionalProperties: false` at every level (locked Phase 2 decision), any unknown key is rejected.
**Why it happens:** Schemas are strict by design. Easy to forget schema updates when adding config sections.
**How to avoid:** Update JSON schemas FIRST, then add YAML sections, then run `make validate` to confirm.
**Warning signs:** `validate_config.py` failures or CI red on config validation step.

### Pitfall 2: vcan0 not available in CI
**What goes wrong:** CAN integration tests fail because `vcan` kernel module is not loaded on GitHub Actions runner.
**Why it happens:** Ubuntu 22.04 runners have the module available but not loaded by default.
**How to avoid:** Add `sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set up vcan0` as a CI step before integration tests. This pattern is already used in the `sim-can` Makefile target.
**Warning signs:** `OSError: [Errno 19] No such device` from python-can.

### Pitfall 3: Modbus PTY pair requires socat
**What goes wrong:** RTU mode Modbus sim fails because socat is not installed.
**Why it happens:** PTYPair class uses socat to create virtual serial port pairs.
**How to avoid:** Either install socat in CI (already listed in `setup` target) or use TCP mode for integration tests (avoids socat dependency). TCP mode is simpler for CI.
**Warning signs:** `FileNotFoundError: socat` or PTY creation timeout.

### Pitfall 4: GPIO shared memory conflicts between tests
**What goes wrong:** GPIO integration test interferes with GPIO unit tests if they use the same shm segment name.
**Why it happens:** POSIX shared memory is system-global.
**How to avoid:** Use unique shm names per test (existing pattern in test_gpio_harness.py: `f"ems_rtdb_test_{os.getpid()}_{id(object())}"`). Integration test should use a unique name too.
**Warning signs:** Test failures that only appear when running full test suite.

### Pitfall 5: Race conditions in health check polling
**What goes wrong:** sim-all.sh declares a simulator healthy before it's actually ready to serve requests.
**Why it happens:** Checking that a process is alive is not the same as checking it's ready. CAN sim may be alive but bus not yet initialized.
**How to avoid:** Poll for actual readiness indicators: vcan0 interface up (CAN), port listening (Modbus), shm file exists (GPIO). Retry with backoff up to 5s timeout.
**Warning signs:** Intermittent "connection refused" errors in integration tests.

### Pitfall 6: Simulator processes not cleaned up after test failure
**What goes wrong:** Orphan simulator processes left running after pytest crash or timeout.
**Why it happens:** `subprocess.Popen` without proper cleanup in fixtures.
**How to avoid:** Use try/finally in fixtures, set process group IDs, use `proc.terminate()` then `proc.wait(timeout=5)` then `proc.kill()` as escalation. Consider `atexit` handler as safety net.
**Warning signs:** Port-in-use errors on subsequent test runs.

## Code Examples

### CAN Fault Injection - Frame Drop
```python
# In RackSimulator._send_frame() -- add at top of method
if self.frame_drop_rate > 0 and random.random() < self.frame_drop_rate:
    log.debug("FAULT: dropped frame %s rack %d/%d", msg_name, self.cluster_index, self.rack_index)
    return
```

### CAN Fault Injection - Corrupt Data
```python
# In RackSimulator._send_frame() -- after encoding, before sending
if self.corrupt_data and random.random() < 0.02:  # 2% corruption rate
    data_list = list(data)
    byte_idx = random.randint(0, len(data_list) - 1)
    data_list[byte_idx] = random.randint(0, 255)
    data = bytes(data_list)
    log.debug("FAULT: corrupted byte %d in %s", byte_idx, msg_name)
```

### CAN Fault Injection - Stale Timeout (Comm Loss)
```python
# In RackSimulator -- track stale state
# In __init__:
self._stale_until: float = 0.0
if self.stale_timeout_ms > 0 and self.rack_index == 0:
    # Stale timeout applies to first rack only (simulates dead BMU)
    self._stale_until = time.monotonic() + (self.stale_timeout_ms / 1000.0)

# In run_fast/run_slow, before sending:
if time.monotonic() < self._stale_until:
    return  # suppress all frames for this rack
```

### Modbus Fault Injection - Response Timeout
```python
# In ModbusSimulator -- stop responding for configurable duration
# Read from config:
fault_cfg = self._pcs_config.get("fault_injection", {})
if fault_cfg.get("response_timeout", False):
    # Don't start the server task, or add a delay wrapper
    # Simplest: delay server start by configured seconds
    timeout_duration_s = fault_cfg.get("timeout_duration_s", 10.0)
```

### Modbus Fault Injection - Exception Code
```python
# In CallbackDataBlock.getValues() -- return exception for specific registers
fault_registers = fault_cfg.get("exception_registers", [])
exception_code = fault_cfg.get("exception_code", 0x02)
if address in fault_registers:
    from pymodbus.pdu import ExceptionResponse
    return ExceptionResponse(func_code, exception_code)
```

### GPIO Fault Injection - Stuck Pin
```python
# In RtdbBackend or a fault-injection wrapper:
def set_di(self, pin: int, value: int) -> None:
    if pin in self._stuck_pins:
        return  # ignore writes to stuck pins
    super().set_di(pin, value)
```

### GPIO Fault Injection - Contact Bounce
```python
# In a fault-injection wrapper around set_di:
import threading
def set_di(self, pin: int, value: int) -> None:
    super().set_di(pin, value)
    if self._bounce_ms > 0:
        def bounce():
            end = time.monotonic() + self._bounce_ms / 1000.0
            while time.monotonic() < end:
                super().set_di(pin, 1 - value)
                time.sleep(0.001)
                super().set_di(pin, value)
                time.sleep(0.001)
        threading.Thread(target=bounce, daemon=True).start()
```

### JSON Schema Extension Pattern
```json
{
  "fault_injection": {
    "type": "object",
    "description": "Optional fault injection for testing. Absent = no faults.",
    "additionalProperties": false,
    "properties": {
      "frame_drop_rate": {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "description": "Fraction of frames to silently drop (0.0 = none, 1.0 = all)"
      }
    }
  }
}
```

### sim-all.sh Health Check Pattern
```bash
wait_for_health() {
    local name="$1" check_cmd="$2" timeout=5 elapsed=0
    while ! eval "$check_cmd" 2>/dev/null; do
        sleep 0.5
        elapsed=$((elapsed + 1))
        if [ "$elapsed" -ge "$((timeout * 2))" ]; then
            echo "FAIL: $name health check timed out after ${timeout}s" >&2
            return 1
        fi
    done
    echo "OK: $name ready"
}

# Usage:
wait_for_health "CAN" "ip link show vcan0 | grep -q UP"
wait_for_health "Modbus" "ss -tln | grep -q :5020"  # TCP mode
wait_for_health "GPIO" "ls /dev/shm/ems_rtdb* 2>/dev/null"
```

### Integration Test Fixture Pattern
```python
import subprocess, time, os, signal

@pytest.fixture(scope="module")
def sim_stack():
    """Start all 3 simulators, yield, then tear down."""
    procs = []
    try:
        # CAN sim (requires vcan0 up)
        can_proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "tools.simulators.can_sim",
             "--config", "config/profiles/residential/bms_config.yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        procs.append(can_proc)

        # Modbus sim (TCP mode for CI simplicity)
        modbus_proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "tools.simulators.modbus_sim",
             "--transport", "tcp", "--tcp-port", "5020",
             "--config", "config/profiles/residential/pcs_config.yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        procs.append(modbus_proc)

        time.sleep(3)  # allow startup
        yield {"can": can_proc, "modbus": modbus_proc}
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Runtime fault injection via IPC | Config-at-startup fault injection | Phase 8 decision | Simpler, deterministic, CI-friendly |
| Separate test configs per fault scenario | Optional fault_injection section in existing configs | Phase 8 decision | No config file proliferation |
| Docker-based simulator stack | Native processes via sim-all.sh | Phase 8 decision | Matches production deployment model (systemd, no Docker) |

## Open Questions

1. **Modbus RTU vs TCP in CI integration test**
   - What we know: RTU mode requires socat for PTY pairs. TCP mode works without socat.
   - What's unclear: Whether CI runner has socat pre-installed or if it must be installed as a step.
   - Recommendation: Use TCP mode for CI integration tests (simpler, no socat dependency). RTU mode can be tested separately via the existing `@pytest.mark.rtu` tests.

2. **GPIO harness in integration test -- which backend?**
   - What we know: RTDB backend needs shm segment. gpio-sim backend needs kernel module.
   - What's unclear: Whether gpio-sim module is available on CI runners.
   - Recommendation: Use RTDB backend for integration tests (no kernel module needed, just creates shm). The GPIO harness CLI already supports `--backend rtdb`.

3. **CAN stale_timeout_ms -- which rack goes stale?**
   - What we know: Context says "stop sending for one rack to simulate comm loss".
   - What's unclear: How to select which rack (config field or always rack 0).
   - Recommendation: Default to rack index 0 going stale. Could add `stale_rack_index` config field for flexibility.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_integration.py -v -m integration` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIM-06a | CAN fault_injection config loads and applies | integration | `uv run pytest tests/test_integration.py::test_can_fault_injection -x` | No -- Wave 0 |
| SIM-06b | Modbus fault_injection config loads and applies | integration | `uv run pytest tests/test_integration.py::test_modbus_fault_injection -x` | No -- Wave 0 |
| SIM-06c | GPIO fault_injection config loads and applies | integration | `uv run pytest tests/test_integration.py::test_gpio_fault_injection -x` | No -- Wave 0 |
| SIM-06d | All 3 sims run simultaneously without conflicts | integration | `uv run pytest tests/test_integration.py::test_simultaneous_operation -x` | No -- Wave 0 |
| SIM-06e | signal_tuning config sections work | unit | `uv run pytest tests/test_can_simulator.py::test_signal_tuning -x` | No -- Wave 0 |
| SIM-06f | JSON schemas validate fault_injection sections | unit | `uv run pytest tests/test_config_validation.py -x` | Partially (existing schema tests cover base case) |
| SIM-06g | sim-all.sh launches all simulators | smoke | `bash tools/sim-all.sh --profile residential & sleep 5 && kill %1` | No -- Wave 0 |
| SIM-06h | CI integration-test job passes | CI | `.github/workflows/pr-check.yml` integration-test job | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_integration.py -v -m integration -x`
- **Per wave merge:** `uv run pytest tests/ -v`
- **Phase gate:** Full suite green before verify-work

### Wave 0 Gaps
- [ ] `tests/test_integration.py` -- integration smoke tests (SIM-06a through SIM-06d)
- [ ] `integration` marker in `pyproject.toml` pytest markers list
- [ ] vcan0 setup step in CI for CAN integration test

## Sources

### Primary (HIGH confidence)
- Project codebase: all three simulator implementations read and analyzed
- Existing schemas: `bms_config.schema.json`, `pcs_config.schema.json`, `gpio_config.schema.json` reviewed
- Existing test files: `test_can_simulator.py`, `test_modbus_simulator.py`, `test_gpio_harness.py` patterns studied
- CI pipeline: `.github/workflows/pr-check.yml` structure reviewed
- Makefile: existing sim targets confirmed

### Secondary (MEDIUM confidence)
- pymodbus ExceptionResponse API: from existing codebase usage in `_ZeroModeDeviceContext`
- GitHub Actions ubuntu-22.04 vcan availability: based on kernel module support in standard Ubuntu kernels

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project, no new dependencies
- Architecture: HIGH -- extending existing patterns, clear CONTEXT.md decisions
- Pitfalls: HIGH -- based on actual codebase analysis (additionalProperties strict mode, PTY/socat dependency, shm naming)

**Research date:** 2026-03-13
**Valid until:** 2026-04-13 (stable -- internal project patterns, no external API dependencies)
