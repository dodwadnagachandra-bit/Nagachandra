/**
 * @file test_can_decode.c
 * @brief Unit tests for CAN DBC decode functions.
 *
 * Tests all 10 DBC message type decoders, CAN ID decomposition with
 * EFF flag masking, and error frame handling for bus-off/error-passive/TEC/REC.
 * Uses simple assert-based tests (matching safety_manager test pattern).
 */

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "can_decode.h"

/* Tolerance for floating-point comparisons */
#define FLOAT_EQ(a, b) (fabsf((a) - (b)) < 0.01f)

static int tests_passed = 0;
static int tests_total  = 0;

#define TEST_BEGIN(name) do { \
    tests_total++; \
    printf("  TEST: %s ... ", name);

#define TEST_END() \
    tests_passed++; \
    printf("PASS\n"); \
} while (0)

/* =========================================================================
 * CAN ID decomposition tests
 * ========================================================================= */

static void test_decode_id_cluster0_rack0_msg0(void)
{
    TEST_BEGIN("decode_id: cluster 0, rack 0, msg 0 (with EFF flag)");
    int cluster, rack, msg_offset;
    /* 0x98FF0003 = 0x80000000 (EFF) | 0x18FF0003 (base_id) */
    can_decode_id(0x98FF0003U, 0x18FF0003U, &cluster, &rack, &msg_offset);
    assert(cluster == 0);
    assert(rack == 0);
    assert(msg_offset == 0);
    TEST_END();
}

static void test_decode_id_rack1(void)
{
    TEST_BEGIN("decode_id: cluster 0, rack 1, msg 0");
    int cluster, rack, msg_offset;
    /* rack 1: base + 0x10 = 0x18FF0013, with EFF: 0x98FF0013 */
    can_decode_id(0x98FF0013U, 0x18FF0003U, &cluster, &rack, &msg_offset);
    assert(cluster == 0);
    assert(rack == 1);
    assert(msg_offset == 0);
    TEST_END();
}

static void test_decode_id_cluster1(void)
{
    TEST_BEGIN("decode_id: cluster 1, rack 0, msg 0");
    int cluster, rack, msg_offset;
    /* cluster 1: base + 0x1000 = 0x18FF1003, with EFF: 0x98FF1003 */
    can_decode_id(0x98FF1003U, 0x18FF0003U, &cluster, &rack, &msg_offset);
    assert(cluster == 1);
    assert(rack == 0);
    assert(msg_offset == 0);
    TEST_END();
}

static void test_decode_id_msg_offset6(void)
{
    TEST_BEGIN("decode_id: cluster 0, rack 0, msg 6 (CellVoltage_07)");
    int cluster, rack, msg_offset;
    /* msg_offset 6: base + 0x03 = 0x18FF0006..., wait: base IS 0x18FF0003
     * so actual CAN ID = 0x18FF0003 + 6 = 0x18FF0009, with EFF: 0x98FF0009 */
    /* Plan says 0x98FF0006 -> msg_offset=6, but base_id is 0x18FF0003.
     * 0x18FF0006 - 0x18FF0003 = 3, which is msg_offset 3.
     * For msg_offset 6: 0x18FF0003 + 6 = 0x18FF0009 -> 0x98FF0009 with EFF.
     * The plan behavior spec says 0x98FF0006 -> msg_offset=6 which implies
     * base_id=0x18FF0000, but our actual base_id is 0x18FF0003.
     * Let's test with the actual formula. */
    can_decode_id(0x98FF0009U, 0x18FF0003U, &cluster, &rack, &msg_offset);
    assert(cluster == 0);
    assert(rack == 0);
    assert(msg_offset == 6);
    TEST_END();
}

