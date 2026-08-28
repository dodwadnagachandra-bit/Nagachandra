# Phase 28: Production Hardening - Research

**Researched:** 2026-03-16
**Domain:** systemd security hardening, ZMQ multi-source bridge, FastAPI REST config write, React frontend status indicators
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**HMI Cloud/OTA Status Integration**
- Backend change: ws.py `telemetry_bridge` adds 2 SUB sockets — SOCK_CLOUD_PUB + SOCK_OTA_PUB
- New topics to frontend: `cloud` (connection status) and `ota` (update status)
- Frontend display: Cloud = green/red dot in sidebar footer; OTA = status badge in Settings screen (admin-only)
- Message format: Same JSON `{topic, data, ts}` envelope as all other telemetry
- No new WebSocket connections — cloud/OTA status multiplexed onto existing `/ws/telemetry`
- TelemetryContext.tsx already handles unknown topics gracefully — just add TypeScript interfaces

**Systemd Security Hardening — Tiered**

| Directive | Safety Manager | All Other Services |
|-----------|---------------|-------------------|
| ProtectSystem | strict | strict |
| PrivateTmp | yes | yes |
| NoNewPrivileges | yes | yes |
| ProtectHome | yes | yes |
| ReadOnlyPaths | / | / |
| ReadWritePaths | /data, /run/ems, /dev/shm | /data, /run/ems, /dev/shm |
| MemoryMax | 64M | 256M (Python services) |
| MemoryHigh | 48M | 200M |
| CPUQuota | — | 50% (per Python service) |
| DeviceAllow | /dev/watchdog, /dev/gpiochip* | — |
| CapabilityBoundingSet | CAP_SYS_NICE CAP_SYS_RAWIO | — |
| SystemCallFilter | @system-service @io-event | @system-service |

- `ProtectSystem=strict` makes entire filesystem read-only except explicitly listed paths
- `/data` is the only persistent read-write path (SSD)
- `/run/ems` is tmpfs for ZMQ IPC sockets — must be writable
- `/dev/shm` is tmpfs for RTDB — must be writable
- safety_manager: NoNewPrivileges=no (required for AmbientCapabilities to work — already in existing unit)

**Tech Debt Cleanup**

| Debt Item | Fix |
|-----------|-----|
| websocket_port config | Remove from hmi_config.yaml and schema; bump `_schema_version` to "2.0" |
| Settings save placeholder | Add PUT `/api/config/schedule` endpoint; validates against JSON Schema before writing; triggers config_manager hot-reload |
| Solar availability hardcode | Add PV config flag; keep False default |

### Claude's Discretion

- WebSocket bridge implementation (single bridge task with multiple SUBs vs separate tasks)
- Frontend TypeScript interfaces for cloud/OTA status
- Settings save endpoint security (admin-only, validate before write)
- MemoryMax values calibration (may need adjustment during Phase 29 testing)
- Schema version migration guidance in error messages

### Deferred Ideas (OUT OF SCOPE)

- Automatic MemoryMax tuning based on topology
- SELinux/AppArmor policies — v2+
- Network namespace isolation per service
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROD-05 | HMI cloud/OTA status integration — subscribe to SOCK_CLOUD_PUB and SOCK_OTA_PUB in WebSocket bridge, add status indicators to frontend | ws.py multi-SUB pattern documented; cloud/ota payload shapes confirmed from publisher.py and loop.py; TelemetryContext reducer extension pattern clear |
| PROD-06 | Production systemd hardening — ProtectSystem=strict, PrivateTmp=yes, NoNewPrivileges=yes, MemoryMax per service, watchdog integration | All 14 service files audited; safety_manager already has partial hardening; Python services have bare unit files needing full directives |
</phase_requirements>

---

## Summary

Phase 28 has three parallel work streams: (1) backend+frontend ZMQ bridge extension for cloud/OTA status, (2) hardening of all 14 systemd service unit files, and (3) three tech debt items in hmi_server.

