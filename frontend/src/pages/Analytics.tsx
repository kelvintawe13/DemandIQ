import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarIcon,
  MapPinIcon,
  ChevronDownIcon,
  UsersIcon,
  ActivityIcon,
  FlagIcon,
  TrendingUpIcon,
  ArrowUpIcon,
  ArrowDownIcon,
  InfoIcon,
  LightbulbIcon,
  ArrowRightIcon,
  Maximize2Icon,
  DownloadIcon,
  BarChart3Icon,
  TableIcon } from
'lucide-react';
import { DemandTrendChart } from '../components/analytics/DemandTrendChart';
import { LocationDonut } from '../components/analytics/LocationDonut';
import { Sparkline } from '../components/analytics/Sparkline';
import { Spinner } from '../components/ui/LoadingState';
import { DateRangeModal } from '../components/ui/DateRangeModal';
import { LocationPickerModal } from '../components/ui/LocationPickerModal';
import { ResponsiveTable } from '../components/ui/Table';
import type { Column } from '../components/ui/Table';
import { EmptyState } from '../components/ui/EmptyState';
import { PageSkeleton } from '../components/ui/Skeleton';
import { Toggle } from '../components/ui/Toggle';
import { Modal, ModalButton } from '../components/ui/Modal';
import { api, ApiException } from '../api/client';
import { useToast } from '../contexts/ToastContext';

interface AnalyticsProps { onNavigate?: (id: string) => void; }

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

type TrendRes = { data: { label: string; current: number; previous: number }[]; granularity: string; period: { start: string; end: string }; previous_period: { start: string; end: string }; };
type ByLocationRes = { data: { name: string; share: string; visitors: number; visitors_formatted: string; value: number; color: string; rank: number }[] };
type BreakdownRowRes = {
  date: string;
  location_name?: string | null;
  visitors: number;
  visitors_formatted: string;
  previous: number;
  previous_formatted: string;
  change: string;
  change_pct: number;
  isPositive: boolean;
  is_positive?: boolean;
  spark: number[];
};
type BreakdownRes = { data: BreakdownRowRes[]; has_more?: boolean; next_cursor?: string | null; };
type InsightsRes = { data: { message: string; type: string }[]; topInsights?: string[]; top_insights?: string[] };

type LocationOption = { id: string; name: string };

/** Preset label → the `range` query value the API understands. */
const rangeToQuery: Record<string,string> = {
  'Last 7 days':'7d',
  'Last 14 days':'14d',
  'Last 30 days':'30d',
  'Last 90 days':'90d',
  // 'This quarter' is calendar quarter (~91d avg) distinct from rolling 'Last 90 days' (90d)
  'This quarter':'91d',
  'This month':'31d',
};

const ALL_LOCATIONS = 'All Locations';

function errorMeta(err: unknown): { variant:'error'|'offline'; title:string; desc:string; hints?:string[] } {
  if (err instanceof ApiException) {
    if (err.status===401) return { variant:'error', title:'Sign in required', desc: err.error.message || 'Your session expired. Sign in again.', hints:['Sign in to view analytics'] };
    if (err.status===403) return { variant:'error', title:'Admin access required', desc: err.error.message || 'You do not have access to analytics.', hints: err.reference ? [`ref: ${err.reference}`] : undefined };
    return { variant:'error', title:'Failed to load analytics', desc: err.error.message + (err.reference?` (ref: ${err.reference})`:''), hints: err.reference?[`ref: ${err.reference}`]:undefined };
  }
  if (err instanceof TypeError) return { variant:'offline', title:'You are offline', desc:'Check your connection and try again.', hints:['Verify VITE_API_URL is set','Check network connectivity'] };
  return { variant:'offline', title:'Network error', desc:String(err) };
}

