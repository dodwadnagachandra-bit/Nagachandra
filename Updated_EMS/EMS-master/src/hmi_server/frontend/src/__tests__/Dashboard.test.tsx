import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Mock chart components to avoid canvas issues in jsdom
vi.mock("../components/charts/SocGauge", () => ({
  SocGauge: ({ soc }: { soc: number }) => (
    <div data-testid="soc-gauge">{soc.toFixed(1)}%</div>
  ),
}));

vi.mock("../components/charts/PowerBar", () => ({
  PowerBar: ({ powerKw }: { powerKw: number }) => (
    <div data-testid="power-bar">{powerKw.toFixed(1)} kW</div>
  ),
}));

vi.mock("../components/charts/TrendLine", () => ({
  TrendLine: ({ label }: { label: string }) => (
    <div data-testid="trend-line">{label}</div>
  ),
}));

// Mock react-router-dom outlet context
vi.mock("react-router-dom", () => ({
  useOutletContext: () => ({ unackedCount: 3 }),
}));

// Mock telemetry hook
const mockUseTelemetry = vi.fn();
vi.mock("../hooks/useTelemetry", () => ({
  useTelemetry: (...args: unknown[]) => mockUseTelemetry(...args),
}));

import Dashboard from "../screens/Dashboard";

const SYSTEM_DATA = {
  last_update_ms: 1000,
  control_state: 3,
  source_priority: 1,
  active_setpoint_kw: 50,
  total_soc: 72.5,
  total_power_kw: 45.3,
  total_energy_kwh: 200,
  ems_uptime_s: 3661,
  pcs_command: 1,
  pcs_command_seq: 5,
  active_derating_pct: 0,
};

describe("Dashboard", () => {
  it("renders SOC value when system data is available", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    expect(screen.getByTestId("soc-gauge")).toHaveTextContent("72.5%");
  });

  it("renders power value", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    expect(screen.getByTestId("power-bar")).toHaveTextContent("45.3 kW");
  });

  it("renders control state name", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    expect(screen.getByText("CHARGING")).toBeInTheDocument();
  });

  it("renders setpoint value", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    // Intl.NumberFormat with maximumFractionDigits:1 drops trailing zero
    expect(screen.getByText(/50/)).toBeInTheDocument();
  });

  it("renders uptime formatted as HH:MM:SS", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    // 3661s = 1h 1m 1s
    expect(screen.getByText("01:01:01")).toBeInTheDocument();
  });

  it("renders active alarm count from outlet context", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<Dashboard />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Active Alarms")).toBeInTheDocument();
  });

  it("handles null system telemetry gracefully", () => {
    mockUseTelemetry.mockReturnValue(null);
    render(<Dashboard />);
    expect(screen.getByText("Waiting for data...")).toBeInTheDocument();
  });
});
