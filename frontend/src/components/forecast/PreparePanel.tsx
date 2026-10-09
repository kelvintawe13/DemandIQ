import { useCallback, useEffect, useState } from 'react';
import { ChefHatIcon, InfoIcon, ScaleIcon, ShieldCheckIcon } from 'lucide-react';
import { Panel } from '../ui/Panel';
import { StatusPill } from '../ui/StatusPill';
import { EmptyState } from '../ui/EmptyState';
import { PermissionNote } from '../ui/PermissionNote';
import { Spinner } from '../ui/LoadingState';
import { api, ApiException } from '../../api/client';
import { plainStatus } from '../../lib/plainLanguage';

/**
 * The preparation decision layer (proposal §3.17–3.20).
 *
 * The proposal's intended user flow ends at "compare preparation policies,
 * inspect the resulting surplus and unmet-demand metrics", and §2.9 adds that a
 * tool for small operators must be understandable without specialist skills.
 * This panel is that step, in the operator's own words: three transparent
 * preparation rules side by side, the quantity each implies, and what each
 * costs on both sides of the trade-off.
 *
 * Two things it deliberately does *not* do:
 *
 * * it never calls a surplus a waste figure; the quantities are
 *   visitor-equivalents (one guest ≈ one portion), which is a planning proxy,
 *   not kilograms (§3.18);
 * * it shows no realised outcome for a day that has not happened yet. The API
 *   returns `surplus_ps`/`unmet_ud` as null until the day is observed, and the
 *   panel says "not measured yet" instead of printing the forecast twice.
 */

export interface PrepareForecast {
  target_date: string;
  point: number;
  lower: number;
  upper: number;
  /** Interval coverage the bounds were built for (e.g. 0.9). */
  coverage: number;
}

interface PolicyRow {
  policy: string;
  /** null when the rule had no basis (e.g. no history); see unavailable_reason. */
  prepared_qty: number | null;
  surplus_sr?: number | null;
  surplus_ps?: number | null;
  unmet_ud?: number | null;
  planned_shortfall?: number | null;
  realised_demand?: number | null;
  basis?: 'forecast' | 'realised';
  unavailable_reason?: string | null;
  quantile?: number | null;
  lookback_days?: number | null;
}

interface SimulateResponse {
  results: PolicyRow[];
  target_date?: string;
  period_start?: string;
}

interface PreparePanelProps {
  locationId: string | null;
  locationName: string;
  forecast: PrepareForecast | null;
  /** POST /policies/simulate needs `policies:simulate`. */
  canSimulate: boolean;
}

/** The policies, described the way an operator would explain them. */
const POLICY_COPY: Record<string, { title: string; blurb: string; icon: typeof ChefHatIcon }> = {
  historical_average: {
    title: 'Your usual average',
    blurb: 'Prepare what this kind of day normally sells.',
    icon: ScaleIcon,
  },
  point_forecast: {
    title: 'Follow the forecast',
    blurb: 'Prepare exactly the number we expect.',
    icon: ChefHatIcon,
  },
  uncertainty_aware: {
    title: 'Forecast with a safety margin',
    blurb: 'Prepare a little more so a busy day does not catch you short.',
    icon: ShieldCheckIcon,
  },
  // Section 3.17's risk-aware rule. It is priced, not padded: the operator says
  // how much a lost customer costs next to a leftover, and the answer follows.
  risk_aware: {
    title: 'Balance the two costs',
    blurb: 'Weigh one missed customer against one leftover and prepare for the balance point.',
    icon: ShieldCheckIcon,
  },
};

const ORDER = ['historical_average', 'point_forecast', 'uncertainty_aware', 'risk_aware'];

const num = (value: number) => Math.round(value).toLocaleString('en-US');

function prettyDate(iso: string) {
  const when = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(when.getTime())) return iso;
  return when.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