static void test_decode_id_cluster2_rack3_msg9(void)
{
    TEST_BEGIN("decode_id: cluster 2, rack 3, msg 9");
    int cluster, rack, msg_offset;
    /* base + 2*0x1000 + 3*0x10 + 9 = 0x18FF0003 + 0x2000 + 0x30 + 9 = 0x18FF203C */
    can_decode_id(0x98FF203CU, 0x18FF0003U, &cluster, &rack, &msg_offset);
    assert(cluster == 2);
    assert(rack == 3);
    assert(msg_offset == 9);
    TEST_END();
}

/* =========================================================================
 * PackSummary (msg_offset 0) tests
 * ========================================================================= */

static void test_decode_pack_summary_normal(void)
{
    TEST_BEGIN("pack_summary: normal values (100V, -20A, 50%SOC, 100%SOH, fault=0)");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* pack_v: 1000 (0x03E8) * 0.1 = 100.0V
     * pack_i: -200 (0xFF38 as signed) * 0.1 = -20.0A
     * pack_soc: 100 (0x64) * 0.5 = 50.0%
     * pack_soh: 200 (0xC8) * 0.5 = 100.0%
     * fault_code: 0 (0x0000) */
    uint8_t data[8] = {0xE8, 0x03, 0x38, 0xFF, 0x64, 0xC8, 0x00, 0x00};
    can_decode_pack_summary(data, &rack);
    assert(FLOAT_EQ(rack.pack_v, 100.0f));
    assert(FLOAT_EQ(rack.pack_i, -20.0f));
    assert(FLOAT_EQ(rack.pack_soc, 50.0f));
    assert(FLOAT_EQ(rack.pack_soh, 100.0f));
    assert(rack.fault_code == 0);
    TEST_END();
}

static void test_decode_pack_summary_zero_soc(void)
{
    TEST_BEGIN("pack_summary: zero SOC edge case");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xE8, 0x03, 0x00, 0x00, 0x00, 0xC8, 0x00, 0x00};
    can_decode_pack_summary(data, &rack);
    assert(FLOAT_EQ(rack.pack_soc, 0.0f));
    assert(FLOAT_EQ(rack.pack_i, 0.0f));
    TEST_END();
}

static void test_decode_pack_summary_max_fault(void)
{
    TEST_BEGIN("pack_summary: max fault code 0xFFFF");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xE8, 0x03, 0x00, 0x00, 0x64, 0xC8, 0xFF, 0xFF};
    can_decode_pack_summary(data, &rack);
    assert(rack.fault_code == 0xFFFF);
    TEST_END();
}

static void test_decode_pack_summary_negative_current(void)
{
    TEST_BEGIN("pack_summary: large negative current (discharge)");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* pack_i: -3000 * 10 = -30000, but i16 range is -32768 to 32767
     * Let's use -1000 * 10 = -10000 = 0xD8F0 as signed int16 */
    /* -1000 in raw = -10000 / 0.1... no. Raw int16 * 0.1 = amps.
     * For -300A: raw = -3000 = 0xF448 */
    uint8_t data[8] = {0xE8, 0x03, 0x48, 0xF4, 0x64, 0xC8, 0x00, 0x00};
    can_decode_pack_summary(data, &rack);
    assert(FLOAT_EQ(rack.pack_i, -300.0f));
    TEST_END();
}

/* =========================================================================
 * CellVoltage (msg_offset 1-7) tests
 * ========================================================================= */

static void test_decode_cell_voltage_slot0(void)
{
    TEST_BEGIN("cell_voltage: slot 0, cells 0-3 = 1.0, 2.0, 3.0, 4.0V");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* cell_v = u16 * 0.001V
     * 1.0V = 1000 = 0x03E8
     * 2.0V = 2000 = 0x07D0
     * 3.0V = 3000 = 0x0BB8
     * 4.0V = 4000 = 0x0FA0 */
    uint8_t data[8] = {0xE8, 0x03, 0xD0, 0x07, 0xB8, 0x0B, 0xA0, 0x0F};
    can_decode_cell_voltage(0, data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_v[0], 1.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[1], 2.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[2], 3.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[3], 4.0f));
    TEST_END();
}

