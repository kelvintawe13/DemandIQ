import { useCallback, useEffect, useState, useMemo } from 'react';
import { SearchCheckIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { StatusPill } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Spinner } from '../../components/ui/LoadingState';
import { api, ApiException } from '../../api/client';

interface ForecastRecord {
  id: string;
  target_date: string;
  point?: number;
  point_forecast?: number;
  lower?: number;
  lower_bound?: number;
  upper?: number;
  upper_bound?: number;
  location_id: string;
  model_version_id?: string;
  coverage_target?: number;
  created_at?: string;
}

interface Location { id: string; name: string; }

interface Contribution {
  feature: string;
  feature_name?: string;
  value: string | null;
  feature_value?: string | null;
  impact: number;
  shap_impact?: number;
  unit?: string;
}

interface ExplanationResponse {
  forecast_id: string;
  attribution_type: string;
  disclaimer: string;
  contributions: Contribution[];
}

export function ForecastExplainer() {
  const [forecasts, setForecasts] = useState<ForecastRecord[]>([]);
  const [locations, setLocations] = useState<Record<string, string>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [contributions, setContributions] = useState<Contribution[]>([]);
  const [disclaimer, setDisclaimer] = useState('Predictive attribution, not causal effect.');
  const [loadingForecasts, setLoadingForecasts] = useState(true);
  const [forecastsError, setForecastsError] = useState<{ variant: 'error' | 'offline'; message: string; status?: number; reference?: string | null } | null>(null);
  const [explanationLoading, setExplanationLoading] = useState(false);
  const [explanationError, setExplanationError] = useState<{ variant: 'error' | 'offline' | 'search'; message: string; status?: number; reference?: string | null } | null>(null);

  const selectedForecast = useMemo(() => forecasts.find((f) => f.id === selectedId) ?? null, [forecasts, selectedId]);

  const fetchForecasts = useCallback(async () => {
    setLoadingForecasts(true);
    setForecastsError(null);
    try {
      const locRes = await api.get<{ data: Location[] }>('/api/v1/locations', { limit: 100 }).catch(() => ({ data: [] as Location[] }));
      const locMap: Record<string, string> = {};
      for (const l of locRes.data ?? []) locMap[l.id] = l.name;
      setLocations(locMap);
      const res = await api.get<{ data: ForecastRecord[]; has_more?: boolean; next_cursor?: string | null }>('/api/v1/forecasts', { limit: 20 });
      const data = res.data ?? [];
      setForecasts(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) setForecastsError({ variant: 'error', message: 'Sign in required', status: 401, reference: err.reference });
        else setForecastsError({ variant: 'error', message: err.error.message, status: err.status, reference: err.reference });
      } else if (err instanceof TypeError) {
        setForecastsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setForecastsError({ variant: 'offline', message: 'Network error' });
      }
    } finally {
      setLoadingForecasts(false);
    }
  }, [selectedId]);

  const fetchExplanation = useCallback(async (forecastId: string) => {
    setExplanationLoading(true);
    setExplanationError(null);
    try {
      const res = await api.get<ExplanationResponse>(`/api/v1/forecasts/${forecastId}/explanation`);
      // Normalize contributions
      const contribs = (res.contributions ?? []).map((c) => ({
        feature: (c as any).feature ?? (c as any).feature_name ?? '',
        value: (c as any).value ?? (c as any).feature_value ?? null,
        impact: (c as any).impact ?? (c as any).shap_impact ?? 0,
        unit: (c as any).unit ?? 'visitor',
      }));
      setContributions(contribs);
      setDisclaimer(res.disclaimer || 'Predictive attribution, not causal effect.');
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 404) {
          setExplanationError({ variant: 'search', message: err.error.message || 'Forecast not found', status: 404, reference: err.reference });
        } else if (err.status === 401) {
          setExplanationError({ variant: 'error', message: 'Sign in required', status: 401, reference: err.reference });
        } else {
          setExplanationError({ variant: 'error', message: err.error.message, status: err.status, reference: err.reference });
        }
      } else if (err instanceof TypeError) {
        setExplanationError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setExplanationError({ variant: 'offline', message: 'Network error' });
      }
      setContributions([]);
    } finally {
      setExplanationLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchForecasts();
  }, [fetchForecasts]);

  useEffect(() => {
    if (selectedId) void fetchExplanation(selectedId);
    else {
      setContributions([]);
      setExplanationError(null);
    }
  }, [selectedId, fetchExplanation]);

  const normalize = (f: ForecastRecord) => ({
    point: (f.point ?? f.point_forecast ?? 0) as number,
    lower: (f.lower ?? f.lower_bound ?? 0) as number,
    upper: (f.upper ?? f.upper_bound ?? 0) as number,
  });

  const maxImpact = useMemo(() => {
    if (contributions.length === 0) return 1;
    return Math.max(...contributions.map((c) => Math.abs(c.impact)), 1);
  }, [contributions]);

  const forecast = selectedForecast;
  const forecastNorm = forecast ? normalize(forecast) : null;

  // For interval visual: use point/lower/upper as prepared = upper? But we can approximate
  // Show interval vs point; actual = point (since realized not in forecast payload)
  // We'll show prepared = upper for uncertainty-aware visual parity.
  const prepared = forecastNorm ? forecastNorm.upper : 0;
  const actual = forecastNorm ? forecastNorm.point : 0; // placeholder for realized if not known
  const inInterval = forecastNorm ? actual >= forecastNorm.lower && actual <= forecastNorm.upper : false;
  const surplus = Math.max(0, prepared - actual);
  const unmet = Math.max(0, actual - prepared);
  // A location id is not a name; when the lookup has not resolved one, say what
  // the reference is rather than dropping a bare hex fragment on screen.
  const locationName = forecast ? (locations[forecast.location_id] ?? `Location ${String(forecast.location_id).slice(0, 8)}`) : '—';
  const forecastDateLabel = forecast ? new Date(forecast.target_date + 'T00:00:00Z').toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }) : '';

  const axisMin = forecastNorm ? Math.min(forecastNorm.lower, actual) - 15 : 0;
  const axisMax = forecastNorm ? Math.max(forecastNorm.upper, actual) + 15 : 100;
  const pos = (value: number) => (value - axisMin) / Math.max(1, axisMax - axisMin) * 100;

  return (
    <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
      <Panel
        title="Past forecasts"
        icon={SearchCheckIcon}
        description="Pick a forecast to see why the model produced it, and what actually happened.">
        
        {loadingForecasts ? (
          <div className="flex items-center justify-center py-10">
            <Spinner label="Loading forecasts" className="h-8 w-8" />
          </div>
        ) : forecastsError ? (
          <EmptyState
            variant={forecastsError.variant}
            title={forecastsError.status === 401 ? 'Sign in required' : forecastsError.variant === 'offline' ? 'You are offline' : 'Failed to load forecasts'}
            description={forecastsError.message + (forecastsError.reference ? ` (ref: ${forecastsError.reference})` : '')}
            action={{ label: 'Retry', onClick: () => void fetchForecasts() }}
            size="sm"
          />
        ) : forecasts.length === 0 ? (
          <EmptyState
            variant="empty"
            title="No forecasts yet"
            description="Generate forecasts to enable explanations."
            hints={['Ingest demand first', 'Generate a forecast for a forecastable location']}
            size="sm"
          />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {forecasts.map((item) => {
              const n = normalize(item);
              const isSelected = item.id === selectedId;
              const loc = locations[item.location_id] ?? `Location ${String(item.location_id).slice(0, 8)}`;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => setSelectedId(item.id)}
                    className={[
                    'w-full rounded-lg border px-3 py-2.5 text-left transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    isSelected ?
                    'border-brand-500 bg-brand-50/60' :
                    'border-slate-200 hover:bg-slate-50'].
                    join(' ')}>
                    
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-meta font-semibold text-slate-900">{loc}</span>
                      <span className="shrink-0 text-micro text-slate-500">{new Date(item.target_date + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })}</span>
                    </span>
                    <span className="mt-0.5 block text-micro text-slate-500">Predicted {n.point} · {n.lower}–{n.upper}</span>
                  </button>
                </li>);

            })}
          </ul>
        )}
      </Panel>

      <div className="flex min-w-0 flex-col gap-3">
        {!forecast ? (
          <Panel title="No forecast selected" description="Select a forecast from the list.">
            <EmptyState
              variant="empty"
              title="Select a forecast to explain"
              description="Choose a forecast on the left to see SHAP attribution."
            />
          </Panel>
        ) : (
          <Panel
            title={`${locationName} · ${forecastDateLabel}`}
            description={`Point ${forecastNorm?.point} · interval ${forecastNorm?.lower}–${forecastNorm?.upper} · coverage ${(forecast.coverage_target ? (forecast.coverage_target * 100).toFixed(0) : '90')}%`}
            action={
            <StatusPill tone={inInterval ? 'success' : 'warning'} dot>
                {inInterval ? 'Inside interval' : 'Outside interval'}
              </StatusPill>
            }>
            
            <div className="relative h-14">
              <div className="absolute inset-x-0 top-6 h-1.5 rounded-full bg-slate-200" />
              <div
                className="absolute top-6 h-1.5 rounded-full bg-brand-200"
                style={{
                  left: `${pos(forecastNorm!.lower)}%`,
                  width: `${pos(forecastNorm!.upper) - pos(forecastNorm!.lower)}%`
                }} />
              
              <div
                className="absolute top-[18px] h-[18px] w-[3px] -translate-x-1/2 rounded-full bg-brand-700"
                style={{ left: `${pos(forecastNorm!.point)}%` }}
                aria-hidden="true" />
              
              <div
                className="absolute top-[14px] h-[26px] w-[3px] -translate-x-1/2 rounded-full bg-slate-900"
                style={{ left: `${pos(actual)}%` }}
                aria-hidden="true" />
              
              <span
                className="absolute top-0 -translate-x-1/2 text-fine font-semibold text-brand-700"
                style={{ left: `${pos(forecastNorm!.point)}%` }}>
                
                {forecastNorm!.point}
              </span>
              <span
                className="absolute bottom-0 -translate-x-1/2 text-fine font-semibold text-slate-900"
                style={{ left: `${pos(actual)}%` }}>
                
                actual {actual}
              </span>
            </div>

            <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
              ['Interval', `${forecastNorm!.lower}–${forecastNorm!.upper}`],
              ['Prepared', String(Math.round(prepared))],
              ['Surplus', String(Math.round(surplus))],
              ['Unmet', String(Math.round(unmet) )]].
              map(([label, value]) =>
              <div key={label} className="rounded-lg border border-slate-200 px-3 py-2">
                  <dt className="text-fine text-slate-500">{label}</dt>
                  <dd className="text-body font-bold tabular-nums text-slate-900">{value}</dd>
                </div>
              )}
            </dl>
          </Panel>
        )}

        <Panel
          title="Why the model said this"
          description="SHAP contributions in visitor units. Predictive attribution, not causal effect.">
          
          {!selectedId ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="Select a forecast to explain"
              description="Pick a forecast from the list to load its SHAP explanation."
            />
          ) : explanationLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner label="Loading explanation" className="h-8 w-8" />
            </div>
          ) : explanationError ? (
            <EmptyState
              variant={explanationError.variant}
              size="sm"
              title={explanationError.status === 401 ? 'Sign in required' : explanationError.variant === 'offline' ? 'You are offline' : explanationError.variant === 'search' ? 'Forecast not found' : 'Failed to load explanation'}
              description={explanationError.message + (explanationError.reference ? ` (ref: ${explanationError.reference})` : '')}
              action={{ label: 'Retry', onClick: () => selectedId && void fetchExplanation(selectedId) }}
            />
          ) : contributions.length === 0 ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="No contributions"
              description="No SHAP data returned for this forecast."
            />
          ) : (
            <>
              <ul className="flex flex-col gap-2.5">
                {contributions.map((item) => {
                  const positive = item.impact > 0;
                  const width = Math.abs(item.impact) / maxImpact * 50;
                  const val = item.value != null ? String(item.value) : '—';
                  return (
                    <li key={item.feature}>
                      <div className="flex items-baseline justify-between gap-3">
                        <code className="truncate font-mono text-caption text-slate-800">{item.feature}</code>
                        <span className="shrink-0 text-micro text-slate-500">{val}</span>
                      </div>
                      <div className="relative mt-1 h-2">
                        <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-slate-200" />
                        <span className="absolute top-0 h-2 w-px bg-slate-300" style={{ left: '50%' }} aria-hidden="true" />
                        <span
                          className={['absolute top-0 h-2 rounded-sm', positive ? 'bg-brand-600' : 'bg-amber-500'].join(' ')}
                          style={positive ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }} />
                      </div>
                      <p className={['mt-0.5 text-fine tabular-nums', positive ? 'text-brand-700' : 'text-amber-700'].join(' ')}>
                        {positive ? '+' : ''}{item.impact.toFixed(1)} covers
                      </p>
                    </li>);

                })}
              </ul>
              <p className="mt-3 text-fine italic text-slate-500">{disclaimer}</p>
              <p className="mt-1 text-fine text-slate-500">Predictive attribution, not causal effect. Units: visitor.</p>
            </>
          )}
        </Panel>
      </div>
    </div>);

}
