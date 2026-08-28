# Phase 7: GPIO Test Harness - Research

**Researched:** 2026-03-13
**Domain:** GPIO simulation, POSIX shared memory ctypes injection, Linux gpio-sim kernel module
**Confidence:** HIGH

## Summary

The GPIO test harness is a Python developer tool that writes DI values into and reads DO values from the `ems_gpio_t` section of the RTDB shared memory. The primary (RTDB-only) mode uses `multiprocessing.shared_memory.SharedMemory` to attach to the `/dev/shm/ems_rtdb` segment and `ctypes.Structure.from_buffer()` to map the `EmsRtdb` struct directly onto the shared memory buffer. The secondary (gpio-sim) mode uses the Linux `gpio-sim` kernel module via configfs to create virtual GPIO chips that the safety_manager C code can interact with through real libgpiod calls.

The RTDB ctypes bindings already exist in `src/common/python/src/ems_common/rtdb.py` (Phase 3 deliverable). The harness needs to implement: (1) shm attach/create logic, (2) seqlock write/read protocol in Python, (3) pin name resolution from `gpio_config.yaml`, (4) gpio-sim configfs lifecycle management, (5) CLI with subcommands. All existing simulators use `argparse` for CLI (no external dependencies), so the harness follows suit.

**Primary recommendation:** Build the harness as a pure-stdlib Python package (no new dependencies beyond PyYAML already in the workspace) using `multiprocessing.shared_memory` + existing `ems_common.rtdb` ctypes structs for RTDB mode, and raw file I/O to `/sys/kernel/config/gpio-sim/` for gpio-sim mode.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Hybrid approach: RTDB-only fallback everywhere, gpio-sim when kernel supports it
- Auto-detect with --backend gpio-sim|rtdb|auto override
- Harness and safety_manager run independently
- Harness writes DI side of ems_gpio_t via Python ctypes, reads DO side
- One-shot CLI commands for RTDB mode, daemon for gpio-sim mode only
- Both pin numbers (DI-6) and config names (ESTOP_NO) accepted
- Multi-pin atomic writes supported (seqlock acquisition)
- Raw electrical level by default, --logical flag applies active_low polarity
- Labeled output default, --raw for machine-parseable
- Clean transitions only -- bounce deferred to Phase 8

### Claude's Discretion
- Exact Python package structure within tools/simulators/gpio_harness/
- gpio-sim sysfs interaction details (chip naming, line numbering)
- Whether daemon uses a PID file or other lifecycle management
- Test structure: how many test functions, what fixtures
- Whether get all uses a formatted table library or manual formatting
- How config loading is cached (per-invocation for one-shot is fine)

### Deferred Ideas (OUT OF SCOPE)
- Contact bounce simulation (configurable bounce duration per pin) -- Phase 8 SIM-06
- Stuck pin fault injection (pin locked high/low, ignoring writes) -- Phase 8 SIM-06
- YAML configurability for harness parameters (default pin states, auto-sequences) -- Phase 8 SIM-06
- GPIO chip unresponsive simulation (libgpiod errors) -- Phase 8 SIM-06
- safety_manager integration pytest fixtures -- M1
- GPIO event edge detection simulation -- M1
- Wiring fault scenario library -- future enhancement
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SIM-05 | GPIO test harness simulates E-Stop (DI-6+7), Fire (DI-3+4), Flood (DI-1), ACDB (DI-0) signals | RTDB ctypes injection pattern (write to ems_gpio_t.di[N]), seqlock protocol, pin name resolution from gpio_config.yaml |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| multiprocessing.shared_memory | stdlib (3.8+) | Attach to POSIX shm segments | stdlib, no C dependencies, creates/attaches /dev/shm/ entries compatible with C shm_open |
| ctypes | stdlib | Map C structs onto shared memory | Already used by ems_common.rtdb, proven in Phase 3 |
| ems_common.rtdb | workspace | EmsRtdb, EmsGpio, EmsSeqlock struct definitions | Phase 3 deliverable, canonical struct mirror |
| PyYAML | 6.x (workspace) | Parse gpio_config.yaml for pin names | Already a workspace dependency |
| argparse | stdlib | CLI parsing | Pattern established by can_sim and modbus_sim |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pathlib | stdlib | File path handling for configfs/sysfs | gpio-sim mode sysfs interaction |
| signal | stdlib | SIGINT/SIGTERM handling for daemon mode | gpio-sim daemon lifecycle |
| subprocess | stdlib | modprobe gpio_sim | gpio-sim auto-detection and module loading |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| multiprocessing.shared_memory | posix_ipc | External dependency, no benefit -- stdlib SharedMemory works with C shm_open on Linux |
| argparse | click/typer | External dependency, other simulators use argparse -- consistency wins |
| Manual table formatting | tabulate | External dependency for a dev tool -- simple f-string formatting is sufficient |

