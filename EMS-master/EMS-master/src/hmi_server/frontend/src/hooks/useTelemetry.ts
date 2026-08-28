import { useTelemetryContext } from "../context/TelemetryContext";
import type {
  BmsRackTelemetry,
  BtmsTelemetry,
  GpioTelemetry,
  MeterTelemetry,
  PcsTelemetry,
  SystemTelemetry,
} from "../types/telemetry";

/** Type-safe overloads for each known topic. */
export function useTelemetry(topic: "system"): SystemTelemetry | null;
export function useTelemetry(topic: "pcs"): PcsTelemetry | null;
export function useTelemetry(topic: "gpio"): GpioTelemetry | null;
export function useTelemetry(topic: "meter"): MeterTelemetry | null;
export function useTelemetry(topic: "btms"): BtmsTelemetry | null;
export function useTelemetry(topic: string): unknown;

/**
 * Returns the latest telemetry data for a given topic from context.
 *
 * For "system", "pcs", "gpio", "meter", "btms" returns the direct state field.
 * For BMS rack topics (e.g. "bms.rack.0.0") strips prefix and looks up in bmsRacks map.
 */
export function useTelemetry(topic: string): unknown {
  const { state } = useTelemetryContext();

  if (topic === "system") return state.system;
  if (topic === "pcs") return state.pcs;
  if (topic === "gpio") return state.gpio;
  if (topic === "meter") return state.meter;
  if (topic === "btms") return state.btms;

  if (topic.startsWith("bms.rack.")) {
    const rackKey: string = topic.replace("bms.rack.", "");
    return (state.bmsRacks[rackKey] as BmsRackTelemetry | undefined) ?? null;
  }

  return null;
}
