/**
 * @file test_rtdb_lifecycle.c
 * @brief Tests for RTDB shared memory lifecycle (create/attach/detach/destroy).
 *
 * Covers: DATA-01 through DATA-04
 * - shm_open + mmap with correct size
 * - Header initialization (magic, version, topology)
 * - Seqlock initialization (all zero/even)
 * - Stale detection and recreation
 * - Attach/detach/destroy semantics
 */

#include "rtdb.h"
#include "rtdb_lifecycle.h"
#include "seqlock.h"

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int tests_run = 0;
static int tests_passed = 0;

#define RUN_TEST(fn)                                        \
    do                                                      \
    {                                                       \
        tests_run++;                                        \
        printf("  %-50s ", #fn);                            \
        fn();                                               \
        tests_passed++;                                     \
        printf("PASS\n");                                   \
    } while (0)

/* Ensure shm is cleaned up before each test */
static void cleanup_shm(void)
{
    shm_unlink(RTDB_SHM_NAME);
}

static void test_create_produces_shm(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);

    /* Verify /dev/shm/ems_rtdb exists */
    int fd = shm_open(RTDB_SHM_NAME, O_RDONLY, 0);
    assert(fd >= 0);

    struct stat st;
    assert(fstat(fd, &st) == 0);
    assert((size_t)st.st_size == sizeof(ems_rtdb_t));
    close(fd);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_create_header_magic_version(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);
    assert(rtdb->magic == RTDB_MAGIC);
    assert(rtdb->version == RTDB_VERSION);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_create_topology_counts(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(2, 8, 12, 16, 8);
    assert(rtdb != NULL);
    assert(rtdb->cluster_count == 2);
    assert(rtdb->racks_per_cluster == 8);
    assert(rtdb->modules_per_rack == 12);
    assert(rtdb->cells_per_module == 16);
    assert(rtdb->temps_per_module == 8);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_seqlocks_initialized_to_zero(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(2, 4, 10, 16, 8);
    assert(rtdb != NULL);

    /* Check per-rack seqlocks */
    for (int c = 0; c < MAX_CLUSTERS; c++)
    {
        for (int r = 0; r < MAX_RACKS_PER_CLUSTER; r++)
        {
            uint32_t seq = rtdb->clusters[c].racks[r].lock.sequence;
            assert(seq == 0);
        }
    }

    /* Check non-BMS seqlocks */
    assert(rtdb->pcs.lock.sequence == 0);
    assert(rtdb->gpio.lock.sequence == 0);
    assert(rtdb->meter.lock.sequence == 0);
    assert(rtdb->btms.lock.sequence == 0);
    assert(rtdb->system.lock.sequence == 0);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_attach_existing_shm(void)
{
    cleanup_shm();

    ems_rtdb_t *creator = rtdb_create(1, 4, 10, 16, 8);
    assert(creator != NULL);

    ems_rtdb_t *attached = rtdb_attach();
    assert(attached != NULL);
    assert(attached->magic == RTDB_MAGIC);
    assert(attached->version == RTDB_VERSION);
    assert(attached->cluster_count == 1);

    rtdb_detach(attached);
    rtdb_detach(creator);
    rtdb_destroy();
}

static void test_detach_no_unlink(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);

    rtdb_detach(rtdb);

    /* shm should still exist after detach */
    int fd = shm_open(RTDB_SHM_NAME, O_RDONLY, 0);
    assert(fd >= 0);
    close(fd);

    rtdb_destroy();
}

static void test_destroy_unlinks(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);
    rtdb_detach(rtdb);

    rtdb_destroy();

    /* shm should not exist after destroy */
    int fd = shm_open(RTDB_SHM_NAME, O_RDONLY, 0);
    assert(fd < 0);
    assert(errno == ENOENT);
}

static void test_stale_detection_wrong_magic(void)
{
    cleanup_shm();

    /* Create a stale shm with wrong magic */
    int fd = shm_open(RTDB_SHM_NAME, O_CREAT | O_RDWR, 0600);
    assert(fd >= 0);
    assert(ftruncate(fd, (off_t)sizeof(ems_rtdb_t)) == 0);
    void *ptr = mmap(NULL, sizeof(ems_rtdb_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(ptr != MAP_FAILED);
    close(fd);

    /* Write wrong magic */
    ems_rtdb_t *stale = (ems_rtdb_t *)ptr;
    stale->magic = 0xDEADBEEF;
    stale->version = RTDB_VERSION;
    munmap(ptr, sizeof(ems_rtdb_t));

    /* rtdb_create should detect stale and recreate */
    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);
    assert(rtdb->magic == RTDB_MAGIC);
    assert(rtdb->version == RTDB_VERSION);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_stale_detection_wrong_version(void)
{
    cleanup_shm();

    /* Create a stale shm with wrong version */
    int fd = shm_open(RTDB_SHM_NAME, O_CREAT | O_RDWR, 0600);
    assert(fd >= 0);
    assert(ftruncate(fd, (off_t)sizeof(ems_rtdb_t)) == 0);
    void *ptr = mmap(NULL, sizeof(ems_rtdb_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(ptr != MAP_FAILED);
    close(fd);

    ems_rtdb_t *stale = (ems_rtdb_t *)ptr;
    stale->magic = RTDB_MAGIC;
    stale->version = 99;  /* Wrong version */
    munmap(ptr, sizeof(ems_rtdb_t));

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);
    assert(rtdb->magic == RTDB_MAGIC);
    assert(rtdb->version == RTDB_VERSION);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

static void test_zero_filled(void)
{
    cleanup_shm();

    ems_rtdb_t *rtdb = rtdb_create(1, 4, 10, 16, 8);
    assert(rtdb != NULL);

    /* Check a cell voltage is 0.0f (zero-filled) */
    assert(rtdb->clusters[0].racks[0].modules[0].cell_v[0] == 0.0f);

    /* Check a pack aggregate is 0.0f */
    assert(rtdb->clusters[0].racks[0].pack_v == 0.0f);

    /* Check PCS is zeroed */
    assert(rtdb->pcs.active_power == 0.0f);

    rtdb_detach(rtdb);
    rtdb_destroy();
}

int main(void)
{
    printf("=== RTDB Lifecycle Tests ===\n");

    RUN_TEST(test_create_produces_shm);
    RUN_TEST(test_create_header_magic_version);
    RUN_TEST(test_create_topology_counts);
    RUN_TEST(test_seqlocks_initialized_to_zero);
    RUN_TEST(test_attach_existing_shm);
    RUN_TEST(test_detach_no_unlink);
    RUN_TEST(test_destroy_unlinks);
    RUN_TEST(test_stale_detection_wrong_magic);
    RUN_TEST(test_stale_detection_wrong_version);
    RUN_TEST(test_zero_filled);

    printf("\n");
    if (tests_passed == tests_run)
    {
        printf("All %d tests passed.\n", tests_run);
        return 0;
    }
    else
    {
        printf("FAILED: %d/%d tests passed.\n", tests_passed, tests_run);
        return 1;
    }
}
