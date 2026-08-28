import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import ConnectionIndicator from "../components/ConnectionIndicator";
import Sidebar from "../components/Sidebar";

// Mock TelemetryContext so Sidebar can render without a full TelemetryProvider
vi.mock("../context/TelemetryContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../context/TelemetryContext")>();
  return {
    ...actual,
    useTelemetryContext: () => ({
      state: {
        system: null,
        pcs: null,
        gpio: null,
        meter: null,
        btms: null,
        cloud: null,
        ota: null,
        bmsRacks: {},
        lastUpdate: 0,
      },
      connectionStatus: "disconnected" as const,
    }),
  };
});

function renderWithRouter(ui: React.ReactElement, initialRoute = "/") {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>{ui}</MemoryRouter>,
  );
}

describe("Layout", () => {
  it("renders sidebar and content area", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("content-area")).toBeInTheDocument();
  });

  it("renders 6 visible navigation tabs (Settings hidden by default)", () => {
    renderWithRouter(
      <Sidebar
        showSettings={false}
        unackedCount={0}
        connectionStatus="disconnected"
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(6);
    // Settings label should not be in the document
    const settingsLabels = screen.queryAllByText("Settings");
    expect(settingsLabels).toHaveLength(0);
  });

  it("renders 7 tabs when showSettings is true", () => {
    renderWithRouter(
      <Sidebar
        showSettings={true}
        unackedCount={0}
        connectionStatus="disconnected"
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(7);
  });

  it("navigates to correct route when clicking a tab", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    // Wait for lazy-loaded Dashboard
    expect(await screen.findByTestId("screen-dashboard")).toBeInTheDocument();

    // Click Battery tab and verify BMS screen
    const batteryLink = screen.getByText("Battery");
    await user.click(batteryLink);
    expect(await screen.findByTestId("screen-bms")).toBeInTheDocument();

    // Click Inverter tab and verify PCS screen
    const pcsLink = screen.getByText("Inverter");
    await user.click(pcsLink);
    expect(await screen.findByTestId("screen-pcs")).toBeInTheDocument();
  });

  it("highlights the active tab", async () => {
    renderWithRouter(
      <Sidebar
        showSettings={false}
        unackedCount={0}
        connectionStatus="disconnected"
      />,
      "/",
    );
    await waitFor(() => {
      const dashboardLink = screen.getByText("Dashboard").closest("a");
      expect(dashboardLink?.className).toMatch(/bg-accent/);
    });
  });
});

describe("ConnectionIndicator", () => {
  it("renders green dot when connected", () => {
    render(<ConnectionIndicator status="connected" />);
    const dot = screen.getByTestId("connection-dot");
    expect(dot.className).toContain("bg-success");
  });

  it("renders red dot when disconnected", () => {
    render(<ConnectionIndicator status="disconnected" />);
    const dot = screen.getByTestId("connection-dot");
    expect(dot.className).toContain("bg-danger");
  });

  it("renders yellow dot when reconnecting", () => {
    render(<ConnectionIndicator status="reconnecting" />);
    const dot = screen.getByTestId("connection-dot");
    expect(dot.className).toContain("bg-warning");
  });

  it("includes screen-reader-only text label", () => {
    render(<ConnectionIndicator status="connected" />);
    expect(screen.getByText("connected")).toBeInTheDocument();
  });
});

describe("Sidebar icons", () => {
  it("renders lucide-react icons for each tab", () => {
    renderWithRouter(
      <Sidebar
        showSettings={true}
        unackedCount={0}
        connectionStatus="disconnected"
      />,
    );
    const links = screen.getAllByRole("link");
    links.forEach((link) => {
      const svg = link.querySelector("svg");
      expect(svg).not.toBeNull();
    });
  });
});
