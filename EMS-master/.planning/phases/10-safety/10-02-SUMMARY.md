---
phase: 10-safety
plan: 02
subsystem: safety_manager
tags: [watchdog, zmq, events, reset, ipc, mpack]
dependency_graph:
  requires: [ipc_defs.h, ems_types.h, mpack, libzmq, response_matrix.h]
  provides: [watchdog.h, watchdog.c, safety_event.h, safety_event.c, safety_reset.h, safety_reset.c]
  affects: [safety_manager main loop, data_manager telemetry, logger]
tech_stack:
  added: [libzmq (C PUB/PUSH/REP sockets)]
  patterns: [condvar-based watchdog feed thread, length-prefixed MessagePack framing, non-blocking ZMQ sends]
key_files:
  created:
    - src/safety_manager/src/watchdog.h
    - src/safety_manager/src/watchdog.c
    - src/safety_manager/src/safety_event.h
    - src/safety_manager/src/safety_event.c
    - src/safety_manager/src/safety_reset.h
    - src/safety_manager/src/safety_reset.c
  modified:
    - src/common/c/include/ipc_defs.h
    - src/safety_manager/CMakeLists.txt
decisions:
  - "safety_reset_poll takes raw DI array and latch flags rather than safety_state_t pointer -- decouples reset validation from response_matrix internals"
  - "Watchdog feed thread falls back to default scheduling if SCHED_FIFO creation fails (dev/CI without CAP_SYS_NICE)"
  - "REP socket replies use blocking sends (mandatory for ZMQ REP semantics); only PUB/PUSH use ZMQ_DONTWAIT"
  - "Added EMS_SOCK_SAFETY_CMD constant to ipc_defs.h for safety-specific reset socket"
metrics:
  duration: "316s"
  completed: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 10 Plan 02: Watchdog, ZMQ Events, and Reset Handler Summary

Condvar-based hardware watchdog with SCHED_FIFO feed thread, mpack MessagePack event publishing on ZMQ PUB+PUSH with length-prefixed framing, and non-blocking ZMQ REP reset command handler with DI input validation.

## Task Results

### Task 1: Watchdog management with condvar-based feed thread

**Files:** `watchdog.h`, `watchdog.c`
**Commit:** f45b034

Implemented `/dev/watchdog` management with:
- `watchdog_open()`: Opens device, sets timeout via `WDIOC_SETTIMEOUT`. Handles EBUSY gracefully (another process owns it). Returns -1 if unavailable (dev/CI behavior).
- `watchdog_kick()`: Calls `ioctl(WDIOC_KEEPALIVE)`.
- `watchdog_close()`: Writes magic character 'V' for proper disable before close.
- `watchdog_thread_start()`: Creates pthread with `SCHED_FIFO` at specified priority using `PTHREAD_EXPLICIT_SCHED`. Falls back to default scheduling if SCHED_FIFO fails.
- `watchdog_signal_scan_complete()`: Sets flag and signals condvar from scan thread.
- Feed thread: `pthread_cond_timedwait` with 1-second timeout. On timeout (scan delayed), still kicks watchdog -- process is alive, scan delay is not death.

### Task 2: ZMQ safety event publishing and reset command handler

**Files:** `safety_event.h`, `safety_event.c`, `safety_reset.h`, `safety_reset.c`
**Commit:** b12481a

**Safety Event Publishing:**
- `safety_event_init()`: Creates ZMQ context, connects PUB to telemetry and PUSH to logger. Sets `ZMQ_SNDHWM=100`. Non-fatal on failure.
- `safety_event_publish()`: Encodes MessagePack envelope `{ts, seq, src, topic, payload}` using mpack writer on stack-allocated 512-byte buffer. Length-prefixed framing (4-byte BE uint32). PUB sends: topic frame + length prefix + body. PUSH sends: length prefix + body. All sends use `ZMQ_DONTWAIT`, EAGAIN silently dropped.
- Convenience functions: `safety_event_estop()`, `safety_event_fire()`, `safety_event_flood()`, `safety_event_gpio_failure()`, `safety_event_reset()`.

**Safety Reset Handler:**
- `safety_reset_init()`: Creates ZMQ context, binds REP socket on `EMS_SOCK_SAFETY_CMD`.
- `safety_reset_poll()`: Non-blocking `zmq_poll` with 0 timeout. Decodes MessagePack request `{action: "safety_reset"}`. Validates that all triggering DI inputs have cleared before accepting. Rejects with descriptive error if inputs still active.
- Input validation: E-Stop checks DI-6+DI-7 normal, Fire checks DI-3+DI-4 inactive, Flood checks DI-1 inactive.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added EMS_SOCK_SAFETY_CMD to ipc_defs.h**
- **Found during:** Task 2
- **Issue:** No socket constant existed for safety reset commands. Plan mentioned using a new constant or parameter.
- **Fix:** Added `#define EMS_SOCK_SAFETY_CMD "ipc:///run/ems/safety_cmd.sock"` to ipc_defs.h.
- **Files modified:** `src/common/c/include/ipc_defs.h`
- **Commit:** f45b034

**2. [Rule 3 - Blocking] Adapted safety_reset_poll interface for Plan 01 decoupling**
- **Found during:** Task 2
- **Issue:** Plan specified `safety_reset_poll(ctx, state, di_raw, config)` calling `safety_reset()` from response_matrix.h directly. Since both plans are Wave 1 and may execute concurrently, the reset handler was designed with raw DI array + latch flags interface instead of safety_state_t pointer.
- **Fix:** `safety_reset_poll()` takes `const uint8_t *di_raw, uint8_t latched` and validates inputs directly. The main loop (Plan 03) bridges between safety_state_t and this interface.
- **Files modified:** `safety_reset.h`, `safety_reset.c`
- **Commit:** b12481a

## Verification

- `cmake --build build --target safety_manager` compiles all 6 new source files -- PASSED
- safety_event.c uses mpack for MessagePack encoding -- CONFIRMED (mpack_writer_init)
- All PUB/PUSH ZMQ sends include ZMQ_DONTWAIT flag -- CONFIRMED (5 occurrences in safety_event.c)
- Watchdog thread uses condvar timedwait with 1-second timeout -- CONFIRMED
- safety_reset validates inputs before accepting reset -- CONFIRMED (check_inputs_clear)
- No malloc/calloc/realloc in any hot-path function -- CONFIRMED (0 occurrences)

## Self-Check: PASSED

All 6 source files exist on disk. Both commits (f45b034, b12481a) verified in git log.
