# Phase 9: Foundation - Research

**Researched:** 2026-03-13
**Domain:** Config validation/serving (Python), RTDB shared memory lifecycle (C + Python), ZMQ IPC
**Confidence:** HIGH

## Summary

Phase 9 builds two foundational subsystems: **config_manager** (Python) and **data_manager** (C + Python hybrid). Config_manager loads, validates, serves, and hot-reloads YAML configuration. Data_manager owns RTDB shared memory creation/lifecycle and publishes 1Hz telemetry snapshots over ZMQ.

The codebase already has substantial scaffolding from M0: all 14 JSON Schemas (Draft 2020-12), all 14 YAML configs with 3 deployment profiles, a working `validate_config.py` tool, complete C and Python RTDB struct definitions with matched sizeof (1,800,744 bytes), seqlock primitives, and IPC contract definitions (socket paths, topics, encode/decode helpers). The implementation work is to wire these pieces into running services.

**Primary recommendation:** Build config_manager as a pure Python asyncio service (inotify + ZMQ REQ/REP). Build data_manager as a hybrid: a small C program creates/owns the shm segment, and a Python process handles ZMQ PUB telemetry and health monitoring. Both share libems_rtdb.so for shm attach/detach.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Fail-fast on any schema validation error -- entire EMS refuses to start if any config file fails validation
- Device configs (dg_config, pv_config, btms_config, meter_config) are optional if that hardware is not declared in system_config topology; core configs (system_config, gpio_config, control_config, alarms_config, schedule_config, bms_config, pcs_config, network_config, cloud_config, hmi_config) must always be present
- system_config.yaml declares which subsystems are present (e.g., `has_dg`, `has_pv`); config_manager only loads device configs for declared subsystems
- All 14 YAML files validated against JSON Schema at startup -- no partial startup, no degraded mode
- Graceful shutdown: data_manager explicitly unlinks shm via systemd ExecStop
- Startup safety net: detect stale shm (check magic/version), destroy and recreate if found
- Topology change (system_config edit) requires full EMS restart -- config_manager rejects hot-reload for system_config
- RTDB zero-filled on creation (memset 0); readers treat zero values + stale `last_update_ms` as "no data received yet"
- `config_reload` ZMQ event includes the full validated config (not a diff); diff logged separately for audit per CONF-08
- Consumers trust config_manager's validation -- no re-validation on receive, normal defensive coding only
- Failed hot-reload validation: reject the change, keep current running config, publish detailed error event with field path, expected vs actual, and reason -- not silent
- inotify debounce: 500ms after last IN_CLOSE_WRITE before validate-and-apply
- Minimal surface: `get_config(name)` and `get_value(name, path)` only -- no `list_configs()`, no `get_schema()`, no remote validate
- Missing path in `get_value()` returns error response with descriptive message, not null
- All queries served from in-memory cache loaded at startup, updated atomically on hot-reload -- no disk re-reads per request
- Remote validation not needed -- CLI tool `ems-config validate` covers field service use (CONF-07)

