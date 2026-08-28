/**
 * @file rtdb_lifecycle.c
 * @brief RTDB shared memory lifecycle implementation.
 *
 * Implements create/attach/detach/destroy for the RTDB POSIX shared memory
 * segment. The owner (data_manager_c) calls rtdb_create(); consumers call
 * rtdb_attach(). On shutdown the owner calls rtdb_destroy() to unlink.
 *
 * Stale detection: If shm exists with wrong magic or version, it is unlinked
 * and recreated fresh. This handles crash recovery where data_manager_c
 * terminated without cleanup.
 */

#include "rtdb_lifecycle.h"

#include "rtdb.h"
#include "seqlock.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

/**
 * Check if an existing shm segment is stale (wrong magic or version).
 * If stale, unlink it. Returns 1 if stale was found and removed,
 * 0 if no shm exists or it is valid, -1 on unexpected error.
 */
static int check_and_remove_stale(void)
{
    int fd = shm_open(RTDB_SHM_NAME, O_RDWR, 0);
    if (fd < 0)
    {
        if (errno == ENOENT)
        {
            return 0; /* No existing shm — not stale */
        }
        perror("rtdb: shm_open (stale check)");
        return -1;
    }

    /* Map just enough to read the header */
    void *ptr = mmap(NULL, sizeof(ems_rtdb_t), PROT_READ, MAP_SHARED, fd, 0);
    close(fd);

    if (ptr == MAP_FAILED)
    {
        /* Cannot map — treat as stale, remove it */
        fprintf(stderr, "rtdb: cannot map existing shm for stale check, removing\n");
        shm_unlink(RTDB_SHM_NAME);
        return 1;
    }

    const ems_rtdb_t *existing = (const ems_rtdb_t *)ptr;
    uint32_t found_magic = existing->magic;
    uint32_t found_version = existing->version;
    int is_stale = (found_magic != RTDB_MAGIC || found_version != RTDB_VERSION);

    munmap(ptr, sizeof(ems_rtdb_t));

    if (is_stale)
    {
        fprintf(stderr, "rtdb: stale shm detected (magic=0x%08X version=%u), recreating\n",
                found_magic, found_version);
        shm_unlink(RTDB_SHM_NAME);
        return 1;
    }

    /* Valid shm already exists — remove it anyway since we are the creator
     * and want a fresh segment with potentially new topology. */
    shm_unlink(RTDB_SHM_NAME);
    return 0;
}

/**
 * Initialize all seqlocks in the RTDB to 0 (even = no write in progress).
 */
static void init_seqlocks(ems_rtdb_t *rtdb)
{
    for (int c = 0; c < MAX_CLUSTERS; c++)
    {
        for (int r = 0; r < MAX_RACKS_PER_CLUSTER; r++)
        {
            ems_seqlock_init(&rtdb->clusters[c].racks[r].lock);
        }
    }
    ems_seqlock_init(&rtdb->pcs.lock);
    ems_seqlock_init(&rtdb->gpio.lock);
    ems_seqlock_init(&rtdb->meter.lock);
    ems_seqlock_init(&rtdb->btms.lock);
    ems_seqlock_init(&rtdb->system.lock);
}

ems_rtdb_t *rtdb_create(uint8_t clusters, uint8_t racks_per_cluster,
                         uint8_t modules_per_rack, uint8_t cells_per_module,
                         uint8_t temps_per_module)
{
    /* Check for stale shm from previous crash */
    check_and_remove_stale();

    /* Create new shm segment */
    int fd = shm_open(RTDB_SHM_NAME, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0)
    {
        perror("rtdb: shm_open (create)");
        return NULL;
    }

    /* Size to exactly sizeof(ems_rtdb_t) */
    if (ftruncate(fd, (off_t)sizeof(ems_rtdb_t)) != 0)
    {
        perror("rtdb: ftruncate");
        close(fd);
        shm_unlink(RTDB_SHM_NAME);
        return NULL;
    }

    /* Map shared */
    void *ptr = mmap(NULL, sizeof(ems_rtdb_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd); /* fd not needed once mapped */

    if (ptr == MAP_FAILED)
    {
        perror("rtdb: mmap");
        shm_unlink(RTDB_SHM_NAME);
        return NULL;
    }

    /* Zero-fill entire struct */
    memset(ptr, 0, sizeof(ems_rtdb_t));

    ems_rtdb_t *rtdb = (ems_rtdb_t *)ptr;

    /* Initialize header */
    rtdb->magic = RTDB_MAGIC;
    rtdb->version = RTDB_VERSION;

    /* Topology counts */
    rtdb->cluster_count = clusters;
    rtdb->racks_per_cluster = racks_per_cluster;
    rtdb->modules_per_rack = modules_per_rack;
    rtdb->cells_per_module = cells_per_module;
    rtdb->temps_per_module = temps_per_module;

    /* Initialize all seqlocks */
    init_seqlocks(rtdb);

    return rtdb;
}

ems_rtdb_t *rtdb_attach(void)
{
    int fd = shm_open(RTDB_SHM_NAME, O_RDWR, 0);
    if (fd < 0)
    {
        perror("rtdb: shm_open (attach)");
        return NULL;
    }

    void *ptr = mmap(NULL, sizeof(ems_rtdb_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);

    if (ptr == MAP_FAILED)
    {
        perror("rtdb: mmap (attach)");
        return NULL;
    }

    ems_rtdb_t *rtdb = (ems_rtdb_t *)ptr;

    /* Verify header */
    if (rtdb->magic != RTDB_MAGIC || rtdb->version != RTDB_VERSION)
    {
        fprintf(stderr, "rtdb: attach failed — invalid header (magic=0x%08X version=%u)\n",
                rtdb->magic, rtdb->version);
        munmap(ptr, sizeof(ems_rtdb_t));
        return NULL;
    }

    return rtdb;
}

void rtdb_detach(ems_rtdb_t *rtdb)
{
    if (rtdb != NULL)
    {
        munmap(rtdb, sizeof(ems_rtdb_t));
    }
}

void rtdb_destroy(void)
{
    if (shm_unlink(RTDB_SHM_NAME) != 0 && errno != ENOENT)
    {
        perror("rtdb: shm_unlink");
    }
}
