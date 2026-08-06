#include "can_reader.h"

#include "can_decode.h"
#include "comm_event.h"
#include "ems_types.h"
#include "rtdb.h"
#include "seqlock.h"

#include <errno.h>
#include <linux/can.h>
#include <linux/can/error.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

/**
 * @file can_reader.c
 * @brief SocketCAN read loop with CAN frame dispatch to can_decode.
 *
 * Each reader thread opens one SocketCAN interface, reads frames in a
 * blocking loop, and writes decoded telemetry to RTDB. Error frames
 * update the per-interface CAN health section and publish events.
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

int can_socket_init(const char *interface)
{
    if (!interface)
    {
        fprintf(stderr, "[can_reader] ERROR: NULL interface name\n");
        return -1;
    }

    /* Create CAN_RAW socket */
    int fd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (fd < 0)
    {
        fprintf(stderr, "[can_reader] ERROR: socket(CAN_RAW) failed: %s\n",
                strerror(errno));
        return -1;
    }

    /* Resolve interface name to index */
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, interface, IFNAMSIZ - 1);

    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0)
    {
        fprintf(stderr, "[can_reader] ERROR: ioctl(SIOCGIFINDEX, %s) failed: %s\n",
                interface, strerror(errno));
        close(fd);
        return -1;
    }

    /* Bind socket to CAN interface */
    struct sockaddr_can addr;
    memset(&addr, 0, sizeof(addr));
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0)
    {
        fprintf(stderr, "[can_reader] ERROR: bind(%s) failed: %s\n",
                interface, strerror(errno));
        close(fd);
        return -1;
    }

    /* Enable error frame reception via CAN_RAW_ERR_FILTER */
    can_err_mask_t err_mask = CAN_ERR_MASK;
    if (setsockopt(fd, SOL_CAN_RAW, CAN_RAW_ERR_FILTER,
                   &err_mask, sizeof(err_mask)) < 0)
    {
        fprintf(stderr, "[can_reader] WARNING: setsockopt(ERR_FILTER) failed: %s\n",
                strerror(errno));
        /* Non-fatal -- continue without error frame reception */
    }

    fprintf(stderr, "[can_reader] socket opened on %s (fd=%d)\n",
            interface, fd);
    return fd;
}

void *can_reader_thread(void *arg)
{
    can_reader_config_t *cfg = (can_reader_config_t *)arg;
    if (!cfg || !cfg->rtdb)
    {
        fprintf(stderr, "[can_reader] ERROR: invalid config\n");
        return NULL;
    }

    int fd = can_socket_init(cfg->interface);
    if (fd < 0)
    {
        fprintf(stderr, "[can_reader] ERROR: cannot open %s -- thread exiting\n",
                cfg->interface);
        return NULL;
    }

    fprintf(stderr, "[can_reader] thread started on %s (cluster=%d, base_id=0x%08X)\n",
            cfg->interface, cfg->cluster_index, cfg->base_can_id);

    /* Track previous bus state for change detection */
    uint32_t prev_bus_state = CAN_BUS_ACTIVE;

    struct can_frame frame;

    while (*(cfg->running))
    {
        ssize_t nbytes = read(fd, &frame, sizeof(frame));

        if (nbytes < 0)
        {
            if (errno == EINTR)
            {
                continue;  /* Interrupted by signal -- check running flag */
            }
            fprintf(stderr, "[can_reader] ERROR: read() on %s failed: %s\n",
                    cfg->interface, strerror(errno));
            break;
        }

        if ((size_t)nbytes < sizeof(struct can_frame))
        {
            continue;  /* Incomplete frame -- skip */
        }

        /* --- Error frame handling --- */
        if (frame.can_id & CAN_ERR_FLAG)
        {
            ems_can_health_t *health =
                &cfg->rtdb->can_health[cfg->cluster_index];

            /* Decode error frame into health struct via seqlock */
            ems_seqlock_write_begin(&health->lock);
            can_handle_error_frame(frame.can_id, frame.data, health);
            health->last_error_frame_ms = clock_mono_ms();
            ems_seqlock_write_end(&health->lock);

            /* Publish event if bus state changed */
            uint32_t new_state = health->bus_state;
            if (new_state != prev_bus_state && cfg->event_ctx)
            {
                char msg[128];
                snprintf(msg, sizeof(msg),
                         "CAN %s bus state changed: %u -> %u "
                         "(TEC=%u REC=%u)",
                         cfg->interface, prev_bus_state, new_state,
                         health->tx_error_count,
                         health->rx_error_count);

                ems_severity_t sev = (new_state >= CAN_BUS_ERROR_PASSIVE)
                                         ? EMS_SEVERITY_ERROR
                                         : EMS_SEVERITY_WARNING;
                comm_event_publish(cfg->event_ctx, "can_bus_error",
                                   msg, sev);
                prev_bus_state = new_state;
            }
            continue;
        }

        /* --- Data frame handling --- */
        uint32_t masked_id = frame.can_id & CAN_EFF_MASK;

        int cluster = 0;
        int rack = 0;
        int msg_offset = 0;
        can_decode_id(frame.can_id, cfg->base_can_id,
                      &cluster, &rack, &msg_offset);

        /* Bounds-check cluster and rack against topology */
        if (cluster != cfg->cluster_index)
        {
            /* Frame from unexpected cluster -- skip */
            (void)masked_id;
            continue;
        }
        if (rack < 0 || rack >= cfg->racks_per_cluster)
        {
            continue;  /* Out-of-range rack index */
        }
        if (msg_offset < 0 || msg_offset >= CAN_MSG_COUNT)
        {
            continue;  /* Unknown message offset */
        }

        ems_rack_t *r = &cfg->rtdb->clusters[cluster].racks[rack];

        /* Decode frame and write to RTDB via seqlock */
        ems_seqlock_write_begin(&r->lock);
        can_decode_frame(msg_offset, frame.data, r);
        r->last_update_ms = clock_mono_ms();
        r->online = 1;
        ems_seqlock_write_end(&r->lock);
    }

    close(fd);
    fprintf(stderr, "[can_reader] thread exiting (%s)\n", cfg->interface);
    return NULL;
}
