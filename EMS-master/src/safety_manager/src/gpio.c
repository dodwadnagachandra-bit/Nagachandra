/**
 * @file gpio.c
 * @brief GPIO abstraction implementations: libgpiod v2 (real) + RTDB (test).
 *
 * The libgpiod backend uses the v2 API exclusively (gpiod_chip_open,
 * gpiod_line_settings_new, gpiod_line_config_new, gpiod_chip_request_lines,
 * gpiod_line_request_get_values/set_values). It is compiled only when
 * HAVE_LIBGPIOD is defined (production builds with libgpiod-dev installed).
 *
 * The RTDB backend reads DI from rtdb->gpio.di[] and writes DO to
 * rtdb->gpio.do_state[], enabling hardware-free testing via the GPIO
 * test harness.
 */

#include "gpio.h"

#include <stdio.h>
#include <string.h>

/* =========================================================================
 * libgpiod v2 backend (production)
 * ========================================================================= */

#ifdef HAVE_LIBGPIOD

#include <gpiod.h>

static struct gpiod_chip         *lg_chip       = NULL;
static struct gpiod_line_request *lg_di_request = NULL;
static struct gpiod_line_request *lg_do_request = NULL;
static size_t                     lg_num_di     = 0;
static size_t                     lg_num_do     = 0;

/**
 * Open the GPIO chip, request DI lines as input and DO lines as output.
 * Consumer string: "ems-safety-manager".
 */
