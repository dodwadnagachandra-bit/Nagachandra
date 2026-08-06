#include "watchdog.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

/**
 * @file watchdog.c
 * @brief /dev/watchdog ioctl management + condvar-based feed thread.
 *
 * The feed thread runs at SCHED_FIFO priority higher than the scan thread.
 * It uses pthread_cond_timedwait with a 1-second timeout. If the scan
 * thread signals completion, the watchdog is kicked immediately. If the
 * timedwait expires (scan thread delayed), the watchdog is still kicked
 * because the process is alive -- scan delay is not process death.
 */

/* Static thread state */
static pthread_mutex_t wdt_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  wdt_cond  = PTHREAD_COND_INITIALIZER;
static volatile bool   scan_complete = false;
static volatile bool   wdt_running   = false;
static pthread_t       wdt_thread;
static int             wdt_fd_global = -1;

int watchdog_open(const char *dev_path, int timeout_sec)
{
    int fd = open(dev_path, O_RDWR);
    if (fd < 0)
    {
        if (errno == EBUSY)
        {
            fprintf(stderr, "[watchdog] WARNING: %s busy (another process owns it)\n",
                    dev_path);
        }
        else
        {
            fprintf(stderr, "[watchdog] WARNING: cannot open %s: %s\n",
                    dev_path, strerror(errno));
        }
        return -1;
    }

    /* Set watchdog timeout -- non-fatal if unsupported by hardware */
    int timeout = timeout_sec;
    if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) < 0)
    {
        fprintf(stderr, "[watchdog] WARNING: WDIOC_SETTIMEOUT failed: %s "
                "(using hardware default)\n", strerror(errno));
    }
    else
    {
        fprintf(stderr, "[watchdog] timeout set to %d seconds\n", timeout);
    }

    return fd;
}

void watchdog_kick(int fd)
{
    if (fd < 0)
    {
        return;
    }
    /* WDIOC_KEEPALIVE is the standard ioctl to pet the watchdog */
    if (ioctl(fd, WDIOC_KEEPALIVE, NULL) < 0)
    {
        fprintf(stderr, "[watchdog] WARNING: WDIOC_KEEPALIVE failed: %s\n",
                strerror(errno));
    }
}

void watchdog_close(int fd)
{
    if (fd < 0)
    {
        return;
    }

    /*
     * Write magic character 'V' to properly disable the watchdog
     * before closing. Without this, many watchdog drivers will
     * reset the system when the fd is closed.
     */
    const char magic = 'V';
    if (write(fd, &magic, 1) < 0)
    {
        fprintf(stderr, "[watchdog] WARNING: magic close write failed: %s\n",
                strerror(errno));
    }

    close(fd);
}

/**
 * Feed thread function -- runs at SCHED_FIFO priority.
 *
 * Waits for scan completion signal via condvar. If timedwait expires
 * (1 second) without signal, still kicks watchdog -- the process is
 * alive, scan thread is just delayed.
 */
static void *watchdog_feed_thread(void *arg)
{
    (void)arg;

    while (wdt_running)
    {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        ts.tv_sec += 1; /* 1-second timedwait timeout */

        pthread_mutex_lock(&wdt_mutex);

        /* Wait for scan completion signal or timeout */
        while (!scan_complete && wdt_running)
        {
            int rc = pthread_cond_timedwait(&wdt_cond, &wdt_mutex, &ts);
            if (rc == ETIMEDOUT)
            {
                /* Scan thread didn't signal in time -- still kick.
                 * Process is alive, scan delay != process death. */
                fprintf(stderr, "[watchdog] WARNING: scan thread did not "
                        "signal in 1s, kicking anyway\n");
                break;
            }
        }

        if (scan_complete)
        {
            scan_complete = false;
        }

        pthread_mutex_unlock(&wdt_mutex);

        if (wdt_running)
        {
            watchdog_kick(wdt_fd_global);
        }
    }

    return NULL;
}

int watchdog_thread_start(int wdt_fd, int sched_priority)
{
    if (wdt_fd < 0)
    {
        fprintf(stderr, "[watchdog] no watchdog fd, feed thread not started\n");
        return -1;
    }

    wdt_fd_global = wdt_fd;
    wdt_running = true;
    scan_complete = false;

    pthread_attr_t attr;
    pthread_attr_init(&attr);

    /* Use explicit scheduling so thread gets its own priority
     * instead of inheriting parent's */
    pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    pthread_attr_setschedpolicy(&attr, SCHED_FIFO);

    struct sched_param param;
    param.sched_priority = sched_priority;
    pthread_attr_setschedparam(&attr, &param);

    int rc = pthread_create(&wdt_thread, &attr, watchdog_feed_thread, NULL);
    if (rc != 0)
    {
        /* SCHED_FIFO requires root/CAP_SYS_NICE. Fall back to default. */
        fprintf(stderr, "[watchdog] WARNING: SCHED_FIFO thread creation failed "
                "(%s), retrying with default scheduling\n", strerror(rc));
        pthread_attr_destroy(&attr);
        pthread_attr_init(&attr);
        rc = pthread_create(&wdt_thread, &attr, watchdog_feed_thread, NULL);
        if (rc != 0)
        {
            fprintf(stderr, "[watchdog] ERROR: thread creation failed: %s\n",
                    strerror(rc));
            pthread_attr_destroy(&attr);
            wdt_running = false;
            return -1;
        }
    }

    pthread_attr_destroy(&attr);
    fprintf(stderr, "[watchdog] feed thread started (priority=%d)\n",
            sched_priority);
    return 0;
}

void watchdog_thread_stop(void)
{
    if (!wdt_running)
    {
        return;
    }

    /* Signal thread to exit */
    pthread_mutex_lock(&wdt_mutex);
    wdt_running = false;
    pthread_cond_signal(&wdt_cond);
    pthread_mutex_unlock(&wdt_mutex);

    pthread_join(wdt_thread, NULL);
    fprintf(stderr, "[watchdog] feed thread stopped\n");
}

void watchdog_signal_scan_complete(void)
{
    pthread_mutex_lock(&wdt_mutex);
    scan_complete = true;
    pthread_cond_signal(&wdt_cond);
    pthread_mutex_unlock(&wdt_mutex);
}
