# Phase 10: Safety - Research

**Researched:** 2026-03-14
**Domain:** Real-time safety I/O with libgpiod v2, hardware watchdog, POSIX RT scheduling
**Confidence:** HIGH

## Summary

Phase 10 implements the safety_manager -- a pure C process that monitors 8 digital inputs (E-Stop, fire, flood, door, ACDB feedback) and drives 8 digital outputs (ACDB trip, extinguisher, lamps, PCS stop, siren) with <100ms response time. The process runs at SCHED_FIFO real-time priority, uses libgpiod v2 for GPIO access, writes GPIO state to RTDB via seqlock, publishes safety events via ZMQ, and manages a hardware watchdog. It must operate independently of all other EMS modules.

The implementation builds on substantial existing infrastructure: RTDB lifecycle API (rtdb_attach/detach), seqlock primitives, IPC socket definitions, mpack for MessagePack serialization, the GPIO test harness (RTDB backend), and a complete gpio_config.yaml with pin mappings. The main.c stub and CMakeLists.txt are already in place. The key work is implementing the scan loop, response matrix, watchdog management, ZMQ event publishing, and safety reset command handling.

**Primary recommendation:** Use a two-thread architecture -- main SCHED_FIFO thread for GPIO scan/response (priority 80) and a higher-priority watchdog feed thread (priority 81) that only kicks /dev/watchdog after the scan thread signals completion of a full cycle. Use libgpiod v2 API (available as 2.2.3 on the target) with polling (not edge events) for deterministic scan timing.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Full safety response matrix defined (see CONTEXT.md tables for exact DI-to-DO mappings)
- E-Stop dual-channel: DI-6 (NO) + DI-7 (NC) must both confirm; single-channel discrepancy = wiring fault (log CRITICAL, do NOT trigger E-Stop response)
- Fire dual-confirm: both DI-3 (Smoke) AND DI-4 (Heat) active before extinguisher; single sensor = Warning Lamp + log only
- Flood de-energizes AC path and stops PCS (IEC 62485-2)
- ACDB feedback loss: PCS stop + fault, but do NOT re-trip ACDB
- Door open: warning lamp only
- Spare DI-5: read/publish only, trigger nothing
- Spare DO-7: never asserted by safety_manager
- Failure mode: fail-safe per channel (IEC 61508), each input fails independently into its own safe state
- GPIO chip failure: assert ALL safety outputs, keep kicking watchdog
- DO write failures: never block other outputs, always retry
- GPIO failures are self-recovering (not latching)
- E-Stop/Fire/Flood recovery: manual latch, ZMQ safety_reset command, validated that inputs have cleared
- ACDB/Door/GPIO recovery: auto-recover when condition resolves
- Indicator lamps: on/off only (no blink patterns), Running + Fault mutually exclusive (IEC 60073)
- Fault lamp follows "any protective output" rule
- Config read once at startup from gpio_config.yaml -- no hot-reload for safety config
- Safety_manager works even if all other modules are dead

### Claude's Discretion
- Main loop architecture (single thread with scan cycle vs multi-thread with dedicated GPIO/watchdog threads)
- libgpiod API usage (edge events vs polling, line request grouping)
- ZMQ message formatting for safety events (within existing envelope contract)
- SCHED_FIFO priority levels for main thread vs watchdog feed thread
- mlockall placement and stack pre-fault strategy
- Startup self-test sequence before entering main loop
- Internal state tracking data structures