static int libgpiod_init(const char *chip_path,
                          const unsigned int *di_offsets, size_t num_di,
                          const unsigned int *do_offsets, size_t num_do)
{
    /* Open chip */
    lg_chip = gpiod_chip_open(chip_path);
    if (!lg_chip)
    {
        fprintf(stderr, "[safety_manager] gpiod_chip_open(%s) failed\n",
                chip_path);
        return -1;
    }

    /* --- DI lines (input) --- */
    struct gpiod_line_settings *di_settings = gpiod_line_settings_new();
    if (!di_settings)
    {
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_line_settings_set_direction(di_settings, GPIOD_LINE_DIRECTION_INPUT);

    struct gpiod_line_config *di_config = gpiod_line_config_new();
    if (!di_config)
    {
        gpiod_line_settings_free(di_settings);
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_line_config_add_line_settings(di_config, di_offsets, num_di,
                                        di_settings);

    struct gpiod_request_config *req_cfg = gpiod_request_config_new();
    if (!req_cfg)
    {
        gpiod_line_config_free(di_config);
        gpiod_line_settings_free(di_settings);
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_request_config_set_consumer(req_cfg, "ems-safety-manager");

    lg_di_request = gpiod_chip_request_lines(lg_chip, req_cfg, di_config);

    gpiod_request_config_free(req_cfg);
    gpiod_line_config_free(di_config);
    gpiod_line_settings_free(di_settings);

    if (!lg_di_request)
    {
        fprintf(stderr, "[safety_manager] DI line request failed\n");
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }

    /* --- DO lines (output, initial low) --- */
    struct gpiod_line_settings *do_settings = gpiod_line_settings_new();
    if (!do_settings)
    {
        gpiod_line_request_release(lg_di_request);
        lg_di_request = NULL;
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_line_settings_set_direction(do_settings, GPIOD_LINE_DIRECTION_OUTPUT);
    gpiod_line_settings_set_output_value(do_settings,
                                         GPIOD_LINE_VALUE_INACTIVE);

    struct gpiod_line_config *do_config = gpiod_line_config_new();
    if (!do_config)
    {
        gpiod_line_settings_free(do_settings);
        gpiod_line_request_release(lg_di_request);
        lg_di_request = NULL;
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_line_config_add_line_settings(do_config, do_offsets, num_do,
                                        do_settings);

    req_cfg = gpiod_request_config_new();
    if (!req_cfg)
    {
        gpiod_line_config_free(do_config);
        gpiod_line_settings_free(do_settings);
        gpiod_line_request_release(lg_di_request);
        lg_di_request = NULL;
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }
    gpiod_request_config_set_consumer(req_cfg, "ems-safety-manager");

    lg_do_request = gpiod_chip_request_lines(lg_chip, req_cfg, do_config);

    gpiod_request_config_free(req_cfg);
    gpiod_line_config_free(do_config);
    gpiod_line_settings_free(do_settings);

    if (!lg_do_request)
    {
        fprintf(stderr, "[safety_manager] DO line request failed\n");
        gpiod_line_request_release(lg_di_request);
        lg_di_request = NULL;
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
        return -1;
    }

    lg_num_di = num_di;
    lg_num_do = num_do;

    return 0;
}

/**
 * Read all DI values. Returns raw GPIO values (no active_low inversion).
 */
static int libgpiod_read_di(uint8_t *values, size_t count)
{
    if (!lg_di_request)
    {
        return -1;
    }

    /* libgpiod v2 uses enum gpiod_line_value per line */
    enum gpiod_line_value gpiod_vals[GPIO_NUM_DI];
    int rc = gpiod_line_request_get_values(lg_di_request, gpiod_vals);
    if (rc < 0)
    {
        fprintf(stderr, "[safety_manager] DI read failed\n");
        return -1;
    }

    size_t n = (count < lg_num_di) ? count : lg_num_di;
    for (size_t i = 0; i < n; i++)
    {
        values[i] = (gpiod_vals[i] == GPIOD_LINE_VALUE_ACTIVE) ? 1 : 0;
    }

    return 0;
}

/**
 * Write individual DO pins. If a single pin write fails, log error but
 * continue writing other pins. Returns count of failed writes.
 */
static int libgpiod_write_do(const uint8_t *values, size_t count)
{
    if (!lg_do_request)
    {
        return (int)count; /* All failed -- no request */
    }

    enum gpiod_line_value gpiod_vals[GPIO_NUM_DO];
    size_t n = (count < lg_num_do) ? count : lg_num_do;

    for (size_t i = 0; i < n; i++)
    {
        gpiod_vals[i] = values[i] ? GPIOD_LINE_VALUE_ACTIVE
                                  : GPIOD_LINE_VALUE_INACTIVE;
    }

    int rc = gpiod_line_request_set_values(lg_do_request, gpiod_vals);
    if (rc < 0)
    {
        fprintf(stderr, "[safety_manager] DO write failed\n");
        return 1;
    }

    return 0;
}

/**
 * Release line requests and close the chip.
 */
static void libgpiod_close(void)
{
    if (lg_do_request)
    {
        gpiod_line_request_release(lg_do_request);
        lg_do_request = NULL;
    }
    if (lg_di_request)
    {
        gpiod_line_request_release(lg_di_request);
        lg_di_request = NULL;
    }
    if (lg_chip)
    {
        gpiod_chip_close(lg_chip);
        lg_chip = NULL;
    }
    lg_num_di = 0;
    lg_num_do = 0;
}

const gpio_ops_t gpio_ops_libgpiod = {
    .init     = libgpiod_init,
    .read_di  = libgpiod_read_di,
    .write_do = libgpiod_write_do,
    .close    = libgpiod_close,
};

#endif /* HAVE_LIBGPIOD */

/* =========================================================================
 * RTDB test backend
 * ========================================================================= */

/** Pointer to RTDB shared memory, set by rtdb_backend_init(). */
static ems_rtdb_t *rtdb_ptr = NULL;

void rtdb_backend_init(ems_rtdb_t *rtdb)
{
    rtdb_ptr = rtdb;
}

/**
 * RTDB backend init -- chip_path and offsets are ignored.
 * Validates that rtdb_backend_init() was called first.
 */
static int rtdb_init(const char *chip_path,
                     const unsigned int *di_offsets, size_t num_di,
                     const unsigned int *do_offsets, size_t num_do)
{
    (void)chip_path;
    (void)di_offsets;
    (void)num_di;
    (void)do_offsets;
    (void)num_do;

    if (!rtdb_ptr)
    {
        fprintf(stderr,
                "[safety_manager] RTDB backend: rtdb_backend_init() "
                "not called\n");
        return -1;
    }

    return 0;
}

/**
 * Read DI values from RTDB gpio.di[] array.
 * Returns raw values -- no active_low inversion.
 */
static int rtdb_read_di(uint8_t *values, size_t count)
{
    if (!rtdb_ptr)
    {
        return -1;
    }

    size_t n = (count < GPIO_NUM_DI) ? count : GPIO_NUM_DI;
    for (size_t i = 0; i < n; i++)
    {
        values[i] = rtdb_ptr->gpio.di[i];
    }

    return 0;
}

/**
 * Write DO values to RTDB gpio.do_state[] array.
 * Always succeeds (memory write). Returns 0.
 */
static int rtdb_write_do(const uint8_t *values, size_t count)
{
    if (!rtdb_ptr)
    {
        return (int)count;
    }

    size_t n = (count < GPIO_NUM_DO) ? count : GPIO_NUM_DO;
    for (size_t i = 0; i < n; i++)
    {
        rtdb_ptr->gpio.do_state[i] = values[i];
    }

    return 0;
}

/**
 * RTDB backend close -- no resources to release.
 */
static void rtdb_close(void)
{
    /* RTDB pointer is not owned by this backend */
    rtdb_ptr = NULL;
}

const gpio_ops_t gpio_ops_rtdb = {
    .init     = rtdb_init,
    .read_di  = rtdb_read_di,
    .write_do = rtdb_write_do,
    .close    = rtdb_close,
};
