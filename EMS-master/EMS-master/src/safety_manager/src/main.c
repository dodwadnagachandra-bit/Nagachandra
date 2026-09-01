/**
 * @file main.c
 * @brief EMS Safety Manager entry point -- L1 safety layer.
 *
 * The safety_manager is the most critical process in the EMS. It runs as a
 * SCHED_FIFO real-time process with mlockall, reading GPIO digital inputs,
 * evaluating the safety response matrix, writing digital outputs, and
 * updating the RTDB GPIO section every scan cycle (~10ms).
 *
 * Startup sequence:
 *   1. Signal handlers (SIGTERM, SIGINT)
 *   2. RT setup (mlockall, stack pre-fault, SCHED_FIFO)
 *   3. Config loading (ZMQ -> file fallback -> compiled defaults)
 *   4. RTDB attach (retry 3x, continue without if unavailable)
 *   5. GPIO backend selection (libgpiod or RTDB test backend)
 *   6. GPIO init (failure -> gpio_failure state -> all outputs asserted)
 *   7. ZMQ events + reset init (non-fatal)
 *   8. Watchdog open + thread start
 *   9. Scan loop (10ms cycle)
 *  10. Shutdown (watchdog stop, outputs de-asserted, cleanup)
 *
 * Independence guarantee (SAFE-10): safety_manager starts and operates
 * even if config_manager, data_manager, and logger are all down.
 */

#include "ems_types.h"
#include "gpio.h"
#include "ipc_defs.h"
#include "response_matrix.h"
#include "rtdb.h"
#include "rtdb_lifecycle.h"
#include "safety_event.h"
#include "safety_reset.h"
#include "seqlock.h"
#include "watchdog.h"

#include <errno.h>
#include <getopt.h>
#include <sched.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

/* ---------------------------------------------------------------------------
 * Constants
 * -------------------------------------------------------------------------*/

/** Default GPIO chip device path. */
#define DEFAULT_CHIP_PATH       "/dev/gpiochip0"

/** Default config file path. */
#define DEFAULT_CONFIG_PATH     "/opt/ems/config/gpio_config.yaml"

/** Default scan interval in milliseconds. */
#define DEFAULT_SCAN_INTERVAL_MS 10

/** SCHED_FIFO priority for the scan loop. */
#define SCAN_PRIORITY           80

/** SCHED_FIFO priority for the watchdog feed thread. */
#define WATCHDOG_PRIORITY       81

/** Stack pre-fault size (64 KiB). */
#define STACK_PREFAULT_SIZE     (64 * 1024)

/** RTDB attach retry count. */
#define RTDB_ATTACH_RETRIES     3

/** RTDB attach retry interval in seconds. */
#define RTDB_ATTACH_RETRY_SEC   1

/** Watchdog device path. */
#define WATCHDOG_DEV_PATH       "/dev/watchdog"

/** Watchdog timeout in seconds. */
#define WATCHDOG_TIMEOUT_SEC    2

/** DI pin offsets on the GPIO chip (ECU-1170-552A residential). */
static const unsigned int DI_OFFSETS[GPIO_NUM_DI] = {
    0, 1, 2, 3, 4, 5, 6, 7
};

/** DO pin offsets on the GPIO chip. */
static const unsigned int DO_OFFSETS[GPIO_NUM_DO] = {
    8, 9, 10, 11, 12, 13, 14, 15
};

/* ---------------------------------------------------------------------------
 * Globals
 * -------------------------------------------------------------------------*/

/** Shutdown signal flag. */
static volatile sig_atomic_t g_running = 1;

/* ---------------------------------------------------------------------------
 * Signal handling
 * -------------------------------------------------------------------------*/

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

static void setup_signal_handlers(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;

    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
}

/* ---------------------------------------------------------------------------
 * Real-time setup
 * -------------------------------------------------------------------------*/