### Deferred Ideas (OUT OF SCOPE)
- SAFE-12: Safety state machine with explicit transitions and audit trail
- SAFE-13: Channel discrepancy detection for E-Stop wiring fault (basic logging IS in scope)
- SAFE-14: GPIO debounce with configurable per-DI timing
- SAFE-15: Safety event black-box ring buffer in shm
- Blink/pulse patterns for indicator lamps
- Physical reset button via DI
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SAFE-01 | E-Stop dual-channel detection (DI-6 NO + DI-7 NC), flags single-channel as wiring fault | libgpiod v2 polling API; active_low config in gpio_config.yaml; dual-channel validation logic |
| SAFE-02 | E-Stop response asserts DO-5, DO-0, DO-6, DO-4 within <100ms | libgpiod v2 set_values for atomic multi-output; SCHED_FIFO + mlockall for timing guarantee |
| SAFE-03 | Fire dual-confirm (DI-3 + DI-4) before DO-1 + E-Stop outputs | Response matrix lookup table pattern; single-sensor warning logic |
| SAFE-04 | Flood (DI-1) triggers DO-5, DO-0, DO-6 | Response matrix row evaluation |
| SAFE-05 | Hardware watchdog /dev/watchdog kicked every 500ms after scan cycle | Linux watchdog API (open, ioctl WDIOC_SETTIMEOUT/KEEPALIVE, magic close) |
| SAFE-06 | SCHED_FIFO + mlockall(MCL_CURRENT\|MCL_FUTURE) at process start | sched_setscheduler + stack pre-fault pattern |
| SAFE-07 | Dedicated watchdog feed thread at higher priority than GPIO poll thread | pthread with SCHED_FIFO priority 81 vs scan thread at 80; condvar signaling |
| SAFE-08 | RTDB GPIO writes on every edge/output change via seqlock | Existing seqlock.h API; rtdb_attach() from rtdb_lifecycle.h |
| SAFE-09 | ZMQ safety events on telemetry + logger sockets | mpack MessagePack encoding; existing IPC socket paths and envelope format |
| SAFE-10 | Independent lifecycle, systemd Restart=always, no runtime deps | systemd service hardening; fallback config loading; RTDB attach retry |
| SAFE-11 | GPIO failure assumes worst case, logs critical, asserts all outputs | gpiod_chip_open error handling; per-line error handling |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| libgpiod | 2.2.3 | GPIO chip/line access via Linux chardev | Official Linux GPIO userspace API; replaces deprecated sysfs; v2 has line settings objects |
| POSIX RT (sched.h, mman.h) | POSIX.1-2008 | SCHED_FIFO scheduling, mlockall memory locking | Kernel-native RT scheduling, no external deps |
| Linux watchdog API | Kernel 5.x+ | /dev/watchdog hardware watchdog | Standard Linux watchdog subsystem via ioctl |
| ZeroMQ (libzmq) | 4.3.x | IPC messaging for events and commands | Already used project-wide; PUB/SUB + PUSH/PULL + REQ/REP |
| mpack | 1.1.1 (vendored) | MessagePack serialization for ZMQ messages | Already vendored in project; length-prefixed framing established |
| libems_rtdb | 1.0.0 (project) | RTDB attach/detach lifecycle | Phase 9 deliverable; provides rtdb_attach() |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pthread | POSIX | Watchdog feed thread, condvar signaling | Watchdog thread at higher SCHED_FIFO priority |
| libyaml or manual parse | -- | GPIO config loading (fallback) | Only if config_manager is unavailable at startup |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| libgpiod polling | libgpiod edge events | Edge events are event-driven but harder to guarantee deterministic scan timing; polling with 10-20ms sleep gives predictable cycle time |
| Separate DI/DO line requests | Single combined request | libgpiod v2 allows one request per direction; separate requests for DI (input) and DO (output) are cleaner |
| ZMQ for config loading | Direct YAML parse | ZMQ requires config_manager to be running; safety_manager should have fallback direct file read |

**Installation (dev system):**
```bash
# Arch Linux
sudo pacman -S libgpiod

# Ubuntu 24.04 (dev target)
sudo apt install libgpiod-dev libgpiod2
```

**CMake linking:**
```cmake
target_link_libraries(safety_manager
    PRIVATE
        ems_common_c
        ems_rtdb
        mpack
        gpiod
        zmq
        pthread
        rt
)
```

## Architecture Patterns

### Recommended Project Structure
```
src/safety_manager/
├── CMakeLists.txt           # Build config (update existing)
├── src/
│   ├── main.c               # Entry point, RT setup, main loop
│   ├── gpio.c               # libgpiod v2 abstraction (open/read/write/close)
│   ├── gpio.h               # GPIO interface (testable abstraction)
│   ├── response_matrix.c    # Safety logic: DI states -> DO outputs
│   ├── response_matrix.h    # Response matrix interface
│   ├── watchdog.c           # /dev/watchdog management
│   ├── watchdog.h           # Watchdog interface
│   ├── safety_event.c       # ZMQ event publishing (telemetry + logger)
│   ├── safety_event.h       # Event publishing interface
│   ├── safety_reset.c       # ZMQ REQ/REP reset command handler
│   └── safety_reset.h       # Reset command interface
└── tests/                   # C unit tests (future)
    └── CMakeLists.txt
```

