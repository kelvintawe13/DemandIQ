import { useCallback, useEffect, useMemo, useState } from 'react';
import { LayersIcon, SearchIcon, AlertTriangleIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';

const displayGroups = ['All','Historical demand','Calendar','Location','Reservations'] as const;
const groupMap:Record<string,string> = { historical:'Historical demand', calendar:'Calendar', location:'Location', reservation:'Reservations' };

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const, title:'Sign in required', desc:e.error.message};
    if(e.status===403) return {variant:'error' as const, title:'Admin access required', desc:e.error.message};
    return {variant:'error' as const, title:'Failed to load features', desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const, title:'You are offline', desc:'Check your connection'};
  return {variant:'offline' as const, title:'Network error', desc:String(e)};
}

export function FeatureStore(){
  const [features,setFeatures]=useState<any[]>([]);
  const [group,setGroup]=useState<(typeof displayGroups)[number]>('All');
  const [query,setQuery]=useState('');
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  // `GET /features/{name}/coverage` is the only place per-location detail
  // exists: how many records each site actually contributed for this feature.
  // The list's percentage is one figure across locations, so it cannot say
  // where a feature is missing — which is exactly what decides whether it can
  // be required or has to stay optional.
  const [coverageFor,setCoverageFor]=useState<string|null>(null);
  const [coverage,setCoverage]=useState<any|null>(null);
  const [coverageLoading,setCoverageLoading]=useState(false);
  const [coverageError,setCoverageError]=useState<string|null>(null);
  const fetch = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const res = await api.get<{data:any[]}>('/api/v1/features');
      setFeatures(res.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[]);
  useEffect(()=>{ void fetch(); },[fetch]);

  const loadCoverage = useCallback(async(name:string)=>{
    setCoverageFor(name); setCoverageLoading(true); setCoverageError(null);
    try{
      const res = await api.get<any>(`/api/v1/features/${encodeURIComponent(name)}/coverage`);
      setCoverage(res);
    }catch(e){
      setCoverage(null);
      setCoverageError(e instanceof ApiException ? e.error.message : 'Could not load the per-location coverage');
    }finally{ setCoverageLoading(false); }
  },[]);

  const visible = useMemo(()=>{
    const needle=query.trim().toLowerCase();
    return features.filter(f=>{
      const g = groupMap[f.feature_group] ?? f.feature_group ?? f.group ?? '';
      const matchesGroup = group==='All' || g===group || f.feature_group===group.toLowerCase() || f.group===group;
      const matchesQuery = !needle || f.name.includes(needle) || (f.description ?? '').toLowerCase().includes(needle);
      return matchesGroup && matchesQuery;
    });
  },[group,query,features]);

  if(loading) return <LoadingState label="Loading features" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetch()}} size="md" />;

  if(features.length===0) return <EmptyState variant="empty" size="md" title="No features yet" description="Features appear once the nightly data build has run." hints={["Wait for the nightly build","Import demand first"]} />;

  const inProduction = features.filter(f=>f.in_production).length;
  const excluded = features.filter(f=> !f.leakage_safe);
  // `GET /features` states a coverage per feature. It used to default a missing
  // one to 0, so a feature the API said nothing about was drawn and labelled
  // "0%" — a claim that it covers nothing. An absent figure is now shown as a
  // dash, and the reservation warning below is only stated when it is known.
  const coverageOf = (feature:any): number|null =>
    feature.coverage_pct ?? feature.coverage ?? null;
  const reservationCoverage: number|null = (()=>{
    const row = features.find(f=>f.name==='reservation_count');
    return row ? coverageOf(row) : null;
  })();

  return (
    <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <Panel title="Features" icon={LayersIcon} description={`${inProduction} of ${features.length} features are used by the live model.`}>
        <div className="mb-3 flex flex-col gap-2 sm:flex-row">
          <div className="flex min-w-0 flex-1 items-center rounded-lg border border-slate-300 bg-white focus-within:border-brand-500 focus-within:ring-1 focus-within:ring-brand-500">
            <SearchIcon className="ml-2.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
            <input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search features" aria-label="Search features" className="w-full min-w-0 bg-transparent px-2.5 py-2 text-body-xs text-slate-900 placeholder:text-slate-500 focus:outline-none" />
          </div>
          <div className="flex gap-1 overflow-x-auto rounded-lg border border-slate-200 p-1">
            {displayGroups.map(option=> <button key={option} type="button" aria-pressed={group===option} onClick={()=>setGroup(option)} className={['shrink-0 rounded-md px-2.5 py-1.5 text-caption transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', group===option?'bg-brand-600 font-semibold text-white':'font-medium text-slate-600 hover:bg-slate-100'].join(' ')}>{option}</button>)}
          </div>
        </div>

        {visible.length===0 ? <EmptyState variant="search" size="sm" title="No matches" description="No features match that search." hints={["Try different keywords","Clear group filter"]} /> : (
          <ul className="flex flex-col gap-1.5">
            {visible.map((feature)=>
            <li key={feature.id ?? feature.name} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-slate-200 px-3 py-2.5">
                <div className="min-w-[180px] flex-1">
                  <p className="flex items-center gap-2"><code className="font-mono text-meta font-semibold text-slate-900">{feature.name}</code><span className="text-fine text-slate-500">{feature.dtype}</span></p>
                  <p className="truncate text-micro text-slate-500">{feature.description}</p>
                </div>
                <div className="w-[104px] shrink-0">
                  <p className="text-fine text-slate-500">Coverage</p>
                  {coverageOf(feature) != null ? (
                    <div className="mt-1 flex items-center gap-1.5">
                      <span className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200"><span className={['block h-full rounded-full', (coverageOf(feature) as number) >=80 ? 'bg-brand-600':'bg-amber-400'].join(' ')} style={{width:`${coverageOf(feature)}%`}} /></span>
                      <span className="shrink-0 text-fine tabular-nums text-slate-500">{coverageOf(feature)}%</span>
                    </div>
                  ) : (
                    <p className="mt-1 text-caption text-slate-500">—</p>
                  )}
                </div>
                <StatusPill tone={!feature.leakage_safe ? 'critical' : feature.in_production ? 'success':'neutral'} dot>{!feature.leakage_safe ? 'excluded' : feature.in_production ? 'in production' : 'candidate'}</StatusPill>
                {/* One figure per feature is an average over locations; this opens
                    the rows behind it. */}
                <button
                  type="button"
                  onClick={()=>void loadCoverage(feature.name)}
                  aria-pressed={coverageFor===feature.name}
                  aria-label={`Per-location coverage for ${feature.name}`}
                  className="shrink-0 rounded-lg border border-slate-300 px-2 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 aria-pressed:border-brand-500 aria-pressed:bg-brand-50 aria-pressed:text-brand-800">
                  Where it is missing
                </button>
              </li>
            )}
          </ul>
        )}
        <p className="mt-2 text-fine text-slate-500">{features.length} features · built from your imported demand</p>
      </Panel>

      <div className="flex flex-col gap-3">
        <Panel title="Per-location coverage" icon={SearchIcon} description="How many records each location contributed for one feature.">
          {!coverageFor ? (
            <p className="text-caption leading-relaxed text-slate-600">
              Pick a feature to see its coverage location by location. A feature that exists everywhere can be
              required; one missing in most places has to stay optional, and a single workspace-wide percentage
              cannot tell the two apart.
            </p>
          ) : coverageLoading ? (
            <LoadingState label="Loading coverage" size="sm" />
          ) : coverageError ? (
            <EmptyState variant="error" size="sm" title="Could not load coverage" description={coverageError} action={{label:'Retry',onClick:()=>void loadCoverage(coverageFor)}} />
          ) : coverage ? (()=>{
            // `per_location` is the payload's own list; `overall_coverage_pct`
            // is the API's figure for it, not a number re-derived here.
            const rows: any[] = Array.isArray(coverage.per_location) ? coverage.per_location : [];
            const overall = coverage.overall_coverage_pct;
            return (
              <>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <code className="font-mono text-meta font-semibold text-slate-900">{coverage.feature ?? coverageFor}</code>
                  <span className="text-micro text-slate-500">
                    {overall != null ? `${overall}% across ${rows.length} location${rows.length===1?'':'s'}` : `${rows.length} location${rows.length===1?'':'s'}`}
                  </span>
                </div>
                {rows.length===0 ? (
                  <EmptyState variant="empty" size="sm" title="No locations to cover" description="This workspace has no locations, so there is nothing to measure coverage against." />
                ) : (
                  <ul className="mt-2 flex flex-col gap-1.5">
                    {rows.map((row:any)=> (
                      <li key={row.location_id ?? row.location_name} className="rounded-lg border border-slate-200 px-2.5 py-2">
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="min-w-0 truncate text-meta font-medium text-slate-800">{row.location_name ?? row.location_id}</span>
                          <span className="shrink-0 text-micro tabular-nums text-slate-500">
                            {row.coverage_pct != null ? `${row.coverage_pct}%` : '—'}
                            {/* `available_records` of `total_records` is what the
                                percentage is made of. */}
                            {row.available_records != null && row.total_records != null ? ` · ${row.available_records} of ${row.total_records} records` : ''}
                          </span>
                        </div>
                        {row.coverage_pct != null && (
                          <span className="mt-1 flex h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                            <span className={['block h-full rounded-full', row.coverage_pct >= 80 ? 'bg-brand-600':'bg-amber-400'].join(' ')} style={{width:`${Math.max(0, Math.min(100, row.coverage_pct))}%`}} />
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 text-fine leading-relaxed text-slate-500">
                  A location below 80% is missing this feature for part of its history, so the model falls back for it there.
                </p>
              </>
            );
          })() : null}
        </Panel>

        <Panel title="Excluded (not known in time)" description="These are blocked because their value is not known when the forecast is made. Using them would be hindsight.">
          {excluded.length===0 ? <EmptyState variant="empty" size="sm" title="No excluded features" description="All features passed leakage checks." /> : (
            <ul className="flex flex-col gap-2">{excluded.map((feature)=>
              <li key={feature.id ?? feature.name} className="flex items-start gap-2.5">
                <AlertTriangleIcon className="mt-0.5 h-4 w-4 shrink-0 text-red-500" strokeWidth={1.9} />
                <span className="min-w-0"><code className="block font-mono text-caption font-semibold text-slate-900">{feature.name}</code><span className="block text-micro leading-relaxed text-slate-500">{feature.description}</span></span>
              </li>
            )}</ul>
          )}
        </Panel>
        <Panel title="Coverage warning" description="Features missing in many places stay optional.">
          <p className="text-caption leading-relaxed text-slate-600">{reservationCoverage != null ? `Reservation features are present for only ~${reservationCoverage}% of locations, so the live model treats them as optional rather than essential. Locations without reservations still receive a full forecast.` : 'Reservation features are optional: the live model treats them as supplementary, and locations without reservations still receive a full forecast.'}</p>
        </Panel>
      </div>
    </div>
  );
}