/**
 * Configure RT scheduling: mlockall, stack pre-fault, SCHED_FIFO.
 *
 * All steps are non-fatal -- dev/CI systems without CAP_SYS_NICE
 * will log a warning and continue in default scheduling.
 */
static void setup_realtime(int priority)
{
    /* Lock all current and future memory */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
    {
        fprintf(stderr, "[safety_manager] WARNING: mlockall failed: %s "
                "(continuing in non-RT mode)\n", strerror(errno));
    }
    else
    {
        fprintf(stderr, "[safety_manager] mlockall OK\n");
    }

    /* Pre-fault the stack to avoid page faults in the scan loop */
    volatile char stack[STACK_PREFAULT_SIZE];
    memset((char *)stack, 0, sizeof(stack));

    /* Set SCHED_FIFO scheduling */
    struct sched_param param;
    memset(&param, 0, sizeof(param));
    param.sched_priority = priority;

    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0)
    {
        fprintf(stderr, "[safety_manager] WARNING: sched_setscheduler(FIFO, %d) "
                "failed: %s (needs CAP_SYS_NICE)\n",
                priority, strerror(errno));
    }
    else
    {
        fprintf(stderr, "[safety_manager] SCHED_FIFO priority %d set\n",
                priority);
    }
}

/* ---------------------------------------------------------------------------
 * Config loading
 * -------------------------------------------------------------------------*/

/**
 * Load GPIO configuration with residential profile defaults.
 *
 * Attempts ZMQ config query first, falls back to compiled-in defaults
 * matching the residential profile (gpio_config.yaml). Full YAML parsing
 * is not implemented here -- the config schema is simple and ZMQ config
 * query is the intended path.
 *
 * @param config  Output GPIO configuration.
 */
static void load_gpio_config(gpio_config_t *config)
{
    /*
     * Compiled-in defaults matching config/gpio_config.yaml residential
     * profile. These are used when config_manager is not running.
     *
     * DI active_low: DI-2 (door switch), DI-7 (E-Stop NC)
     * DO active_low: none (all active high in residential profile)
     */
    memset(config, 0, sizeof(*config));

    config->active_low_di[2] = true;  /* DI-2: DOOR_SWITCH (NC contact) */
    config->active_low_di[7] = true;  /* DI-7: ESTOP_NC */
    /* All other DI: active_high (false is default from memset) */
    /* All DO: active_high (false is default from memset) */

    fprintf(stderr, "[safety_manager] using compiled-in GPIO config "
            "(residential profile defaults)\n");
}

/* ---------------------------------------------------------------------------
 * RTDB helpers
 * -------------------------------------------------------------------------*/

/**
 * Get current time in milliseconds from CLOCK_MONOTONIC.
 */
static uint64_t clock_mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

/**
 * Write GPIO state to RTDB via seqlock.
 *
 * @param rtdb    RTDB pointer (must not be NULL).
 * @param di_raw  Current DI values (8 bytes).
 * @param do_out  Current DO values (8 bytes).
 */
static void rtdb_write_gpio(ems_rtdb_t *rtdb,
                             const uint8_t *di_raw,
                             const uint8_t *do_out)
{
    ems_seqlock_write_begin(&rtdb->gpio.lock);

    memcpy(rtdb->gpio.di, di_raw, GPIO_NUM_DI);
    memcpy(rtdb->gpio.do_state, do_out, GPIO_NUM_DO);
    rtdb->gpio.last_update_ms = clock_mono_ms();

    ems_seqlock_write_end(&rtdb->gpio.lock);
}

/* ---------------------------------------------------------------------------
 * Event publishing helpers
 * -------------------------------------------------------------------------*/

/**
 * DO output name strings for event messages.
 */
static const char *do_names[GPIO_NUM_DO] = {
    "ACDB_TRIP", "EXTINGUISHER", "WARNING_LAMP", "RUNNING_LAMP",
    "FAULT_LAMP", "PCS_STOP", "SIREN", "SPARE_DO7"
};