**Installation:**
No new dependencies. The harness uses only stdlib + existing workspace packages (PyYAML, ems_common).

## Architecture Patterns

### Recommended Package Structure
```
tools/simulators/gpio_harness/
    __init__.py          # Public API: set_di, get_di, get_do, set_do (for testing)
    __main__.py          # CLI entry point (argparse, subcommands)
    rtdb_backend.py      # RTDB shared memory read/write operations
    gpio_sim_backend.py  # gpio-sim configfs/sysfs operations
    backend.py           # Backend ABC + auto-detection logic
    config.py            # gpio_config.yaml loading, pin name resolution
    pyproject.toml       # uv workspace member
```

### Pattern 1: Backend Abstraction
**What:** Abstract base class `GpioBackend` with `set_di()`, `get_di()`, `get_do()`, `set_do()` methods. Two concrete implementations: `RtdbBackend` and `GpioSimBackend`.
**When to use:** Always -- the CLI and Python API call backend methods, auto-detection picks the implementation.
**Example:**
```python
# backend.py
from abc import ABC, abstractmethod

class GpioBackend(ABC):
    @abstractmethod
    def set_di(self, pin: int, value: int) -> None: ...

    @abstractmethod
    def get_di(self, pin: int) -> int: ...

    @abstractmethod
    def get_do(self, pin: int) -> int: ...

    @abstractmethod
    def set_di_multi(self, values: dict[int, int]) -> None:
        """Atomic multi-pin write (seqlock for RTDB, single sysfs op for gpio-sim)."""
        ...

def detect_backend(preference: str = "auto") -> GpioBackend:
    if preference == "gpio-sim" or (preference == "auto" and _gpio_sim_available()):
        return GpioSimBackend()
    return RtdbBackend()

def _gpio_sim_available() -> bool:
    """Check if gpio-sim kernel module is loaded or loadable."""
    return Path("/sys/kernel/config/gpio-sim").exists()
```

### Pattern 2: RTDB SharedMemory Attach + ctypes from_buffer
**What:** Attach to existing RTDB shm segment (or create for testing), map EmsRtdb struct via `from_buffer()`, access `gpio.di[N]` and `gpio.do_state[N]` directly.
**When to use:** RTDB backend -- all environments.
**Example:**
```python
# rtdb_backend.py
from multiprocessing import shared_memory
import ctypes
from ems_common.rtdb import EmsRtdb, RTDB_MAGIC, RTDB_VERSION

RTDB_SHM_NAME = "ems_rtdb"

class RtdbBackend(GpioBackend):
    def __init__(self, shm_name: str = RTDB_SHM_NAME, create: bool = False) -> None:
        size = ctypes.sizeof(EmsRtdb)
        try:
            self._shm = shared_memory.SharedMemory(
                name=shm_name, create=create, size=size if create else 0
            )
        except FileNotFoundError:
            # No RTDB exists yet -- create one for standalone testing
            self._shm = shared_memory.SharedMemory(
                name=shm_name, create=True, size=size
            )
            self._created = True
        self._rtdb = EmsRtdb.from_buffer(self._shm.buf)
        if create or getattr(self, "_created", False):
            self._rtdb.magic = RTDB_MAGIC
            self._rtdb.version = RTDB_VERSION

    def set_di(self, pin: int, value: int) -> None:
        self._seqlock_write(lambda: setattr_array(self._rtdb.gpio.di, pin, value))

    def set_di_multi(self, values: dict[int, int]) -> None:
        def _write() -> None:
            for pin, val in values.items():
                self._rtdb.gpio.di[pin] = val
        self._seqlock_write(_write)

    def get_do(self, pin: int) -> int:
        return self._seqlock_read(lambda: self._rtdb.gpio.do_state[pin])

    def _seqlock_write(self, fn: Callable[[], None]) -> None:
        lock = self._rtdb.gpio.lock
        seq = lock.sequence
        lock.sequence = seq + 1  # odd = write in progress
        fn()
        lock.sequence = seq + 2  # even = write complete

    def _seqlock_read(self, fn: Callable[[], T]) -> T:
        lock = self._rtdb.gpio.lock
        while True:
            seq = lock.sequence
            if seq & 1:
                continue  # write in progress, spin
            val = fn()
            if lock.sequence == seq:
                return val  # consistent read

    def close(self) -> None:
        del self._rtdb  # MUST delete ctypes ref before closing shm
        self._shm.close()
        if getattr(self, "_created", False):
            self._shm.unlink()
```

