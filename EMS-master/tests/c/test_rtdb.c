#include "rtdb.h"
#include "seqlock.h"

#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static int tests_run = 0;
static int tests_passed = 0;

#define RUN_TEST(fn)                                    \
    do                                                  \
    {                                                   \
        tests_run++;                                    \
        printf("  %-40s ", #fn);                        \
        fn();                                           \
        tests_passed++;                                 \
        printf("PASS\n");                               \
    } while (0)

static void test_magic_and_version(void)
{
    assert(RTDB_MAGIC == 0x454D5352);
    assert(RTDB_VERSION == 1);
}

static void test_struct_sizes(void)
{
    printf("\n");
    printf("    sizeof(ems_module_t)  = %zu\n", sizeof(ems_module_t));
    printf("    sizeof(ems_rack_t)    = %zu\n", sizeof(ems_rack_t));
    printf("    sizeof(ems_cluster_t) = %zu\n", sizeof(ems_cluster_t));
    printf("    sizeof(ems_pcs_t)     = %zu\n", sizeof(ems_pcs_t));
    printf("    sizeof(ems_gpio_t)    = %zu\n", sizeof(ems_gpio_t));
    printf("    sizeof(ems_meter_t)   = %zu\n", sizeof(ems_meter_t));
    printf("    sizeof(ems_btms_t)    = %zu\n", sizeof(ems_btms_t));
    printf("    sizeof(ems_system_t)  = %zu\n", sizeof(ems_system_t));
    printf("    sizeof(ems_rtdb_t)    = %zu\n", sizeof(ems_rtdb_t));
    printf("    ");

    assert(sizeof(ems_module_t) < 1024);
    assert(sizeof(ems_rack_t) > MAX_MODULES_PER_RACK * sizeof(ems_module_t));
    assert(sizeof(ems_rtdb_t) < 2 * 1024 * 1024);
}

static void test_populate_residential(void)
{
    static ems_rtdb_t rtdb;
    memset(&rtdb, 0, sizeof(rtdb));

    rtdb.magic = RTDB_MAGIC;
    rtdb.version = RTDB_VERSION;
    rtdb.cluster_count = 1;
    rtdb.racks_per_cluster = 4;
    rtdb.modules_per_rack = 8;
    rtdb.cells_per_module = 16;
    rtdb.temps_per_module = 8;

    /* Populate a cell voltage and read back */
    rtdb.clusters[0].racks[0].modules[0].cell_v[0] = 3.3f;
    assert(rtdb.clusters[0].racks[0].modules[0].cell_v[0] == 3.3f);

    /* Populate rack-level aggregates */
    rtdb.clusters[0].racks[0].pack_v = 51.2f;
    rtdb.clusters[0].racks[0].pack_soc = 85.0f;
    assert(rtdb.clusters[0].racks[0].pack_v == 51.2f);
    assert(rtdb.clusters[0].racks[0].pack_soc == 85.0f);
}

static void test_populate_container(void)
{
    static ems_rtdb_t rtdb;
    memset(&rtdb, 0, sizeof(rtdb));

    rtdb.magic = RTDB_MAGIC;
    rtdb.version = RTDB_VERSION;
    rtdb.cluster_count = 4;
    rtdb.racks_per_cluster = 16;
    rtdb.modules_per_rack = 20;
    rtdb.cells_per_module = 108;
    rtdb.temps_per_module = 40;

    /* Populate at max valid indices */
    rtdb.clusters[3].racks[15].modules[19].cell_v[107] = 3.6f;
    assert(rtdb.clusters[3].racks[15].modules[19].cell_v[107] == 3.6f);

    /* Max topology rack aggregates */
    rtdb.clusters[3].racks[15].pack_i = -25.0f;
    assert(rtdb.clusters[3].racks[15].pack_i == -25.0f);
}

static void test_seqlock_basic(void)
{
    ems_seqlock_t lock;
    ems_seqlock_init(&lock);

    /* Initial state: sequence 0 (even) */
    uint32_t seq = ems_seqlock_read_begin(&lock);
    assert(seq == 0);
    assert(!ems_seqlock_read_retry(&lock, seq));

    /* Write cycle: begin (odd) -> end (even=2) */
    ems_seqlock_write_begin(&lock);
    ems_seqlock_write_end(&lock);

    seq = ems_seqlock_read_begin(&lock);
    assert(seq == 2);
    assert(!ems_seqlock_read_retry(&lock, seq));
}

static void test_non_bms_sections(void)
{
    static ems_rtdb_t rtdb;
    memset(&rtdb, 0, sizeof(rtdb));

    rtdb.pcs.active_power = 100.5f;
    assert(rtdb.pcs.active_power == 100.5f);

    rtdb.gpio.di[6] = 1;  /* E-Stop channel */
    assert(rtdb.gpio.di[6] == 1);

    rtdb.meter.energy_import = 1234.5f;
    assert(rtdb.meter.energy_import == 1234.5f);

    rtdb.btms.fan_speed_pct = 75.0f;
    assert(rtdb.btms.fan_speed_pct == 75.0f);

    rtdb.system.control_state = EMS_STATE_CHARGING;
    assert(rtdb.system.control_state == EMS_STATE_CHARGING);

    rtdb.system.total_power_kw = 250.0f;
    assert(rtdb.system.total_power_kw == 250.0f);
}

static void test_topology_counts(void)
{
    ems_rtdb_t rtdb;
    memset(&rtdb, 0, sizeof(rtdb));

    rtdb.cluster_count = 2;
    rtdb.racks_per_cluster = 8;
    rtdb.modules_per_rack = 12;
    rtdb.cells_per_module = 16;
    rtdb.temps_per_module = 8;

    assert(rtdb.cluster_count == 2);
    assert(rtdb.racks_per_cluster == 8);
    assert(rtdb.modules_per_rack == 12);
    assert(rtdb.cells_per_module == 16);
    assert(rtdb.temps_per_module == 8);
}

int main(void)
{
    printf("=== RTDB Layout Tests ===\n");

    RUN_TEST(test_magic_and_version);
    RUN_TEST(test_struct_sizes);
    RUN_TEST(test_populate_residential);
    RUN_TEST(test_populate_container);
    RUN_TEST(test_seqlock_basic);
    RUN_TEST(test_non_bms_sections);
    RUN_TEST(test_topology_counts);

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