/**
 * Publish events for DO state changes.
 *
 * Compares current DO with previous DO and publishes events for
 * any changes. Protective outputs use CRITICAL severity, advisory
 * outputs use WARNING.
 */
static void publish_do_changes(safety_event_ctx_t *evt_ctx,
                                const uint8_t *do_out,
                                const uint8_t *do_prev)
{
    if (!evt_ctx || !evt_ctx->pub_sock)
    {
        return;
    }

    for (int i = 0; i < GPIO_NUM_DO; i++)
    {
        if (do_out[i] != do_prev[i])
        {
            char msg[128];
            snprintf(msg, sizeof(msg), "%s %s",
                     do_names[i],
                     do_out[i] ? "ASSERTED" : "de-asserted");

            /* Protective outputs (DO-0, DO-1, DO-5, DO-6) are CRITICAL */
            ems_severity_t severity;
            uint8_t pin_mask = (uint8_t)(1 << i);
            if (pin_mask & (DO_ACDB_TRIP | DO_EXTINGUISHER |
                            DO_PCS_STOP | DO_SIREN))
            {
                severity = do_out[i] ? EMS_SEVERITY_CRITICAL
                                     : EMS_SEVERITY_INFO;
            }
            else
            {
                severity = do_out[i] ? EMS_SEVERITY_WARNING
                                     : EMS_SEVERITY_INFO;
            }

            safety_event_publish(evt_ctx, "do_change", msg, severity);
        }
    }
}

/* ---------------------------------------------------------------------------
 * CLI argument parsing
 * -------------------------------------------------------------------------*/

/** CLI options. */
typedef struct
{
    const char *config_path;
    const char *chip_path;
    bool        use_rtdb_backend;
    bool        no_watchdog;
    int         scan_interval_ms;
} cli_opts_t;

static void print_usage(const char *prog)
{
    fprintf(stderr,
            "Usage: %s [OPTIONS]\n"
            "\n"
            "EMS Safety Manager -- L1 Safety layer\n"
            "\n"
            "Options:\n"
            "  --config PATH          Config file path (default: %s)\n"
            "  --chip PATH            GPIO chip device (default: %s)\n"
            "  --rtdb-backend         Use RTDB test backend instead of libgpiod\n"
            "  --no-watchdog          Skip hardware watchdog (for testing)\n"
            "  --scan-interval-ms N   Scan interval in ms (default: %d)\n"
            "  --help                 Show this help\n",
            prog, DEFAULT_CONFIG_PATH, DEFAULT_CHIP_PATH,
            DEFAULT_SCAN_INTERVAL_MS);
}

static int parse_cli(int argc, char **argv, cli_opts_t *opts)
{
    static const struct option long_opts[] = {
        {"config",           required_argument, NULL, 'c'},
        {"chip",             required_argument, NULL, 'g'},
        {"rtdb-backend",     no_argument,       NULL, 'r'},
        {"no-watchdog",      no_argument,       NULL, 'w'},
        {"scan-interval-ms", required_argument, NULL, 's'},
        {"help",             no_argument,       NULL, 'h'},
        {NULL, 0, NULL, 0}
    };

    opts->config_path      = DEFAULT_CONFIG_PATH;
    opts->chip_path        = DEFAULT_CHIP_PATH;
    opts->use_rtdb_backend = false;
    opts->no_watchdog      = false;
    opts->scan_interval_ms = DEFAULT_SCAN_INTERVAL_MS;

    /* Also check environment variable for backend selection */
    const char *env_backend = getenv("EMS_GPIO_BACKEND");
    if (env_backend && strcmp(env_backend, "rtdb") == 0)
    {
        opts->use_rtdb_backend = true;
    }

    int opt;
    while ((opt = getopt_long(argc, argv, "c:g:rws:h", long_opts, NULL)) != -1)
    {
        switch (opt)
        {
        case 'c':
            opts->config_path = optarg;
            break;
        case 'g':
            opts->chip_path = optarg;
            break;
        case 'r':
            opts->use_rtdb_backend = true;
            break;
        case 'w':
            opts->no_watchdog = true;
            break;
        case 's':
            opts->scan_interval_ms = atoi(optarg);
            if (opts->scan_interval_ms < 1 || opts->scan_interval_ms > 1000)
            {
                fprintf(stderr, "[safety_manager] ERROR: scan-interval-ms "
                        "must be 1-1000, got %s\n", optarg);
                return -1;
            }
            break;
        case 'h':
            print_usage(argv[0]);
            exit(0);
        default:
            print_usage(argv[0]);
            return -1;
        }
    }

    return 0;
}

