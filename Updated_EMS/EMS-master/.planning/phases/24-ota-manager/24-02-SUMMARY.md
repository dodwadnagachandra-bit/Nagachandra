---
phase: 24-ota-manager
plan: 02
subsystem: ota
tags: [ota, state-machine, zmq, health-check, a-b-partition, firmware, rollback]

# Dependency graph
requires:
  - phase: 24-ota-manager
    plan: 01
    provides: HttpDownloader, PackageVerifier, PartitionBackend, load_ota_config, IPC constants

provides:
  - OtaState enum with 6 states (IDLE, DOWNLOADING, VERIFYING, APPLYING, REBOOTING, ROLLED_BACK)
  - OtaStateMachine: full update pipeline with on_state_change callback and rollback
  - VersionState: atomic JSON persistence with safe defaults
  - HealthChecker: systemctl polling with timeout and pluggable check_fn
  - OtaManager: ZMQ PUB status + REP commands async loop
  - __main__.py: uv-runnable entry point with signal handling
  - ota_manager.service: updated systemd unit file

affects: [25-diagnostics]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - OtaManager uses poll(timeout=50ms) + asyncio.sleep for non-blocking REP loop (same as cloud_manager NOBLOCK pattern)
    - VersionState atomic write: json + .tmp sibling + os.rename (same as BootFlag pattern from Plan 01)
    - HealthChecker accepts optional check_fn for test isolation (same as HttpDownloader transport= pattern)
    - OtaManager constructor accepts pre-built ota_pub_socket / ota_rep_socket for test injection (avoids ipc path dependency)
    - __main__.py mirrors cloud_manager pattern exactly: parse_args -> async run -> asyncio.run(run(args))

key-files:
  created:
    - src/ota_manager/src/ems_ota_manager/health.py
    - src/ota_manager/src/ems_ota_manager/state_machine.py
    - src/ota_manager/src/ems_ota_manager/loop.py
    - src/ota_manager/src/ems_ota_manager/__main__.py
  modified:
    - src/ota_manager/src/ems_ota_manager/__init__.py (added exports)
    - deploy/systemd/ota_manager.service (updated ExecStart)
    - tests/test_ota_manager.py (added 21 new tests)

key-decisions:
  - "HealthChecker uses optional check_fn Callable[[str], Awaitable[bool]] for test isolation — avoids subprocess in unit tests without requiring extra dependencies"
  - "OtaStateMachine._on_state_change_cb is an attribute (not only constructor param) so tests can set it post-construction: sm._on_state_change_cb = callback"
  - "OtaManager poll(timeout=50ms) on REP socket instead of NOBLOCK recv — allows command_loop to wake up for stop_event without blocking indefinitely; tests add asyncio.sleep(0.05) after req.send() to allow message delivery"
  - "OtaManager accepts pre-built ota_pub_socket/ota_rep_socket for test isolation — avoids binding to ipc:///run/ems/ paths that don't exist in CI"
  - "ZMQ socket cleanup uses linger=0 in tests to prevent ctx.term() from hanging on unread messages"
  - "verify_manifest called with empty sig_bytes when no signature field in manifest — allows mock verifier in tests to assert call was made without real Ed25519 data"

requirements-completed: [OTA-03, OTA-04, OTA-05, OTA-06]

# Metrics
duration: 18min
completed: 2026-03-15
---

# Phase 24 Plan 02: OTA Manager Wire-Up Summary

**OTA state machine (IDLE->DOWNLOADING->VERIFYING->APPLYING->REBOOTING), health checker with rollback, ZMQ PUB/REP loop, and uv-runnable entry point — 42 tests, 21 added**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-03-15T14:51:41Z
- **Completed:** 2026-03-15T15:09:05Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 7

## Accomplishments

- **OtaState enum:** 6 states — IDLE, DOWNLOADING, VERIFYING, APPLYING, REBOOTING, ROLLED_BACK
- **VersionState:** dataclass-like class with `save(path)` and `VersionState.load(path)` class method; atomic tmp+rename write; missing file returns ("unknown", "unknown") defaults
- **HealthChecker:** systemctl is-active polling per service; pluggable `check_fn` for test isolation; `run_health_check()` polls until all pass or timeout expires; returns `bool`
- **OtaStateMachine:** orchestrates HttpDownloader, PackageVerifier, PartitionBackend, HealthChecker; try/except on each stage returns to IDLE with cleanup; `check_post_boot_health()` clears boot flag on success or calls `_do_rollback()` on timeout; `do_manual_rollback()` operator-triggered path
- **OtaManager:** ZMQ PUB binds SOCK_OTA_PUB (state telemetry); ZMQ REP binds SOCK_OTA_CMD (get_version, rollback, start_update); `_maybe_run_post_boot_health()` on startup; graceful stop_event shutdown
- **__main__.py:** parse_args, async run wiring all components, SIGTERM/SIGINT signal handlers, EMS_OTA_PUB_ENDPOINT/EMS_OTA_CMD_ENDPOINT env overrides, cleanup in finally block
- **systemd service:** updated ExecStart to `uv run python -m ems_ota_manager --config /etc/ems/ota_config.yaml`

