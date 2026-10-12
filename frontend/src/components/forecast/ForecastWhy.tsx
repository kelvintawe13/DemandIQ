import { useCallback, useEffect, useState } from 'react';
import { LightbulbIcon } from 'lucide-react';
import { Panel } from '../ui/Panel';
import { EmptyState } from '../ui/EmptyState';
import { Spinner } from '../ui/LoadingState';
import { api, ApiException } from '../../api/client';

/**
 * "Why this number?" — the SHAP explanation for the forecast on screen (§3.24).
 *
 * The proposal requires the explanation to be part of the flow rather than a
 * separate analyst screen, and to be framed correctly: §2.6 and §3.24 insist a
 * feature's contribution to a prediction is *not* proof that it causes demand.
 * The API already returns that disclaimer in the payload, and it is rendered
 * verbatim here instead of being paraphrased into something stronger.
 *
 * Feature names arrive in schema form (`roll_mean_7`, `dow`) because they are
 * the model's inputs. The dictionary below is the operator-facing translation;
 * anything unmapped falls back to de-snake-cased text rather than an invented
 * label.
 */

interface Contribution {
  feature: string;
  value?: number | boolean | string | null;
  impact: number;
  unit?: string;
}

interface ExplanationResponse {
  forecast_id: string;
  attribution_type?: string;
  /**
   * Which path wrote these drivers: `booster` (the served model's own TreeSHAP)
   * or `stub` (the placeholder used when no model is loaded). The values alone
   * cannot tell the two apart, and only one of them is model evidence.
   */
  attribution_source?: 'booster' | 'stub' | 'mixed' | string | null;
  disclaimer?: string;
  contributions: Contribution[];
}

const FEATURE_LABELS: Record<string, string> = {
  lag_1: "Yesterday's customers",
  lag_7: 'Same day last week',
  lag_14: 'Same day two weeks ago',
  roll_mean_7: "Last week's daily average",
  roll_mean_28: "Last month's daily average",
  roll_std_28: 'How up and down last month was',
  roll_max_7: 'Your busiest day last week',
  roll_max_28: 'Your busiest day last month',
  roll_p90_28: 'A busy day last month',
  same_weekday_mean: 'Typical for this weekday',
  dow_mean_8w: 'This weekday over the last 8 weeks',
  dow_std_8w: 'Week-to-week variation',
  volatility_28: 'How changeable demand has been',
  dow: 'Day of the week',
  is_weekend: 'Weekend',
  month: 'Month of the year',
  is_public_holiday: 'Public holiday',
  day_before_holiday: 'Day before a holiday',
  day_after_holiday: 'Day after a holiday',
  location_area: 'Neighbourhood',
  location_cuisine: 'Venue or cuisine type',
  days_since_opening: 'How long the location has traded',
  reservation_count: 'Reservations booked',
  reservation_lead_time_days: 'How far ahead people booked',
  history_days: 'Days of history available',
};

const labelFor = (feature: string) =>
  FEATURE_LABELS[feature] ?? feature.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const num = (value: number) => Math.abs(value).toFixed(value % 1 === 0 ? 0 : 1);

interface ForecastWhyProps {
  /** Forecast to explain; `null` renders the "generate one first" state. */
  forecastId: string | null;
  targetDate?: string | null;
}

export function ForecastWhy({ forecastId, targetDate }: ForecastWhyProps) {
  const [contributions, setContributions] = useState<Contribution[]>([]);
  const [disclaimer, setDisclaimer] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!forecastId) {
      setContributions([]);
      setDisclaimer(null);
      setSource(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<ExplanationResponse>(`/api/v1/forecasts/${forecastId}/explanation`);
      const sorted = [...(res.contributions ?? [])].sort(
        (a, b) => Math.abs(b.impact) - Math.abs(a.impact),
      );
      setContributions(sorted.slice(0, 6));
      setDisclaimer(res.disclaimer ?? null);
      setSource(res.attribution_source ?? null);
    } catch (err) {
      if (err instanceof ApiException) {
        setError(
          err.status === 404
            ? 'No explanation is stored for this forecast yet.'
            : err.error.message || 'Could not load the explanation.',
        );
      } else {
        setError('You appear to be offline. Check your connection.');
      }
      setContributions([]);
      setSource(null);
    } finally {
      setLoading(false);
    }
  }, [forecastId]);

  useEffect(() => {
    void load();
  }, [load]);

  const largest = contributions.reduce((max, c) => Math.max(max, Math.abs(c.impact)), 0);

  return (
    <Panel
      title="Why this forecast?"
      icon={LightbulbIcon}
      description={
        targetDate
          ? `What pushed the number for ${targetDate} up or down. Nothing here is a proven cause.`
          : 'What pushed this number up or down. Nothing here is a proven cause.'
      }>
      {!forecastId ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="No forecast to explain yet"
          description="Generate a forecast and its main drivers appear here."
        />
      ) : loading ? (
        <div className="flex items-center justify-center py-6">
          <Spinner label="Loading the explanation" className="h-6 w-6" />
        </div>
      ) : error ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="Explanation unavailable"
          description={error}
          action={{ label: 'Try again', onClick: () => void load() }}
        />
      ) : contributions.length === 0 ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="No drivers recorded"
          description="This forecast has no stored feature attribution."
        />
      ) : (
        <>
          {source === 'stub' ? (
            <p
              role="note"
              className="mb-3 rounded-lg bg-amber-50 px-2.5 py-2 text-micro leading-relaxed text-amber-800">
              These drivers are placeholders: no trained model is loaded for this workspace, so
              nothing here is attributed to one.
            </p>
          ) : null}
          <ul className="flex flex-col gap-2">
            {contributions.map((item) => {
              const raised = item.impact >= 0;
              const width = largest > 0 ? Math.max(4, Math.round((Math.abs(item.impact) / largest) * 100)) : 4;
              return (
                <li key={item.feature} className="flex flex-col gap-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                    <span className="text-meta font-medium text-slate-800">
                      {labelFor(item.feature)}
                    </span>
                    <span
                      className={[
                        'text-meta font-semibold tabular-nums',
                        raised ? 'text-brand-700' : 'text-amber-700',
                      ].join(' ')}>
                      {raised ? '+' : '−'}
                      {num(item.impact)} {raised ? 'higher' : 'lower'}
                    </span>
                  </div>
                  <span className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <span
                      className={['block h-full rounded-full', raised ? 'bg-brand-600' : 'bg-amber-500'].join(' ')}
                      style={{ width: `${width}%` }}
                    />
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-600">
            {disclaimer ?? 'Predictive attribution, not causal effect.'}
          </p>
        </>
      )}
    </Panel>
  );
}
