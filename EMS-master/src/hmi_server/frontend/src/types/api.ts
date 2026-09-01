/** POST /api/auth/login request body */
export interface LoginRequest {
  pin: string;
}

/** POST /api/auth/login response */
export interface LoginResponse {
  token: string;
  level: "operator" | "admin";
  expires_in: number;
}

/** Standard ZMQ response envelope for all REST command responses */
export interface ZmqResponse {
  status: "ok" | "error";
  result: Record<string, unknown> | null;
  error_msg: string | null;
}

/** POST /api/control/mode request body */
export interface ModeChangeRequest {
  target_state: string;
}

/** POST /api/control/setpoint request body */
export interface ManualSetpointRequest {
  power_kw: number;
}

/** POST /api/control/priority request body */
export interface SourcePriorityRequest {
  mode: string;
}

/** POST /api/control/maintenance request body */
export interface MaintenanceRequest {
  action: "enter" | "exit";
}

/** POST /api/alarm/acknowledge request body */
export interface AcknowledgeRequest {
  alarm_id: string;
}

/** Active alarm from GET /api/alarm/active */
export interface ActiveAlarm {
  alarm_id: string;
  signal: string;
  severity: "warning" | "action" | "protection";
  state: "ACTIVE_UNACKED" | "ACTIVE_ACKED" | "CLEARED_UNACKED";
  activated_at: number;
}

/** Schedule configuration for Settings screen */
export interface ScheduleConfig {
  mode: "manual" | "time_of_day" | "curve";
  time_windows: TimeWindow[];
  day_night: {
    day_start: string;
    night_start: string;
  };
  power_curve: number[];
}

/** Time window for schedule configuration */
export interface TimeWindow {
  start: string;
  end: string;
  action: "charge" | "discharge" | "idle";
  power_kw: number;
}
