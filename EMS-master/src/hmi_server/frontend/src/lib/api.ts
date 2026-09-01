/** Error thrown on 401/403 responses (token expired or invalid). */
export class AuthError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "AuthError";
    this.status = status;
  }
}

/** Error thrown on non-OK responses (other than auth errors). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Fetch wrapper with auth headers and error handling.
 *
 * @param path - API path (e.g. "/api/control/mode")
 * @param token - Bearer token, or null for unauthenticated requests
 * @param options - Additional RequestInit options
 * @returns Parsed JSON response typed as T
 * @throws AuthError on 401/403
 * @throws ApiError on other non-OK responses
 */
export async function apiFetch<T>(
  path: string,
  token: string | null,
  options?: RequestInit,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const res: Response = await fetch(path, {
    ...options,
    headers: { ...headers, ...(options?.headers as Record<string, string> | undefined) },
  });

  if (res.status === 401 || res.status === 403) {
    const text: string = await res.text();
    throw new AuthError(res.status, text);
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail: string = (body as { detail?: string }).detail ?? "Unknown error";
    throw new ApiError(res.status, detail);
  }

  return res.json() as Promise<T>;
}
