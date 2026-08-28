---
phase: 11-communications
plan: "03"
subsystem: comm_manager
tags: [can, socketcan, heartbeat, zmq, events, rtdb, pthread]
dependency_graph:
  requires: [can_decode, can_health_rtdb]
  provides: [comm_manager_c_binary, can_reader, can_health_monitor, comm_event_publisher]
  affects: [data_manager, logger, alarm_manager]
tech_stack:
  added: [linux/can/raw.h, net/if.h, sys/ioctl.h]
  patterns: [pthread-per-interface, seqlock-rtdb-write, mpack-zmq-publish, heartbeat-timeout-detection]
key_files:
  created:
    - src/comm_manager/c/src/comm_event.h
    - src/comm_manager/c/src/comm_event.c
    - src/comm_manager/c/src/can_reader.h
    - src/comm_manager/c/src/can_reader.c
    - src/comm_manager/c/src/can_health.h
    - src/comm_manager/c/src/can_health.c
  modified:
    - src/comm_manager/c/src/main.c
    - src/comm_manager/c/CMakeLists.txt
decisions:
  - "comm_event follows identical pattern to safety_event (mpack + length-prefixed ZMQ) with source=comm_manager_c and topic=comm_fault"
  - "One CAN reader pthread per SocketCAN interface (max 2), each assigned a cluster_index"
  - "Health monitor initializes prev_online to 0 (unknown) to avoid false recovery events at startup"
  - "Rack last_update_ms initialized to CLOCK_MONOTONIC at startup to prevent false heartbeat timeouts"
  - "Main thread uses pause() loop for signal-driven shutdown (no polling)"
metrics:
  duration: "4m 26s"
  completed: "2026-03-14"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 0
  tests_passed: 27
---

# Phase 11 Plan 03: CAN C Process -- SocketCAN Reader, Heartbeat Monitor, ZMQ Events Summary

Complete comm_manager_c binary with SocketCAN reader threads, heartbeat-based offline detection at 300ms intervals, ZMQ comm_fault/recovery event publishing via mpack, and main entry point with CLI arg parsing, RTDB attach, multi-thread lifecycle, and SIGTERM handling.

## Task Completion

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 | CAN reader thread, heartbeat monitor, and ZMQ comm event publisher | 66f4759 | Done |
| 2 | Main entry point with RTDB attach, config parsing, thread spawn, and signal handling | f972ed2 | Done |

## What Was Built

### Task 1: CAN Reader, Health Monitor, Comm Event Publisher

**comm_event.h/.c** -- ZMQ event publisher following safety_event.c pattern:
- `comm_event_init()` creates ZMQ context, connects PUB to telemetry, PUSH to logger, ZMQ_LINGER=0
- `comm_event_publish()` encodes 5-key mpack envelope (ts, seq, src=comm_manager_c, topic=comm_fault, payload={event_type, message, severity})
- `comm_event_publish_with_data()` extended variant with device_id and device_address in payload
- Stack-allocated 512-byte buffer, length-prefixed framing, ZMQ_DONTWAIT sends

**can_reader.h/.c** -- SocketCAN read loop:
- `can_socket_init()` creates CAN_RAW socket, binds to interface, enables CAN_RAW_ERR_FILTER with CAN_ERR_MASK
- `can_reader_thread()` blocking read() loop: error frames update RTDB can_health via seqlock and publish can_bus_error events on state change; data frames decoded via can_decode_id/can_decode_frame, written to RTDB rack via seqlock with last_update_ms and online=1
- Checks volatile running flag for SIGTERM-driven shutdown

**can_health.h/.c** -- Heartbeat timeout detector:
- `can_health_init_timestamps()` sets all rack last_update_ms to CLOCK_MONOTONIC at startup
- `can_health_thread()` runs at 300ms intervals, reads each rack's last_update_ms via seqlock
- Offline transition: marks rack->online=0, publishes heartbeat_timeout comm_fault with device_id
- Recovery transition: publishes comm_recovery event
- prev_online initialized to 0 (unknown) to avoid false recovery at startup

### Task 2: Main Entry Point

**main.c** -- Full process lifecycle:
- CLI via getopt_long: --interface (repeatable, max 2), --base-id, --clusters, --racks-per-cluster, --heartbeat-timeout-ms, --check-interval-ms, --help
- RTDB attach via rtdb_attach() with magic/version validation
- Topology read from RTDB header (overridable by CLI)
- Spawns one can_reader_thread per CAN interface, one shared can_health_thread
- Main thread: pause() loop until SIGTERM/SIGINT
- Shutdown: join all threads, close ZMQ, detach RTDB, exit 0

**CMakeLists.txt** -- Updated with all new sources, libzmq/mpack/ems_rtdb/pthread linkage.

## Verification

- comm_manager_c builds with 0 warnings (-Wall -Wextra -Werror)
- `--help` prints usage and exits cleanly
- CAN decode tests from Plan 01 still pass (27/27)
- Binary links libzmq, pthread, rt, ems_rtdb, mpack correctly

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED

All 8 created/modified files verified on disk. Both commits (66f4759, f972ed2) verified in git log.