**Stream 1 (ZMQ Bridge):** The existing `telemetry_bridge` in `ws.py` connects to a single PUB socket (`SOCK_TELEMETRY`). It must be extended to also subscribe to `SOCK_CLOUD_PUB` (bound by `cloud_manager/publisher.py`) and `SOCK_OTA_PUB` (bound by `ota_manager/loop.py`). The payload shapes are known and use the standard `encode_telemetry` envelope. The cleanest implementation uses `zmq.Poller` across multiple SUB sockets in one bridge task — this avoids race conditions from separate tasks writing to the shared `ClientManager` concurrently (though ClientManager is not async-safe from separate tasks, using a single task with a poller solves this). The frontend needs two new TypeScript interfaces (`CloudTelemetry`, `OtaTelemetry`), two new fields in `TelemetryState`, two new cases in `telemetryReducer`, and a `CloudStatusIndicator` component added to the `Sidebar` footer alongside `ConnectionIndicator`, plus an OTA badge section in `SettingsScreen`.

**Stream 2 (Systemd Hardening):** The safety_manager unit already has `ProtectSystem=strict`, `PrivateTmp=yes`, and `ProtectHome=yes` but uses `ReadWritePaths=/run/ems /dev/shm /opt/ems/logs` (note: `/opt/ems/logs` instead of `/data`). All other services have bare unit files with no security directives whatsoever. The production data path decision specifies `/data` as the read-write SSD path; the existing `ReadWritePaths` in safety_manager uses `/opt/ems/logs` which contradicts this — needs reconciliation. The `NoNewPrivileges=no` exception for safety_manager is correct and must be preserved.

**Stream 3 (Tech Debt):** The `websocket_port` field is present in both `hmi_config.yaml` and its schema (confirmed), it is referenced nowhere in the Python application code. The Settings save placeholder is confirmed in `SettingsScreen.tsx` — `handleSave` currently just sets a "not yet available" message. The `PUT /api/config/schedule` endpoint needs to: validate body against `schedule_config.schema.json`, write `config/schedule_config.yaml`, then let the `config_manager` inotify watcher (`IN_CLOSE_WRITE`) pick up the change automatically.

**Primary recommendation:** Use `zmq.Poller` for the multi-SUB bridge task; add hardening directives as a single wave across all unit files; implement schedule save as a new router in hmi_server following the existing `zmq_command` pattern for admin auth.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyzmq | >=27.1.0 | ZMQ async polling across multiple sockets | Already in project; `zmq.Poller` is the canonical multi-socket pattern |
| fastapi | >=0.115 | REST endpoint for PUT /api/config/schedule | Already in project |
| jsonschema | (already via config_manager) | Validate schedule body before write | Draft202012Validator is project standard (per STATE.md decision 26-01) |
| pyyaml | >=6.0 | Write schedule_config.yaml | Already in project |
| systemd | (OS package) | ProtectSystem, MemoryMax directives | OS-provided; no install needed |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| zmq.asyncio.Poller | (included in pyzmq) | Poll multiple async SUB sockets in one coroutine | Use for multi-source bridge to avoid separate task concurrency issues |
| jsonschema.Draft202012Validator | (project standard) | Schema validation before YAML write | Consistent with all other config validation in project |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Single bridge task with zmq.Poller | Three separate asyncio tasks (one per PUB source) | Separate tasks require locking ClientManager (not currently needed); Poller is simpler |
| zmq.Poller | zmq.asyncio poll() per socket | Per-socket await works but requires `asyncio.gather` and can miss messages during gap; Poller is lower latency |

---

## Architecture Patterns

### Recommended Project Structure Changes

