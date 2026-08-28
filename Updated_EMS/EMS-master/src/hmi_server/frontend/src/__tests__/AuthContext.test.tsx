import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { AuthProvider, useAuth } from "../context/AuthContext";

// Helper component to expose auth state for testing
function AuthConsumer(): React.ReactElement {
  const { state, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="token">{state.token ?? "null"}</span>
      <span data-testid="level">{state.level ?? "null"}</span>
      <button data-testid="login" onClick={() => login("1234")}>
        Login
      </button>
      <button data-testid="logout" onClick={() => logout()}>
        Logout
      </button>
    </div>
  );
}

describe("AuthContext", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it("starts with null token and level", () => {
    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );
    expect(screen.getByTestId("token").textContent).toBe("null");
    expect(screen.getByTestId("level").textContent).toBe("null");
  });

  it("stores token and level after successful login", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          token: "test-token-abc",
          level: "operator",
          expires_in: 1800,
        }),
    });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await user.click(screen.getByTestId("login"));

    expect(await screen.findByText("test-token-abc")).toBeInTheDocument();
    expect(screen.getByTestId("level").textContent).toBe("operator");
  });

  it("clears token on logout", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            token: "test-token",
            level: "admin",
            expires_in: 1800,
          }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await user.click(screen.getByTestId("login"));
    expect(await screen.findByText("test-token")).toBeInTheDocument();

    await user.click(screen.getByTestId("logout"));

    // Wait for token to be cleared
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByTestId("token").textContent).toBe("null");
    expect(screen.getByTestId("level").textContent).toBe("null");
  });

  it("clears state on 401 login response", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: () => Promise.resolve("Unauthorized"),
    });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await user.click(screen.getByTestId("login"));

    // Should remain null after failed login
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByTestId("token").textContent).toBe("null");
  });

  it("sends login request with correct body", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          token: "t",
          level: "operator",
          expires_in: 60,
        }),
    });
    globalThis.fetch = mockFetch;

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await user.click(screen.getByTestId("login"));
    await vi.advanceTimersByTimeAsync(100);

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ pin: "1234" }),
      }),
    );
  });
});