### Claude's Discretion
- Profile overlay merge strategy (full replacement vs deep merge)
- RTDB ownership model (long-running C process, hybrid C+Python, or other architecture)
- data_manager internal architecture for ZMQ telemetry publishing and health monitoring

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CONF-01 | Load and validate all 14 YAML config files against JSON Schema at startup, fail fast | Existing `validate_config.py` and all 14 schemas provide the foundation; config_manager wraps this into a service |
| CONF-02 | Hot-reload for control_config, alarms_config, schedule_config via inotify with 500ms debounce | Python `asyncio` + `inotify_simple` for non-blocking file watching; schemas already have `x-hot-reload: true` metadata |
| CONF-03 | Schema version validation rejects mismatched versions with migration guidance | Add `schema_version` field to each YAML + schema; config_manager checks at load time |
| CONF-04 | Config backup before hot-reload (keep last 5) | Simple `shutil.copy2` to `config/backups/{name}.{timestamp}.yaml` with glob cleanup |
| CONF-05 | ZMQ REQ/REP config query API (get_config, get_value) | New `SOCK_CONFIG` socket needed in ipc.py/ipc_defs.h; pyzmq REP socket in config_manager |
| CONF-06 | Deployment profile overlay loading from config/profiles/ | 3 profiles already exist with all 14 files; overlay is CLI arg or env var at startup |
| CONF-07 | Config validation dry-run CLI (ems-config validate) | Extend existing `validate_config.py` into a proper CLI entry point in config_manager package |
| CONF-08 | Config diff on reload in ZMQ event payload | Python `deepdiff` or manual dict comparison; publish via existing `TOPIC_CONFIG_RELOAD` event |
| DATA-01 | POSIX shm creation via shm_open + ftruncate + mmap, sized from system_config topology | C data_manager_c process owns shm; uses existing rtdb.h struct (always 1.8 MB fixed size) |
| DATA-02 | RTDB initialization zeroes segment, writes magic/version/topology, initializes seqlocks | C code: memset(0), then write header fields from parsed system_config topology values |
| DATA-03 | RTDB lifecycle API (create/attach/detach/destroy) for C and Python | C: libems_rtdb.so shared library; Python: ctypes wrapper with resource_tracker.unregister() |
| DATA-04 | Topology validation writes actual counts; readers check before iterating | Header fields cluster_count etc. written from config; all readers must check these |
| DATA-05 | ZMQ PUB/SUB telemetry fan-out, 1Hz RTDB section snapshots as MessagePack | Python data_manager process reads shm via ctypes, publishes on SOCK_TELEMETRY using existing encode_telemetry() |
| DATA-06 | Health monitoring checks last_update_ms per section at 1Hz | Python data_manager compares CLOCK_MONOTONIC against each section's last_update_ms |
| DATA-07 | Startup ordering via systemd After=ems-data-manager.service | systemd unit file with Type=notify (or Type=simple with readiness protocol) |
| DATA-08 | Periodic RTDB snapshot to disk for forensic analysis | Python: dump ctypes buffer to binary file at configurable interval (default 60s) and on safety events |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jsonschema | 4.26.0 | JSON Schema Draft 2020-12 validation | Already in use; only Python library supporting Draft 2020-12 |
| PyYAML | 6.0.3 | YAML config parsing | Already in use; safe_load prevents code execution |
| pyzmq | >=26.0 | ZMQ REQ/REP + PUB/SUB sockets | Project-specified IPC transport; needs to be added to deps |
| msgpack | 1.1.2 | MessagePack serialization | Already in use; matches mpack 1.1.1 on C side |
| inotify_simple | >=1.3 | Linux inotify for hot-reload file watching | Thin wrapper over Linux inotify; no polling overhead |
| POSIX shm (C) | N/A | shm_open/mmap for RTDB | Project architecture decision; no library needed |
| mpack | 1.1.1 | C MessagePack (vendored amalgamation) | Already vendored in project |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| deepdiff | >=8.0 | Config diff for CONF-08 audit | Optional -- can use manual dict walk instead to avoid dependency |
| click | >=8.0 | CLI framework for ems-config | Optional -- argparse is sufficient for simple validate CLI |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| inotify_simple | watchdog | watchdog is cross-platform but heavier; inotify_simple is Linux-only which matches embedded target |
| deepdiff | manual dict comparison | deepdiff gives path-annotated diffs automatically; manual is zero-dependency |
| pyzmq asyncio | pyzmq sync | asyncio is preferred for single-threaded config_manager with inotify + REQ/REP |

**Installation:**
```bash
uv add pyzmq inotify-simple --package ems-config-manager
uv add pyzmq --package ems-data-manager
```

## Architecture Patterns