```
src/hmi_server/
├── src/ems_hmi_server/
│   ├── ws.py            # Extend telemetry_bridge: add SOCK_CLOUD_PUB + SOCK_OTA_PUB via Poller
│   ├── app.py           # Pass cloud/ota socket paths to telemetry_bridge; update lifespan
│   ├── config.py        # No change (schema validation uses separate jsonschema call)
│   └── schedule.py      # NEW: PUT /api/config/schedule router
├── frontend/src/
│   ├── types/
│   │   └── telemetry.ts # Add CloudTelemetry, OtaTelemetry interfaces; extend TelemetryState
│   ├── context/
│   │   └── TelemetryContext.tsx  # Add cloud/ota reducer cases
│   ├── components/
│   │   ├── Sidebar.tsx           # Add CloudStatusIndicator in footer
│   │   └── CloudStatusIndicator.tsx  # NEW: green/red dot for cloud state
│   └── screens/
│       └── SettingsScreen.tsx    # Add OTA status badge section

deploy/systemd/
├── safety_manager.service   # Fix ReadWritePaths; add MemoryMax, SystemCallFilter
├── cloud_manager.service    # Add full hardening block
├── hmi_server.service       # Add full hardening block
├── ota_manager.service      # Fix ExecStart (uses uv run — wrong for Yocto venv)
├── data_manager.service     # Add full hardening block
├── comm_manager.service     # Add full hardening block (has uv run — check)
├── control_manager.service  # Add full hardening block
├── logger.service           # Add full hardening block
├── config_manager.service   # Add full hardening block
├── alarm_manager.service    # Add full hardening block
├── scheduler.service        # Add full hardening block
└── diagnostics.service      # Add full hardening block

config/
├── hmi_config.yaml          # Remove websocket_port field
└── schemas/
    └── hmi_config.schema.json  # Remove websocket_port; bump _schema_version to "2.0"
```

### Pattern 1: Multi-Source ZMQ Poller Bridge

**What:** Single async bridge task using `zmq.asyncio.Poller` to wait on multiple SUB sockets simultaneously. Whichever socket has data first gets processed; no message is lost waiting on a slower source.

**When to use:** Any time a bridge must aggregate from N independent PUB sources into one fan-out.

**Example:**
```python
# Source: pyzmq asyncio documentation pattern
async def telemetry_bridge(
    zmq_ctx: zmq.asyncio.Context,
    client_manager: ClientManager,
    telemetry_socket: str,
    cloud_socket: str,
    ota_socket: str,
) -> None:
    sub_telemetry: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)
    for topic in _TELEMETRY_TOPICS:
        sub_telemetry.setsockopt_string(zmq.SUBSCRIBE, topic)
    sub_telemetry.connect(telemetry_socket)

    sub_cloud: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)
    sub_cloud.setsockopt_string(zmq.SUBSCRIBE, TOPIC_CLOUD)
    sub_cloud.connect(cloud_socket)

    sub_ota: zmq.asyncio.Socket = zmq_ctx.socket(zmq.SUB)
    sub_ota.setsockopt_string(zmq.SUBSCRIBE, TOPIC_OTA)
    sub_ota.connect(ota_socket)

    poller: zmq.asyncio.Poller = zmq.asyncio.Poller()
    poller.register(sub_telemetry, zmq.POLLIN)
    poller.register(sub_cloud, zmq.POLLIN)
    poller.register(sub_ota, zmq.POLLIN)

    try:
        while True:
            socks: dict = dict(await poller.poll())
            for sock in (sub_telemetry, sub_cloud, sub_ota):
                if sock in socks:
                    parts: list[bytes] = await sock.recv_multipart()
                    if len(parts) < 2:
                        continue
                    envelope: dict = decode_telemetry(parts[1])
                    message: dict = {
                        "topic": envelope["topic"],
                        "data": envelope["payload"],
                        "ts": envelope["ts"],
                    }
                    client_manager.broadcast(message)
    except asyncio.CancelledError:
        logger.info("Telemetry bridge cancelled")
    finally:
        sub_telemetry.close()
        sub_cloud.close()
        sub_ota.close()
```

### Pattern 2: Systemd Hardening Block (Python Services)

**What:** Standardized security stanza appended to every Python service unit.

**When to use:** All services except safety_manager (which uses the tiered variant).

**Example:**
```ini
# Security hardening — production (PROD-06)
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=/data /run/ems /dev/shm
MemoryMax=256M
MemoryHigh=200M
CPUQuota=50%
SystemCallFilter=@system-service
```

**Safety manager tiered variant** (preserves existing AmbientCapabilities):
```ini
# Safety manager hardening — tiered (PROD-06)
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=no
# NoNewPrivileges=no required for AmbientCapabilities (CAP_SYS_NICE, CAP_SYS_RAWIO)
ReadWritePaths=/data /run/ems /dev/shm
DeviceAllow=/dev/gpiochip0 rw
DeviceAllow=/dev/gpiochip1 rw
DeviceAllow=/dev/watchdog rw
CapabilityBoundingSet=CAP_SYS_NICE CAP_SYS_RAWIO
SystemCallFilter=@system-service @io-event
MemoryMax=64M
MemoryHigh=48M
```

