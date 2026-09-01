/** WebSocket message envelope received from ws://host:8081/ws/telemetry */
export interface WsMessage {
  topic: string;
  data: Record<string, unknown>;
  ts: number;
}

/** topic: "system" */
export interface SystemTelemetry {
  last_update_ms: number;
  control_state: number;
  source_priority: number;
  active_setpoint_kw: number;
  total_soc: number;
  total_power_kw: number;
  total_energy_kwh: number;
  ems_uptime_s: number;
  pcs_command: number;
  pcs_command_seq: number;
  active_derating_pct: number;
}

/** topic: "bms.rack.{cluster}.{rack}" */
export interface BmsRackTelemetry {
  last_update_ms: number;
  pack_v: number;
  pack_i: number;
  pack_soc: number;
  pack_soh: number;
  min_cell_v: number;
  max_cell_v: number;
  avg_cell_v: number;
  min_cell_t: number;
  max_cell_t: number;
  avg_cell_t: number;
  fault_code: number;
  online: number;
}

/** topic: "pcs" */
export interface PcsTelemetry {
  last_update_ms: number;
  ac_voltage: number;
  ac_current: number;
  active_power: number;
  reactive_power: number;
  dc_voltage: number;
  dc_current: number;
  frequency: number;
  temperature: number;
  state: number;
  fault_code: number;
}

/** topic: "gpio" */
export interface GpioTelemetry {
  last_update_ms: number;
  di: number[];
  do_state: number[];
}

/** topic: "meter" */
export interface MeterTelemetry {
  last_update_ms: number;
  voltage: number;
  current: number;
  active_power: number;
  reactive_power: number;
  frequency: number;
  power_factor: number;
  energy_import: number;
  energy_export: number;
}

/** topic: "btms" */
export interface BtmsTelemetry {
  last_update_ms: number;
  inlet_temp: number;
  outlet_temp: number;
  fan_speed_pct: number;
  cooling_active: number;
}

/** topic: "cloud" */
export interface CloudTelemetry {
  state: "connected" | "disconnected";
  broker: string;
  ts: number;
}

/** topic: "ota" */
export interface OtaTelemetry {
  state: string; // "idle" | "downloading" | "verifying" | "applying" | "rebooting" | "failed"
  version_current: string | null;
  version_previous: string | null;
  detail: Record<string, unknown> | null;
  ts: number;
}

/** Global telemetry state held in context */
export interface TelemetryState {
  system: SystemTelemetry | null;
  pcs: PcsTelemetry | null;
  gpio: GpioTelemetry | null;
  meter: MeterTelemetry | null;
  btms: BtmsTelemetry | null;
  cloud: CloudTelemetry | null;
  ota: OtaTelemetry | null;
  bmsRacks: Record<string, BmsRackTelemetry>;
  lastUpdate: number;
}

/** Actions dispatched by the WebSocket message handler */
export type TelemetryAction = {
  type: "UPDATE_TOPIC";
  topic: string;
  data: Record<string, unknown>;
  ts: number;
};

/** WebSocket connection status */
export type ConnectionStatus = "connected" | "disconnected" | "reconnecting";
