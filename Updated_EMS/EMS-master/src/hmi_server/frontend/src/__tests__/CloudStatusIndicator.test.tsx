import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CloudStatusIndicator from "../components/CloudStatusIndicator";
import type { CloudTelemetry } from "../types/telemetry";

describe("CloudStatusIndicator", () => {
  it("renders gray dot when cloud is null", () => {
    render(<CloudStatusIndicator cloud={null} />);
    const dot = screen.getByTestId("cloud-status-dot");
    expect(dot.className).toContain("bg-gray-500");
  });

  it("renders green dot when cloud.state is connected", () => {
    const cloud: CloudTelemetry = {
      state: "connected",
      broker: "mqtt.example.com",
      ts: 1000,
    };
    render(<CloudStatusIndicator cloud={cloud} />);
    const dot = screen.getByTestId("cloud-status-dot");
    expect(dot.className).toContain("bg-green-500");
  });

  it("renders red dot when cloud.state is disconnected", () => {
    const cloud: CloudTelemetry = {
      state: "disconnected",
      broker: "mqtt.example.com",
      ts: 2000,
    };
    render(<CloudStatusIndicator cloud={cloud} />);
    const dot = screen.getByTestId("cloud-status-dot");
    expect(dot.className).toContain("bg-red-500");
  });
});
