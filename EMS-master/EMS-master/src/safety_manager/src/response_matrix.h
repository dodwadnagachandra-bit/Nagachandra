#ifndef SAFETY_RESPONSE_MATRIX_H
#define SAFETY_RESPONSE_MATRIX_H

/**
 * @file response_matrix.h
 * @brief Safety response matrix: DI conditions to DO output mapping.
 *
 * Implements the locked safety response matrix from CONTEXT.md:
 *   - E-Stop (DI-6+7 dual-channel) -> ACDB trip, fault, PCS stop, siren
 *   - Fire (DI-3+4 dual-confirm)   -> ACDB trip, extinguisher, fault, PCS stop, siren
 *   - Flood (DI-1)                 -> ACDB trip, fault, PCS stop, siren
 *   - ACDB loss (DI-0)             -> Fault, PCS stop, siren
 *   - Door open (DI-2)             -> Warning lamp only
 *   - GPIO failure                 -> ALL safety outputs (worst case)
 *
 * Latching: E-Stop, Fire, Flood latch until safety_reset() is called.
 * Auto-recover: ACDB loss, Door open, GPIO failure clear when condition resolves.
 *
 * No dynamic allocation. No heap usage. Safe for SCHED_FIFO scan loop.
 */

#include <stdbool.h>
#include <stdint.h>

#include "gpio.h"

/* ---------------------------------------------------------------------------
 * DO bitmask constants (bit position = DO pin number)
 * -------------------------------------------------------------------------*/

#define DO_ACDB_TRIP     (1 << 0)  /**< DO-0: ACDB contactor trip */
#define DO_EXTINGUISHER  (1 << 1)  /**< DO-1: Fire extinguisher discharge */
#define DO_WARNING_LAMP  (1 << 2)  /**< DO-2: Amber warning lamp */
#define DO_RUNNING_LAMP  (1 << 3)  /**< DO-3: Green running lamp */
#define DO_FAULT_LAMP    (1 << 4)  /**< DO-4: Red fault lamp */
#define DO_PCS_STOP      (1 << 5)  /**< DO-5: PCS emergency stop */
#define DO_SIREN         (1 << 6)  /**< DO-6: Audible alarm siren */
/* DO-7 spare: never asserted by safety_manager */

/**
 * @brief Mask of protective outputs (any of these ON -> fault lamp ON).
 *
 * Per CONTEXT.md: "Fault lamp follows the any protective output rule:
 * if DO-0, DO-1, DO-5, or DO-6 is asserted, Fault lamp is ON."
 */
#define PROTECTIVE_OUTPUTS \
    (DO_ACDB_TRIP | DO_EXTINGUISHER | DO_PCS_STOP | DO_SIREN)

/* ---------------------------------------------------------------------------
 * Safety state
 * -------------------------------------------------------------------------*/

/**
 * @brief Safety state derived from DI evaluation.
 *
 * Latched states (estop, fire, flood) stay true even after inputs clear.
 * They require an explicit safety_reset() call to clear.
 */
typedef struct
{
    /* Current input conditions */
    bool estop_active;         /**< E-Stop confirmed (both channels) */
    bool fire_active;          /**< Fire confirmed (both sensors) */
    bool flood_active;         /**< Flood detected */
    bool acdb_loss;            /**< ACDB feedback loss (auto-recovers) */
    bool door_open;            /**< Door open (auto-recovers) */
    bool gpio_failure;         /**< GPIO chip/read failure (auto-recovers) */
    bool estop_discrepancy;    /**< E-Stop wiring fault (channels disagree) */
    bool fire_single_sensor;   /**< Single fire sensor active (warning only) */

    /* Latch tracking -- stays true until safety_reset() */
    bool estop_latched;        /**< E-Stop latch (manual reset required) */
    bool fire_latched;         /**< Fire latch (manual reset required) */
    bool flood_latched;        /**< Flood latch (manual reset required) */
} safety_state_t;

/* ---------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------*/

/**
 * @brief Evaluate raw DI values and update safety state.
 *
 * Applies active_low inversion per pin from config. Detects E-Stop
 * dual-channel, fire dual-confirm, flood, ACDB loss, door open.
 * Sets latched flags when appropriate. Auto-recovering states are
 * updated directly from current input values.
 *
 * @param di_raw   Raw DI values from GPIO read (8 bytes).
 * @param config   GPIO configuration with active_low flags.
 * @param state    Safety state to update (in/out -- preserves latches).
 */
void evaluate_inputs(const uint8_t *di_raw,
                     const gpio_config_t *config,
                     safety_state_t *state);

/**
 * @brief Compute DO bitmask from current safety state.
 *
 * Implements the response matrix table from CONTEXT.md.
 * Running lamp (DO-3) ON only when no protective outputs active.
 *
 * @param state  Current safety state.
 * @return DO bitmask (bit N = 1 means assert DO-N).
 */
uint8_t evaluate_response_matrix(const safety_state_t *state);

/**
 * @brief Attempt to reset latched safety states.
 *
 * Validates that triggering inputs have actually cleared before
 * accepting the reset. Each latch is checked independently.
 *
 * @param state   Safety state to reset (in/out).
 * @param di_raw  Current raw DI values for validation.
 * @param config  GPIO config for active_low inversion.
 * @return 0 on success (all eligible latches cleared),
 *         -1 if any triggering input is still active (reject reset).
 */
int safety_reset(safety_state_t *state,
                 const uint8_t *di_raw,
                 const gpio_config_t *config);

#endif /* SAFETY_RESPONSE_MATRIX_H */
