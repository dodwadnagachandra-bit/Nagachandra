#include "safety_reset.h"

#include "ems_types.h"
#include "ipc_defs.h"
#include "mpack.h"

#include <arpa/inet.h>   /* htonl */
#include <stdio.h>
#include <string.h>
#include <zmq.h>

/**
 * @file safety_reset.c
 * @brief Non-blocking ZMQ poll for reset commands.
 *
 * Receives MessagePack-encoded reset commands on a REP socket.
 * Validates that all triggering DI inputs have cleared before
 * accepting a reset. Sends MessagePack reply with status.
 *
 * DI layout (from gpio_config.yaml):
 *   DI-0: ACDB Feedback   DI-1: Flood Sensor
 *   DI-2: Door Open        DI-3: Smoke Detector
 *   DI-4: Heat Detector    DI-5: Spare
 *   DI-6: E-Stop NO        DI-7: E-Stop NC
 *
 * Latch flags (bit positions):
 *   bit 0: E-Stop latched
 *   bit 1: Fire latched
 *   bit 2: Flood latched
 */

/** Receive buffer size for incoming reset commands. */
#define RESET_RECV_BUF_SIZE 256

/** Reply buffer size for outgoing responses. */
#define RESET_REPLY_BUF_SIZE 256

/** ZMQ receive high-water mark. */
#define RESET_HWM 10

/** Latch flag bit positions. */
#define LATCH_ESTOP  (1u << 0)
#define LATCH_FIRE   (1u << 1)
#define LATCH_FLOOD  (1u << 2)

int safety_reset_init(safety_reset_ctx_t *ctx, const char *bind_addr)
{
    if (!ctx || !bind_addr)
    {
        return -1;
    }

    memset(ctx, 0, sizeof(*ctx));

    ctx->zmq_ctx = zmq_ctx_new();
    if (!ctx->zmq_ctx)
    {
        fprintf(stderr, "[safety_reset] ERROR: zmq_ctx_new failed\n");
        return -1;
    }

    ctx->rep_sock = zmq_socket(ctx->zmq_ctx, ZMQ_REP);
    if (!ctx->rep_sock)
    {
        fprintf(stderr, "[safety_reset] ERROR: zmq_socket(REP) failed\n");
        zmq_ctx_destroy(ctx->zmq_ctx);
        ctx->zmq_ctx = NULL;
        return -1;
    }

    int hwm = RESET_HWM;
    int linger = 0;  /* Don't block on zmq_close -- safety must not hang */
    zmq_setsockopt(ctx->rep_sock, ZMQ_RCVHWM, &hwm, sizeof(hwm));
    zmq_setsockopt(ctx->rep_sock, ZMQ_LINGER, &linger, sizeof(linger));

    if (zmq_bind(ctx->rep_sock, bind_addr) != 0)
    {
        fprintf(stderr, "[safety_reset] ERROR: zmq_bind(%s) failed: %s\n",
                bind_addr, zmq_strerror(zmq_errno()));
        zmq_close(ctx->rep_sock);
        ctx->rep_sock = NULL;
        zmq_ctx_destroy(ctx->zmq_ctx);
        ctx->zmq_ctx = NULL;
        return -1;
    }

    fprintf(stderr, "[safety_reset] initialized (bind=%s)\n", bind_addr);
    return 0;
}

/**
 * Check if safety inputs are still active (preventing reset).
 *
 * E-Stop reset: DI-6 (NO, active_low) must be 0 AND DI-7 (NC) must be 1
 *               (both in normal state).
 * Fire reset:   DI-3 (Smoke) must be 0 AND DI-4 (Heat) must be 0.
 * Flood reset:  DI-1 (Flood) must be 0.
 *
 * @param di_raw   Current digital input values.
 * @param latched  Current latch state flags.
 * @param reason   Output buffer for reason string (at least 128 bytes).
 * @return true if all inputs are clear and reset is allowed.
 */
