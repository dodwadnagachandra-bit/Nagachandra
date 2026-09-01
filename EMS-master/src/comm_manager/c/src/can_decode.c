#include <stdbool.h>
#include <stdint.h>

#include "rtdb.h"
#include "can_decode.h"

#include <linux/can.h>
#include <linux/can/error.h>
#include <string.h>

/* ---------------------------------------------------------------------------
 * CAN IDs
 * -------------------------------------------------------------------------*/

#define first_msg             0x201U
#define Time_Date_ID          0x18FF0E03U
#define Soc_Message_ID        0x18FF0003U
#define Pack_status_ID        0x18FF0503U
#define Pack_Info_ID          0x18FF0803U
#define Min_Max_Volt_ID       0x18FF0203U
#define Min_Max_Temp_ID       0x18FF0303U
#define Pack_Voltage_Current_ID 0x18FF0103U
#define IMD_Info_Voltage      0x18FFD303U
#define IMD_Info_Isolation    0x18FFD203U
#define IMD_Info_General      0x18FFD103U
#define Hw_Version_ID         0x18FF0A03U
#define DTC_Accom_ID          0x18FF0403U
#define Config_Version_ID     0x18FF0D03U
#define FW_Version_ID         0x18FF0B03U
#define ARAI_FW_Version_ID    0x18FF0C03U
#define BLT_Version_ID        0x18FF0903U
#define Cell_Balance_ID       0x18FFD003U

#define Cell_Temp_1_ID        0x18FF9003U
#define Cell_Temp_2_ID        0x18FF9103U
#define Cell_Temp_3_ID        0x18FF9203U
#define Cell_Temp_4_ID        0x18FF9303U
#define Cell_Temp_5_ID        0x18FF9403U
#define Cell_Temp_6_ID        0x18FF9503U
#define Cell_Temp_7_ID        0x18FF9603U
#define Cell_Temp_8_ID        0x18FF9703U
#define Cell_Temp_9_ID        0x18FF9803U
#define Cell_Temp_10_ID       0x18FF9903U

#define Cell_Volt_1_ID        0x18FF1003U
#define Cell_Volt_2_ID        0x18FF1103U
#define Cell_Volt_3_ID        0x18FF1203U
#define Cell_Volt_4_ID        0x18FF1303U
#define Cell_Volt_5_ID        0x18FF1403U
#define Cell_Volt_6_ID        0x18FF1503U
#define Cell_Volt_7_ID        0x18FF1603U
#define Cell_Volt_8_ID        0x18FF1703U
#define Cell_Volt_9_ID        0x18FF1803U
#define Cell_Volt_10_ID       0x18FF1903U
#define Cell_Volt_11_ID       0x18FF1A03U
#define Cell_Volt_12_ID       0x18FF1B03U
#define Cell_Volt_13_ID       0x18FF1C03U
#define Cell_Volt_14_ID       0x18FF1D03U
#define Cell_Volt_15_ID       0x18FF1E03U
#define Cell_Volt_16_ID       0x18FF1F03U
#define Cell_Volt_17_ID       0x18FF2003U
#define Cell_Volt_18_ID       0x18FF2103U
#define Cell_Volt_19_ID       0x18FF2203U
#define Cell_Volt_20_ID       0x18FF2303U
#define Cell_Volt_21_ID       0x18FF2403U
#define Cell_Volt_22_ID       0x18FF2503U
#define Cell_Volt_23_ID       0x18FF2603U
#define Cell_Volt_24_ID       0x18FF2703U
#define Cell_Volt_25_ID       0x18FF2803U
#define Cell_Volt_26_ID       0x18FF2903U
#define Cell_Volt_27_ID       0x18FF2A03U
#define Cell_Volt_28_ID       0x18FF2B03U
#define Cell_Volt_29_ID       0x18FF2C03U
#define Cell_Volt_30_ID       0x18FF2D03U
#define Cell_Volt_31_ID       0x18FF2E03U
#define Cell_Volt_32_ID       0x18FF2F03U
#define Cell_Volt_33_ID       0x18FF3003U
#define Cell_Volt_34_ID       0x18FF3103U
#define Cell_Volt_35_ID       0x18FF3203U
#define Cell_Volt_36_ID       0x18FF3303U
#define Cell_Volt_37_ID       0x18FF3403U
#define Cell_Volt_38_ID       0x18FF3503U
#define Cell_Volt_39_ID       0x18FF3603U
#define Cell_Volt_40_ID       0x18FF3703U


