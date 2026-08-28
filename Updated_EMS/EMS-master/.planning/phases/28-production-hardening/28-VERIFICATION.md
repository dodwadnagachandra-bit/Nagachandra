---
phase: 28-production-hardening
verified: 2026-03-16T00:00:00Z
status: passed
score: 14/14 must-haves verified
re_verification: false
---

# Phase 28: Production Hardening Verification Report

**Phase Goal:** All services hardened for production, tech debt resolved, HMI updated with cloud/OTA status
**Verified:** 2026-03-16
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth                                                                                             | Status     | Evidence                                                                 |
|----|---------------------------------------------------------------------------------------------------|------------|--------------------------------------------------------------------------|
| 1  | WebSocket bridge subscribes to SOCK_CLOUD_PUB and SOCK_OTA_PUB alongside SOCK_TELEMETRY         | VERIFIED   | ws.py uses zmq.asyncio.Poller; SOCK paths passed from app.py via app.state |
| 2  | Cloud and OTA telemetry messages broadcast to all WebSocket clients with {topic, data, ts} envelope | VERIFIED | ws.py poll loop decodes all 3 sources; topic/data/ts envelope confirmed |
| 3  | PUT /api/config/schedule validates body against JSON Schema and writes YAML                       | VERIFIED   | schedule.py: jsonschema.validate + yaml.dump to schedule_config.yaml      |
| 4  | PUT /api/config/schedule returns 403 for non-admin and 422 for invalid body                       | VERIFIED   | Depends(require_admin) -> 403; HTTPException(422) on ValidationError      |
| 5  | websocket_port field removed from schema and config (schema v2.0)                                 | VERIFIED   | hmi_config.schema.json const "2.0"; hmi_config.yaml _schema_version: "2.0"; no websocket_port present |
| 6  | TelemetryContext reducer routes 'cloud' topic to state.cloud field                               | VERIFIED   | TelemetryContext.tsx line 54: cloud: data as unknown as CloudTelemetry    |
| 7  | TelemetryContext reducer routes 'ota' topic to state.ota field                                   | VERIFIED   | TelemetryContext.tsx line 57: ota: data as unknown as OtaTelemetry        |
| 8  | Cloud status green/red dot displayed in sidebar footer next to ConnectionIndicator               | VERIFIED   | Sidebar.tsx imports CloudStatusIndicator; renders at line 95 next to ConnectionIndicator |
| 9  | OTA status badge displayed in Settings screen (admin-only)                                        | VERIFIED   | SettingsScreen.tsx: OtaStatusSection component with state-dependent badge colors |
| 10 | Settings screen handleSave calls PUT /api/config/schedule and shows success/error feedback        | VERIFIED   | SettingsScreen.tsx line 119: fetch("/api/config/schedule", {method:"PUT"}) |
| 11 | All 16 service files contain ProtectSystem=strict                                                 | VERIFIED   | grep -l confirmed 16/16 service files                                    |
| 12 | All Python services have MemoryMax=256M and CPUQuota=50%                                          | VERIFIED   | 13 Python services confirmed; cloud_manager.service checked as sample     |
| 13 | safety_manager has MemoryMax=64M, DeviceAllow for GPIO/watchdog, NoNewPrivileges=no               | VERIFIED   | safety_manager.service lines 37-47 confirmed all three properties         |
| 14 | ota_manager and comm_manager ExecStart fixed to use venv path (not uv run)                       | VERIFIED   | Both use /opt/ems/python/.venv/bin/python; 0 files contain "uv run"       |

**Score:** 14/14 truths verified

### Required Artifacts

| Artifact                                                                    | Expected                                       | Status     | Details                                              |
|-----------------------------------------------------------------------------|------------------------------------------------|------------|------------------------------------------------------|
| `src/hmi_server/src/ems_hmi_server/ws.py`                                  | Multi-source ZMQ poller bridge                 | VERIFIED   | Uses zmq.asyncio.Poller; 3-socket poll loop          |
| `src/hmi_server/src/ems_hmi_server/schedule.py`                            | PUT /api/config/schedule endpoint              | VERIFIED   | 99 lines; jsonschema validate, yaml.dump, require_admin |
| `src/hmi_server/tests/test_schedule.py`                                    | Schedule endpoint tests                        | VERIFIED   | 4 test functions: valid, invalid, forbidden, yaml-write |
| `config/schemas/hmi_config.schema.json`                                    | Schema v2.0 without websocket_port             | VERIFIED   | const "2.0" present; websocket_port absent           |
| `src/hmi_server/frontend/src/types/telemetry.ts`                           | CloudTelemetry, OtaTelemetry interfaces        | VERIFIED   | Lines 85-108: interfaces + TelemetryState fields     |
| `src/hmi_server/frontend/src/components/CloudStatusIndicator.tsx`          | Green/red dot component                        | VERIFIED   | 36 lines; bg-green-500/bg-red-500/bg-gray-500        |
| `src/hmi_server/frontend/src/__tests__/CloudStatusIndicator.test.tsx`      | Tests for cloud status indicator               | VERIFIED   | 3 tests: null/connected/disconnected                 |
| `deploy/systemd/safety_manager.service`                                    | Tiered hardening with DeviceAllow, CapabilityBoundingSet | VERIFIED | MemoryMax=64M, DeviceAllow x3, CapabilityBoundingSet |
| `deploy/systemd/cloud_manager.service`                                     | Standard Python service hardening              | VERIFIED   | ProtectSystem=strict, MemoryMax=256M, CPUQuota=50%   |
| `deploy/systemd/ota_manager.service`                                       | Fixed ExecStart + hardening                    | VERIFIED   | /opt/ems/python/.venv/bin/python confirmed           |

