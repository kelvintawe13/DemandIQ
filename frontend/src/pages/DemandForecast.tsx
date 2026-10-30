import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CalendarIcon,
  MapPinIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  LightbulbIcon,
  InfoIcon,
  ArrowUpIcon,
  DownloadIcon,
  CalendarClockIcon,
  ArrowRightIcon } from
'lucide-react';
import { ForecastChart } from '../components/forecast/ForecastChart';
import { DateRangeModal } from '../components/ui/DateRangeModal';
import { LocationPickerModal } from '../components/ui/LocationPickerModal';
import { CalendarClock } from '../components/ui/CalendarClock';
import { Modal, ModalButton } from '../components/ui/Modal';
import { Field, inputClass } from '../components/ui/Field';
import { useToast } from '../contexts/ToastContext';
import { useNavigation } from '../contexts/NavigationContext';
import { EmptyState } from '../components/ui/EmptyState';
import { Spinner } from '../components/ui/LoadingState';
import { StatusPill } from '../components/ui/StatusPill';
import { api, ApiException, getAuthHeader } from '../api/client';
import { useSession } from '../contexts/SessionContext';
import { PreparePanel } from '../components/forecast/PreparePanel';
import { ForecastWhy } from '../components/forecast/ForecastWhy';
import { StatCard } from '../components/ui/StatCard';
import { ResponsiveTable } from '../components/ui/Table';
import { FilterBar } from '../components/ui/FilterBar';
import { Pagination } from '../components/ui/Pagination';
import { Panel } from '../components/ui/Panel';
import { ForecastByDayChart } from '../components/forecast/ForecastByDayChart';
import type { ForecastDayPoint } from '../components/forecast/ForecastByDayChart';
import { DemandDistributionChart } from '../components/forecast/DemandDistributionChart';
import { WeekdayProfileChart } from '../components/forecast/WeekdayProfileChart';
import { useListState, type FilterDef } from '../hooks/useListState';
import type { ForecastPoint } from '../data/forecast';
// The day this page plans for is the *workspace's* day, not UTC's: a
// `toISOString()` "today" is already tomorrow in Kigali for two hours every
// night, which made the forecast target the wrong service day.
import { todayISO, addDaysISO } from '../lib/workspaceTime';

interface DemandForecastProps {
  onNavigate?: (id: string) => void;
}

const granularities = ['Daily', 'Weekly', 'Monthly'] as const;

interface Location {
  id: string;
  name: string;
  area?: string | null;
  cuisine?: string | null;
}

interface ForecastRecord {
  id: string;
  target_date: string;
  point?: number;
  point_forecast?: number;
  lower?: number;
  lower_bound?: number;
  upper?: number;
  upper_bound?: number;
  coverage_target?: number;
  model_version_id?: string;
  model_version?: string;
  fallback?: boolean;
  feature_set_version?: string;
  as_of_timestamp?: string;
  created_at?: string;
}

interface Paginated<T> {
  data: T[];
  has_more?: boolean;
  next_cursor?: string | null;
}

function parseRangeDates(range: string, today: string): { from: string; to: string } {
  // Custom range like "2026-08-20 → 2026-09-19"
  if (range.includes('→')) {
    const parts = range.split('→').map((s) => s.trim());
    const from = parts[0] || addDaysISO(today, -30);
    const to = parts[1] || addDaysISO(today, 30);
    return { from, to };
  }
  const map: Record<string, number> = {
    'Last 7 days': 7,
    'Last 14 days': 14,
    'Last 30 days': 30,
    'Last 90 days': 90,
    'This month': 30,
  };
  const days = map[range] ?? 30;
  const from = addDaysISO(today, -days);
  const to = addDaysISO(today, 30);
  return { from, to };
}

/**
 * `Fri, Sep 26` for a `YYYY-MM-DD` day.
 *
 * Module scope, not a closure inside the component: the chart memos below run
 * during the same render pass that declares it, so a `const` further down the
 * body would be in its temporal dead zone when they call it.
 */