### Pattern 1: Two-Thread Architecture
**What:** Main scan thread (SCHED_FIFO 80) runs the GPIO poll + response loop. Watchdog feed thread (SCHED_FIFO 81) waits on a condvar signal from the scan thread, then kicks /dev/watchdog.
**When to use:** Always -- SAFE-07 requires the watchdog thread to be higher priority to prevent starvation.
**Example:**
```c
/* Scan thread signals watchdog thread after each complete cycle */
static pthread_mutex_t wdt_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  wdt_cond  = PTHREAD_COND_INITIALIZER;
static volatile bool   scan_complete = false;

/* Called at end of each scan cycle in main thread */
static void signal_scan_complete(void)
{
    pthread_mutex_lock(&wdt_mutex);
    scan_complete = true;
    pthread_cond_signal(&wdt_cond);
    pthread_mutex_unlock(&wdt_mutex);
}

/* Watchdog feed thread (priority 81) */
static void *watchdog_thread(void *arg)
{
    int wdt_fd = *(int *)arg;
    while (g_running)
    {
        pthread_mutex_lock(&wdt_mutex);
        while (!scan_complete && g_running)
        {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_sec += 1;  /* 1s timeout to detect stuck scan */
            pthread_cond_timedwait(&wdt_cond, &wdt_mutex, &ts);
        }
        scan_complete = false;
        pthread_mutex_unlock(&wdt_mutex);

        if (g_running)
        {
            ioctl(wdt_fd, WDIOC_KEEPALIVE, NULL);
        }
    }
    return NULL;
}
```

### Pattern 2: Response Matrix as Static Lookup Table
**What:** The safety response matrix is a compile-time array mapping input conditions to output bitmasks. No dynamic allocation, no branching logic per event.
**When to use:** Always -- the response matrix is fully defined and static.
**Example:**
```c
/* Output bitmask constants */
#define DO_ACDB_TRIP     (1 << 0)
#define DO_EXTINGUISHER  (1 << 1)
#define DO_WARNING_LAMP  (1 << 2)
#define DO_RUNNING_LAMP  (1 << 3)
#define DO_FAULT_LAMP    (1 << 4)
#define DO_PCS_STOP      (1 << 5)
#define DO_SIREN         (1 << 6)
/* DO-7 spare: never asserted */

/* Protective outputs mask (triggers fault lamp when any active) */
#define PROTECTIVE_OUTPUTS (DO_ACDB_TRIP | DO_EXTINGUISHER | DO_PCS_STOP | DO_SIREN)

/* Safety state flags (latching for E-Stop/Fire/Flood) */
typedef struct
{
    bool estop_active;      /* latched until manual reset */
    bool fire_active;       /* latched until manual reset */
    bool flood_active;      /* latched until manual reset */
    bool acdb_loss;         /* auto-recovers */
    bool door_open;         /* auto-recovers */
    bool gpio_failure;      /* auto-recovers */
    bool estop_discrepancy; /* wiring fault detected */
} safety_state_t;

/* Evaluate all inputs and compute combined DO bitmask */
static uint8_t evaluate_response_matrix(const safety_state_t *state)
{
    uint8_t outputs = 0;

    if (state->estop_active)
    {
        outputs |= DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }
    if (state->fire_active)
    {
        outputs |= DO_ACDB_TRIP | DO_EXTINGUISHER | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }
    if (state->flood_active)
    {
        outputs |= DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }
    if (state->acdb_loss)
    {
        outputs |= DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }
    if (state->door_open)
    {
        outputs |= DO_WARNING_LAMP;
    }
    if (state->gpio_failure)
    {
        /* Assert ALL safety outputs (worst case) */
        outputs |= DO_ACDB_TRIP | DO_EXTINGUISHER | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }

    /* Running lamp: ON only when no faults (IEC 60073: green+red prohibited) */
    if (!(outputs & PROTECTIVE_OUTPUTS))
    {
        outputs |= DO_RUNNING_LAMP;
    }

    return outputs;
}
```

### Pattern 3: GPIO Abstraction Layer for Testability
**What:** Wrap libgpiod calls behind a function pointer table (vtable) so unit tests can substitute mock GPIO without hardware.
**When to use:** Always -- safety_manager tests on CI have no GPIO hardware.
**Example:**
```c
typedef struct
{
    int  (*init)(const char *chip_path, const unsigned int *di_offsets,
                 size_t num_di, const unsigned int *do_offsets, size_t num_do);
    int  (*read_di)(uint8_t *values, size_t count);
    int  (*write_do)(const uint8_t *values, size_t count);
    void (*close)(void);
} gpio_ops_t;

/* Real implementation uses libgpiod v2 */
extern const gpio_ops_t gpio_ops_libgpiod;

/* Test stub uses RTDB directly (same as GPIO harness) */
extern const gpio_ops_t gpio_ops_rtdb;
```

### Pattern 4: RT Process Initialization
**What:** Lock memory, pre-fault stack, set SCHED_FIFO, then enter main loop.
**When to use:** Always at process startup (SAFE-06).
**Example:**
```c
#include <sched.h>
#include <sys/mman.h>
#include <string.h>

#define STACK_PREFAULT_SIZE (64 * 1024)  /* 64 KB stack pre-fault */

static int setup_realtime(int priority)
{
    /* Lock all current and future memory */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
    {
        perror("mlockall failed");
        return -1;
    }

    /* Pre-fault stack pages */
    volatile char stack_prefault[STACK_PREFAULT_SIZE];
    memset((char *)stack_prefault, 0, sizeof(stack_prefault));

    /* Set SCHED_FIFO priority */
    struct sched_param param;
    param.sched_priority = priority;
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0)
    {
        perror("sched_setscheduler failed");
        /* Non-fatal: log warning, continue without RT */
        return -1;
    }

    return 0;
}
```

