/**
 * @file test_response_matrix.c
 * @brief Unit tests for safety response matrix logic.
 *
 * Pure logic tests -- no GPIO hardware, no RTDB, no ZMQ.
 * Tests all DI-to-DO paths from the CONTEXT.md safety response matrix.
 *
 * GPIO config matches residential profile:
 *   DI-2 (DOOR_SWITCH): active_low
 *   DI-7 (ESTOP_NC):    active_low
 *   All others:          active_high
 *   All DO:              active_high
 */

#include "response_matrix.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

/* -------------------------------------------------------------------------
 * Test infrastructure
 * -----------------------------------------------------------------------*/

static int tests_run    = 0;
static int tests_passed = 0;
static int tests_failed = 0;

#define RUN_TEST(fn)                                         \
    do                                                       \
    {                                                        \
        tests_run++;                                         \
        printf("  [%2d] %-60s ", tests_run, #fn);            \
        fn();                                                \
        tests_passed++;                                      \
        printf("PASS\n");                                    \
    } while (0)

/* Assertion macro that prints details on failure */
#define ASSERT_EQ(actual, expected)                                    \
    do                                                                 \
    {                                                                  \
        if ((actual) != (expected))                                    \
        {                                                              \
            printf("FAIL\n");                                          \
            fprintf(stderr,                                            \
                    "    %s:%d: %s == 0x%02X, expected 0x%02X\n",      \
                    __FILE__, __LINE__, #actual,                       \
                    (unsigned)(actual), (unsigned)(expected));          \
            tests_failed++;                                            \
            tests_passed--;                                            \
            return;                                                    \
        }                                                              \
    } while (0)

#define ASSERT_TRUE(cond)                                              \
    do                                                                 \
    {                                                                  \
        if (!(cond))                                                   \
        {                                                              \
            printf("FAIL\n");                                          \
            fprintf(stderr, "    %s:%d: %s was false\n",               \
                    __FILE__, __LINE__, #cond);                        \
            tests_failed++;                                            \
            tests_passed--;                                            \
            return;                                                    \
        }                                                              \
    } while (0)

#define ASSERT_FALSE(cond) ASSERT_TRUE(!(cond))

/* -------------------------------------------------------------------------
 * Shared test config (residential profile)
 * -----------------------------------------------------------------------*/

/**
 * Create a gpio_config_t matching the residential profile.
 * DI-2 (DOOR_SWITCH): active_low
 * DI-7 (ESTOP_NC):    active_low
 * All others:          active_high
 */
static gpio_config_t make_residential_config(void)
{
    gpio_config_t config;
    memset(&config, 0, sizeof(config));

    /* DI active_low flags */
    config.active_low_di[2] = true;  /* DI-2: DOOR_SWITCH (active_low) */
    config.active_low_di[7] = true;  /* DI-7: ESTOP_NC    (active_low) */
    /* All other DI: active_high (false, already zeroed) */

    /* All DO: active_high (false, already zeroed) */

    return config;
}

/**
 * Create a zeroed safety state.
 */
static safety_state_t make_clean_state(void)
{
    safety_state_t state;
    memset(&state, 0, sizeof(state));
    return state;
}

/**
 * Create a zeroed DI array.
 */
static void clear_di(uint8_t *di)
{
    memset(di, 0, GPIO_NUM_DI);
}

/* -------------------------------------------------------------------------
 * Test: Normal state -> only DO-3 (running lamp) on
 * -----------------------------------------------------------------------*/

static void test_normal_state(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Normal: DI-0 (ACDB feedback) high (contactor closed) */
    di[0] = 1;
    /* DI-2 (Door) raw high -> active_low inverted -> logical low = door closed */
    di[2] = 1;
    /* DI-7 (ESTOP_NC) raw high -> active_low inverted -> logical low = normal */
    di[7] = 1;

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    /* Only running lamp should be on */
    ASSERT_EQ(outputs, DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: E-Stop dual-channel confirm
 * DI-6 high (NO pressed) + DI-7 low raw (NC pressed, active_low -> logical high)
 * -> DO-0 ACDB trip, DO-4 fault, DO-5 PCS stop, DO-6 siren, DO-3 off
 * -----------------------------------------------------------------------*/

static void test_estop_dual_channel_confirm(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 1;  /* Door closed (active_low: raw high -> logical low) */
    di[6] = 1;  /* ESTOP_NO pressed (active_high: raw high -> logical high) */
    di[7] = 0;  /* ESTOP_NC pressed (active_low: raw low -> logical high) */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    uint8_t expected = DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    ASSERT_EQ(outputs, expected);
    ASSERT_TRUE(state.estop_active);
    ASSERT_TRUE(state.estop_latched);
    ASSERT_FALSE(state.estop_discrepancy);
    /* Running lamp must be OFF */
    ASSERT_FALSE(outputs & DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: E-Stop single-channel discrepancy (wiring fault)
 * DI-6 high, DI-7 high raw (NC not pressed, active_low -> logical low)
 * -> estop_discrepancy=true, NO E-Stop outputs
 * -----------------------------------------------------------------------*/

static void test_estop_discrepancy(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 1;  /* Door closed */
    di[6] = 1;  /* ESTOP_NO pressed */
    di[7] = 1;  /* ESTOP_NC NOT pressed (active_low: raw high -> logical low) */

    evaluate_inputs(di, &config, &state);

    ASSERT_TRUE(state.estop_discrepancy);
    ASSERT_FALSE(state.estop_active);

    uint8_t outputs = evaluate_response_matrix(&state);

    /* Warning lamp for discrepancy, running lamp since no protective outputs */
    ASSERT_TRUE(outputs & DO_WARNING_LAMP);
    ASSERT_TRUE(outputs & DO_RUNNING_LAMP);
    /* No protective outputs from E-Stop */
    ASSERT_FALSE(outputs & DO_ACDB_TRIP);
    ASSERT_FALSE(outputs & DO_PCS_STOP);
    ASSERT_FALSE(outputs & DO_SIREN);
}

/* -------------------------------------------------------------------------
 * Test: Fire dual-confirm (DI-3 + DI-4 both high)
 * -> DO-0 ACDB trip, DO-1 extinguisher, DO-4 fault, DO-5 PCS stop, DO-6 siren
 * -----------------------------------------------------------------------*/

static void test_fire_dual_confirm(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 1;  /* Door closed */
    di[3] = 1;  /* Smoke detected */
    di[4] = 1;  /* Heat detected */
    di[7] = 1;  /* ESTOP_NC normal */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    uint8_t expected = DO_ACDB_TRIP | DO_EXTINGUISHER | DO_FAULT_LAMP
                       | DO_PCS_STOP | DO_SIREN;
    ASSERT_EQ(outputs, expected);
    ASSERT_TRUE(state.fire_active);
    ASSERT_TRUE(state.fire_latched);
}

/* -------------------------------------------------------------------------
 * Test: Fire single sensor (DI-3 only) -> DO-2 warning only, NO extinguisher
 * -----------------------------------------------------------------------*/

static void test_fire_single_sensor(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 1;  /* Door closed */
    di[3] = 1;  /* Smoke detected (only) */
    di[7] = 1;  /* ESTOP_NC normal */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    /* Warning lamp for single sensor, running lamp (no protective outputs) */
    ASSERT_TRUE(outputs & DO_WARNING_LAMP);
    ASSERT_TRUE(outputs & DO_RUNNING_LAMP);
    /* No extinguisher, no ACDB trip */
    ASSERT_FALSE(outputs & DO_EXTINGUISHER);
    ASSERT_FALSE(outputs & DO_ACDB_TRIP);
    ASSERT_FALSE(state.fire_active);
    ASSERT_TRUE(state.fire_single_sensor);
}

/* -------------------------------------------------------------------------
 * Test: Flood (DI-1 high) -> DO-0, DO-4, DO-5, DO-6
 * -----------------------------------------------------------------------*/

static void test_flood(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[1] = 1;  /* Flood detected */
    di[2] = 1;  /* Door closed */
    di[7] = 1;  /* ESTOP_NC normal */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    uint8_t expected = DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    ASSERT_EQ(outputs, expected);
    ASSERT_TRUE(state.flood_active);
    ASSERT_TRUE(state.flood_latched);
}

/* -------------------------------------------------------------------------
 * Test: ACDB feedback loss (DI-0 low) -> DO-4, DO-5, DO-6
 * -----------------------------------------------------------------------*/

static void test_acdb_loss(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 0;  /* ACDB feedback LOST (active_high: low = loss) */
    di[2] = 1;  /* Door closed */
    di[7] = 1;  /* ESTOP_NC normal */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    uint8_t expected = DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    ASSERT_EQ(outputs, expected);
    ASSERT_TRUE(state.acdb_loss);
    /* No ACDB trip (it's already open) */
    ASSERT_FALSE(outputs & DO_ACDB_TRIP);
}

/* -------------------------------------------------------------------------
 * Test: Door open (DI-2 active_low, raw low = door open)
 * -> DO-2 warning only
 * -----------------------------------------------------------------------*/

static void test_door_open(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 0;  /* Door OPEN (active_low: raw low -> logical high = open) */
    di[7] = 1;  /* ESTOP_NC normal */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    /* Warning lamp + running lamp (no protective outputs) */
    ASSERT_TRUE(outputs & DO_WARNING_LAMP);
    ASSERT_TRUE(outputs & DO_RUNNING_LAMP);
    ASSERT_TRUE(state.door_open);
    /* No shutdown */
    ASSERT_FALSE(outputs & DO_ACDB_TRIP);
    ASSERT_FALSE(outputs & DO_PCS_STOP);
}

/* -------------------------------------------------------------------------
 * Test: GPIO failure -> ALL safety outputs asserted
 * -----------------------------------------------------------------------*/

static void test_gpio_failure(void)
{
    safety_state_t state = make_clean_state();
    state.gpio_failure = true;

    uint8_t outputs = evaluate_response_matrix(&state);

    /* All safety outputs except running lamp */
    ASSERT_TRUE(outputs & DO_ACDB_TRIP);
    ASSERT_TRUE(outputs & DO_EXTINGUISHER);
    ASSERT_TRUE(outputs & DO_FAULT_LAMP);
    ASSERT_TRUE(outputs & DO_PCS_STOP);
    ASSERT_TRUE(outputs & DO_SIREN);
    /* Running lamp OFF (protective outputs active) */
    ASSERT_FALSE(outputs & DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: E-Stop latched -- inputs clear but state remains until safety_reset()
 * -----------------------------------------------------------------------*/

static void test_estop_latched(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Step 1: Trigger E-Stop */
    di[0] = 1;
    di[2] = 1;
    di[6] = 1;  /* ESTOP_NO pressed */
    di[7] = 0;  /* ESTOP_NC pressed */
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.estop_active);
    ASSERT_TRUE(state.estop_latched);

    /* Step 2: Clear inputs (E-Stop released) */
    di[6] = 0;  /* ESTOP_NO released */
    di[7] = 1;  /* ESTOP_NC released (active_low: high = normal) */
    evaluate_inputs(di, &config, &state);

    /* State should still be latched */
    ASSERT_TRUE(state.estop_active);
    ASSERT_TRUE(state.estop_latched);

    /* Outputs should still have E-Stop response */
    uint8_t outputs = evaluate_response_matrix(&state);
    ASSERT_TRUE(outputs & DO_ACDB_TRIP);
    ASSERT_TRUE(outputs & DO_PCS_STOP);
}

/* -------------------------------------------------------------------------
 * Test: safety_reset() rejected when inputs still active
 * -----------------------------------------------------------------------*/

static void test_reset_rejected_inputs_active(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Trigger E-Stop */
    di[0] = 1;
    di[2] = 1;
    di[6] = 1;
    di[7] = 0;
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.estop_latched);

    /* Try reset while E-Stop still pressed */
    int rc = safety_reset(&state, di, &config);
    ASSERT_EQ(rc, -1);  /* Rejected */
    ASSERT_TRUE(state.estop_latched);
    ASSERT_TRUE(state.estop_active);
}

/* -------------------------------------------------------------------------
 * Test: safety_reset() accepted when inputs cleared
 * -----------------------------------------------------------------------*/

static void test_reset_accepted_inputs_cleared(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Trigger E-Stop */
    di[0] = 1;
    di[2] = 1;
    di[6] = 1;
    di[7] = 0;
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.estop_latched);

    /* Clear E-Stop inputs */
    di[6] = 0;
    di[7] = 1;
    evaluate_inputs(di, &config, &state);

    /* Reset should succeed */
    int rc = safety_reset(&state, di, &config);
    ASSERT_EQ(rc, 0);
    ASSERT_FALSE(state.estop_latched);
    ASSERT_FALSE(state.estop_active);

    /* Outputs should return to normal */
    uint8_t outputs = evaluate_response_matrix(&state);
    ASSERT_EQ(outputs, DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: Multiple simultaneous conditions (E-Stop + Door) combine outputs
 * -----------------------------------------------------------------------*/

static void test_multiple_conditions(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    di[0] = 1;  /* ACDB feedback OK */
    di[2] = 0;  /* Door OPEN (active_low: raw low -> logical high) */
    di[6] = 1;  /* ESTOP_NO pressed */
    di[7] = 0;  /* ESTOP_NC pressed */

    evaluate_inputs(di, &config, &state);
    uint8_t outputs = evaluate_response_matrix(&state);

    /* E-Stop outputs + Warning lamp (door open) */
    ASSERT_TRUE(outputs & DO_ACDB_TRIP);
    ASSERT_TRUE(outputs & DO_PCS_STOP);
    ASSERT_TRUE(outputs & DO_SIREN);
    ASSERT_TRUE(outputs & DO_FAULT_LAMP);
    ASSERT_TRUE(outputs & DO_WARNING_LAMP);
    /* Running lamp OFF (fault active) */
    ASSERT_FALSE(outputs & DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: Running lamp OFF when any fault active (IEC 60073)
 * -----------------------------------------------------------------------*/

static void test_running_lamp_off_during_fault(void)
{
    safety_state_t state = make_clean_state();

    /* Only ACDB loss -- no ACDB trip but PCS stop and siren active */
    state.acdb_loss = true;
    uint8_t outputs = evaluate_response_matrix(&state);

    /* Protective outputs active (PCS stop, siren) */
    ASSERT_TRUE(outputs & DO_PCS_STOP);
    ASSERT_TRUE(outputs & DO_SIREN);
    ASSERT_TRUE(outputs & DO_FAULT_LAMP);
    /* Running lamp MUST be OFF */
    ASSERT_FALSE(outputs & DO_RUNNING_LAMP);
}

/* -------------------------------------------------------------------------
 * Test: Fire latch and reset
 * -----------------------------------------------------------------------*/

static void test_fire_latch_and_reset(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Trigger fire */
    di[0] = 1;
    di[2] = 1;
    di[3] = 1;  /* Smoke */
    di[4] = 1;  /* Heat */
    di[7] = 1;  /* ESTOP normal */
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.fire_active);
    ASSERT_TRUE(state.fire_latched);

    /* Clear fire inputs */
    di[3] = 0;
    di[4] = 0;
    evaluate_inputs(di, &config, &state);
    /* Still latched */
    ASSERT_TRUE(state.fire_active);
    ASSERT_TRUE(state.fire_latched);

    /* Reset */
    int rc = safety_reset(&state, di, &config);
    ASSERT_EQ(rc, 0);
    ASSERT_FALSE(state.fire_latched);
    ASSERT_FALSE(state.fire_active);
}

/* -------------------------------------------------------------------------
 * Test: Flood latch and reset
 * -----------------------------------------------------------------------*/

static void test_flood_latch_and_reset(void)
{
    gpio_config_t config = make_residential_config();
    safety_state_t state = make_clean_state();
    uint8_t di[GPIO_NUM_DI];
    clear_di(di);

    /* Trigger flood */
    di[0] = 1;
    di[1] = 1;  /* Flood */
    di[2] = 1;
    di[7] = 1;
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.flood_active);

    /* Clear flood, still latched */
    di[1] = 0;
    evaluate_inputs(di, &config, &state);
    ASSERT_TRUE(state.flood_active);
    ASSERT_TRUE(state.flood_latched);

    /* Reset with input cleared */
    int rc = safety_reset(&state, di, &config);
    ASSERT_EQ(rc, 0);
    ASSERT_FALSE(state.flood_latched);
    ASSERT_FALSE(state.flood_active);
}

/* =========================================================================
 * Main test runner
 * ========================================================================= */

int main(void)
{
    printf("\n=== Safety Response Matrix Tests ===\n\n");

    RUN_TEST(test_normal_state);
    RUN_TEST(test_estop_dual_channel_confirm);
    RUN_TEST(test_estop_discrepancy);
    RUN_TEST(test_fire_dual_confirm);
    RUN_TEST(test_fire_single_sensor);
    RUN_TEST(test_flood);
    RUN_TEST(test_acdb_loss);
    RUN_TEST(test_door_open);
    RUN_TEST(test_gpio_failure);
    RUN_TEST(test_estop_latched);
    RUN_TEST(test_reset_rejected_inputs_active);
    RUN_TEST(test_reset_accepted_inputs_cleared);
    RUN_TEST(test_multiple_conditions);
    RUN_TEST(test_running_lamp_off_during_fault);
    RUN_TEST(test_fire_latch_and_reset);
    RUN_TEST(test_flood_latch_and_reset);

    printf("\n=== Results: %d/%d passed", tests_passed, tests_run);
    if (tests_failed > 0)
    {
        printf(", %d FAILED", tests_failed);
    }
    printf(" ===\n\n");

    return tests_failed > 0 ? 1 : 0;
}
