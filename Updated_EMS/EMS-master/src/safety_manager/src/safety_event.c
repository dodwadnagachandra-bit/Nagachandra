#include "safety_event.h"

#include "ems_types.h"
#include "ipc_defs.h"
#include "mpack.h"

#include <stdio.h>
#include <string.h>
#include <time.h>
#include <zmq.h>

/**
 * @file safety_event.c
 * @brief mpack MessagePack encoding + non-blocking ZMQ send.
 *
 * All encoding uses a stack-allocated 512-byte buffer. No heap
 * allocation in the hot path. ZMQ sends use ZMQ_DONTWAIT and
 * silently drop on EAGAIN -- safety outputs take priority over
 * telemetry delivery.
 *
 * Framing contract (matching Python convention):
 *   PUB:  [topic frame | SNDMORE] [msgpack body]
 *   PUSH: [msgpack body]  (single frame)
 */

/** Stack buffer size for MessagePack encoding -- 512 bytes per research. */
#define EVENT_BUF_SIZE 512

/** ZMQ send high-water mark for safety event sockets. */
#define EVENT_HWM 100

/** Severity enum to string mapping. */
static const char *severity_str(ems_severity_t sev)
{
    switch (sev)
    {
        case EMS_SEVERITY_INFO:     return EMS_SEVERITY_STR_INFO;
        case EMS_SEVERITY_WARNING:  return EMS_SEVERITY_STR_WARNING;
        case EMS_SEVERITY_ERROR:    return EMS_SEVERITY_STR_ERROR;
        case EMS_SEVERITY_CRITICAL: return EMS_SEVERITY_STR_CRITICAL;
        default:                    return EMS_SEVERITY_STR_INFO;
    }
}

int safety_event_init(safety_event_ctx_t *ctx, const char *pub_addr,
                      const char *push_addr)
{
    if (!ctx || !pub_addr || !push_addr)
    {
        return -1;
    }

    memset(ctx, 0, sizeof(*ctx));

    ctx->zmq_ctx = zmq_ctx_new();
    if (!ctx->zmq_ctx)
    {
        fprintf(stderr, "[safety_event] ERROR: zmq_ctx_new failed\n");
        return -1;
    }

    /* PUB socket -- connect to telemetry (data_manager owns the bind) */
    ctx->pub_sock = zmq_socket(ctx->zmq_ctx, ZMQ_PUB);
    if (!ctx->pub_sock)
    {
        fprintf(stderr, "[safety_event] ERROR: zmq_socket(PUB) failed\n");
        goto fail_ctx;
    }

    int hwm = EVENT_HWM;
    int linger = 0;  /* Don't block on zmq_close -- safety must not hang */
    zmq_setsockopt(ctx->pub_sock, ZMQ_SNDHWM, &hwm, sizeof(hwm));
    zmq_setsockopt(ctx->pub_sock, ZMQ_LINGER, &linger, sizeof(linger));

    if (zmq_connect(ctx->pub_sock, pub_addr) != 0)
    {
        fprintf(stderr, "[safety_event] ERROR: zmq_connect(PUB, %s) failed: %s\n",
                pub_addr, zmq_strerror(zmq_errno()));
        goto fail_pub;
    }

    /* PUSH socket -- connect to logger */
    ctx->push_sock = zmq_socket(ctx->zmq_ctx, ZMQ_PUSH);
    if (!ctx->push_sock)
    {
        fprintf(stderr, "[safety_event] ERROR: zmq_socket(PUSH) failed\n");
        goto fail_pub;
    }

    zmq_setsockopt(ctx->push_sock, ZMQ_SNDHWM, &hwm, sizeof(hwm));
    zmq_setsockopt(ctx->push_sock, ZMQ_LINGER, &linger, sizeof(linger));

    if (zmq_connect(ctx->push_sock, push_addr) != 0)
    {
        fprintf(stderr, "[safety_event] ERROR: zmq_connect(PUSH, %s) failed: %s\n",
                push_addr, zmq_strerror(zmq_errno()));
        goto fail_push;
    }

    ctx->seq = 0;
    fprintf(stderr, "[safety_event] initialized (PUB=%s, PUSH=%s)\n",
            pub_addr, push_addr);
    return 0;

fail_push:
    zmq_close(ctx->push_sock);
    ctx->push_sock = NULL;
fail_pub:
    zmq_close(ctx->pub_sock);
    ctx->pub_sock = NULL;
fail_ctx:
    zmq_ctx_destroy(ctx->zmq_ctx);
    ctx->zmq_ctx = NULL;
    return -1;
}

/**
 * Encode a safety event into MessagePack envelope.
 *
 * Map with 5 keys: ts, seq, src, topic, payload
 * Payload is inner map: event_type, message, severity
 *
 * @return Number of bytes written, or 0 on error.
 */
