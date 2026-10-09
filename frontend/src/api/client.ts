/**
 * DemandIQ — typed API client. Single fetch wrapper for all live calls.
 * Handles VITE_API_URL, Bearer, x-request-id, unified {error} envelope and EmptyState mapping.
 */
import { notifySessionExpired } from '../lib/sessionExpiry';

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

type ApiError = { code: string; message: string; field?: string | null; reference?: string | null; details?: unknown };

function getToken(): string | null {
  try {
    return localStorage.getItem("demandiq_token");
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem("demandiq_token", token);
    else localStorage.removeItem("demandiq_token");
  } catch {}
}

export function getAuthHeader(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export class ApiException extends Error {
  status: number;
  error: ApiError;
  reference?: string | null;
  constructor(status: number, error: ApiError) {
    super(error.message || `Request failed ${status}`);
    this.status = status;
    this.error = error;
    this.reference = error.reference;
  }
}

// --- Silent access-token refresh -------------------------------------------
// On a 401 (expired access token) the client exchanges the persisted refresh
// token once, replays the original request, and notifies the session layer.
// Single-flight: concurrent 401s await the same in-flight refresh.

let refreshInFlight: Promise<boolean> | null = null;
let on401Handler: (() => void) | null = null;

/** Register the callback fired when a session can no longer be recovered. */
export function setOn401(handler: (() => void) | null) {
  on401Handler = handler;
}

function getRefreshToken(): string | null {
  try {
    return localStorage.getItem('demandiq_refresh_token');
  } catch {
    return null;
  }
}

async function performRefresh(): Promise<boolean> {
  const refresh_token = getRefreshToken();
  if (!refresh_token) return false;
  try {
    const res = await fetch(`${BASE}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ refresh_token }),
    });
    if (!res.ok) return false;
    // The backend rotates refresh tokens: every response carries a fresh
    // refresh_token and the presented one is revoked server-side.
    const data = (await res.json()) as { access_token?: string; refresh_token?: string };
    if (!data.access_token) return false;
    setToken(data.access_token);
    try {
      if (data.refresh_token) localStorage.setItem('demandiq_refresh_token', data.refresh_token);
      else localStorage.removeItem('demandiq_refresh_token');
    } catch {}
    return true;
  } catch {
    return false;
  }
}

/** Refresh once (single-flight across concurrent 401s). */
function refreshAccessToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function request<T>(path: string, init: RequestInit & { query?: Record<string, string | number | boolean | undefined> } = {}, isRetry = false): Promise<T> {
  const { query, headers: extraHeaders, ...rest } = init as any;
  let url = `${BASE}${path}`;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...getAuthHeader(),
    ...(extraHeaders as Record<string, string> | undefined),
  };
  // Do not send Content-Type for FormData
  if (rest.body instanceof FormData) delete (headers as any)["Content-Type"];

  // Cap hung sockets so auth boot (and every other call) cannot wait forever.
  // Callers may still pass their own AbortSignal.
  const signal =
    (rest as RequestInit).signal ??
    (typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal
      ? AbortSignal.timeout(20_000)
      : undefined);
  const res = await fetch(url, { ...rest, headers, signal });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    // Expired access token → one silent refresh + one replay of the original
    // request. Only for bearer-authenticated calls, and only once per request
    // (isRetry) so a genuinely invalid token cannot loop.
    if (
      res.status === 401 &&
      !isRetry &&
      getAuthHeader().Authorization &&
      getRefreshToken() &&
      !path.startsWith('/api/v1/auth/login') &&
      !path.startsWith('/api/v1/auth/register') &&
      !path.startsWith('/api/v1/auth/refresh') &&
      !path.startsWith('/api/v1/auth/logout')
    ) {
      if (await refreshAccessToken()) {
        return request<T>(path, init, true);
      }
      // Refresh failed — the session is unrecoverable. Clear credentials,
      // notify the session layer, and raise the expiry UX (toast + redirect).
      setToken(null);
      on401Handler?.();
      notifySessionExpired();
    }
    const err: ApiError = data?.error ?? { code: `http_${res.status}`, message: res.statusText || "Request failed", reference: res.headers.get("x-request-id") };
    throw new ApiException(res.status, err);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Record<string, any>, init?: RequestInit) => request<T>(path, { method: "GET", query, ...init }),
  post: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined, ...init }),
  postForm: <T>(path: string, form: FormData, init?: RequestInit) => request<T>(path, { method: "POST", body: form as any, ...init }),
  patch: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined, ...init }),
  delete: <T>(path: string, init?: RequestInit) => request<T>(path, { method: "DELETE", ...init }),
  put: <T>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined, ...init }),
};

// Convenience typed helpers for common envelopes
export type Paginated<T> = { data: T[]; has_more?: boolean; next_cursor?: string | null };

// EmptyState helpers — map status to variant
export function emptyVariantFor(status: number | null, hasData: boolean): "empty" | "error" | "offline" | "search" {
  if (hasData) return "empty";
  if (status === null) return "offline";
  if (status >= 500) return "error";
  if (status === 404) return "search";
  return "empty";
}
