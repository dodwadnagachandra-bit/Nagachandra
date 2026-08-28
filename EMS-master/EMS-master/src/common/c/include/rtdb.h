#ifndef EMS_RTDB_H
#define EMS_RTDB_H

/**
 * @file rtdb.h
 * @brief Hierarchical RTDB shared memory struct definitions.
 *
 * RTDB Memory Layout — compile-time max arrays
 *
 * The struct is always allocated at max topology size. The topology count
 * fields (cluster_count, racks_per_cluster, etc.) tell readers how many
 * entries in each array are actually valid.
 *
 * Size at max topology (8x16x20x108cells x40temps): ~1.8 MB
 * Size for residential  (1x4x8x16cells x8temps):    ~4 KB valid data
 * Size for container     (4x16x20x108cells x40temps): ~900 KB valid data
 *
 * 1.8 MB is trivial on the 4 GB DDR4 ECU-1170-552A.
 */

#include <stdbool.h>
#include <stdint.h>

#include "ems_types.h"
#include "seqlock.h"

/* ---------------------------------------------------------------------------
 * Constants — derived from config/schemas/system_config.schema.json maximums
 * -------------------------------------------------------------------------*/

#define RTDB_VERSION          1
#define RTDB_MAGIC            0x454D5352  /* "EMSR" in ASCII */

#define MAX_CLUSTERS          8
#define MAX_RACKS_PER_CLUSTER 16
#define MAX_MODULES_PER_RACK  20
#define MAX_CELLS_PER_MODULE  108
#define MAX_TEMPS_PER_MODULE  40
#define MAX_CAN_INTERFACES    2

/* ---------------------------------------------------------------------------
 * Struct hierarchy (bottom-up)
 * -------------------------------------------------------------------------*/

/** Battery module (LMU) — leaf node with per-cell data. */
typedef struct
{
    float   cell_v[MAX_CELLS_PER_MODULE];     /* V   — individual cell voltages   */
    float   cell_t[MAX_TEMPS_PER_MODULE];     /* degC — temperature sensor readings */
    uint8_t balancing[MAX_CELLS_PER_MODULE];  /* 0/1 — per-cell balancing active   */
} ems_module_t;

/** Battery rack (BMU) — one rack with module array and pre-aggregated data. */
typedef struct
{
    ems_seqlock_t lock;                              /* seqlock for this rack      */
    uint64_t      last_update_ms;                    /* ms — CLOCK_MONOTONIC       */
    ems_module_t  modules[MAX_MODULES_PER_RACK];     /* module array               */

    /* Pack-level aggregates (written by comm_manager after DBC decode) */
    float    pack_v;          /* V    — pack total voltage               */
    float    pack_i;          /* A    — pack current (positive=charge)   */
    float    pack_soc;        /* %    — state of charge                  */
    float    pack_soh;        /* %    — state of health                  */
    float    min_cell_v;      /* V    — minimum cell voltage in rack     */
    float    max_cell_v;      /* V    — maximum cell voltage in rack     */
    float    avg_cell_v;      /* V    — average cell voltage in rack     */
    float    min_cell_t;      /* degC — minimum cell temperature in rack */
    float    max_cell_t;      /* degC — maximum cell temperature in rack */
    float    avg_cell_t;      /* degC — average cell temperature in rack */

    float    max_cell_num;    /*      - indicates cell number with maximum voltage */
    float    min_cell_num;    /*      - indicates cell number with minimum voltage */
    float    full_cap_rem;    /* mAh  - Full capacity (Remaining)         */
    float    Delta_t;         /* degC - Difference between the temperature */
    float    Delta_v;         /* V    - Difference between the  voltages  */
    uint32_t fault_code;      /* BMS fault bitmap                        */
    uint8_t  online;          /* 0=offline, 1=online (comm status)       */

    
} ems_rack_t;