void can_decoder(uint32_t can_id, const uint8_t data[8], ems_rack_t *rack)
{
    if (!rack || !data)
    {
        return;
    }

    switch (can_id)
    {

    /* =======================================================================
     * TEMPORARILY DISABLED
     *
     * These fields are NOT declared in ems_rack_t in rtdb.h:
     * hours, minutes, seconds, date, month, year
     * =======================================================================
     */

    /*
    case Time_Date_ID:
    {
        uint8_t hours = data[0];
        rack->hours = hours;

        uint8_t minutes = data[1];
        rack->minutes = minutes;

        uint8_t seconds = data[2];
        rack->seconds = seconds;

        uint8_t raw_date = data[3];
        rack->date = raw_date;

        uint8_t raw_month = data[4];
        rack->month = raw_month;

        uint16_t raw_year = (data[5] << 8) | data[6];
        rack->year = raw_year;

        break;
    }
    */


    /* =======================================================================
     * SOC MESSAGE
     *
     * pack_soc IS declared in rtdb.h.
     *
     * The other fields in this CAN message are NOT declared:
     * soc_ccl, soc_dcl, soc_bmu_ops, soc_dtc_acc, soc_hbs
     *
     * Therefore only pack_soc is decoded.
     * =======================================================================
     */

    case Soc_Message_ID:
    {
        uint8_t raw_soc = data[0];
        float soc = raw_soc * 0.5f;
        rack->pack_soc = soc;

        /*
         * NOT DECLARED IN rtdb.h:
         *
         * rack->soc_ccl
         * rack->soc_dcl
         * rack->soc_bmu_ops
         * rack->soc_dtc_acc
         * rack->soc_hbs
         *
         * Temporarily disabled.
         */

        break;
    }


    /* =======================================================================
     * PACK VOLTAGE / CURRENT
     *
     * pack_v and pack_i ARE declared.
     *
     * sw_pack_v and contractor fields are NOT declared.
     * =======================================================================
     */

    case Pack_Voltage_Current_ID:
    {
        int16_t raw_usw_pack_v = (data[0] << 8) | data[1];
        float USW_pack_v = raw_usw_pack_v * 0.0625f;
        rack->pack_v = USW_pack_v;

        int16_t raw_Pack_Current = (data[2] << 8) | data[3];
        float Pack_current = raw_Pack_Current * 0.125f;
        rack->pack_i = Pack_current;

        /*
         * NOT DECLARED IN rtdb.h:
         *
         * rack->sw_pack_v
         * rack->main_pos_contractor
         * rack->main_neg_contractor
         * rack->precharge_contractor
         * rack->charge_contractor
         * rack->mcu_contractor
         * rack->main_pos_contr_st
         * rack->main_neg_contr_st
         * rack->precharge_contr_st
         * rack->charge_contr_st
         * rack->mcu_contr_st
         * rack->ign_pin_st
         *
         * Temporarily disabled.
         */

        break;
    }


    /* =======================================================================
     * PACK STATUS
     *
     * All fields from this message are currently NOT declared in rtdb.h.
     * =======================================================================
     */

    /*
    case Pack_status_ID:
    {
        uint8_t raw_fuse_st = data[0];
        rack->fuse_st = raw_fuse_st;

        uint8_t raw_mccb_st = data[1];
        rack->mccb_st = raw_mccb_st;

        uint8_t raw_rack_id = data[2];
        rack->rack_id = raw_rack_id;

        uint8_t raw_ps_st = data[3];
        rack->ps_st = raw_ps_st;

        uint8_t raw_overriden_fan = data[4];
        rack->ov_fan = raw_overriden_fan;

        uint8_t raw_watchdog_reset = data[5];
        rack->watchdog_reset = raw_watchdog_reset;

        break;
    }
    */


    /* =======================================================================
     * PACK INFO
     *
     * pack_soh, cycle_cnt, Delta_t and Delta_v ARE declared in rtdb.h.
     * =======================================================================
     */

    case Pack_Info_ID:
    {
        uint8_t raw_SOH = data[0];
        float SOH = raw_SOH * 0.5f;
        rack->pack_soh = SOH;

        uint16_t raw_cycle_count = (data[1] << 8) | data[2];
        float cycle_count = raw_cycle_count * 0.5f;
        rack->cycle_cnt = cycle_count;

        int16_t raw_delta_t = (data[3] << 8) | data[4];
        rack->Delta_t = raw_delta_t;

        uint16_t raw_delta_v = (data[5] << 8) | data[6];
        rack->Delta_v = raw_delta_v;

        break;
    }


    /* =======================================================================
     * MIN / MAX CELL VOLTAGE
     *
     * All four destination fields ARE declared in rtdb.h.
     * =======================================================================
     */

    case Min_Max_Volt_ID:
    {
        uint16_t raw_Vmin = (data[0] << 8) | data[1];
        float Vmin = raw_Vmin * 0.000122f;
        rack->min_cell_v = Vmin;

        uint16_t raw_Vmax = (data[2] << 8) | data[3];
        float Vmax = raw_Vmax * 0.000122f;
        rack->max_cell_v = Vmax;

        uint16_t raw_Vmin_id = (data[4] << 8) | data[5];
        float Vmin_id = raw_Vmin_id * 1;
        rack->min_cell_num = Vmin_id;

        uint16_t raw_Vmax_id = (data[6] << 8) | data[7];
        float Vmax_id = raw_Vmax_id * 1;
        rack->max_cell_num = Vmax_id;

        break;
    }


    /* =======================================================================
     * MIN / MAX TEMPERATURE
     *
     * min_cell_t and max_cell_t ARE declared.
     *
     * Tmax_id and Tmin_id are ALSO declared in rtdb.h.
     * =======================================================================
     */

    case Min_Max_Temp_ID:
    {
        int16_t raw_Min_temp = (data[0] << 8) | data[1];
        float Min_temp = raw_Min_temp * 0.015625f;
        rack->min_cell_t = Min_temp;

        int16_t raw_Max_temp = (data[2] << 8) | data[3];
        float Max_temp = raw_Max_temp * 0.015625f;
        rack->max_cell_t = Max_temp;

        uint16_t raw_Tmax_id = data[4];
        float Tmax_id = raw_Tmax_id * 1;
        rack->Tmax_id = Tmax_id;

        uint16_t raw_Tmin_id = data[5];
        float Tmin_id = raw_Tmin_id * 1;
        rack->Tmin_id = Tmin_id;

        break;
    }


    /* =======================================================================
     * IMD INFORMATION
     *
     * None of these destination fields are declared in rtdb.h.
     * Temporarily disabled.
     * =======================================================================
     */

    /*
    case IMD_Info_Voltage:
    {
        uint16_t raw_Imd_volt = data[0] | (data[1] << 8);
        float Imd_volt_hv = raw_Imd_volt * 0.05f - 1606;
        rack->imd_volt = Imd_volt_hv;

        uint16_t raw_Imd_volt_hvneg = data[2] | (data[3] << 8);
        float Imd_volt_hvneg = raw_Imd_volt_hvneg * 0.05f - 1606;
        rack->imd_volt_hv_neg = Imd_volt_hvneg;

        uint16_t raw_Imd_volt_hvpos = data[4] | (data[5] << 8);
        float Imd_volt_hvpos = raw_Imd_volt_hvpos * 0.05f - 1606;
        rack->imd_volt_hv_pos = Imd_volt_hvpos;

        uint16_t Imd_volt_count = data[6];
        rack->imd_volt_cnt = Imd_volt_count;

        break;
    }


    case IMD_Info_Isolation:
    {
        uint16_t raw_r_iso_neg = data[0] | (data[1] << 8);
        rack->r_iso_neg = raw_r_iso_neg;

        uint16_t raw_r_iso_pos = data[2] | (data[3] << 8);
        rack->r_iso_pos = raw_r_iso_pos;

        uint16_t raw_r_iso_original = data[4] | (data[5] << 8);
        rack->r_iso_original = raw_r_iso_original;

        uint8_t raw_iso_counter = data[6];
        rack->iso_counter_det = raw_iso_counter;

        uint8_t raw_iso_quality = data[7];
        rack->iso_quality = raw_iso_quality;

        break;
    }


    case IMD_Info_General:
    {
        uint16_t raw_r_iso_corrected = data[0] | (data[1] << 8);
        rack->r_iso_corrected = raw_r_iso_corrected;

        uint8_t raw_r_iso_status = data[2] & 0x03;
        rack->r_iso_status = raw_r_iso_status;

        uint8_t raw_iso_cnt = data[3];
        rack->iso_cnt = raw_iso_cnt;

        uint8_t raw_warning_split = data[4];

        rack->imd_warn_bit00 = (raw_warning_split & 0x01);
        rack->imd_warn_bit01 = (raw_warning_split & 0x02) >> 1;
        rack->imd_warn_bit02 = (raw_warning_split & 0x04) >> 2;
        rack->imd_warn_bit03 = (raw_warning_split & 0x08) >> 3;
        rack->imd_warn_bit04 = (raw_warning_split & 0x10) >> 4;
        rack->imd_warn_bit05 = (raw_warning_split & 0x20) >> 5;
        rack->imd_warn_bit06 = (raw_warning_split & 0x40) >> 6;
        rack->imd_warn_bit07 = (raw_warning_split & 0x80) >> 7;

        uint8_t raw_imd_warning_split2 = data[5];

        rack->imd_warn_bit08 = (raw_imd_warning_split2 & 0x01);
        rack->imd_warn_bit09 = (raw_imd_warning_split2 & 0x02) >> 1;
        rack->imd_warn_bit10 = (raw_imd_warning_split2 & 0x04) >> 2;
        rack->imd_warn_bit11 = (raw_imd_warning_split2 & 0x08) >> 3;
        rack->imd_warn_bit12 = (raw_imd_warning_split2 & 0x10) >> 4;
        rack->imd_warn_bit13 = (raw_imd_warning_split2 & 0x20) >> 5;
        rack->imd_warn_bit14 = (raw_imd_warning_split2 & 0x40) >> 6;
        rack->imd_warn_bit15 = (raw_imd_warning_split2 & 0x80) >> 7;

        uint8_t raw_imd_device_activity = data[6];
        rack->imd_device_act = raw_imd_device_activity;

        break;
    }
    */


    /* =======================================================================
     * VERSION / DIAGNOSTIC / BALANCING MESSAGES
     *
     * These destination fields are NOT declared in rtdb.h.
     * Temporarily disabled.
     * =======================================================================
     */

    /*
    case Hw_Version_ID:
    {
        rack->hw_vr_0 = data[0];
        rack->hw_vr_1 = data[1];
        rack->hw_vr_2 = data[2];
        rack->hw_vr_3 = data[3];
        rack->hw_vr_4 = data[4];
        rack->hw_vr_5 = data[5];
        rack->hw_vr_6 = data[6];
        rack->hw_vr_7 = data[7];
        break;
    }


    case FW_Version_ID:
    {
        rack->fw_vr_0 = data[0];
        rack->fw_vr_1 = data[1];
        rack->fw_vr_2 = data[2];
        rack->fw_vr_3 = data[3];
        rack->fw_vr_4 = data[4];
        rack->fw_vr_5 = data[5];
        rack->fw_vr_6 = data[6];
        rack->fw_vr_7 = data[7];
        break;
    }


    case DTC_Accom_ID:
    {
        rack->active_flt_1 = data[0];
        rack->active_flt_2 = data[1];
        rack->active_flt_3 = data[2];
        rack->active_flt_4 = data[3];
        rack->active_flt_5 = data[4];
        rack->active_flt_6 = data[5];
        rack->active_flt_7 = data[6];
        rack->active_flt_8 = data[7];
        break;
    }


    case Config_Version_ID:
    {
        rack->config_ver_0 = data[0];
        rack->config_ver_1 = data[1];
        rack->config_ver_2 = data[2];
        rack->config_ver_3 = data[3];
        rack->config_ver_4 = data[4];
        rack->config_ver_5 = data[5];
        rack->config_ver_6 = data[6];
        rack->config_ver_7 = data[7];
        break;
    }


    case ARAI_FW_Version_ID:
    {
        rack->arai_ver_0 = data[0];
        rack->arai_ver_1 = data[1];
        rack->arai_ver_2 = data[2];
        rack->arai_ver_3 = data[3];
        rack->arai_ver_4 = data[4];
        rack->arai_ver_5 = data[5];
        rack->arai_ver_6 = data[6];
        rack->arai_ver_7 = data[7];
        break;
    }


    case BLT_Version_ID:
    {
        rack->blt_ver_0 = data[0];
        rack->blt_ver_1 = data[1];
        rack->blt_ver_2 = data[2];
        rack->blt_ver_3 = data[3];
        rack->blt_ver_4 = data[4];
        rack->blt_ver_5 = data[5];
        rack->blt_ver_6 = data[6];
        rack->blt_ver_7 = data[7];
        break;
    }


    case Cell_Balance_ID:
    {
        rack->cb_0 = data[0];
        rack->cb_1 = data[1];
        rack->cb_2 = data[2];
        rack->cb_3 = data[3];
        rack->cb_4 = data[4];
        rack->cb_5 = data[5];
        rack->cb_6 = data[6];
        rack->cb_7 = data[7];
        break;
    }
    */


    /* =======================================================================
     * CELL TEMPERATURE MESSAGES
     *
     * T_0 through T_39 are NOT declared in rtdb.h.
     *
     * rtdb.h currently has only:
     *
     * min_cell_t
     * max_cell_t
     * avg_cell_t
     * Tmax_id
     * Tmin_id
     *
     * Therefore all individual temperature cases are disabled for now.
     * =======================================================================
     */

    /*
    case Cell_Temp_1_ID:
    case Cell_Temp_2_ID:
    case Cell_Temp_3_ID:
    case Cell_Temp_4_ID:
    case Cell_Temp_5_ID:
    case Cell_Temp_6_ID:
    case Cell_Temp_7_ID:
    case Cell_Temp_8_ID:
    case Cell_Temp_9_ID:
    case Cell_Temp_10_ID:
    {
        // T_0 ... T_39 are not declared in rtdb.h.
        // Temporarily disabled.
        break;
    }
    */


    /* =======================================================================
     * CELL VOLTAGE 1
     *
     * V_0 - V_3 ARE declared in rtdb.h.
     * =======================================================================
     */

    case Cell_Volt_1_ID:
    {
        uint16_t raw_V_0 = (data[0] << 8) | data[1];
        float raw_v_0 = raw_V_0 * 0.000122f;
        rack->V_0 = raw_v_0;

        uint16_t raw_V_1 = (data[2] << 8) | data[3];
        float raw_v_1 = raw_V_1 * 0.000122f;
        rack->V_1 = raw_v_1;

        uint16_t raw_V_2 = (data[4] << 8) | data[5];
        float raw_v_2 = raw_V_2 * 0.000122f;
        rack->V_2 = raw_v_2;

        uint16_t raw_V_3 = (data[6] << 8) | data[7];
        float raw_v_3 = raw_V_3 * 0.000122f;
        rack->V_3 = raw_v_3;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 2
     * V_4 - V_7
     * =======================================================================
     */

    case Cell_Volt_2_ID:
    {
        uint16_t raw_V_4 = (data[0] << 8) | data[1];
        float raw_v_4 = raw_V_4 * 0.000122f;
        rack->V_4 = raw_v_4;

        uint16_t raw_V_5 = (data[2] << 8) | data[3];
        float raw_v_5 = raw_V_5 * 0.000122f;
        rack->V_5 = raw_v_5;

        uint16_t raw_V_6 = (data[4] << 8) | data[5];
        float raw_v_6 = raw_V_6 * 0.000122f;
        rack->V_6 = raw_v_6;

        uint16_t raw_V_7 = (data[6] << 8) | data[7];
        float raw_v_7 = raw_V_7 * 0.000122f;
        rack->V_7 = raw_v_7;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 3
     * V_8 - V_11
     * =======================================================================
     */

    case Cell_Volt_3_ID:
    {
        uint16_t raw_V_8 = (data[0] << 8) | data[1];
        float raw_v_8 = raw_V_8 * 0.000122f;
        rack->V_8 = raw_v_8;

        uint16_t raw_V_9 = (data[2] << 8) | data[3];
        float raw_v_9 = raw_V_9 * 0.000122f;
        rack->V_9 = raw_v_9;

        uint16_t raw_V_10 = (data[4] << 8) | data[5];
        float raw_v_10 = raw_V_10 * 0.000122f;
        rack->V_10 = raw_v_10;

        uint16_t raw_V_11 = (data[6] << 8) | data[7];
        float raw_v_11 = raw_V_11 * 0.000122f;
        rack->V_11 = raw_v_11;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 4
     * V_12 - V_15
     * =======================================================================
     */

    case Cell_Volt_4_ID:
    {
        uint16_t raw_V_12 = (data[0] << 8) | data[1];
        float raw_v_12 = raw_V_12 * 0.000122f;
        rack->V_12 = raw_v_12;

        uint16_t raw_V_13 = (data[2] << 8) | data[3];
        float raw_v_13 = raw_V_13 * 0.000122f;
        rack->V_13 = raw_v_13;

        uint16_t raw_V_14 = (data[4] << 8) | data[5];
        float raw_v_14 = raw_V_14 * 0.000122f;
        rack->V_14 = raw_v_14;

        uint16_t raw_V_15 = (data[6] << 8) | data[7];
        float raw_v_15 = raw_V_15 * 0.000122f;
        rack->V_15 = raw_v_15;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 5
     * V_16 - V_19
     * =======================================================================
     */

    case Cell_Volt_5_ID:
    {
        uint16_t raw_V_16 = (data[0] << 8) | data[1];
        float raw_v_16 = raw_V_16 * 0.000122f;
        rack->V_16 = raw_v_16;

        uint16_t raw_V_17 = (data[2] << 8) | data[3];
        float raw_v_17 = raw_V_17 * 0.000122f;
        rack->V_17 = raw_v_17;

        uint16_t raw_V_18 = (data[4] << 8) | data[5];
        float raw_v_18 = raw_V_18 * 0.000122f;
        rack->V_18 = raw_v_18;

        uint16_t raw_V_19 = (data[6] << 8) | data[7];
        float raw_v_19 = raw_V_19 * 0.000122f;
        rack->V_19 = raw_v_19;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 6
     * V_20 - V_23
     * =======================================================================
     */

    case Cell_Volt_6_ID:
    {
        uint16_t raw_V_20 = (data[0] << 8) | data[1];
        float raw_v_20 = raw_V_20 * 0.000122f;
        rack->V_20 = raw_v_20;

        uint16_t raw_V_21 = (data[2] << 8) | data[3];
        float raw_v_21 = raw_V_21 * 0.000122f;
        rack->V_21 = raw_v_21;

        uint16_t raw_V_22 = (data[4] << 8) | data[5];
        float raw_v_22 = raw_V_22 * 0.000122f;
        rack->V_22 = raw_v_22;

        uint16_t raw_V_23 = (data[6] << 8) | data[7];
        float raw_v_23 = raw_V_23 * 0.000122f;
        rack->V_23 = raw_v_23;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 7
     * V_24 - V_27
     * =======================================================================
     */

    case Cell_Volt_7_ID:
    {
        uint16_t raw_V_24 = (data[0] << 8) | data[1];
        float raw_v_24 = raw_V_24 * 0.000122f;
        rack->V_24 = raw_v_24;

        uint16_t raw_V_25 = (data[2] << 8) | data[3];
        float raw_v_25 = raw_V_25 * 0.000122f;
        rack->V_25 = raw_v_25;

        uint16_t raw_V_26 = (data[4] << 8) | data[5];
        float raw_v_26 = raw_V_26 * 0.000122f;
        rack->V_26 = raw_v_26;

        uint16_t raw_V_27 = (data[6] << 8) | data[7];
        float raw_v_27 = raw_V_27 * 0.000122f;
        rack->V_27 = raw_v_27;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 8
     * V_28 - V_31
     * =======================================================================
     */

    case Cell_Volt_8_ID:
    {
        uint16_t raw_V_28 = (data[0] << 8) | data[1];
        float raw_v_28 = raw_V_28 * 0.000122f;
        rack->V_28 = raw_v_28;

        uint16_t raw_V_29 = (data[2] << 8) | data[3];
        float raw_v_29 = raw_V_29 * 0.000122f;
        rack->V_29 = raw_v_29;

        uint16_t raw_V_30 = (data[4] << 8) | data[5];
        float raw_v_30 = raw_V_30 * 0.000122f;
        rack->V_30 = raw_v_30;

        uint16_t raw_V_31 = (data[6] << 8) | data[7];
        float raw_v_31 = raw_V_31 * 0.000122f;
        rack->V_31 = raw_v_31;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 9
     * V_32 - V_35
     * =======================================================================
     */

    case Cell_Volt_9_ID:
    {
        uint16_t raw_V_32 = (data[0] << 8) | data[1];
        float raw_v_32 = raw_V_32 * 0.000122f;
        rack->V_32 = raw_v_32;

        uint16_t raw_V_33 = (data[2] << 8) | data[3];
        float raw_v_33 = raw_V_33 * 0.000122f;
        rack->V_33 = raw_v_33;

        uint16_t raw_V_34 = (data[4] << 8) | data[5];
        float raw_v_34 = raw_V_34 * 0.000122f;
        rack->V_34 = raw_v_34;

        uint16_t raw_V_35 = (data[6] << 8) | data[7];
        float raw_v_35 = raw_V_35 * 0.000122f;
        rack->V_35 = raw_v_35;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 10
     * V_36 - V_39
     * =======================================================================
     */

    case Cell_Volt_10_ID:
    {
        uint16_t raw_V_36 = (data[0] << 8) | data[1];
        float raw_v_36 = raw_V_36 * 0.000122f;
        rack->V_36 = raw_v_36;

        uint16_t raw_V_37 = (data[2] << 8) | data[3];
        float raw_v_37 = raw_V_37 * 0.000122f;
        rack->V_37 = raw_v_37;

        uint16_t raw_V_38 = (data[4] << 8) | data[5];
        float raw_v_38 = raw_V_38 * 0.000122f;
        rack->V_38 = raw_v_38;

        uint16_t raw_V_39 = (data[6] << 8) | data[7];
        float raw_v_39 = raw_V_39 * 0.000122f;
        rack->V_39 = raw_v_39;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 11
     * V_40 - V_43
     * =======================================================================
     */

    case Cell_Volt_11_ID:
    {
        uint16_t raw_V_40 = (data[0] << 8) | data[1];
        float raw_v_40 = raw_V_40 * 0.000122f;
        rack->V_40 = raw_v_40;

        uint16_t raw_V_41 = (data[2] << 8) | data[3];
        float raw_v_41 = raw_V_41 * 0.000122f;
        rack->V_41 = raw_v_41;

        uint16_t raw_V_42 = (data[4] << 8) | data[5];
        float raw_v_42 = raw_V_42 * 0.000122f;
        rack->V_42 = raw_v_42;

        uint16_t raw_V_43 = (data[6] << 8) | data[7];
        float raw_v_43 = raw_V_43 * 0.000122f;
        rack->V_43 = raw_v_43;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 12
     * V_44 - V_47
     * =======================================================================
     */

    case Cell_Volt_12_ID:
    {
        uint16_t raw_V_44 = (data[0] << 8) | data[1];
        float raw_v_44 = raw_V_44 * 0.000122f;
        rack->V_44 = raw_v_44;

        uint16_t raw_V_45 = (data[2] << 8) | data[3];
        float raw_v_45 = raw_V_45 * 0.000122f;
        rack->V_45 = raw_v_45;

        uint16_t raw_V_46 = (data[4] << 8) | data[5];
        float raw_v_46 = raw_V_46 * 0.000122f;
        rack->V_46 = raw_v_46;

        uint16_t raw_V_47 = (data[6] << 8) | data[7];
        float raw_v_47 = raw_V_47 * 0.000122f;
        rack->V_47 = raw_v_47;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 13
     * V_48 - V_51
     * =======================================================================
     */

    case Cell_Volt_13_ID:
    {
        uint16_t raw_V_48 = (data[0] << 8) | data[1];
        float raw_v_48 = raw_V_48 * 0.000122f;
        rack->V_48 = raw_v_48;

        uint16_t raw_V_49 = (data[2] << 8) | data[3];
        float raw_v_49 = raw_V_49 * 0.000122f;
        rack->V_49 = raw_v_49;

        uint16_t raw_V_50 = (data[4] << 8) | data[5];
        float raw_v_50 = raw_V_50 * 0.000122f;
        rack->V_50 = raw_v_50;

        uint16_t raw_V_51 = (data[6] << 8) | data[7];
        float raw_v_51 = raw_V_51 * 0.000122f;
        rack->V_51 = raw_v_51;

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 14
     *
     * V_52, V_53 and V_54 ARE declared.
     *
     * V_55 IS NOT declared, so data[6]/data[7] is intentionally ignored.
     * =======================================================================
     */

    case Cell_Volt_14_ID:
    {
        uint16_t raw_V_52 = (data[0] << 8) | data[1];
        float raw_v_52 = raw_V_52 * 0.000122f;
        rack->V_52 = raw_v_52;

        uint16_t raw_V_53 = (data[2] << 8) | data[3];
        float raw_v_53 = raw_V_53 * 0.000122f;
        rack->V_53 = raw_v_53;

        uint16_t raw_V_54 = (data[4] << 8) | data[5];
        float raw_v_54 = raw_V_54 * 0.000122f;
        rack->V_54 = raw_v_54;

        /*
         * V_55 is NOT declared in rtdb.h.
         *
         * Temporarily disabled:
         *
         * uint16_t raw_V_55 = (data[6] << 8) | data[7];
         * float raw_v_55 = raw_V_55 * 0.000122f;
         * rack->V_55 = raw_v_55;
         */

        break;
    }


    /* =======================================================================
     * CELL VOLTAGE 15 - 40
     *
     * V_56 through V_159 are NOT declared in rtdb.h.
     *
     * Therefore these complete CAN cases are temporarily disabled.
     *
     * When rtdb.h is expanded to include V_55 ... V_159, these cases can
     * be enabled again.
     * =======================================================================
     */

    /*
    case Cell_Volt_15_ID:
    case Cell_Volt_16_ID:
    case Cell_Volt_17_ID:
    case Cell_Volt_18_ID:
    case Cell_Volt_19_ID:
    case Cell_Volt_20_ID:
    case Cell_Volt_21_ID:
    case Cell_Volt_22_ID:
    case Cell_Volt_23_ID:
    case Cell_Volt_24_ID:
    case Cell_Volt_25_ID:
    case Cell_Volt_26_ID:
    case Cell_Volt_27_ID:
    case Cell_Volt_28_ID:
    case Cell_Volt_29_ID:
    case Cell_Volt_30_ID:
    case Cell_Volt_31_ID:
    case Cell_Volt_32_ID:
    case Cell_Volt_33_ID:
    case Cell_Volt_34_ID:
    case Cell_Volt_35_ID:
    case Cell_Volt_36_ID:
    case Cell_Volt_37_ID:
    case Cell_Volt_38_ID:
    case Cell_Volt_39_ID:
    case Cell_Volt_40_ID:
    {
        // V_56 ... V_159 are not declared in rtdb.h.
        break;
    }
    */


    default:
        break;
    }
}


/* ===========================================================================
 * CAN ERROR FRAME HANDLING
 * ========================================================================== */

void can_handle_error_frame(uint32_t can_id,
                            const uint8_t data[8],
                            ems_can_health_t *health)
{
    if (!health || !data)
    {
        return;
    }

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
