#ifndef CAN_READER_H
#define CAN_READER_H

/**
 * @file can_reader.h
 * @brief CAN reader thread -- SocketCAN read loop with frame dispatch.
 *
 * Each CAN reader thread opens a SocketCAN socket on a specified interface,
 * reads frames in a blocking loop, and dispatches to can_decode for RTDB
 * writes. Error frames are forwarded to can_handle_error_frame().
 */

#include "comm_event.h"
#include "rtdb.h"

#include <signal.h>
#include <stdint.h>

/** CAN reader thread configuration. */
typedef struct
{
    const char        *interface;         /**< CAN interface name (e.g., "can0")  */
    uint32_t           base_can_id;       /**< Base CAN ID for decode             */
    int                cluster_index;     /**< Cluster index for this interface    */
    int                racks_per_cluster; /**< Number of racks per cluster         */
    ems_rtdb_t        *rtdb;             /**< RTDB shared memory pointer           */
    comm_event_ctx_t  *event_ctx;        /**< ZMQ event context for error events   */
    volatile sig_atomic_t *running;      /**< Shared running flag for shutdown     */
} can_reader_config_t;

/**
 * Create and configure a SocketCAN raw socket.
 *
 * Opens a CAN_RAW socket, binds to the named interface, and enables
 * CAN_RAW_ERR_FILTER with CAN_ERR_MASK for error frame reception.
 *
 * @param interface  CAN interface name (e.g., "can0").
 * @return Socket file descriptor on success, -1 on failure.
 */
int can_socket_init(const char *interface);

/**
 * CAN reader pthread entry point.
 *
 * Blocking read() loop on CAN socket. For each frame:
 *   - Error frame: update RTDB can_health, publish can_bus_error event
 *   - Data frame: decode CAN ID, bounds-check, decode frame to RTDB rack
 *
 * Checks *config->running flag to terminate on SIGTERM.
 *
 * @param arg  Pointer to can_reader_config_t.
 * @return NULL always.
 */
void *can_reader_thread(void *arg);

#endif /* CAN_READER_H */