**CRITICAL: ctypes from_buffer lifetime.** The ctypes struct holds a reference to the SharedMemory buffer. You MUST `del` the struct reference before calling `shm.close()`, otherwise you get `BufferError: cannot close exported pointers exist`. Verified experimentally.

### Pattern 3: gpio-sim configfs Lifecycle
**What:** Create virtual GPIO chip via configfs, set lines live, control via sysfs pull/value files.
**When to use:** gpio-sim backend -- when kernel module is available and loaded (requires root).
**Example:**
```python
# gpio_sim_backend.py
CONFIGFS_ROOT = Path("/sys/kernel/config/gpio-sim")
SYSFS_PLATFORM = Path("/sys/devices/platform")

class GpioSimBackend(GpioBackend):
    def __init__(self, chip_name: str = "ems-gpio", num_lines: int = 16) -> None:
        self._device_dir = CONFIGFS_ROOT / chip_name
        self._device_dir.mkdir(exist_ok=True)

        # Create bank with 16 lines (8 DI + 8 DO on one chip)
        bank_dir = self._device_dir / "bank0"
        bank_dir.mkdir(exist_ok=True)
        (bank_dir / "num_lines").write_text(str(num_lines))

        # Configure line names from gpio_config.yaml
        for offset, name in enumerate(line_names):
            line_dir = bank_dir / f"line{offset}"
            line_dir.mkdir(exist_ok=True)
            (line_dir / "name").write_text(name)

        # Go live
        (self._device_dir / "live").write_text("1")

        # Find the created gpiochip sysfs path
        self._chip_sysfs = self._find_chip_sysfs()

    def set_di(self, pin: int, value: int) -> None:
        """Inject DI value via sysfs pull attribute."""
        pull = "pull-up" if value else "pull-down"
        sim_gpio_dir = self._chip_sysfs / f"sim_gpio{pin}"
        (sim_gpio_dir / "pull").write_text(pull)

    def get_do(self, pin: int) -> int:
        """Read DO value from sysfs value attribute."""
        sim_gpio_dir = self._chip_sysfs / f"sim_gpio{pin + 8}"  # DO offset
        return int((sim_gpio_dir / "value").read_text().strip())

    def teardown(self) -> None:
        (self._device_dir / "live").write_text("0")
        # Remove line dirs, bank dir, device dir (rmdir in reverse order)
```

### Pattern 4: Pin Name Resolution
**What:** Load `gpio_config.yaml`, build bidirectional map between pin IDs (DI-0..DI-7, DO-0..DO-7) and config names (ESTOP_NO, PCS_STOP, etc.).
**When to use:** When CLI receives a name like ESTOP_NO instead of DI-6.
**Example:**
```python
# config.py
import yaml
from pathlib import Path

DEFAULT_CONFIG = Path("config/gpio_config.yaml")

class GpioConfig:
    def __init__(self, config_path: Path = DEFAULT_CONFIG) -> None:
        with open(config_path) as f:
            raw = yaml.safe_load(f)

        self._di_names: dict[str, int] = {}  # name -> pin number
        self._do_names: dict[str, int] = {}
        self._di_active_low: dict[int, bool] = {}
        self._do_active_low: dict[int, bool] = {}

        for pin_id, info in raw["digital_inputs"].items():
            num = int(pin_id.split("-")[1])
            self._di_names[info["name"]] = num
            self._di_active_low[num] = info.get("active_low", False)

        for pin_id, info in raw["digital_outputs"].items():
            num = int(pin_id.split("-")[1])
            self._do_names[info["name"]] = num
            self._do_active_low[num] = info.get("active_low", False)

    def resolve_di(self, pin_or_name: str) -> int:
        """Resolve 'DI-6' or 'ESTOP_NO' to pin number 6."""
        if pin_or_name.upper().startswith("DI-"):
            return int(pin_or_name.split("-")[1])
        return self._di_names[pin_or_name.upper()]

    def apply_active_low_di(self, pin: int, logical: bool) -> int:
        """Convert logical 'active' to raw electrical level."""
        if self._di_active_low[pin]:
            return 0 if logical else 1
        return 1 if logical else 0
```