/** Battery cluster — group of racks on one CAN bus. */
typedef struct
{
    ems_rack_t racks[MAX_RACKS_PER_CLUSTER];  /* rack array */

    /* Cluster-level aggregates (same pattern as rack) */
    float cluster_v;       /* V    — cluster total voltage               */
    float cluster_i;       /* A    — cluster current                     */
    float cluster_soc;     /* %    — cluster state of charge             */
    float cluster_soh;     /* %    — cluster state of health             */
    float min_cell_v;      /* V    — minimum cell voltage in cluster     */
    float max_cell_v;      /* V    — maximum cell voltage in cluster     */
    float avg_cell_v;      /* V    — average cell voltage in cluster     */
    float min_cell_t;      /* degC — minimum cell temperature in cluster */
    float max_cell_t;      /* degC — maximum cell temperature in cluster */
    float avg_cell_t;      /* degC — average cell temperature in cluster */
} ems_cluster_t;

/** PCS inverter telemetry. */
typedef struct
{
    ems_seqlock_t    lock;             /* seqlock for PCS section */
    uint64_t         last_update_ms;   /* ms — CLOCK_MONOTONIC    */

    float            ac_voltage;       /* V    — AC side voltage       */
    float            ac_current;       /* A    — AC side current       */
    float            active_power;     /* kW   — active power          */
    float            reactive_power;   /* kVAR — reactive power        */
    float            dc_voltage;       /* V    — DC side voltage       */
    float            dc_current;       /* A    — DC side current       */
    float            frequency;        /* Hz   — grid frequency        */
    float            temperature;      /* degC — inverter temperature  */
    ems_pcs_state_t  state;            /* PCS operational state        */
    uint32_t         fault_code;       /* PCS fault bitmap             */
} ems_pcs_t;

/** Safety digital I/O. */
typedef struct
{
    ems_seqlock_t lock;             /* seqlock for GPIO section */
    uint64_t      last_update_ms;   /* ms — CLOCK_MONOTONIC     */

    uint8_t di[8];                  /* digital inputs  (0=open, 1=closed) */
    uint8_t do_state[8];           /* digital outputs (do is a C keyword) */
} ems_gpio_t;

/** Energy meter readings. */
typedef struct
{
    ems_seqlock_t lock;             /* seqlock for meter section */
    uint64_t      last_update_ms;   /* ms — CLOCK_MONOTONIC      */

    float voltage;                  /* V    — grid voltage        */
    float current;                  /* A    — grid current        */
    float active_power;             /* kW   — active power        */
    float reactive_power;           /* kVAR — reactive power      */
    float frequency;                /* Hz   — grid frequency      */
    float power_factor;             /* —    — power factor (0-1)  */
    float energy_import;            /* kWh  — total imported      */
    float energy_export;            /* kWh  — total exported      */
} ems_meter_t;

/** Battery thermal management system (per-system, not per-rack). */
typedef struct
{
    ems_seqlock_t lock;             /* seqlock for BTMS section */
    uint64_t      last_update_ms;   /* ms — CLOCK_MONOTONIC     */

    float   inlet_temp;             /* degC — coolant inlet temperature  */
    float   outlet_temp;            /* degC — coolant outlet temperature */
    float   fan_speed_pct;          /* %    — fan speed percentage       */
    uint8_t cooling_active;         /* 0=off, 1=active                   */
} ems_btms_t;

/**
 * PCS command enum values (stored in ems_system_t.pcs_command).
 *
 * control_manager writes the command and increments pcs_command_seq.
 * comm_manager detects the seq increment and executes the Modbus write.
 * 0=NONE (no pending command)
 * 1=ON   (PCS start command — FC06 reg 0x0291 = 1)
 * 2=OFF  (PCS stop command  — FC06 reg 0x0291 = 0)
 * 3=FAULT_RESET (PCS fault reset — FC06 reg 0x5064 = 1)
 */
#define PCS_CMD_NONE         0
#define PCS_CMD_ON           1
#define PCS_CMD_OFF          2
#define PCS_CMD_FAULT_RESET  3