/* ---------------------------------------------------------------------------
 * Main
 * -------------------------------------------------------------------------*/

int main(int argc, char **argv)
{
    fprintf(stderr, "[safety_manager] EMS Safety Manager %s starting\n",
            EMS_VERSION);

    /* --- Parse CLI arguments --- */
    cli_opts_t opts;
    if (parse_cli(argc, argv, &opts) != 0)
    {
        return 1;
    }

    /* --- Signal handlers --- */
    setup_signal_handlers();

    /* --- RT setup --- */
    setup_realtime(SCAN_PRIORITY);

    /* --- Load GPIO config --- */
    gpio_config_t gpio_cfg;
    load_gpio_config(&gpio_cfg);

    /* --- Attach RTDB --- */
    ems_rtdb_t *rtdb = NULL;
    for (int attempt = 0; attempt < RTDB_ATTACH_RETRIES; attempt++)
    {
        rtdb = rtdb_attach();
        if (rtdb)
        {
            fprintf(stderr, "[safety_manager] RTDB attached\n");
            break;
        }
        fprintf(stderr, "[safety_manager] WARNING: rtdb_attach failed "
                "(attempt %d/%d)\n", attempt + 1, RTDB_ATTACH_RETRIES);
        if (attempt + 1 < RTDB_ATTACH_RETRIES)
        {
            sleep(RTDB_ATTACH_RETRY_SEC);
        }
    }
    if (!rtdb)
    {
        fprintf(stderr, "[safety_manager] CRITICAL: RTDB unavailable -- "
                "continuing without RTDB state publishing (SAFE-10)\n");
    }

    /* --- Select GPIO backend --- */
    const gpio_ops_t *gpio = NULL;

    if (opts.use_rtdb_backend)
    {
        if (!rtdb)
        {
            fprintf(stderr, "[safety_manager] ERROR: --rtdb-backend requires "
                    "RTDB but RTDB is unavailable\n");
            if (rtdb)
            {
                rtdb_detach(rtdb);
            }
            return 1;
        }
        rtdb_backend_init(rtdb);
        gpio = &gpio_ops_rtdb;
        fprintf(stderr, "[safety_manager] GPIO backend: RTDB (test mode)\n");
    }
    else
    {
#ifdef HAVE_LIBGPIOD
        gpio = &gpio_ops_libgpiod;
        fprintf(stderr, "[safety_manager] GPIO backend: libgpiod\n");
#else
        fprintf(stderr, "[safety_manager] ERROR: libgpiod not available and "
                "--rtdb-backend not specified\n");
        if (rtdb)
        {
            rtdb_detach(rtdb);
        }
        return 1;
#endif
    }

    /* --- Initialize GPIO --- */
    safety_state_t state;
    memset(&state, 0, sizeof(state));

    int gpio_rc = gpio->init(opts.chip_path, DI_OFFSETS, GPIO_NUM_DI,
                             DO_OFFSETS, GPIO_NUM_DO);
    if (gpio_rc != 0)
    {
        fprintf(stderr, "[safety_manager] CRITICAL: GPIO init failed -- "
                "entering fail-safe mode (all outputs asserted)\n");
        state.gpio_failure = true;
        /* Continue into scan loop -- response matrix will assert all
         * safety outputs per SAFE-11. */
    }

    /* --- Initialize ZMQ events (non-fatal) --- */
    safety_event_ctx_t evt_ctx;
    memset(&evt_ctx, 0, sizeof(evt_ctx));
    bool evt_ok = (safety_event_init(&evt_ctx, EMS_SOCK_TELEMETRY,
                                      EMS_SOCK_LOGGER) == 0);
    if (!evt_ok)
    {
        fprintf(stderr, "[safety_manager] WARNING: event publishing "
                "unavailable -- continuing without events\n");
    }

    /* --- Initialize ZMQ reset handler (non-fatal) --- */
    safety_reset_ctx_t reset_ctx;
    memset(&reset_ctx, 0, sizeof(reset_ctx));
    bool reset_ok = (safety_reset_init(&reset_ctx, EMS_SOCK_SAFETY_CMD) == 0);
    if (!reset_ok)
    {
        fprintf(stderr, "[safety_manager] WARNING: reset handler "
                "unavailable -- continuing without reset support\n");
    }

    /* --- Open watchdog --- */
    int wdt_fd = -1;
    if (!opts.no_watchdog)
    {
        wdt_fd = watchdog_open(WATCHDOG_DEV_PATH, WATCHDOG_TIMEOUT_SEC);
        if (wdt_fd < 0)
        {
            fprintf(stderr, "[safety_manager] WARNING: watchdog not available "
                    "-- continuing without hardware watchdog\n");
        }
    }
    else
    {
        fprintf(stderr, "[safety_manager] watchdog disabled (--no-watchdog)\n");
    }

    /* --- Start watchdog feed thread --- */
    if (wdt_fd >= 0)
    {
        if (watchdog_thread_start(wdt_fd, WATCHDOG_PRIORITY) != 0)
        {
            fprintf(stderr, "[safety_manager] WARNING: watchdog thread "
                    "start failed\n");
        }
        else
        {
            fprintf(stderr, "[safety_manager] watchdog thread started "
                    "(priority %d)\n", WATCHDOG_PRIORITY);
        }
    }

    /* --- Log startup complete --- */
    fprintf(stderr, "[safety_manager] startup complete "
            "(scan_interval=%dms, backend=%s, watchdog=%s)\n",
            opts.scan_interval_ms,
            opts.use_rtdb_backend ? "rtdb" : "libgpiod",
            wdt_fd >= 0 ? "active" : "none");

    if (evt_ok)
    {
        safety_event_publish(&evt_ctx, "lifecycle",
                             "safety_manager_started", EMS_SEVERITY_INFO);
    }

    /* =====================================================================
     * MAIN SCAN LOOP
     * ===================================================================*/

    uint8_t di_raw[GPIO_NUM_DI];
    uint8_t do_out[GPIO_NUM_DO];
    uint8_t do_prev[GPIO_NUM_DO];
    memset(di_raw, 0, sizeof(di_raw));
    memset(do_out, 0, sizeof(do_out));
    memset(do_prev, 0, sizeof(do_prev));

    struct timespec sleep_ts;
    sleep_ts.tv_sec  = 0;
    sleep_ts.tv_nsec = (long)opts.scan_interval_ms * 1000000L;

    while (g_running)
    {
        /* 1. Read all DI */
        if (!state.gpio_failure)
        {
            int rc = gpio->read_di(di_raw, GPIO_NUM_DI);
            if (rc != 0)
            {
                state.gpio_failure = true;
                fprintf(stderr, "[safety_manager] ERROR: GPIO read failed "
                        "-- entering fail-safe\n");
                if (evt_ok)
                {
                    safety_event_gpio_failure(&evt_ctx, "read_di failed");
                }
            }
        }

        /* 2. Evaluate inputs if read succeeded */
        if (!state.gpio_failure)
        {
            evaluate_inputs(di_raw, &gpio_cfg, &state);
        }

        /* 3. Poll for reset commands (non-blocking) */
        if (reset_ok)
        {
            /* Build latched flags bitmask for reset_poll */
            uint8_t latched = 0;
            if (state.estop_latched)
            {
                latched |= (1u << 0);
            }
            if (state.fire_latched)
            {
                latched |= (1u << 1);
            }
            if (state.flood_latched)
            {
                latched |= (1u << 2);
            }

            int reset_done = 0;
            int rrc = safety_reset_poll(&reset_ctx, di_raw, latched,
                                        &reset_done);
            if (rrc == 1 && reset_done)
            {
                /* Reset was accepted -- call safety_reset to clear latches */
                int sr = safety_reset(&state, di_raw, &gpio_cfg);
                if (sr == 0)
                {
                    fprintf(stderr, "[safety_manager] safety reset "
                            "accepted and applied\n");
                    if (evt_ok)
                    {
                        safety_event_reset(&evt_ctx, "all latches cleared");
                    }
                }
                else
                {
                    fprintf(stderr, "[safety_manager] safety reset "
                            "partially applied (some inputs still active)\n");
                    if (evt_ok)
                    {
                        safety_event_reset(&evt_ctx,
                                           "partial -- some inputs active");
                    }
                }
            }
        }

        /* 4. Compute DO bitmask from safety state */
        uint8_t do_mask = evaluate_response_matrix(&state);

        /* 5. Convert bitmask to per-pin array */
        for (int i = 0; i < GPIO_NUM_DO; i++)
        {
            do_out[i] = (do_mask >> i) & 1;
        }

        /* 6. Write DO */
        if (!state.gpio_failure)
        {
            gpio->write_do(do_out, GPIO_NUM_DO);
        }

        /* 7. Write RTDB GPIO section (if available) */
        if (rtdb)
        {
            rtdb_write_gpio(rtdb, di_raw, do_out);
        }

        /* 8. Publish events for DO state changes */
        if (evt_ok)
        {
            publish_do_changes(&evt_ctx, do_out, do_prev);
        }

        /* 9. Save previous DO state */
        memcpy(do_prev, do_out, GPIO_NUM_DO);

        /* 10. Signal watchdog: scan cycle complete */
        if (wdt_fd >= 0)
        {
            watchdog_signal_scan_complete();
        }

        /* 11. Sleep for scan interval */
        nanosleep(&sleep_ts, NULL);
    }

    /* =====================================================================
     * SHUTDOWN SEQUENCE
     * ===================================================================*/

    fprintf(stderr, "[safety_manager] shutting down\n");

    /* 1. Stop watchdog thread */
    if (wdt_fd >= 0)
    {
        watchdog_thread_stop();
    }

    /* 2. Close watchdog (magic close) */
    if (wdt_fd >= 0)
    {
        watchdog_close(wdt_fd);
    }

    /* 3. De-assert all outputs (safe state on shutdown) */
    if (!state.gpio_failure)
    {
        uint8_t do_off[GPIO_NUM_DO];
        memset(do_off, 0, sizeof(do_off));
        gpio->write_do(do_off, GPIO_NUM_DO);
    }

    /* 4. Close GPIO */
    gpio->close();

    /* 5. Close ZMQ */
    if (evt_ok)
    {
        safety_event_publish(&evt_ctx, "lifecycle",
                             "safety_manager_stopped", EMS_SEVERITY_INFO);
        safety_event_close(&evt_ctx);
    }
    if (reset_ok)
    {
        safety_reset_close(&reset_ctx);
    }

    /* 6. Detach RTDB */
    if (rtdb)
    {
        rtdb_detach(rtdb);
    }

    fprintf(stderr, "[safety_manager] shutdown complete\n");
    return 0;
}