### Pattern 5: CLI Subcommand Structure
**What:** argparse with subparsers for `set`, `get`, `daemon` commands. Matches existing simulator patterns but adds subcommands because GPIO harness has multiple distinct operations.
**When to use:** Always -- this is the CLI entry point.
**Example:**
```python
# __main__.py
import argparse

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GPIO Test Harness -- simulate DI/DO for safety_manager testing"
    )
    parser.add_argument("--backend", choices=["auto", "rtdb", "gpio-sim"], default="auto")
    parser.add_argument("--config", default="config/gpio_config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    # set DI-6 high  OR  set DI-6=high DI-7=low (atomic)
    set_parser = sub.add_parser("set", help="Set DI pin value(s)")
    set_parser.add_argument("assignments", nargs="+",
                            help="PIN VALUE or PIN=VALUE pairs")
    set_parser.add_argument("--logical", action="store_true")

    # get DO-5  OR  get all
    get_parser = sub.add_parser("get", help="Read DI/DO pin value(s)")
    get_parser.add_argument("pin", help="Pin name/number or 'all'")
    get_parser.add_argument("--logical", action="store_true")
    get_parser.add_argument("--raw", action="store_true")

    # daemon (gpio-sim mode only)
    sub.add_parser("daemon", help="Run gpio-sim daemon (holds virtual chip alive)")

    return parser
```

### Anti-Patterns to Avoid
- **Creating RTDB in the harness by default:** The harness should try to attach to an existing RTDB first. Only create if none exists (standalone testing). The data_manager is the canonical RTDB creator (M1).
- **Using raw mmap + ctypes instead of SharedMemory:** `multiprocessing.shared_memory` handles fd management, /dev/shm naming, and cleanup. No reason to call ctypes.cdll libc.shm_open manually.
- **Holding seqlock during I/O or logging:** The seqlock write section must be minimal -- set the values, nothing else. Log before/after the critical section.
- **Forgetting to delete ctypes struct before closing SharedMemory:** This causes `BufferError`. Always `del self._rtdb` before `self._shm.close()`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Shared memory attachment | Custom ctypes libc.shm_open wrapper | `multiprocessing.shared_memory.SharedMemory` | stdlib, handles /dev/shm naming, fd lifecycle, cleanup |
| RTDB struct layout | Duplicate struct definitions | `ems_common.rtdb.EmsRtdb` | Phase 3 deliverable, single source of truth, tested against C sizeof |
| YAML parsing | Custom config parser | PyYAML + `gpio_config.yaml` | Already validated by JSON Schema (Phase 2), already in workspace |
| CLI framework | Custom arg parsing | argparse with subparsers | Established pattern in can_sim and modbus_sim |
| gpio-sim chip creation | Shell scripts or subprocess chains | Direct Path.write_text to configfs | Python pathlib is cleaner than shell, no subprocess overhead |

**Key insight:** The entire RTDB backend is ~80 lines of code because all the hard work (struct layout, seqlock protocol, YAML config) was delivered in Phases 2-3. The harness is a thin CLI/API layer on top of existing infrastructure.

## Common Pitfalls

### Pitfall 1: BufferError on SharedMemory Close
**What goes wrong:** `BufferError: cannot close exported pointers exist` when calling `shm.close()`.
**Why it happens:** `ctypes.Structure.from_buffer(shm.buf)` holds a reference to the memoryview. Python refuses to close the mmap while exported pointers exist.
**How to avoid:** Always `del` the ctypes struct before calling `shm.close()`. Use a `close()` method or context manager pattern.
**Warning signs:** Test teardown failures, resource tracker warnings about leaked shm objects.

### Pitfall 2: Seqlock Not Atomic in Python
**What goes wrong:** Python's GIL does not provide true atomic operations. The seqlock sequence counter in Python ctypes is not _Atomic -- it's a plain uint32 write.
**Why it happens:** The C seqlock uses `_Atomic uint32_t` with memory ordering fences. Python ctypes maps this to `c_uint32` without atomic semantics.
**How to avoid:** For the harness (a developer tool), this is acceptable because: (1) the harness is the only DI writer, (2) there is no concurrent safety_manager in Phase 7, (3) when safety_manager exists (M1), it reads DI with seqlock retry which handles torn reads. The sequence increment pattern (odd=writing, even=done) still signals intent correctly.
**Warning signs:** Only matters under true concurrent access. Document this limitation.

