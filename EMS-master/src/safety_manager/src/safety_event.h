#ifndef SAFETY_EVENT_H
#define SAFETY_EVENT_H

/**
 * @file safety_event.h
 * @brief ZMQ event publishing interface for safety events.
 *
 * Publishes safety events on the telemetry PUB socket (topic "gpio")
 * and pushes to the logger PUSH socket for persistence. All ZMQ sends
 * use ZMQ_DONTWAIT to never block the safety scan loop.
 *
 * MessagePack envelope format (matching project convention):
 *   {ts: double, seq: uint64, src: "safety_manager", topic: "gpio",
 *    payload: {event_type: str, message: str, severity: str}}
 *
 * Framing: 4-byte BE uint32 length prefix before MessagePack body.
 * PUB: topic frame (SNDMORE) + length prefix (SNDMORE) + body
 * PUSH: length prefix (SNDMORE) + body
 */

#include "ems_types.h"
#include <stdint.h>

/** Safety event publishing context. */
typedef struct
{
    void     *zmq_ctx;     /**< ZMQ context (owned)                */
    void     *pub_sock;    /**< PUB socket connected to telemetry  */
    void     *push_sock;   /**< PUSH socket connected to logger    */
    uint64_t  seq;         /**< Monotonic sequence counter         */
} safety_event_ctx_t;

/**
 * Initialize ZMQ event publishing context.
 *
 * Connects PUB socket to telemetry and PUSH socket to logger.
 * Non-fatal on failure -- safety continues without event publishing.
 *
 * @param ctx        Event context to initialize.
 * @param pub_addr   Telemetry PUB address (e.g., EMS_SOCK_TELEMETRY).
 * @param push_addr  Logger PUSH address (e.g., EMS_SOCK_LOGGER).
 * @return 0 on success, -1 on failure.
 */
int safety_event_init(safety_event_ctx_t *ctx, const char *pub_addr,
                      const char *push_addr);

/**
 * Publish a safety event on PUB and PUSH sockets.
 *
 * Encodes a MessagePack envelope with length-prefixed framing.
 * Uses stack-allocated buffer (no heap allocation in hot path).
 * Silently drops on EAGAIN -- safety outputs are priority.
 *
 * @param ctx         Event context.
 * @param event_type  Event type string (e.g., "estop", "fire", "flood").
 * @param message     Human-readable event description.
 * @param severity    Event severity level.
 * @return 0 on success, -1 if encoding fails.
 */
int safety_event_publish(safety_event_ctx_t *ctx, const char *event_type,
                         const char *message, ems_severity_t severity);

/**
 * Close event publishing context and free ZMQ resources.
 *
 * @param ctx  Event context to close.
 */
void safety_event_close(safety_event_ctx_t *ctx);

/* ---- Convenience functions for common safety events ---- */

/** Publish E-Stop activation event (CRITICAL severity). */
void safety_event_estop(safety_event_ctx_t *ctx);

/** Publish fire detection event (CRITICAL severity). */
void safety_event_fire(safety_event_ctx_t *ctx);

/** Publish flood detection event (CRITICAL severity). */
void safety_event_flood(safety_event_ctx_t *ctx);

/** Publish GPIO failure event (ERROR severity). */
void safety_event_gpio_failure(safety_event_ctx_t *ctx, const char *detail);

/** Publish safety reset event (INFO severity). */
void safety_event_reset(safety_event_ctx_t *ctx, const char *what);

#endif /* SAFETY_EVENT_H */
