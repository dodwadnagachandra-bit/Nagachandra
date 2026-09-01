# Phase 28: Production Hardening - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning
**Method:** Auto-generated (autonomous decision-making)

<domain>
## Phase Boundary

HMI cloud/OTA status integration, systemd security hardening, and tech debt cleanup. Covers PROD-05, PROD-06. Builds on Phase 27 Yocto rootfs.

</domain>

<decisions>
## Implementation Decisions

### HMI Cloud/OTA Status Integration

How should cloud and OTA status reach the HMI frontend?

**Decision:** Add two ZMQ SUB connections in the HMI WebSocket bridge (ws.py), subscribing to SOCK_CLOUD_PUB and SOCK_OTA_PUB. Frontend gets new topics "cloud" and "ota" via existing WebSocket channel.

| Aspect | Decision |
|--------|----------|
| Backend change | ws.py `telemetry_bridge` adds 2 SUB sockets: SOCK_CLOUD_PUB + SOCK_OTA_PUB |
| New topics to frontend | `cloud` (connection status) and `ota` (update status) |
| Frontend display | Cloud: green/red dot in sidebar footer. OTA: status badge in Settings screen |
| Message format | Same JSON `{topic, data, ts}` envelope as all other telemetry |

Key rules:
- No new WebSocket connections — cloud/OTA status multiplexed onto existing `/ws/telemetry`.
- TelemetryContext.tsx already handles unknown topics gracefully — just add TypeScript interfaces.
- Cloud status indicator next to existing ConnectionIndicator in sidebar footer.
- OTA status shown in Settings screen (admin-only) — not Dashboard (too noisy for operators).

**Rationale:** Reusing the existing WebSocket bridge avoids new connection management. The topic-based multiplexing pattern (Phase 18) handles additional topics without code changes — just subscribe to more PUB sockets in the bridge task.

### Systemd Security Hardening

What security directives should be applied to all EMS services?

**Decision:** Tiered hardening — strictest for safety_manager, standard for all others.

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

Key rules:
- `ProtectSystem=strict` makes the entire filesystem read-only except explicitly listed paths.
- `/data` is the only persistent read-write path (SSD) — Parquet, JSONL, buffer, snapshots.
- `/run/ems` is tmpfs for ZMQ IPC sockets — must be writable.
- `/dev/shm` is tmpfs for RTDB — must be writable.
- MemoryMax kills the service if exceeded — prevents OOM from taking down the whole system.
- Safety_manager gets hardware device access (watchdog, GPIO) — no other service needs it.
- CPUQuota prevents a runaway Python process from starving safety_manager's RT thread.

**Rationale:** systemd security directives are the standard Linux service hardening approach. `ProtectSystem=strict` with explicit `ReadWritePaths` follows the principle of least privilege. MemoryMax prevents cascading OOM (one service's memory leak shouldn't crash the safety system). DeviceAllow restricts hardware access to only the services that need it.

### Tech Debt Cleanup

| Debt Item | Fix | Phase 28 Scope |
|-----------|-----|---------------|
| websocket_port config | Remove from hmi_config.yaml and schema | Yes — dead field |
| Settings save placeholder | Add PUT `/api/config/schedule` endpoint that writes schedule_config.yaml | Yes — enables schedule editing |
| Solar availability hardcode | Add PV meter reading to source_priority when PV is configured | Partial — add config flag, keep False default |

Key rules:
- websocket_port removal is a breaking schema change — bump `_schema_version` to "2.0".
- Settings save endpoint validates new schedule against JSON Schema before writing to disk — triggers config_manager hot-reload.
- Solar availability stays False by default — the flag enables it when PV meter is physically connected.

### Claude's Discretion

- WebSocket bridge implementation (single bridge task with multiple SUBs vs separate tasks)
- Frontend TypeScript interfaces for cloud/OTA status
- Settings save endpoint security (admin-only, validate before write)
- MemoryMax values calibration (may need adjustment during Phase 29 testing)
- Schema version migration guidance in error messages

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/hmi_server/src/ems_hmi_server/ws.py` — WebSocket telemetry bridge (add SUB sockets)
- `src/hmi_server/frontend/src/context/TelemetryContext.tsx` — Topic routing (add cloud/ota)
- `deploy/systemd/*.service` — All 14 service files (add hardening directives)
- `config/schemas/hmi_config.schema.json` — Remove websocket_port field
- `config/schemas/schedule_config.schema.json` — Reference for settings save validation

### Integration Points
- SOCK_CLOUD_PUB → ws.py SUB → WebSocket → TelemetryContext → frontend
- SOCK_OTA_PUB → ws.py SUB → WebSocket → TelemetryContext → frontend
- Settings save: PUT /api/config/schedule → write YAML → config_manager inotify → hot-reload

</code_context>

<deferred>
## Deferred Ideas

- Automatic MemoryMax tuning based on topology (container needs more RAM than residential)
- SELinux/AppArmor policies — v2+
- Network namespace isolation per service — overkill for single-controller

</deferred>

---

*Phase: 28-production-hardening*
*Context gathered: 2026-03-16*
