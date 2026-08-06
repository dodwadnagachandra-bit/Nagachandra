#include "can_health.h"
#include "can_reader.h"
#include "comm_event.h"
#include "ems_types.h"
#include "ipc_defs.h"
#include "rtdb.h"
#include "rtdb_lifecycle.h"
#include "seqlock.h"

#include <errno.h>
#include <getopt.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/**
 * @file main.c
 * @brief EMS comm_manager_c entry point -- CAN BMS reader process.
 *
 * This is the crash-isolated C process that reads BMS CAN frames from
 * SocketCAN interfaces and writes decoded telemetry to RTDB shared memory.
 * It runs as an independent systemd service (comm_manager_c) with no
 * dependency on the Python Modbus process.
 *
 * Startup sequence:
 *   1. Parse CLI arguments (interface, base-id, topology, timeouts)
 *   2. Install SIGTERM/SIGINT handlers
 *   3. Attach to RTDB via rtdb_attach(), validate magic/version
 *   4. Initialize ZMQ comm event publisher
 *   5. Initialize last_update_ms for all racks (prevent false timeouts)
 *   6. Spawn CAN reader thread(s) -- one per interface
 *   7. Spawn heartbeat health monitor thread
 *   8. Main thread: pause() loop until signal
 *   9. Shutdown: join threads, close ZMQ, detach RTDB
 */

/* ---------------------------------------------------------------------------
 * Constants
 * -------------------------------------------------------------------------*/

/** Default CAN interface name. */
#define DEFAULT_CAN_INTERFACE   "can0"

/** Default base CAN ID (BMS Layer 2 DBC). */
#define DEFAULT_BASE_CAN_ID     0x98FF0003U

/** Default heartbeat timeout in milliseconds. */
#define DEFAULT_HEARTBEAT_TIMEOUT_MS  900

/** Default heartbeat check interval in milliseconds. */
#define DEFAULT_CHECK_INTERVAL_MS     300

/** Maximum number of CAN interfaces (one per cluster). */
#define MAX_CAN_IFACE_COUNT     MAX_CAN_INTERFACES

/* ---------------------------------------------------------------------------
 * Globals
 * -------------------------------------------------------------------------*/

/** Shutdown signal flag -- shared with reader and health threads. */
static volatile sig_atomic_t g_running = 1;

/* ---------------------------------------------------------------------------
 * Signal handling
 * -------------------------------------------------------------------------*/

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

/**
 * Install signal handlers for graceful shutdown.
 */
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
 * CLI argument parsing
 * -------------------------------------------------------------------------*/

/** CLI options. */
typedef struct
{
    const char *interfaces[MAX_CAN_IFACE_COUNT]; /**< CAN interface names  */
    int         iface_count;                     /**< Number of interfaces  */
    uint32_t    base_can_id;                     /**< Base CAN ID           */
    int         clusters;                        /**< Number of clusters    */
    int         racks_per_cluster;               /**< Racks per cluster     */
    uint32_t    heartbeat_timeout_ms;            /**< Heartbeat timeout     */
    uint32_t    check_interval_ms;               /**< Health check interval */
} cli_opts_t;

static void print_usage(const char *prog)
{
    fprintf(stderr,
            "Usage: %s [OPTIONS]\n"
            "\n"
            "EMS Comm Manager (C) -- CAN BMS reader process\n"
            "\n"
            "Options:\n"
            "  --interface NAME       CAN interface (repeatable, max %d)\n"
            "  --base-id HEX          Base CAN ID (default: 0x%08X)\n"
            "  --clusters N           Number of clusters (default: from RTDB)\n"
            "  --racks-per-cluster N  Racks per cluster (default: from RTDB)\n"
            "  --heartbeat-timeout-ms N  Heartbeat timeout ms (default: %d)\n"
            "  --check-interval-ms N  Health check interval ms (default: %d)\n"
            "  --help                 Show this help\n",
            prog, MAX_CAN_IFACE_COUNT, DEFAULT_BASE_CAN_ID,
            DEFAULT_HEARTBEAT_TIMEOUT_MS, DEFAULT_CHECK_INTERVAL_MS);
}

/**
 * Parse CLI arguments using getopt_long.
 *
 * @return 0 on success, -1 on error, 1 if --help was printed.
 */