export function Analytics({ onNavigate }: AnalyticsProps) {
  const [compareEnabled, setCompareEnabled] = useState(true);
  const [range, setRange] = useState('Last 30 days');
  // Bounds behind a custom range. Presets send `range` instead; the two are
  // mutually exclusive so a stale pair can never widen a preset by accident.
  const [customRange, setCustomRange] = useState<{from:string;to:string} | null>(null);
  const [locationName, setLocationName] = useState(ALL_LOCATIONS);
  const [granularity, setGranularity] = useState('Daily');
  const [picker, setPicker] = useState<'range' | 'location' | undefined>();
  const [trendModalOpen, setTrendModalOpen] = useState(false);

  const [summary, setSummary] = useState<SummaryRes | null>(null);
  const [trend, setTrend] = useState<TrendRes | null>(null);
  const [byLoc, setByLoc] = useState<ByLocationRes | null>(null);
  const [breakdown, setBreakdown] = useState<BreakdownRes | null>(null);
  const [insights, setInsights] = useState<InsightsRes | null>(null);
  const [loading, setLoading] = useState(true);
  // A filter change keeps the current panels on screen and marks them busy,
  // rather than swapping them for a spinner: blanking the page on every change
  // is what made the filters feel broken.
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<{variant:'error'|'offline'; title:string; desc:string; hints?:string[]} | null>(null);
  const [locations, setLocations] = useState<LocationOption[]>([]);
  const [breakdownCursor, setBreakdownCursor] = useState<string | null>(null);
  const [breakdownHasMore, setBreakdownHasMore] = useState(false);
  const { toast } = useToast();

  // The location filter is sent as `location_id`; the picker speaks names.
  const locationId = useMemo(
    () => locations.find((l) => l.name === locationName)?.id ?? null,
    [locations, locationName],
  );

  const firstLoad = useRef(true);

  const fetchLocations = useCallback(async () => {
    try {
      const res = await api.get<{ data: Array<{ id: string; name: string }> }>('/api/v1/locations', { limit: 100 });
      setLocations((res.data ?? []).map((l) => ({ id: String(l.id), name: l.name })));
    } catch {
      // The picker falls back to "All Locations" only; it is not worth failing
      // the whole screen over, since every panel still works unfiltered.
      setLocations([]);
    }
  }, []);

  const fetchAll = useCallback(async (cursor?: string | null) => {
    if (firstLoad.current) setLoading(true); else setRefreshing(true);
    setErr(null);

    const period: Record<string, string> = customRange
      ? { from: customRange.from, to: customRange.to }
      : { range: rangeToQuery[range] ?? '30d' };
    const scoped = locationId ? { ...period, location_id: locationId } : period;

    try {
      const [s, t, bL, br, ins] = await Promise.all([
        api.get<SummaryRes>('/api/v1/analytics/summary', scoped),
        // Granularity is a real server parameter; it used to sit in the
        // dependency list while never being sent, so Weekly refetched the same
        // daily series and looked like a dead control.
        api.get<TrendRes>('/api/v1/analytics/trend', { ...scoped, granularity: granularity.toLowerCase() }),
        api.get<ByLocationRes>('/api/v1/analytics/by-location', period),
        api.get<BreakdownRes>('/api/v1/analytics/breakdown', { ...scoped, cursor: cursor ?? undefined }),
        api.get<InsightsRes>('/api/v1/analytics/insights', scoped),
      ]);
      setSummary(s);
      setTrend(t);
      setByLoc(bL);
      setBreakdown(br);
      setBreakdownCursor(br.next_cursor ?? null);
      setBreakdownHasMore(!!br.has_more);
      setInsights(ins);
    } catch (e) {
      setErr(errorMeta(e));
    } finally {
      firstLoad.current = false;
      setLoading(false);
      setRefreshing(false);
    }
  }, [range, customRange, locationId, granularity]);

  useEffect(() => {
    void fetchLocations();
  }, [fetchLocations]);

  useEffect(() => { void fetchAll(null); }, [fetchAll]);

  // A selection that no longer exists (renamed or archived location) would
  // filter to nothing and read as "no data"; fall back to the whole group.
  useEffect(() => {
    if (locations.length && locationName !== ALL_LOCATIONS && !locations.some((l) => l.name === locationName)) {
      setLocationName(ALL_LOCATIONS);
    }
  }, [locations, locationName]);

  const handleRetry = () => void fetchAll(null);
  const handlePrevNext = async (dir:'next'|'prev')=>{
    // backend is cursor only forward; for prev we reset to first page
    if (dir==='prev') { void fetchAll(null); toast('Returned to first page','info'); return; }
    if (breakdownCursor) void fetchAll(breakdownCursor);
  };

  const applyRange = (label: string) => setRange(label);
  const clearCustomRange = () => { setCustomRange(null); setRange('Last 30 days'); };

  // First load only: a filter change keeps the panels and marks them busy.
  if (loading && !summary) {
    return <PageSkeleton label="Loading analytics" variant="equal" />;
  }
  if (err && !summary) {
    return (
      <EmptyState
        variant={err.variant}
        title={err.title}
        description={err.desc}
        hints={err.hints}
        action={{ label:'Retry', onClick: handleRetry }}
        size="md"
      />
    );
  }
  // Empty check: summary empty if total_visitors ===0 and no by-location data
  const isEmpty = !summary || (summary.total_visitors===0 && (!byLoc || byLoc.data.length===0));
  if (isEmpty && !locationId) {
    return (
      <EmptyState
        variant="empty"
        title="No analytics yet"
        description="Import demand data to generate analytics. Every number shows the period it covers and where it came from."
        hints={["Ingest demand first","Wait for nightly feature materialisation"]}
        action={{ label:'Go to ingestion', onClick: ()=> onNavigate?.('ingestion') }}
        size="md"
      />
    );
  }

  const kpis = [
    { label:'Total Visitors', value: summary ? summary.total_visitors.toLocaleString() : '—', delta: summary?.total_visitors_change_pct != null ? `${summary.total_visitors_change_pct>0?'+':''}${summary.total_visitors_change_pct.toFixed(1)}%` : '—', icon: UsersIcon, isPositive: (summary?.total_visitors_change_pct ?? 0) >=0, provenance: summary ? `${summary.period.start} → ${summary.period.end}` : '' },
    { label:'Average Daily Demand', value: summary ? Math.round(summary.average_daily_demand).toLocaleString() : '—', delta: summary?.average_daily_demand_change_pct != null ? `${summary.average_daily_demand_change_pct>0?'+':''}${summary.average_daily_demand_change_pct.toFixed(1)}%` : '—', icon: ActivityIcon, isPositive: (summary?.average_daily_demand_change_pct ?? 0) >=0, provenance: 'from your imported demand records' },
    { label:'Peak Day Visitors', value: summary ? summary.peak_day_visitors.toLocaleString() : '—', delta: summary?.peak_day_visitors_change_pct != null ? `${summary.peak_day_visitors_change_pct>0?'+':''}${summary.peak_day_visitors_change_pct.toFixed(1)}%` : '—', icon: FlagIcon, isPositive: (summary?.peak_day_visitors_change_pct ?? 0) >=0, provenance: summary?.peak_day_date ? `busiest day ${summary.peak_day_date}` : '' },
    { label:'Growth Rate', value: summary ? `${summary.growth_rate.toFixed(1)}%` : '—', delta: summary?.growth_rate_delta_pp != null ? `${summary.growth_rate_delta_pp>0?'+':''}${summary.growth_rate_delta_pp.toFixed(1)} points` : '—', icon: TrendingUpIcon, isPositive: (summary?.growth_rate_delta_pp ?? 0) >=0, provenance: `vs ${summary?.previous_period.start} → ${summary?.previous_period.end}` },
  ];

  const trendData = trend?.data ?? [];
  const donutSlices = byLoc?.data.map(d=>({ name:d.name, value:d.value, color:d.color })) ?? [];
  const breakdownRows = breakdown?.data ?? [];
  const insightList: string[] = (()=> {
    if (!insights) return [];
    if (insights.data && insights.data.length) return insights.data.map(i=> i.message);
    if (insights.topInsights && insights.topInsights.length) return insights.topInsights;
    if (insights.top_insights && insights.top_insights.length) return insights.top_insights;
    return [];
  })();

  // With no location selected the API aggregates per location and stamps every
  // row with the period end date, so the first column has to be the location,
  // labelling it "Date" printed the same date on every row.
  const breakdownByLocation = !locationId;
  const breakdownColumns: Column<BreakdownRowRes>[] = [
    {
      key: 'label',
      label: breakdownByLocation ? 'Location' : 'Date',
      primary: true,
      render: (row) => (
        <span className="block truncate font-medium text-slate-900" title={row.location_name ?? row.date}>
          {(breakdownByLocation ? row.location_name : row.date) ?? row.date}
        </span>
      ),
    },
    {
      key: 'visitors',
      label: 'Visitors',
      render: (row) => <span className="font-medium tabular-nums text-slate-900">{row.visitors_formatted ?? String(row.visitors)}</span>,
    },
    {
      key: 'previous',
      label: 'vs Previous',
      render: (row) => <span className="tabular-nums text-slate-600">{row.previous_formatted ?? String(row.previous)}</span>,
    },
    {
      key: 'change',
      label: '% Change',
      render: (row) => {
        const up = row.isPositive ?? row.is_positive;
        return (
          <span className={['flex items-center gap-1 font-semibold tabular-nums', up ? 'text-brand-700' : 'text-red-600'].join(' ')}>
            {up ? <ArrowUpIcon className="h-3.5 w-3.5" strokeWidth={2.5} /> : <ArrowDownIcon className="h-3.5 w-3.5" strokeWidth={2.5} />}
            {row.change}
          </span>
        );
      },
    },
    {
      key: 'trend',
      label: 'Trend',
      cardHidden: true,
      render: (row) => <Sparkline values={row.spark ?? [row.visitors, row.previous]} isPositive={!!(row.isPositive ?? row.is_positive)} />,
    },
  ];

  const previousLabel = customRange ? 'the previous equal-length period' : `previous ${rangeToQuery[range] === '91d' ? 'quarter' : range.replace('Last ', '')}`;

  return (
    <div aria-busy={refreshing} className="flex w-full min-w-0 max-w-full flex-col gap-3 overflow-x-clip">
      {/* Filters: compact 2-col grid on mobile, inline wrap on desktop */}
      <div className="grid w-full min-w-0 max-w-full grid-cols-2 gap-2 overflow-hidden sm:flex sm:flex-wrap sm:items-center sm:gap-3 sm:overflow-visible">
        <button
          type="button"
          aria-label={`Period: ${range}`}
          onClick={() => setPicker('range')}
          className="inline-flex w-full items-center justify-between gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-body-xs font-medium text-slate-800 sm:w-auto sm:justify-start sm:gap-2.5 sm:px-3.5 sm:py-2.5 sm:text-control transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <span className="inline-flex items-center gap-1.5 sm:gap-2">
            <CalendarIcon className="h-4 w-4 shrink-0 text-brand-600 sm:h-[18px] sm:w-[18px]" strokeWidth={1.75} />
            <span className="truncate">{range}</span>
          </span>
          <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-500 sm:h-4 sm:w-4" />
        </button>
        <button
          type="button"
          aria-label={`Location: ${locationName}`}
          onClick={() => setPicker('location')}
          className="inline-flex w-full items-center justify-between gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-body-xs font-medium text-slate-800 sm:w-auto sm:justify-start sm:gap-2.5 sm:px-3.5 sm:py-2.5 sm:text-control transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <span className="inline-flex min-w-0 items-center gap-1.5 sm:gap-2">
            <MapPinIcon className="h-4 w-4 shrink-0 text-brand-600 sm:h-[18px] sm:w-[18px]" strokeWidth={1.75} />
            <span className="truncate">{locationName}</span>
          </span>
          <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-500 sm:h-4 sm:w-4" />
        </button>
        <div
          role="group"
          aria-label="Group the trend by"
          className="inline-flex w-full overflow-hidden rounded-xl border border-slate-200 bg-white sm:w-auto"
        >
          {['Daily','Weekly'].map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={granularity === option}
              onClick={() => setGranularity(option)}
              className={['flex-1 py-2.5 text-body-xs font-semibold sm:flex-none sm:px-3.5 sm:py-2.5 sm:text-body-sm transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500', granularity === option ? 'bg-brand-50 text-brand-800' : 'text-slate-600 hover:bg-slate-50'].join(' ')}
            >
              {option}
            </button>
          ))}
        </div>
        <span className="inline-flex w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 sm:w-auto sm:justify-start sm:gap-3 sm:px-3.5 sm:py-2 text-meta font-medium text-slate-800 sm:text-body-sm">
          <span className="min-w-0 truncate text-caption sm:text-body-sm sm:truncate-none">vs previous</span>
          <span className="shrink-0">
            <Toggle
              checked={compareEnabled}
              onChange={setCompareEnabled}
              label="Compare with the previous period"
            />
          </span>
        </span>
        {(refreshing || customRange) && (
          <span className="col-span-2 flex items-center gap-2 text-fine text-slate-500 sm:col-span-1 sm:text-caption">
            {refreshing && <Spinner className="h-3.5 w-3.5" />}
            {refreshing ? 'Updating…' : customRange ? `${customRange.from} → ${customRange.to}` : ''}
          </span>
        )}
        {customRange && !refreshing && (
          <button
            type="button"
            onClick={clearCustomRange}
            className="col-span-2 tap-expand rounded text-left text-caption font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:col-span-1"
          >
            Clear custom range
          </button>
        )}
      </div>

      {err && (
        <div className="mt-3">
          <EmptyState
            variant={err.variant}
            title={err.title}
            description={`${err.desc} The panels below are the last successful load.`}
            hints={err.hints}
            action={{ label:'Retry', onClick: handleRetry }}
            size="sm"
          />
        </div>
      )}

      {/* KPIs: 2-col grid on mobile, 4-col on desktop, compact, no scroll */}
      <div className="grid w-full min-w-0 max-w-full grid-cols-2 gap-2 overflow-hidden sm:gap-3 sm:overflow-visible xl:grid-cols-4">
        {kpis.map((kpi) => {
          const Icon = kpi.icon;
          return (
            <article
              key={kpi.label}
              className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white p-3 sm:gap-3 sm:p-4"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 sm:h-11 sm:w-11">
                <Icon className="h-4 w-4 text-brand-600 sm:h-5 sm:w-5" strokeWidth={1.9} />
              </span>
              <div className="min-w-0 flex-1 overflow-hidden">
                <h2 className="text-fine font-medium leading-[1.15] text-slate-600 sm:text-body-sm line-clamp-2 break-words">{kpi.label}</h2>
                <p className="mt-0.5 truncate text-stat-sm font-bold leading-tight tracking-tight text-slate-900 sm:text-h2">{kpi.value}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-1 text-fine leading-tight text-slate-500 sm:mt-1 sm:gap-x-1.5 sm:text-meta">
                  <span className={['flex items-center gap-0.5 font-semibold sm:gap-1', kpi.isPositive ? 'text-brand-700' : 'text-red-600'].join(' ')}>
                    {kpi.isPositive ? <ArrowUpIcon className="h-3 w-3 sm:h-3.5 sm:w-3.5" strokeWidth={2.5} /> : <ArrowDownIcon className="h-3 w-3 sm:h-3.5 sm:w-3.5" strokeWidth={2.5} />}
                    {kpi.delta}
                  </span>
                  <span className="hidden sm:inline">vs {previousLabel}</span>
                  <span className="sm:hidden">vs prev</span>
                </p>
                {kpi.provenance && <p className="mt-0.5 hidden truncate text-fine text-slate-500 sm:block">{kpi.provenance}</p>}
              </div>
            </article>);
        })}
      </div>

      {/* Trend + location */}
      <div className="grid w-full min-w-0 max-w-full grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <section aria-labelledby="demand-trend-heading" className="w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="demand-trend-heading" className="flex items-center gap-2 text-h4 font-bold tracking-tight text-slate-900">
                Demand Trend
                <InfoIcon className="h-4 w-4 text-slate-500" />
              </h2>
              <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-meta text-slate-600">
                <span className="flex items-center gap-2"><span className="h-0.5 w-6 rounded bg-brand-700" />Visitors (Current Period)</span>
                {compareEnabled && (
                <span className="flex items-center gap-2"><span className="h-0.5 w-6 rounded" style={{ backgroundImage:'repeating-linear-gradient(to right, var(--brand-400) 0 6px, transparent 6px 11px)' }} />Visitors (Previous Period)</span>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="hidden rounded-lg bg-slate-50 px-2.5 py-1 text-micro font-semibold text-slate-600 sm:inline-flex">{granularity}</span>
              <button
                type="button"
                onClick={() => setTrendModalOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-caption font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50 hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                aria-label="Expand demand trend"
              >
                <Maximize2Icon className="h-3.5 w-3.5" strokeWidth={2} />
                <span className="hidden sm:inline">View</span>
                <span className="sm:hidden">Expand</span>
              </button>
            </div>
          </div>
          <div className="mt-3">
            {trendData.length===0 ? (
              <EmptyState variant="empty" size="sm" title="No trend data" description="No daily demand in this window." hints={["Ingest demand first"]} />
            ) : (
              <DemandTrendChart
                data={trendData.map(d=>({
                  label:d.label,
                  current: d.current,
                  // when compare is off, hide previous series; undefined + showPrevious=false skips the Line
                  previous: compareEnabled ? d.previous : undefined,
                }))}
                showPrevious={compareEnabled}
              />
            )}
            <p className="mt-2 text-fine text-slate-500">
              window {trend?.period.start} → {trend?.period.end} · previous {trend?.previous_period.start} → {trend?.previous_period.end}
              {locationId ? ` · ${locationName}` : ''}
            </p>
          </div>
        </section>

        <section aria-labelledby="demand-location-heading" className="w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="demand-location-heading" className="text-h4 font-bold tracking-tight text-slate-900">Demand by Location</h2>
            {/* There was a "Breakdown metric" selector here offering Visitors,
                Surplus exposure and Unmet demand. `GET /analytics/by-location`
                takes no metric parameter, so two of the three options returned
                identical numbers; the panel measures demand share, and says so. */}
            <span className="rounded-lg bg-slate-50 px-2.5 py-1 text-micro font-semibold text-slate-600">share of demand</span>
          </div>
          {locationId && (
            <p className="mt-2 rounded-lg bg-slate-50 px-2.5 py-2 text-micro text-slate-600">
              Scoped to <span className="font-semibold text-slate-800">{locationName}</span>. Choose “{ALL_LOCATIONS}” to compare every site.
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-center justify-center gap-5 sm:flex-nowrap">
            {byLoc && byLoc.data.length===0 ? (
              <EmptyState variant="empty" size="sm" title="No locations" description="Add locations to see demand share." />
            ) : (
              <>
                <LocationDonut data={donutSlices} />
                <ul className="min-w-0 flex-1 space-y-2.5">
                  {(byLoc?.data ?? []).map((slice)=>
                  // Wraps on a phone rather than crushing the location name into an
                  // ellipsis: the name keeps a floor, so the count drops to its own
                  // line instead of stealing the label's width.
                  <li key={slice.name} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-body-sm text-slate-700">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: slice.color }} />
                      <span className="min-w-[6.5rem] flex-1 truncate">{slice.name}</span>
                      <span className="shrink-0 tabular-nums text-slate-600">{slice.share}</span>
                      <span className="shrink-0 text-right tabular-nums font-medium text-slate-900 sm:w-14">{slice.visitors_formatted}</span>
                    </li>
                  )}
                </ul>
              </>
            )}
          </div>
          <p className="mt-2 text-fine text-slate-500">{byLoc?.data.length ?? 0} {(byLoc?.data.length ?? 0) === 1 ? 'location' : 'locations'} · period {trend?.period.start} → {trend?.period.end}</p>
        </section>
      </div>

      {/* Breakdown + insights */}
      <div className="grid w-full min-w-0 max-w-full grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <section aria-labelledby="performance-heading" className="w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h2 id="performance-heading" className="text-h4 font-bold tracking-tight text-slate-900">Performance Breakdown</h2>
          <p className="mt-1 text-meta text-slate-500">
            {breakdownByLocation
              ? 'Every location in this window, ranked by demand, against the same window a period earlier.'
              : `Daily demand for ${locationName}, against the same day in the previous period.`}
          </p>
          {breakdownRows.length===0 ? (
            <EmptyState variant="empty" size="sm" title="No breakdown yet" description="No daily rows in this window." />
          ) : (
            <>
              <div className="mt-3">
                <ResponsiveTable
                  caption="Performance breakdown"
                  minWidth="min-w-[440px]"
                  /* A period arrives a day per row, so the table is capped
                     rather than left to grow with the window. */
                  maxHeight="max-h-[420px]"
                  columns={breakdownColumns}
                  rows={breakdownRows}
                  rowKey={(row) => `${row.location_name ?? row.date}-${row.date}`}
                />
              </div>
              {(breakdownHasMore || breakdownCursor) && (
                <div className="mt-3 flex gap-2">
                  <button type="button" onClick={()=>void handlePrevNext('prev')} className="rounded-lg border border-slate-300 px-3 py-2.5 text-caption font-semibold text-slate-600 hover:bg-slate-50 sm:py-1.5">Previous</button>
                  {breakdownHasMore && <button type="button" onClick={()=>void handlePrevNext('next')} className="rounded-lg bg-brand-700 px-3 py-2.5 text-caption font-semibold text-white hover:bg-brand-800 sm:py-1.5">Next</button>}
                </div>
              )}
            </>
          )}
        </section>

        <section aria-labelledby="top-insights-heading" className="w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <h2 id="top-insights-heading" className="flex items-center gap-2.5 text-h4 font-bold tracking-tight text-slate-900"><LightbulbIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />Top Insights</h2>
          {insightList.length===0 ? (
            <EmptyState variant="empty" size="sm" title="No insights" description="Not enough data to generate insights in this window." />
          ) : (
            <ul className="mt-4 flex flex-col gap-4">
              {insightList.map((insight,idx)=>
              <li key={idx} className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50"><LightbulbIcon className="h-4 w-4 text-brand-600" strokeWidth={2} /></span>
                  <p className="text-body-sm leading-relaxed text-slate-700">{insight}</p>
                </li>
              )}
            </ul>
          )}
          <p className="mt-2 text-fine text-slate-500">Period {trend?.period.start} → {trend?.period.end}</p>
        </section>
      </div>

      <div className="flex justify-center">
        <button type="button" onClick={() => onNavigate?.('reports')} className="tap-expand inline-flex items-center gap-2 rounded text-body-sm-lg font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          View full report
          <ArrowRightIcon className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>

      <DateRangeModal
        open={picker === 'range'}
        onClose={() => setPicker(undefined)}
        value={range}
        onApply={applyRange}
        onApplyRange={setCustomRange} />
      <LocationPickerModal
        open={picker === 'location'}
        onClose={() => setPicker(undefined)}
        value={locationName}
        onApply={setLocationName}
        options={[ALL_LOCATIONS, ...locations.map((l) => l.name)]} />

      {/* Demand Trend: expanded view */}
      <Modal
        open={trendModalOpen}
        onClose={() => setTrendModalOpen(false)}
        title="Demand Trend: detailed view"
        description={`Window ${trend?.period.start ?? '—'} → ${trend?.period.end ?? '—'} · previous ${trend?.previous_period.start ?? '—'} → ${trend?.previous_period.end ?? '—'}${locationId ? ` · ${locationName}` : ''}`}
        size="2xl"
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setTrendModalOpen(false)}>Close</ModalButton>
            <ModalButton
              onClick={() => {
                const rows = trendData.map(d => `${d.label},${d.current},${d.previous ?? ''}`).join('\n');
                const blob = new Blob([`label,current,previous\n${rows}`], { type: 'text/csv' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `trend_${trend?.period.start ?? 'export'}.csv`;
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              <DownloadIcon className="h-4 w-4" strokeWidth={2} />
              Export CSV
            </ModalButton>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {/* Section 1: Controls */}
          <section className="rounded-xl border border-slate-200 p-3">
            <h3 className="flex items-center gap-2 text-caption font-semibold uppercase tracking-wide text-slate-500">
              <BarChart3Icon className="h-3.5 w-3.5" strokeWidth={2} />
              Controls
            </h3>
            <div className="mt-2.5 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
              <div role="group" aria-label="Granularity" className="inline-flex overflow-hidden rounded-lg border border-slate-200">
                {['Daily','Weekly'].map(opt => (
                  <button
                    key={opt}
                    type="button"
                    aria-pressed={granularity===opt}
                    onClick={() => setGranularity(opt)}
                    className={['flex-1 px-3 py-1.5 text-meta font-semibold sm:flex-none', granularity===opt ? 'bg-brand-50 text-brand-800' : 'text-slate-600 hover:bg-slate-50'].join(' ')}
                  >
                    {opt}
                  </button>
                ))}
              </div>
              <span className="inline-flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-meta font-medium text-slate-700">
                <span>Compare</span>
                <Toggle checked={compareEnabled} onChange={setCompareEnabled} label="Compare with previous period" />
              </span>
              <span className="col-span-2 text-fine leading-relaxed text-slate-500 sm:col-span-1">
                {trendData.length} points · {granularity.toLowerCase()} · {compareEnabled ? 'with previous' : 'current only'}
              </span>
            </div>
          </section>

          {/* Section 2: Chart */}
          <section className="overflow-hidden rounded-xl border border-slate-200">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
              <h3 className="flex items-center gap-2 text-caption font-semibold uppercase tracking-wide text-slate-500">
                <BarChart3Icon className="h-3.5 w-3.5" strokeWidth={2} />
                Visitors over time
              </h3>
              <span className="rounded-md bg-white px-2 py-0.5 text-fine font-semibold text-slate-600 shadow-sm">{granularity}</span>
            </div>
            <div className="p-3 sm:p-4">
              {trendData.length===0 ? (
                <EmptyState variant="empty" size="sm" title="No trend data" description="No daily demand in this window." />
              ) : (
                <DemandTrendChart
                  data={trendData.map(d=>({ label: d.label, current: d.current, previous: compareEnabled ? d.previous : undefined }))}
                  showPrevious={compareEnabled}
                  height={340}
                />
              )}
              {/* Legend: no overlap, wraps */}
              <div className="mt-3 flex flex-wrap items-center justify-center gap-x-5 gap-y-1.5 border-t border-slate-100 pt-3 text-micro text-slate-600">
                <span className="flex items-center gap-2"><span className="h-0.5 w-5 rounded bg-brand-700" />Current</span>
                {compareEnabled && <span className="flex items-center gap-2"><span className="h-0.5 w-5 rounded" style={{ backgroundImage:'repeating-linear-gradient(to right, var(--brand-400) 0 5px, transparent 5px 9px)' }} />Previous</span>}
                <span className="text-fine text-slate-500">hover for values · {trendData.length} labels, no overlap</span>
              </div>
            </div>
          </section>

          {/* Section 3: Summary */}
          <section className="rounded-xl border border-slate-200 p-3">
            <h3 className="text-caption font-semibold uppercase tracking-wide text-slate-500">Summary</h3>
            {(() => {
              const curTotal = trendData.reduce((s,d)=> s + (d.current||0), 0);
              const prevTotal = trendData.reduce((s,d)=> s + (d.previous||0), 0);
              const delta = prevTotal ? ((curTotal - prevTotal) / prevTotal * 100) : 0;
              const curAvg = trendData.length ? Math.round(curTotal / trendData.length) : 0;
              return (
                <div className="mt-2.5 grid grid-cols-3 gap-2">
                  <div className="rounded-lg bg-slate-50 px-3 py-2.5">
                    <p className="text-fine font-medium text-slate-500">Current total</p>
                    <p className="mt-0.5 text-h5 font-bold tabular-nums text-slate-900">{curTotal.toLocaleString()}</p>
                    <p className="text-fine text-slate-500">{trendData.length} points</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 px-3 py-2.5">
                    <p className="text-fine font-medium text-slate-500">Previous total</p>
                    <p className="mt-0.5 text-h5 font-bold tabular-nums text-slate-900">{prevTotal.toLocaleString()}</p>
                    <p className="text-fine text-slate-500">same window</p>
                  </div>
                  <div className={['rounded-lg px-3 py-2.5', delta>=0 ? 'bg-emerald-50' : 'bg-red-50'].join(' ')}>
                    <p className="text-fine font-medium text-slate-500">Change</p>
                    <p className={['mt-0.5 flex items-center gap-1 text-h5 font-bold tabular-nums', delta>=0 ? 'text-emerald-700' : 'text-red-600'].join(' ')}>
                      {delta>=0 ? <ArrowUpIcon className="h-3.5 w-3.5" strokeWidth={2.5}/> : <ArrowDownIcon className="h-3.5 w-3.5" strokeWidth={2.5}/>}
                      {delta>=0?'+':''}{delta.toFixed(1)}%
                    </p>
                    <p className="text-fine text-slate-500">avg {curAvg}/day</p>
                  </div>
                </div>
              );
            })()}
            <p className="mt-2 text-fine text-slate-500">Provenance: {trend?.period.start} → {trend?.period.end} · {locationId ? locationName : 'all locations'}</p>
          </section>

          {/* Section 4: Table */}
          <section className="overflow-hidden rounded-xl border border-slate-200">
            <div className="flex items-center gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
              <TableIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={2} />
              <h3 className="text-caption font-semibold uppercase tracking-wide text-slate-500">Data</h3>
              <span className="ml-auto text-fine text-slate-500">{trendData.length} rows</span>
            </div>
            <div className="max-h-[260px] overflow-auto">
              <table className="w-full text-left text-meta">
                <thead className="sticky top-0 bg-white">
                  <tr className="border-b border-slate-200 text-fine uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 font-semibold">Date</th>
                    <th className="px-3 py-2 text-right font-semibold">Current</th>
                    {compareEnabled && <th className="px-3 py-2 text-right font-semibold">Previous</th>}
                    {compareEnabled && <th className="px-3 py-2 text-right font-semibold">Δ</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {trendData.map(row => {
                    const delta = compareEnabled && row.previous != null ? row.current - (row.previous as number) : null;
                    return (
                      <tr key={row.label} className="hover:bg-slate-50/60">
                        <td className="px-3 py-1.5 font-medium text-slate-800">{row.label}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-900">{row.current.toLocaleString()}</td>
                        {compareEnabled && <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">{(row.previous ?? 0).toLocaleString()}</td>}
                        {compareEnabled && <td className={['px-3 py-1.5 text-right tabular-nums font-medium', delta!=null && delta>=0 ? 'text-emerald-700' : 'text-red-600'].join(' ')}>{delta!=null ? `${delta>=0?'+':''}${delta}` : '—'}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </Modal>
    </div>
  );
}
