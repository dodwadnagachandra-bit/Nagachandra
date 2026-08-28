/**
 * @file response_matrix.c
 * @brief Safety response matrix: DI-to-DO mapping logic.
 *
 * Implements the locked safety response matrix from CONTEXT.md.
 * No dynamic allocation. No heap usage. All buffers on stack.
 *
 * DI pin assignments (from gpio_config.yaml):
 *   DI-0: ACDB_FEEDBACK (active_high)    DI-4: HEAT_DETECTOR (active_high)
 *   DI-1: FLOOD_SENSOR  (active_high)    DI-5: SPARE         (active_high)
 *   DI-2: DOOR_SWITCH   (active_low)     DI-6: ESTOP_NO      (active_high)
 *   DI-3: SMOKE_DETECTOR(active_high)    DI-7: ESTOP_NC      (active_low)
 *
 * DO pin assignments:
 *   DO-0: ACDB_TRIP      DO-4: FAULT_LAMP
 *   DO-1: EXTINGUISHER   DO-5: PCS_STOP
 *   DO-2: WARNING_LAMP   DO-6: SIREN
 *   DO-3: RUNNING_LAMP   DO-7: SPARE (never asserted)
 */

#include "response_matrix.h"

#include <stdio.h>

/* DI index constants for readability */
#define DI_ACDB_FEEDBACK  0
#define DI_FLOOD_SENSOR   1
#define DI_DOOR_SWITCH    2
#define DI_SMOKE_DETECTOR 3
#define DI_HEAT_DETECTOR  4
#define DI_SPARE          5
#define DI_ESTOP_NO       6
#define DI_ESTOP_NC       7

/**
 * Apply active_low inversion to a single raw DI value.
 * logical = active_low ? !raw : raw
 */
static inline bool apply_active_low(uint8_t raw, bool active_low)
{
    bool logical = (raw != 0);
    if (active_low)
    {
        logical = !logical;
    }
    return logical;
}

void evaluate_inputs(const uint8_t *di_raw,
                     const gpio_config_t *config,
                     safety_state_t *state)
{
    /* Apply active_low inversion to get logical values */
    bool di_logical[GPIO_NUM_DI];
    for (int i = 0; i < GPIO_NUM_DI; i++)
    {
        di_logical[i] = apply_active_low(di_raw[i], config->active_low_di[i]);
    }

    /* --- E-Stop dual-channel detection --- */
    /*
     * DI-6 (NO): logical high = E-Stop pressed
     * DI-7 (NC): active_low, so raw low -> logical high = E-Stop pressed
     *
     * Both channels must agree for a valid E-Stop.
     * If only one channel is active -> wiring discrepancy.
     */
    bool estop_ch1 = di_logical[DI_ESTOP_NO]; /* Channel 1: NO contact */
    bool estop_ch2 = di_logical[DI_ESTOP_NC]; /* Channel 2: NC contact */

    if (estop_ch1 && estop_ch2)
    {
        /* Both channels confirm: valid E-Stop */
        state->estop_active = true;
        state->estop_latched = true;
        state->estop_discrepancy = false;
    }
    else if (estop_ch1 != estop_ch2)
    {
        /* Single-channel discrepancy: wiring fault */
        state->estop_discrepancy = true;
        fprintf(stderr,
                "[safety_manager] CRITICAL: E-Stop channel discrepancy "
                "(DI-6=%d, DI-7=%d raw)\n",
                di_raw[DI_ESTOP_NO], di_raw[DI_ESTOP_NC]);
        /* Do NOT trigger E-Stop response for discrepancy */
        if (!state->estop_latched)
        {
            state->estop_active = false;
        }
    }
    else
    {
        /* Both channels normal (not pressed) */
        state->estop_discrepancy = false;
        /* estop_active stays true if latched */
        if (!state->estop_latched)
        {
            state->estop_active = false;
        }
    }

    /* --- Fire dual-confirm detection --- */
    /*
     * DI-3 (Smoke) AND DI-4 (Heat) both logical high = fire confirmed.
     * Single sensor alone = warning only (no extinguisher).
     */
    bool smoke = di_logical[DI_SMOKE_DETECTOR];
    bool heat  = di_logical[DI_HEAT_DETECTOR];

    if (smoke && heat)
    {
        /* Both sensors confirm: fire */
        state->fire_active = true;
        state->fire_latched = true;
        state->fire_single_sensor = false;
    }
    else if (smoke || heat)
    {
        /* Single sensor: warning only */
        state->fire_single_sensor = true;
        if (!state->fire_latched)
        {
            state->fire_active = false;
        }
    }
    else
    {
        /* No fire sensors active */
        state->fire_single_sensor = false;
        if (!state->fire_latched)
        {
            state->fire_active = false;
        }
    }

    /* --- Flood detection --- */
    /* DI-1 logical high = flood detected */
    if (di_logical[DI_FLOOD_SENSOR])
    {
        state->flood_active = true;
        state->flood_latched = true;
    }
    else
    {
        if (!state->flood_latched)
        {
            state->flood_active = false;
        }
    }

    /* --- ACDB feedback loss --- */
    /* DI-0 logical LOW = loss of signal (contactor open) */
    /* Auto-recovers when DI-0 goes high */
    state->acdb_loss = !di_logical[DI_ACDB_FEEDBACK];

    /* --- Door open --- */
    /* DI-2 active_low: raw low = door open -> logical high after inversion */
    /* Auto-recovers when door closes */
    state->door_open = di_logical[DI_DOOR_SWITCH];

    /* --- Spare DI-5 --- */
    /* Read and store, trigger nothing */
    (void)di_logical[DI_SPARE];
}

