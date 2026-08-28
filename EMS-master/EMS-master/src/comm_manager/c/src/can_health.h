#ifndef CAN_HEALTH_H
#define CAN_HEALTH_H

/**
 * @file can_health.h
 * @brief Heartbeat timeout checker -- per-rack online/offline tracking.
 *
 * A dedicated thread runs at 300ms intervals, checking last_update_ms
 * for each active rack. Racks that go stale beyond timeout_ms are
 * marked offline and comm_fault events are published. Racks that
 * come back online trigger comm_recovery events.
 */

#include "comm_event.h"
#include "rtdb.h"

#include <signal.h>
#include <stdint.h>

/** Health monitor thread configuration. */
typedef struct
{
    ems_rtdb_t              *rtdb;            /**< RTDB shared memory pointer      */
    comm_event_ctx_t        *event_ctx;       /**< ZMQ event context               */
    int                      cluster_count;   /**< Number of active clusters       */
    int                      racks_per_cluster; /**< Racks per cluster             */
    uint32_t                 timeout_ms;      /**< Heartbeat timeout in ms         */
    uint32_t                 check_interval_ms; /**< Check interval in ms (300)    */
    volatile sig_atomic_t   *running;         /**< Shared running flag             */

    /** Previous online state per rack for edge detection. */
    uint8_t prev_online[MAX_CLUSTERS][MAX_RACKS_PER_CLUSTER];
} can_health_config_t;

/**
 * Initialize last_update_ms for all active racks to current CLOCK_MONOTONIC.
 *
 * Prevents false heartbeat timeouts at startup before any CAN frames
 * have been received.
 *
 * @param rtdb             RTDB pointer.
 * @param cluster_count    Number of active clusters.
 * @param racks_per_cluster Number of racks per cluster.
 */
void can_health_init_timestamps(ems_rtdb_t *rtdb, int cluster_count,
                                int racks_per_cluster);

/**
 * Health monitor pthread entry point.
 *
 * Runs at check_interval_ms intervals. For each active rack:
 *   - If stale > timeout_ms and was online: mark offline, publish comm_fault
 *   - If not stale and was offline: publish comm_recovery
 *
 * @param arg  Pointer to can_health_config_t.
 * @return NULL always.
 */
void *can_health_thread(void *arg);

#endif /* CAN_HEALTH_H */