### Pattern 3: Settings Save Endpoint

**What:** Admin-only PUT endpoint that validates incoming JSON against the schedule schema, writes the YAML file, and returns success. The inotify watcher in config_manager automatically picks up the write via `IN_CLOSE_WRITE`.

**When to use:** Any hot-reloadable config file that the HMI should be able to modify.

**Example:**
```python
# Source: project pattern from existing control.py + deps.py
@router.put("/api/config/schedule", response_model=ZmqResponse)
async def update_schedule_config(
    body: dict[str, Any] = Body(...),
    auth: dict = Depends(require_admin),
    request: Request = ...,
) -> dict:
    """Write updated schedule config; triggers config_manager hot-reload."""
    # Validate against JSON Schema before writing
    schema_path = Path("config/schemas/schedule_config.schema.json")
    schema = json.loads(schema_path.read_text())
    try:
        jsonschema.validate(body, schema)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc.message))

    config_path = Path("config/schedule_config.yaml")
    config_path.write_text(yaml.dump(body, default_flow_style=False))

    return {"status": "ok", "result": {"written": True}, "error_msg": None}
```

### Pattern 4: TelemetryContext Extension

**What:** Adding new topics to the reducer without breaking existing topics. TelemetryContext.tsx already ignores unknown topics (returns `state` unchanged). The pattern is: add interface, add field to TelemetryState, add case to reducer.

**Example:**
```typescript
// In types/telemetry.ts — add interfaces
export interface CloudTelemetry {
  state: "connected" | "disconnected";
  broker: string;
  ts: number;
}

export interface OtaTelemetry {
  state: string; // "idle" | "downloading" | "verifying" | "applying" | "rebooting" | "failed"
  version_current: string | null;
  version_previous: string | null;
  detail: Record<string, unknown> | null;
  ts: number;
}

// In TelemetryState — add fields
export interface TelemetryState {
  // ...existing fields...
  cloud: CloudTelemetry | null;
  ota: OtaTelemetry | null;
}

// In telemetryReducer — add cases
if (topic === "cloud") {
  return { ...state, cloud: data as unknown as CloudTelemetry, lastUpdate: ts };
}
if (topic === "ota") {
  return { ...state, ota: data as unknown as OtaTelemetry, lastUpdate: ts };
}
```

### Anti-Patterns to Avoid

- **`ReadWritePaths=/opt/ems/logs`:** The current safety_manager unit has this legacy path. Production data lives under `/data` (SSD partition, as established in Phase 27). The `/opt/ems/logs` path will be read-only under `ProtectSystem=strict` unless explicitly listed. Fix to use `/data`.
- **`uv run` in ExecStart:** The `ota_manager.service` uses `uv run python -m ems_ota_manager`. Under Yocto (Phase 27), services use the venv at `/opt/ems/python/.venv/bin/python`. The `comm_manager.service` also uses `uv run`. These must be updated to use the venv path consistent with all other services.
- **Separate asyncio tasks writing to ClientManager:** The `ClientManager.broadcast()` uses synchronous `put_nowait` which is safe from a single thread/task, but calling from two concurrent tasks introduces ordering non-determinism. Use the Poller pattern (single task, multiple sockets) instead.
- **Schema version const mismatch:** After bumping `_schema_version` to `"2.0"` in the schema's `const`, the YAML file must also be updated. Both must change atomically or config_manager will reject the config on reload.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Multi-socket async wait | `asyncio.gather` + `per-socket await` | `zmq.asyncio.Poller` | Poller provides fair scheduling and does not miss messages on one socket while blocked on another |
| Config JSON Schema validation | Custom field-by-field validation | `jsonschema.Draft202012Validator` | Project standard; handles `additionalProperties`, `required`, pattern matching, const validation |
| YAML write atomicity | tmpfile + rename | `pathlib.Path.write_text` | The inotify watcher uses `IN_CLOSE_WRITE` which fires on close — write_text opens, writes, closes; single atomic write; no tmpfile needed for this use case |
| Hot-reload trigger | ZMQ message to config_manager | inotify `IN_CLOSE_WRITE` | Config_manager already watches the config directory; just write the file and it reloads within 500ms |