static int parse_cli(int argc, char **argv, cli_opts_t *opts)
{
    static const struct option long_opts[] = {
        {"interface",           required_argument, NULL, 'i'},
        {"base-id",             required_argument, NULL, 'b'},
        {"clusters",            required_argument, NULL, 'c'},
        {"racks-per-cluster",   required_argument, NULL, 'r'},
        {"heartbeat-timeout-ms", required_argument, NULL, 't'},
        {"check-interval-ms",   required_argument, NULL, 'k'},
        {"help",                no_argument,       NULL, 'h'},
        {NULL, 0, NULL, 0}
    };

    /* Set defaults */
    memset(opts, 0, sizeof(*opts));
    opts->base_can_id = DEFAULT_BASE_CAN_ID;
    opts->clusters = 0;           /* 0 = read from RTDB */
    opts->racks_per_cluster = 0;  /* 0 = read from RTDB */
    opts->heartbeat_timeout_ms = DEFAULT_HEARTBEAT_TIMEOUT_MS;
    opts->check_interval_ms = DEFAULT_CHECK_INTERVAL_MS;

    int opt;
    while ((opt = getopt_long(argc, argv, "i:b:c:r:t:k:h",
                              long_opts, NULL)) != -1)
    {
        switch (opt)
        {
        case 'i':
            if (opts->iface_count >= MAX_CAN_IFACE_COUNT)
            {
                fprintf(stderr, "[comm_manager_c] ERROR: max %d interfaces\n",
                        MAX_CAN_IFACE_COUNT);
                return -1;
            }
            opts->interfaces[opts->iface_count++] = optarg;
            break;
        case 'b':
            opts->base_can_id = (uint32_t)strtoul(optarg, NULL, 0);
            break;
        case 'c':
            opts->clusters = atoi(optarg);
            if (opts->clusters < 1 || opts->clusters > MAX_CLUSTERS)
            {
                fprintf(stderr, "[comm_manager_c] ERROR: clusters must be "
                        "1-%d\n", MAX_CLUSTERS);
                return -1;
            }
            break;
        case 'r':
            opts->racks_per_cluster = atoi(optarg);
            if (opts->racks_per_cluster < 1 ||
                opts->racks_per_cluster > MAX_RACKS_PER_CLUSTER)
            {
                fprintf(stderr, "[comm_manager_c] ERROR: racks-per-cluster "
                        "must be 1-%d\n", MAX_RACKS_PER_CLUSTER);
                return -1;
            }
            break;
        case 't':
            opts->heartbeat_timeout_ms = (uint32_t)atoi(optarg);
            if (opts->heartbeat_timeout_ms < 100)
            {
                fprintf(stderr, "[comm_manager_c] ERROR: heartbeat-timeout-ms "
                        "must be >= 100\n");
                return -1;
            }
            break;
        case 'k':
            opts->check_interval_ms = (uint32_t)atoi(optarg);
            if (opts->check_interval_ms < 50)
            {
                fprintf(stderr, "[comm_manager_c] ERROR: check-interval-ms "
                        "must be >= 50\n");
                return -1;
            }
            break;
        case 'h':
            print_usage(argv[0]);
            return 1;
        default:
            print_usage(argv[0]);
            return -1;
        }
    }

    /* Default interface if none specified */
    if (opts->iface_count == 0)
    {
        opts->interfaces[0] = DEFAULT_CAN_INTERFACE;
        opts->iface_count = 1;
    }

    return 0;
}

/* ---------------------------------------------------------------------------
 * Main
 * -------------------------------------------------------------------------*/