### Recommended Project Structure
```
src/
  config_manager/
    src/ems_config_manager/
      __init__.py          # Package init
      __main__.py          # Entry point (uvx run / systemd)
      manager.py           # ConfigManager class (load, validate, cache, serve)
      watcher.py           # InotifyWatcher class (debounced hot-reload)
      cli.py               # ems-config validate CLI
      overlay.py           # Profile overlay merge logic
  data_manager/
    c/
      src/
        main.c             # shm create, write header, hold open, signal ready
        rtdb_lifecycle.c    # create/attach/detach/destroy functions
      include/
        rtdb_lifecycle.h    # Public API for libems_rtdb.so
      CMakeLists.txt        # Build data_manager_c + libems_rtdb.so
    python/
      src/ems_data_manager/
        __init__.py
        __main__.py         # Entry point
        publisher.py        # 1Hz ZMQ PUB telemetry loop
        health.py           # Section staleness monitor
        snapshot.py         # Periodic disk snapshot (DATA-08)
        shm.py              # Python shm attach/detach wrapper
  common/
    python/src/ems_common/
      rtdb.py               # (existing) ctypes mirror
      ipc.py                # (existing) + add SOCK_CONFIG
    c/include/
      rtdb.h                # (existing)
      seqlock.h             # (existing)
      ipc_defs.h            # (existing) + add EMS_SOCK_CONFIG
      rtdb_lifecycle.h      # New: C API for shm lifecycle
```

### Pattern 1: Config Manager as Async Service
**What:** Single asyncio event loop running inotify watcher + ZMQ REP socket + ZMQ PUB for reload events
**When to use:** config_manager is a long-running Python process
**Example:**
```python
# Simplified config_manager main loop
import asyncio
import zmq
import zmq.asyncio
from ems_common.ipc import SOCK_CONFIG, encode_command_response

class ConfigManager:
    def __init__(self, config_dir: Path, profile: str | None = None) -> None:
        self._configs: dict[str, dict] = {}  # In-memory cache
        self._schemas: dict[str, dict] = {}
        self._config_dir = config_dir
        self._profile = profile

    async def startup(self) -> None:
        """Load all configs, validate, populate cache. Fail-fast on error."""
        self._load_schemas()
        self._load_and_validate_all()  # Raises on failure

    async def serve_forever(self) -> None:
        """Run REQ/REP query server + inotify watcher concurrently."""
        ctx = zmq.asyncio.Context()
        rep_sock = ctx.socket(zmq.REP)
        rep_sock.bind("ipc:///run/ems/config.sock")
        await asyncio.gather(
            self._query_loop(rep_sock),
            self._watch_loop(),
        )
```

### Pattern 2: RTDB Lifecycle (C Owner + Python Consumer)
**What:** C process creates and owns shm; Python processes attach as readers/writers
**When to use:** data_manager_c is the shm owner; all other processes attach
**Example:**
```c
/* rtdb_lifecycle.c -- C API */
#include "rtdb.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <string.h>

#define RTDB_SHM_NAME "/ems_rtdb"

ems_rtdb_t *rtdb_create(uint8_t clusters, uint8_t racks,
                        uint8_t modules, uint8_t cells, uint8_t temps)
{
    /* Check for stale shm */
    int fd = shm_open(RTDB_SHM_NAME, O_RDWR, 0);
    if (fd >= 0) {
        /* Stale shm found -- destroy and recreate */
        close(fd);
        shm_unlink(RTDB_SHM_NAME);
    }

    fd = shm_open(RTDB_SHM_NAME, O_CREAT | O_RDWR, 0660);
    ftruncate(fd, sizeof(ems_rtdb_t));
    ems_rtdb_t *rtdb = mmap(NULL, sizeof(ems_rtdb_t),
                            PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);

    /* Zero-fill and write header */
    memset(rtdb, 0, sizeof(ems_rtdb_t));
    rtdb->magic = RTDB_MAGIC;
    rtdb->version = RTDB_VERSION;
    rtdb->cluster_count = clusters;
    rtdb->racks_per_cluster = racks;
    rtdb->modules_per_rack = modules;
    rtdb->cells_per_module = cells;
    rtdb->temps_per_module = temps;

    /* Initialize all seqlocks */
    for (int c = 0; c < MAX_CLUSTERS; c++)
        for (int r = 0; r < MAX_RACKS_PER_CLUSTER; r++)
            ems_seqlock_init(&rtdb->clusters[c].racks[r].lock);
    ems_seqlock_init(&rtdb->pcs.lock);
    ems_seqlock_init(&rtdb->gpio.lock);
    ems_seqlock_init(&rtdb->meter.lock);
    ems_seqlock_init(&rtdb->btms.lock);
    ems_seqlock_init(&rtdb->system.lock);

    return rtdb;
}
```

