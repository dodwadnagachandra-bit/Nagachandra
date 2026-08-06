---
phase: 10-safety
verified: 2026-03-14T09:00:00Z
status: passed
score: 11/11 must-haves verified
re_verification: false
---

# Phase 10: Safety Manager Verification Report

**Phase Goal:** Safety manager independently protects equipment and people with <100ms GPIO response, regardless of other module state
**Verified:** 2026-03-14T09:00:00Z
**Status:** PASSED
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | E-Stop triggers only when both DI-6 (NO) and DI-7 (NC) confirm; single-channel flags wiring fault | VERIFIED | response_matrix.c:71-91 -- dual-channel AND logic, XOR sets estop_discrepancy without triggering response. 16/16 C unit tests pass including test_estop_dual_channel_confirm and test_estop_discrepancy. |
| 2 | Fire extinguisher activates only when both DI-3 (Smoke) and DI-4 (Heat) are active | VERIFIED | response_matrix.c:111-135 -- dual-confirm sets fire_active, single sensor sets fire_single_sensor (warning only). DO_EXTINGUISHER only in fire_active branch (line 180). |
| 3 | Flood on DI-1 triggers ACDB trip, PCS stop, fault lamp, and siren | VERIFIED | response_matrix.c:185-188 -- outputs = DO_ACDB_TRIP, DO_FAULT_LAMP, DO_PCS_STOP, DO_SIREN. Unit test test_flood passes. |
| 4 | GPIO chip failure asserts ALL safety outputs (worst case) | VERIFIED | response_matrix.c:215-219 -- gpio_failure asserts ACDB_TRIP, EXTINGUISHER, FAULT_LAMP, PCS_STOP, SIREN. Unit test test_gpio_failure passes. main.c sets gpio_failure=true on read_di failure. |
| 5 | Response matrix correctly maps every DI condition to the right DO bitmask | VERIFIED | All 16 C unit tests pass via CTest covering: normal, estop dual, estop discrepancy, fire dual, fire single, flood, ACDB loss, door, GPIO failure, latching (3 tests), reset rejected/accepted, multiple conditions, IEC 60073 running lamp. |
| 6 | Hardware watchdog kicked only after scan thread signals cycle completion | VERIFIED | watchdog.c uses condvar (WDIOC_KEEPALIVE at line 73). main.c calls watchdog_signal_scan_complete() at line 671 after full scan cycle. Feed thread at SCHED_FIFO priority 81 (higher than scan at 80). |
| 7 | ZMQ safety events publish with correct MessagePack envelope | VERIFIED | safety_event.c:131 uses mpack_writer_init, encodes {ts, seq, src, topic, payload}. Length-prefixed framing. All sends use ZMQ_DONTWAIT (5 occurrences). PUB connects to EMS_SOCK_TELEMETRY, PUSH to EMS_SOCK_LOGGER. |
| 8 | Safety reset validates inputs before clearing latches | VERIFIED | response_matrix.c:234-293 -- safety_reset() checks each latch independently, only clears if triggering DI inputs are in normal state. Returns -1 if any still active. safety_reset.c polls via ZMQ REP with non-blocking poll. |
| 9 | Safety manager starts with SCHED_FIFO + mlockall, pre-faults stack | VERIFIED | main.c:136 mlockall(MCL_CURRENT|MCL_FUTURE), line 150 SCHED_FIFO at priority 80, line 145 stack pre-fault 64KiB. Graceful fallback if EPERM. |
| 10 | RTDB GPIO section updated via seqlock every scan cycle | VERIFIED | main.c:227-233 -- ems_seqlock_write_begin/end around memcpy of di/do/timestamp. Called from scan loop on every iteration. |
| 11 | Safety manager operates independently -- systemd Restart=always, no Requires= | VERIFIED | safety_manager.service: Restart=always, RestartSec=1, Wants= (not Requires=) ems-data-manager. CAP_SYS_NICE, LimitRTPRIO=99, LimitMEMLOCK=infinity. main.c continues without RTDB (line 424), without config_manager, without watchdog. |