static int check_inputs_clear(const uint8_t *di_raw, uint8_t latched,
                              char *reason, size_t reason_size)
{
    reason[0] = '\0';

    if (latched & LATCH_ESTOP)
    {
        /*
         * E-Stop: DI-6 is NO (active_low: true in config, so 0=pressed),
         * DI-7 is NC (active_low: false, so 1=pressed).
         * For "normal" (released): DI-6 should be 1, DI-7 should be 0.
         * But from the raw values perspective, we check that the E-Stop
         * condition is no longer active. E-Stop activates when
         * DI-6 reads active AND DI-7 reads active. The exact polarity
         * handling is done in the response matrix. Here we do a
         * simplified check: if either DI-6 or DI-7 is in active state,
         * reject.
         *
         * Per CONTEXT.md: DI-6 (NO) + DI-7 (NC) must both confirm.
         * Active = DI-6 high (pressed) + DI-7 low (released from NC).
         * Normal = DI-6 low + DI-7 high.
         */
        if (di_raw[6] != 0 || di_raw[7] != 1)
        {
            snprintf(reason, reason_size, "E-Stop inputs still active "
                     "(DI-6=%u, DI-7=%u)", di_raw[6], di_raw[7]);
            return 0;
        }
    }

    if (latched & LATCH_FIRE)
    {
        /* Fire: both DI-3 (Smoke) AND DI-4 (Heat) must be inactive */
        if (di_raw[3] != 0 || di_raw[4] != 0)
        {
            snprintf(reason, reason_size, "Fire inputs still active "
                     "(DI-3=%u, DI-4=%u)", di_raw[3], di_raw[4]);
            return 0;
        }
    }

    if (latched & LATCH_FLOOD)
    {
        /* Flood: DI-1 must be inactive */
        if (di_raw[1] != 0)
        {
            snprintf(reason, reason_size, "Flood input still active "
                     "(DI-1=%u)", di_raw[1]);
            return 0;
        }
    }

    return 1;
}

/**
 * Encode a reply message in MessagePack format.
 *
 * Success: {status: "ok"}
 * Error:   {status: "error", error_msg: "..."}
 *
 * @return Number of bytes written, or 0 on error.
 */
static size_t encode_reply(char *buf, size_t buf_size,
                           const char *status, const char *error_msg)
{
    mpack_writer_t writer;
    mpack_writer_init(&writer, buf, buf_size);

    int nkeys = (error_msg != NULL) ? 2 : 1;
    mpack_start_map(&writer, (uint32_t)nkeys);

    mpack_write_cstr(&writer, EMS_MSG_KEY_STATUS);
    mpack_write_cstr(&writer, status);

    if (error_msg)
    {
        mpack_write_cstr(&writer, EMS_MSG_KEY_ERROR_MSG);
        mpack_write_cstr(&writer, error_msg);
    }

    mpack_finish_map(&writer);

    size_t used = mpack_writer_buffer_used(&writer);

    if (mpack_writer_destroy(&writer) != mpack_ok)
    {
        return 0;
    }

    return used;
}

/**
 * Send a framed reply on the REP socket.
 *
 * Uses length-prefixed framing: 4-byte BE uint32 + msgpack body.
 */
static void send_reply(void *rep_sock, const char *buf, size_t len)
{
    uint32_t len_be = htonl((uint32_t)len);

    zmq_send(rep_sock, &len_be, sizeof(len_be), ZMQ_SNDMORE);
    zmq_send(rep_sock, buf, len, 0);
}