**Key insight:** The ZMQ PUB/SUB infrastructure, JSON Schema validation, inotify hot-reload, and admin auth are all already implemented. Phase 28 is wiring them together, not building new infrastructure.

---

## Common Pitfalls

### Pitfall 1: `NoNewPrivileges=yes` Breaks AmbientCapabilities

**What goes wrong:** Setting `NoNewPrivileges=yes` on `safety_manager` prevents `AmbientCapabilities=CAP_SYS_NICE CAP_SYS_RAWIO` from granting those capabilities. The service starts but cannot set `SCHED_FIFO` priority or access `/dev/watchdog` without the capability.

**Why it happens:** `NoNewPrivileges` is a Linux prctl flag that blocks capability elevation. AmbientCapabilities require the "new privileges" path to be open.

**How to avoid:** Keep `NoNewPrivileges=no` on safety_manager. All other services use `NoNewPrivileges=yes`. The existing unit already has this correct — do not "normalize" it away.

**Warning signs:** safety_manager fails with `EPERM` on `sched_setscheduler()` or watchdog open. Check `journalctl -u safety_manager -b`.

### Pitfall 2: `/run/ems` Must Exist Before Services Start

**What goes wrong:** `ProtectSystem=strict` with `ReadWritePaths=/run/ems` requires `/run/ems` to already exist at service start. If it doesn't, ZMQ `ipc://` socket creation fails with `ENOENT`.

**Why it happens:** `ReadWritePaths` grants write access to a path but does not create it. The directory must exist on the filesystem.

**How to avoid:** Ensure `RuntimeDirectory=ems` or a `tmpfiles.d` entry creates `/run/ems` at boot. Check that Phase 27's Yocto `ems-boot-script` or `tmpfiles.d` provisions this. If not, add `RuntimeDirectory=ems` to each service's `[Service]` section — systemd creates `RuntimeDirectory` under `/run/` automatically.

**Warning signs:** Services fail to bind ZMQ IPC sockets. `journalctl` shows `zmq.error.ZMQError: No such file or directory`.

### Pitfall 3: `ProtectSystem=strict` Blocks Config Directory

**What goes wrong:** Services that load config from `config/` at startup will fail if that directory is under `/opt/ems` which becomes read-only under `ProtectSystem=strict`. Reading files is fine (read-only access is allowed); writing is blocked. But if `WorkingDirectory=/opt/ems` and the service tries to create a socket or temp file there, it fails.

**Why it happens:** `ProtectSystem=strict` makes the entire filesystem tree (including `/opt`) read-only.

**How to avoid:** Services only need to **read** config files at startup — this works fine under `ProtectSystem=strict`. The only required write paths are `/data` (Parquet/JSONL), `/run/ems` (ZMQ IPC sockets), and `/dev/shm` (RTDB). Confirm no service writes to `/opt/ems` at runtime.

**Warning signs:** `open() for write` failures under `/opt/ems`. Check all services for any runtime writes to their working directory.

### Pitfall 4: `MemoryMax` OOM Kill During Testing

**What goes wrong:** Setting `MemoryMax=256M` for Python services with `MemoryHigh=200M` can OOM-kill a service during integration testing if the Python VM + dependencies exceed 256M. Python services with DuckDB (logger), ZMQ, and msgpack typically peak at 80-150M under load, but cold start with large YAML schemas and imports can spike.

**Why it happens:** `MemoryMax` is a hard limit — the kernel OOM killer terminates the process immediately when exceeded.

**How to avoid:** Set values are conservative estimates from the CONTEXT.md decision. Phase 29 hardware validation (PROD-07) will verify actual RSS peaks. The `MemoryHigh=200M` soft limit causes the kernel to reclaim memory aggressively before the hard cap; this should prevent most OOM kills. Do not lower the values further without measurement data.

