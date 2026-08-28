import { useCallback } from "react";
import { apiFetch, AuthError } from "../lib/api";
import { useAuth } from "../context/AuthContext";

/**
 * Returns a fetch function that automatically injects the Bearer token
 * from AuthContext. On AuthError, calls logout to clear session.
 */
export function useApi(): {
  apiFetch: <T>(path: string, options?: RequestInit) => Promise<T>;
} {
  const { state, logout } = useAuth();

  const wrappedFetch = useCallback(
    async <T>(path: string, options?: RequestInit): Promise<T> => {
      try {
        return await apiFetch<T>(path, state.token, options);
      } catch (err: unknown) {
        if (err instanceof AuthError) {
          await logout();
        }
        throw err;
      }
    },
    [state.token, logout],
  );

  return { apiFetch: wrappedFetch };
}
