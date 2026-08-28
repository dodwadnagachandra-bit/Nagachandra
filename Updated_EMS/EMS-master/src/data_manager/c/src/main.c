/**
 * @file main.c
 * @brief data_manager_c — RTDB owner process.
 *
 * Creates the RTDB shared memory segment and holds it open until SIGTERM
 * or SIGINT. On shutdown, detaches and destroys (unlinks) the shm segment.
 *
 * Usage: data_manager_c <clusters> <racks> <modules> <cells> <temps>
 * Example: data_manager_c 1 4 10 16 8
 */

#include "ems_types.h"
#include "rtdb.h"
#include "rtdb_lifecycle.h"

#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static volatile sig_atomic_t g_shutdown = 0;

static void signal_handler(int sig)
{
    (void)sig;
    g_shutdown = 1;
}

static void install_signal_handlers(void)
{
    struct sigaction sa;
    sa.sa_handler = signal_handler;
    sa.sa_flags = 0;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
}

static void print_usage(const char *prog)
{
    fprintf(stderr, "Usage: %s <clusters> <racks> <modules> <cells> <temps>\n", prog);
    fprintf(stderr, "  clusters  — number of battery clusters (1-%d)\n", MAX_CLUSTERS);
    fprintf(stderr, "  racks     — racks per cluster (1-%d)\n", MAX_RACKS_PER_CLUSTER);
    fprintf(stderr, "  modules   — modules per rack (1-%d)\n", MAX_MODULES_PER_RACK);
    fprintf(stderr, "  cells     — cells per module (1-%d)\n", MAX_CELLS_PER_MODULE);
    fprintf(stderr, "  temps     — temp sensors per module (1-%d)\n", MAX_TEMPS_PER_MODULE);
}

int main(int argc, char *argv[])
{
    printf("EMS Data Manager (C) %s\n", EMS_VERSION);

    if (argc != 6)
    {
        print_usage(argv[0]);
        return 1;
    }

    uint8_t clusters = (uint8_t)atoi(argv[1]);
    uint8_t racks    = (uint8_t)atoi(argv[2]);
    uint8_t modules  = (uint8_t)atoi(argv[3]);
    uint8_t cells    = (uint8_t)atoi(argv[4]);
    uint8_t temps    = (uint8_t)atoi(argv[5]);

    /* Validate ranges */
    if (clusters == 0 || clusters > MAX_CLUSTERS ||
        racks == 0 || racks > MAX_RACKS_PER_CLUSTER ||
        modules == 0 || modules > MAX_MODULES_PER_RACK ||
        cells == 0 || cells > MAX_CELLS_PER_MODULE ||
        temps == 0 || temps > MAX_TEMPS_PER_MODULE)
    {
        fprintf(stderr, "Error: topology values out of range\n");
        print_usage(argv[0]);
        return 1;
    }

    install_signal_handlers();

    ems_rtdb_t *rtdb = rtdb_create(clusters, racks, modules, cells, temps);
    if (rtdb == NULL)
    {
        fprintf(stderr, "Error: failed to create RTDB\n");
        return 1;
    }

    printf("RTDB created: %zu bytes, %uc/%ur/%um/%ucell/%utemp\n",
           sizeof(ems_rtdb_t), clusters, racks, modules, cells, temps);

    /* Block until shutdown signal */
    while (!g_shutdown)
    {
        pause(); /* Suspend until signal delivery */
    }

    printf("Shutting down data_manager_c...\n");
    rtdb_detach(rtdb);
    rtdb_destroy();
    printf("RTDB destroyed. Goodbye.\n");

    return 0;
}
