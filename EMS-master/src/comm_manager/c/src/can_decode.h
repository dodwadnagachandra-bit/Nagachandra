#ifndef EMS_CAN_DECODE_H
#define EMS_CAN_DECODE_H

/**
 * @file can_decode.h
 * @brief CAN DBC decode functions — pure computation, no SocketCAN I/O.
 *
 * Decodes raw CAN frame bytes into RTDB rack struct fields using the
 * BMS Layer 2 DBC protocol. All signals are little-endian (Intel byte order).
 *
 * CAN ID formula: base_id + cluster * 0x1000 + rack * 0x10 + msg_offset
 * Message offsets 0x00-0x09 map to the 10 DBC message types.
 */

#include <stdbool.h>
#include <stdint.h>

#include "rtdb.h"

/* ---------------------------------------------------------------------------
 * CAN ID masking
 * -------------------------------------------------------------------------*/

/** Mask for 29-bit extended CAN ID (strips EFF/RTR/ERR flags). */
#define CAN_29BIT_MASK  0x1FFFFFFFU

/* ---------------------------------------------------------------------------
 * Message offset enum — maps to DBC message types
 * -------------------------------------------------------------------------*/

typedef enum
{
    CAN_MSG_PACK_SUMMARY     = 0,
    CAN_MSG_CELL_VOLTAGE_01  = 1,
    CAN_MSG_CELL_VOLTAGE_02  = 2,
    CAN_MSG_CELL_VOLTAGE_03  = 3,
    CAN_MSG_CELL_VOLTAGE_04  = 4,
    CAN_MSG_CELL_VOLTAGE_05  = 5,
    CAN_MSG_CELL_VOLTAGE_06  = 6,
    CAN_MSG_CELL_VOLTAGE_07  = 7,
    CAN_MSG_CELL_TEMPERATURE = 8,
    CAN_MSG_RACK_STATUS      = 9,
    CAN_MSG_COUNT            = 10
} ems_can_msg_type_t;

/* ---------------------------------------------------------------------------
 * Function declarations
 * -------------------------------------------------------------------------*/

/**
 * Decompose a raw CAN ID into cluster, rack, and message offset.
 *
 * Applies CAN_29BIT_MASK (strips EFF flag 0x80000000), then computes:
 *   offset_from_base = (masked_id - base_id)
 *   cluster = offset_from_base / 0x1000
 *   rack    = (offset_from_base % 0x1000) / 0x10
 *   msg_offset = offset_from_base % 0x10
 */
void can_decode_id(uint32_t raw_can_id, uint32_t base_id,
                   int *cluster, int *rack, int *msg_offset);

/**
 * Dispatch a CAN data frame to the appropriate per-message decoder.
 *
 * @param msg_offset Message offset 0-9 (from can_decode_id).
 * @param data       8-byte CAN data payload.
 * @param rack       Target RTDB rack struct to write decoded values.
 */
void can_decode_frame(int msg_offset, const uint8_t data[8], ems_rack_t *rack);

/** Decode PackSummary (msg_offset 0): pack_v, pack_i, pack_soc, pack_soh, fault_code. */
void can_decode_pack_summary(const uint8_t data[8], ems_rack_t *rack);

/** Decode CellVoltage (msg_offset 1-7): 4 cells per message, slot 0-6. */
void can_decode_cell_voltage(int slot, const uint8_t data[8], ems_rack_t *rack);

/** Decode CellTemperature (msg_offset 8): 8 temperatures with offset -40. */
void can_decode_cell_temperature(const uint8_t data[8], ems_rack_t *rack);

/** Decode RackStatus (msg_offset 9): online, min/max/avg cell_v. */
void can_decode_rack_status(const uint8_t data[8], ems_rack_t *rack);

/**
 * Handle a CAN error frame — update per-interface health struct.
 *
 * @param can_id CAN error frame ID (contains error class flags).
 * @param data   8-byte error frame data (TEC in data[6], REC in data[7]).
 * @param health Target CAN health struct in RTDB.
 */
void can_handle_error_frame(uint32_t can_id, const uint8_t data[8],
                            ems_can_health_t *health);

#endif /* EMS_CAN_DECODE_H */