**Score:** 11/11 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/safety_manager/src/gpio.h` | GPIO vtable abstraction | VERIFIED (116 lines) | gpio_ops_t with init/read_di/write_do/close. gpio_config_t. Both backends declared. |
| `src/safety_manager/src/gpio.c` | libgpiod v2 + RTDB backend (min 100) | VERIFIED (341 lines) | Conditional HAVE_LIBGPIOD compilation. RTDB backend reads/writes rtdb->gpio. gpiod_chip_open, gpiod_chip_request_lines for real backend. |
| `src/safety_manager/src/response_matrix.h` | Safety state + evaluate API | VERIFIED (124 lines) | safety_state_t, DO bitmask constants, PROTECTIVE_OUTPUTS, evaluate_inputs, evaluate_response_matrix, safety_reset. |
| `src/safety_manager/src/response_matrix.c` | DI-to-DO mapping logic (min 80) | VERIFIED (293 lines) | Full response matrix. Active-low inversion. E-Stop dual-channel. Fire dual-confirm. Latching. Reset with input validation. Zero malloc. |
| `src/safety_manager/src/watchdog.h` | Watchdog API | VERIFIED (70 lines) | open/kick/close/thread_start/thread_stop/signal_scan_complete. |
| `src/safety_manager/src/watchdog.c` | /dev/watchdog + condvar thread (min 60) | VERIFIED (223 lines) | WDIOC_KEEPALIVE, WDIOC_SETTIMEOUT, EBUSY handling, magic 'V' close, SCHED_FIFO thread, condvar timedwait. |
| `src/safety_manager/src/safety_event.h` | ZMQ event publishing | VERIFIED (87 lines) | safety_event_ctx_t, init/publish/close + convenience functions. |
| `src/safety_manager/src/safety_event.c` | mpack + non-blocking ZMQ (min 80) | VERIFIED (309 lines) | mpack_writer_init on stack buffer. Length-prefixed framing. ZMQ_DONTWAIT on all sends. ZMQ_LINGER=0. |
| `src/safety_manager/src/safety_reset.h` | ZMQ REP reset handler | VERIFIED (63 lines) | safety_reset_ctx_t, init/poll/close. |
| `src/safety_manager/src/safety_reset.c` | Non-blocking poll + input validation (min 60) | VERIFIED (378 lines) | zmq_poll with 0 timeout. mpack decode. Input validation before latch clear. ZMQ_LINGER=0. |
| `src/safety_manager/src/main.c` | Complete entry point (min 200) | VERIFIED (727 lines) | RT setup, config loading, RTDB attach, GPIO backend selection, scan loop (10ms), RTDB seqlock writes, event publishing, watchdog signal, CLI args, shutdown sequence. |
| `src/safety_manager/CMakeLists.txt` | Build config with all deps | VERIFIED (54 lines) | All 6 source files. Links ems_common_c, ems_rtdb, mpack, zmq, pthread, rt. Conditional libgpiod. Tests enabled. -Wall -Wextra -Werror. |
| `deploy/systemd/safety_manager.service` | Production systemd unit | VERIFIED (46 lines) | Restart=always, RestartSec=1, CAP_SYS_NICE+CAP_SYS_RAWIO, LimitRTPRIO=99, LimitMEMLOCK=infinity, After/Wants (not Requires), DeviceAllow, security hardening. |
| `src/safety_manager/tests/test_response_matrix.c` | C unit tests (min 100) | VERIFIED (606 lines) | 16 test cases, all pass via CTest. Covers every matrix path. |
| `tests/test_safety_manager.py` | Python integration tests (min 200) | VERIFIED (1112 lines) | 26 tests, 23 pass, 3 skip (ZMQ needing /run/ems). Covers SAFE-01 through SAFE-11. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| main.c | gpio.h | gpio_ops vtable selection | WIRED | Lines 438-459: selects gpio_ops_rtdb or gpio_ops_libgpiod based on CLI/env |
| main.c | response_matrix.h | evaluate_inputs + evaluate_response_matrix | WIRED | Lines 588 and 639: called every scan cycle |
| main.c | rtdb_lifecycle.h | rtdb_attach() | WIRED | Line 418: RTDB attach with 3x retry |
| main.c | watchdog.h | thread_start + signal_scan_complete | WIRED | Lines 527 and 671 |
| main.c | safety_event.h | safety_event_init + publish | WIRED | Line 490 init, line 288 publish changes |
| main.c | safety_reset.h | safety_reset_init + poll | WIRED | Line 610 poll in scan loop |
| response_matrix.c | rtdb.h | ems_gpio_t struct | WIRED | Via gpio.h which includes rtdb.h; GPIO_NUM_DI/DO match ems_gpio_t arrays |
| gpio.c | libgpiod v2 | gpiod_chip_open | WIRED | Line 43, behind #ifdef HAVE_LIBGPIOD |
| safety_event.c | ipc_defs.h | EMS_SOCK_TELEMETRY | WIRED | Line 490 in main.c passes constant to init |
| safety_event.c | mpack | mpack_writer_init | WIRED | Line 131 for MessagePack encoding |
| watchdog.c | Linux watchdog API | WDIOC_KEEPALIVE | WIRED | Line 73 ioctl call |
| test_safety_manager.py | main.c binary | subprocess with --rtdb-backend | WIRED | Binary started with CLI flags for test mode |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SAFE-01 | 10-01 | E-Stop dual-channel detection | SATISFIED | Dual-channel AND logic in response_matrix.c, 3 integration tests |
| SAFE-02 | 10-01 | E-Stop response <100ms | SATISFIED | 10ms scan loop, timing test measures <100ms (typically <10ms) |
| SAFE-03 | 10-01 | Fire dual-confirm | SATISFIED | Both DI-3+DI-4 required for extinguisher, 2 integration tests |
| SAFE-04 | 10-01 | Flood detection | SATISFIED | DI-1 triggers ACDB+PCS+siren, 1 integration test |
| SAFE-05 | 10-02 | Hardware watchdog | SATISFIED | /dev/watchdog with WDIOC_KEEPALIVE, condvar feed thread |
| SAFE-06 | 10-03 | SCHED_FIFO RT priority | SATISFIED | mlockall + SCHED_FIFO priority 80, stack pre-fault |
| SAFE-07 | 10-02 | Watchdog thread priority | SATISFIED | Feed thread at priority 81 (higher than scan 80), PTHREAD_EXPLICIT_SCHED |
| SAFE-08 | 10-03 | RTDB GPIO writes | SATISFIED | seqlock write every scan cycle with DI, DO, timestamp |
| SAFE-09 | 10-02 | ZMQ safety events | SATISFIED | mpack MessagePack on PUB+PUSH, ZMQ_DONTWAIT, 1 integration test |
| SAFE-10 | 10-03 | Independent lifecycle | SATISFIED | Restart=always, Wants (not Requires), continues without RTDB/config/logger |
| SAFE-11 | 10-01, 10-04 | GPIO failure worst-case | SATISFIED | gpio_failure asserts ALL safety outputs, C unit test + integration test |

No orphaned requirements found. All 11 SAFE requirements declared in plans match REQUIREMENTS.md Phase 10 mapping.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No TODO/FIXME/PLACEHOLDER/HACK found in any safety_manager source file |

Zero anti-patterns detected. No dynamic allocation in response matrix or scan loop hot path (confirmed via grep).

### Human Verification Required

### 1. E-Stop Response Timing on Target Hardware

**Test:** Run safety_manager on ECU-1170-552A with real GPIO. Press E-Stop and measure time from DI edge to DO assertion with oscilloscope.
**Expected:** <100ms end-to-end (software measured <10ms with 5ms scan, but hardware latency adds to total).
**Why human:** Real GPIO latency, libgpiod overhead, and PREEMPT_RT kernel jitter cannot be measured in CI.

### 2. Watchdog Recovery Behavior

**Test:** Kill -9 the safety_manager process while /dev/watchdog is open. Observe system behavior within 2 seconds.
**Expected:** Hardware watchdog triggers system reset (or systemd restarts the process before watchdog fires).
**Why human:** Requires hardware watchdog device. Dev systems return -1 from watchdog_open.

### 3. GPIO libgpiod Backend on Real Hardware

**Test:** Run on ECU-1170 with real gpiochip0. Verify DI reads match physical input states and DO writes drive physical outputs.
**Expected:** All 8 DI values match physical wiring, all 8 DO assert/de-assert correctly.
**Why human:** libgpiod backend compiled conditionally (HAVE_LIBGPIOD). All CI tests use RTDB backend.

### Gaps Summary

No gaps found. All 11 SAFE requirements are satisfied with implementation evidence. The safety_manager compiles cleanly (-Wall -Wextra -Werror), 16/16 C unit tests pass, 23/26 integration tests pass (3 skipped due to ipc:// socket path requirements -- not failures). The response matrix implements every row from the CONTEXT.md safety response matrix table. The main scan loop runs at 10ms with SCHED_FIFO priority 80, updates RTDB via seqlock, publishes ZMQ events, and signals the watchdog. The systemd service is hardened with Restart=always, RT capabilities, and device access.

---

_Verified: 2026-03-14T09:00:00Z_
_Verifier: Claude (gsd-verifier)_