function shortDay(iso: string): string {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

function normalizeForecast(f: ForecastRecord): { point: number; lower: number; upper: number; coverage: number; fallback: boolean; modelVersion: string; targetDate: string } {
  const point = (f.point ?? f.point_forecast ?? 0) as number;
  const lower = (f.lower ?? f.lower_bound ?? 0) as number;
  const upper = (f.upper ?? f.upper_bound ?? 0) as number;
  const coverage = f.coverage_target ?? 0.90;
  const fallback = Boolean(f.fallback);
  const modelVersion = (f.model_version_id ?? f.model_version ?? '') as string;
  const targetDate = f.target_date;
  return { point, lower, upper, coverage, fallback, modelVersion, targetDate };
}

export function DemandForecast({ onNavigate }: DemandForecastProps) {
  const [granularity, setGranularity] =
  useState<(typeof granularities)[number]>('Daily');
  const [range, setRange] = useState('Last 30 days');
  const [picker, setPicker] = useState<'range' | 'location' | undefined>();
  const [generating, setGenerating] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  // Report-schedule form state, so the modal posts what the operator chose
  // instead of closing with a toast and sending nothing.
  const [scheduleCadence, setScheduleCadence] = useState('weekly');
  const [scheduleRecipients, setScheduleRecipients] = useState('kitchen@demandiq.app');
  const [scheduleFormat, setScheduleFormat] = useState('pdf');
  const [scheduling, setScheduling] = useState(false);
  const { toast } = useToast();
  const navigation = useNavigation();
  const { user } = useSession();
  // Capabilities, not role names: the API enforces these too.
  const permissions = user.permissions ?? [];
  // The actuals behind the chart come from the ingestion dataset, and the whole
  // `/api/v1/ingest` router is behind INGEST_WRITE. A seat without it (a viewer,
  // an analyst) gets an empty history rather than a 403 on every location.
  const canReadIngest = permissions.includes('ingest:write');

  // Locations state
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationsLoading, setLocationsLoading] = useState(true);
  const [locationsError, setLocationsError] = useState<{ variant: 'error' | 'offline'; message: string; reference?: string | null; status?: number } | null>(null);
  const [selectedLocationId, setSelectedLocationId] = useState<string | null>(null);
  const [selectedLocationName, setSelectedLocationName] = useState('All Locations');

  // Forecasts state
  const [forecasts, setForecasts] = useState<ForecastRecord[]>([]);
  const [forecastsLoading, setForecastsLoading] = useState(true);
  const [forecastsError, setForecastsError] = useState<{ variant: 'error' | 'offline'; message: string; reference?: string | null; status?: number } | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [historical, setHistorical] = useState<Array<{ date: string; demand_qty: number }>>([]);

  /*
    The workspace's day, re-read while the page stays open: a target date
    computed once at mount would still name yesterday after Kigali midnight.
    The check is a minute long and the state only changes on a new day, so it
    never re-renders the chart in between.
  */
  const [today, setToday] = useState(() => todayISO());
  useEffect(() => {
    const id = window.setInterval(() => {
      setToday((current) => {
        const next = todayISO();
        return current === next ? current : next;
      });
    }, 60_000);
    return () => window.clearInterval(id);
  }, []);

  const { from, to } = useMemo(() => parseRangeDates(range, today), [range, today]);

  const granularityParam = useMemo(() => {
    if (granularity === 'Weekly') return 'weekly';
    return 'daily';
  }, [granularity]);

  const fetchLocations = useCallback(async () => {
    setLocationsLoading(true);
    setLocationsError(null);
    try {
      const res = await api.get<Paginated<Location>>('/api/v1/locations', { limit: 100 });
      const data = res.data ?? [];
      setLocations(data);
      if (data.length > 0 && !selectedLocationId) {
        setSelectedLocationId(data[0].id);
        setSelectedLocationName(data[0].name);
      } else if (data.length === 0) {
        setSelectedLocationId(null);
        setSelectedLocationName('All Locations');
      }
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) {
          setLocationsError({ variant: 'error', message: 'Sign in required', reference: err.reference, status: 401 });
        } else if (err.status >= 500) {
          setLocationsError({ variant: 'error', message: err.error.message || 'Failed to load locations', reference: err.reference, status: err.status });
        } else {
          setLocationsError({ variant: 'error', message: err.error.message || 'Failed to load locations', reference: err.reference, status: err.status });
        }
      } else if (err instanceof TypeError) {
        setLocationsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setLocationsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      }
    } finally {
      setLocationsLoading(false);
    }
  }, [selectedLocationId]);

  const fetchForecasts = useCallback(async () => {
    if (!selectedLocationId) {
      setForecasts([]);
      setForecastsLoading(false);
      return;
    }
    setForecastsLoading(true);
    setForecastsError(null);
    try {
      const res = await api.get<Paginated<ForecastRecord>>('/api/v1/forecasts', {
        location_id: selectedLocationId,
        from,
        to,
        limit: 50,
      });
      setForecasts(res.data ?? []);
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) {
          setForecastsError({ variant: 'error', message: 'Sign in required', reference: err.reference, status: 401 });
        } else {
          setForecastsError({ variant: 'error', message: err.error.message || 'Failed to load forecasts', reference: err.reference, status: err.status });
        }
      } else if (err instanceof TypeError) {
        setForecastsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setForecastsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      }
    } finally {
      setForecastsLoading(false);
    }
  }, [selectedLocationId, from, to]);

  useEffect(() => {
    void fetchLocations();
  }, [fetchLocations]);

  useEffect(() => {
    void fetchForecasts();
  }, [fetchForecasts]);

  useEffect(() => {
    if (!selectedLocationId || !canReadIngest) { setHistorical([]); return; }
    void (async () => {
      try {
        const res = await api.get<{ data: Array<{ date: string; demand_qty: number }> }>('/api/v1/ingest/demand', { location_id: selectedLocationId, limit: 14 });
        // Sort ascending for chart
        const sorted = (res.data ?? []).slice().sort((a,b)=> a.date.localeCompare(b.date));
        setHistorical(sorted);
      } catch { setHistorical([]); }
    })();
  }, [selectedLocationId, forecasts.length, canReadIngest]);

  const generate = async () => {
    if (!selectedLocationId) {
      toast('Select a location first', 'error');
      return;
    }
    setGenerating(true);
    try {
      // target_date = tomorrow (inside the current from/to window) so the
      // new point appears immediately in the chart. Previously we used
      // `addDaysISO(to,1)` (30 days in the future) which fell outside the
      // fetched `from/to` range and never showed up.
      const targetDate = addDaysISO(today, 1);
      const res = await api.post<ForecastRecord>('/api/v1/forecasts', {
        location_id: selectedLocationId,
        target_date: targetDate,
        horizon_days: 7,
        granularity: granularityParam,
      });
      const norm = normalizeForecast(res);
      toast(`Forecast generated for ${selectedLocationName.toLowerCase()}: ${norm.point} covers`);
      // Refresh horizon view
      await fetchForecasts();
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) {
          toast('Sign in required', 'error');
        } else if (err.status === 422) {
          const fieldMsg = err.error.field ? `${err.error.field}: ` : '';
          toast(`${fieldMsg}${err.error.message}`, 'error');
        } else {
          toast(err.error.message || 'Failed to generate forecast', 'error');
        }
      } else if (err instanceof TypeError) {
        toast('You appear to be offline. Check your connection.', 'error');
      } else {
        toast('Failed to generate forecast', 'error');
      }
    } finally {
      setGenerating(false);
    }
  };

  /**
   * Create a real report schedule.
   *
   * The modal used to close with "Forecast report scheduled." without calling
   * anything, so an operator believed a report would arrive and none existed.
   */
  const createSchedule = async () => {
    const recipients = scheduleRecipients
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    if (recipients.length === 0) {
      toast('Add at least one recipient email', 'error');
      return;
    }
    setScheduling(true);
    try {
      await api.post('/api/v1/reports/schedules', {
        // The demand summary is the template that covers this page's numbers.
        template_id: 'demand_summary',
        cadence: scheduleCadence,
        recipients,
        format: scheduleFormat,
        enabled: true,
      });
      setScheduleOpen(false);
      toast(`Demand summary scheduled: ${scheduleCadence}.`);
    } catch (err) {
      if (err instanceof ApiException) {
        toast(err.error.message || 'Could not schedule the report', 'error');
      } else {
        toast('You appear to be offline. Check your connection.', 'error');
      }
    } finally {
      setScheduling(false);
    }
  };

  const handleExport = async (id: string) => {
    if (!id) return;
    setExportingId(id);
    try {
      const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';
      const url = `${BASE}/api/v1/forecasts/${id}/export?format=csv`;
      const headers: Record<string, string> = { Accept: 'text/csv' };
      const auth = getAuthHeader();
      Object.assign(headers, auth);
      const res = await fetch(url, { headers });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        let message = text;
        try {
          const j = JSON.parse(text);
          message = j?.error?.message || message;
        } catch {}
        throw new Error(message || `Export failed ${res.status}`);
      }
      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition');
      let filename = `forecast-${id}.csv`;
      if (disposition) {
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match) filename = match[1];
      }
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
      toast('Forecast exported as CSV.');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Export failed';
      if (msg.toLowerCase().includes('failed to fetch') || msg.toLowerCase().includes('network')) {
        toast('You appear to be offline. Check your connection.', 'error');
      } else {
        toast(msg, 'error');
      }
    } finally {
      setExportingId(null);
    }
  };

  // Prepare chart data: merge historical demand + forecasts chronologically
  // so the graph always shows something (past fills) even before the first
  // forecast, and the clock's targetDate (Africa/Kigali) matches the labels.
  const chartData: ForecastPoint[] | undefined = useMemo(() => {
    const histMap = new Map(historical.map(h => [h.date, h.demand_qty]));
    const forecastMap = new Map<string, ForecastRecord>();
    for (const f of forecasts) {
      const key = normalizeForecast(f).targetDate;
      if (!forecastMap.has(key)) forecastMap.set(key, f);
    }
    const allDates = Array.from(new Set([...historical.map(h=>h.date), ...Array.from(forecastMap.keys())])).sort();
    if (allDates.length === 0) return undefined;
    return allDates.map(dateStr => {
      const hist = histMap.get(dateStr) ?? null;
      const rec = forecastMap.get(dateStr);
      const n = rec ? normalizeForecast(rec) : null;
      // UTC noon, then read in the workspace zone: a bare `T12:00:00` is parsed
      // as *browser* local noon, so a viewer far enough west would land on the
      // next Kigali day and label the point with the wrong date.
      const label = new Date(dateStr + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'Africa/Kigali' });
      return {
        label,
        historical: hist,
        forecast: n ? n.point : null,
        // `null` (not `undefined`) when there is no forecast row: the chart
        // treats the two differently, and ForecastPoint allows only null.
        band: n ? ([n.lower, n.upper] as [number, number]) : null,
      };
    });
  }, [historical, forecasts]);

  /*
    The chart gallery. All four views are derived from the same two sources the
    breakdown table uses — the forecast rows and the ingested history — so the
    panels cannot disagree with the numbers underneath them.
  */
  const forecastDays = useMemo<ForecastDayPoint[]>(() =>
  [...forecasts].
  sort((a, b) =>
  normalizeForecast(a).targetDate.localeCompare(normalizeForecast(b).targetDate)).
  map((row) => {
    const n = normalizeForecast(row);
    return { label: shortDay(n.targetDate), point: n.point, lower: n.lower, upper: n.upper };
  }), [forecasts]);

  const forecastValues = useMemo(
    () => forecasts.map((row) => normalizeForecast(row).point),
    [forecasts],
  );
  const historyValues = useMemo(() => historical.map((row) => row.demand_qty), [historical]);

  /*
    Breakdown filters. `match` reads the normalised row rather than the raw
    record, because the API answers with either `point`/`lower`/`upper` or the
    `*_bound` spellings depending on the endpoint.
  */
  const breakdownFilters = useMemo<FilterDef<ForecastRecord>[]>(() => [
  {
    key: 'method',
    label: 'Method',
    options: [
    { value: '', label: 'Every method' },
    { value: 'model', label: 'Model forecast' },
    { value: 'fallback', label: 'Historical average' }],

    match: (row, value) => (normalizeForecast(row).fallback ? 'fallback' : 'model') === value
  },
  {
    key: 'window',
    label: 'Dates',
    options: [
    { value: '', label: 'Whole range' },
    { value: 'next7', label: 'Next 7 days' },
    { value: 'next30', label: 'Next 30 days' },
    { value: 'past', label: 'Already past' }],

    match: (row, value) => {
      const date = normalizeForecast(row).targetDate;
      if (value === 'past') return date < today;
      if (value === 'next7') return date >= today && date <= addDaysISO(today, 7);
      return date >= today && date <= addDaysISO(today, 30);
    }
  },
  {
    key: 'range',
    label: 'Range width',
    options: [
    { value: '', label: 'Any range' },
    { value: 'tight', label: 'Tight (under half the estimate)' },
    { value: 'wide', label: 'Wide (half the estimate or more)' }],

    match: (row, value) => {
      const n = normalizeForecast(row);
      // With no positive estimate the ratio is meaningless, so those rows are
      // reported as "wide" rather than divided by zero.
      if (n.point <= 0) return value === 'wide';
      const ratio = (n.upper - n.lower) / n.point;
      return value === 'tight' ? ratio < 0.5 : ratio >= 0.5;
    }
  }],
  [today]);

  const breakdown = useListState<ForecastRecord>({
    key: 'forecast-breakdown',
    items: forecasts,
    filters: breakdownFilters,
    searchFields: (row) => {
      const n = normalizeForecast(row);
      return [
      n.targetDate,
      shortDay(n.targetDate),
      n.fallback ? 'historical average' : 'model',
      n.modelVersion].
      join(' ');
    },
    sortValue: (row, key) =>
    key === 'point' ? normalizeForecast(row).point : normalizeForecast(row).targetDate,
    defaultSort: { key: 'date', dir: 'asc' },
    // Five keeps the panel the height it always was; the pager reveals the rest,
    // which is what the old "View all forecast data" toggle did.
    pageSize: 5
  });

  /*
    The day this screen is planning for: the same date the calendar card shows
    and "Generate Forecast" writes. Everything below — KPIs, the preparation
    comparison, and the "Why this forecast?" panel — describes this one day.

    It used to be the newest row in the loaded window, which reaches 30 days
    ahead, so the date control and the calendar said 31 Oct while those panels
    answered for 14 Nov: the same screen showed two different days and neither
    said which one it was describing. The newest available row is used only when
    the planning day has no forecast, and every label then names its own date.
  */
  const focusDate = addDaysISO(today, 1);
  const focusForecast = useMemo(() => {
    if (forecasts.length === 0) return null;
    const forDay = forecasts.find((row) => normalizeForecast(row).targetDate === focusDate);
    if (forDay) return forDay;
    return (
      [...forecasts]
        .sort((a, b) =>
          normalizeForecast(a).targetDate.localeCompare(normalizeForecast(b).targetDate),
        )
        .at(-1) ?? null
    );
  }, [forecasts, focusDate]);
  const focusNorm = focusForecast ? normalizeForecast(focusForecast) : null;

  const handleLocationApply = (label: string) => {
    // Label may be from legacy modal; try to map to real location name
    const found = locations.find((l) => l.name === label);
    if (found) {
      setSelectedLocationId(found.id);
      setSelectedLocationName(found.name);
    } else if (label === 'All Locations') {
      if (locations.length > 0) {
        setSelectedLocationId(locations[0].id);
        setSelectedLocationName(locations[0].name);
      } else {
        setSelectedLocationName(label);
        setSelectedLocationId(null);
      }
    } else {
      // Fallback: treat label as name even if not in list (e.g. seed names)
      const byName = locations.find((l) => l.name.toLowerCase() === label.toLowerCase());
      if (byName) {
        setSelectedLocationId(byName.id);
        setSelectedLocationName(byName.name);
      } else {
        setSelectedLocationName(label);
      }
    }
  };

  const goToIngestion = () => {
    if (onNavigate) onNavigate('ingestion');
    else navigation.navigate('ingestion');
  };

  // Derived KPI values. Every one of these is `null` when there is nothing to
  // show: a missing forecast used to render an invented "87%", and the "Peak
  // Day" tile actually showed the newest row while claiming to be the busiest.
  const expectedDemand = focusNorm ? focusNorm.point.toLocaleString('en-US') : '—';
  const coveragePct = focusNorm ? Math.round((focusNorm.coverage || 0.9) * 100) : null;

  // The busiest day in the window actually loaded, computed from the rows.
  const peak = useMemo(() => {
    if (forecasts.length === 0) return null;
    return forecasts
      .map(normalizeForecast)
      .reduce((busiest, current) => (current.point > busiest.point ? current : busiest));
  }, [forecasts]);
  const peakDayLabel = peak ? shortDay(peak.targetDate) : '—';
  const focusDayLabel = focusNorm ? shortDay(focusNorm.targetDate) : null;

  // Who can act on the numbers on this page.
  const canSimulate = permissions.includes('policies:simulate');
  const canScheduleReport = permissions.includes('reports:generate');

  return (
    <>
      {/* Filter bar */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_auto]">
        {/*
          Steps 1 and 2 in the order the proposal's user flow asks for them
          (§3.22: "select a restaurant, select a forecast date"). The controls
          are numbered and worded as questions, because a first-time operator
          does not know that "Location / Site" means "my restaurant".
        */}
        <button
          type="button"
          onClick={() => setPicker('location')}
          className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          <MapPinIcon className="h-5 w-5 shrink-0 text-brand-600" strokeWidth={1.75} />
          <span className="min-w-0 flex-1">
            <span className="block text-meta text-slate-500">Step 1 · Which restaurant?</span>
            <span className="block truncate text-control font-medium text-slate-900">
              {locationsLoading ? 'Loading…' : selectedLocationName}
            </span>
          </span>
          <ChevronDownIcon className="h-4 w-4 shrink-0 text-slate-500" />
        </button>

        <button
          type="button"
          onClick={() => setPicker('range')}
          className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left transition-colors duration-150 ease-out hover:border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          
          <CalendarIcon className="h-5 w-5 shrink-0 text-brand-600" strokeWidth={1.75} />
          <span className="min-w-0 flex-1">
            <span className="block text-meta text-slate-500">Step 2 · Which dates?</span>
            <span className="block truncate text-control font-medium text-slate-900">
              {range} → Next 30 days
            </span>
          </span>
          <ChevronRightIcon className="h-4 w-4 shrink-0 text-slate-500" />
        </button>

        <div className="rounded-xl border border-slate-200 bg-white px-3.5 py-2.5">
          <span className="block text-meta text-slate-500">Step 3 · How to show it</span>
          <div className="mt-1.5 flex gap-1" role="group" aria-label="Granularity">
            {granularities.map((option) => {
              const isActive = option === granularity;
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => setGranularity(option)}
                  className={[
                  'flex-1 rounded-md px-2 py-2.5 text-body-xs sm:py-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                  isActive ?
                  'bg-brand-600 font-semibold text-white' :
                  'font-medium text-slate-600 hover:bg-slate-100'].
                  join(' ')}>
                  
                  {option}
                </button>);

            })}
          </div>
        </div>

        <button
          type="button"
          onClick={generate}
          disabled={generating || !selectedLocationId}
          className="inline-flex items-center justify-center gap-2.5 rounded-xl bg-brand-700 px-5 py-3 text-body font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">
          
          <LightbulbIcon
            className={['h-[18px] w-[18px]', generating ? 'animate-pulse' : ''].join(' ')}
            strokeWidth={2} />
          
          {generating ? 'Generating…' : 'Generate Forecast'}
        </button>
        <p className="text-caption text-slate-500 sm:col-span-2 xl:hidden">
          Pick a restaurant and dates, then generate. The forecast, its likely
          range, what to prepare and why are shown together below.
        </p>
      </div>

      {/*
        The model was trained on public restaurant data from Japan. Saying so
        beside the numbers is the difference between a decision aid and a
        confident guess: an operator in Kigali needs to know the data does not
        come from businesses like theirs.
      */}
      <div
        role="note"
        aria-label="Training data and accuracy limit"
        className="mt-3 flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 dark:border-amber-900/60 dark:bg-amber-950/30">
        
        <InfoIcon className="mt-0.5 h-[18px] w-[18px] shrink-0 text-amber-700 dark:text-amber-400" strokeWidth={1.75} />
        <p className="text-body-sm leading-relaxed text-amber-900 dark:text-amber-100">
          <span className="font-semibold">Trained on restaurant data from Japan.</span>{' '}
          Forecasts can be wrong, especially on very busy days and for businesses unlike those
          restaurants. Use your own judgement before you buy or prepare food.
        </p>
      </div>

      {/* Calendar clock: ML features for the forecast target date (as_of 23:59) */}
      <div className="mt-3">
        <CalendarClock targetDate={addDaysISO(today, 1)} />
      </div>

      {/* KPI cards: plain language first, with the unit spelled out. */}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="flex flex-col gap-1.5">
          <StatCard
            label="Expected customers"
            value={expectedDemand}
            sub={focusNorm ? `for ${focusDayLabel}` : 'generate a forecast to see this'}
            icon={ArrowUpIcon}
            tone={focusNorm?.fallback ? 'warning' : 'default'} />
          {focusNorm && (
            <p className="flex flex-wrap items-center gap-1.5 px-1 text-caption text-slate-500">
              {focusNorm.fallback ? (
                <>
                  <StatusPill tone="warning">Limited history</StatusPill>
                  <span className="text-amber-700">Needs 7+ days for a model forecast</span>
                </>
              ) : (
                <>
                  <span className="font-semibold text-brand-700">Model forecast</span>
                  <span>· model {focusNorm.modelVersion.slice(0, 8)}</span>
                </>
              )}
            </p>
          )}
        </div>

        <StatCard
          label="How sure we are"
          value={coveragePct === null ? '—' : `${coveragePct}%`}
          sub={
            coveragePct === null
              ? 'no forecast yet'
              : `the range below holds about ${Math.round(coveragePct / 10)} days in 10`
          }
          icon={InfoIcon}
          tone={focusNorm?.fallback ? 'warning' : 'default'} />

        <StatCard
          label="Busiest day ahead"
          value={peak ? `${peak.point.toLocaleString('en-US')} guests` : '—'}
          sub={peak ? peakDayLabel : 'nothing forecast in this window'}
          icon={CalendarIcon}
          tone="info" />

        <StatCard
          label="Likely range"
          value={
            focusNorm
              ? `${focusNorm.lower.toLocaleString('en-US')}–${focusNorm.upper.toLocaleString('en-US')}`
              : '—'
          }
          sub={
            focusNorm
              ? `guests on the day, ${coveragePct}% of the time`
              : 'needs a forecast'
          }
          icon={ChevronDownIcon}
          tone={focusNorm?.fallback ? 'warning' : 'success'} />
      </div>

      {/*
        The decision layer, in the order the proposal's user flow asks for it
        (§3.22): the preparation comparison first, then the explanation for the
        number the comparison is built on. These sat in separate admin screens
        before, so the operator who needs them never saw them together.
      */}
      <div className="mt-3 grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <PreparePanel
          locationId={selectedLocationId}
          locationName={selectedLocationName}
          forecast={
            focusNorm
              ? {
                  target_date: focusNorm.targetDate,
                  point: focusNorm.point,
                  lower: focusNorm.lower,
                  upper: focusNorm.upper,
                  coverage: focusNorm.coverage,
                }
              : null
          }
          canSimulate={canSimulate} />
        <ForecastWhy forecastId={focusForecast?.id ?? null} targetDate={focusDayLabel} />
      </div>

      {/* Chart + insights */}
      <div className="mt-3 grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
        <section
          aria-labelledby="forecast-overview-heading"
          className="min-w-0 rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5">
          
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div>
              <h2
                id="forecast-overview-heading"
                className="flex items-center gap-2 text-h4 font-bold tracking-tight text-slate-900">
                
                Demand Forecast Overview
                <InfoIcon className="h-4 w-4 text-slate-500" />
              </h2>
              <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-body-xs text-slate-600">
                <span className="flex items-center gap-2">
                  <span className="h-0.5 w-6 rounded bg-brand-700" />
                  Historical Demand
                </span>
                <span className="flex items-center gap-2">
                  <span
                    className="h-0.5 w-6 rounded"
                    style={{
                      backgroundImage:
                      'repeating-linear-gradient(to right, #17a294 0 6px, transparent 6px 11px)'
                    }} />
                  
                  Forecast (AI Prediction)
                </span>
              </div>
            </div>
            <p className="max-w-[190px] text-meta leading-snug text-slate-500 sm:text-right">
              {focusNorm
                ? `Shaded area is the ${Math.round(focusNorm.coverage * 100)}% range we expect demand to fall in`
                : 'Shaded area is the forecast range'}
            </p>
          </div>

          <div className="mt-3">
            {forecastsLoading ? (
              <div className="flex h-[200px] items-center justify-center sm:h-[220px]">
                <Spinner label="Loading forecasts" className="h-8 w-8" />
              </div>
            ) : forecastsError ? (
              <EmptyState
                variant={forecastsError.variant}
                title={forecastsError.status === 401 ? 'Sign in required' : forecastsError.variant === 'offline' ? 'You are offline' : 'Failed to load forecasts'}
                description={forecastsError.message + (forecastsError.reference ? ` (ref: ${forecastsError.reference})` : '')}
                action={{ label: 'Retry', onClick: () => void fetchForecasts() }}
              />
            ) : !chartData ? (
              <EmptyState
                variant="empty"
                title="No data yet"
                description="Ingest demand for this location to see history, then generate your first forecast."
                hints={['Past days come from your imported demand', 'Forecasts need 7+ days of history']}
                action={{ label: 'Go to ingestion', onClick: goToIngestion }}
                secondaryAction={{ label: 'Generate forecast', onClick: () => void generate() }}
              />
            ) : (
              <ForecastChart data={chartData} />
            )}
          </div>

          {focusNorm && focusNorm.fallback && (
            <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <StatusPill tone="warning">Limited history</StatusPill>
              <span className="text-meta text-amber-800">Model forecast with limited history. 7+ days gives tighter intervals.</span>
            </div>
          )}
          {focusNorm && (
            <div className="mt-2 flex flex-wrap gap-2 text-fine text-slate-500">
              <span>model version {focusNorm.modelVersion.slice(0, 8) || '—'}</span>
              <span>· this range holds about {Math.round(focusNorm.coverage * 10)} days in 10</span>
            </div>
          )}
        </section>

        <section
          aria-labelledby="ai-insights-heading"
          className="min-w-0 rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5">
          
          <h2
            id="ai-insights-heading"
            className="flex items-center gap-2.5 text-h4 font-bold tracking-tight text-slate-900">
            
            <LightbulbIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
            AI Insights
          </h2>

          {focusForecast ? (
            <ul className="mt-4 flex flex-col gap-4">
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50">
                  <LightbulbIcon className="h-4 w-4 text-brand-600" strokeWidth={2} />
                </span>
                <p className="text-body-sm leading-relaxed text-slate-700">Around {focusNorm?.point.toLocaleString('en-US')} visitors are expected on {focusDayLabel}, most likely between {focusNorm?.lower.toLocaleString('en-US')} and {focusNorm?.upper.toLocaleString('en-US')}.</p>
              </li>
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50">
                  <LightbulbIcon className="h-4 w-4 text-brand-600" strokeWidth={2} />
                </span>
                <p className="text-body-sm leading-relaxed text-slate-700">As of {focusNorm?.targetDate} · model version {focusNorm?.modelVersion.slice(0, 8) || '—'} · {focusNorm?.fallback ? 'Historical average (needs 7 days)' : 'Model forecast'}</p>
              </li>
              <li className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50">
                  <LightbulbIcon className="h-4 w-4 text-brand-600" strokeWidth={2} />
                </span>
                <p className="text-body-sm leading-relaxed text-slate-700">The shaded band is how uncertain the forecast is: preparing nearer the top of the range leaves less unmet demand but more leftover food.</p>
              </li>
            </ul>
          ) : (
            <p className="mt-3 text-body-xs text-slate-500">Select a location and generate a forecast to see AI insights.</p>
          )}

          <button
            type="button"
            onClick={() => onNavigate?.('analytics')}
            className="tap-expand mt-5 inline-flex items-center gap-2 rounded text-control font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
            
            View full analysis
            <ArrowRightIcon className="h-4 w-4" strokeWidth={2} />
          </button>
        </section>
      </div>

      {/*
        The same forecast, four ways. Each panel reads data already on the page
        — the forecast rows and the imported history — so the gallery can never
        disagree with the table below it.
      */}
      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel
          title="Forecast by day"
          description="One bar per day. The whisker is that day's 90% range, so a tall bar with a wide whisker reads as uncertain rather than busy.">
          
          <ForecastByDayChart data={forecastDays} />
        </Panel>

        <Panel
          title="How demand is spread"
          description="How many forecast days land in each demand band.">
          
          <DemandDistributionChart values={forecastValues} scope="forecast" />
        </Panel>

        <Panel
          title="Past demand distribution"
          description="How many recorded days land in each demand band.">
          
          <DemandDistributionChart values={historyValues} scope="history" />
        </Panel>

        <Panel
          title="Busiest weekdays"
          description="Average demand per weekday across the imported history.">
          
          <WeekdayProfileChart rows={historical} />
        </Panel>
      </div>

      {/* Location error banner */}
      {locationsError && (
        <div className="mt-3">
          <EmptyState
            variant={locationsError.variant}
            title={locationsError.status === 401 ? 'Sign in required' : locationsError.variant === 'offline' ? 'You are offline' : 'Failed to load locations'}
            description={locationsError.message + (locationsError.reference ? ` (ref: ${locationsError.reference})` : '')}
            action={{ label: 'Retry', onClick: () => void fetchLocations() }}
          />
        </div>
      )}

      {/* Breakdown table */}
      <section
        aria-labelledby="forecast-breakdown-heading"
        className="mt-3 rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5">
        
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2
            id="forecast-breakdown-heading"
            className="flex items-center gap-2 text-h4 font-bold tracking-tight text-slate-900">
            
            Forecast Breakdown
            <InfoIcon className="h-4 w-4 text-slate-500" />
          </h2>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!focusForecast || exportingId !== null}
              onClick={() => focusForecast && void handleExport(focusForecast.id)}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-50">
              
              <DownloadIcon className="h-4 w-4 text-brand-600" strokeWidth={2} />
              {exportingId ? 'Exporting…' : 'Export CSV'}
            </button>
            {canScheduleReport && (
              <button
                type="button"
                onClick={() => setScheduleOpen(true)}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-body-sm font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                
                <CalendarClockIcon className="h-4 w-4 text-brand-600" strokeWidth={2} />
                Schedule Report
              </button>
            )}
          </div>
        </div>

        {forecastsLoading ? (
          <div className="mt-4 flex items-center justify-center py-10">
            <Spinner label="Loading" className="h-8 w-8" />
          </div>
        ) : forecastsError ? (
          <div className="mt-4">
            <EmptyState
              variant={forecastsError.variant}
              title={forecastsError.status === 401 ? 'Sign in required' : forecastsError.variant === 'offline' ? 'You are offline' : 'Failed to load forecasts'}
              description={forecastsError.message + (forecastsError.reference ? ` (ref: ${forecastsError.reference})` : '')}
              action={{ label: 'Retry', onClick: () => void fetchForecasts() }}
            />
          </div>
        ) : forecasts.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              variant="empty"
              title="No forecasts yet"
              description="There are no forecasts for this location and date range."
              hints={['Import demand first', 'Pick a location with at least 7 days of history']}
              action={{ label: 'Go to ingestion', onClick: goToIngestion }}
              secondaryAction={{ label: 'Generate forecast', onClick: () => void generate() }}
            />
          </div>
        ) : (
          <>
            {/* Search, named filters and removable chips come from the shared
                toolbar, so this table behaves like every other list in the app. */}
            <div className="mt-3">
              <FilterBar<ForecastRecord>
                search={breakdown.search}
                onSearch={breakdown.setSearch}
                searchPlaceholder="Search days…"
                filters={breakdownFilters}
                values={breakdown.filterValues}
                onFilterChange={breakdown.setFilter}
                onClear={breakdown.clearFilters}
                activeCount={breakdown.activeFilterCount}
                countLabel={
                breakdown.filtered !== breakdown.total ?
                `${breakdown.filtered} of ${breakdown.total} days` :
                undefined
                } />
              
            </div>
            {/* ResponsiveTable, not a raw table: on a phone this restyles each
                day into its own labelled card instead of a 720px table you
                have to drag sideways to read. */}
            <div className="mt-3">
              <ResponsiveTable<ForecastRecord>
                caption="Forecast by day"
                minWidth="min-w-[440px]"
                rows={breakdown.pageRows}
                rowKey={(row) => row.id}
                sort={breakdown.sort}
                onSort={breakdown.setSortKey}
                empty={
                <EmptyState
                  variant="empty"
                  size="sm"
                  title="No days match these filters"
                  description="Clear a filter to bring the forecast days back."
                  action={{ label: 'Clear filters', onClick: breakdown.clearFilters }} />

                }
                columns={[
                  {
                    key: 'date',
                    label: 'Day',
                    primary: true,
                    sortable: true,
                    render: (row) => (
                      <span className="text-body-sm text-slate-700">
                        {new Date(normalizeForecast(row).targetDate + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })}
                      </span>
                    ),
                  },
                  {
                    key: 'point',
                    label: 'Expected visitors',
                    sortable: true,
                    render: (row) => <span className="text-body-sm font-medium text-slate-900">{normalizeForecast(row).point.toLocaleString('en-US')}</span>,
                  },
                  {
                    key: 'coverage',
                    label: 'How sure',
                    render: (row) => {
                      const n = normalizeForecast(row);
                      return (
                        <span className="flex items-center gap-2.5">
                          <span className="text-body-sm text-slate-700">{Math.round(n.coverage * 100)}%</span>
                          <span className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-200">
                            <span className="block h-full rounded-full bg-brand-700" style={{ width: `${Math.round(n.coverage * 100)}%` }} />
                          </span>
                        </span>
                      );
                    },
                  },
                  {
                    key: 'range',
                    label: 'Likely range',
                    render: (row) => {
                      const n = normalizeForecast(row);
                      return <span className="text-body-sm text-slate-700">{n.lower.toLocaleString('en-US')} – {n.upper.toLocaleString('en-US')}</span>;
                    },
                  },
                  {
                    key: 'model',
                    label: 'Model',
                    cardHidden: true,
                    render: (row) => <span className="text-fine font-mono text-slate-500">{normalizeForecast(row).modelVersion.slice(0, 8) || '—'}</span>,
                  },
                  {
                    key: 'method',
                    label: 'Method',
                    render: (row) => normalizeForecast(row).fallback
                      ? <StatusPill tone="warning">Historical average (needs 7 days)</StatusPill>
                      : <StatusPill tone="success">Model</StatusPill>,
                  },
                ]}
              />
            </div>

            <Pagination
              page={breakdown.page}
              pageCount={breakdown.pageCount}
              onPage={breakdown.setPage}
              total={breakdown.filtered}
              from={breakdown.from}
              to={breakdown.to}
              noun="days" />
            
          </>
        )}
      </section>

      <DateRangeModal
        open={picker === 'range'}
        onClose={() => setPicker(undefined)}
        value={range}
        onApply={setRange} />
      
      <LocationPickerModal
        open={picker === 'location'}
        onClose={() => setPicker(undefined)}
        value={selectedLocationName}
        onApply={handleLocationApply}
        options={locations.length > 0 ? locations.map((l) => l.name) : undefined} />
      

      <Modal
        open={scheduleOpen}
        onClose={() => setScheduleOpen(false)}
        title="Schedule forecast report"
        description={`${selectedLocationName} · ${granularity.toLowerCase()} forecast`}
        footer={
        <>
            <ModalButton tone="ghost" onClick={() => setScheduleOpen(false)}>
              Cancel
            </ModalButton>
            <ModalButton
            onClick={() => void createSchedule()}
            disabled={scheduling || !scheduleRecipients.trim()}>
            
              {scheduling ? 'Scheduling…' : 'Schedule'}
            </ModalButton>
          </>
        }>
        
        <div className="flex flex-col gap-3">
          <Field id="fc-cadence" label="How often" hint="Delivered in the morning.">
            <select
              id="fc-cadence"
              value={scheduleCadence}
              onChange={(event) => setScheduleCadence(event.target.value)}
              className={inputClass}>
              <option value="daily">Every morning</option>
              <option value="weekly">Every Monday</option>
              <option value="monthly">1st of the month</option>
            </select>
          </Field>
          <Field id="fc-recipients" label="Send to" hint="Comma separated emails.">
            <input
              id="fc-recipients"
              type="text"
              value={scheduleRecipients}
              onChange={(event) => setScheduleRecipients(event.target.value)}
              className={inputClass} />
            
          </Field>
          <Field id="fc-format" label="Format">
            <select
              id="fc-format"
              value={scheduleFormat}
              onChange={(event) => setScheduleFormat(event.target.value)}
              className={inputClass}>
              <option value="pdf">PDF</option>
              <option value="csv">CSV</option>
              <option value="xlsx">XLSX</option>
            </select>
          </Field>
          <p className="text-micro leading-relaxed text-slate-500">
            Manage or turn off scheduled deliveries any time on the Reports page.
          </p>
        </div>
      </Modal>
    </>);

}