uint8_t evaluate_response_matrix(const safety_state_t *state)
{
    uint8_t outputs = 0;

    /* E-Stop: ACDB trip + Fault + PCS stop + Siren */
    if (state->estop_active)
    {
        outputs |= DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }

    /* Fire: ACDB trip + Extinguisher + Fault + PCS stop + Siren */
    if (state->fire_active)
    {
        outputs |= DO_ACDB_TRIP | DO_EXTINGUISHER | DO_FAULT_LAMP
                   | DO_PCS_STOP | DO_SIREN;
    }

    /* Flood: ACDB trip + Fault + PCS stop + Siren */
    if (state->flood_active)
    {
        outputs |= DO_ACDB_TRIP | DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }

    /* ACDB feedback loss: Fault + PCS stop + Siren (do NOT re-trip ACDB) */
    if (state->acdb_loss)
    {
        outputs |= DO_FAULT_LAMP | DO_PCS_STOP | DO_SIREN;
    }

    /* Door open: Warning lamp only */
    if (state->door_open)
    {
        outputs |= DO_WARNING_LAMP;
    }

    /* Single fire sensor: Warning lamp only (no extinguisher) */
    if (state->fire_single_sensor)
    {
        outputs |= DO_WARNING_LAMP;
    }

    /* E-Stop discrepancy: Warning lamp (wiring fault indication) */
    if (state->estop_discrepancy)
    {
        outputs |= DO_WARNING_LAMP;
    }

    /* GPIO failure: assert ALL safety outputs (worst case) */
    if (state->gpio_failure)
    {
        outputs |= DO_ACDB_TRIP | DO_EXTINGUISHER | DO_FAULT_LAMP
                   | DO_PCS_STOP | DO_SIREN;
    }

    /*
     * Running lamp (DO-3): ON only when no protective outputs active.
     * IEC 60073: green + red simultaneously is prohibited.
     * Fault lamp follows "any protective output" rule.
     */
    if (!(outputs & PROTECTIVE_OUTPUTS))
    {
        outputs |= DO_RUNNING_LAMP;
    }

    return outputs;
}

int safety_reset(safety_state_t *state,
                 const uint8_t *di_raw,
                 const gpio_config_t *config)
{
    /* Apply active_low inversion to get current logical values */
    bool di_logical[GPIO_NUM_DI];
    for (int i = 0; i < GPIO_NUM_DI; i++)
    {
        di_logical[i] = apply_active_low(di_raw[i], config->active_low_di[i]);
    }

    int rejected = 0;

    /* E-Stop reset: only if DI-6 and DI-7 both in normal state */
    if (state->estop_latched)
    {
        bool ch1_normal = !di_logical[DI_ESTOP_NO];
        bool ch2_normal = !di_logical[DI_ESTOP_NC];

        if (ch1_normal && ch2_normal)
        {
            state->estop_latched = false;
            state->estop_active = false;
        }
        else
        {
            rejected = 1;
        }
    }

    /* Fire reset: only if DI-3 and DI-4 both inactive */
    if (state->fire_latched)
    {
        if (!di_logical[DI_SMOKE_DETECTOR] && !di_logical[DI_HEAT_DETECTOR])
        {
            state->fire_latched = false;
            state->fire_active = false;
        }
        else
        {
            rejected = 1;
        }
    }

    /* Flood reset: only if DI-1 inactive */
    if (state->flood_latched)
    {
        if (!di_logical[DI_FLOOD_SENSOR])
        {
            state->flood_latched = false;
            state->flood_active = false;
        }
        else
        {
            rejected = 1;
        }
    }

    return rejected ? -1 : 0;
}
