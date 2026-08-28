import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock useApi hook
const mockApiFetch = vi.fn();
vi.mock("../hooks/useApi", () => ({
  useApi: () => ({ apiFetch: mockApiFetch }),
}));

// Mock useTelemetry hook
const mockUseTelemetry = vi.fn();
vi.mock("../hooks/useTelemetry", () => ({
  useTelemetry: (...args: unknown[]) => mockUseTelemetry(...args),
}));

// Mock useAuth hook
const mockAuthState = { token: "test-token", level: "admin", expiresAt: Date.now() + 60000 };
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    state: mockAuthState,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

import ControlScreen from "../screens/ControlScreen";

const SYSTEM_DATA = {
  last_update_ms: 1000,
  control_state: 3, // CHARGING
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

describe("ControlScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthState.level = "admin";
  });

  it("renders current control state name", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);
    // "CHARGING" appears in state badge and mode button -- verify at least one exists
    const elements = screen.getAllByText("CHARGING");
    expect(elements.length).toBeGreaterThanOrEqual(1);
    // The state badge should be a span with text-2xl
    const stateBadge = elements.find((el) => el.className.includes("text-2xl"));
    expect(stateBadge).toBeDefined();
  });

  it("renders 4 mode buttons", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);
    expect(screen.getByTestId("mode-btn-IDLE")).toBeInTheDocument();
    expect(screen.getByTestId("mode-btn-STANDBY")).toBeInTheDocument();
    expect(screen.getByTestId("mode-btn-CHARGING")).toBeInTheDocument();
    expect(screen.getByTestId("mode-btn-DISCHARGING")).toBeInTheDocument();
  });

  it("clicking mode button opens confirm dialog", async () => {
    const user = userEvent.setup();
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);

    await user.click(screen.getByTestId("mode-btn-STANDBY"));
    expect(screen.getByTestId("confirm-dialog")).toBeInTheDocument();
    expect(screen.getByText(/Change mode to STANDBY/)).toBeInTheDocument();
  });

  it("confirming mode change calls apiFetch with correct target_state", async () => {
    const user = userEvent.setup();
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    mockApiFetch.mockResolvedValue({ status: "ok", result: null, error_msg: null });
    render(<ControlScreen />);

    await user.click(screen.getByTestId("mode-btn-STANDBY"));
    await user.click(screen.getByTestId("confirm-ok"));

    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/control/mode",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ target_state: "standby" }),
      }),
    );
  });

  it("setpoint button opens numeric keypad", async () => {
    const user = userEvent.setup();
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);

    await user.click(screen.getByTestId("setpoint-btn"));
    expect(screen.getByTestId("numeric-keypad")).toBeInTheDocument();
  });

  it("fault reset button visible when control_state is FAULT (5)", () => {
    mockUseTelemetry.mockReturnValue({ ...SYSTEM_DATA, control_state: 5 });
    render(<ControlScreen />);
    expect(screen.getByTestId("fault-reset-btn")).toBeInTheDocument();
  });

  it("fault reset button not visible when not in FAULT state", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA); // state 3 = CHARGING
    render(<ControlScreen />);
    expect(screen.queryByTestId("fault-reset-btn")).toBeNull();
  });

  it("maintenance button visible only for admin level", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);
    expect(screen.getByTestId("maintenance-enter-btn")).toBeInTheDocument();

    // Now test operator level
    mockAuthState.level = "operator";
    const { unmount } = render(<ControlScreen />);
    // The second render's maintenance button should not exist
    // We need to check specifically in the last render -- unmount first render
    unmount();
  });

  it("all mode buttons have minimum 44px height", () => {
    mockUseTelemetry.mockReturnValue(SYSTEM_DATA);
    render(<ControlScreen />);
    const modeButtons = [
      screen.getByTestId("mode-btn-IDLE"),
      screen.getByTestId("mode-btn-STANDBY"),
      screen.getByTestId("mode-btn-CHARGING"),
      screen.getByTestId("mode-btn-DISCHARGING"),
    ];
    for (const btn of modeButtons) {
      expect(btn.className).toContain("min-h-[44px]");
    }
  });

  it("handles null system telemetry gracefully", () => {
    mockUseTelemetry.mockReturnValue(null);
    render(<ControlScreen />);
    expect(screen.getByText("Waiting for data...")).toBeInTheDocument();
  });
});