/** System-level aggregates and control state. */
typedef struct
{
    ems_seqlock_t          lock;              /* seqlock for system section */
    uint64_t               last_update_ms;    /* ms — CLOCK_MONOTONIC       */

    ems_control_state_t    control_state;     /* EMS state machine state     */
    ems_source_priority_t  source_priority;   /* source priority mode        */
    float                  active_setpoint_kw; /* kW — active power setpoint */
    float                  total_soc;         /* %  — system-level SOC       */
    float                  total_power_kw;    /* kW — system total power     */
    float                  total_energy_kwh;  /* kWh — system total energy   */
    uint32_t               ems_uptime_s;      /* s  — EMS uptime             */

    /* PCS command path (control_manager → comm_manager via RTDB) */
    uint8_t                pcs_command;       /* PCS_CMD_* enum value        */
    uint8_t                _pad_cmd[3];       /* pad pcs_command_seq to 4-byte boundary */
    uint32_t               pcs_command_seq;   /* monotonic counter; comm_manager acts on increment */
    float                  active_derating_pct; /* % — active derating 0.0-100.0 (Phase 16) */
} ems_system_t;

/* ---------------------------------------------------------------------------
 * CAN bus health (per-interface)
 * -------------------------------------------------------------------------*/

/** CAN bus state — tracks error frame escalation. */
typedef enum
{
    CAN_BUS_ACTIVE         = 0,  /* Normal operation                     */
    CAN_BUS_ERROR_WARNING  = 1,  /* TEC or REC >= 96                     */
    CAN_BUS_ERROR_PASSIVE  = 2,  /* TEC or REC >= 128                    */
    CAN_BUS_OFF            = 3   /* TEC >= 256, bus offline               */
} ems_can_bus_state_t;

/** Per-CAN-interface health tracking — written by comm_manager_c. */
typedef struct
{
    ems_seqlock_t lock;               /* seqlock for this CAN health entry    */
    uint64_t      last_update_ms;     /* ms — CLOCK_MONOTONIC last good frame */
    uint32_t      bus_state;          /* ems_can_bus_state_t (uint32 storage)  */
    uint8_t       tx_error_count;     /* TEC from CAN error frame             */
    uint8_t       rx_error_count;     /* REC from CAN error frame             */
    uint8_t       _pad[2];           /* alignment to 8-byte boundary          */
    uint64_t      last_error_frame_ms; /* ms — CLOCK_MONOTONIC last error     */
} ems_can_health_t;

_Static_assert(sizeof(ems_can_health_t) == 32,
               "ems_can_health_t must be 32 bytes (check padding)");

/* ---------------------------------------------------------------------------
 * Top-level RTDB container
 * -------------------------------------------------------------------------*/

/** Top-level RTDB shared memory structure. */
typedef struct
{
    /* Header */
    uint32_t magic;               /* must be RTDB_MAGIC (0x454D5352)         */
    uint32_t version;             /* must be RTDB_VERSION                    */

    /* Topology counts — how many entries are valid in each array */
    uint8_t  cluster_count;       /* active clusters (from config)           */
    uint8_t  racks_per_cluster;   /* active racks per cluster (from config)  */
    uint8_t  modules_per_rack;    /* active modules per rack (from config)   */
    uint8_t  cells_per_module;    /* active cells per module (from config)   */
    uint8_t  temps_per_module;    /* active temps per module (from config)   */
    uint8_t  _pad[3];            /* alignment padding to 4-byte boundary    */

    /* BMS hierarchy */
    ems_cluster_t clusters[MAX_CLUSTERS];

    /* Non-BMS flat sections */
    ems_pcs_t    pcs;
    ems_gpio_t   gpio;
    ems_meter_t  meter;
    ems_btms_t   btms;
    ems_system_t system;

    /* CAN bus health — per-interface (appended to preserve existing offsets) */
    ems_can_health_t can_health[MAX_CAN_INTERFACES];
} ems_rtdb_t;

/* ---------------------------------------------------------------------------
 * Compile-time size assertions
 * -------------------------------------------------------------------------*/

_Static_assert(sizeof(ems_module_t) < 1024,
               "ems_module_t exceeds 1 KB — check field sizes");

_Static_assert(sizeof(ems_rtdb_t) < 2 * 1024 * 1024,
               "ems_rtdb_t exceeds 2 MB safety ceiling");

_Static_assert(RTDB_MAGIC == 0x454D5352,
               "RTDB_MAGIC value mismatch");

#endif /* EMS_RTDB_H */