### Pattern 3: Python shm Attach with resource_tracker Fix
**What:** Python 3.12 SharedMemory always registers with resource_tracker; we must unregister to prevent premature cleanup
**When to use:** Any Python process attaching to the C-created shm
**Example:**
```python
import ctypes
import multiprocessing.resource_tracker as rt
from multiprocessing.shared_memory import SharedMemory
from ems_common.rtdb import EmsRtdb, RTDB_MAGIC, RTDB_VERSION

def attach_rtdb() -> tuple[SharedMemory, EmsRtdb]:
    """Attach to existing RTDB shared memory. Returns (shm, rtdb_ptr)."""
    shm = SharedMemory(name="ems_rtdb", create=False)
    # Prevent resource_tracker from unlinking on process exit
    rt.unregister(f"/{shm.name}", "shared_memory")
    rtdb = EmsRtdb.from_buffer(shm.buf)
    assert rtdb.magic == RTDB_MAGIC, f"Bad magic: 0x{rtdb.magic:08X}"
    assert rtdb.version == RTDB_VERSION, f"Bad version: {rtdb.version}"
    return shm, rtdb
```

### Pattern 4: Debounced Inotify Hot-Reload
**What:** Watch config files for IN_CLOSE_WRITE, debounce 500ms, then validate-and-swap
**When to use:** config_manager hot-reload of control_config, alarms_config, schedule_config
**Example:**
```python
import asyncio
from inotify_simple import INotify, flags

class ConfigWatcher:
    DEBOUNCE_S: float = 0.5
    HOT_RELOAD_CONFIGS: set[str] = {"control_config", "alarms_config", "schedule_config"}

    async def watch_loop(self, config_dir: Path, on_change: Callable) -> None:
        inotify = INotify()
        wd = inotify.add_watch(str(config_dir), flags.CLOSE_WRITE)
        pending: dict[str, asyncio.Task] = {}
        loop = asyncio.get_event_loop()

        while True:
            # inotify.read() blocks -- run in executor
            events = await loop.run_in_executor(None, inotify.read)
            for event in events:
                name = Path(event.name).stem  # e.g., "control_config"
                if name in self.HOT_RELOAD_CONFIGS:
                    if name in pending:
                        pending[name].cancel()
                    pending[name] = asyncio.create_task(
                        self._debounced_reload(name, config_dir, on_change)
                    )

    async def _debounced_reload(self, name, config_dir, on_change):
        await asyncio.sleep(self.DEBOUNCE_S)
        await on_change(name, config_dir)
```

