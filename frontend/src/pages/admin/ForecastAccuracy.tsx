import { useCallback, useEffect, useState } from 'react';
import { AlertTriangleIcon, PlayIcon, RefreshCwIcon, TargetIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import type { StatusTone } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Toggle } from '../../components/ui/Toggle';
import { ResponsiveTable } from '../../components/ui/Table';
import { api, ApiException } from '../../api/client';
import { plainStatus } from '../../lib/plainLanguage';

/**
 * Live forecast accuracy — `GET /api/v1/models/accuracy`.
 *
 * The registry's MAE is what a training run measured on its held-out window;
 * this screen is the other half of the question, and the one a manager asks:
 * how is the forecast we actually published doing against the demand that has
 * since been recorded? It is per location, against the window before it, and it
 * moves on its own as imports land — hence the poll.
 *
 * Nothing here is inferred. A day is scored only when its forecast was
 * published before the day began and its demand has been recorded; days the
 * rolling-mean fallback answered are counted and excluded rather than scored;
 * and a window with nothing to measure says so instead of showing a zero.
 */

/** Demand lands once a day, so a minute is already generous for a poll. */
const LIVE_POLL_MS = 60000;
const WINDOW_CHOICES = [28, 56, 90];

interface AccuracyMetrics {
  n: number;
  mae: number;
  wape_pct: number | null;
  bias: number;
  coverage_pct: number;
  mean_actual: number;
  fallback_days: number;
}

interface AccuracySignal {
  code: string;
  label: string;
  severity: string;
  detail: string;
}

interface AccuracyLocation extends AccuracyMetrics {
  location_id: string;
  name: string | null;
  status: string;
}

interface AccuracyPayload {
  evaluated_at: string;
  window_days: number;
  status: string;
  reason: string | null;
  target_coverage: number;
  reference: {
    model_version_id: string;
    algorithm: string;
    version: string;
    test_mae: number | null;
    test_end: string | null;
  } | null;
  overall: AccuracyMetrics | null;
  prior: AccuracyMetrics | null;
  signals: AccuracySignal[];
  locations: AccuracyLocation[];
}

const fmt = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits);

