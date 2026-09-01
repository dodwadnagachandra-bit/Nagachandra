import { createContext, useContext, useReducer } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import type {
  BmsRackTelemetry,
  BtmsTelemetry,
  CloudTelemetry,
  ConnectionStatus,
  GpioTelemetry,
  MeterTelemetry,
  OtaTelemetry,
  PcsTelemetry,
  SystemTelemetry,
  TelemetryAction,
  TelemetryState,
} from "../types/telemetry";

/** Initial state: all topics null, no BMS racks, no updates. */
export const initialTelemetryState: TelemetryState = {
  system: null,
  pcs: null,
  gpio: null,
  meter: null,
  btms: null,
  cloud: null,
  ota: null,
  bmsRacks: {},
  lastUpdate: 0,
};

/** Routes UPDATE_TOPIC actions to the correct state field. */
export function telemetryReducer(
  state: TelemetryState,
  action: TelemetryAction,
): TelemetryState {
  if (action.type === "UPDATE_TOPIC") {
    const { topic, data, ts } = action;

    if (topic === "system") {
      return { ...state, system: data as unknown as SystemTelemetry, lastUpdate: ts };
    }
    if (topic === "pcs") {
      return { ...state, pcs: data as unknown as PcsTelemetry, lastUpdate: ts };
    }
    if (topic === "gpio") {
      return { ...state, gpio: data as unknown as GpioTelemetry, lastUpdate: ts };
    }
    if (topic === "meter") {
      return { ...state, meter: data as unknown as MeterTelemetry, lastUpdate: ts };
    }
    if (topic === "btms") {
      return { ...state, btms: data as unknown as BtmsTelemetry, lastUpdate: ts };
    }
    if (topic === "cloud") {
      return { ...state, cloud: data as unknown as CloudTelemetry, lastUpdate: ts };
    }
    if (topic === "ota") {
      return { ...state, ota: data as unknown as OtaTelemetry, lastUpdate: ts };
    }
    if (topic.startsWith("bms.rack.")) {
      const rackKey: string = topic.replace("bms.rack.", "");
      return {
        ...state,
        bmsRacks: {
          ...state.bmsRacks,
          [rackKey]: data as unknown as BmsRackTelemetry,
        },
        lastUpdate: ts,
      };
    }
  }
  return state;
}

/** Context value shape. */
interface TelemetryContextValue {
  state: TelemetryState;
  connectionStatus: ConnectionStatus;
}

const TelemetryContext = createContext<TelemetryContextValue | null>(null);

/** Provides global telemetry state and WebSocket connection to children. */
export function TelemetryProvider({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const [state, dispatch] = useReducer(telemetryReducer, initialTelemetryState);

  // Build WebSocket URL: relative path works for both dev (Vite proxy) and production
  const wsUrl: string =
    typeof window !== "undefined" && window.location.protocol === "https:"
      ? `wss://${window.location.host}/ws/telemetry`
      : typeof window !== "undefined" && window.location.host
        ? `ws://${window.location.host}/ws/telemetry`
        : "/ws/telemetry";

  const connectionStatus: ConnectionStatus = useWebSocket(wsUrl, dispatch);

  return (
    <TelemetryContext.Provider value={{ state, connectionStatus }}>
      {children}
    </TelemetryContext.Provider>
  );
}

/** Access telemetry context. Throws if used outside TelemetryProvider. */
export function useTelemetryContext(): TelemetryContextValue {
  const ctx = useContext(TelemetryContext);
  if (ctx === null) {
    throw new Error("useTelemetryContext must be used within a TelemetryProvider");
  }
  return ctx;
}