### Anti-Patterns to Avoid
- **Polling config files instead of inotify:** Wastes CPU cycles on embedded hardware. Use inotify (Linux-specific is fine -- target is always Linux).
- **Validating config on every query:** Queries come from trusted internal modules. Validate once at load/reload, serve from cache.
- **Creating shm from Python:** Python's SharedMemory has resource_tracker issues. Let C own creation/unlink; Python only attaches.
- **Using threading for config_manager:** asyncio is simpler for I/O-bound work (inotify + ZMQ). No shared state races.
- **Re-reading YAML from disk on each config query:** Cache in memory, swap atomically on reload.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON Schema validation | Custom YAML checker | jsonschema with Draft202012Validator | Schema conditionals (if/then/else), additionalProperties, x-extensions already defined |
| File change detection | Timer-based poll loop | inotify_simple | Kernel-level notification, zero CPU when idle |
| MessagePack encoding | Custom binary format | msgpack + mpack | C/Python interop already proven in M0 IPC tests |
| Config diff generation | Manual field-by-field compare | deepdiff or recursive dict diff | Nested YAML diffs are tricky (arrays, reordering) |
| Shared memory lifecycle | Python SharedMemory for creation | C shm_open/mmap directly | resource_tracker issues in Python 3.12; C gives full control |
| ZMQ async patterns | Threading + blocking ZMQ | pyzmq asyncio API | Single-threaded event loop, no locking needed |

**Key insight:** The M0 scaffolding already solved the hard interop problems (ctypes struct matching, MessagePack C/Python round-trip, JSON Schema with Draft 2020-12). Phase 9 wires these proven pieces into running services.

## Common Pitfalls

### Pitfall 1: Python resource_tracker Unlinking Shared Memory
**What goes wrong:** Python's SharedMemory registers with multiprocessing.resource_tracker. When Python process exits, tracker unlinks the shm even though C processes still use it.
**Why it happens:** Python 3.12 lacks `track=False` parameter on SharedMemory. resource_tracker is a separate daemon process.
**How to avoid:** Call `resource_tracker.unregister(f"/{shm.name}", "shared_memory")` immediately after SharedMemory creation/attach. This is already documented in STATE.md as a known pattern.
**Warning signs:** RTDB disappears when a Python consumer restarts but data_manager_c is still running.

### Pitfall 2: Inotify Event Storms During Editor Save
**What goes wrong:** Text editors (vim, nano, sed -i) may create temp files, rename, delete, and create multiple events for a single logical save.
**Why it happens:** vim writes to `.swp`, renames original to backup, renames `.swp` to original. sed -i creates temp, writes, renames.
**How to avoid:** Watch only IN_CLOSE_WRITE (not MODIFY or CREATE). Debounce 500ms per file. Match only `*.yaml` filenames. Ignore dot-prefixed temp files.
**Warning signs:** Hot-reload fires multiple times or fires for temp files.

### Pitfall 3: ZMQ Socket Bind Order Race
**What goes wrong:** Consumer starts before publisher and misses initial messages, or REQ connects before REP binds and gets ECONNREFUSED.
**Why it happens:** systemd After= only guarantees process start, not socket readiness.
**How to avoid:** PUB/SUB is tolerant (late subscriber just misses old messages). For REQ/REP, use ZMQ_RECONNECT_IVL. For critical readiness, use sd_notify(READY=1) with Type=notify systemd units. Config consumers should retry get_config on startup.
**Warning signs:** Intermittent "Connection refused" errors in logs on fast restart.

### Pitfall 4: Stale Shared Memory After Crash
**What goes wrong:** data_manager crashes without ExecStop running. Next startup finds existing shm with potentially corrupted data.
**Why it happens:** shm_unlink was only called in shutdown path; crash skips it.
**How to avoid:** On startup, check if shm exists. If magic/version mismatch or topology mismatch, unlink and recreate. If valid but stale (check uptime field), unlink and recreate. This is a locked decision from CONTEXT.md.
**Warning signs:** RTDB has data from previous run; `last_update_ms` values are in the past.

### Pitfall 5: seqlock Spin on Python Side
**What goes wrong:** Python ctypes reading a seqlock field that C is actively writing can cause visible spin loop.
**Why it happens:** Python's GIL + ctypes means the read is not truly atomic. seqlock retry loop may iterate many times.
**How to avoid:** Accept that Python readers are slower. The 1Hz publish rate means contention is extremely low (write takes microseconds out of 1000ms). For the telemetry publisher, copy the entire section bytes (`ctypes.memmove`) inside a seqlock read, minimizing time in the critical section.
**Warning signs:** High CPU on data_manager Python process (would only happen if a writer is stuck, which indicates a bug).