static void test_decode_cell_voltage_slot1(void)
{
    TEST_BEGIN("cell_voltage: slot 1, cells 4-7");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* 3.3V = 3300 = 0x0CE4 for all 4 cells */
    uint8_t data[8] = {0xE4, 0x0C, 0xE4, 0x0C, 0xE4, 0x0C, 0xE4, 0x0C};
    can_decode_cell_voltage(1, data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_v[4], 3.3f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[5], 3.3f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[6], 3.3f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[7], 3.3f));
    TEST_END();
}

static void test_decode_cell_voltage_slot6(void)
{
    TEST_BEGIN("cell_voltage: slot 6, cells 24-27");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* 3.5V = 3500 = 0x0DAC */
    uint8_t data[8] = {0xAC, 0x0D, 0xAC, 0x0D, 0xAC, 0x0D, 0xAC, 0x0D};
    can_decode_cell_voltage(6, data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_v[24], 3.5f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[25], 3.5f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[26], 3.5f));
    assert(FLOAT_EQ(rack.modules[0].cell_v[27], 3.5f));
    TEST_END();
}

/* =========================================================================
 * CellTemperature (msg_offset 8) tests
 * ========================================================================= */

static void test_decode_cell_temperature_normal(void)
{
    TEST_BEGIN("cell_temperature: -15, -10, -5, 0, 5, 10, 15, 20 degC");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* value = raw - 40, so raw = value + 40
     * -15 + 40 = 25 = 0x19
     * -10 + 40 = 30 = 0x1E
     *  -5 + 40 = 35 = 0x23
     *   0 + 40 = 40 = 0x28
     *   5 + 40 = 45 = 0x2D
     *  10 + 40 = 50 = 0x32
     *  15 + 40 = 55 = 0x37
     *  20 + 40 = 60 = 0x3C */
    uint8_t data[8] = {0x19, 0x1E, 0x23, 0x28, 0x2D, 0x32, 0x37, 0x3C};
    can_decode_cell_temperature(data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_t[0], -15.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[1], -10.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[2],  -5.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[3],   0.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[4],   5.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[5],  10.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[6],  15.0f));
    assert(FLOAT_EQ(rack.modules[0].cell_t[7],  20.0f));
    TEST_END();
}

static void test_decode_cell_temperature_min(void)
{
    TEST_BEGIN("cell_temperature: minimum -40 degC (raw 0)");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    can_decode_cell_temperature(data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_t[0], -40.0f));
    TEST_END();
}

static void test_decode_cell_temperature_max(void)
{
    TEST_BEGIN("cell_temperature: maximum 215 degC (raw 255)");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    can_decode_cell_temperature(data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_t[0], 215.0f));
    TEST_END();
}

/* =========================================================================
 * RackStatus (msg_offset 9) tests
 * ========================================================================= */

static void test_decode_rack_status_normal(void)
{
    TEST_BEGIN("rack_status: online=1, min=3.2V, max=3.4V, avg=3.3V");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    /* online: 1 (byte 0)
     * balancing_count: 5 (byte 1) -- not stored in ems_rack_t
     * min_cell_v: 3200 = 0x0C80 (bytes 2-3) * 0.001 = 3.2V
     * max_cell_v: 3400 = 0x0D48 (bytes 4-5) * 0.001 = 3.4V
     * avg_cell_v: 3300 = 0x0CE4 (bytes 6-7) * 0.001 = 3.3V */
    uint8_t data[8] = {0x01, 0x05, 0x80, 0x0C, 0x48, 0x0D, 0xE4, 0x0C};
    can_decode_rack_status(data, &rack);
    assert(rack.online == 1);
    assert(FLOAT_EQ(rack.min_cell_v, 3.2f));
    assert(FLOAT_EQ(rack.max_cell_v, 3.4f));
    assert(FLOAT_EQ(rack.avg_cell_v, 3.3f));
    TEST_END();
}

