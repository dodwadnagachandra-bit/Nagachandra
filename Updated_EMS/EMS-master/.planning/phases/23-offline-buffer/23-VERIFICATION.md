---
phase: 23-offline-buffer
verified: 2026-03-15T00:00:00Z
status: passed
score: 9/9 must-haves verified
gaps: []
---

# Phase 23: Offline Buffer Verification Report

**Phase Goal:** Telemetry and events are buffered to local disk during MQTT outages and replayed FIFO when connectivity returns
**Verified:** 2026-03-15
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | BufferManager writes JSONL records to hourly files under buffer_dir/{year}/{month}/{day}/cloud_{hour}.jsonl | VERIFIED | `buffer.py` line 103: `json.dumps(record) + "\n"` appended to path derived via `_current_file_path_for(now)` at `buffer_dir/YYYY/MM/DD/cloud_HH.jsonl`; `test_buffer_write_creates_jsonl` and `test_buffer_rotation` pass |
| 2 | Retention enforces max_hours by deleting files older than the threshold based on path date components | VERIFIED | `buffer.py` `_enforce_retention()` lines 220-227: age computed from `_file_datetime_from_path()` path components (not mtime), files deleted when `age_hours > self._max_hours`; `test_retention_hours` passes |
| 3 | Retention enforces max_mb by deleting oldest files until total buffer size is under limit | VERIFIED | `buffer.py` `_enforce_retention()` lines 230-233: while-loop deletes oldest files until `_total_size_mb() <= max_mb`; `test_retention_mb` and `test_retention_dual` pass |
| 4 | Crash recovery skips truncated last lines when reading JSONL files (json.JSONDecodeError) | VERIFIED | `buffer.py` `drain()` lines 136-143: `json.loads(line)` wrapped in `except json.JSONDecodeError` with debug log; `test_buffer_crash_recovery` passes |
| 5 | When MQTT disconnects, telemetry and events are routed to BufferManager instead of MQTT publish | VERIFIED | `buffered_loop.py` `_do_publish_telemetry()` lines 82-85 and `_do_publish_event()` lines 94-97: branch on `self._publisher.connected`; `test_telemetry_routes_to_buffer_when_offline` and `test_event_routes_to_buffer_when_offline` pass; connection guards removed from `_periodic_publish` and `_zmq_event_forwarder` overrides |
| 6 | When MQTT reconnects, buffer is flushed then drained FIFO at 10 msg/s throttle | VERIFIED | `buffered_loop.py` `_buffer_replay_task()` lines 241-272: `self._buffer.flush()` called before drain, `asyncio.sleep(1.0 / self._replay_rate)` with default `_replay_rate = 10.0`; `test_flush_before_replay` and `test_replay_throttle` pass |
| 7 | Replay stops immediately if MQTT disconnects mid-drain and resumes on next reconnect | VERIFIED | `_buffer_replay_task()` checks `self._publisher.connected` before each record and at file boundaries (lines 246, 251); `test_replay_stops_on_disconnect` and `test_replay_resumes_on_reconnect` pass |
| 8 | Buffer progress (files_remaining, mb_remaining) is published on ZMQ cloud status topic | VERIFIED | `buffered_loop.py` `_publish_buffer_status()` lines 291-321: encodes `buffer_files_remaining` and `buffer_mb_remaining` via `encode_telemetry`, sends on `publisher._cloud_pub` ZMQ socket with topic `"cloud_buffer"`; `test_buffer_status_published` passes |
| 9 | cloud_manager __main__.py creates BufferedCloudLoop when offline_buffer.enabled is true | VERIFIED | `__main__.py` lines 91-125: reads `config.get("offline_buffer", {})`, constructs `BufferManager` + `BufferedCloudLoop` when `enabled` is True; `EMS_CLOUD_BUFFER_DIR` env var documented in module docstring; `TestMainWiring` 3 tests pass |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/cloud_manager/src/ems_cloud_manager/buffer.py` | BufferManager class with write, drain, enforce_retention, stats | VERIFIED | 350 lines; exports `BufferManager`; no MQTT/ZMQ deps; all methods present: `write`, `drain`, `flush`, `_enforce_retention`, `_total_size_mb`, `_all_files_sorted`, `stats` property, `_file_datetime_from_path`, `_remove_empty_parents` |
| `src/cloud_manager/src/ems_cloud_manager/buffered_loop.py` | BufferedCloudLoop subclass with offline routing and replay task | VERIFIED | 322 lines; exports `BufferedCloudLoop(CloudLoop)`; overrides `_do_publish_telemetry`, `_do_publish_event`, `_periodic_publish`, `_zmq_event_forwarder`, `run`; adds `_buffer_replay_task`, `_publish_buffer_status` |
| `src/cloud_manager/src/ems_cloud_manager/__main__.py` | Entry point wiring with BufferManager + BufferedCloudLoop | VERIFIED | Lines 29-30 import both; lines 91-125 conditional wiring; `EMS_CLOUD_BUFFER_DIR` documented in module docstring |
| `tests/test_cloud_manager.py` | TestBufferManager + TestBufferedCloudLoop + TestMainWiring test classes | VERIFIED | All three classes present at lines 1159, 1626, 2183; 13 + 13 + 3 = 29 new tests covering phase 23 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `buffer.py::write()` | JSONL files on disk | `json.dumps(record) + "\n"` + file append + periodic fsync | VERIFIED | Line 103: `json.dumps(record, separators=(",", ":")) + "\n"`; fsync at line 110 via `_sync()` every `_SYNC_EVERY=100` writes |
| `buffer.py::drain()` | JSONL files on disk | `json.loads` per line, skip `JSONDecodeError` | VERIFIED | Lines 136-143: `json.loads(line)` + `except json.JSONDecodeError` crash recovery |
| `buffer.py::_enforce_retention()` | buffer files sorted oldest-first | path date extraction + unlink + empty parent removal | VERIFIED | Lines 221: `_file_datetime_from_path`; line 226: `_delete_file`; line 246: `_remove_empty_parents` |
| `buffered_loop.py::_do_publish_telemetry()` | `buffer.py::BufferManager.write()` | routes to buffer when not `publisher.connected` | VERIFIED | Line 85: `self._buffer.write("telemetry", "telemetry", payload)` |
| `buffered_loop.py::_do_publish_event()` | `buffer.py::BufferManager.write()` | routes to buffer when not `publisher.connected` | VERIFIED | Line 97: `self._buffer.write("event", topic, payload)` |
| `buffered_loop.py::_buffer_replay_task()` | `buffer.py::BufferManager.drain()` | async loop draining FIFO at 10 msg/s | VERIFIED | Line 245: `for file_path, records in self._buffer.drain():`; line 272: `await asyncio.sleep(_replay_interval)` |
| `buffered_loop.py::_buffer_replay_task()` | `_publish_buffer_status()` | ZMQ PUB with buffer stats appended | VERIFIED | Line 285: `self._publish_buffer_status()` called after each cycle; lines 302-303: `buffer_files_remaining`, `buffer_mb_remaining` |
| `__main__.py` | `buffered_loop.py::BufferedCloudLoop` | conditional construction when `offline_buffer.enabled` | VERIFIED | Lines 93-116: `if buffer_cfg.get("enabled", False): ... loop_obj = BufferedCloudLoop(...)` |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CLOUD-04 | 23-01, 23-02 | Offline buffer stores telemetry and events to local disk when MQTT connection is lost, with configurable retention (max_hours, max_mb per profile) | SATISFIED | `BufferManager` in `buffer.py`: JSONL hourly rotation, dual-constraint retention (age + size), crash recovery; integrated via `BufferedCloudLoop._do_publish_telemetry/event` routing when offline; config reads `max_hours`/`max_mb` from `offline_buffer` section |
| CLOUD-05 | 23-02 | Buffer replay drains offline buffer FIFO when MQTT connection is restored, throttled to prevent broker overload, with progress tracking | SATISFIED | `_buffer_replay_task()` in `buffered_loop.py`: FIFO drain via `BufferManager.drain()`, 10 msg/s throttle via `asyncio.sleep(1/replay_rate)`, progress published via `_publish_buffer_status()` on ZMQ `cloud_buffer` topic; files deleted only after full publish |

All requirement IDs declared in plan frontmatter (CLOUD-04 in 23-01, CLOUD-04+CLOUD-05 in 23-02) are accounted for. No orphaned requirements found for Phase 23 in REQUIREMENTS.md.

---

### Anti-Patterns Found

No anti-patterns detected.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | — | — | — |

Scanned files: `buffer.py`, `buffered_loop.py`, `__main__.py`. No TODOs, FIXMEs, placeholder returns, or empty implementations found.

---

### Human Verification Required

None. All behavioral claims are structurally verifiable and test-covered.

Items that could benefit from operational observation (not blocking):

1. **ZMQ slow-joiner in production** — The `test_event_forwarder_calls_hook_when_offline` test requires a 0.1s settle time before sending events. In production with ipc:// sockets this is handled by systemd ordering but has not been observed on real hardware.
   - Why human: Requires real embedded hardware with paho MQTT broker connection cycling.

2. **10 msg/s replay rate under load** — The throttle is tested with mocked publisher. Under real MQTT broker with QoS 1 ACK latency, replay may behave differently.
   - Why human: Requires a live MQTT broker (Mosquitto).

---

### Gaps Summary

No gaps. All 9 observable truths verified, all 4 required artifacts are substantive and wired, all 8 key links confirmed in source code, all tests pass (58/58 including 29 new Phase 23 tests and no regressions from Phase 22's 29 tests).

---

_Verified: 2026-03-15_
_Verifier: Claude (gsd-verifier)_