/** Signed, with a real minus sign rather than a hyphen. */
const signed = (value: number | null | undefined, digits = 1): string => {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return '—';
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(digits)}`;
};

const statusTone = (status?: string | null): StatusTone =>
  status === 'healthy' ? 'success' : status === 'watch' ? 'warning' : status === 'degraded' ? 'critical' : 'neutral';

/** A location the API did not name — a labelled short reference, never bare hex. */
const locationLabel = (row: { name?: string | null; location_id: string }): string =>
  row.name ?? `Location ${String(row.location_id).slice(0, 8)}`;

function errMeta(e: unknown) {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error' as const, title: 'Sign in required', desc: e.error.message };
    if (e.status === 403) return { variant: 'error' as const, title: 'Admin access required', desc: e.error.message };
    return {
      variant: 'error' as const,
      title: 'Failed to load accuracy',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  if (e instanceof TypeError) return { variant: 'offline' as const, title: 'You are offline', desc: 'Check your connection' };
  return { variant: 'offline' as const, title: 'Network error', desc: String(e) };
}

export function ForecastAccuracy() {
  const [data, setData] = useState<AccuracyPayload | null>(null);
  const [windowDays, setWindowDays] = useState(WINDOW_CHOICES[0]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta> | null>(null);
  const [live, setLive] = useState(true);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const fetchAccuracy = useCallback(async (days: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<AccuracyPayload>('/api/v1/models/accuracy', { window_days: days });
      setData(res ?? null);
    } catch (e) {
      setError(errMeta(e));
    } finally {
      setLoading(false);
      setCheckedAt(new Date());
    }
  }, []);

  useEffect(() => {
    fetchAccuracy(windowDays).catch(() => null);
  }, [fetchAccuracy, windowDays]);

  // The figure changes without the page being touched: yesterdays imports land,
  // a forecast's day passes. Poll rather than make the operator reload.
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => fetchAccuracy(windowDays).catch(() => null), LIVE_POLL_MS);
    return () => clearInterval(timer);
  }, [live, fetchAccuracy, windowDays]);

  if (loading && !data) return <LoadingState label="Loading accuracy" />;
  if (error)
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.desc}
        action={{ label: 'Retry', onClick: () => void fetchAccuracy(windowDays) }}
        size="md"
      />
    );

  const overall = data?.overall ?? null;
  const prior = data?.prior ?? null;
  const reference = data?.reference ?? null;
  const signals = data?.signals ?? [];
  const locations = data?.locations ?? [];

  /** A figure in the recent window set against the window before it. */
  const change = (key: keyof AccuracyMetrics, digits = 2) => {
    if (!overall || !prior) return null;
    const now = overall[key];
    const before = prior[key];
    if (typeof now !== 'number' || typeof before !== 'number') return null;
    return { value: now - before, digits };
  };
  const wapeChange = change('wape_pct');
  const worse = wapeChange != null && wapeChange.value > 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white">
          <TargetIcon className="h-5 w-5" strokeWidth={2} />
        </span>
        <div className="min-w-0">
          <h1 className="text-h4 font-bold tracking-tight text-slate-900">Forecast accuracy</h1>
          <p className="text-meta text-slate-500">
            How the published forecast did against the demand that has landed, by location, and against the window before it.
          </p>
        </div>
      </div>

      <Panel
        title="Live accuracy"
        icon={PlayIcon}
        description={`Last ${windowDays} days, with the ${windowDays} days before them beside it. A day is scored only once its forecast was published before the day began and its demand has been recorded.`}
        action={
          <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
            <span className="text-micro text-slate-500">{checkedAt ? `Checked ${checkedAt.toLocaleTimeString()}` : 'Checking…'}</span>
            <label className="flex items-center gap-1.5 text-micro font-semibold text-slate-600">
              Window
              <select
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value))}
                className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-caption font-medium text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                {WINDOW_CHOICES.map((days) => (
                  <option key={days} value={days}>
                    {days} days
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void fetchAccuracy(windowDays)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
            >
              <RefreshCwIcon className="h-3.5 w-3.5" strokeWidth={2} />
              Refresh
            </button>
            <span className="flex items-center gap-2">
              <span className="text-micro font-semibold text-slate-600">Auto-refresh</span>
              <Toggle checked={live} onChange={setLive} label="Auto-refresh" title="Re-read accuracy every minute as new demand lands" />
            </span>
          </div>
        }
      >
        {!data ? (
          <EmptyState variant="empty" size="sm" title="No reading yet" description="Accuracy has not been read from the API yet." />
        ) : overall === null ? (
          <EmptyState
            variant="empty"
            size="md"
            title="Nothing to score yet"
            description={data.reason ?? 'No published forecast could be measured against recorded demand in this window.'}
            hints={['A day counts once its forecast is older than the day and its demand is recorded', 'Days answered by a rolling-mean average are counted separately, never scored']}
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <StatusPill tone={statusTone(data.status)} dot>
                {plainStatus(data.status)}
              </StatusPill>
              <span className="text-meta text-slate-600">
                {overall.n} store-day{overall.n === 1 ? '' : 's'} scored
                {wapeChange
                  ? ` · error ${worse ? 'up' : 'down'} ${Math.abs(wapeChange.value).toFixed(1)} points against the window before`
                  : ''}
              </span>
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
              {([
                ['Average error', `${fmt(overall.mae)} a day`, prior ? `was ${fmt(prior.mae)}` : null],
                ['Error as share of demand', overall.wape_pct == null ? '—' : `${fmt(overall.wape_pct, 1)}%`, prior?.wape_pct != null ? `was ${fmt(prior.wape_pct, 1)}%` : null],
                ['Bias', `${signed(overall.bias)} a day`, overall.bias > 0 ? 'forecasts run high' : overall.bias < 0 ? 'forecasts run low' : 'centred'],
                ['Inside the published range', `${fmt(overall.coverage_pct, 1)}%`, `target ${fmt(data.target_coverage, 0)}%`],
              ] as [string, string, string | null][]).map(([label, value, note]) => (
                <div key={label}>
                  <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="text-body font-bold tabular-nums text-slate-900">{value}</dd>
                  {note && <dd className="text-fine text-slate-500">{note}</dd>}
                </div>
              ))}
            </dl>

            <div className="mt-3 flex flex-col gap-1.5 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-600">
              {reference && (
                <p>
                  <span className="font-semibold text-slate-800">Reference:</span>{' '}
                  {reference.test_mae == null
                    ? `${reference.version} has no held-out error recorded, so there is nothing to compare the live figure with.`
                    : `${reference.version} (${reference.algorithm}) measured ${fmt(reference.test_mae)} a day on its held-out window${reference.test_end ? ` ending ${reference.test_end}` : ''}.`}
                </p>
              )}
              <p>
                A typical scored day is {fmt(overall.mean_actual, 0)}.
                {overall.fallback_days > 0
                  ? ` ${overall.fallback_days} scored day${overall.fallback_days === 1 ? '' : 's'} in this window were answered with a rolling-mean average instead of the model, and are left out of the figures above.`
                  : ' No day in this window was answered by the rolling-mean fallback.'}
              </p>
            </div>
          </>
        )}
      </Panel>

      {signals.length > 0 && (
        <Panel
          title="What needs attention"
          icon={AlertTriangleIcon}
          description="Raised from this window by the accuracy check. Each names the figures behind it."
          action={<span className="text-micro text-slate-500">{signals.length} flag{signals.length === 1 ? '' : 's'}</span>}
        >
          <ul className="flex flex-col gap-2">
            {signals.map((signal) => (
              <li key={signal.code} className="rounded-xl border border-slate-200 px-3.5 py-3">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
                  <p className="text-body-xs font-semibold text-slate-900">{signal.label}</p>
                  <StatusPill tone={statusTone(signal.severity)}>{plainStatus(signal.severity)}</StatusPill>
                </div>
                <p className="mt-1 text-caption leading-relaxed text-slate-600">{signal.detail}</p>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        title="By location"
        icon={TargetIcon}
        description="Worst error first, so the place that needs attention is at the top."
        action={<span className="text-micro text-slate-500">{locations.length} location{locations.length === 1 ? '' : 's'}</span>}
      >
        {locations.length === 0 ? (
          <EmptyState
            variant="empty"
            size="sm"
            title="No location has been scored"
            description="No location has a published forecast with recorded demand behind it in this window yet."
            hints={['Demand enters through Data Ingestion or a connected source']}
          />
        ) : (
          <ResponsiveTable<AccuracyLocation>
            caption="Forecast accuracy by location"
            minWidth="min-w-[620px]"
            /* One row per location — a wide tenant has a long list here. */
            maxHeight="max-h-[420px]"
            rows={locations}
            rowKey={(row) => row.location_id}
            columns={[
              { key: 'location', label: 'Location', primary: true, render: (row) => <span className="text-meta font-semibold text-slate-900">{locationLabel(row)}</span> },
              { key: 'days', label: 'Days scored', render: (row) => <span className="tabular-nums">{row.n}</span> },
              { key: 'mae', label: 'Avg error', render: (row) => <span className="font-medium tabular-nums text-slate-900" title={`${row.mean_actual} a day is typical here`}>{fmt(row.mae)}</span> },
              { key: 'wape', label: 'Error share', render: (row) => <span className="tabular-nums">{row.wape_pct == null ? '—' : `${fmt(row.wape_pct, 1)}%`}</span> },
              { key: 'bias', label: 'Bias', render: (row) => <span className="tabular-nums">{signed(row.bias)}</span> },
              // Same range-hold bar as the Experiments windows list, per location:
              // the column names what it measures, the bar shows how much of the target
              // the site actually reached at a glance.
              { key: 'coverage', label: 'In range', render: (row) => (
                  <div className="min-w-[84px]">
                    <span className="tabular-nums">{row.coverage_pct == null ? '—' : `${fmt(row.coverage_pct, 0)}%`}</span>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200" title="Share of this location's scored days where demand fell inside the published range"><div className="h-full rounded-full bg-emerald-500" style={{width:`${Math.min(100, Math.max(0, Number(row.coverage_pct)||0))}%`}} /></div>
                  </div>) },
              { key: 'fallback', label: 'Fallback days', render: (row) => <span className="tabular-nums">{row.fallback_days}</span> },
              { key: 'status', label: 'Status', render: (row) => <StatusPill tone={statusTone(row.status)}>{plainStatus(row.status)}</StatusPill> },
            ]}
          />
        )}
        <p className="mt-2 text-fine text-slate-500">
          Error is the forecast minus the demand that was recorded for the same day; the share is that error against the location's own demand, so a quiet site and a busy one can be compared.
        </p>
      </Panel>
    </div>
  );
}
