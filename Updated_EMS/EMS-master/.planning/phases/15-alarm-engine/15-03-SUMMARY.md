---
phase: 15-alarm-engine
plan: "03"
subsystem: alarm_manager
tags: [alarm-manager, loop, rtdb, zmq, tdd, iec-62682, entry-point]
dependency_graph:
  requires: [15-01, 15-02]
  provides: [AlarmLoop, alarm_manager_entry_point]
  affects: [16-01]
tech_stack:
  added: []
  patterns: [TDD, seqlock-read-copy, ctypes-in-process-mock, zmq-rep-drain, rtdb-copy-helper]
key_files:
  created:
    - src/alarm_manager/src/ems_alarm_manager/loop.py
    - src/alarm_manager/src/ems_alarm_manager/__main__.py
    - src/alarm_manager/tests/test_loop.py
  modified: []
key_decisions:
  - SOCK_ALARM_PUB defined locally in loop.py (ipc:///run/ems/alarm_pub.sock) — not added to ipc.py, follows Phase 14 precedent
  - _seqlock_read_section duplicated in loop.py (same as control_manager) — deferred refactor to ems_common
  - _RtdbCopy/_ClusterCopy/_RackCopy hierarchy built each tick to give resolver a ctypes-compatible view without exposing live shm to resolver
  - SignalResolver.validate_paths called at startup — unknown paths log ERROR and disable affected rules (fail-open per CONTEXT.md)
  - _dispatch_command returns encoded bytes directly (vs control_manager returning (ok, err) tuple) — cleaner for alarm's richer response shapes
metrics:
  duration_seconds: 173
  completed_date: "2026-03-15"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
  tests_added: 14
  tests_passing: 61
---

# Phase 15 Plan 03: AlarmLoop Wiring Summary

**One-liner:** AlarmLoop class wiring RTDB seqlock reads, SignalResolver, AlarmEvaluator, and three ZMQ sockets (REP/PUSH/PUB) into a 1Hz async loop, with a full entry-point supporting SIGTERM/SIGINT.

## Tasks Completed

| Task | Description | Commit | Tests |
|------|-------------|--------|-------|
| 1 (TDD RED) | test_loop.py — 14 failing tests | 00d6daa | 0/14 |
| 1 (TDD GREEN) | loop.py — AlarmLoop class | 8de5a97 | 14/14 |
| 2 | __main__.py — entry point | ca77138 | --help verified |

## What Was Built

### loop.py — AlarmLoop class

The `AlarmLoop` class drives the full alarm evaluation pipeline at 1Hz:

**`__init__`:**
- `attach_rtdb()` to obtain RTDB shared memory
- `SignalResolver()` + `validate_paths()` at startup — unknown signal paths log ERROR and disable the offending rules (fail-open)
- `build_alarm_instances(config)` + `AlarmEvaluator(instances)` from Plan 02
- Three ZMQ sockets: REP (bind, SOCK_ALARM_CMD), PUSH (connect, SOCK_LOGGER), PUB (bind, SOCK_ALARM_PUB)
- `asyncio.Event()` stop_event for clean shutdown wiring

**`_poll_commands()`:** Non-blocking REP drain dispatching 3 commands:
- `get_active_alarms` → evaluator.get_active_alarms() → `{status: ok, result: {alarms: [...]}}`
- `acknowledge` → evaluator.acknowledge(alarm_id) → `{status: ok, result: {alarm_id, from_state, to_state}}` or `{status: error}`
- `get_alarm_config` → evaluator.get_alarm_config() → `{status: ok, result: {rules: [...]}}`
- Unknown action → `{status: error, error_msg: "Unknown action: ..."}`
- All decode/dispatch exceptions → error reply (REP never hangs)

**`_tick(now_ms)`:**
1. `_seqlock_read_section(rtdb.pcs)` for PCS copy
2. `_RtdbCopy(pcs_copy, rtdb)` — builds per-rack seqlock copies for all 8×16 racks wrapped in a resolver-compatible object
3. `resolver.resolve_all(signal_paths, rtdb_copy)` → values dict
4. `evaluator.evaluate_tick(values, now_ms)` → events list
5. For each event: PUSH via `encode_event(source="alarm_manager", event_type=TOPIC_ALARM)` + PUB via `send_string(TOPIC_ALARM) + send(msgpack_payload)`; both use `zmq.NOBLOCK` with `zmq.Again` catch

**RTDB copy hierarchy:** `_RtdbCopy` → `_ClusterCopy` (list of _RackCopy) + `pcs` copy. Each `_RackCopy` reads its rack via `_seqlock_read_section` and extracts the 6 resolver-needed fields. This keeps live shm out of the resolver's hands.

**`run()`:** 1Hz asyncio loop with timing-corrected sleep (identical to ControlLoop pattern).

**`cleanup()`:** Closes REP, PUSH, PUB sockets; terminates ZMQ context; detaches RTDB.

### __main__.py — Entry point

Follows exact pattern from `ems_control_manager/__main__.py`:
- `parse_args()`: `--config` (Path, default `config/alarms_config.yaml`) + `--log-level`
- `run()`: async — `load_alarm_config()`, `AlarmLoop()`, wire SIGTERM/SIGINT to `stop_event.set()`
- `main()`: `logging.basicConfig`, `asyncio.run(run(args))`, KeyboardInterrupt guard
- `python -m ems_alarm_manager --help` verified working

### test_loop.py — 14 integration tests

| Class | Tests | Coverage |
|-------|-------|----------|
| TestPushEventPublishing | 4 | PUSH event content, PUB topic prefix, all 3 severities |
| TestZmqRepCommandApi | 7 | get_active_alarms (empty + active), acknowledge (ok + rejected), get_alarm_config, unknown, malformed |
| TestRtdbIntegration | 2 | signal read verification, all-offline no-alarm |
| TestAlarmLifecycle | 1 | activate → ack → clear → RTN → NORMAL |

All tests use `tcp://127.0.0.1:156XX` ports (15610-15672 range) to avoid conflicts with control_manager tests.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files

- src/alarm_manager/src/ems_alarm_manager/loop.py — EXISTS
- src/alarm_manager/src/ems_alarm_manager/__main__.py — EXISTS
- src/alarm_manager/tests/test_loop.py — EXISTS

### Commits

- 00d6daa — test(15-03): RED — AlarmLoop integration tests
- 8de5a97 — feat(15-03): AlarmLoop — 1Hz RTDB reads, signal resolution, ZMQ I/O
- ca77138 — feat(15-03): alarm_manager entry point with SIGTERM/SIGINT handling

## Self-Check: PASSED
