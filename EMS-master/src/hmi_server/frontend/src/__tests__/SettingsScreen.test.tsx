import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock useApi hook
const mockApiFetch = vi.fn();
vi.mock("../hooks/useApi", () => ({
  useApi: () => ({ apiFetch: mockApiFetch }),
}));

// Mock useAuth hook
const mockLogout = vi.fn();
const mockAuthState = {
  token: "test-token",
  level: "admin" as "operator" | "admin" | null,
  expiresAt: Date.now() + 60000,
};
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    state: mockAuthState,
    login: vi.fn(),
    logout: mockLogout,
  }),
}));

// Mock react-router-dom useNavigate
const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

// Mock TelemetryContext
const mockTelemetryState = {
  system: null,
  pcs: null,
  gpio: null,
  meter: null,
  btms: null,
  cloud: null,
  ota: null as null | {
    state: string;
    version_current: string | null;
    version_previous: string | null;
    detail: Record<string, unknown> | null;
    ts: number;
  },
  bmsRacks: {},
  lastUpdate: 0,
};
vi.mock("../context/TelemetryContext", () => ({
  useTelemetryContext: () => ({
    state: mockTelemetryState,
    connectionStatus: "disconnected",
  }),
}));

import SettingsScreen from "../screens/SettingsScreen";

describe("SettingsScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthState.level = "admin";
    mockTelemetryState.ota = null;
  });

  it("renders schedule mode selector buttons", () => {
    render(<SettingsScreen />);
    expect(screen.getByTestId("mode-manual")).toBeInTheDocument();
    expect(screen.getByTestId("mode-time_of_day")).toBeInTheDocument();
    expect(screen.getByTestId("mode-curve")).toBeInTheDocument();
  });

  it("selecting time_of_day mode shows time windows section", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("mode-time_of_day"));
    expect(screen.getByTestId("time-windows-section")).toBeInTheDocument();
  });

  it("add window button adds a new window row", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("mode-time_of_day"));
    const addBtn = screen.getByTestId("add-window-btn");
    await user.click(addBtn);

    expect(screen.getByTestId("window-row-0")).toBeInTheDocument();
  });

  it("remove button removes a window row", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("mode-time_of_day"));
    // Add a window
    await user.click(screen.getByTestId("add-window-btn"));
    expect(screen.getByTestId("window-row-0")).toBeInTheDocument();

    // Remove it
    await user.click(screen.getByTestId("remove-window-0"));
    expect(screen.queryByTestId("window-row-0")).toBeNull();
  });

  it("non-admin user gets redirected", () => {
    mockAuthState.level = "operator";
    render(<SettingsScreen />);
    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  it("save button calls PUT /api/config/schedule with auth token", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    } as Response);

    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("save-btn"));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/config/schedule",
        expect.objectContaining({
          method: "PUT",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            Authorization: "Bearer test-token",
          }),
        }),
      );
    });
  });

  it("save button shows success message on 200 response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    } as Response);

    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("save-btn"));

    await waitFor(() => {
      expect(screen.getByText(/schedule saved successfully/i)).toBeInTheDocument();
    });
  });

  it("save button shows error message on non-200 response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: vi.fn().mockResolvedValue({}),
    } as unknown as Response);

    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("save-btn"));

    await waitFor(() => {
      expect(screen.getByText(/failed to save schedule/i)).toBeInTheDocument();
    });
  });

  it("renders OTA waiting message when no OTA data", () => {
    mockTelemetryState.ota = null;
    render(<SettingsScreen />);
    expect(screen.getByTestId("ota-status-section")).toBeInTheDocument();
    expect(screen.getByText(/waiting for data/i)).toBeInTheDocument();
  });

  it("renders OTA status section with state badge when data available", () => {
    mockTelemetryState.ota = {
      state: "idle",
      version_current: "1.2.3",
      version_previous: "1.2.2",
      detail: null,
      ts: 1000,
    };
    render(<SettingsScreen />);
    expect(screen.getByTestId("ota-status-section")).toBeInTheDocument();
    expect(screen.getByTestId("ota-state-badge")).toBeInTheDocument();
    expect(screen.getByText(/1\.2\.3/)).toBeInTheDocument();
  });

  it("logout button calls logout function", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen />);

    await user.click(screen.getByTestId("logout-btn"));
    expect(mockLogout).toHaveBeenCalled();
  });
});
