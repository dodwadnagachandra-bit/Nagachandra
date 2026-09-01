#ifndef SAFETY_GPIO_H
#define SAFETY_GPIO_H

/**
 * @file gpio.h
 * @brief GPIO abstraction layer with vtable for testability.
 *
 * Provides a function pointer table (gpio_ops_t) so the safety_manager
 * can use either the real libgpiod v2 backend (production) or the RTDB
 * test backend (CI/development without GPIO hardware).
 *
 * Active-low inversion is NOT applied in this layer -- raw values are
 * returned. The response_matrix module handles inversion using the
 * gpio_config_t configuration.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "rtdb.h"

/* ---------------------------------------------------------------------------
 * GPIO configuration (loaded from gpio_config.yaml at startup)
 * -------------------------------------------------------------------------*/

#define GPIO_NUM_DI 8
#define GPIO_NUM_DO 8

/**
 * @brief Per-pin active_low configuration from gpio_config.yaml.
 *
 * Active-low inversion is applied in the response matrix evaluate_inputs()
 * function, not in the GPIO read/write layer.
 */
typedef struct
{
    bool active_low_di[GPIO_NUM_DI]; /**< Per-DI active_low flags */
    bool active_low_do[GPIO_NUM_DO]; /**< Per-DO active_low flags */
} gpio_config_t;

/* ---------------------------------------------------------------------------
 * GPIO operations vtable
 * -------------------------------------------------------------------------*/

/**
 * @brief GPIO hardware abstraction vtable.
 *
 * Two implementations:
 *   - gpio_ops_libgpiod: Real hardware via libgpiod v2 (production)
 *   - gpio_ops_rtdb:     RTDB shared memory backend (testing)
 */
typedef struct
{
    /**
     * @brief Initialize the GPIO backend.
     * @param chip_path    Path to GPIO chip (e.g., "/dev/gpiochip0").
     *                     Ignored by RTDB backend.
     * @param di_offsets   Array of DI line offsets on the chip.
     * @param num_di       Number of DI lines.
     * @param do_offsets   Array of DO line offsets on the chip.
     * @param num_do       Number of DO lines.
     * @return 0 on success, -1 on failure (caller handles fail-safe).
     */
    int (*init)(const char *chip_path,
                const unsigned int *di_offsets, size_t num_di,
                const unsigned int *do_offsets, size_t num_do);

    /**
     * @brief Read all digital inputs (raw values, no active_low inversion).
     * @param values  Output buffer for DI values (0 or 1 each).
     * @param count   Number of DI values to read.
     * @return 0 on success, -1 on read failure.
     */
    int (*read_di)(uint8_t *values, size_t count);

    /**
     * @brief Write all digital outputs.
     *
     * If a single pin write fails, logs error but continues writing
     * remaining pins (DO write failures never block other outputs).
     *
     * @param values  Array of DO values to write (0 or 1 each).
     * @param count   Number of DO values to write.
     * @return Number of failed writes (0 = all succeeded).
     */
    int (*write_do)(const uint8_t *values, size_t count);

    /**
     * @brief Close the GPIO backend and release resources.
     */
    void (*close)(void);
} gpio_ops_t;

/* ---------------------------------------------------------------------------
 * Backend declarations
 * -------------------------------------------------------------------------*/

#ifdef HAVE_LIBGPIOD
/** Real GPIO backend using libgpiod v2 API. */
extern const gpio_ops_t gpio_ops_libgpiod;
#endif

/** Test GPIO backend using RTDB shared memory. */
extern const gpio_ops_t gpio_ops_rtdb;

/**
 * @brief Initialize the RTDB test backend with a pointer to the RTDB.
 * @param rtdb  Pointer to the mapped RTDB shared memory.
 *
 * Must be called before gpio_ops_rtdb.init(). The RTDB backend reads
 * DI from rtdb->gpio.di[] and writes DO to rtdb->gpio.do_state[].
 */
void rtdb_backend_init(ems_rtdb_t *rtdb);

#endif /* SAFETY_GPIO_H */