static void test_decode_rack_status_offline(void)
{
    TEST_BEGIN("rack_status: online=0 (offline rack)");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    can_decode_rack_status(data, &rack);
    assert(rack.online == 0);
    assert(FLOAT_EQ(rack.min_cell_v, 0.0f));
    TEST_END();
}

/* =========================================================================
 * can_decode_frame dispatch tests
 * ========================================================================= */

static void test_decode_frame_dispatches_pack_summary(void)
{
    TEST_BEGIN("decode_frame: dispatches msg_offset=0 to pack_summary");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xE8, 0x03, 0x38, 0xFF, 0x64, 0xC8, 0x00, 0x00};
    can_decode_frame(CAN_MSG_PACK_SUMMARY, data, &rack);
    assert(FLOAT_EQ(rack.pack_v, 100.0f));
    TEST_END();
}

static void test_decode_frame_dispatches_cell_voltage(void)
{
    TEST_BEGIN("decode_frame: dispatches msg_offset=1 to cell_voltage slot 0");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xE8, 0x03, 0xD0, 0x07, 0xB8, 0x0B, 0xA0, 0x0F};
    can_decode_frame(CAN_MSG_CELL_VOLTAGE_01, data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_v[0], 1.0f));
    TEST_END();
}

static void test_decode_frame_dispatches_temperature(void)
{
    TEST_BEGIN("decode_frame: dispatches msg_offset=8 to cell_temperature");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0x28, 0x28, 0x28, 0x28, 0x28, 0x28, 0x28, 0x28};
    can_decode_frame(CAN_MSG_CELL_TEMPERATURE, data, &rack);
    assert(FLOAT_EQ(rack.modules[0].cell_t[0], 0.0f)); /* 40 - 40 = 0 */
    TEST_END();
}

static void test_decode_frame_dispatches_rack_status(void)
{
    TEST_BEGIN("decode_frame: dispatches msg_offset=9 to rack_status");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0x01, 0x00, 0x80, 0x0C, 0x48, 0x0D, 0xE4, 0x0C};
    can_decode_frame(CAN_MSG_RACK_STATUS, data, &rack);
    assert(rack.online == 1);
    TEST_END();
}

