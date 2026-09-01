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


/**
 * Handle a CAN error frame — update per-interface health struct.
 *
 * @param can_id CAN error frame ID (contains error class flags).
 * @param data   8-byte error frame data (TEC in data[6], REC in data[7]).
 * @param health Target CAN health struct in RTDB.
 */


 void can_decoder ( uint32_t can_id ,const uint8_t data[8], ems_rack_t *rack);
 void can_handle_error_frame(uint32_t can_id, const uint8_t data[8],ems_can_health_t *health);


#endif /* EMS_CAN_DECODE_H */
