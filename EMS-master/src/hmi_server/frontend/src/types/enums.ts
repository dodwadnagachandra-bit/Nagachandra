/** Display names for control_state values from SystemTelemetry */
export const CONTROL_STATE_NAMES: Record<number, string> = {
  1: "IDLE",
  2: "STANDBY",
  3: "CHARGING",
  4: "DISCHARGING",
  5: "FAULT",
  6: "EMERGENCY",
  7: "MAINTENANCE",
};

/** Display names for PCS state values from PcsTelemetry */
export const PCS_STATE_NAMES: Record<number, string> = {
  0: "OFF",
  1: "STANDBY",
  2: "RUNNING",
  3: "FAULT",
};