### Pitfall 6: system_config Topology Fields Not Yet in Schema
**What goes wrong:** The CONTEXT.md mentions `has_dg`, `has_pv` flags in system_config to control optional device loading, but these fields do not currently exist in the system_config JSON Schema or YAML.
**Why it happens:** This is a new requirement from Phase 9 discussion that was not part of M0.
**How to avoid:** Add optional device presence fields to system_config.schema.json (e.g., `has_dg`, `has_pv`, `has_btms`, `has_meter`) with defaults based on model tier. Update all 3 profiles and active config accordingly. Must be done before config_manager can implement conditional loading.
**Warning signs:** config_manager has no way to know which device configs to skip.

## Code Examples

### Existing validate_file (from tools/validate_config.py)
```python
# Source: tools/validate_config.py (already in repo)
def validate_file(name: str, config_dir: Path) -> list[str]:
    yaml_path = config_dir / f"{name}.yaml"
    schema_path = SCHEMAS_DIR / f"{name}.schema.json"
    with yaml_path.open("r") as fh:
        data = yaml.safe_load(fh)
    with schema_path.open("r") as fh:
        schema = json.load(fh)
    validator = Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(data):
        errors.append(format_error(f"{name}.yaml", error))
        break  # Fail-fast
    return errors
```

### Existing IPC Telemetry Encoding (from ems_common/ipc.py)
```python
# Source: src/common/python/src/ems_common/ipc.py (already in repo)
def encode_telemetry(timestamp_ms, seq, source, topic, payload) -> bytes:
    msg = {
        "ts": timestamp_ms, "seq": seq, "src": source,
        "topic": topic, "payload": payload,
    }
    return msgpack.packb(msg, use_bin_type=True)
```

### ZMQ Config Query Protocol (new for CONF-05)
```python
# Config query request format (REQ side):
request = encode_command_request("get_config", {"name": "pcs_config"})
# Response format (REP side):
response = encode_command_response("ok", result={"connection": {...}, "registers": {...}})

# get_value with dotted path:
request = encode_command_request("get_value", {"name": "pcs_config", "path": "connection.protocol"})
response = encode_command_response("ok", result={"value": "rtu"})
```

### Profile Overlay Strategy (Claude's Discretion -- Recommendation: Full Replacement)
```python
# Recommended: Full file replacement, not deep merge
# Rationale: All 14 YAML files exist in each profile directory.
# Deep merge introduces ambiguity (what does "remove a key" look like?).
# Full replacement is deterministic and already matches the file structure.

def load_with_profile(name: str, config_dir: Path, profile: str | None) -> dict:
    """Load config with optional profile overlay (full replacement)."""
    if profile:
        profile_path = config_dir / "profiles" / profile / f"{name}.yaml"
        if profile_path.exists():
            return yaml.safe_load(profile_path.read_text())
    return yaml.safe_load((config_dir / f"{name}.yaml").read_text())
```

