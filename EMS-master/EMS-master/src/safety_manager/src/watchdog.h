#ifndef SAFETY_WATCHDOG_H
#define SAFETY_WATCHDOG_H

/**
 * @file watchdog.h
 * @brief Hardware watchdog management with condvar-based feed thread.
 *
 * The watchdog feed thread runs at a higher SCHED_FIFO priority than the
 * scan thread. It waits on a condition variable signaled by the scan thread
 * after each complete cycle. If the scan thread stalls, the feed thread
 * still kicks the watchdog (process is alive, scan delay is not death).
 *
 * On dev/CI systems without /dev/watchdog, watchdog_open() returns -1
 * and the scan loop continues without watchdog protection.
 */

/**
 * Open the hardware watchdog device and set timeout.
 *
 * @param dev_path  Path to watchdog device (e.g., "/dev/watchdog").
 * @param timeout_sec  Watchdog timeout in seconds (default recommendation: 2).
 * @return File descriptor on success, -1 on failure.
 *         EBUSY means another process owns the watchdog (logged, non-fatal).
 */
int watchdog_open(const char *dev_path, int timeout_sec);

/**
 * Kick (pet) the hardware watchdog.
 *
 * @param fd  Watchdog file descriptor from watchdog_open().
 */
void watchdog_kick(int fd);

/**
 * Close the watchdog device with proper magic-close disable.
 *
 * Writes the magic character 'V' before closing to prevent
 * an immediate system reset on close.
 *
 * @param fd  Watchdog file descriptor from watchdog_open().
 */
void watchdog_close(int fd);

/**
 * Start the watchdog feed thread at the specified SCHED_FIFO priority.
 *
 * The thread waits on a condvar for scan completion signals. On timeout
 * (scan thread stuck), it still kicks the watchdog -- the process is alive.
 *
 * @param wdt_fd  Watchdog file descriptor from watchdog_open().
 * @param sched_priority  SCHED_FIFO priority (e.g., 81 -- higher than scan thread).
 * @return 0 on success, -1 on failure.
 */
int watchdog_thread_start(int wdt_fd, int sched_priority);

/**
 * Stop the watchdog feed thread and join.
 */
void watchdog_thread_stop(void);

/**
 * Signal that the scan thread completed a full cycle.
 *
 * Called by the scan thread after each complete GPIO scan + response
 * matrix evaluation. Sets a flag and signals the condvar so the feed
 * thread can kick the watchdog.
 */
void watchdog_signal_scan_complete(void);

#endif /* SAFETY_WATCHDOG_H */
