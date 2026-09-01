import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { useWebSocket } from "../hooks/useWebSocket";
import type { TelemetryAction } from "../types/telemetry";

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 0; // CONNECTING
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  simulateOpen(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  simulateMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  simulateClose(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("useWebSocket", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sets status to 'reconnecting' on initial connect attempt", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    const { result } = renderHook(() => useWebSocket("/ws/telemetry", dispatch));
    expect(result.current).toBe("reconnecting");
  });

  it("sets status to 'connected' on WebSocket open", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    const { result } = renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    act(() => {
      MockWebSocket.instances[0].simulateOpen();
    });

    expect(result.current).toBe("connected");
  });

  it("dispatches UPDATE_TOPIC action on message", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    act(() => {
      MockWebSocket.instances[0].simulateOpen();
      MockWebSocket.instances[0].simulateMessage({
        topic: "system",
        data: { total_soc: 85 },
        ts: 1000,
      });
    });

    expect(dispatch).toHaveBeenCalledWith({
      type: "UPDATE_TOPIC",
      topic: "system",
      data: { total_soc: 85 },
      ts: 1000,
    });
  });

  it("reconnects with exponential backoff on close", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    // Initial connection
    expect(MockWebSocket.instances).toHaveLength(1);

    // Close -> 1s delay -> reconnect
    act(() => {
      MockWebSocket.instances[0].simulateClose();
    });
    expect(MockWebSocket.instances).toHaveLength(1); // Not yet reconnected

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(2); // 1s backoff

    // Close -> 2s delay -> reconnect
    act(() => {
      MockWebSocket.instances[1].simulateClose();
    });
    act(() => {
      vi.advanceTimersByTime(1999);
    });
    expect(MockWebSocket.instances).toHaveLength(2); // Not yet
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(3); // 2s backoff

    // Close -> 4s delay
    act(() => {
      MockWebSocket.instances[2].simulateClose();
    });
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(MockWebSocket.instances).toHaveLength(4); // 4s backoff
  });

  it("caps backoff at 30s", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    // Close enough times to exceed 30s: 1, 2, 4, 8, 16, 32->30
    for (let i = 0; i < 5; i++) {
      act(() => {
        MockWebSocket.instances[MockWebSocket.instances.length - 1].simulateClose();
      });
      const delay = Math.min(1000 * Math.pow(2, i), 30000);
      act(() => {
        vi.advanceTimersByTime(delay);
      });
    }

    // Now retry 5 (index 5): delay should be min(32000, 30000) = 30000
    const countBefore = MockWebSocket.instances.length;
    act(() => {
      MockWebSocket.instances[MockWebSocket.instances.length - 1].simulateClose();
    });
    act(() => {
      vi.advanceTimersByTime(29999);
    });
    expect(MockWebSocket.instances).toHaveLength(countBefore); // Not yet
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(countBefore + 1); // 30s cap
  });

  it("resets retry counter on successful connection", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    // Connect successfully then close
    act(() => {
      MockWebSocket.instances[0].simulateOpen();
    });
    act(() => {
      MockWebSocket.instances[0].simulateClose();
    });

    // Should retry after 1s (retries reset to 0 on open)
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("cleans up WebSocket and timeout on unmount", () => {
    const dispatch = vi.fn<[TelemetryAction], void>();
    const { unmount } = renderHook(() => useWebSocket("/ws/telemetry", dispatch));

    const ws = MockWebSocket.instances[0];
    expect(ws.closed).toBe(false);

    unmount();
    expect(ws.closed).toBe(true);
  });
});
