#include "can_health.h"

#include "comm_event.h"
#include "ems_types.h"
#include "rtdb.h"
#include "seqlock.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

/**
 * @file can_health.c
 * @brief Timer-based heartbeat check with comm_fault/recovery events.
 *
 * The health monitor thread runs at a configurable interval (default 300ms)
 * and checks each rack's last_update_ms against CLOCK_MONOTONIC. Racks
 * that exceed the heartbeat timeout are marked offline and a comm_fault
 * event is published. Racks that return are published as comm_recovery.
 *
 * Startup: prev_online initialized to 0 (unknown) to avoid false recovery
 * events at process start.
 */

/**
 * Get current time in milliseconds from CLOCK_MONOTONIC.
 */
static uint64_t clock_mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

void can_health_init_timestamps(ems_rtdb_t *rtdb, int cluster_count,
                                int racks_per_cluster)
{
    if (!rtdb)
    {
        return;
    }

    uint64_t now = clock_mono_ms();

    for (int c = 0; c < cluster_count && c < MAX_CLUSTERS; c++)
    {
        for (int r = 0; r < racks_per_cluster && r < MAX_RACKS_PER_CLUSTER; r++)
        {
            ems_rack_t *rack = &rtdb->clusters[c].racks[r];
            ems_seqlock_write_begin(&rack->lock);
            rack->last_update_ms = now;
            ems_seqlock_write_end(&rack->lock);
        }
    }

    fprintf(stderr, "[can_health] initialized last_update_ms for %d clusters "
            "x %d racks to %lu ms\n",
            cluster_count, racks_per_cluster, (unsigned long)now);
}

void *can_health_thread(void *arg)
{
    can_health_config_t *cfg = (can_health_config_t *)arg;
    if (!cfg || !cfg->rtdb)
    {
        fprintf(stderr, "[can_health] ERROR: invalid config\n");
        return NULL;
    }

    fprintf(stderr, "[can_health] thread started (timeout=%u ms, "
            "interval=%u ms, clusters=%d, racks=%d)\n",
            cfg->timeout_ms, cfg->check_interval_ms,
            cfg->cluster_count, cfg->racks_per_cluster);

    /* Initialize prev_online to 0 (unknown) -- avoids false recovery events */
    memset(cfg->prev_online, 0, sizeof(cfg->prev_online));

    struct timespec sleep_ts;
    sleep_ts.tv_sec  = cfg->check_interval_ms / 1000;
    sleep_ts.tv_nsec = (long)(cfg->check_interval_ms % 1000) * 1000000L;

    while (*(cfg->running))
    {
        nanosleep(&sleep_ts, NULL);

        if (!*(cfg->running))
        {
            break;
        }

        uint64_t now = clock_mono_ms();

        for (int c = 0; c < cfg->cluster_count && c < MAX_CLUSTERS; c++)
        {
            for (int r = 0; r < cfg->racks_per_cluster &&
                            r < MAX_RACKS_PER_CLUSTER; r++)
            {
                ems_rack_t *rack = &cfg->rtdb->clusters[c].racks[r];

                /* Read last_update_ms and online via seqlock */
                uint64_t last_ms;
                uint8_t  online;
                uint32_t seq;
                do
                {
                    seq = ems_seqlock_read_begin(&rack->lock);
                    last_ms = rack->last_update_ms;
                    online  = rack->online;
                } while (ems_seqlock_read_retry(&rack->lock, seq));

                uint64_t age = now - last_ms;
                int is_stale = (age > cfg->timeout_ms);

                /* Detect offline transition: was online, now stale */
                if (is_stale && cfg->prev_online[c][r] == 1)
                {
                    /* Mark rack offline via seqlock */
                    ems_seqlock_write_begin(&rack->lock);
                    rack->online = 0;
                    ems_seqlock_write_end(&rack->lock);

                    /* Publish comm_fault event */
                    if (cfg->event_ctx)
                    {
                        char msg[128];
                        char dev_id[32];
                        snprintf(msg, sizeof(msg),
                                 "Rack %d:%d heartbeat timeout "
                                 "(age=%lu ms, threshold=%u ms)",
                                 c, r, (unsigned long)age,
                                 cfg->timeout_ms);
                        snprintf(dev_id, sizeof(dev_id), "rack_%d_%d", c, r);
                        comm_event_publish_with_data(
                            cfg->event_ctx, "heartbeat_timeout", msg,
                            EMS_SEVERITY_ERROR, dev_id, r);
                    }

                    cfg->prev_online[c][r] = 0;
                    fprintf(stderr, "[can_health] rack %d:%d OFFLINE "
                            "(age=%lu ms)\n", c, r, (unsigned long)age);
                }
                /* Detect online recovery: was offline, now not stale */
                else if (!is_stale && online == 1 &&
                         cfg->prev_online[c][r] == 0)
                {
                    if (cfg->event_ctx)
                    {
                        char msg[128];
                        char dev_id[32];
                        snprintf(msg, sizeof(msg),
                                 "Rack %d:%d communication recovered", c, r);
                        snprintf(dev_id, sizeof(dev_id), "rack_%d_%d", c, r);
                        comm_event_publish_with_data(
                            cfg->event_ctx, "comm_recovery", msg,
                            EMS_SEVERITY_INFO, dev_id, r);
                    }

                    cfg->prev_online[c][r] = 1;
                    fprintf(stderr, "[can_health] rack %d:%d ONLINE\n", c, r);
                }
                /* Steady state: already online, update tracking */
                else if (!is_stale && online == 1)
                {
                    cfg->prev_online[c][r] = 1;
                }
            }
        }
    }

    fprintf(stderr, "[can_health] thread exiting\n");
    return NULL;
}