### RTDB Section Read with Seqlock (Python)
```python
import ctypes
import time

def read_pcs_section(rtdb: EmsRtdb) -> dict:
    """Read PCS section with seqlock retry."""
    while True:
        seq = rtdb.pcs.lock.sequence
        if seq & 1:  # Write in progress
            continue
        # Copy data
        data = {
            "ac_voltage": rtdb.pcs.ac_voltage,
            "active_power": rtdb.pcs.active_power,
            "dc_voltage": rtdb.pcs.dc_voltage,
            "frequency": rtdb.pcs.frequency,
            "temperature": rtdb.pcs.temperature,
            "state": rtdb.pcs.state,
            "fault_code": rtdb.pcs.fault_code,
            "last_update_ms": rtdb.pcs.last_update_ms,
        }
        if rtdb.pcs.lock.sequence == seq:  # No write during our read
            return data
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Python 3.8 SharedMemory (track=True only) | Python 3.13 track=False parameter | Python 3.13 (Oct 2024) | Project uses 3.12, so must use resource_tracker.unregister() workaround |
| jsonschema Draft-07 | jsonschema Draft 2020-12 | jsonschema 4.18+ | Already using Draft 2020-12 with if/then/else conditionals |
| pyzmq blocking | pyzmq asyncio | pyzmq 22+ | Use zmq.asyncio.Context for non-blocking sockets |
| inotifyx (abandoned) | inotify_simple | 2020+ | inotify_simple is actively maintained, simple API |

**Deprecated/outdated:**
- `watchgod` (renamed to `watchfiles`): Uses Rust backend, overkill for watching 3 files
- Python `multiprocessing.shared_memory` for shm creation: resource_tracker issues on 3.12; use C for creation

## Open Questions

1. **SOCK_CONFIG socket path**
   - What we know: ipc.py and ipc_defs.h define SOCK_TELEMETRY, SOCK_CONTROL_CMD, SOCK_ALARM_CMD, SOCK_LOGGER but NOT a config query socket
   - What's unclear: None -- we need to add it
   - Recommendation: Add `SOCK_CONFIG = "ipc:///run/ems/config.sock"` to both ipc.py and ipc_defs.h

2. **system_config subsystem presence flags**
   - What we know: CONTEXT.md says `has_dg`, `has_pv` etc. should be in system_config, but schema lacks these fields
   - What's unclear: Exact field names and defaults per profile
   - Recommendation: Add optional boolean fields `has_dg`, `has_pv`, `has_btms`, `has_meter` under a new `subsystems` object in system_config. Default all to true for container, all to false for residential except `has_meter`.

3. **data_manager C process role after startup**
   - What we know: C process creates shm and must keep it alive (shm exists as long as at least one fd or mapping exists)
   - What's unclear: Whether C process should do anything after creation or just sleep
   - Recommendation: C process creates shm, writes header, then either (a) blocks on a signal waiting for shutdown, or (b) runs a simple health loop. Python data_manager process handles all ZMQ publishing. The C process only exists to own shm lifecycle cleanly.

4. **Schema version field format**
   - What we know: CONF-03 requires schema version validation
   - What's unclear: Whether this means a new field in each YAML or matching against the `$id` in the JSON Schema
   - Recommendation: Add `_schema_version: "1.0"` field at root of each YAML. Each JSON Schema defines this as a const. Config_manager checks this first and provides migration guidance if mismatched.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_config_manager.py tests/test_data_manager.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONF-01 | All 14 YAML load and validate at startup | unit | `uv run pytest tests/test_config_manager.py::test_startup_all_valid -x` | Wave 0 |
| CONF-01 | Invalid config fails fast with clear error | unit | `uv run pytest tests/test_config_manager.py::test_startup_invalid_fails_fast -x` | Wave 0 |
| CONF-02 | Hot-reload detects change within 1s | integration | `uv run pytest tests/test_config_manager.py::test_hot_reload_within_1s -x` | Wave 0 |
| CONF-02 | Hot-reload validates before applying | unit | `uv run pytest tests/test_config_manager.py::test_hot_reload_rejects_invalid -x` | Wave 0 |
| CONF-03 | Schema version mismatch rejected | unit | `uv run pytest tests/test_config_manager.py::test_schema_version_mismatch -x` | Wave 0 |
| CONF-04 | Backup created before hot-reload | unit | `uv run pytest tests/test_config_manager.py::test_backup_on_reload -x` | Wave 0 |
| CONF-05 | get_config returns full config | unit | `uv run pytest tests/test_config_manager.py::test_query_get_config -x` | Wave 0 |
| CONF-05 | get_value returns dotted path value | unit | `uv run pytest tests/test_config_manager.py::test_query_get_value -x` | Wave 0 |
| CONF-05 | Missing path returns error | unit | `uv run pytest tests/test_config_manager.py::test_query_missing_path_error -x` | Wave 0 |
| CONF-06 | Profile overlay loads correct files | unit | `uv run pytest tests/test_config_manager.py::test_profile_overlay -x` | Wave 0 |
| CONF-07 | CLI validate returns pass/fail | unit | `uv run pytest tests/test_config_manager.py::test_cli_validate -x` | Wave 0 |
| CONF-08 | Config diff in reload event | unit | `uv run pytest tests/test_config_manager.py::test_reload_event_includes_diff -x` | Wave 0 |
| DATA-01 | shm created with correct size | unit | `uv run pytest tests/test_data_manager.py::test_shm_create -x` | Wave 0 |
| DATA-02 | RTDB initialized with magic/version/topology | unit | `uv run pytest tests/test_data_manager.py::test_rtdb_init_header -x` | Wave 0 |
| DATA-03 | C and Python can attach/detach | integration | `uv run pytest tests/test_data_manager.py::test_c_python_attach -x` | Wave 0 |
| DATA-04 | Topology counts match config | unit | `uv run pytest tests/test_data_manager.py::test_topology_from_config -x` | Wave 0 |
| DATA-05 | 1Hz ZMQ PUB publishes all sections | integration | `uv run pytest tests/test_data_manager.py::test_zmq_pub_1hz -x` | Wave 0 |
| DATA-06 | Stale section detected and warned | unit | `uv run pytest tests/test_data_manager.py::test_health_stale_detection -x` | Wave 0 |
| DATA-07 | systemd unit file has correct After= | unit | `uv run pytest tests/test_data_manager.py::test_systemd_ordering -x` | Wave 0 |
| DATA-08 | Snapshot written to disk at interval | unit | `uv run pytest tests/test_data_manager.py::test_snapshot_periodic -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_config_manager.py tests/test_data_manager.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_config_manager.py` -- covers CONF-01 through CONF-08
- [ ] `tests/test_data_manager.py` -- covers DATA-01 through DATA-08 (note: `tests/test_rtdb.py` exists but covers M0 struct matching, not runtime lifecycle)
- [ ] pyzmq dependency: `uv add pyzmq --package ems-config-manager && uv add pyzmq --package ems-data-manager`
- [ ] inotify-simple dependency: `uv add inotify-simple --package ems-config-manager`
- [ ] SOCK_CONFIG added to ipc.py and ipc_defs.h

## Sources

### Primary (HIGH confidence)
- Codebase inspection: all 14 JSON Schemas in `config/schemas/`, all configs in `config/`, `tools/validate_config.py`, `src/common/c/include/rtdb.h`, `src/common/c/include/seqlock.h`, `src/common/python/src/ems_common/rtdb.py`, `src/common/python/src/ems_common/ipc.py`
- Existing tests: `tests/test_config_validation.py` (21 tests), `tests/test_rtdb.py` (9 tests), `tests/test_ipc_contracts.py` (IPC interop)
- jsonschema 4.26.0 installed and tested with Draft 2020-12
- msgpack 1.1.2 installed and tested with C interop
- PyYAML 6.0.3 installed

### Secondary (MEDIUM confidence)
- Python 3.12 SharedMemory resource_tracker behavior (documented in CPython issue tracker, workaround validated in STATE.md)
- inotify_simple API (simple Linux wrapper, well-understood behavior)
- pyzmq asyncio API (widely used, documented in pyzmq docs)

### Tertiary (LOW confidence)
- None -- all recommendations are based on existing codebase patterns and installed libraries

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in use or well-established for the use case
- Architecture: HIGH -- patterns follow existing M0 scaffolding and locked decisions from CONTEXT.md
- Pitfalls: HIGH -- based on real codebase inspection (resource_tracker issue documented, seqlock already implemented, inotify behavior well-known)

**Research date:** 2026-03-13
**Valid until:** 2026-04-13 (stable domain, no fast-moving dependencies)
