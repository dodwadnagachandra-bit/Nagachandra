#include <stdbool.h>
#include <stdint.h>
#include "rtdb.h"
#include "can_decode.h"
#include <linux/can.h>
#include <linux/can/error.h>
#include <string.h>
#include <stdio.h>

#define first_msg 0x201U

void can_decoder(uint32_t can_id, const uint8_t data[8], ems_rack_t *rack)
{

    switch (can_id)
    {
        case first_msg:
        {
            uint16_t raw_voltage = (data[0] << 8) | data[1];
            float voltage = raw_voltage * 0.001f;
            rack->pack_v = voltage;

            uint16_t raw_current = (data[2] << 8) | data[3];
            float current = raw_current * 1;
            rack->pack_i = current;

            uint16_t raw_soc = (data[4] << 8) | data[5];
            float soc = raw_soc * 1;
            rack->pack_soc = soc;

            printf("0x201: V=%.2f I=%.2f SOC=%.2f\n",
                   rack->pack_v,
                   rack->pack_i,
                   rack->pack_soc);

            break;
        }

        case 0x202U:
        {
            uint16_t raw_SOH = (data[0] << 8) | data[1];
            float SOH = raw_SOH * 1;
            rack->pack_soh = SOH;

            break;
        }

        case 0x204U:
        {
            uint16_t raw_Vmax = (data[0] << 8) | data[1];
            float Vmax = raw_Vmax * 0.001f;
            rack->max_cell_v = Vmax;

            uint16_t raw_Vmin = (data[2] << 8) | data[3];
            float Vmin = raw_Vmin * 0.001f;
            rack->min_cell_v = Vmin;

            float Vavg = (Vmax + Vmin) / 2;
            rack->avg_cell_v = Vavg;

            break;
        }

        case 0x205U:
        {
            uint16_t raw_Max_temp = (data[0] << 8) | data[1];
            float Max_temp = raw_Max_temp - 40;
            rack->max_cell_t = Max_temp;

            uint16_t raw_Min_temp = (data[2] << 8) | data[3];
            float Min_temp = raw_Min_temp - 40;
            rack->min_cell_t = Min_temp;

            uint16_t raw_Avg_temp = (data[4] << 8) | data[5];
            float Avg_temp = raw_Avg_temp - 40;
            rack->avg_cell_t = Avg_temp;

            break;
        }
    }
}

void can_handle_error_frame(uint32_t can_id,
                            const uint8_t data[8],
                            ems_can_health_t *health)
{
    health->tx_error_count = data[6];
    health->rx_error_count = data[7];

    if (can_id & CAN_ERR_BUSOFF)
    {
        health->bus_state = CAN_BUS_OFF;
    }
    else if (can_id & CAN_ERR_CRTL)
    {
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