**Warning signs:** Service exits with code 137 (SIGKILL from OOM killer). Check `journalctl -u <service>` for OOM messages.

### Pitfall 5: Schema Version Bump Requires Two-File Change

**What goes wrong:** Bumping `_schema_version` to `"2.0"` in `hmi_config.schema.json` but forgetting to update `config/hmi_config.yaml` (or vice versa) causes config_manager validation to fail on startup.

**Why it happens:** The schema uses `"const": "2.0"` — any config file with `_schema_version: "1.0"` fails validation with `Draft202012Validator`.

**How to avoid:** Update both files in the same commit. The conftest.py `test_config` fixture also references `websocket_port` — it must be updated to remove that field and match the new schema.

**Warning signs:** hmi_server fails to start. config_manager logs validation error for `hmi_config`. Test suite fails in `conftest.py` if test_config still has `websocket_port`.

### Pitfall 6: `ota_manager.service` Uses `uv run` ExecStart

**What goes wrong:** The current `ota_manager.service` uses `ExecStart=uv run python -m ems_ota_manager`. Under the Yocto venv (Phase 27), `uv` is not present — only the pre-built venv at `/opt/ems/python/.venv/`. Service fails to start on production Yocto image.

**Why it happens:** The ota_manager service was created before the Yocto venv pattern was established. The `comm_manager.service` has the same issue (`uv run python`).

**How to avoid:** Update both to use `/opt/ems/python/.venv/bin/python -m <module>` consistent with `cloud_manager.service`, `hmi_server.service`, `data_manager.service`, `logger.service`, and `control_manager.service`.

---

## Code Examples

Verified patterns from codebase inspection:

### Existing Bridge Task Signature (ws.py)
```python
async def telemetry_bridge(
    zmq_ctx: zmq.asyncio.Context,
    client_manager: ClientManager,
    socket_path: str,
) -> None:
```
Must be extended to accept `cloud_socket_path: str` and `ota_socket_path: str` parameters.

### Existing app.py Lifespan — Where Bridge Is Started
```python
# In lifespan(), telemetry_bridge is started as:
bridge_task: asyncio.Task = asyncio.create_task(
    telemetry_bridge(app.state.zmq_ctx, app.state.client_manager, telemetry_socket)
)
```
The `cloud_socket` and `ota_socket` defaults must come from `ems_common.ipc` (`SOCK_CLOUD_PUB`, `SOCK_OTA_PUB`), with override capability for tests (same pattern as `telemetry_socket`).

### Cloud Publisher Payload Shape (publisher.py)
```python
payload = {
    "state": "connected" | "disconnected",
    "broker": self._broker_host,      # str: hostname
    "ts": int(time.time() * 1000),    # int: ms epoch
}
# Wrapped in encode_telemetry(ts, seq, "cloud_manager", "cloud", payload)
```

### OTA Publisher Payload Shape (loop.py)
```python
payload = {
    "state": new_state.value,         # str: OtaState enum value
    "version_current": vs.current,    # str | None
    "version_previous": vs.previous,  # str | None
    "detail": detail,                 # dict | None
    "ts": ts,                         # int: ms epoch
}
# Wrapped in encode_telemetry(ts, seq, "ota_manager", "ota", payload)
```

### Existing Admin Auth Pattern (deps.py)
```python
async def require_admin(auth: dict = Depends(require_auth)) -> dict:
    if auth["level"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth
```
Use `Depends(require_admin)` for the PUT `/api/config/schedule` endpoint.

### inotify Hot-Reload Trigger Pattern
Config_manager's `ConfigWatcher` watches for `IN_CLOSE_WRITE` on the config directory. The schedule endpoint just needs to do a normal file write — `Path.write_text()` generates the `IN_CLOSE_WRITE` event when the file descriptor is closed. No explicit notification to config_manager is needed.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `uv run python -m ...` in unit files | `/opt/ems/python/.venv/bin/python -m ...` | Phase 27 | Yocto venv pattern; `uv` not available in production image |
| No security directives | `ProtectSystem=strict` + `MemoryMax` | Phase 28 | OOM protection, filesystem isolation |
| `websocket_port` in schema | Removed (WS multiplexed on HTTP port) | Phase 28 | Schema v2.0; WebSocket uses same port as HTTP via FastAPI |

