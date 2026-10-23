import { useCallback, useEffect, useMemo, useState } from 'react';
import { SlidersHorizontalIcon, SaveIcon, CalendarIcon, MapPinIcon, ArrowRightIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { StatusPill } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { ResponsiveTable } from '../../components/ui/Table';
import { PermissionNote } from '../../components/ui/PermissionNote';
import { Spinner } from '../../components/ui/LoadingState';
import { useToast } from '../../contexts/ToastContext';
import { useSession } from '../../contexts/SessionContext';
import { useNavigation } from '../../contexts/NavigationContext';
import { api, ApiException } from '../../api/client';
import { todayISO, addDaysISO } from '../../lib/workspaceTime';

interface Policy {
  id: string;
  kind: string;
  quantile: number | null;
  lookback_days: number | null;
  /** Risk-aware only: cost of one missed customer vs one leftover portion. */
  risk_weight?: number | null;
  is_active: boolean;
}

interface SimResult {
  policy: string;
  /** null when the rule had no basis (e.g. no history); see unavailable_reason. */
  prepared_qty?: number | null;
  prepared_total?: number | null;
  surplus_sr?: number | null;
  /** null until the target day has an observed outcome. */
  surplus_ps?: number | null;
  unmet_ud?: number | null;
  surplus_days: number;
  unmet_days: number;
  /** Days in the period the rule could be worked out for (period simulation only). */
  prepared_days?: number;
  /** How many of those days have an outcome recorded (what PS/UD are measured on). */
  realised_days?: number;
  quantile: number | null;
  lookback_days: number | null;
  risk_weight?: number | null;
  unavailable_reason?: string | null;
  unit: string;
}

interface Location {
  id: string;
  name: string;
}

/** A figure that was never measured renders as an em dash, never as 0. */
const fmt1 = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : value.toFixed(1);

export function PolicyLab() {
  const [activeId, setActiveId] = useState<'historical' | 'point' | 'uncertainty' | 'risk'>('uncertainty');
  const [quantile, setQuantile] = useState(0.85);
  // Section 3.17 risk-aware policy: the cost of one missed customer relative to
  // one leftover portion. 1.0 means the two cost the same.
  const [riskWeight, setRiskWeight] = useState(2);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();
  const { navigate } = useNavigation();
  const { user } = useSession();
  // Simulating is an analyst's read; saving a workspace default is the
  // administrator's decision (PATCH /policies/{id} requires policies:write and
  // answers 403 for anyone else). The analyst sees the whole lab, and only the
  // button that would be rejected is disabled.
  const canWrite = (user.permissions ?? []).includes('policies:write');

  // Locations
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedLocationId, setSelectedLocationId] = useState<string | null>(null);
  const [selectedLocationName, setSelectedLocationName] = useState<string>('—');
  const [locationsError, setLocationsError] = useState<{ variant: 'error' | 'offline'; message: string; status?: number; reference?: string | null } | null>(null);

  // Policies
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [policiesLoading, setPoliciesLoading] = useState(true);
  const [policiesError, setPoliciesError] = useState<{ variant: 'error' | 'offline'; message: string; status?: number; reference?: string | null; field?: string | null } | null>(null);

  // Period: default 30 days ending today, plus horizon for simulate
  const periodStart = useMemo(() => addDaysISO(todayISO(), -14), []);
  const periodEnd = useMemo(() => addDaysISO(todayISO(), 14), []);
  // The period the aggregates cover, inclusive — the coverage counts below are
  // read against it so "12 of 29 days" means something.
  const periodDays = useMemo(() => {
    const start = new Date(`${periodStart}T00:00:00Z`).getTime();
    const end = new Date(`${periodEnd}T00:00:00Z`).getTime();
    return Math.round((end - start) / 86_400_000) + 1;
  }, [periodStart, periodEnd]);

  // Simulation
  const [results, setResults] = useState<SimResult[]>([]);
  const [simulateLoading, setSimulateLoading] = useState(false);
  const [simulateError, setSimulateError] = useState<{ variant: 'error' | 'offline'; message: string; status?: number; reference?: string | null; field?: string | null } | null>(null);
  const [hasSimulated, setHasSimulated] = useState(false);

  const activePolicyKind = useMemo(() => {
    if (activeId === 'historical') return 'historical_average';
    if (activeId === 'point') return 'point_forecast';
    if (activeId === 'risk') return 'risk_aware';
    return 'uncertainty_aware';
  }, [activeId]);

  const fetchLocations = useCallback(async () => {
    try {
      const res = await api.get<{ data: Location[]; has_more?: boolean; next_cursor?: string | null }>('/api/v1/locations', { limit: 100 });
      const data = res.data ?? [];
      // normalize shape: backend may return {data: Location[]} or Paginated
      const list = Array.isArray(res) ? (res as unknown as Location[]) : data;
      setLocations(list);
      if (list.length > 0 && !selectedLocationId) {
        setSelectedLocationId(list[0].id);
        setSelectedLocationName(list[0].name);
      }
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) setLocationsError({ variant: 'error', message: 'Sign in required', status: 401, reference: err.reference });
        else setLocationsError({ variant: 'error', message: err.error.message, status: err.status, reference: err.reference });
      } else if (err instanceof TypeError) {
        setLocationsError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setLocationsError({ variant: 'offline', message: 'You appear to be offline.' });
      }
    }
  }, [selectedLocationId]);

  const fetchPolicies = useCallback(async () => {
    setPoliciesLoading(true);
    setPoliciesError(null);
    try {
      const res = await api.get<{ data: Policy[] }>('/api/v1/policies');
      const data = res.data ?? [];
      setPolicies(data);
      // sync quantile slider with backend uncertainty policy
      const unc = data.find((p) => p.kind === 'uncertainty_aware');
      if (unc?.quantile) setQuantile(unc.quantile);
      // The risk-aware policy's own parameter: the stored cost ratio, so the
      // control opens on the workspace's configured value rather than a guess.
      const risk = data.find((p) => p.kind === 'risk_aware');
      if (risk?.risk_weight) setRiskWeight(risk.risk_weight);
      // Determine activeId from is_active
      const active = data.find((p) => p.is_active);
      if (active) {
        if (active.kind === 'historical_average') setActiveId('historical');
        else if (active.kind === 'point_forecast') setActiveId('point');
        else if (active.kind === 'risk_aware') setActiveId('risk');
        else setActiveId('uncertainty');
      }
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) setPoliciesError({ variant: 'error', message: 'Sign in required', status: 401, reference: err.reference });
        else setPoliciesError({ variant: 'error', message: err.error.message, status: err.status, reference: err.reference, field: err.error.field });
      } else if (err instanceof TypeError) {
        setPoliciesError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setPoliciesError({ variant: 'offline', message: 'Network error' });
      }
    } finally {
      setPoliciesLoading(false);
    }
  }, []);

  const simulate = useCallback(async () => {
    if (!selectedLocationId) return;
    setSimulateLoading(true);
    setSimulateError(null);
    try {
      const res = await api.post<{ location_id: string; period_start: string; period_end: string; results: SimResult[]; units?: string }>('/api/v1/policies/simulate', {
        location_id: selectedLocationId,
        period_start: periodStart,
        period_end: periodEnd,
        policies: ['historical_average', 'point_forecast', 'uncertainty_aware', 'risk_aware'],
        quantile,
        risk_weight: riskWeight,
      });
      setResults(res.results ?? []);
      setHasSimulated(true);
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) setSimulateError({ variant: 'error', message: 'Sign in required', status: 401, reference: err.reference });
        else if (err.status === 422) setSimulateError({ variant: 'error', message: err.error.message, status: 422, reference: err.reference, field: err.error.field });
        else setSimulateError({ variant: 'error', message: err.error.message, status: err.status, reference: err.reference, field: err.error.field });
      } else if (err instanceof TypeError) {
        setSimulateError({ variant: 'offline', message: 'You appear to be offline. Check your connection.' });
      } else {
        setSimulateError({ variant: 'offline', message: 'Network error' });
      }
    } finally {
      setSimulateLoading(false);
    }
  }, [selectedLocationId, periodStart, periodEnd, quantile, riskWeight]);

  useEffect(() => {
    void fetchLocations();
    void fetchPolicies();
  }, [fetchLocations, fetchPolicies]);

  useEffect(() => {
    if (selectedLocationId && policies.length > 0) {
      void simulate();
    }
  }, [selectedLocationId, policies.length, simulate]);

  const handleSave = async () => {
    // Each policy has its own parameter, so the button saves whichever one the
    // operator is looking at: the safety level, or the risk-aware cost ratio.
    const isRisk = activeId === 'risk';
    const target = policies.find((p) => p.kind === (isRisk ? 'risk_aware' : 'uncertainty_aware'));
    if (!target) {
      toast(isRisk ? 'No risk-aware policy found' : 'No uncertainty-aware policy found', 'error');
      return;
    }
    setSaving(true);
    try {
      await api.patch<{ change_request_id: string; status: string; policy_id: string }>(`/api/v1/policies/${target.id}`, isRisk ? {
        risk_weight: riskWeight,
      } : {
        quantile,
      });
      setSaved(true);
      // The change-request id is opaque to a policy author; the screen's job is
      // to confirm the change was queued, and Approvals names it in full.
      toast('Change request submitted, awaiting a decision', 'success');
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.status === 401) {
          toast('Sign in required', 'error');
        } else if (err.status === 422) {
          const fieldMsg = err.error.field ? `${err.error.field}: ` : '';
          toast(`${fieldMsg}${err.error.message}`, 'error');
        } else if (err.status === 403) {
          toast('Admin role required', 'error');
        } else {
          toast(err.error.message || 'Failed to save policy', 'error');
        }
      } else if (err instanceof TypeError) {
        toast('You appear to be offline. Check your connection.', 'error');
      } else {
        toast('Failed to save policy', 'error');
      }
    } finally {
      setSaving(false);
    }
  };

  // Prepare display rows: map policies to simulated results
  const policyDisplay = useMemo(() => {
    const kindToName: Record<string, string> = {
      historical_average: 'Historical average',
      point_forecast: 'Point forecast',
      uncertainty_aware: 'Uncertainty-aware',
      risk_aware: 'Risk-aware (cost balance)',
    };
    // Plain sentence first; the formula lives in the card's tooltip so the
    // number is explicable without being the only thing on screen.
    const kindToRule: Record<string, string> = {
      historical_average: 'Prepare the average of the same weekday in recent weeks',
      point_forecast: 'Prepare exactly what the model predicts',
      uncertainty_aware: `Prepare above the forecast, at the ${Math.round(quantile * 100)}% safety level`,
      risk_aware: 'Prepare where one missed customer costs as much as the leftover portions it avoids (§3.17)',
    };
    const kindToId: Record<string, 'historical' | 'point' | 'uncertainty' | 'risk'> = {
      historical_average: 'historical',
      point_forecast: 'point',
      uncertainty_aware: 'uncertainty',
      risk_aware: 'risk',
    };
    // Use simulate results if available, otherwise show placeholder
    if (results.length > 0) {
      return results.map((r) => ({
        id: kindToId[r.policy] ?? 'uncertainty',
        name: kindToName[r.policy] ?? r.policy,
        rule: kindToRule[r.policy] ?? r.policy,
        description: r.policy === 'uncertainty_aware'
          ? 'Safest option: covers busy days better, leaves more leftovers.'
          : r.policy === 'point_forecast'
            ? 'Prepares exactly to the predicted demand, with no cushion.'
            : r.policy === 'risk_aware'
              ? 'Weighs a missed customer against a leftover portion and prepares for the balance point.'
              : 'The rule most kitchens already use.',
        surplus: r.surplus_ps ?? null,
        surplusSr: r.surplus_sr ?? null,
        unmet: r.unmet_ud ?? null,
        prepared: r.prepared_qty ?? r.prepared_total ?? null,
        unavailableReason: r.unavailable_reason ?? null,
        surplusDays: r.surplus_days,
        unmetDays: r.unmet_days,
        preparedDays: r.prepared_days ?? null,
        realisedDays: r.realised_days ?? null,
        isActive: r.policy === activePolicyKind,
      }));
    }
    // No results yet — show the structure with nothing measured.
    const blank = {
      surplus: null,
      surplusSr: null,
      unmet: null,
      prepared: null,
      unavailableReason: null,
      surplusDays: 0,
      unmetDays: 0,
      preparedDays: null,
      realisedDays: null,
    };
    return [
      { id: 'historical' as const, name: 'Historical average', rule: kindToRule.historical_average, description: 'The rule most kitchens already use.', ...blank, isActive: activeId === 'historical' },
      { id: 'point' as const, name: 'Point forecast', rule: kindToRule.point_forecast, description: 'Prepares exactly to the predicted demand, with no cushion.', ...blank, isActive: activeId === 'point' },
      { id: 'uncertainty' as const, name: 'Uncertainty-aware', rule: kindToRule.uncertainty_aware, description: 'Safest option: covers busy days better, leaves more leftovers.', ...blank, isActive: activeId === 'uncertainty' },
      { id: 'risk' as const, name: 'Risk-aware (cost balance)', rule: kindToRule.risk_aware, description: 'Weighs a missed customer against a leftover portion and prepares for the balance point.', ...blank, isActive: activeId === 'risk' },
    ];
  }, [results, activePolicyKind, activeId, quantile]);

  type PolicyRow = (typeof policyDisplay)[number];

  const maxExposure = Math.max(
    ...policyDisplay.map((p) => Math.max(p.surplus ?? 0, p.unmet ?? 0)),
    1,
  );

  const currentResult = results.find((r) => r.policy === activePolicyKind);

  return (
    <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        {/* Location/period header */}
        <Panel
          title="Preparation policies"
          description={`Each policy is scored against days the model did not train on, in customers. Surplus = food prepared but not ordered; missed = demand you could not serve. Period ${periodStart} → ${periodEnd}.`}>
          
          {locationsError && (
            <div className="mb-3">
              <EmptyState
                variant={locationsError.variant}
                title={locationsError.status === 401 ? 'Sign in required' : locationsError.variant === 'offline' ? 'You are offline' : 'Failed to load locations'}
                description={locationsError.message + (locationsError.reference ? ` (ref: ${locationsError.reference})` : '')}
                action={{ label: 'Retry', onClick: () => void fetchLocations() }}
                size="sm"
              />
            </div>
          )}

          {policiesLoading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner label="Loading policies" className="h-8 w-8" />
            </div>
          ) : policiesError ? (
            <EmptyState
              variant={policiesError.variant}
              title={policiesError.status === 401 ? 'Sign in required' : policiesError.variant === 'offline' ? 'You are offline' : 'Failed to load policies'}
              description={policiesError.message + (policiesError.reference ? ` (ref: ${policiesError.reference})` : '') + (policiesError.field ? ` field: ${policiesError.field}` : '')}
              action={{ label: 'Retry', onClick: () => void fetchPolicies() }}
              size="sm"
            />
          ) : (
            <>
              <div className="mb-3 flex flex-wrap gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-fine font-medium text-slate-600">
                  <MapPinIcon className="h-3 w-3" />
                  {selectedLocationName}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-fine font-medium text-slate-600">
                  <CalendarIcon className="h-3 w-3" />
                  {periodStart} → {periodEnd}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-fine font-medium text-brand-700" title={`Preparation quantile ${quantile.toFixed(2)}`}>
                  Safety level {Math.round(quantile * 100)}% · higher means fewer missed customers, more leftovers
                </span>
              </div>

              {/* Location selector */}
              <div className="mb-3">
                {/* The label sat above the select without being associated with
                    it, so the control reached a screen reader unnamed. */}
                <label htmlFor="policy-location" className="mb-1 block text-fine font-semibold text-slate-600">Location</label>
                <select
                  id="policy-location"
                  value={selectedLocationId ?? ''}
                  onChange={(e) => {
                    const loc = locations.find((l) => l.id === e.target.value);
                    if (loc) {
                      setSelectedLocationId(loc.id);
                      setSelectedLocationName(loc.name);
                      setHasSimulated(false);
                    }
                  }}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-meta text-slate-900 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25">
                  
                  {locations.length === 0 && <option value="">No locations</option>}
                  {locations.map((loc) => (
                    <option key={loc.id} value={loc.id}>{loc.name}</option>
                  ))}
                </select>
              </div>

              <ul className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {policyDisplay.map((preset) => {
                  const isActive = preset.id === activeId;
                  return (
                    <li key={preset.id}>
                      <button
                        type="button"
                        aria-pressed={isActive}
                        onClick={() => setActiveId(preset.id)}
                        className={[
                        'flex h-full w-full flex-col rounded-xl border px-3 py-2.5 text-left transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                        isActive ?
                        'border-brand-500 bg-brand-50/60' :
                        'border-slate-200 hover:bg-slate-50'].
                        join(' ')}>
                        
                        <span className="flex items-start justify-between gap-2">
                          <span className="text-body-xs font-semibold text-slate-900">{preset.name}</span>
                          {isActive && <StatusPill tone="success">Active</StatusPill>}
                        </span>
                        <span className="mt-1 text-fine leading-snug text-brand-700">{preset.rule}</span>
                        <span className="mt-auto pt-1.5 text-micro leading-relaxed text-slate-500">{preset.description}</span>
                      </button>
                    </li>);

                })}
              </ul>

              {!hasSimulated && !simulateLoading && results.length === 0 ? (
                <div className="mt-3">
                  <EmptyState
                    variant="empty"
                    size="sm"
                    title="No simulations yet"
                    description="Run a simulation to compare preparation policies."
                    hints={['Select a location and period', 'Choose quantile for uncertainty-aware policy', 'Compare surplus vs unmet trade-offs']}
                    action={{ label: 'Run simulation', onClick: () => void simulate() }}
                  />
                </div>
              ) : simulateLoading ? (
                <div className="mt-4 flex items-center justify-center py-8">
                  <Spinner label="Simulating" className="h-8 w-8" />
                </div>
              ) : simulateError ? (
                <div className="mt-3">
                  <EmptyState
                    variant={simulateError.variant}
                    title={simulateError.status === 401 ? 'Sign in required' : simulateError.variant === 'offline' ? 'You are offline' : 'Simulation failed'}
                    description={simulateError.message + (simulateError.reference ? ` (ref: ${simulateError.reference})` : '') + (simulateError.field ? ` field: ${simulateError.field}` : '')}
                    action={{ label: 'Retry', onClick: () => void simulate() }}
                    size="sm"
                  />
                </div>
              ) : (
                <div className="mt-3">
                  {/* ResponsiveTable: a phone gets one labelled card per policy
                      instead of a wide table to drag sideways. The minimum is
                      sized to the narrow `xl` grid column (~568px) so the table
                      does not scroll on a laptop. */}
                  <ResponsiveTable<PolicyRow>
                    caption="Preparation policies compared"
                    minWidth="min-w-[440px]"
                    rows={policyDisplay}
                    rowKey={(preset) => preset.id}
                    rowClassName={(preset) => (preset.id === activeId ? 'bg-brand-50/40' : '')}
                    columns={[
                      {
                        key: 'policy',
                        label: 'Policy',
                        primary: true,
                        render: (preset) => (
                          <span className="text-meta font-medium text-slate-900">
                            {preset.name}
                            {preset.unavailableReason && (
                              <span className="ml-1.5 text-fine font-normal text-slate-500">
                                (not enough history)
                              </span>
                            )}
                          </span>
                        ),
                      },
                      {
                        key: 'surplus',
                        label: 'Left over',
                        render: (preset) => (
                          <div className="flex items-center gap-2">
                            <span className="w-12 shrink-0 text-meta tabular-nums text-slate-900" title="Surplus vs realised demand (PS)">{fmt1(preset.surplus)}</span>
                            <span className="h-1.5 w-full max-w-[110px] overflow-hidden rounded-full bg-slate-200">
                              <span className="block h-full rounded-full bg-amber-400" style={{ width: `${(preset.surplus ?? 0) / maxExposure * 100}%` }} />
                            </span>
                          </div>
                        ),
                      },
                      {
                        key: 'surplusSr',
                        label: 'Left-over %',
                        render: (preset) => (
                          <div className="flex items-center gap-2">
                            <span className="w-12 shrink-0 text-meta tabular-nums text-slate-900" title="Surplus relative to the forecast (SR)">{fmt1(preset.surplusSr)}</span>
                            <span className="h-1.5 w-full max-w-[110px] overflow-hidden rounded-full bg-slate-200">
                              <span className="block h-full rounded-full bg-slate-400" style={{ width: `${(preset.surplusSr ?? 0) / maxExposure * 100}%` }} />
                            </span>
                          </div>
                        ),
                      },
                      {
                        key: 'unmet',
                        label: 'Missed demand',
                        render: (preset) => (
                          <div className="flex items-center gap-2">
                            <span className="w-12 shrink-0 text-meta tabular-nums text-slate-900" title="Unmet demand (UD)">{fmt1(preset.unmet)}</span>
                            <span className="h-1.5 w-full max-w-[110px] overflow-hidden rounded-full bg-slate-200">
                              <span className="block h-full rounded-full bg-brand-600" style={{ width: `${(preset.unmet ?? 0) / maxExposure * 100}%` }} />
                            </span>
                          </div>
                        ),
                      },
                      {
                        key: 'surplusDays',
                        label: 'Days with leftovers',
                        render: (preset) => <span className="text-meta tabular-nums text-slate-700">{preset.surplusDays}</span>,
                      },
                      {
                        key: 'unmetDays',
                        label: 'Days short',
                        render: (preset) => <span className="text-meta tabular-nums text-slate-700">{preset.unmetDays}</span>,
                      },
                    ]}
                  />
                  <p className="mt-2 text-fine leading-relaxed text-slate-500">Measured in customers, never kilograms. "Left over" is food prepared but not ordered (PS); "Left-over %" is the same against the forecast (SR); "Missed demand" is customers you could not serve (UD). A higher safety level means fewer missed customers and more leftovers.</p>
                </div>
              )}
            </>
          )}
        </Panel>

        {hasSimulated && results.length > 0 && (
          <Panel title="What this trade-off means" description="Why the safety level matters.">
            {currentResult && currentResult.prepared_qty === null ? (
              <>
                <p className="text-caption leading-relaxed text-slate-600">
                  This rule cannot be worked out for the selected period: it averages demand recorded
                  <em> before</em> the day it is preparing for, and none is stored for this location
                  yet. The other rules above work from the forecast alone, so they still compare.
                  Import demand history to bring this one in. Nothing is guessed in the meantime.
                </p>
                {/* The screen knows exactly what is missing and where it is fixed,
                    so it hands the reader the fix rather than a description of it. */}
                <button
                  type="button"
                  onClick={() => navigate('ingestion')}
                  className="mt-2.5 inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2 text-meta font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
                >
                  Import demand history
                  <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} />
                </button>
              </>
            ) : (
              <p className="text-caption leading-relaxed text-slate-600">
                At a {Math.round(quantile * 100)}% safety level, the safer policy would prepare{' '}
                {fmt1(currentResult?.prepared_qty ?? currentResult?.prepared_total)} customers' worth.{' '}
                {currentResult?.surplus_ps != null && currentResult?.unmet_ud != null ? (
                  <>
                    Compared with preparing exactly to the forecast, it trades about
                    <span className="font-semibold text-amber-700">
                      {' '}+{currentResult.surplus_ps.toFixed(1)} extra portions prepared
                    </span>{' '}
                    for
                    <span className="font-semibold text-brand-700">
                      {' '}−{currentResult.unmet_ud.toFixed(1)} customers served
                    </span>
                    . Raising the safety level always means fewer missed customers and more
                    leftovers.
                  </>
                ) : (
                  <>
                    No day in this period has an outcome recorded yet, so the realised trade-off (leftovers against missed customers) appears once they do.
                  </>
                )}
              </p>
            )}
            {/* A period figure is an average over the days the rule could
                actually be applied, so the count behind it belongs on screen:
                "12.3 a day" over 3 of 29 days is not the same claim as over 29. */}
            {currentResult?.prepared_days != null && (
              <p className="mt-2 text-fine leading-relaxed text-slate-500">
                {`The period ${periodStart} → ${periodEnd} is ${periodDays} days long. This rule could be worked out for ${currentResult.prepared_days} of them`}
                {currentResult.realised_days != null
                  ? `, and ${currentResult.realised_days} have an outcome recorded. Leftovers and missed customers are measured on those days only.`
                  : '.'}
              </p>
            )}
          </Panel>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <Panel
          title="Safety level"
          icon={SlidersHorizontalIcon}
          description="Only applies to the uncertainty-aware policy. Calibrated on data the model did not train on.">
          
          <label
            htmlFor="quantile"
            className="flex items-baseline justify-between gap-3 text-caption font-semibold text-slate-800">
            
            How much to prepare
            <span className="text-control tabular-nums text-brand-700">{Math.round(quantile * 100)}%</span>
          </label>
          <input
            id="quantile"
            type="range"
            min={0.5}
            max={0.95}
            step={0.05}
            value={quantile}
            disabled={activeId !== 'uncertainty' || policiesLoading}
            onChange={(event) => {
              setQuantile(Number(event.target.value));
              setSaved(false);
            }}
            onMouseUp={() => void simulate()}
            onTouchEnd={() => void simulate()}
            className="mt-2 w-full accent-brand-600 disabled:opacity-40" />
          
          <div className="mt-1 flex justify-between text-fine text-slate-500">
            <span>Less surplus</span>
            <span>Fewer shortfalls</span>
          </div>

          {/* Section 3.17: the risk-aware rule is priced rather than padded, so
              its control is the relative cost of a lost customer, not a
              coverage level. */}
          <div className="mt-4 border-t border-slate-100 pt-3">
            <label
              htmlFor="risk-weight"
              className="flex items-baseline justify-between gap-3 text-caption font-semibold text-slate-800">
              What a lost customer costs
              <span className="text-control tabular-nums text-brand-700">
                {riskWeight.toFixed(1)}× a leftover
              </span>
            </label>
            <input
              id="risk-weight"
              type="range"
              min={0.5}
              max={5}
              step={0.5}
              value={riskWeight}
              disabled={activeId !== 'risk' || policiesLoading}
              onChange={(event) => {
                setRiskWeight(Number(event.target.value));
                setSaved(false);
              }}
              onMouseUp={() => void simulate()}
              onTouchEnd={() => void simulate()}
              className="mt-2 w-full accent-brand-600 disabled:opacity-40"
            />
            <div className="mt-1 flex justify-between text-fine text-slate-500">
              <span>Leftovers matter more</span>
              <span>Missed customers matter more</span>
            </div>
            <p className="mt-2 text-micro leading-relaxed text-slate-600">
              At 1.0 the two cost the same and the rule prepares exactly to the forecast. Rising values
              prepare more; falling values prepare less. This is a property of the site, so it is worth
              agreeing with whoever carries the cost.
            </p>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            {(() => {
              // No result yet means no number. These tiles used to fall back to
              // a fixed 6.8 / 1.7, which read as measured exposure for a period
              // the simulation had never covered.
              const active = results.find((r) => r.policy === activePolicyKind);
              const fmt = (v: number | null | undefined) =>
                v === null || v === undefined ? '—' : v.toFixed(1);
              return (
                <>
                  <div className="rounded-lg border border-slate-200 px-3 py-2">
                    <p className="text-fine text-slate-500">Left over (PS)</p>
                    <p className="text-lead font-bold tabular-nums text-slate-900">{fmt(active?.surplus_ps)}</p>
                    <p className="text-fine text-slate-500">customers' worth</p>
                  </div>
                  <div className="rounded-lg border border-slate-200 px-3 py-2">
                    <p className="text-fine text-slate-500">Missed demand (UD)</p>
                    <p className="text-lead font-bold tabular-nums text-slate-900">{fmt(active?.unmet_ud)}</p>
                    <p className="text-fine text-slate-500">customers' worth</p>
                  </div>
                </>
              );
            })()}
          </div>

          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !canWrite || (activeId !== 'uncertainty' && activeId !== 'risk')}
            title={canWrite ? undefined : 'Your seat can simulate policies but not change the workspace default'}
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">
            
            <SaveIcon className="h-4 w-4" strokeWidth={2} />
            {saving ? 'Saving…' : saved ? 'Policy saved, pending approval' : 'Save as workspace default'}
          </button>
          <p className="mt-1.5 text-center text-fine text-slate-500" aria-live="polite">
            {saved ? 'Change request created, pending approval (two-person rule).' : canWrite ? 'Changes create a change_request; admin must approve.' : ''}
          </p>
          {!canWrite && (
            <PermissionNote className="mt-2">
              Your seat simulates policies but does not set the workspace default. That decision belongs to an administrator. The numbers above are the same ones they will see.
            </PermissionNote>
          )}

          <button
            type="button"
            onClick={() => void simulate()}
            disabled={simulateLoading || !selectedLocationId}
            className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-body-xs font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50">
            
            {simulateLoading ? 'Simulating…' : 'Re-run simulation'}
          </button>
        </Panel>

        <Panel title="Assumptions" description="Stated explicitly with every simulation.">
          <ul className="flex flex-col gap-1.5 text-micro leading-relaxed text-slate-600">
            {[
            'Preparation is measured in customers, not kilograms.',
            'Surplus is an estimate of what you would have prepared too much. It is not measured food waste.',
            'The safety level is chosen on data the model did not train on, never on the days it is scored against.',
            'Both leftover measures are shown: against what was ordered (PS) and against the forecast (SR).',
            'A higher safety level always means fewer missed customers and more leftovers.'].
            map((item) =>
            <li key={item} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
                {item}
              </li>
            )}
          </ul>
        </Panel>
      </div>
    </div>);

}
