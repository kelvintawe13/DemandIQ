/**
 * DemandIQ typed API client (VITE_API_URL-aware).
 *
 * - Base URL from `import.meta.env.VITE_API_URL` (docker-compose.yml:70)
 *   with fallback to http://localhost:8000.
 * - Attaches `Authorization: Bearer <token>` when a token is stored.
 * - Normalises the unified error shape `{error:{code,message,field,reference}}`.
 * - Exposes cursor-paginated helpers and typed domain calls:
 *   location / forecast / policy / quality / alert / report / webhook / governance.
 *
 * When `VITE_API_URL` is empty the caller can fall back to local mock data
 * (see `shouldUseMock()`).
 */

// ---------------------------------------------------------------------------
// Config & helpers
// ---------------------------------------------------------------------------

const API_BASE: string =
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ((import.meta as any)?.env?.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://localhost:8000";

export const apiBaseUrl = API_BASE;

export function shouldUseMock(): boolean {
  // Allow forcing mocks via env; otherwise use mock only when no base URL.
  const raw = (import.meta as unknown as { env?: Record<string, string> })?.env?.["VITE_API_URL"];
  return !raw;
}

// ---------------------------------------------------------------------------
// Auth token storage
// ---------------------------------------------------------------------------

const TOKEN_KEY = "demandiq_access_token";
const REFRESH_KEY = "demandiq_refresh_token";

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setTokens(access: string, refresh?: string | null): void {
  try {
    localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  } catch {
    // ignore storage errors (e.g. SSR)
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Error shape
// ---------------------------------------------------------------------------

export interface ApiErrorDetail {
  code: string;
  message: string;
  field: string | null;
  reference: string | null;
}

export class ApiError extends Error {
  status: number;
  detail: ApiErrorDetail;
  raw: unknown;

  constructor(status: number, detail: ApiErrorDetail, raw: unknown) {
    super(detail.message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.raw = raw;
  }
}

// ---------------------------------------------------------------------------
// Core fetch
// ---------------------------------------------------------------------------

export interface Paginated<T> {
  data: T[];
  next_cursor: string | null;
  has_more: boolean;
}

type FetchOpts = Omit<RequestInit, "body"> & {
  body?: unknown;
  auth?: boolean;
  rawBody?: boolean;
};

async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { auth = true, rawBody, body, headers, ...rest } = opts;

  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;

  const finalHeaders: Record<string, string> = {
    ...(headers as Record<string, string> | undefined),
  };

  if (!rawBody && body !== undefined && body !== null && !(body instanceof FormData)) {
    finalHeaders["Content-Type"] = "application/json";
  }

  if (auth) {
    const token = getAccessToken();
    if (token) finalHeaders["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(url, {
    ...rest,
    headers: finalHeaders,
    body:
      body === undefined || body === null
        ? undefined
        : rawBody || body instanceof FormData
          ? (body as BodyInit)
          : JSON.stringify(body),
  });

  const contentType = res.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload: unknown = isJson ? await res.json().catch(() => null) : await res.text().catch(() => null);

  if (!res.ok) {
    const errObj = payload as { error?: ApiErrorDetail } | null;
    const detail: ApiErrorDetail =
      errObj && typeof errObj === "object" && "error" in errObj && (errObj as { error: ApiErrorDetail }).error
        ? (errObj as { error: ApiErrorDetail }).error
        : {
            code: `http_${res.status}`,
            message: typeof payload === "string" ? payload : res.statusText,
            field: null,
            reference: null,
          };
    throw new ApiError(res.status, detail, payload);
  }

  return payload as T;
}

// Convenience: GET with query params

function toQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Domain types (mirroring backend schemas)
// ---------------------------------------------------------------------------

export interface Location {
  id: string;
  workspace_id: string;
  name: string;
  area: string | null;
  cuisine: string | null;
  opened_on: string | null;
  archived_at: string | null;
  created_at: string;
}

export interface Forecast {
  id: string;
  workspace_id: string;
  location_id: string;
  target_date: string;
  point: number;
  lower: number;
  upper: number;
  coverage_target: number;
  model_version_id: string;
  feature_set_version: string;
  as_of_timestamp: string;
  fallback: boolean;
  created_at: string;
}

export interface ForecastExplanation {
  forecast_id: string;
  attribution_type: string;
  disclaimer: string;
  contributions: Array<{ feature: string; value: string | null; impact: number; unit: string }>;
}

export interface Policy {
  id: string;
  kind: string;
  quantile: number | null;
  lookback_days: number | null;
  is_active: boolean;
}

export interface PolicySimResult {
  policy: string;
  /** null when the rule had no basis (e.g. no demand history) — see unavailable_reason. */
  prepared_qty: number | null;
  prepared_total?: number | null;
  surplus_sr: number | null;
  /** null until the target day has an observed outcome. */
  surplus_ps: number | null;
  unmet_ud: number | null;
  planned_shortfall?: number | null;
  /** "no_history" when the rule could not be computed, else null. */
  unavailable_reason?: string | null;
  surplus_days: number;
  unmet_days: number;
  /** Days in the period the rule could be worked out for (period simulation only). */
  prepared_days?: number;
  /** How many of those days have an outcome recorded — what PS/UD are measured on. */
  realised_days?: number;
  quantile: number | null;
  lookback_days: number | null;
  unit: string;
}

export interface QualityScore {
  overall: number;
  dimensions: Record<string, number>;
  open_issues: number;
}

export interface Alert {
  id: string;
  severity: string;
  title: string;
  body: string | null;
  read_at: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface ReportTemplate {
  id: string;
  name: string;
  description: string;
  cadence: string;
  format: string;
}

export interface Report {
  id: string;
  template: string;
  period_start: string | null;
  period_end: string | null;
  format: string;
  status: string;
  file_uri: string | null;
  signed_url?: string | null;
  download_url?: string | null;
  size_bytes: number | null;
  created_at: string;
}

export interface ReportSchedule {
  id: string;
  template: string;
  cadence: string;
  recipients: string[];
  format: string;
  enabled: boolean;
  next_run_at: string | null;
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  secret?: string;
  status: string;
  created_at: string;
}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event: string;
  response_code: number | null;
  attempt: number;
  delivered_at: string;
}

export interface ChangeRequest {
  id: string;
  workspace_id: string;
  kind: string;
  title: string;
  before_value: string | null;
  after_value: string | null;
  impact: string | null;
  status: string;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface Member {
  id: string;
  name: string;
  email: string;
  role: string;
  email_verified: boolean;
  created_at: string | null;
}

// ---------------------------------------------------------------------------
// API groups
// ---------------------------------------------------------------------------

export const locationApi = {
  list: (cursor?: string | null, limit = 20): Promise<Paginated<Location>> =>
    apiFetch<Paginated<Location>>(`/api/v1/locations${toQuery({ cursor, limit })}`),
  get: (id: string): Promise<Location> => apiFetch<Location>(`/api/v1/locations/${id}`),
  create: (payload: { name: string; area?: string; cuisine?: string }): Promise<Location> =>
    apiFetch<Location>(`/api/v1/locations`, { method: "POST", body: payload }),
};

export const forecastApi = {
  create: (payload: {
    location_id: string;
    target_date: string;
    horizon_days?: number;
    granularity?: string;
  }): Promise<Forecast> => apiFetch<Forecast>(`/api/v1/forecasts`, { method: "POST", body: payload }),
  list: (params: { location_id?: string; from?: string; to?: string; cursor?: string | null; limit?: number }): Promise<Paginated<Forecast>> =>
    apiFetch<Paginated<Forecast>>(`/api/v1/forecasts${toQuery(params as Record<string, string>)}`),
  get: (id: string): Promise<Forecast> => apiFetch<Forecast>(`/api/v1/forecasts/${id}`),
  explanation: (id: string): Promise<ForecastExplanation> =>
    apiFetch<ForecastExplanation>(`/api/v1/forecasts/${id}/explanation`),
  exportUrl: (id: string, format: "csv" | "xlsx" = "csv"): string => `${API_BASE}/api/v1/forecasts/${id}/export?format=${format}`,
};

export const policyApi = {
  list: (): Promise<{ data: Policy[] }> => apiFetch<{ data: Policy[] }>(`/api/v1/policies`),
  simulate: (payload: {
    location_id: string;
    period_start?: string;
    period_end?: string;
    quantile?: number;
    point?: number;
    lower?: number;
    upper?: number;
    target_date?: string;
  }): Promise<{ location_id: string; period_start: string; period_end: string; results: PolicySimResult[] }> =>
    apiFetch(`/api/v1/policies/simulate`, { method: "POST", body: payload }),
};

export const qualityApi = {
  score: (): Promise<QualityScore> => apiFetch<QualityScore>(`/api/v1/quality/score`),
  issues: (params?: { severity?: string; location_id?: string; cursor?: string | null; limit?: number }): Promise<Paginated<Record<string, unknown>>> =>
    apiFetch(`/api/v1/quality/issues${toQuery((params ?? {}) as Record<string, string>)}`),
  rules: (): Promise<{ data: unknown[] }> => apiFetch(`/api/v1/quality/rules`),
};

export const alertApi = {
  list: (params?: { status?: string; cursor?: string | null; limit?: number }): Promise<Paginated<Alert>> =>
    apiFetch(`/api/v1/alerts${toQuery((params ?? {}) as Record<string, string>)}`),
  markRead: (id: string): Promise<unknown> => apiFetch(`/api/v1/alerts/${id}/read`, { method: "POST" }),
  resolve: (id: string): Promise<unknown> => apiFetch(`/api/v1/alerts/${id}/resolve`, { method: "POST" }),
};

export const reportApi = {
  templates: (): Promise<{ data: ReportTemplate[] }> => apiFetch(`/api/v1/reports/templates`),
  generate: (payload: {
    template_id: string;
    period?: { start: string; end: string };
    format?: string;
    location_ids?: string[];
  }): Promise<{ report_id: string; status: string; id: string }> =>
    apiFetch(`/api/v1/reports/generate`, { method: "POST", body: payload }),
  get: (id: string): Promise<Report> => apiFetch<Report>(`/api/v1/reports/${id}`),
  list: (cursor?: string | null, limit = 20): Promise<Paginated<Report>> =>
    apiFetch<Paginated<Report>>(`/api/v1/reports${toQuery({ cursor, limit })}`),
  schedules: {
    list: (): Promise<{ data: ReportSchedule[] }> => apiFetch(`/api/v1/reports/schedules`),
    create: (payload: { template: string; cadence: string; recipients: string[]; format?: string }): Promise<ReportSchedule> =>
      apiFetch(`/api/v1/reports/schedules`, { method: "POST", body: payload }),
    patch: (id: string, payload: Partial<ReportSchedule>): Promise<ReportSchedule> =>
      apiFetch(`/api/v1/reports/schedules/${id}`, { method: "PATCH", body: payload }),
    remove: (id: string): Promise<void> => apiFetch<void>(`/api/v1/reports/schedules/${id}`, { method: "DELETE" }),
  },
};

export const webhookApi = {
  list: (): Promise<{ data: WebhookEndpoint[] }> => apiFetch(`/api/v1/webhooks`),
  create: (payload: { url: string; events: string[]; secret?: string }): Promise<WebhookEndpoint> =>
    apiFetch(`/api/v1/webhooks`, { method: "POST", body: payload }),
  patch: (id: string, payload: Partial<WebhookEndpoint>): Promise<WebhookEndpoint> =>
    apiFetch(`/api/v1/webhooks/${id}`, { method: "PATCH", body: payload }),
  remove: (id: string): Promise<void> => apiFetch<void>(`/api/v1/webhooks/${id}`, { method: "DELETE" }),
  test: (id: string): Promise<{ event: string; response_code: number; attempt: number }> =>
    apiFetch(`/api/v1/webhooks/${id}/test`, { method: "POST" }),
  deliveries: (id: string, cursor?: string | null, limit = 20): Promise<Paginated<WebhookDelivery>> =>
    apiFetch(`/api/v1/webhooks/${id}/deliveries${toQuery({ cursor, limit })}`),
  events: (): Promise<{ data: Array<{ id: string; label: string; detail: string }> }> =>
    apiFetch(`/api/v1/webhooks/events`),
};

export const governanceApi = {
  changeRequests: {
    list: (params?: { status?: string; kind?: string }): Promise<{ data: ChangeRequest[] }> =>
      apiFetch(`/api/v1/change-requests${toQuery((params ?? {}) as Record<string, string>)}`),
    create: (payload: { kind: string; title: string; before_value?: string; after_value?: string }): Promise<ChangeRequest> =>
      apiFetch(`/api/v1/change-requests`, { method: "POST", body: payload }),
    approve: (id: string): Promise<ChangeRequest> => apiFetch(`/api/v1/change-requests/${id}/approve`, { method: "POST" }),
    reject: (id: string): Promise<ChangeRequest> => apiFetch(`/api/v1/change-requests/${id}/reject`, { method: "POST" }),
  },
  members: {
    list: (): Promise<{ data: Member[] }> => apiFetch(`/api/v1/members`),
    invite: (payload: { email: string; role: string; name?: string }): Promise<Member> =>
      apiFetch(`/api/v1/members/invite`, { method: "POST", body: payload }),
    patch: (id: string, payload: { role?: string; name?: string }): Promise<Member> =>
      apiFetch(`/api/v1/members/${id}`, { method: "PATCH", body: payload }),
    remove: (id: string): Promise<void> => apiFetch<void>(`/api/v1/members/${id}`, { method: "DELETE" }),
  },
  roles: (): Promise<{ data: Array<{ role: string; permissions: string[] }> }> => apiFetch(`/api/v1/roles`),
  auditLog: (params?: { actor_id?: string; action?: string; cursor?: string | null; limit?: number }): Promise<Paginated<unknown>> =>
    apiFetch(`/api/v1/audit-log${toQuery((params ?? {}) as Record<string, string>)}`),
  pipeline: {
    runs: (): Promise<{ data: unknown[] }> => apiFetch(`/api/v1/pipeline/runs`),
    rerun: (id: string): Promise<unknown> => apiFetch(`/api/v1/pipeline/runs/${id}/rerun`, { method: "POST" }),
    drift: (): Promise<{ signals: unknown[]; overall: string }> => apiFetch(`/api/v1/pipeline/drift`),
    schedule: (): Promise<{ data: unknown[] }> => apiFetch(`/api/v1/pipeline/schedule`),
  },
};

// ---------------------------------------------------------------------------
// Fetch with mock fallback
// ---------------------------------------------------------------------------

export async function fetchWithMockFallback<T>(apiCall: () => Promise<T>, mock: T): Promise<T> {
  if (shouldUseMock()) return mock;
  try {
    return await apiCall();
  } catch (err) {
    // Network errors fall back to mock so the UI remains usable offline
    if (err instanceof ApiError && err.status >= 500) return mock;
    if (err instanceof TypeError) return mock; // fetch network failure
    throw err;
  }
}