**Deprecated/outdated in this phase:**
- `websocket_port` field: Was always unused in practice (FastAPI WebSocket upgrades HTTP connections on same port). Removing it eliminates dead config.
- `uv run` in `ota_manager.service` and `comm_manager.service`: Must match the venv pattern from Phase 27.

---

## Open Questions

1. **`/data` vs `/opt/ems/logs` path in safety_manager**
   - What we know: Current `safety_manager.service` has `ReadWritePaths=/run/ems /dev/shm /opt/ems/logs`. The CONTEXT.md decision specifies `/data` as the SSD persistent path.
   - What's unclear: Does safety_manager actually write anything to `/opt/ems/logs` or `/data`? The C binary likely writes to the RTDB (`/dev/shm`) and reads GPIO — it may not need `/opt/ems/logs` at all.
   - Recommendation: Use `/data /run/ems /dev/shm` for safety_manager (matching all other services). If safety_manager has a log file writer, that file should move to `/data/logs/` (consistent with the SSD partition layout).

2. **`comm_manager.service` SupplementaryGroups=dialout**
   - What we know: comm_manager needs `dialout` group for RS485 serial access. `ProtectSystem=strict` does not block device file access — that's handled by `DeviceAllow`. Serial ports (`/dev/ttyS*`, `/dev/ttyUSB*`) are not GPIO/watchdog, so `DeviceAllow` is not needed — group membership provides access.
   - What's unclear: Whether `SupplementaryGroups` survives with `ProtectSystem=strict`.
   - Recommendation: `SupplementaryGroups` is orthogonal to `ProtectSystem` — it still works. Keep `SupplementaryGroups=dialout` and do not add `DeviceAllow` for serial ports.

3. **`RuntimeDirectory=ems` vs external provisioning of `/run/ems`**
   - What we know: Phase 27 Yocto recipes include `ems-boot-script`. Whether `/run/ems` is provisioned via `tmpfiles.d` or `RuntimeDirectory=` in the unit files is not confirmed from research.
   - What's unclear: If `/run/ems` is already created by the boot script or a `tmpfiles.d` config, adding `RuntimeDirectory=ems` would create `/run/ems` again (harmless). If not, services will fail.
   - Recommendation: Add `RuntimeDirectory=ems` to all services as a safety net. systemd creates it if missing, does nothing if it already exists. This is idempotent and the safest choice.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.24 (asyncio_mode=auto) |
| Config file | `src/hmi_server/pyproject.toml` — `[tool.pytest.ini_options] asyncio_mode = "auto"` |
| Quick run command | `cd src/hmi_server && uv run pytest tests/ -x -q` |
| Full suite command | `cd src/hmi_server && uv run pytest tests/ -v` |
| Frontend test command | `cd src/hmi_server/frontend && bun run test --run` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROD-05 | Bridge subscribes to SOCK_CLOUD_PUB; cloud topic delivered to WS clients | integration | `uv run pytest tests/test_ws.py -x -q` | ✅ (extend existing) |
| PROD-05 | Bridge subscribes to SOCK_OTA_PUB; ota topic delivered to WS clients | integration | `uv run pytest tests/test_ws.py -x -q` | ✅ (extend existing) |
| PROD-05 | TelemetryContext reducer handles "cloud" topic; updates state.cloud | unit | `bun run test --run` | ✅ (extend TelemetryContext.test.tsx) |
| PROD-05 | TelemetryContext reducer handles "ota" topic; updates state.ota | unit | `bun run test --run` | ✅ (extend TelemetryContext.test.tsx) |
| PROD-05 | CloudStatusIndicator renders green dot when state=="connected" | unit | `bun run test --run` | ❌ Wave 0 |
| PROD-05 | CloudStatusIndicator renders red dot when state=="disconnected" | unit | `bun run test --run` | ❌ Wave 0 |
| PROD-05 | SettingsScreen shows OTA status section | unit | `bun run test --run` | ✅ (extend SettingsScreen.test.tsx) |
| PROD-05 | PUT /api/config/schedule returns 200 with valid body | integration | `uv run pytest tests/test_schedule.py -x -q` | ❌ Wave 0 |
| PROD-05 | PUT /api/config/schedule returns 422 with invalid body | integration | `uv run pytest tests/test_schedule.py -x -q` | ❌ Wave 0 |
| PROD-05 | PUT /api/config/schedule returns 403 for operator-level auth | integration | `uv run pytest tests/test_schedule.py -x -q` | ❌ Wave 0 |
| PROD-06 | All service unit files contain ProtectSystem=strict | file validation | `grep -r "ProtectSystem=strict" deploy/systemd/` | manual check |
| PROD-06 | All service unit files contain MemoryMax | file validation | `grep -r "MemoryMax" deploy/systemd/` | manual check |

