#ifndef EMS_IPC_DEFS_H
#define EMS_IPC_DEFS_H

/**
 * @file ipc_defs.h
 * @brief IPC contract definitions -- socket paths, topic strings, message constants.
 *
 * All ZeroMQ socket paths and PUB/SUB topic strings are defined here.
 * Python mirror: src/common/python/src/ems_common/ipc.py
 *
 * Socket topology:
 *   PUB/SUB   -- data_manager binds PUB, all consumers connect SUB
 *   REQ/REP   -- each command module binds REP, requesters connect REQ
 *   PUSH/PULL -- logger binds PULL, all modules connect PUSH
 *
 * Base directory: /run/ems/ (created by systemd tmpfiles.d or ExecStartPre)
 */

/* ---------------------------------------------------------------------------
 * Socket paths
 * -------------------------------------------------------------------------*/

#define EMS_IPC_BASE_DIR       "/run/ems"

/* PUB/SUB -- telemetry fan-out (data_manager binds PUB) */
#define EMS_SOCK_TELEMETRY     "ipc:///run/ems/telemetry.sock"

/* REQ/REP -- command endpoints (target module binds REP) */
#define EMS_SOCK_CONTROL_CMD   "ipc:///run/ems/control_cmd.sock"
#define EMS_SOCK_ALARM_CMD     "ipc:///run/ems/alarm_cmd.sock"

/* REQ/REP -- config queries (config_manager binds REP) */
#define EMS_SOCK_CONFIG        "ipc:///run/ems/config.sock"

/* PUB/SUB -- config reload events (config_manager binds PUB) */
#define EMS_SOCK_CONFIG_PUB    "ipc:///run/ems/config_pub.sock"

/* REQ/REP -- safety reset commands (safety_manager binds REP) */
#define EMS_SOCK_SAFETY_CMD    "ipc:///run/ems/safety_cmd.sock"

/* PUSH/PULL -- event ingestion (logger binds PULL) */
#define EMS_SOCK_LOGGER        "ipc:///run/ems/logger.sock"

/* REQ/REP -- logger query endpoint (logger binds REP) */
#define EMS_SOCK_LOGGER_QUERY  "ipc:///run/ems/logger_query.sock"

/* ---------------------------------------------------------------------------
 * PUB/SUB topic strings (dotted hierarchy, per-section granularity)
 * -------------------------------------------------------------------------*/

/* Telemetry topics -- published by data_manager at 1 Hz */
#define EMS_TOPIC_BMS_RACK       "bms.rack"
#define EMS_TOPIC_PCS            "pcs"
#define EMS_TOPIC_GPIO           "gpio"
#define EMS_TOPIC_METER          "meter"
#define EMS_TOPIC_BTMS           "btms"
#define EMS_TOPIC_SYSTEM         "system"

/* Event topics -- prefixed on PUSH/PULL messages */
#define EMS_TOPIC_ALARM          "alarm"
#define EMS_TOPIC_STATE_CHANGE   "state_change"
#define EMS_TOPIC_COMM_FAULT     "comm_fault"
#define EMS_TOPIC_CONFIG_RELOAD  "config_reload"

/* Control state topic */
#define EMS_TOPIC_CONTROL_STATE  "control.state"

/* ---------------------------------------------------------------------------
 * Message envelope field keys (for MessagePack map encoding)
 * -------------------------------------------------------------------------*/

#define EMS_MSG_KEY_TIMESTAMP    "ts"
#define EMS_MSG_KEY_SEQUENCE     "seq"
#define EMS_MSG_KEY_SOURCE       "src"
#define EMS_MSG_KEY_TOPIC        "topic"
#define EMS_MSG_KEY_PAYLOAD      "payload"

/* Command request/response keys */
#define EMS_MSG_KEY_ACTION       "action"
#define EMS_MSG_KEY_PARAMS       "params"
#define EMS_MSG_KEY_STATUS       "status"
#define EMS_MSG_KEY_RESULT       "result"
#define EMS_MSG_KEY_ERROR_MSG    "error_msg"

/* Event keys */
#define EMS_MSG_KEY_SEVERITY     "severity"
#define EMS_MSG_KEY_EVENT_TYPE   "event_type"
#define EMS_MSG_KEY_MESSAGE      "message"
#define EMS_MSG_KEY_DATA         "data"

/* ---------------------------------------------------------------------------
 * Status and severity string constants
 * -------------------------------------------------------------------------*/

#define EMS_STATUS_OK            "ok"
#define EMS_STATUS_ERROR         "error"

/* Severity string constants (match ems_severity_t enum names) */
#define EMS_SEVERITY_STR_INFO     "info"
#define EMS_SEVERITY_STR_WARNING  "warning"
#define EMS_SEVERITY_STR_ERROR    "error"
#define EMS_SEVERITY_STR_CRITICAL "critical"

#endif /* EMS_IPC_DEFS_H */