int safety_reset_poll(safety_reset_ctx_t *ctx, const uint8_t *di_raw,
                      uint8_t latched, int *out_reset)
{
    if (!ctx || !ctx->rep_sock || !di_raw || !out_reset)
    {
        return -1;
    }

    *out_reset = 0;

    /* Non-blocking poll with 0 timeout = immediate return */
    zmq_pollitem_t item = { .socket = ctx->rep_sock, .events = ZMQ_POLLIN };
    int rc = zmq_poll(&item, 1, 0);

    if (rc < 0)
    {
        fprintf(stderr, "[safety_reset] ERROR: zmq_poll failed: %s\n",
                zmq_strerror(zmq_errno()));
        return -1;
    }

    if (rc == 0 || !(item.revents & ZMQ_POLLIN))
    {
        return 0; /* No message available */
    }

    /* Receive the message -- may be length-prefixed (skip prefix frames) */
    char recv_buf[RESET_RECV_BUF_SIZE];
    int nbytes;
    char msgpack_buf[RESET_RECV_BUF_SIZE];
    int msgpack_len = 0;

    /* Read all frames, keep the last one as the msgpack body */
    int more = 1;
    while (more)
    {
        nbytes = zmq_recv(ctx->rep_sock, recv_buf, sizeof(recv_buf) - 1, 0);
        if (nbytes < 0)
        {
            fprintf(stderr, "[safety_reset] ERROR: zmq_recv failed: %s\n",
                    zmq_strerror(zmq_errno()));
            return -1;
        }

        /* Copy to msgpack buffer (overwrite with each frame, last one wins) */
        if (nbytes > 0 && (size_t)nbytes <= sizeof(msgpack_buf))
        {
            memcpy(msgpack_buf, recv_buf, (size_t)nbytes);
            msgpack_len = nbytes;
        }

        size_t more_size = sizeof(more);
        zmq_getsockopt(ctx->rep_sock, ZMQ_RCVMORE, &more, &more_size);
    }

    if (msgpack_len <= 0)
    {
        /* Empty message -- send error reply */
        char reply[RESET_REPLY_BUF_SIZE];
        size_t rlen = encode_reply(reply, sizeof(reply), EMS_STATUS_ERROR,
                                   "empty request");
        if (rlen > 0)
        {
            send_reply(ctx->rep_sock, reply, rlen);
        }
        return -1;
    }

    /* Decode MessagePack request: expect {action: "safety_reset", ...} */
    mpack_reader_t reader;
    mpack_reader_init_data(&reader, msgpack_buf, (size_t)msgpack_len);

    uint32_t map_count = mpack_expect_map(&reader);
    char action[64] = {0};

    for (uint32_t i = 0; i < map_count; i++)
    {
        char key[64];
        mpack_expect_cstr(&reader, key, sizeof(key));

        if (strcmp(key, EMS_MSG_KEY_ACTION) == 0)
        {
            mpack_expect_cstr(&reader, action, sizeof(action));
        }
        else
        {
            /* Skip unknown keys */
            mpack_discard(&reader);
        }
    }

    mpack_done_map(&reader);

    if (mpack_reader_destroy(&reader) != mpack_ok)
    {
        char reply[RESET_REPLY_BUF_SIZE];
        size_t rlen = encode_reply(reply, sizeof(reply), EMS_STATUS_ERROR,
                                   "invalid MessagePack");
        if (rlen > 0)
        {
            send_reply(ctx->rep_sock, reply, rlen);
        }
        return -1;
    }

    /* Validate action */
    if (strcmp(action, "safety_reset") != 0)
    {
        char reply[RESET_REPLY_BUF_SIZE];
        char err[128];
        snprintf(err, sizeof(err), "unknown action: %s", action);
        size_t rlen = encode_reply(reply, sizeof(reply), EMS_STATUS_ERROR, err);
        if (rlen > 0)
        {
            send_reply(ctx->rep_sock, reply, rlen);
        }
        return -1;
    }

    /* Validate that triggering inputs have cleared */
    char reason[128];
    if (!check_inputs_clear(di_raw, latched, reason, sizeof(reason)))
    {
        fprintf(stderr, "[safety_reset] reset rejected: %s\n", reason);
        char reply[RESET_REPLY_BUF_SIZE];
        char err[192];
        snprintf(err, sizeof(err), "inputs still active: %s", reason);
        size_t rlen = encode_reply(reply, sizeof(reply), EMS_STATUS_ERROR, err);
        if (rlen > 0)
        {
            send_reply(ctx->rep_sock, reply, rlen);
        }
        return 1; /* Message was processed, but reset rejected */
    }

    /* Reset accepted */
    fprintf(stderr, "[safety_reset] reset accepted\n");
    *out_reset = 1;

    char reply[RESET_REPLY_BUF_SIZE];
    size_t rlen = encode_reply(reply, sizeof(reply), EMS_STATUS_OK, NULL);
    if (rlen > 0)
    {
        send_reply(ctx->rep_sock, reply, rlen);
    }

    return 1;
}

void safety_reset_close(safety_reset_ctx_t *ctx)
{
    if (!ctx)
    {
        return;
    }

    if (ctx->rep_sock)
    {
        zmq_close(ctx->rep_sock);
        ctx->rep_sock = NULL;
    }
    if (ctx->zmq_ctx)
    {
        zmq_ctx_destroy(ctx->zmq_ctx);
        ctx->zmq_ctx = NULL;
    }

    fprintf(stderr, "[safety_reset] closed\n");
}