export function PreparePanel({ locationId, locationName, forecast, canSimulate }: PreparePanelProps) {
  const [rows, setRows] = useState<PolicyRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetDate = forecast?.target_date ?? null;
  const point = forecast?.point ?? null;
  const lower = forecast?.lower ?? null;
  const upper = forecast?.upper ?? null;

  const simulate = useCallback(async () => {
    // Only simulate a complete forecast (a partial one would silently prepare
    // the operator for a made-up number) and only for a caller who holds the
    // capability. The effect below runs on mount regardless of the render
    // branch, so without this the panel fired a POST that was always going to
    // be refused with a 403.
    if (
      !canSimulate ||
      !locationId ||
      !targetDate ||
      point === null ||
      lower === null ||
      upper === null
    ) {
      setRows([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.post<SimulateResponse>('/api/v1/policies/simulate', {
        location_id: locationId,
        target_date: targetDate,
        point,
        lower,
        upper,
        // §3.16's nominal 90% level, matching the interval the operator is shown.
        quantile: 0.9,
      });
      setRows(res.results ?? []);
    } catch (err) {
      if (err instanceof ApiException) {
        setError(
          err.status === 403
            ? 'Your role can view forecasts but not run preparation comparisons.'
            : err.error.message || 'Could not compare preparation options.',
        );
      } else {
        setError('You appear to be offline. Check your connection.');
      }
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [canSimulate, locationId, targetDate, point, lower, upper]);

  useEffect(() => {
    void simulate();
  }, [simulate]);

  if (!forecast) {
    return (
      <Panel title="How much should I prepare?" icon={ChefHatIcon}>
        <EmptyState
          variant="empty"
          size="sm"
          title="No forecast to plan from yet"
          description="Generate a forecast for a location and date, and the preparation options appear here."
          hints={['Pick a location', 'Press Generate Forecast']}
        />
      </Panel>
    );
  }

  if (!canSimulate) {
    return (
      <Panel
        title="How much should I prepare?"
        icon={ChefHatIcon}
        description={`Preparation options for ${prettyDate(forecast.target_date)}`}>
        <p className="text-body-xs leading-relaxed text-slate-600">
          We expect about <strong>{num(forecast.point)}</strong> customers that day.
        </p>
        <PermissionNote>
          Comparing preparation options is a decision capability. An analyst, administrator or the
          owner can run the comparison with you; you can still read the forecast and its range
          above.
        </PermissionNote>
      </Panel>
    );
  }

  const byKind = new Map(rows.map((row) => [row.policy, row]));
  const ordered = ORDER.map((kind) => byKind.get(kind)).filter((row): row is PolicyRow => !!row);
  // The headline number is the uncertainty-aware rule: it is the policy the
  // proposal mandates for the decision layer, and the one whose coverage the
  // interval above the fold already states. The risk-aware rule is shown beside
  // it rather than promoted, because which of the two is preferable depends on
  // the site's relative cost of a leftover and a lost customer, a judgement the
  // operator makes, not one this panel can assert.
  const recommended = byKind.get('uncertainty_aware') ?? byKind.get('point_forecast');
  const realisedAvailable = ordered.some((row) => row.surplus_ps !== null && row.surplus_ps !== undefined);

  return (
    <Panel
      title="How much should I prepare?"
      icon={ChefHatIcon}
      description={`${locationName} · ${prettyDate(forecast.target_date)}`}
      action={
        <StatusPill tone="info">
          {`${Math.round((forecast.coverage || 0.9) * 100)}% range ${num(forecast.lower)}–${num(forecast.upper)}`}
        </StatusPill>
      }>
      {loading ? (
        <div className="flex items-center justify-center py-6">
          <Spinner label="Comparing preparation options" className="h-6 w-6" />
        </div>
      ) : error ? (
        <EmptyState
          variant="error"
          size="sm"
          title="Could not compare options"
          description={error}
          action={{ label: 'Try again', onClick: () => void simulate() }}
        />
      ) : ordered.length === 0 ? (
        <EmptyState
          variant="empty"
          size="sm"
          title="No options to show"
          description="The comparison returned no preparation options for this day."
        />
      ) : (
        <>
          {/* The answer, first and in one line. */}
          {recommended && recommended.prepared_qty != null && (
            <div className="rounded-xl border border-brand-200 bg-brand-50/60 px-3.5 py-3">
              <p className="text-fine font-semibold uppercase tracking-wide text-brand-800">
                Suggested for this day
              </p>
              <p className="mt-1 text-h2 font-bold leading-none tracking-tight text-slate-900">
                {num(recommended.prepared_qty)}
                <span className="ml-1.5 text-body-xs font-semibold text-slate-600">
                  portions to prepare
                </span>
              </p>
              <p className="mt-1.5 text-meta leading-relaxed text-slate-700">
                That is the forecast of {num(forecast.point)} plus a safety margin, so a busier-than-
                expected day is covered. Preparing less saves food when it is quiet; preparing more
                turns fewer customers away.
              </p>
            </div>
          )}

          {/* The rules, side by side, so the trade-off is visible. Two columns on
              a tablet (four rules in three columns wraps 3 + 1 and reads as a
              layout bug), four on a wide screen. */}
          <ul className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
            {ordered.map((row) => {
              // A policy this build does not know yet still reads as words rather
              // than `uncertainty aware`, the same vocabulary the rest of the UI uses.
              const copy = POLICY_COPY[row.policy] ?? {
                title: plainStatus(row.policy),
                blurb: '',
                icon: ChefHatIcon,
              };
              const Icon = copy.icon;
              const isRecommended = row.policy === recommended?.policy;
              return (
                <li
                  key={row.policy}
                  className={[
                    'flex flex-col rounded-lg border px-3 py-2.5',
                    isRecommended ? 'border-brand-300 bg-white' : 'border-slate-200 bg-white',
                  ].join(' ')}>
                  <div className="flex items-start justify-between gap-2">
                    <p className="flex items-center gap-1.5 text-meta font-semibold text-slate-900">
                      <Icon className="h-3.5 w-3.5 shrink-0 text-brand-600" strokeWidth={2} />
                      {copy.title}
                    </p>
                    {isRecommended && <StatusPill tone="success">Suggested</StatusPill>}
                  </div>
                  <p className="mt-1 text-h3-lg font-bold leading-none tabular-nums text-slate-900">
                    {row.prepared_qty === null ? '—' : num(row.prepared_qty)}
                  </p>
                  <p className="mt-0.5 text-micro text-slate-500">
                    {row.prepared_qty === null
                      ? 'no demand history to average yet'
                      : row.lookback_days
                        ? `portion${row.prepared_qty === 1 ? '' : 's'} · last ${row.lookback_days} days`
                        : 'portions'}
                  </p>
                  <p className="mt-1.5 text-micro leading-relaxed text-slate-600">{copy.blurb}</p>
                  <dl className="mt-2 flex flex-col gap-1 border-t border-slate-100 pt-2 text-micro">
                    <div className="flex items-baseline justify-between gap-2">
                      <dt className="text-slate-500">Above the forecast</dt>
                      <dd className="tabular-nums text-slate-800">
                        {row.surplus_sr === null || row.surplus_sr === undefined
                          ? 'not computed'
                          : row.surplus_sr > 0
                            ? `+${num(row.surplus_sr)}`
                            : 'none'}
                      </dd>
                    </div>
                    <div className="flex items-baseline justify-between gap-2">
                      <dt className="text-slate-500">If demand misses low</dt>
                      <dd className="tabular-nums text-slate-800">
                        {row.surplus_ps === null || row.surplus_ps === undefined
                          ? 'not measured yet'
                          : `+${num(row.surplus_ps)}`}
                      </dd>
                    </div>
                    <div className="flex items-baseline justify-between gap-2">
                      <dt className="text-slate-500">Customers short</dt>
                      <dd className="tabular-nums text-slate-800">
                        {row.unmet_ud === null || row.unmet_ud === undefined
                          ? 'not measured yet'
                          : num(row.unmet_ud)}
                      </dd>
                    </div>
                  </dl>
                </li>
              );
            })}
          </ul>

          {/* Where the numbers come from, and where they stop being evidence. */}
          <div className="mt-3 flex flex-col gap-1.5 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-600">
            <p className="flex items-start gap-1.5">
              <InfoIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
              <span>
                Portions are <strong>visitor-equivalents</strong> (one expected customer ≈ one
                portion), a planning proxy, not a weight of food.
              </span>
            </p>
            <p className="flex items-start gap-1.5">
              <ScaleIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
              <span>
                {realisedAvailable
                  ? 'Surplus and shortfall are measured against what actually sold that day.'
                  : 'The day has not happened yet, so how much would have been left over is not measured. Check a past date to see those figures.'}
              </span>
            </p>
          </div>
        </>
      )}
    </Panel>
  );
}