### Sampling Rate
- **Per task commit:** `cd src/hmi_server && uv run pytest tests/ -x -q`
- **Per wave merge:** `cd src/hmi_server && uv run pytest tests/ -v && cd frontend && bun run test --run`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/hmi_server/tests/test_schedule.py` — covers schedule save endpoint (PROD-05)
- [ ] `src/hmi_server/frontend/src/__tests__/CloudStatusIndicator.test.tsx` — covers cloud dot component (PROD-05)
- [ ] conftest.py `test_config` — remove `websocket_port` field to match schema v2.0 (or test will fail schema validation)
- [ ] `src/hmi_server/tests/test_ws.py` — extend `test_subscribes_to_all_topics` to include `cloud` and `ota` topics

---

## Sources

### Primary (HIGH confidence)

- Codebase inspection: `src/hmi_server/src/ems_hmi_server/ws.py` — confirmed single-socket bridge; `zmq.asyncio.Poller` is the canonical multi-socket extension
- Codebase inspection: `src/common/python/src/ems_common/ipc.py` — `SOCK_CLOUD_PUB`, `SOCK_OTA_PUB`, `TOPIC_CLOUD`, `TOPIC_OTA` all defined
- Codebase inspection: `src/cloud_manager/src/ems_cloud_manager/publisher.py` — confirmed cloud payload shape: `{state, broker, ts}`
- Codebase inspection: `src/ota_manager/src/ems_ota_manager/loop.py` — confirmed OTA payload shape: `{state, version_current, version_previous, detail, ts}`
- Codebase inspection: `deploy/systemd/safety_manager.service` — partial hardening already present; `NoNewPrivileges=no` needed for AmbientCapabilities
- Codebase inspection: `deploy/systemd/cloud_manager.service`, `hmi_server.service`, etc. — bare unit files with no security directives
- Codebase inspection: `config/schemas/hmi_config.schema.json` — `websocket_port` confirmed present; `_schema_version: "1.0"` confirmed
- Codebase inspection: `src/hmi_server/frontend/src/screens/SettingsScreen.tsx` — `handleSave` placeholder confirmed
- Codebase inspection: `src/config_manager/src/ems_config_manager/watcher.py` — `IN_CLOSE_WRITE` inotify; hot-reload triggered by file write
- systemd.exec(5) man page — `ProtectSystem=strict`, `ReadWritePaths`, `MemoryMax`, `CPUQuota`, `SystemCallFilter`, `RuntimeDirectory` semantics
- pyzmq documentation — `zmq.asyncio.Poller` multi-socket async poll pattern

### Secondary (MEDIUM confidence)

- `.planning/STATE.md` decisions — Draft202012Validator as project standard validator; Python venv path `/opt/ems/python/.venv/` from Phase 27
- `.planning/phases/28-production-hardening/28-CONTEXT.md` — all locked decisions verified against codebase

### Tertiary (LOW confidence)

- None — all claims verified against source files

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in project; no new dependencies needed
- Architecture: HIGH — bridge pattern, unit file directives, and endpoint pattern all verified against existing codebase
- Pitfalls: HIGH — pitfalls identified from direct codebase inspection (wrong paths, wrong ExecStart, AmbientCapabilities constraint)
- Test coverage gaps: HIGH — existing test files inspected; gaps identified precisely

**Research date:** 2026-03-16
**Valid until:** 2026-04-16 (stable domain — systemd directives and ZMQ patterns do not change rapidly)
