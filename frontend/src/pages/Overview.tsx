import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  UploadIcon,
  ZapIcon,
  FileTextIcon,
  ArrowUpRightIcon,
  ArrowRightIcon,
  ArrowDownIcon,
  DatabaseIcon,
  GaugeIcon,
  TriangleAlertIcon,
  PlugZapIcon,
  ActivityIcon } from
'lucide-react';
import { Panel } from '../components/ui/Panel';
import { StatusPill } from '../components/ui/StatusPill';
import { TodayPulseChart, type PulsePoint } from '../components/overview/TodayPulseChart';
import { CalendarClock } from '../components/ui/CalendarClock';
import { EmptyState } from '../components/ui/EmptyState';
import { PageSkeleton } from '../components/ui/Skeleton';
import { api, ApiException } from '../api/client';
import { useSession } from '../contexts/SessionContext';
import { jobLabel } from '../lib/schedule';
import {
  auditActionLabel,
  auditClientLabel,
  auditDetailLabel,
  auditTargetLabel,
  plainStatus,
  qualityDimensionHint,
  qualityDimensionLabel,
} from '../lib/plainLanguage';

interface OverviewProps {
  onNavigate: (id: string) => void;
}

const activityIcon = {
  import: DatabaseIcon,
  forecast: GaugeIcon,
  alert: TriangleAlertIcon,
  source: PlugZapIcon
} as const;

type ActivityKind = keyof typeof activityIcon;

interface ActivityEntry {
  id: string;
  title: string;
  detail: string;
  time: string;
  kind: ActivityKind;
}

// API shapes
type SummaryRes = {
  period: { start: string; end: string };
  previous_period: { start: string; end: string };
  total_visitors: number;
  total_visitors_previous: number;
  total_visitors_change_pct?: number | null;
  average_daily_demand: number;
  average_daily_demand_previous: number;
  average_daily_demand_change_pct?: number | null;
  peak_day_visitors: number;
  peak_day_visitors_previous: number;
  peak_day_visitors_change_pct?: number | null;
  peak_day_date?: string | null;
  growth_rate: number;
  growth_rate_delta_pp?: number | null;
};

type QualityRes = {
  overall: number;
  dimensions: Record<string, number>;
  open_issues: number;
};

type AlertsRes = {
  data: {
    id: string;
    title: string;
    body?: string;
    detail?: string;
    severity: 'critical' | 'warning' | 'info' | string;
    created_at?: string;
    time?: string;
    source?: string;
    read_at?: string | null;
    resolved_at?: string | null;
  }[];
  has_more?: boolean;
  next_cursor?: string | null;
};

type TrendRes = {
  data: { label: string; current: number; previous: number; date?: string | null }[];
  period: { start: string; end: string };
  previous_period: { start: string; end: string };
  granularity?: string;
};

type PipelineRun = {
  id: string;
  job_type: string;
  status: string;
  rows_processed?: number;
  duration_ms?: number;
  started_at?: string;
  created_at?: string;
};

type PipelineRes = { data: PipelineRun[] };

type AuditRes = {
  data: {
    id: string;
    action: string;
    target?: string | null;
    actor_id?: string;
    client?: string;
    created_at: string;
    meta_data?: Record<string, unknown> | null;
  }[];
};

type ForecastRes = { data: { id: string; model_version_id: string; point: number }[] };

function errorMeta(err: unknown): { variant: 'error' | 'offline'; title: string; description: string; hints?: string[] } {
  if (err instanceof ApiException) {
    if (err.status === 401) return { variant: 'error', title: 'Sign in required', description: err.error.message || 'Your session expired. Sign in again.', hints: err.reference ? [`ref: ${err.reference}`] : undefined };
    if (err.status === 403) return { variant: 'error', title: 'Access denied', description: err.error.message || 'Administrator access required.', hints: err.reference ? [`ref: ${err.reference}`] : undefined };
    return { variant: 'error', title: 'Failed to load overview', description: err.error.message + (err.reference ? ` (ref: ${err.reference})` : ''), hints: err.reference ? [`ref: ${err.reference}`] : undefined };
  }
  if (err instanceof TypeError) return { variant: 'offline', title: 'You are offline', description: 'Check your connection and try again.', hints: ['Verify VITE_API_URL is set', 'Check network connectivity'] };
  // navigator offline hint
  if (typeof navigator !== 'undefined' && !navigator.onLine) return { variant: 'offline', title: 'You are offline', description: 'Check your connection and try again.', hints: ['Verify VITE_API_URL is set', 'Check network connectivity'] };
  return { variant: 'offline', title: 'Network error', description: String(err) };
}

function toRelativeTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso ?? '—';
  const diff = Date.now() - d.getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs===1?'':'s'} ago`;
  const days = Math.round(hrs / 24);
  if (days === 1) return 'Yesterday';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function mapAuditToActivity(a: AuditRes['data'][number]): ActivityEntry {
  const action = a.action ?? 'activity';
  // `action` is the literal request line (`POST /api/v1/agent/messages`) — correct
  // for an audit trail, unreadable on a dashboard — and `target` is the same path,
  // so both go through the plain-language layer rather than reaching the screen raw.
  const target = auditTargetLabel(a.target);
  const via = auditClientLabel(a.client) ? `from ${auditClientLabel(a.client)}` : '';
  let kind: ActivityKind = 'import';
  let title = auditActionLabel(action);
  let detail = auditDetailLabel(a);
  if (action.includes('forecast')) { kind = 'forecast'; title = 'Forecast generated'; detail = target ? `forecast ${target.slice(0,8)}` : 'forecast run'; }
  else if (action.includes('ingest') || action.includes('import') || action.includes('upload')) { kind = 'import'; title = 'Demand imported'; detail = target ? `upload ${target.slice(0,8)}` : 'ingest run'; }
  else if (action.includes('alert')) { kind = 'alert'; title = 'Alert event'; detail = via || title; }
  else if (action.includes('source') || action.includes('connector') || action.includes('sync')) { kind = 'source'; title = 'Source sync'; detail = target ? target.slice(0,24) : via || title; }
  return { id: a.id, title, detail: detail || via || title, time: toRelativeTime(a.created_at), kind };
}

function mapPipelineToActivity(r: PipelineRun): ActivityEntry {
  let kind: ActivityKind = 'import';
  if (r.job_type?.includes('forecast')) kind = 'forecast';
  else if (r.job_type?.includes('alert') || r.job_type?.includes('quality')) kind = 'alert';
  else if (r.job_type?.includes('source') || r.job_type?.includes('connector') || r.job_type?.includes('sync')) kind = 'source';
  else if (r.job_type?.includes('ingest')) kind = 'import';
  // `feature_materialisation running` means nothing to an operator; "Build features
  // — running now" does. `jobLabel` is shared with the Sources/Schedule screens.
  const title = r.job_type ? `${jobLabel(r.job_type)} · ${plainStatus(r.status)}` : `Data job · ${plainStatus(r.status)}`;
  const detail = `${r.rows_processed ?? 0} rows processed`;
  const time = toRelativeTime(r.started_at ?? r.created_at ?? null);
  return { id: r.id, title, detail, time, kind };
}

export function Overview({ onNavigate }: OverviewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errorMeta> | null>(null);
  const [summary, setSummary] = useState<SummaryRes | null>(null);
  const [quality, setQuality] = useState<QualityRes | null>(null);
  const [alerts, setAlerts] = useState<AlertsRes['data']>([]);
  const [pulseData, setPulseData] = useState<PulsePoint[]>([]);
  const [trendPeriod, setTrendPeriod] = useState<TrendRes['period'] | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [modelVersion, setModelVersion] = useState<string | null>(null);
  const { user } = useSession();

  // Capabilities published by /auth/me. Optional panels (alerts, pipeline,
  // audit) are gated on these so a role that has no right to them never fires
  // the request, instead of firing it and collecting a 403.
  const permissions = useMemo(() => user?.permissions ?? [], [user?.permissions]);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Optional panels are gated on the capabilities /auth/me publishes, so a
      // role without them never fires the request at all (and never sees a 403
      // in the console). Everything in this batch is required, so the page
      // still fails loudly if the core reads break.
      const may = (cap: string) => permissions.includes(cap);
      const [s, q, a, t, p] = await Promise.all([
        api.get<SummaryRes>('/api/v1/analytics/summary', { range: '7d' }),
        api.get<QualityRes>('/api/v1/quality/score'),
        may('alerts:read') ?
        api.get<AlertsRes>('/api/v1/alerts', { status: 'unread', limit: 5 }) :
        Promise.resolve({ data: [] } as AlertsRes),
        api.get<TrendRes>('/api/v1/analytics/trend', { range: '7d', granularity: 'daily' }),
        may('pipeline:read') ?
        api.
        get<PipelineRes>('/api/v1/pipeline/runs', { limit: 3 }).
        catch(() => null as PipelineRes | null) :
        Promise.resolve(null as PipelineRes | null),
      ]);
      const pipeRows = p?.data ?? [];
      setSummary(s);
      setQuality(q);
      setAlerts(a.data ?? []);

      // TodayPulseChart data: map trend.current/previous -> PulsePoint, fallback to forecasts if empty
      let pulse: PulsePoint[] = [];
      if (t?.data && t.data.length > 0) {
        pulse = t.data.map((d) => ({
          label: d.label ?? d.date ?? '',
          actual: typeof d.current === 'number' ? d.current : null,
          predicted: typeof d.previous === 'number' ? d.previous : 0,
        }));
        setTrendPeriod(t.period);
      } else {
        // fallback: try forecasts
        try {
          const fc = await api.get<ForecastRes>('/api/v1/forecasts', { limit: 5 });
          const fcPulse: PulsePoint[] = (fc.data ?? []).map((f) => ({
            label: (f as any).target_date ? String((f as any).target_date).slice(5) : String(f.id).slice(0,5),
            actual: null,
            predicted: (f as any).point ?? (f as any).point_forecast ?? 0,
          }));
          pulse = fcPulse;
          if (fc.data?.[0]?.model_version_id) setModelVersion(fc.data[0].model_version_id);
        } catch {
          pulse = [];
        }
      }
      setPulseData(pulse);

      // model_version from forecasts if not yet set
      if (!modelVersion) {
        try {
          const fc2 = await api.get<ForecastRes>('/api/v1/forecasts', { limit: 1 });
          if (fc2.data?.[0]?.model_version_id) setModelVersion(fc2.data[0].model_version_id);
        } catch { /* ignore */ }
      }

      // Recent activity: audit-log when the role holds audit:read
      // (owner/administrator), otherwise the pipeline rows it may already see.
      let acts: ActivityEntry[] = [];
      if (may('audit:read')) {
        try {
          const audit = await api.get<AuditRes>('/api/v1/audit-log', { limit: 5 });
          acts = (audit.data ?? []).map(mapAuditToActivity).slice(0, 5);
        } catch (e) {
          if (!(e instanceof ApiException) || e.status >= 500) throw e;
        }
      }
      if (acts.length === 0) acts = pipeRows.slice(0, 5).map(mapPipelineToActivity);
      setActivity(acts);
    } catch (e) {
      setError(errorMeta(e));
    } finally {
      setLoading(false);
    }
    // `modelVersion` is deliberately not a dependency: it is reassigned inside
    // this callback, so depending on it re-enters the callback forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permissions]);

  useEffect(() => { void fetchAll(); }, [fetchAll]);

  if (loading) {
    return <PageSkeleton label="Loading overview" />;
  }

  if (error) {
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.description}
        hints={error.hints}
        action={{ label: 'Retry', onClick: () => void fetchAll() }}
        size="md"
      />
    );
  }

  const hasSummary = !!summary;
  const hasTrend = pulseData.length > 0;
  const hasAlerts = alerts.length > 0;
  const hasActivity = activity.length > 0;
  const isEmpty = hasSummary && summary!.total_visitors === 0 && !hasTrend && !hasAlerts && !hasActivity;
  // also treat no summary as empty only if other signals empty
  const showOverallEmpty = isEmpty || (!hasSummary && !hasTrend && !hasAlerts && quality?.overall === 0);

  if (showOverallEmpty) {
    return (
      <EmptyState
        variant="empty"
        title="No data yet"
        description="Import demand data to generate your overview. Every number shows the period it covers and where it came from."
        hints={['Ingest demand first', 'Wait for nightly materialisation']}
        action={{ label: 'Go to ingestion', onClick: () => onNavigate('ingestion') }}
        size="md"
      />
    );
  }

  // Derived KPIs
  // "Demand per day" is the 7-day average the API actually returns — it is not a
  // forecast for today, and calling it "Predicted visitors today" made operators
  // read a backward-looking average as a forward-looking prediction.
  const demandPerDay = summary ? Math.round(summary.average_daily_demand).toLocaleString('en-US') : '—';
  const changePct = summary?.total_visitors_change_pct ?? summary?.average_daily_demand_change_pct ?? null;
  const changeLabel = changePct != null ? `${changePct > 0 ? '+' : ''}${changePct.toFixed(1)}%` : '—';
  const isPositive = (changePct ?? 0) >= 0;
  const visitorsInPeriod = summary ? summary.total_visitors.toLocaleString('en-US') : '—';
  const peakDate = summary?.peak_day_date ? new Date(summary.peak_day_date) : null;
  const busiestDay = peakDate && !Number.isNaN(peakDate.getTime())
    ? peakDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
    : summary
      ? `${summary.peak_day_visitors.toLocaleString('en-US')} on the busiest day`
      : '—';
  const dataHealth = quality ? `${Math.round(quality.overall)}%` : '—';

  // Data health rows from quality dimensions, using the shared plain-language map.
  const healthRows = quality ? Object.entries(quality.dimensions).map(([k, v]) => {
    const label = qualityDimensionLabel(k);
    const hint = qualityDimensionHint(k);
    const tone: 'success' | 'warning' | 'critical' = v >= 90 ? 'success' : v >= 70 ? 'warning' : 'critical';
    return { label, value: `${Math.round(v)}%`, tone, hint };
  }) : [];

  // Alerts for attention (already filtered unread, take 3)
  const openAlerts = alerts.slice(0, 3);

  return (
    <>
      {/* Live calendar clock — Kigali time + ML calendar features */}
      <div className="mb-3">
        <CalendarClock />
      </div>

      {/* Primary: today's demand */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel className="xl:p-4">
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
            <div>
              <p className="text-meta font-medium text-slate-500">
                Visitors per day
              </p>
              <p className="mt-1 text-display-sm font-bold leading-none tracking-tight text-slate-900 sm:text-display-lg">
                {demandPerDay}
              </p>
              <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-slate-500">
                <span className={['flex items-center gap-1 font-semibold', isPositive ? 'text-brand-700' : 'text-red-600'].join(' ')}>
                  {isPositive ? <ArrowUpRightIcon className="h-3.5 w-3.5" strokeWidth={2.5} /> : <ArrowDownIcon className="h-3.5 w-3.5" strokeWidth={2.5} />}
                  {changeLabel}
                </span>
                vs the previous 7 days
                <StatusPill tone={quality && quality.overall >= 70 ? 'success' : quality && quality.overall >= 50 ? 'warning' : 'critical'} dot>
                  {quality && quality.overall >= 70 ? 'On track' : quality ? 'Watch' : '—'}
                </StatusPill>
              </p>
            </div>

            <dl className="flex flex-wrap gap-x-6 gap-y-3">
              <div>
                <dt className="text-micro text-slate-500">Visitors this week</dt>
                <dd className="text-stat-sm font-bold text-slate-900">{visitorsInPeriod}</dd>
              </div>
              <div>
                <dt className="text-micro text-slate-500">Busiest day</dt>
                <dd className="text-stat-sm font-bold text-slate-900">{busiestDay}</dd>
              </div>
              <div className="hidden sm:block">
                <dt className="text-micro text-slate-500">Data health</dt>
                <dd className="text-stat-sm font-bold text-slate-900">{dataHealth}</dd>
              </div>
            </dl>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-caption text-slate-600">
            <span className="flex items-center gap-2">
              <span className="h-0.5 w-5 rounded bg-brand-700" />
              Actual
            </span>
            <span className="flex items-center gap-2">
              <span
                className="h-0.5 w-5 rounded"
                style={{
                  backgroundImage:
                  'repeating-linear-gradient(to right, #17a294 0 5px, transparent 5px 10px)'
                }} />
              Predicted
            </span>
          </div>

          <TodayPulseChart data={pulseData} />
          <p className="mt-2 text-fine text-slate-500">
            From your imported demand data · {summary?.period.start ?? trendPeriod?.start ?? '—'} to {summary?.period.end ?? trendPeriod?.end ?? '—'}
            {modelVersion ? ` · model ${modelVersion.slice(0, 8)}` : ''}
          </p>
        </Panel>

        <div className="flex flex-col gap-3">
          <Panel title="Jump back in" description="The three things teams do most.">
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => onNavigate('ingestion')}
                className="flex items-center gap-3 rounded-lg bg-brand-700 px-3.5 py-3 text-left text-body-sm font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
                <UploadIcon className="h-[18px] w-[18px] shrink-0" strokeWidth={2} />
                Import demand data
                <ArrowRightIcon className="ml-auto h-4 w-4" strokeWidth={2} />
              </button>
              <button
                type="button"
                onClick={() => onNavigate('forecast')}
                className="flex items-center gap-3 rounded-lg border border-slate-300 px-3.5 py-3 text-left text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                <ZapIcon className="h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={2} />
                Generate a forecast
                <ArrowRightIcon className="ml-auto h-4 w-4 text-slate-500" strokeWidth={2} />
              </button>
              <button
                type="button"
                onClick={() => onNavigate('reports')}
                className="flex items-center gap-3 rounded-lg border border-slate-300 px-3.5 py-3 text-left text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                <FileTextIcon className="h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={2} />
                Build a report
                <ArrowRightIcon className="ml-auto h-4 w-4 text-slate-500" strokeWidth={2} />
              </button>
            </div>
          </Panel>

          <Panel
            title="Data health"
            icon={ActivityIcon}
            action={
            <button
              type="button"
              onClick={() => onNavigate('quality')}
              className="tap-expand rounded text-caption font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                Open monitor
              </button>
            }>
            {healthRows.length === 0 ? (
              <EmptyState
                variant="empty"
                size="sm"
                title="No health data"
                description="No quality dimensions computed yet."
                hints={['Ingest demand first']}
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {healthRows.map((row) =>
                <li key={row.label} className="flex items-center gap-3 py-2 first:pt-0">
                    <span className="min-w-0 flex-1">
                      <span className="block text-body-xs font-medium text-slate-800">
                        {row.label}
                      </span>
                      <span className="block truncate text-micro text-slate-500">
                        {row.hint}
                      </span>
                    </span>
                    <StatusPill tone={row.tone}>{row.value}</StatusPill>
                  </li>
                )}
              </ul>
            )}
            <p className="mt-2 text-fine text-slate-500">overall {quality ? `${quality.overall}` : '—'} · open issues {quality?.open_issues ?? '—'}</p>
          </Panel>
        </div>
      </div>

      {/* Secondary: activity + alerts */}
      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel title="Recent activity" description="Everything that touched your data.">
          {activity.length === 0 ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="No activity yet"
              description="No pipeline or audit events in this window."
              hints={['Ingest demand first', 'Wait for nightly materialisation']}
            />
          ) : (
            <ol className="relative border-l border-slate-200 pl-4">
              {activity.map((entry) => {
                const Icon = activityIcon[entry.kind];
                return (
                  <li key={entry.id} className="relative pb-4 last:pb-0">
                    <span className="absolute -left-[25px] flex h-4 w-4 items-center justify-center rounded-full bg-white">
                      <span className="h-2 w-2 rounded-full bg-brand-500" />
                    </span>
                    <div className="flex items-start gap-2.5">
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
                      <div className="min-w-0 flex-1">
                        <p className="text-body-xs font-semibold text-slate-900">
                          {entry.title}
                        </p>
                        <p className="text-caption text-slate-500">{entry.detail}</p>
                      </div>
                      <span className="shrink-0 text-micro text-slate-500">
                        {entry.time}
                      </span>
                    </div>
                  </li>);
              })}
            </ol>
          )}
          <p className="mt-2 text-fine text-slate-500">From {trendPeriod?.start ?? summary?.period.start ?? '—'} to {trendPeriod?.end ?? summary?.period.end ?? '—'}</p>
        </Panel>

        <Panel
          title="Needs attention"
          icon={TriangleAlertIcon}
          action={
          <button
            type="button"
            onClick={() => onNavigate('alerts')}
            className="tap-expand rounded text-caption font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              All alerts
            </button>
          }>
          {openAlerts.length === 0 ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="All caught up"
              description="No unread alerts. Everything looks healthy."
              hints={['Alerts appear when thresholds are breached']}
            />
          ) : (
            <ul className="flex flex-col gap-2">
              {openAlerts.map((alert) =>
              <li
                key={alert.id}
                className="rounded-lg border border-slate-200 px-3 py-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-meta font-semibold text-slate-900">
                      {alert.title}
                    </p>
                    <StatusPill
                    tone={
                    alert.severity === 'critical' ?
                    'critical' :
                    alert.severity === 'warning' ?
                    'warning' :
                    'info'
                    }>
                      {String(alert.severity)}
                    </StatusPill>
                  </div>
                  <p className="mt-1 text-micro leading-relaxed text-slate-500">
                    {alert.body ?? alert.detail ?? ''}
                  </p>
                  <p className="mt-1.5 text-fine text-slate-500">
                    {alert.severity} · {alert.created_at ? toRelativeTime(alert.created_at) : (alert as any).time ?? '—'}
                  </p>
                </li>
              )}
            </ul>
          )}
        </Panel>
      </div>
      <p className="mt-3 text-center text-fine text-slate-500">
        Based on imported demand from {summary?.period.start ?? trendPeriod?.start ?? '—'} to {summary?.period.end ?? trendPeriod?.end ?? '—'}
        {modelVersion ? ` · model ${modelVersion.slice(0,12)}` : ' · model —'}
      </p>
    </>);
}