static size_t encode_event(char *buf, size_t buf_size,
                           uint64_t seq, const char *event_type,
                           const char *message, ems_severity_t severity)
{
    mpack_writer_t writer;
    mpack_writer_init(&writer, buf, buf_size);

    /* Outer envelope: 5-key map */
    mpack_start_map(&writer, 5);

    /* ts -- CLOCK_REALTIME as double (seconds.nanoseconds) */
    mpack_write_cstr(&writer, EMS_MSG_KEY_TIMESTAMP);
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    double ts = (double)now.tv_sec + (double)now.tv_nsec / 1e9;
    mpack_write_double(&writer, ts);

    /* seq -- monotonic sequence number */
    mpack_write_cstr(&writer, EMS_MSG_KEY_SEQUENCE);
    mpack_write_u64(&writer, seq);

    /* src -- source module */
    mpack_write_cstr(&writer, EMS_MSG_KEY_SOURCE);
    mpack_write_cstr(&writer, "safety_manager");

    /* topic */
    mpack_write_cstr(&writer, EMS_MSG_KEY_TOPIC);
    mpack_write_cstr(&writer, EMS_TOPIC_GPIO);

    /* payload -- inner map with event details */
    mpack_write_cstr(&writer, EMS_MSG_KEY_PAYLOAD);
    mpack_start_map(&writer, 3);

    mpack_write_cstr(&writer, EMS_MSG_KEY_EVENT_TYPE);
    mpack_write_cstr(&writer, event_type);

    mpack_write_cstr(&writer, EMS_MSG_KEY_MESSAGE);
    mpack_write_cstr(&writer, message);

    mpack_write_cstr(&writer, EMS_MSG_KEY_SEVERITY);
    mpack_write_cstr(&writer, severity_str(severity));

    mpack_finish_map(&writer);  /* payload */
    mpack_finish_map(&writer);  /* envelope */

    /* Get buffer usage before destroy invalidates the writer */
    size_t used = mpack_writer_buffer_used(&writer);

    if (mpack_writer_destroy(&writer) != mpack_ok)
    {
        fprintf(stderr, "[safety_event] ERROR: mpack encoding failed\n");
        return 0;
    }

    return used;
}

int safety_event_publish(safety_event_ctx_t *ctx, const char *event_type,
                         const char *message, ems_severity_t severity)
{
    if (!ctx || !ctx->zmq_ctx)
    {
        return -1;
    }

    /* Stack-allocated buffer -- no heap allocation in hot path */
    char buf[EVENT_BUF_SIZE];

    uint64_t seq = ctx->seq++;

    size_t msg_len = encode_event(buf, sizeof(buf), seq, event_type,
                                  message, severity);
    if (msg_len == 0)
    {
        return -1;
    }

    /*
     * Send on PUB socket: 2 frames [topic | SNDMORE] [msgpack body]
     * Matches Python convention -- no length prefix frame.
     */
    if (ctx->pub_sock)
    {
        const char *topic = EMS_TOPIC_GPIO;

        /* Topic frame (SNDMORE) */
        if (zmq_send(ctx->pub_sock, topic, strlen(topic),
                     ZMQ_DONTWAIT | ZMQ_SNDMORE) >= 0)
        {
            /* Body frame (no SNDMORE) */
            zmq_send(ctx->pub_sock, buf, msg_len, ZMQ_DONTWAIT);
        }
        /* EAGAIN silently dropped -- safety outputs are priority */
    }

    /*
     * Send on PUSH socket: single frame [msgpack body]
     * Matches Python convention -- no length prefix frame.
     */
    if (ctx->push_sock)
    {
        zmq_send(ctx->push_sock, buf, msg_len, ZMQ_DONTWAIT);
        /* EAGAIN silently dropped */
    }

    return 0;
}

void safety_event_close(safety_event_ctx_t *ctx)
{
    if (!ctx)
    {
        return;
    }

    if (ctx->pub_sock)
    {
        zmq_close(ctx->pub_sock);
        ctx->pub_sock = NULL;
    }
    if (ctx->push_sock)
    {
        zmq_close(ctx->push_sock);
        ctx->push_sock = NULL;
    }
    if (ctx->zmq_ctx)
    {
        zmq_ctx_destroy(ctx->zmq_ctx);
        ctx->zmq_ctx = NULL;
    }

    fprintf(stderr, "[safety_event] closed\n");
}

/* ---- Convenience functions ---- */

void safety_event_estop(safety_event_ctx_t *ctx)
{
    safety_event_publish(ctx, "estop",
                         "Emergency stop activated (dual-channel confirmed)",
                         EMS_SEVERITY_CRITICAL);
}

void safety_event_fire(safety_event_ctx_t *ctx)
{
    safety_event_publish(ctx, "fire",
                         "Fire detected (smoke + heat dual-confirm)",
                         EMS_SEVERITY_CRITICAL);
}

void safety_event_flood(safety_event_ctx_t *ctx)
{
    safety_event_publish(ctx, "flood",
                         "Flood sensor activated",
                         EMS_SEVERITY_CRITICAL);
}

void safety_event_gpio_failure(safety_event_ctx_t *ctx, const char *detail)
{
    char msg[256];
    snprintf(msg, sizeof(msg), "GPIO failure: %s", detail ? detail : "unknown");
    safety_event_publish(ctx, "gpio_failure", msg, EMS_SEVERITY_ERROR);
}

void safety_event_reset(safety_event_ctx_t *ctx, const char *what)
{
    char msg[256];
    snprintf(msg, sizeof(msg), "Safety reset: %s", what ? what : "all");
    safety_event_publish(ctx, "safety_reset", msg, EMS_SEVERITY_INFO);
}