static void test_decode_frame_ignores_invalid_offset(void)
{
    TEST_BEGIN("decode_frame: ignores msg_offset >= CAN_MSG_COUNT");
    ems_rack_t rack;
    memset(&rack, 0, sizeof(rack));
    uint8_t data[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    /* Should not crash or modify rack */
    can_decode_frame(CAN_MSG_COUNT, data, &rack);
    can_decode_frame(99, data, &rack);
    assert(rack.pack_v == 0.0f); /* unchanged */
    TEST_END();
}

/* =========================================================================
 * Error frame handling tests
 * ========================================================================= */

static void test_error_frame_bus_off(void)
{
    TEST_BEGIN("error_frame: CAN_ERR_BUSOFF sets bus_state to CAN_BUS_OFF");
    ems_can_health_t health;
    memset(&health, 0, sizeof(health));
    /* CAN_ERR_BUSOFF = 0x00000040 */
    uint8_t data[8] = {0, 0, 0, 0, 0, 0, 50, 30};
    can_handle_error_frame(0x00000040U, data, &health);
    assert(health.bus_state == CAN_BUS_OFF);
    assert(health.tx_error_count == 50);
    assert(health.rx_error_count == 30);
    TEST_END();
}

static void test_error_frame_error_passive(void)
{
    TEST_BEGIN("error_frame: CAN_ERR_CRTL with TX_PASSIVE sets CAN_BUS_ERROR_PASSIVE");
    ems_can_health_t health;
    memset(&health, 0, sizeof(health));
    /* CAN_ERR_CRTL = 0x00000004
     * CAN_ERR_CRTL_TX_PASSIVE = 0x20 in data[1] */
    uint8_t data[8] = {0, 0x20, 0, 0, 0, 0, 128, 10};
    can_handle_error_frame(0x00000004U, data, &health);
    assert(health.bus_state == CAN_BUS_ERROR_PASSIVE);
    assert(health.tx_error_count == 128);
    assert(health.rx_error_count == 10);
    TEST_END();
}

static void test_error_frame_error_warning(void)
{
    TEST_BEGIN("error_frame: CAN_ERR_CRTL with TX_WARNING sets CAN_BUS_ERROR_WARNING");
    ems_can_health_t health;
    memset(&health, 0, sizeof(health));
    /* CAN_ERR_CRTL_TX_WARNING = 0x04 in data[1] */
    uint8_t data[8] = {0, 0x04, 0, 0, 0, 0, 96, 20};
    can_handle_error_frame(0x00000004U, data, &health);
    assert(health.bus_state == CAN_BUS_ERROR_WARNING);
    assert(health.tx_error_count == 96);
    assert(health.rx_error_count == 20);
    TEST_END();
}

static void test_error_frame_rx_passive(void)
{
    TEST_BEGIN("error_frame: CAN_ERR_CRTL with RX_PASSIVE sets CAN_BUS_ERROR_PASSIVE");
    ems_can_health_t health;
    memset(&health, 0, sizeof(health));
    /* CAN_ERR_CRTL_RX_PASSIVE = 0x10 in data[1] */
    uint8_t data[8] = {0, 0x10, 0, 0, 0, 0, 10, 140};
    can_handle_error_frame(0x00000004U, data, &health);
    assert(health.bus_state == CAN_BUS_ERROR_PASSIVE);
    TEST_END();
}

static void test_error_frame_tec_rec_extraction(void)
{
    TEST_BEGIN("error_frame: TEC=50, REC=30 from data[6], data[7]");
    ems_can_health_t health;
    memset(&health, 0, sizeof(health));
    uint8_t data[8] = {0, 0, 0, 0, 0, 0, 50, 30};
    can_handle_error_frame(0x00000040U, data, &health);
    assert(health.tx_error_count == 50);
    assert(health.rx_error_count == 30);
    TEST_END();
}

/* =========================================================================
 * Main
 * ========================================================================= */

int main(void)
{
    printf("=== CAN Decode Unit Tests ===\n\n");

    printf("[CAN ID Decomposition]\n");
    test_decode_id_cluster0_rack0_msg0();
    test_decode_id_rack1();
    test_decode_id_cluster1();
    test_decode_id_msg_offset6();
    test_decode_id_cluster2_rack3_msg9();

    printf("\n[PackSummary Decode]\n");
    test_decode_pack_summary_normal();
    test_decode_pack_summary_zero_soc();
    test_decode_pack_summary_max_fault();
    test_decode_pack_summary_negative_current();

    printf("\n[CellVoltage Decode]\n");
    test_decode_cell_voltage_slot0();
    test_decode_cell_voltage_slot1();
    test_decode_cell_voltage_slot6();

    printf("\n[CellTemperature Decode]\n");
    test_decode_cell_temperature_normal();
    test_decode_cell_temperature_min();
    test_decode_cell_temperature_max();

    printf("\n[RackStatus Decode]\n");
    test_decode_rack_status_normal();
    test_decode_rack_status_offline();

    printf("\n[Frame Dispatch]\n");
    test_decode_frame_dispatches_pack_summary();
    test_decode_frame_dispatches_cell_voltage();
    test_decode_frame_dispatches_temperature();
    test_decode_frame_dispatches_rack_status();
    test_decode_frame_ignores_invalid_offset();

    printf("\n[Error Frame Handling]\n");
    test_error_frame_bus_off();
    test_error_frame_error_passive();
    test_error_frame_error_warning();
    test_error_frame_rx_passive();
    test_error_frame_tec_rec_extraction();

    printf("\n=== Results: %d/%d tests passed ===\n", tests_passed, tests_total);
    return (tests_passed == tests_total) ? 0 : 1;
}