### Pitfall 3: gpio-sim Requires Root
**What goes wrong:** `modprobe gpio_sim` and configfs writes require root/CAP_SYS_MODULE.
**Why it happens:** Kernel module loading is a privileged operation. configfs is typically root-owned.
**How to avoid:** Auto-detect checks `Path("/sys/kernel/config/gpio-sim").exists()` (module already loaded) rather than trying to modprobe. CLI prints clear message: "gpio-sim not available, falling back to RTDB mode". Tests use `pytest.mark.skipif` for gpio-sim tests.
**Warning signs:** CI failures if tests assume gpio-sim is available.

### Pitfall 4: SharedMemory Name Mismatch Between C and Python
**What goes wrong:** C uses `shm_open("/ems_rtdb", ...)` with leading slash. Python's SharedMemory strips the leading slash internally.
**Why it happens:** POSIX shm names must start with `/` in C. Python's SharedMemory on Linux maps `name="ems_rtdb"` to `/dev/shm/ems_rtdb`, automatically handling the slash.
**How to avoid:** Use `SharedMemory(name="ems_rtdb")` (no leading slash) in Python. This matches the `/dev/shm/ems_rtdb` file created by C's `shm_open("/ems_rtdb", ...)`. Verified experimentally.
**Warning signs:** `FileNotFoundError` when trying to attach.

### Pitfall 5: gpio-sim configfs Cleanup Order
**What goes wrong:** `rmdir` on configfs dirs fails with "Device or resource busy".
**Why it happens:** Must set `live` to `0` before removing line/bank/device directories. Must remove in reverse order (lines first, then banks, then device).
**How to avoid:** Teardown sequence: write "0" to `live`, then rmdir line dirs, rmdir bank dirs, rmdir device dir. Use try/finally or atexit handler.
**Warning signs:** Stale gpio-sim chips persisting after harness exits.

