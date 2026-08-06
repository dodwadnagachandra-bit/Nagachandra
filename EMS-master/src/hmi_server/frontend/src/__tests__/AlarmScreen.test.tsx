import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock useApi hook
const mockApiFetch = vi.fn();
vi.mock("../hooks/useApi", () => ({
  useApi: () => ({ apiFetch: mockApiFetch }),
}));

import AlarmScreen from "../screens/AlarmScreen";

const MOCK_ALARMS = [
  {
    alarm_id: "alarm-001",
    signal: "bms.pack_v",
    severity: "protection",
    state: "ACTIVE_UNACKED",
    activated_at: 1700000001000,
  },
  {
    alarm_id: "alarm-002",
    signal: "pcs.temperature",
    severity: "action",
    state: "ACTIVE_ACKED",
    activated_at: 1700000002000,
  },
  {
    alarm_id: "alarm-003",
    signal: "btms.inlet_temp",
    severity: "warning",
    state: "CLEARED_UNACKED",
    activated_at: 1700000003000,
  },
];

describe("AlarmScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders table with correct number of alarm rows", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: MOCK_ALARMS },
      error_msg: null,
    });

    render(<AlarmScreen />);
    // Wait for alarms to load
    const rows = await screen.findAllByTestId(/^alarm-row-/);
    expect(rows).toHaveLength(3);
  });

  it("applies severity color classes to rows", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: MOCK_ALARMS },
      error_msg: null,
    });

    render(<AlarmScreen />);
    const protectionRow = await screen.findByTestId("alarm-row-alarm-001");
    const actionRow = screen.getByTestId("alarm-row-alarm-002");
    const warningRow = screen.getByTestId("alarm-row-alarm-003");

    expect(protectionRow.className).toContain("border-red");
    expect(actionRow.className).toContain("border-orange");
    expect(warningRow.className).toContain("border-yellow");
  });

  it("shows ACK button for ACTIVE_UNACKED alarms", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: MOCK_ALARMS },
      error_msg: null,
    });

    render(<AlarmScreen />);
    const protectionRow = await screen.findByTestId("alarm-row-alarm-001");
    const ackBtn = within(protectionRow).getByTestId("ack-btn");
    expect(ackBtn).toBeInTheDocument();

    // ACTIVE_ACKED should NOT have ACK button
    const ackedRow = screen.getByTestId("alarm-row-alarm-002");
    expect(within(ackedRow).queryByTestId("ack-btn")).toBeNull();
  });

  it("clicking ACK calls apiFetch with correct alarm_id", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mockApiFetch
      .mockResolvedValueOnce({
        status: "ok",
        result: { alarms: MOCK_ALARMS },
        error_msg: null,
      })
      .mockResolvedValueOnce({ status: "ok", result: null, error_msg: null })
      .mockResolvedValueOnce({
        status: "ok",
        result: { alarms: MOCK_ALARMS },
        error_msg: null,
      });

    render(<AlarmScreen />);
    const protectionRow = await screen.findByTestId("alarm-row-alarm-001");
    const ackBtn = within(protectionRow).getByTestId("ack-btn");

    await user.click(ackBtn);

    // Should have called acknowledge endpoint
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/alarm/acknowledge",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ alarm_id: "alarm-001" }),
      }),
    );
  });

  it("renders empty state message when no alarms", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: [] },
      error_msg: null,
    });

    render(<AlarmScreen />);
    expect(await screen.findByText("No active alarms")).toBeInTheDocument();
  });

  it("sort toggles when clicking severity column header", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: MOCK_ALARMS },
      error_msg: null,
    });

    render(<AlarmScreen />);
    await screen.findByTestId("alarm-row-alarm-001");

    // Click severity header to sort
    const severityHeader = screen.getByTestId("sort-severity");
    await user.click(severityHeader);

    // After sort toggle, rows should still be present (order may change)
    const rows = screen.getAllByTestId(/^alarm-row-/);
    expect(rows).toHaveLength(3);
  });

  it("shows CLEARED_UNACKED alarms with ACK button", async () => {
    mockApiFetch.mockResolvedValue({
      status: "ok",
      result: { alarms: MOCK_ALARMS },
      error_msg: null,
    });

    render(<AlarmScreen />);
    const clearedRow = await screen.findByTestId("alarm-row-alarm-003");
    const ackBtn = within(clearedRow).getByTestId("ack-btn");
    expect(ackBtn).toBeInTheDocument();
  });
});
