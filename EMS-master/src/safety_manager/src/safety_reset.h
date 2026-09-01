#ifndef SAFETY_RESET_H
#define SAFETY_RESET_H

/**
 * @file safety_reset.h
 * @brief ZMQ REQ/REP reset command handler for safety manager.
 *
 * Accepts safety reset commands via ZMQ REP socket. Commands are
 * decoded from MessagePack format: {action: "safety_reset", source: "hmi"|"operator"}.
 *
 * Before accepting a reset, validates that all triggering inputs have
 * cleared (E-Stop DI-6+DI-7 normal, Fire DI-3+DI-4 inactive, Flood DI-1
 * inactive). If inputs are still active, rejects with error response.
 *
 * All ZMQ operations use ZMQ_DONTWAIT where appropriate.
 */

#include <stdint.h>

/** Safety reset command handler context. */
typedef struct
{
    void *zmq_ctx;    /**< ZMQ context (owned)                     */
    void *rep_sock;   /**< REP socket bound for reset commands     */
} safety_reset_ctx_t;

/**
 * Initialize the safety reset command handler.
 *
 * Creates ZMQ context and binds REP socket on the given address.
 *
 * @param ctx        Reset context to initialize.
 * @param bind_addr  Address to bind REP socket (e.g., EMS_SOCK_SAFETY_CMD).
 * @return 0 on success, -1 on failure.
 */
int safety_reset_init(safety_reset_ctx_t *ctx, const char *bind_addr);

/**
 * Poll for and process reset commands (non-blocking).
 *
 * Uses zmq_poll with 0 timeout for immediate return. If a message is
 * available, decodes the MessagePack request, validates the safety
 * state, and sends a reply.
 *
 * @param ctx       Reset context.
 * @param di_raw    Current digital input values (8 bytes, DI-0..DI-7).
 * @param latched   Current latch state flags (bit 0=estop, 1=fire, 2=flood).
 * @param out_reset Set to 1 if a successful reset was processed, 0 otherwise.
 * @return  1 if reset was processed successfully,
 *          0 if no message available,
 *         -1 on error.
 */
int safety_reset_poll(safety_reset_ctx_t *ctx, const uint8_t *di_raw,
                      uint8_t latched, int *out_reset);

/**
 * Close the reset command handler and free ZMQ resources.
 *
 * @param ctx  Reset context to close.
 */
void safety_reset_close(safety_reset_ctx_t *ctx);

#endif /* SAFETY_RESET_H */
