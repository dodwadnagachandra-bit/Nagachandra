#ifndef COMM_EVENT_H
#define COMM_EVENT_H

/**
 * @file comm_event.h
 * @brief ZMQ event publishing interface for comm_manager_c events.
 *
 * Publishes communication events (comm_fault, comm_recovery, can_bus_error)
 * on the telemetry PUB socket and pushes to the logger PUSH socket for
 * persistence. Follows the same pattern as safety_event.h.
 *
 * MessagePack envelope format (matching project convention):
 *   {ts: double, seq: uint64, src: "comm_manager_c", topic: "comm_fault",
 *    payload: {event_type: str, message: str, severity: str}}
 *
 * Extended payload (publish_with_data):
 *   payload: {event_type, message, severity, device_id, device_address}
 *
 * Framing: 4-byte BE uint32 length prefix before MessagePack body.
 * PUB: topic frame (SNDMORE) + length prefix (SNDMORE) + body
 * PUSH: length prefix (SNDMORE) + body
 */

#include "ems_types.h"
#include <stdint.h>

/** Comm event publishing context. */
typedef struct
{
    void     *zmq_ctx;     /**< ZMQ context (owned)                */
    void     *pub_sock;    /**< PUB socket connected to telemetry  */
    void     *push_sock;   /**< PUSH socket connected to logger    */
    uint64_t  seq;         /**< Monotonic sequence counter         */
} comm_event_ctx_t;

/**
 * Initialize ZMQ event publishing context.
 *
 * Connects PUB socket to telemetry and PUSH socket to logger.
 *
 * @param ctx        Event context to initialize.
 * @param pub_addr   Telemetry PUB address (e.g., EMS_SOCK_TELEMETRY).
 * @param push_addr  Logger PUSH address (e.g., EMS_SOCK_LOGGER).
 * @return 0 on success, -1 on failure.
 */
int comm_event_init(comm_event_ctx_t *ctx, const char *pub_addr,
                    const char *push_addr);

/**
 * Publish a comm event on PUB and PUSH sockets.
 *
 * Encodes a MessagePack envelope with length-prefixed framing.
 * Uses stack-allocated buffer (no heap allocation in hot path).
 * Silently drops on EAGAIN.
 *
 * @param ctx         Event context.
 * @param event_type  Event type string (e.g., "heartbeat_timeout", "comm_recovery").
 * @param message     Human-readable event description.
 * @param severity    Event severity level.
 * @return 0 on success, -1 if encoding fails.
 */
int comm_event_publish(comm_event_ctx_t *ctx, const char *event_type,
                       const char *message, ems_severity_t severity);

/**
 * Publish a comm event with extended device data.
 *
 * Like comm_event_publish but adds device_id and device_address
 * to the payload map.
 *
 * @param ctx            Event context.
 * @param event_type     Event type string.
 * @param message        Human-readable event description.
 * @param severity       Event severity level.
 * @param device_id      Device identifier string (e.g., "rack_0_1").
 * @param device_address Device address or index.
 * @return 0 on success, -1 if encoding fails.
 */
int comm_event_publish_with_data(comm_event_ctx_t *ctx, const char *event_type,
                                 const char *message, ems_severity_t severity,
                                 const char *device_id, int device_address);

/**
 * Close event publishing context and free ZMQ resources.
 *
 * @param ctx  Event context to close.
 */
void comm_event_close(comm_event_ctx_t *ctx);

#endif /* COMM_EVENT_H */
