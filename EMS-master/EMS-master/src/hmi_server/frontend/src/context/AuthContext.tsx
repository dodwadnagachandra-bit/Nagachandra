import { createContext, useCallback, useContext, useState } from "react";
import { apiFetch, AuthError } from "../lib/api";
import type { LoginResponse } from "../types/api";

/** Auth state shape. Token held in memory only (kiosk sessions are ephemeral). */
export interface AuthState {
  token: string | null;
  level: "operator" | "admin" | null;
  expiresAt: number | null;
}

interface AuthContextValue {
  state: AuthState;
  login: (pin: string) => Promise<void>;
  logout: () => Promise<void>;
}

const initialAuthState: AuthState = {
  token: null,
  level: null,
  expiresAt: null,
};

const AuthContext = createContext<AuthContextValue | null>(null);

/** Provides auth state and login/logout functions to children. */
export function AuthProvider({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const [state, setState] = useState<AuthState>(initialAuthState);

  const login = useCallback(async (pin: string): Promise<void> => {
    try {
      const resp: LoginResponse = await apiFetch<LoginResponse>(
        "/api/auth/login",
        null,
        {
          method: "POST",
          body: JSON.stringify({ pin }),
        },
      );
      setState({
        token: resp.token,
        level: resp.level,
        expiresAt: Date.now() + resp.expires_in * 1000,
      });
    } catch (err: unknown) {
      setState(initialAuthState);
      if (err instanceof AuthError) {
        // Auth error -- state already cleared
        return;
      }
      throw err;
    }
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    const currentToken: string | null = state.token;
    setState(initialAuthState);
    try {
      await apiFetch<unknown>("/api/auth/logout", currentToken, {
        method: "POST",
      });
    } catch {
      // Logout failed -- state already cleared, best-effort
    }
  }, [state.token]);

  return (
    <AuthContext.Provider value={{ state, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/** Access auth context. Throws if used outside AuthProvider. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