### Anti-Patterns to Avoid
- **Dynamic allocation in scan loop:** Never malloc/free in the RT scan path. All buffers must be pre-allocated.
- **Blocking I/O in scan thread:** ZMQ send must use ZMQ_DONTWAIT. If send fails, drop the message -- safety outputs are more important than event delivery.
- **Shared state without synchronization:** RTDB seqlock writes must bracket all GPIO struct updates. The scan thread is the single writer for the gpio section.
- **Ignoring watchdog on GPIO failure:** Even during chip failure, the process is alive and making decisions. Keep kicking the watchdog. An uncontrolled reboot is worse than the fail-safe GPIO state.
- **Latching fault reset in safety_manager:** Latching acknowledgement belongs in alarm_manager (M2). Safety_manager only handles its own latched states (E-Stop/Fire/Flood manual reset).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| GPIO access | Direct /dev/gpiochipN ioctl | libgpiod v2 API | ABI stability, active_low handling, proper resource cleanup |
| Watchdog management | Custom timer-based reboot | /dev/watchdog kernel subsystem | Hardware watchdog is more reliable; kernel handles timeout enforcement |
| MessagePack encoding | Manual binary packing | mpack 1.1.1 (vendored) | Already used project-wide; handles map/array/string encoding |
| Seqlock concurrency | Custom atomic operations | seqlock.h (project) | Already validated in Phase 9; correct memory ordering |
| RTDB lifecycle | Manual shm_open/mmap | rtdb_lifecycle.h (rtdb_attach) | Already validated; handles magic/version checking |
| IPC socket paths | Hardcoded strings | ipc_defs.h constants | Single source of truth for all modules |

**Key insight:** Every infrastructure component safety_manager needs already exists from Phase 9 and M0. The only new external dependency is libgpiod.

## Common Pitfalls

### Pitfall 1: libgpiod v2 API vs v1
**What goes wrong:** Using gpiod_chip_get_line() or gpiod_line_set_value() which are v1-only functions that don't exist in v2.
**Why it happens:** Most online examples and tutorials still show libgpiod v1 API.
**How to avoid:** Use v2 API exclusively: gpiod_chip_open(), gpiod_line_settings_new(), gpiod_line_config_new(), gpiod_chip_request_lines(), gpiod_line_request_get_values(), gpiod_line_request_set_values().
**Warning signs:** Compilation errors about missing gpiod_line_get/set functions.

### Pitfall 2: Page Faults Under SCHED_FIFO
**What goes wrong:** First call to a function or first access to a memory page triggers a page fault, causing unpredictable latency spikes.
**Why it happens:** mlockall(MCL_FUTURE) locks pages after they're faulted in, but doesn't pre-fault them.
**How to avoid:** Call mlockall(MCL_CURRENT | MCL_FUTURE) early, then pre-fault the stack with a volatile memset, and pre-fault all heap allocations before entering the scan loop.
**Warning signs:** Intermittent latency spikes only on first iteration.

### Pitfall 3: Watchdog Not Kicked During Long Operations
**What goes wrong:** Watchdog timeout fires and reboots the system because the scan thread was blocked (e.g., ZMQ send or RTDB write).
**Why it happens:** Watchdog thread waits on condvar from scan thread; if scan thread blocks, watchdog starves.
**How to avoid:** Use condvar timedwait with 1-second timeout. If scan thread hasn't signaled within timeout, log a warning but still kick the watchdog (the process is alive). The watchdog detects process death, not scan delays.
**Warning signs:** System reboots during development with no error log.

### Pitfall 4: E-Stop Dual-Channel Active-Low Confusion
**What goes wrong:** E-Stop detection logic doesn't account for active_low on DI-7 (NC contact). Raw GPIO value and logical value are inverted.
**Why it happens:** DI-6 is active-high (raw 1 = pressed), DI-7 is active-low (raw 0 = pressed). Both must be in "pressed" logical state for valid E-Stop.
**How to avoid:** Apply active_low inversion from gpio_config.yaml at read time: `logical = active_low ? !raw : raw`. Then check both logical values are true.
**Warning signs:** E-Stop triggers on a single channel or doesn't trigger at all.

