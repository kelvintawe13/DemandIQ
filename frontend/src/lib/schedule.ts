import { api } from '../api/client';

/**
 * Job cadence, last run and next tick — `GET /api/v1/pipeline/schedule`.
 *
 * Shared by Pipeline Health (all jobs) and Sources (the ingestion jobs), so the
 * two pages cannot disagree about what "next run" means or how a job name is
 * spelled out.
 */
export interface ScheduleRow {
  job_type: string;
  cadence: string;
  cron: string;
  last_run?: {
    status: string;
    rows_processed?: number | null;
    duration_ms?: number | null;
    started_at?: string | null;
    detail?: string | null;
  } | null;
  next_run?: string | null;
}

/**
 * Fetch the job schedule.
 *
 * Returns `null` rather than throwing when the caller may not read pipeline
 * information (a data clerk gets 403) or the request fails — callers hide the
 * panel instead of rendering a plausible-looking cadence they made up.
 */
export async function fetchSchedule(): Promise<ScheduleRow[] | null> {
  try {
    const res = await api.get<{ data: ScheduleRow[] }>('/api/v1/pipeline/schedule');
    return Array.isArray(res?.data) ? res.data : null;
  } catch {
    return null;
  }
}

/** `connector_sync` → `Connector sync`. */
export const jobLabel = (jobType: string) =>
  jobType.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** `in 6h` / `in 12m`, the clock time beyond a day, `—` when unknown. */
export function nextRunLabel(iso?: string | null): string {
  if (!iso) return '—';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '—';
  const mins = Math.round((when.getTime() - Date.now()) / 60000);
  if (mins <= 0) return 'due now';
  if (mins < 60) return `in ${mins}m`;
  if (mins < 24 * 60) return `in ${Math.round(mins / 60)}h`;
  return when.toLocaleString();
}

/** Tone for a run status, so both pages colour `failed` the same way. */
export const runStatusTone = (
  status?: string,
): 'success' | 'info' | 'warning' | 'critical' | 'neutral' => {
  switch (status) {
    case 'success':
    case 'completed':
      return 'success';
    case 'running':
      return 'info';
    case 'failed':
    case 'error':
      return 'critical';
    case 'queued':
    case 'pending':
      return 'warning';
    default:
      return 'neutral';
  }
};
