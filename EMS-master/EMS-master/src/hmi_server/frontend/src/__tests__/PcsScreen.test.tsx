import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Mock chart components
vi.mock("../components/charts/TrendLine", () => ({
  TrendLine: ({ label }: { label: string }) => (
    <div data-testid="trend-line">{label}</div>
  ),
}));

// Mock telemetry hook
const mockUseTelemetry = vi.fn();
vi.mock("../hooks/useTelemetry", () => ({
  useTelemetry: (...args: unknown[]) => mockUseTelemetry(...args),
}));

import PcsScreen from "../screens/PcsScreen";

const PCS_DATA = {
  last_update_ms: 1000,
  ac_voltage: 415.2,
  ac_current: 120.5,
  active_power: 48.3,
  reactive_power: 5.1,
  dc_voltage: 720.0,
  dc_current: 68.4,
  frequency: 50.01,
  temperature: 42.3,
  state: 2,
  fault_code: 0,
};

describe("PcsScreen", () => {
  it("renders AC voltage", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/415\.2/)).toBeInTheDocument();
  });

  it("renders AC current", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/120\.5/)).toBeInTheDocument();
  });

  it("renders active power", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/48\.3/)).toBeInTheDocument();
  });

  it("renders reactive power", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/5\.1/)).toBeInTheDocument();
  });

  it("renders DC voltage", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    // Intl.NumberFormat with maximumFractionDigits:1 formats 720.0 as "720"
    expect(screen.getByText(/720/)).toBeInTheDocument();
  });

  it("renders DC current", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/68\.4/)).toBeInTheDocument();
  });

  it("renders frequency", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/50\.01/)).toBeInTheDocument();
  });

  it("renders temperature", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText(/42\.3/)).toBeInTheDocument();
  });

  it("renders PCS state name", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
  });

  it("renders fault code as None when zero", () => {
    mockUseTelemetry.mockReturnValue(PCS_DATA);
    render(<PcsScreen />);
    expect(screen.getByText("None")).toBeInTheDocument();
  });

  it("renders fault code in hex when non-zero", () => {
    mockUseTelemetry.mockReturnValue({ ...PCS_DATA, fault_code: 0x0010 });
    render(<PcsScreen />);
    expect(screen.getByText("0x0010")).toBeInTheDocument();
  });

  it("handles null PCS telemetry gracefully", () => {
    mockUseTelemetry.mockReturnValue(null);
    render(<PcsScreen />);
    expect(screen.getByText("Waiting for PCS data...")).toBeInTheDocument();
  });
});