### Pitfall 6: SharedMemory track=True Causes Resource Tracker Warnings
**What goes wrong:** Resource tracker warns about "leaked" shared memory on exit.
**Why it happens:** When attaching to an existing shm (created by another process), the resource tracker thinks it should clean it up.
**How to avoid:** Use `track=False` when attaching to existing RTDB shm (the harness doesn't own it). Only use `track=True` when the harness creates the shm for standalone testing.
**Warning signs:** UserWarning about leaked shared_memory objects at shutdown.

## Code Examples

### Example 1: One-Shot DI Write (RTDB Mode)
```python
# Verified pattern: attach to shm, write DI, exit
from multiprocessing import shared_memory
import ctypes
from ems_common.rtdb import EmsRtdb

shm = shared_memory.SharedMemory(name="ems_rtdb", create=False, track=False)
rtdb = EmsRtdb.from_buffer(shm.buf)

# Seqlock write: set DI-6=1 (E-Stop NO activated)
lock = rtdb.gpio.lock
seq = lock.sequence
lock.sequence = seq + 1   # begin write (odd)
rtdb.gpio.di[6] = 1
lock.sequence = seq + 2   # end write (even)

del rtdb
shm.close()
```

### Example 2: Multi-Pin Atomic Write
```python
# E-Stop dual-channel: DI-6 (NO) high + DI-7 (NC) low simultaneously
lock = rtdb.gpio.lock
seq = lock.sequence
lock.sequence = seq + 1   # begin write (odd)
rtdb.gpio.di[6] = 1       # NO channel activated
rtdb.gpio.di[7] = 0       # NC channel activated (active-low)
lock.sequence = seq + 2   # end write (even)
```

### Example 3: DO Readback with Seqlock
```python
# Read DO-5 (PCS_STOP) with seqlock retry
lock = rtdb.gpio.lock
while True:
    seq = lock.sequence
    if seq & 1:
        continue  # write in progress
    value = rtdb.gpio.do_state[5]
    if lock.sequence == seq:
        break  # consistent read
print(f"PCS_STOP = {value}")
```

### Example 4: gpio-sim Chip Creation via configfs
```python
# Source: https://docs.kernel.org/admin-guide/gpio/gpio-sim.html
from pathlib import Path

CONFIGFS = Path("/sys/kernel/config/gpio-sim")
device = CONFIGFS / "ems-gpio"
device.mkdir()

bank = device / "bank0"
bank.mkdir()
(bank / "num_lines").write_text("16")

# Name line 6 as ESTOP_NO
line6 = bank / "line6"
line6.mkdir()
(line6 / "name").write_text("ESTOP_NO")

# Activate
(device / "live").write_text("1")

# Read chip name for gpiochip path
chip_name = (bank / "chip_name").read_text().strip()
# e.g., "gpiochip4" -> /sys/devices/platform/gpio-sim.0/gpiochip4/

# Inject DI value via sysfs pull
sysfs_chip = Path(f"/sys/devices/platform/gpio-sim.0/{chip_name}")
(sysfs_chip / "sim_gpio6" / "pull").write_text("pull-up")  # DI-6 = high
value = (sysfs_chip / "sim_gpio6" / "value").read_text().strip()  # "1"

# Teardown
(device / "live").write_text("0")
for line_dir in bank.iterdir():
    if line_dir.is_dir():
        line_dir.rmdir()
bank.rmdir()
device.rmdir()
```

### Example 5: Auto-Detection
```python
def _gpio_sim_available() -> bool:
    """Check if gpio-sim configfs is mounted and accessible."""
    configfs_dir = Path("/sys/kernel/config/gpio-sim")
    if not configfs_dir.exists():
        # Try loading the module (may fail without root)
        try:
            subprocess.run(
                ["modprobe", "gpio_sim"],
                check=True, capture_output=True, timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False
    return configfs_dir.exists()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| gpio-mockup kernel module | gpio-sim configfs module | Linux 5.17 (2022) | gpio-sim is the replacement, gpio-mockup deprecated |
| /sys/class/gpio sysfs interface | libgpiod character device API | libgpiod 1.0 (2017) | sysfs GPIO deprecated since Linux 4.8, libgpiod is the standard |
| gpiod Python v1 API (Chip, Line objects) | gpiod v2 API (request_lines, LineSettings) | gpiod 2.0 (2023) | v2 is a complete rewrite, v1 API removed |

**Deprecated/outdated:**
- `gpio-mockup`: Replaced by `gpio-sim`. gpio-mockup requires module reload to change config; gpio-sim uses configfs for runtime configuration.
- `/sys/class/gpio` sysfs interface: Deprecated. Use libgpiod character device interface.
- gpiod Python v1 API: Removed in gpiod 2.0. The v2 API uses `gpiod.request_lines()` + `LineSettings`.

**Note:** The gpiod Python package (v2.4.1, March 2026) is NOT needed for the harness. The harness controls gpio-sim via sysfs configfs (file I/O), not via libgpiod. The safety_manager C code will use libgpiod. The harness only needs to create the virtual chip and control line pulls via sysfs.

## Open Questions

1. **RTDB shm name convention**
   - What we know: Architecture docs reference `/ems_rtdb`. Phase 3 CONTEXT mentions `shm_open("/ems_rtdb")`.
   - What's unclear: The exact shm name hasn't been implemented yet (data_manager is M1).
   - Recommendation: Use `"ems_rtdb"` as the default name (maps to `/dev/shm/ems_rtdb`). Make it configurable via `--shm-name` CLI flag for testing flexibility.

2. **gpio-sim device numbering**
   - What we know: gpio-sim creates `/sys/devices/platform/gpio-sim.N/` where N is auto-assigned.
   - What's unclear: If multiple gpio-sim devices exist, finding our specific one requires reading `chip_name` from configfs.
   - Recommendation: Read `chip_name` from the configfs bank directory after going live, use that to locate the sysfs path.

3. **DI/DO mapping on gpio-sim chip**
   - What we know: Physical hardware has separate DI and DO banks. gpio-sim creates a single chip with N lines.
   - What's unclear: Whether to map DI as lines 0-7 and DO as lines 8-15 on one chip, or create two separate chips.
   - Recommendation: Single chip with 16 lines (DI 0-7, DO 8-15). Simpler configfs management. Document the offset mapping clearly.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (already configured) |
| Config file | pyproject.toml [tool.pytest] |
| Quick run command | `uv run pytest tests/test_gpio_harness.py -x -q` |
| Full suite command | `uv run pytest -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIM-05 | DI injection (all 8 pins) | unit | `uv run pytest tests/test_gpio_harness.py::test_set_di_all_pins -x` | No -- Wave 0 |
| SIM-05 | DO readback (all 8 pins) | unit | `uv run pytest tests/test_gpio_harness.py::test_get_do_all_pins -x` | No -- Wave 0 |
| SIM-05 | E-Stop dual-channel (DI-6 + DI-7 atomic) | unit | `uv run pytest tests/test_gpio_harness.py::test_estop_dual_channel -x` | No -- Wave 0 |
| SIM-05 | Fire dual-confirm (DI-3 + DI-4) | unit | `uv run pytest tests/test_gpio_harness.py::test_fire_dual_confirm -x` | No -- Wave 0 |
| SIM-05 | Flood (DI-1) | unit | `uv run pytest tests/test_gpio_harness.py::test_flood_signal -x` | No -- Wave 0 |
| SIM-05 | ACDB feedback (DI-0) | unit | `uv run pytest tests/test_gpio_harness.py::test_acdb_feedback -x` | No -- Wave 0 |
| SIM-05 | Pin name resolution (ESTOP_NO -> DI-6) | unit | `uv run pytest tests/test_gpio_harness.py::test_pin_name_resolution -x` | No -- Wave 0 |
| SIM-05 | --logical polarity inversion | unit | `uv run pytest tests/test_gpio_harness.py::test_logical_polarity -x` | No -- Wave 0 |
| SIM-05 | Multi-pin atomic write (seqlock) | unit | `uv run pytest tests/test_gpio_harness.py::test_multi_pin_atomic -x` | No -- Wave 0 |
| SIM-05 | CLI set/get commands | integration | `uv run pytest tests/test_gpio_harness.py::test_cli_set_get -x` | No -- Wave 0 |
| SIM-05 | Backend auto-detection | unit | `uv run pytest tests/test_gpio_harness.py::test_backend_autodetect -x` | No -- Wave 0 |
| SIM-05 | gpio-sim backend (when available) | integration | `uv run pytest tests/test_gpio_harness.py -m gpio_sim -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_gpio_harness.py -x -q`
- **Per wave merge:** `uv run pytest -x -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/test_gpio_harness.py` -- covers SIM-05 (all DI/DO tests, CLI tests, backend tests)
- [ ] `tools/simulators/gpio_harness/pyproject.toml` -- workspace member registration
- [ ] SharedMemory test fixture (create/teardown RTDB shm per test)

## Sources

### Primary (HIGH confidence)
- Kernel docs: https://docs.kernel.org/admin-guide/gpio/gpio-sim.html -- configfs structure, sysfs pull/value attributes, lifecycle
- Python docs: https://docs.python.org/3/library/multiprocessing.shared_memory.html -- SharedMemory API, name parameter, track parameter
- libgpiod docs: https://libgpiod.readthedocs.io/en/latest/python_line_request.html -- v2 API reference (for future safety_manager, not the harness)
- Codebase: `src/common/python/src/ems_common/rtdb.py` -- EmsGpio struct (di[8], do_state[8], seqlock)
- Codebase: `src/common/c/include/seqlock.h` -- seqlock protocol (odd=writing, even=done)
- Codebase: `config/gpio_config.yaml` -- full pin map with names, active_low, debounce_ms
- Codebase: `tools/simulators/modbus_sim/` -- reference simulator package structure

### Secondary (MEDIUM confidence)
- PyPI gpiod 2.4.1: https://pypi.org/project/gpiod/ -- current version, not needed for harness but relevant for gpio-sim context
- Experimental verification: SharedMemory creates /dev/shm/ entries compatible with C shm_open; ctypes.from_buffer works but requires del before close

### Tertiary (LOW confidence)
- gpio-sim device numbering (gpio-sim.N sysfs path) -- based on kernel docs, not experimentally verified due to permission constraints

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib + existing workspace packages, no new dependencies
- Architecture: HIGH -- follows established simulator patterns (can_sim, modbus_sim), RTDB bindings proven in Phase 3
- Pitfalls: HIGH -- BufferError and seqlock limitations verified experimentally
- gpio-sim specifics: MEDIUM -- based on kernel docs, not fully tested due to root requirement

**Research date:** 2026-03-13
**Valid until:** 2026-04-13 (stable domain, no fast-moving dependencies)