### Pitfall 5: ZMQ Socket in RT Thread Blocks
**What goes wrong:** zmq_send() blocks when HWM is reached, causing scan cycle to miss the 100ms deadline.
**Why it happens:** Default ZMQ PUB socket has a send buffer; when consumers are slow or disconnected, the buffer fills.
**How to avoid:** Always use ZMQ_DONTWAIT flag. If send returns EAGAIN, log and drop the message. Safety outputs are the priority, not event delivery.
**Warning signs:** Scan cycle time increases when logger is slow or crashed.

### Pitfall 6: Watchdog Device Already Opened
**What goes wrong:** open("/dev/watchdog") fails with EBUSY because another process (systemd, another watchdog daemon) already has it open.
**Why it happens:** Only one process can hold the watchdog device at a time.
**How to avoid:** Ensure systemd's WatchdogSec= is NOT set in the service file (safety_manager manages its own watchdog). Check for existing watchdog daemons (watchdog, wd_keepalive). Handle EBUSY gracefully -- log warning and continue without hardware watchdog.
**Warning signs:** safety_manager starts but /dev/watchdog open fails.

### Pitfall 7: SCHED_FIFO Without CAP_SYS_NICE
**What goes wrong:** sched_setscheduler() returns EPERM because the process doesn't have sufficient privileges.
**Why it happens:** SCHED_FIFO requires either root or CAP_SYS_NICE capability.
**How to avoid:** Use systemd service with `AmbientCapabilities=CAP_SYS_NICE` and `LimitRTPRIO=99`. The existing service file has these commented out -- they need to be enabled.
**Warning signs:** safety_manager logs warning about RT scheduling but continues at normal priority.

## Code Examples

### libgpiod v2: Open Chip and Request Lines
```c
/* Source: libgpiod v2 official docs (libgpiod.readthedocs.io) */
#include <gpiod.h>

static struct gpiod_chip *chip = NULL;
static struct gpiod_line_request *di_request = NULL;
static struct gpiod_line_request *do_request = NULL;

static int gpio_init(const char *chip_path,
                     const unsigned int *di_offsets, size_t num_di,
                     const unsigned int *do_offsets, size_t num_do)
{
    chip = gpiod_chip_open(chip_path);
    if (!chip)
    {
        return -1;
    }

    /* Configure DI lines as inputs */
    struct gpiod_line_settings *di_settings = gpiod_line_settings_new();
    gpiod_line_settings_set_direction(di_settings, GPIOD_LINE_DIRECTION_INPUT);
    /* Note: active_low and debounce are applied in software, not via libgpiod,
       because we need per-pin active_low from config and software debounce
       implementation is deferred (SAFE-14). */

    struct gpiod_line_config *di_config = gpiod_line_config_new();
    gpiod_line_config_add_line_settings(di_config, di_offsets, num_di, di_settings);

    struct gpiod_request_config *req_cfg = gpiod_request_config_new();
    gpiod_request_config_set_consumer(req_cfg, "ems-safety-manager");

    di_request = gpiod_chip_request_lines(chip, req_cfg, di_config);

    gpiod_request_config_free(req_cfg);
    gpiod_line_config_free(di_config);
    gpiod_line_settings_free(di_settings);

    if (!di_request)
    {
        gpiod_chip_close(chip);
        return -1;
    }

    /* Configure DO lines as outputs (initial low) */
    struct gpiod_line_settings *do_settings = gpiod_line_settings_new();
    gpiod_line_settings_set_direction(do_settings, GPIOD_LINE_DIRECTION_OUTPUT);
    gpiod_line_settings_set_output_value(do_settings, GPIOD_LINE_VALUE_INACTIVE);

    struct gpiod_line_config *do_config = gpiod_line_config_new();
    gpiod_line_config_add_line_settings(do_config, do_offsets, num_do, do_settings);

    req_cfg = gpiod_request_config_new();
    gpiod_request_config_set_consumer(req_cfg, "ems-safety-manager");

    do_request = gpiod_chip_request_lines(chip, req_cfg, do_config);

    gpiod_request_config_free(req_cfg);
    gpiod_line_config_free(do_config);
    gpiod_line_settings_free(do_settings);

    if (!do_request)
    {
        gpiod_line_request_release(di_request);
        gpiod_chip_close(chip);
        return -1;
    }

    return 0;
}
```

### Hardware Watchdog Setup
```c
/* Source: Linux kernel watchdog API docs */
#include <fcntl.h>
#include <linux/watchdog.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int watchdog_open(const char *dev_path, int timeout_sec)
{
    int fd = open(dev_path, O_RDWR);
    if (fd < 0)
    {
        return -1;
    }

    /* Set timeout */
    int timeout = timeout_sec;
    if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) != 0)
    {
        /* Some watchdogs don't support settimeout -- continue with default */
    }

    return fd;
}

static void watchdog_kick(int fd)
{
    ioctl(fd, WDIOC_KEEPALIVE, NULL);
}

static void watchdog_close(int fd)
{
    /* Magic close: write 'V' to properly disable watchdog */
    write(fd, "V", 1);
    close(fd);
}
```