### Key Link Verification

| From                                        | To                                      | Via                                               | Status   | Details                                                    |
|---------------------------------------------|-----------------------------------------|---------------------------------------------------|----------|------------------------------------------------------------|
| `ws.py`                                     | `ems_common.ipc`                        | imports TOPIC_CLOUD, TOPIC_OTA                    | WIRED    | Lines 22-25; SOCK paths passed through app.py (correct arch split) |
| `app.py`                                    | `ws.telemetry_bridge`                   | passes cloud_socket and ota_socket                | WIRED    | Lines 52-62: SOCK_CLOUD_PUB/SOCK_OTA_PUB resolved and passed |
| `schedule.py`                               | `config/schemas/schedule_config.schema.json` | jsonschema.validate against schema file       | WIRED    | Line 30: resolves schedule_config.schema.json              |
| `Sidebar.tsx`                               | `CloudStatusIndicator.tsx`              | renders CloudStatusIndicator in footer            | WIRED    | Line 13 (import) + line 95 (render)                        |
| `TelemetryContext.tsx`                      | `telemetry.ts`                          | imports CloudTelemetry, OtaTelemetry              | WIRED    | Lines 6, 10: imports confirmed                             |
| `SettingsScreen.tsx`                        | `/api/config/schedule`                  | fetch PUT on handleSave                           | WIRED    | Line 119: fetch("/api/config/schedule", {method:"PUT"})    |
| `safety_manager.service`                    | `/dev/gpiochip0, /dev/watchdog`         | DeviceAllow directives                            | WIRED    | Lines 42-44: DeviceAllow=/dev/gpiochip0, /dev/gpiochip1, /dev/watchdog |
| `comm_manager.service`                      | `/dev/ttyS*, serial`                    | SupplementaryGroups=dialout (preserved)           | WIRED    | Line 15: SupplementaryGroups=dialout confirmed             |

### Requirements Coverage

| Requirement | Source Plan | Description                                                                                             | Status    | Evidence                                                          |
|-------------|-------------|--------------------------------------------------------------------------------------------------------|-----------|-------------------------------------------------------------------|
| PROD-05     | 28-01, 28-02 | HMI cloud/OTA status integration — subscribe to SOCK_CLOUD_PUB/SOCK_OTA_PUB in WebSocket bridge, add status indicators | SATISFIED | ws.py multi-source poller; CloudStatusIndicator in Sidebar; OTA badge in Settings; schedule save wired |
| PROD-06     | 28-03       | Production systemd hardening — ProtectSystem=strict, PrivateTmp, NoNewPrivileges, MemoryMax per service | SATISFIED | 16/16 service files hardened; tiered memory limits; ExecStart paths fixed |

No orphaned requirements — both PROD-05 and PROD-06 are claimed by plans and verified satisfied.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `SettingsScreen.tsx` | 209, 221, 293, 305 | `placeholder="HH:MM"` | INFO | HTML input placeholder attributes — not code stubs, expected |

No blockers or warnings found.

### Human Verification Required

#### 1. Cloud dot visibility in sidebar

**Test:** Open the EMS HMI in a browser with the backend running. Look at the sidebar footer.
**Expected:** A colored dot labeled "Cloud" appears next to the connection indicator. It shows gray when no cloud telemetry has been received, green when connected, red when disconnected.
**Why human:** Visual layout and Tailwind breakpoint behavior (xl-only label) cannot be verified programmatically.

#### 2. OTA badge state transitions in Settings

**Test:** Navigate to Settings screen as admin. Check the OTA Status section at the top. Observe when ota_manager publishes different states (idle, downloading, failed).
**Expected:** Badge color changes (gray for idle, blue+pulse for downloading/verifying/applying, amber for rebooting, red for failed). Firmware version displayed when available.
**Why human:** Dynamic badge animation and real-time state transitions require live system.

#### 3. Schedule save round-trip with hot-reload

**Test:** In Settings, modify schedule time windows and click Save. Check config_manager log for hot-reload event.
**Expected:** PUT succeeds with "Schedule saved successfully" toast. config_manager detects inotify change on schedule_config.yaml and reloads.
**Why human:** Hot-reload integration requires running config_manager and inotify subsystem.

#### 4. Systemd service hardening on target hardware

**Test:** On Advantech ECU-1170-552A running Yocto, start all services via systemctl. Check journald for any capability or namespace errors.
**Expected:** All 16 services start successfully. safety_manager has RT scheduling (CAP_SYS_NICE). comm_manager_c has CAN access (CAP_NET_RAW). No sandboxing errors.
**Why human:** ProtectSystem=strict and device namespace behavior can only be fully validated on target hardware.

### Gaps Summary

No gaps. All 14 observable truths verified against the codebase. All artifacts exist with substantive implementations (no stubs). All key links confirmed wired. Both PROD-05 and PROD-06 requirements are satisfied.

One notable architectural note: `SOCK_CLOUD_PUB` and `SOCK_OTA_PUB` constants are imported in `app.py` rather than `ws.py`. The PLAN key link listed these in `ws.py`, but the actual implementation correctly separates concerns — `ws.py` handles topic names (`TOPIC_CLOUD`, `TOPIC_OTA`) while `app.py` owns socket address resolution. This is a sound architectural choice and does not represent a gap.

Two pre-existing test failures in `test_app.py` (SPA fallback routing, logged in `deferred-items.md`) are out of scope for Phase 28 and were present before this phase began.

---

_Verified: 2026-03-16_
_Verifier: Claude (gsd-verifier)_
