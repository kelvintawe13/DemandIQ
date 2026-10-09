import { useCallback, useEffect, useState } from 'react';
import { ShieldCheckIcon, CircleCheckIcon, DatabaseIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load datasets',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function DatasetsAdmin(){
  const [datasets,setDatasets]=useState<any[]>([]);
  const [waste,setWaste]=useState<any>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [manifest,setManifest]=useState<any>(null);
  const [selectedId,setSelectedId]=useState<string|null>(null);

  // `GET /api/v1/waste/summary` nests its figures under ``descriptive_stats``;
  // the panel used to read ``waste.observations``/``waste.mean_waste``, which the
  // API never sends — so every line silently fell back to a literal baked into
  // this file and presented fixture numbers as the live summary.
  const wasteStats = waste?.descriptive_stats ?? null;
  const wasteRows: [string, string][] = wasteStats
    ? (
        [
          ['Records', wasteStats.total_records != null ? String(wasteStats.total_records) : ''],
          ['Weights measured', wasteStats.non_missing_waste != null ? String(wasteStats.non_missing_waste) : ''],
          ['Establishments', wasteStats.establishments != null ? `${wasteStats.establishments}${wasteStats.countries ? ` across ${wasteStats.countries} countries` : ''}` : ''],
          ['Mean waste', wasteStats.waste_kg?.mean != null ? `${wasteStats.waste_kg.mean} kg per record` : ''],
          ['Median waste', wasteStats.waste_kg?.median != null ? `${wasteStats.waste_kg.median} kg` : ''],
          ['Order data available', wasteStats.ordered_portions_available != null ? `${wasteStats.ordered_portions_available} records` : ''],
          ['Period', wasteStats.date_range?.start ? `${wasteStats.date_range.start} → ${wasteStats.date_range.end ?? ''}` : ''],
        ] as [string, string][]
      ).filter(([, value]) => value !== '')
    : [];
  // `stats_source` says whether the figures came from the live table or from the
  // audited fixture, which is worth stating on a research-data panel.
  const wasteSource = waste?.stats_source === 'waste_observations' ? 'waste_observations (live)' : 'audited URBAN-WASTE fixture';
  // `establishment_variation` is the per-kitchen spread behind that mean: how
  // many records each site contributed and how far apart its own readings are.
  // It is the field that says whether a single "37.8 kg per record" can be read
  // as typical — one kitchen averaging 125 kg beside one averaging 2.9 kg is a
  // different claim from a workspace where every site sits near the middle — so
  // the panel shows it rather than leaving it to the API docs. The API bounds
  // the list it returns, so the count on screen is not the count in the dataset.
  const wasteVariation: any[] = Array.isArray(wasteStats?.establishment_variation)
    ? [...wasteStats.establishment_variation].sort((a:any, b:any)=> (b?.count ?? 0) - (a?.count ?? 0))
    : [];
  const variationShown = wasteVariation.slice(0, 8);
  const datasetEstablishments: number | null = typeof wasteStats?.establishments === 'number' ? wasteStats.establishments : null;

  const fetch = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const [dRes] = await Promise.all([
        api.get<{data:any[]}>('/api/v1/datasets'),
      ]);
      setDatasets(dRes.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
    // waste separate to not block datasets
    try{
      const w = await api.get<any>('/api/v1/waste/summary');
      setWaste(w);
    }catch{}
  },[]);

  useEffect(()=>{ void fetch(); },[fetch]);

  const loadManifest = async (id:string)=>{
    setSelectedId(id);
    try{
      const m = await api.get<any>(`/api/v1/datasets/${id}/manifest`);
      setManifest(m);
    }catch(e){ if(e instanceof ApiException) setManifest({error:e.error.message}); }
  };

  if(loading) return <LoadingState label="Loading datasets" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetch()}} size="md" />;

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <Panel title="Registered datasets" icon={DatabaseIcon} description="Provenance, access conditions and audited shape for every source in the framework.">
        {datasets.length===0 ? (
          <EmptyState variant="empty" size="md" title="No datasets registered" description="Datasets will appear once ingestion registers them." hints={["Ingest demand first","Datasets are audited on registration"]} />
        ) : (
          <ul className="flex flex-col gap-2">{datasets.map((dataset)=>
          <li key={dataset.id} className="rounded-xl border border-slate-200 px-3.5 py-3">
              <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                {/* The sub-line named ``role``/``description``, neither of which
                    the datasets API sends, so it rendered as an empty line. It
                    now names the table the source is stored in. */}
                <div className="min-w-0"><p className="text-body-sm font-semibold text-slate-900">{dataset.name ?? dataset.title}</p><p className="mt-0.5 text-micro text-slate-500">{dataset.table ? `Stored in ${dataset.table}` : dataset.source_dataset ?? ''}</p></div>
                <StatusPill tone={dataset.status==='validated' || dataset.status==='active' ? 'success':'warning'} dot>{dataset.status ?? 'unknown'}</StatusPill>
              </div>
              <dl className="mt-2.5 grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">{[
                ['Records', dataset.records ?? dataset.record_count ?? dataset.count ?? '—'],
                ['Coverage', dataset.coverage ?? '—'],
                ['Period', dataset.period ?? (dataset.start_date? `${dataset.start_date} → ${dataset.end_date}`:'—')],
                ['Access', dataset.licence ?? dataset.license ?? dataset.access ?? '—']
              ].map(([label,value])=> <div key={label} className="min-w-0"><dt className="text-fine text-slate-500">{label}</dt><dd className="truncate text-caption font-medium text-slate-700">{String(value)}</dd></div>)}</dl>
              <p className="mt-2 rounded-lg bg-slate-50 px-2.5 py-2 text-micro text-slate-600">{dataset.notes ?? dataset.description ?? ''}</p>
              {/* The manifest opens in place, so the control has to say so:
                  without `aria-expanded` a screen reader gave no feedback that
                  anything had happened. */}
              <button type="button" onClick={()=>void loadManifest(dataset.id)}
                aria-expanded={selectedId===dataset.id && Boolean(manifest)}
                aria-controls={`manifest-${dataset.id}`}
                aria-label={`${selectedId===dataset.id && manifest ? 'Hide' : 'View'} manifest for ${dataset.name ?? dataset.title}`}
                className="tap-expand mt-2 rounded text-micro font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                {selectedId===dataset.id && manifest ? 'Hide manifest' : 'View manifest'} →
              </button>
              {selectedId===dataset.id && manifest && (
                <pre id={`manifest-${dataset.id}`} tabIndex={0} className="mt-2 max-h-[200px] overflow-auto rounded bg-slate-900 p-2 text-fine text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">{JSON.stringify(manifest,null,2)}</pre>
              )}
            </li>
          )}</ul>
        )}
        {/* The boundary is stated by the API as well; when it is present the panel
            repeats that wording rather than a second, drifting paraphrase. */}
        <p className="mt-2.5 rounded-lg border border-dashed border-slate-300 px-3 py-2.5 text-micro leading-relaxed text-slate-600"><span className="font-semibold text-slate-800">Evidence boundary:</span> {waste?.evidence_boundary ? `${waste.evidence_boundary}. Simulated surplus is never presented as measured waste.` : 'waste observations are characterised independently and are never joined to demand records at row level, so simulated surplus is never presented as measured waste.'}</p>
        <p className="mt-1 text-fine text-slate-500">{datasets.length} datasets in the registry</p>
      </Panel>

      <div className="flex flex-col gap-3">
        <Panel title="Leakage controls" icon={ShieldCheckIcon} description="Enforced on every training run.">
          <ul className="flex flex-col gap-2">{[
            {label:'Chronological split',detail:'Train → validation → held-out test, never shuffled.'},
            {label:'Historical lags only',detail:'Every lag and rolling window closes before the forecast date.'},
            {label:'Reservation cut-off',detail:'Reservations restricted to what was known at prediction time.'},
            {label:'No test-period tuning',detail:'Test window untouched during selection and calibration.'},
            {label:'No cross-dataset merge',detail:'URBAN-WASTE never joined to Recruit at row level.'},
          ].map(check=> <li key={check.label} className="flex items-start gap-2.5"><CircleCheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" strokeWidth={1.9} /><span className="min-w-0"><span className="block text-meta font-semibold text-slate-900">{check.label}</span><span className="block text-micro leading-relaxed text-slate-500">{check.detail}</span></span></li>)}
          </ul>
        </Panel>

        <Panel title="Observed waste context" description="URBAN-WASTE descriptive summary.">
          {wasteStats ? (
            <>
              <dl className="flex flex-col gap-2">{wasteRows.map(([label,value])=> <div key={label} className="flex items-baseline justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0"><dt className="text-caption text-slate-500">{label}</dt><dd className="text-meta font-semibold text-slate-900">{value}</dd></div>)}</dl>
              {variationShown.length > 0 && (
                <div className="mt-3 border-t border-slate-100 pt-3">
                  <p className="text-caption font-semibold text-slate-800">Spread by establishment</p>
                  <p className="mt-0.5 text-micro leading-relaxed text-slate-500">
                    Most-observed kitchens first. A wide range on few records is not a reliable average.
                  </p>
                  <ul className="mt-2 flex flex-col">
                    {variationShown.map((est:any, index:number)=> (
                      <li key={`${est?.establishment_code ?? 'unnamed'}-${index}`} className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-slate-100 py-1.5 last:border-0">
                        <span className="min-w-0 truncate text-caption font-medium text-slate-800">{est?.establishment_code ?? 'Unnamed establishment'}</span>
                        <span className="shrink-0 text-micro tabular-nums text-slate-600">
                          {est?.mean != null ? `${est.mean} kg avg` : '—'}
                          {est?.std != null ? ` ± ${est.std}` : ''}
                          {est?.min != null && est?.max != null ? ` · ${est.min}–${est.max} kg` : ''}
                          {est?.count != null ? ` · ${est.count} records` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-fine text-slate-500">
                    {wasteVariation.length > variationShown.length
                      ? `Showing ${variationShown.length} of ${wasteVariation.length} establishments the API returned`
                      : `${wasteVariation.length} establishment${wasteVariation.length === 1 ? '' : 's'} reported`}
                    {datasetEstablishments != null && datasetEstablishments > wasteVariation.length
                      ? ` · ${datasetEstablishments} in the dataset`
                      : ''}
                  </p>
                </div>
              )}
              <p className="mt-2 text-fine text-slate-500">Source {wasteSource}{waste?.generated_at ? ` · generated ${new Date(waste.generated_at).toLocaleString()}` : ''} · not joined to your demand data</p>
            </>
          ) : (
            <EmptyState variant="empty" size="sm" title="Waste summary unavailable" description="The API did not return the URBAN-WASTE summary, so no waste figures are shown." action={{label:'Retry',onClick:()=>void fetch()}} />
          )}
        </Panel>
      </div>
    </div>
  );
}
