import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  TelemetryProvider,
  telemetryReducer,
  initialTelemetryState,
} from "../context/TelemetryContext";
import type { TelemetryState } from "../types/telemetry";

describe("telemetryReducer", () => {
  it("updates state.system on UPDATE_TOPIC with topic 'system'", () => {
    const data = { total_soc: 85.5, total_power_kw: 100 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "system",
      data,
      ts: 1000,
    });
    expect(result.system).toEqual(data);
    expect(result.lastUpdate).toBe(1000);
  });

  it("updates state.pcs on UPDATE_TOPIC with topic 'pcs'", () => {
    const data = { active_power: 50, state: 2 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "pcs",
      data,
      ts: 2000,
    });
    expect(result.pcs).toEqual(data);
    expect(result.lastUpdate).toBe(2000);
  });

  it("updates state.gpio on UPDATE_TOPIC with topic 'gpio'", () => {
    const data = { di: [1, 0, 0, 0, 0, 0, 0, 0], do_state: [0, 0, 0, 0, 0, 0, 0, 0] };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "gpio",
      data,
      ts: 3000,
    });
    expect(result.gpio).toEqual(data);
  });

  it("updates state.meter on UPDATE_TOPIC with topic 'meter'", () => {
    const data = { voltage: 230, frequency: 50 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "meter",
      data,
      ts: 4000,
    });
    expect(result.meter).toEqual(data);
  });

  it("updates state.btms on UPDATE_TOPIC with topic 'btms'", () => {
    const data = { inlet_temp: 25, outlet_temp: 30 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "btms",
      data,
      ts: 5000,
    });
    expect(result.btms).toEqual(data);
  });

  it("updates state.bmsRacks on UPDATE_TOPIC with topic 'bms.rack.0.0'", () => {
    const data = { pack_soc: 90, pack_v: 52.1 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "bms.rack.0.0",
      data,
      ts: 6000,
    });
    expect(result.bmsRacks["0.0"]).toEqual(data);
    expect(result.lastUpdate).toBe(6000);
  });

  it("handles multiple BMS rack topics independently", () => {
    let state: TelemetryState = initialTelemetryState;
    state = telemetryReducer(state, {
      type: "UPDATE_TOPIC",
      topic: "bms.rack.0.0",
      data: { pack_soc: 90 },
      ts: 100,
    });
    state = telemetryReducer(state, {
      type: "UPDATE_TOPIC",
      topic: "bms.rack.0.1",
      data: { pack_soc: 85 },
      ts: 200,
    });
    expect(state.bmsRacks["0.0"]).toEqual({ pack_soc: 90 });
    expect(state.bmsRacks["0.1"]).toEqual({ pack_soc: 85 });
  });

  it("returns state unchanged for unknown topic", () => {
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "unknown_topic",
      data: { foo: "bar" },
      ts: 9999,
    });
    expect(result).toBe(initialTelemetryState);
  });

  it("updates state.cloud on UPDATE_TOPIC with topic 'cloud'", () => {
    const data = { state: "connected", broker: "mqtt.example.com", ts: 7000 };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "cloud",
      data,
      ts: 7000,
    });
    expect(result.cloud).toEqual(data);
    expect(result.lastUpdate).toBe(7000);
  });

  it("updates state.ota on UPDATE_TOPIC with topic 'ota'", () => {
    const data = {
      state: "idle",
      version_current: "1.2.3",
      version_previous: "1.2.2",
      detail: null,
      ts: 8000,
    };
    const result = telemetryReducer(initialTelemetryState, {
      type: "UPDATE_TOPIC",
      topic: "ota",
      data,
      ts: 8000,
    });
    expect(result.ota).toEqual(data);
    expect(result.lastUpdate).toBe(8000);
  });
});

describe("TelemetryProvider", () => {
  it("renders children", () => {
    render(
      <TelemetryProvider>
        <div data-testid="child">Hello</div>
      </TelemetryProvider>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });
});