## Task Commits

Each task was committed atomically following TDD (RED then GREEN):

1. **TDD RED Task 1** — `7606f2d` (test: add failing tests for state machine, health checker, version persistence)
2. **Task 1 GREEN** — `66ce00f` (feat: state machine, health checker, and version persistence)
3. **TDD RED Task 2** — `749f4fe` (test: add failing tests for OtaManager loop, commands, startup, entry point)
4. **Task 2 GREEN** — `8a67c3e` (feat: OTA loop, entry point, package exports, and systemd service)

## Files Created/Modified

- `src/ota_manager/src/ems_ota_manager/health.py` — HealthChecker (109 lines)
- `src/ota_manager/src/ems_ota_manager/state_machine.py` — OtaState, VersionState, OtaStateMachine (320 lines)
- `src/ota_manager/src/ems_ota_manager/loop.py` — OtaManager async loop (306 lines)
- `src/ota_manager/src/ems_ota_manager/__main__.py` — entry point (120 lines)
- `src/ota_manager/src/ems_ota_manager/__init__.py` — package exports
- `deploy/systemd/ota_manager.service` — updated ExecStart
- `tests/test_ota_manager.py` — 21 new tests added (42 total)

## Decisions Made

- HealthChecker `check_fn` injection avoids subprocess calls in unit tests — same DI pattern as HttpDownloader's `transport=` from Plan 01
- `_on_state_change_cb` as a settable attribute (not just constructor arg) enables post-construction wiring in tests without re-constructing the full state machine
- `poll(timeout=50ms)` on the REP socket avoids blocking the event loop while still being responsive; tests sleep 50ms after `req.send()` to ensure ZMQ message delivery before `_handle_one_command()` checks
- Pre-built socket injection (`ota_pub_socket=`, `ota_rep_socket=`) avoids creating `/run/ems/` IPC directories in CI — consistent with how cloud_manager tests use `tcp://127.0.0.1:0`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ZMQ poll timeout in _handle_one_command (50ms not 0ms)**
- **Found during:** Task 2 GREEN phase (3 test timeouts)
- **Issue:** `poll(timeout=0)` is truly non-blocking — when test called `_handle_one_command` immediately after `req.send()`, ZMQ had not yet delivered the message; the REP socket never replied; the REQ hung waiting for reply
- **Fix:** Changed to `poll(timeout=50)` (50ms) so the handler waits briefly for a message to arrive; tests also add `asyncio.sleep(0.05)` after send to ensure delivery timing is deterministic
- **Files modified:** `src/ota_manager/src/ems_ota_manager/loop.py`, `tests/test_ota_manager.py`
- **Commit:** 8a67c3e (Task 2 GREEN)

**2. [Rule 1 - Bug] Fixed graceful_shutdown test context.term() hang**
- **Found during:** Task 2 GREEN phase (ctx.term() timeout)
- **Issue:** Test created a ZMQ context, then called `manager.run()` which bound the pre-injected sockets; after run() the test's `finally: ctx.term()` hung because the sockets still had messages queued (linger defaults to indefinite wait)
- **Fix:** Close sockets with `linger=0` in finally block before `ctx.term()`; also move pub/rep creation before the try block so they can be closed in finally even if OtaManager construction fails
- **Files modified:** `tests/test_ota_manager.py`
- **Commit:** 8a67c3e (Task 2 GREEN)

---

**Total deviations:** 2 auto-fixed (both Rule 1 bugs in ZMQ test isolation)
**Impact on plan:** Both fixes required for correct test behavior. No scope creep.

## Issues Encountered

- Pre-existing failures in `tests/test_config_hot_reload.py` and `tests/test_data_manager.py` (C library/module dependencies missing) are unrelated to this plan and unchanged.

## User Setup Required

None. The `public_key_hex` placeholder in `ota_config.yaml` must be replaced with a real Ed25519 public key during hardware commissioning (noted in Plan 01 summary).

## Next Phase Readiness

- Full OTA pipeline is complete and importable: `from ems_ota_manager import OtaState, OtaStateMachine, OtaManager`
- Phase 25 (diagnostics) can use SOCK_OTA_PUB to subscribe to OTA state change events
- Production deployment: update `/etc/ems/ota_config.yaml` and enable `ota_manager.service`

## Self-Check: PASSED

All created files verified present on disk. All task commits verified in git log.

---
*Phase: 24-ota-manager*
*Completed: 2026-03-15*