int main(int argc, char **argv)
{
    fprintf(stderr, "[comm_manager_c] EMS Comm Manager (C) %s starting\n",
            EMS_VERSION);

    /* --- Parse CLI arguments --- */
    cli_opts_t opts;
    int rc = parse_cli(argc, argv, &opts);
    if (rc != 0)
    {
        return (rc == 1) ? 0 : 1;  /* 1 = --help, clean exit */
    }

    /* --- Signal handlers --- */
    setup_signal_handlers();

    /* --- Attach RTDB --- */
    ems_rtdb_t *rtdb = rtdb_attach();
    if (!rtdb)
    {
        fprintf(stderr, "[comm_manager_c] ERROR: rtdb_attach failed -- "
                "is data_manager_c running?\n");
        return 1;
    }

    /* Validate RTDB header */
    if (rtdb->magic != RTDB_MAGIC || rtdb->version != RTDB_VERSION)
    {
        fprintf(stderr, "[comm_manager_c] ERROR: RTDB invalid "
                "(magic=0x%08X version=%u)\n",
                rtdb->magic, rtdb->version);
        rtdb_detach(rtdb);
        return 1;
    }

    /* Read topology from RTDB if not overridden by CLI */
    int clusters = opts.clusters > 0
                       ? opts.clusters
                       : (int)rtdb->cluster_count;
    int racks_per_cluster = opts.racks_per_cluster > 0
                                ? opts.racks_per_cluster
                                : (int)rtdb->racks_per_cluster;

    if (clusters == 0)
    {
        clusters = 1;
    }
    if (racks_per_cluster == 0)
    {
        racks_per_cluster = 4;
    }

    fprintf(stderr, "[comm_manager_c] topology: %d cluster(s), "
            "%d rack(s) per cluster\n", clusters, racks_per_cluster);

    /* --- Initialize ZMQ comm event publisher --- */
    comm_event_ctx_t evt_ctx;
    memset(&evt_ctx, 0, sizeof(evt_ctx));
    bool evt_ok = (comm_event_init(&evt_ctx, EMS_SOCK_TELEMETRY,
                                    EMS_SOCK_LOGGER) == 0);
    if (!evt_ok)
    {
        fprintf(stderr, "[comm_manager_c] WARNING: event publishing "
                "unavailable -- continuing without events\n");
    }

    /* --- Initialize rack timestamps to prevent false timeouts --- */
    can_health_init_timestamps(rtdb, clusters, racks_per_cluster);

    /* --- Spawn CAN reader threads --- */
    pthread_t reader_threads[MAX_CAN_IFACE_COUNT];
    can_reader_config_t reader_configs[MAX_CAN_IFACE_COUNT];
    int reader_count = 0;

    for (int i = 0; i < opts.iface_count && i < clusters; i++)
    {
        memset(&reader_configs[i], 0, sizeof(reader_configs[i]));
        reader_configs[i].interface         = opts.interfaces[i];
        reader_configs[i].base_can_id       = opts.base_can_id;
        reader_configs[i].cluster_index     = i;
        reader_configs[i].racks_per_cluster = racks_per_cluster;
        reader_configs[i].rtdb              = rtdb;
        reader_configs[i].event_ctx         = evt_ok ? &evt_ctx : NULL;
        reader_configs[i].running           = &g_running;

        int prc = pthread_create(&reader_threads[i], NULL,
                                 can_reader_thread, &reader_configs[i]);
        if (prc != 0)
        {
            fprintf(stderr, "[comm_manager_c] ERROR: pthread_create "
                    "(reader %s) failed: %s\n",
                    opts.interfaces[i], strerror(prc));
        }
        else
        {
            reader_count++;
            fprintf(stderr, "[comm_manager_c] reader thread started: "
                    "%s (cluster %d)\n", opts.interfaces[i], i);
        }
    }

    /* --- Spawn heartbeat health monitor thread --- */
    pthread_t health_thread_id;
    can_health_config_t health_cfg;
    bool health_started = false;

    memset(&health_cfg, 0, sizeof(health_cfg));
    health_cfg.rtdb              = rtdb;
    health_cfg.event_ctx         = evt_ok ? &evt_ctx : NULL;
    health_cfg.cluster_count     = clusters;
    health_cfg.racks_per_cluster = racks_per_cluster;
    health_cfg.timeout_ms        = opts.heartbeat_timeout_ms;
    health_cfg.check_interval_ms = opts.check_interval_ms;
    health_cfg.running           = &g_running;

    int hrc = pthread_create(&health_thread_id, NULL,
                             can_health_thread, &health_cfg);
    if (hrc != 0)
    {
        fprintf(stderr, "[comm_manager_c] ERROR: pthread_create "
                "(health) failed: %s\n", strerror(hrc));
    }
    else
    {
        health_started = true;
        fprintf(stderr, "[comm_manager_c] health thread started\n");
    }

    /* --- Log startup complete --- */
    fprintf(stderr, "[comm_manager_c] startup complete "
            "(interfaces=%d, base_id=0x%08X, "
            "heartbeat_timeout=%u ms, check_interval=%u ms)\n",
            opts.iface_count, opts.base_can_id,
            opts.heartbeat_timeout_ms, opts.check_interval_ms);

    if (evt_ok)
    {
        comm_event_publish(&evt_ctx, "lifecycle",
                           "comm_manager_c started", EMS_SEVERITY_INFO);
    }

    /* --- Main thread: wait for shutdown signal --- */
    while (g_running)
    {
        pause();  /* Unblocked by signal */
    }

    /* =====================================================================
     * SHUTDOWN SEQUENCE
     * ===================================================================*/

    fprintf(stderr, "[comm_manager_c] shutting down\n");

    /* Join reader threads */
    for (int i = 0; i < reader_count; i++)
    {
        pthread_join(reader_threads[i], NULL);
        fprintf(stderr, "[comm_manager_c] reader thread %d joined\n", i);
    }

    /* Join health thread */
    if (health_started)
    {
        pthread_join(health_thread_id, NULL);
        fprintf(stderr, "[comm_manager_c] health thread joined\n");
    }

    /* Close ZMQ event publisher */
    if (evt_ok)
    {
        comm_event_publish(&evt_ctx, "lifecycle",
                           "comm_manager_c stopped", EMS_SEVERITY_INFO);
        comm_event_close(&evt_ctx);
    }

    /* Detach RTDB */
    rtdb_detach(rtdb);

    fprintf(stderr, "[comm_manager_c] shutdown complete\n");
    return 0;
}