### RTDB GPIO Write with Seqlock
```c
/* Source: Project seqlock.h + rtdb.h */
static void rtdb_write_gpio(ems_rtdb_t *rtdb,
                            const uint8_t *di, size_t num_di,
                            const uint8_t *do_out, size_t num_do)
{
    ems_seqlock_write_begin(&rtdb->gpio.lock);

    for (size_t i = 0; i < num_di && i < 8; i++)
    {
        rtdb->gpio.di[i] = di[i];
    }
    for (size_t i = 0; i < num_do && i < 8; i++)
    {
        rtdb->gpio.do_state[i] = do_out[i];
    }

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    rtdb->gpio.last_update_ms =
        (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;

    ems_seqlock_write_end(&rtdb->gpio.lock);
}
```

### ZMQ Safety Event Publish (Non-Blocking)
```c
/* Source: Project ipc_defs.h + mpack conventions */
#include <zmq.h>
#include "mpack.h"
#include "ipc_defs.h"

static int publish_safety_event(void *pub_sock, void *push_sock,
                                const char *event_type,
                                const char *message,
                                ems_severity_t severity,
                                uint64_t seq_num)
{
    char buf[512];
    mpack_writer_t writer;
    mpack_writer_init(&writer, buf, sizeof(buf));

    mpack_start_map(&writer, 5);
    mpack_write_cstr(&writer, EMS_MSG_KEY_TIMESTAMP);
    {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        mpack_write_double(&writer, ts.tv_sec + ts.tv_nsec / 1e9);
    }
    mpack_write_cstr(&writer, EMS_MSG_KEY_SEQUENCE);
    mpack_write_u64(&writer, seq_num);
    mpack_write_cstr(&writer, EMS_MSG_KEY_SOURCE);
    mpack_write_cstr(&writer, "safety_manager");
    mpack_write_cstr(&writer, EMS_MSG_KEY_TOPIC);
    mpack_write_cstr(&writer, EMS_TOPIC_GPIO);
    mpack_write_cstr(&writer, EMS_MSG_KEY_PAYLOAD);
    {
        mpack_start_map(&writer, 3);
        mpack_write_cstr(&writer, EMS_MSG_KEY_EVENT_TYPE);
        mpack_write_cstr(&writer, event_type);
        mpack_write_cstr(&writer, EMS_MSG_KEY_MESSAGE);
        mpack_write_cstr(&writer, message);
        mpack_write_cstr(&writer, EMS_MSG_KEY_SEVERITY);
        mpack_write_int(&writer, (int)severity);
        mpack_finish_map(&writer);
    }
    mpack_finish_map(&writer);

    if (mpack_writer_destroy(&writer) != mpack_ok)
    {
        return -1;
    }

    size_t msg_len = mpack_writer_buffer_used(&writer);

    /* Length-prefixed framing (4-byte BE uint32) */
    uint32_t frame_len = htonl((uint32_t)msg_len);

    /* Publish on telemetry PUB socket (non-blocking) */
    zmq_send(pub_sock, EMS_TOPIC_GPIO, strlen(EMS_TOPIC_GPIO), ZMQ_SNDMORE | ZMQ_DONTWAIT);
    zmq_send(pub_sock, &frame_len, 4, ZMQ_SNDMORE | ZMQ_DONTWAIT);
    zmq_send(pub_sock, buf, msg_len, ZMQ_DONTWAIT);

    /* Push to logger PUSH socket (non-blocking) */
    zmq_send(push_sock, &frame_len, 4, ZMQ_SNDMORE | ZMQ_DONTWAIT);
    zmq_send(push_sock, buf, msg_len, ZMQ_DONTWAIT);

    return 0;
}
```

