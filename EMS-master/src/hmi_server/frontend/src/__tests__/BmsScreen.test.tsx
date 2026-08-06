import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// Mock chart components
vi.mock("../components/charts/SocGauge", () => ({
  SocGauge: ({ soc }: { soc: number }) => (
    <div data-testid="soc-gauge">{soc.toFixed(1)}%</div>
  ),
}));

vi.mock("../components/charts/TrendLine", () => ({
  TrendLine: ({ label }: { label: string }) => (
    <div data-testid="trend-line">{label}</div>
  ),
}));

// Mock telemetry context
const mockState = {
  system: null,
  pcs: null,
  gpio: null,
  meter: null,
  btms: null,
  bmsRacks: {} as Record<string, unknown>,
  lastUpdate: 0,
};

vi.mock("../context/TelemetryContext", () => ({
  useTelemetryContext: () => ({
    state: mockState,
    connectionStatus: "connected",
  }),
}));

import BmsScreen from "../screens/BmsScreen";

const RACK_0_0 = {
  last_update_ms: 1000,
  pack_v: 512.4,
  pack_i: 25.3,
  pack_soc: 85.2,
  pack_soh: 98.1,
  min_cell_v: 3.21,
  max_cell_v: 3.35,
  avg_cell_v: 3.28,
  min_cell_t: 22.5,
  max_cell_t: 28.1,
  avg_cell_t: 25.3,
  fault_code: 0,
  online: 1,
};

const RACK_0_1 = {
  ...RACK_0_0,
  pack_soc: 60.0,
  pack_v: 498.7,
  fault_code: 0x0003,
  online: 0,
};

describe("BmsScreen", () => {
  it("renders rack selector buttons from bmsRacks keys", () => {
    mockState.bmsRacks = { "0.0": RACK_0_0, "0.1": RACK_0_1 };
    render(<BmsScreen />);
    expect(screen.getByText("Rack 0.0")).toBeInTheDocument();
    expect(screen.getByText("Rack 0.1")).toBeInTheDocument();
  });

  it("shows selected rack pack voltage and SOC", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0 };
    render(<BmsScreen />);
    // First rack is auto-selected via useEffect
    await waitFor(() => {
      expect(screen.getByText(/512\.4/)).toBeInTheDocument();
    });
    // SOC appears in both gauge mock and data row
    expect(screen.getAllByText(/85\.2/).length).toBeGreaterThanOrEqual(1);
  });

  it("shows online status badge", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0 };
    render(<BmsScreen />);
    await waitFor(() => {
      // "Online" appears as label and badge -- check the badge class
      const badges = screen.getAllByText("Online");
      expect(badges.length).toBeGreaterThanOrEqual(1);
      const badge = badges.find((el) => el.className.includes("bg-success"));
      expect(badge).toBeDefined();
    });
  });

  it("selecting a different rack shows its data", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0, "0.1": RACK_0_1 };
    const user = userEvent.setup();
    render(<BmsScreen />);
    await user.click(screen.getByText("Rack 0.1"));
    expect(screen.getByText(/498\.7/)).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("shows fault code in hex when non-zero", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0, "0.1": RACK_0_1 };
    const user = userEvent.setup();
    render(<BmsScreen />);
    await user.click(screen.getByText("Rack 0.1"));
    expect(screen.getByText("0x0003")).toBeInTheDocument();
  });

  it("shows 'No racks available' when bmsRacks is empty", () => {
    mockState.bmsRacks = {};
    render(<BmsScreen />);
    expect(screen.getByText("No racks available")).toBeInTheDocument();
  });

  it("shows cell voltage range for selected rack", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0 };
    render(<BmsScreen />);
    await waitFor(() => {
      expect(screen.getByText(/3\.21/)).toBeInTheDocument();
    });
    expect(screen.getByText(/3\.35/)).toBeInTheDocument();
  });

  it("shows cell temperature range for selected rack", async () => {
    mockState.bmsRacks = { "0.0": RACK_0_0 };
    render(<BmsScreen />);
    await waitFor(() => {
      expect(screen.getByText(/22\.5/)).toBeInTheDocument();
    });
    expect(screen.getByText(/28\.1/)).toBeInTheDocument();
  });
});
