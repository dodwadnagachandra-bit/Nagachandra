import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock EnergyBar chart component to avoid canvas issues in jsdom
vi.mock("../components/charts/EnergyBar", () => ({
  EnergyBar: ({
    charge_kwh,
    discharge_kwh,
    import_kwh,
    export_kwh,
  }: {
    charge_kwh: number;
    discharge_kwh: number;
    import_kwh: number;
    export_kwh: number;
  }) => (
    <div data-testid="energy-bar">
      charge={charge_kwh} discharge={discharge_kwh} import={import_kwh}{" "}
      export={export_kwh}
    </div>
  ),
}));

// Mock useApi hook
const mockApiFetch = vi.fn();
vi.mock("../hooks/useApi", () => ({
  useApi: () => ({ apiFetch: mockApiFetch }),
}));

import EnergyScreen from "../screens/EnergyScreen";

const ENERGY_RESPONSE = {
  status: "ok" as const,
  result: {
    columns: ["discharge_wh", "charge_wh", "grid_import_wh", "grid_export_wh"],
    rows: [[5000000, 3500000, 1200000, 800000]],
    count: 1,
  },
  error_msg: null,
};

describe("EnergyScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiFetch.mockResolvedValue(ENERGY_RESPONSE);
  });

  it("renders time range selector buttons", async () => {
    render(<EnergyScreen />);
    await waitFor(() => {
      expect(screen.getByTestId("range-today")).toBeInTheDocument();
    });
    expect(screen.getByTestId("range-7d")).toBeInTheDocument();
    expect(screen.getByTestId("range-30d")).toBeInTheDocument();
  });

  it("fetches energy_totals on mount with today range", async () => {
    render(<EnergyScreen />);
    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/api/query/energy_totals",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });
    // Verify the body contains start_ts and end_ts
    const callBody = JSON.parse(mockApiFetch.mock.calls[0][1].body);
    expect(callBody).toHaveProperty("start_ts");
    expect(callBody).toHaveProperty("end_ts");
  });

  it("clicking 7 Days button re-fetches with wider range", async () => {
    const user = userEvent.setup();
    render(<EnergyScreen />);

    // Wait for initial fetch
    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledTimes(1);
    });

    // Click 7 Days
    await user.click(screen.getByTestId("range-7d"));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledTimes(2);
    });

    const secondCall = JSON.parse(mockApiFetch.mock.calls[1][1].body);
    const firstCall = JSON.parse(mockApiFetch.mock.calls[0][1].body);
    // 7-day range should have earlier start_ts than today
    expect(secondCall.start_ts).toBeLessThan(firstCall.start_ts);
  });

  it("renders energy total values in kWh", async () => {
    render(<EnergyScreen />);
    await waitFor(() => {
      // charge_wh=3500000 -> 3500 kWh
      expect(screen.getByText(/3,500/)).toBeInTheDocument();
    });
    // discharge_wh=5000000 -> 5000 kWh
    expect(screen.getByText(/5,000/)).toBeInTheDocument();
    // grid_import_wh=1200000 -> 1200 kWh
    expect(screen.getByText(/1,200/)).toBeInTheDocument();
    // grid_export_wh=800000 -> 800 kWh
    expect(screen.getByText(/800 kWh/)).toBeInTheDocument();
  });

  it("shows loading state while fetching", () => {
    // Make fetch never resolve
    mockApiFetch.mockReturnValue(new Promise(() => {}));
    render(<EnergyScreen />);
    expect(screen.getByText(/Fetching energy data/)).toBeInTheDocument();
  });

  it("shows error state when fetch fails", async () => {
    mockApiFetch.mockRejectedValue(new Error("Network error"));
    render(<EnergyScreen />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load energy data/)).toBeInTheDocument();
    });
  });

  it("renders EnergyBar chart with converted values", async () => {
    render(<EnergyScreen />);
    await waitFor(() => {
      expect(screen.getByTestId("energy-bar")).toBeInTheDocument();
    });
    const bar = screen.getByTestId("energy-bar");
    // Values converted from Wh to kWh: charge=3500, discharge=5000, import=1200, export=800
    expect(bar.textContent).toContain("charge=3500");
    expect(bar.textContent).toContain("discharge=5000");
    expect(bar.textContent).toContain("import=1200");
    expect(bar.textContent).toContain("export=800");
  });

  it("handles empty result gracefully", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { columns: [], rows: [], count: 0 },
      error_msg: null,
    });
    render(<EnergyScreen />);
    await waitFor(() => {
      expect(
        screen.getByText(/No energy data for this period/),
      ).toBeInTheDocument();
    });
  });
});
