#ifndef EMS_TYPES_H
#define EMS_TYPES_H

/**
 * @file ems_types.h
 * @brief EMS shared type definitions, enumerations, and version macro.
 */

#include <stdint.h>

#define EMS_VERSION "0.1.0"

/** Control manager states (1 Hz state machine). */
typedef enum
{
    EMS_STATE_INIT = 0,
    EMS_STATE_IDLE,
    EMS_STATE_STANDBY,
    EMS_STATE_CHARGING,
    EMS_STATE_DISCHARGING,
    EMS_STATE_FAULT,
    EMS_STATE_EMERGENCY,
    EMS_STATE_MAINTENANCE,
    EMS_STATE_COUNT
} ems_control_state_t;

/** PCS inverter operational states. */
typedef enum
{
    PCS_STATE_OFF = 0,
    PCS_STATE_STANDBY,
    PCS_STATE_RUNNING,
    PCS_STATE_FAULT,
    PCS_STATE_COUNT
} ems_pcs_state_t;

/** Source priority modes. */
typedef enum
{
    SRC_PRIORITY_DAY = 0,
    SRC_PRIORITY_NIGHT,
    SRC_PRIORITY_MANUAL,
    SRC_PRIORITY_COUNT
} ems_source_priority_t;

/** Event severity levels (IEC 62682 aligned). */
typedef enum
{
    EMS_SEVERITY_INFO = 0,
    EMS_SEVERITY_WARNING,
    EMS_SEVERITY_ERROR,
    EMS_SEVERITY_CRITICAL,
    EMS_SEVERITY_COUNT
} ems_severity_t;

#endif /* EMS_TYPES_H */