### Main Scan Loop Structure
```c
static volatile sig_atomic_t g_running = 1;

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

/* Main scan loop -- runs at SCHED_FIFO 80 */
static void scan_loop(const gpio_ops_t *gpio, ems_rtdb_t *rtdb,
                      void *pub_sock, void *push_sock, void *rep_sock)
{
    safety_state_t state = {0};
    uint8_t di_raw[8] = {0};
    uint8_t do_prev[8] = {0};
    uint64_t seq = 0;

    while (g_running)
    {
        /* 1. Read all DI */
        int rc = gpio->read_di(di_raw, 8);
        if (rc < 0)
        {
            /* GPIO read failure: assume worst case */
            state.gpio_failure = true;
        }
        else
        {
            state.gpio_failure = false;
            /* 2. Apply active_low, evaluate input conditions */
            evaluate_inputs(di_raw, &state);
        }

        /* 3. Check for reset commands (non-blocking ZMQ poll) */
        check_reset_commands(rep_sock, &state, di_raw);

        /* 4. Compute output bitmask from response matrix */
        uint8_t do_mask = evaluate_response_matrix(&state);

        /* 5. Convert bitmask to per-pin array and write DO */
        uint8_t do_out[8];
        for (int i = 0; i < 8; i++)
        {
            do_out[i] = (do_mask >> i) & 1;
        }
        gpio->write_do(do_out, 8);

        /* 6. Write to RTDB */
        rtdb_write_gpio(rtdb, di_raw, 8, do_out, 8);

        /* 7. Publish events for state changes */
        for (int i = 0; i < 8; i++)
        {
            if (do_out[i] != do_prev[i])
            {
                publish_safety_event(pub_sock, push_sock,
                    do_out[i] ? "output_asserted" : "output_cleared",
                    /* message with DO name */,
                    EMS_SEVERITY_CRITICAL, ++seq);
            }
        }
        memcpy(do_prev, do_out, 8);

        /* 8. Signal watchdog thread that scan is complete */
        signal_scan_complete();

        /* 9. Sleep for scan interval (10-20ms target, well under 100ms) */
        struct timespec sleep_ts = {0, 10 * 1000000};  /* 10ms */
        nanosleep(&sleep_ts, NULL);
    }
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| sysfs GPIO (/sys/class/gpio/) | libgpiod chardev (/dev/gpiochipN) | Kernel 4.8+ (2016), sysfs deprecated 5.x | Must use libgpiod, sysfs is gone in modern kernels |
| libgpiod v1 (gpiod_chip_get_line) | libgpiod v2 (gpiod_chip_request_lines) | libgpiod 2.0 (2023) | v1 API removed; v2 uses settings/config objects |
| Watchdog daemon (watchdog package) | Application-managed /dev/watchdog | Always available | Direct ioctl gives tighter control over kick timing |

**Deprecated/outdated:**
- sysfs GPIO: completely deprecated, do not use
- libgpiod v1 API: functions removed in v2, all examples using gpiod_line_get/set are outdated

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (Python integration) + custom C unit tests (CMake/CTest) |
| Config file | `pyproject.toml` (pytest section exists) |
| Quick run command | `cd /home/overlord/Dev/Revx_Energy/EMS && uv run pytest tests/test_safety_manager.py -x` |
| Full suite command | `cd /home/overlord/Dev/Revx_Energy/EMS && uv run pytest tests/ -x && cmake --build build && ctest --test-dir build` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SAFE-01 | E-Stop dual-channel detect + wiring fault | integration | `uv run pytest tests/test_safety_manager.py::test_estop_dual_channel -x` | No -- Wave 0 |
| SAFE-02 | E-Stop response outputs within timing | integration | `uv run pytest tests/test_safety_manager.py::test_estop_response_outputs -x` | No -- Wave 0 |
| SAFE-03 | Fire dual-confirm logic | integration | `uv run pytest tests/test_safety_manager.py::test_fire_dual_confirm -x` | No -- Wave 0 |
| SAFE-04 | Flood response outputs | integration | `uv run pytest tests/test_safety_manager.py::test_flood_response -x` | No -- Wave 0 |
| SAFE-05 | Watchdog kick after scan cycle | unit (C) | `ctest --test-dir build -R test_watchdog` | No -- Wave 0 |
| SAFE-06 | SCHED_FIFO + mlockall setup | unit (C) | `ctest --test-dir build -R test_rt_setup` | No -- Wave 0 |
| SAFE-07 | Watchdog thread higher priority | integration | `uv run pytest tests/test_safety_manager.py::test_watchdog_thread_priority -x` | No -- Wave 0 |
| SAFE-08 | RTDB GPIO seqlock writes | integration | `uv run pytest tests/test_safety_manager.py::test_rtdb_gpio_writes -x` | No -- Wave 0 |
| SAFE-09 | ZMQ safety events published | integration | `uv run pytest tests/test_safety_manager.py::test_zmq_safety_events -x` | No -- Wave 0 |
| SAFE-10 | Independent lifecycle (systemd) | integration | `uv run pytest tests/test_safety_manager.py::test_independent_lifecycle -x` | No -- Wave 0 |
| SAFE-11 | GPIO failure worst-case response | integration | `uv run pytest tests/test_safety_manager.py::test_gpio_failure_response -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_safety_manager.py -x` (integration tests via GPIO harness)
- **Per wave merge:** Full suite including C unit tests and GPIO harness tests
- **Phase gate:** All SAFE-01 through SAFE-11 tests green + timing validation

### Wave 0 Gaps
- [ ] `tests/test_safety_manager.py` -- integration tests using GPIO harness RTDB backend to stimulate DI and verify DO responses
- [ ] `src/safety_manager/tests/CMakeLists.txt` -- C unit test build config
- [ ] `src/safety_manager/tests/test_response_matrix.c` -- pure C unit tests for response matrix logic (no hardware)
- [ ] libgpiod-dev install: `sudo pacman -S libgpiod` (Arch) or `sudo apt install libgpiod-dev` (Ubuntu)
- [ ] CMakeLists.txt update: link gpiod, zmq, mpack, ems_rtdb, pthread, rt

### Testing Strategy: GPIO Harness as Test Backend
The GPIO harness RTDB backend (tools/simulators/gpio_harness/rtdb_backend.py) provides the test stimulus path:
1. Test creates RTDB shm segment via RtdbBackend
2. safety_manager process starts with --rtdb-backend flag (or env var) to use RTDB reads instead of libgpiod
3. Test calls `rtdb_backend.set_di_multi({6: 1, 7: 0})` to simulate E-Stop
4. Test reads `rtdb_backend.get_do(0)` to verify ACDB trip was asserted
5. Test measures elapsed time between DI write and DO assertion for timing validation

This requires the GPIO abstraction layer (gpio_ops_t vtable) to support both libgpiod and RTDB backends.

## Open Questions

1. **GPIO chip path on ECU-1170-552A**
   - What we know: AM6548 has multiple GPIO controllers, chip path is typically /dev/gpiochip0 through /dev/gpiochipN
   - What's unclear: Which chip and which line offsets correspond to the 8 DI and 8 DO on the ECU-1170-552A
   - Recommendation: Make chip path and line offsets configurable (already in gpio_config.yaml schema space). Default to /dev/gpiochip0 with offsets 0-7 for DI and 8-15 for DO. Real values will come from ECU-1170-552A hardware bringup.

2. **Watchdog timeout value**
   - What we know: SAFE-05 specifies "kicked every 500ms"
   - What's unclear: Optimal watchdog timeout. If kick is every 500ms, timeout should be 2-3x that (1-2 seconds). Too short = spurious reboots. Too long = slow recovery from real hangs.
   - Recommendation: Set timeout to 2 seconds (4x the kick interval). Make it configurable via gpio_config.yaml or CLI arg.

3. **Config loading fallback**
   - What we know: Safety_manager reads config once at startup. Config_manager serves it via ZMQ.
   - What's unclear: If config_manager is not running at safety_manager start, should it: (a) retry ZMQ until config_manager appears, (b) read gpio_config.yaml directly from disk, or (c) use compiled-in defaults?
   - Recommendation: Try ZMQ first with a short timeout (1 second), then fall back to direct file read from /opt/ems/config/gpio_config.yaml. Compiled-in defaults as last resort. This preserves SAFE-10 independence.

## Sources

### Primary (HIGH confidence)
- [libgpiod v2 official docs](https://libgpiod.readthedocs.io/en/latest/) -- chip, line_request, line_settings, line_config APIs
- [Linux Watchdog API docs](https://www.kernel.org/doc/html/v5.9/watchdog/watchdog-api.html) -- ioctl interface, magic close, timeout setting
- [Linux RT wiki - stack prefault pattern](https://rt.wiki.kernel.org/index.php/Threaded_RT-application_with_memory_locking_and_stack_handling_example) -- mlockall + stack pre-fault
- [sched(7) man page](https://man7.org/linux/man-pages/man7/sched.7.html) -- SCHED_FIFO semantics
- Project source code: rtdb.h, seqlock.h, ipc_defs.h, ems_types.h, rtdb_lifecycle.h, gpio_config.yaml, test_gpio_harness.py

### Secondary (MEDIUM confidence)
- [ICS libgpiod blog](https://www.ics.com/blog/gpio-programming-exploring-libgpiod-library) -- v2 migration guide
- [Linux RT application development](https://shuhaowu.com/blog/2022/04-linux-rt-appdev-part4.html) -- practical SCHED_FIFO + mlockall examples

### Tertiary (LOW confidence)
- GPIO chip numbering on AM6548 -- needs hardware verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries are established (libgpiod, POSIX RT, watchdog API), project infrastructure verified in source
- Architecture: HIGH -- two-thread model is well-understood RT pattern; response matrix is deterministic and fully specified in CONTEXT.md
- Pitfalls: HIGH -- RT scheduling, libgpiod v2 migration, and watchdog management are well-documented problem areas
- GPIO hardware mapping: LOW -- actual chip/line offsets unknown until ECU-1170-552A hardware testing

**Research date:** 2026-03-14
**Valid until:** 2026-04-14 (stable domain, libgpiod v2 API is mature)
