/**
 * @file can_decode.c
 * @brief CAN DBC decode functions — manual decode for BMS Layer 2 protocol.
 *
 * Implements decode for all 10 DBC message types (PackSummary, CellVoltage_01-07,
 * CellTemperature, RackStatus) plus CAN error frame handling.
 *
 * All signals are little-endian (Intel byte order) per the DBC specification.
 * This file contains pure computation only — no SocketCAN I/O.
 */

#include "can_decode.h"

#include <linux/can.h>
#include <linux/can/error.h>
#include <string.h>

/* ---------------------------------------------------------------------------
 * Helper: extract little-endian values from CAN data bytes
 * -------------------------------------------------------------------------*/

/** Extract unsigned 16-bit little-endian value from data at byte offset. */
static inline uint16_t le_u16(const uint8_t *data, int offset)
{
    return (uint16_t)((uint16_t)data[offset] | ((uint16_t)data[offset + 1] << 8));
}

/** Extract signed 16-bit little-endian value from data at byte offset. */
static inline int16_t le_s16(const uint8_t *data, int offset)
{
    uint16_t raw = le_u16(data, offset);
    return (int16_t)raw;
}

/* ---------------------------------------------------------------------------
 * CAN ID decomposition
 * -------------------------------------------------------------------------*/

void can_decode_id(uint32_t raw_can_id, uint32_t base_id,
                   int *cluster, int *rack, int *msg_offset)
{
    /* Strip EFF/RTR/ERR flags to get 29-bit CAN ID */
    uint32_t masked = raw_can_id & CAN_29BIT_MASK;

    /* Compute offset from base ID */
    uint32_t offset_from_base = masked - base_id;

    /* Decompose: cluster * 0x1000 + rack * 0x10 + msg_offset */
    *cluster    = (int)(offset_from_base / 0x1000U);
    *rack       = (int)((offset_from_base % 0x1000U) / 0x10U);
    *msg_offset = (int)(offset_from_base % 0x10U);
}

/* ---------------------------------------------------------------------------
 * PackSummary (msg_offset 0)
 * -------------------------------------------------------------------------*/

void can_decode_pack_summary(const uint8_t data[8], ems_rack_t *rack)
{
    /* pack_v: u16 at [0:2] * 0.1 V */
    rack->pack_v = (float)le_u16(data, 0) * 0.1f;

    /* pack_i: s16 at [2:4] * 0.1 A */
    rack->pack_i = (float)le_s16(data, 2) * 0.1f;

    /* pack_soc: u8 at [4] * 0.5 % */
    rack->pack_soc = (float)data[4] * 0.5f;

    /* pack_soh: u8 at [5] * 0.5 % */
    rack->pack_soh = (float)data[5] * 0.5f;

    /* fault_code: u16 at [6:8] */
    rack->fault_code = le_u16(data, 6);
}

/* ---------------------------------------------------------------------------
 * CellVoltage (msg_offset 1-7, slot 0-6)
 * -------------------------------------------------------------------------*/

void can_decode_cell_voltage(int slot, const uint8_t data[8], ems_rack_t *rack)
{
    /* 4 cells per message, cell indices = slot * 4 + 0..3
     * All cells go into module 0 (flat cell array within first module).
     * With 28 cells max across 7 messages, module_idx stays 0. */
    int cell_base = slot * 4;

    for (int i = 0; i < 4; i++)
    {
        /* u16 at [i*2 : i*2+2] * 0.001 V */
        rack->modules[0].cell_v[cell_base + i] =
            (float)le_u16(data, i * 2) * 0.001f;
    }
}

/* ---------------------------------------------------------------------------
 * CellTemperature (msg_offset 8)
 * -------------------------------------------------------------------------*/

void can_decode_cell_temperature(const uint8_t data[8], ems_rack_t *rack)
{
    /* 8 temperatures, each u8 with offset -40 applied */
    for (int i = 0; i < 8; i++)
    {
        rack->modules[0].cell_t[i] = (float)data[i] - 40.0f;
    }
}

/* ---------------------------------------------------------------------------
 * RackStatus (msg_offset 9)
 * -------------------------------------------------------------------------*/

void can_decode_rack_status(const uint8_t data[8], ems_rack_t *rack)
{
    /* online: u8 at [0] */
    rack->online = data[0];

    /* balancing_count: u8 at [1] — not stored in ems_rack_t (informational) */

    /* min_cell_v: u16 at [2:4] * 0.001 V */
    rack->min_cell_v = (float)le_u16(data, 2) * 0.001f;

    /* max_cell_v: u16 at [4:6] * 0.001 V */
    rack->max_cell_v = (float)le_u16(data, 4) * 0.001f;

    /* avg_cell_v: u16 at [6:8] * 0.001 V */
    rack->avg_cell_v = (float)le_u16(data, 6) * 0.001f;
}

/* ---------------------------------------------------------------------------
 * Frame dispatch
 * -------------------------------------------------------------------------*/

void can_decode_frame(int msg_offset, const uint8_t data[8], ems_rack_t *rack)
{
    switch (msg_offset)
    {
    case CAN_MSG_PACK_SUMMARY:
        can_decode_pack_summary(data, rack);
        break;
    case CAN_MSG_CELL_VOLTAGE_01:
    case CAN_MSG_CELL_VOLTAGE_02:
    case CAN_MSG_CELL_VOLTAGE_03:
    case CAN_MSG_CELL_VOLTAGE_04:
    case CAN_MSG_CELL_VOLTAGE_05:
    case CAN_MSG_CELL_VOLTAGE_06:
    case CAN_MSG_CELL_VOLTAGE_07:
        can_decode_cell_voltage(msg_offset - CAN_MSG_CELL_VOLTAGE_01, data, rack);
        break;
    case CAN_MSG_CELL_TEMPERATURE:
        can_decode_cell_temperature(data, rack);
        break;
    case CAN_MSG_RACK_STATUS:
        can_decode_rack_status(data, rack);
        break;
    default:
        /* Unknown message offset — ignore silently */
        break;
    }
}

/* ---------------------------------------------------------------------------
 * Error frame handling
 * -------------------------------------------------------------------------*/

void can_handle_error_frame(uint32_t can_id, const uint8_t data[8],
                            ems_can_health_t *health)
{
    /* Always extract TEC and REC from data[6] and data[7] */
    health->tx_error_count = data[6];
    health->rx_error_count = data[7];

    /* Determine bus state from error class flags in can_id */
    if (can_id & CAN_ERR_BUSOFF)
    {
        health->bus_state = CAN_BUS_OFF;
    }
    else if (can_id & CAN_ERR_CRTL)
    {
        /* Check controller error detail in data[1] */
        uint8_t ctrl = data[1];
        if ((ctrl & CAN_ERR_CRTL_TX_PASSIVE) ||
            (ctrl & CAN_ERR_CRTL_RX_PASSIVE))
        {
            health->bus_state = CAN_BUS_ERROR_PASSIVE;
        }
        else if ((ctrl & CAN_ERR_CRTL_TX_WARNING) ||
                 (ctrl & CAN_ERR_CRTL_RX_WARNING))
        {
            health->bus_state = CAN_BUS_ERROR_WARNING;
        }
    }
}
