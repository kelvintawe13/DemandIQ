import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIcon, CheckIcon, PlayIcon, RefreshCwIcon, RotateCcwIcon, BrainCircuitIcon, InfoIcon, PencilIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Toggle } from '../../components/ui/Toggle';
import { api, ApiException } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { RecordList } from '../../components/ui/RecordList';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { jobLabel, runStatusTone } from '../../lib/schedule';
import { modelLabel, plainStatus } from '../../lib/plainLanguage';

/** How often the live panels re-read the API while auto-refresh is on. */
const LIVE_POLL_MS = 15000;
/** Jobs that produce or recalibrate a model, the ones this console is about. */
const TRAINING_JOBS = ['full_retrain', 'recalibration'];

/**
 * What the API is serving, in one line an operator can act on.
 *
 * `GET /models/serving` distinguishes four states, and the difference between
 * them matters: a deployment running rolling means looks identical to a healthy
 * one from the outside until someone reads the numbers.
 */
function servingTone(state: any): 'success' | 'warning' | 'critical' | 'neutral' {
  if (!state) return 'neutral';
  // A configured export that stopped loading is the one state this console can
  // see and the boot check cannot: the check only runs at startup, so a process
  // that started with a good bundle and lost it keeps rendering this page while
  // the API answers from the stub. That must look wrong, not neutral.
  if (state.configuration?.mode === 'error') return 'critical';
  if (state.mode !== 'bundle') return 'warning';
  if (state.agreement === 'in_sync') return 'success';
  if (state.agreement === 'version_mismatch') return 'critical';
  return 'warning';
}

function servingLabel(state: any): string {
  if (!state) return 'Checking what is being served…';
  if (state.configuration?.mode === 'error')
    return 'Misconfigured: the export MODEL_BUNDLE_PATH names is not loadable';
  if (state.mode !== 'bundle') return 'Fallback averages (no trained export loaded)';
  if (state.agreement === 'in_sync') return 'Trained model, in sync with the registry';
  if (state.agreement === 'version_mismatch')
    return 'Loaded export differs from the production row';
  return 'Trained export loaded, but nothing is marked production';
}

/**
 * How the loaded export was chosen, in operator words.
 *
 * The same `configuration` block `GET /api/v1/health` publishes. `mode: bundle`
 * only says an export is loaded; it does not say whether an operator asked for
 * it (`MODEL_BUNDLE_PATH`) or whether this deployment merely happened to ship
 * one next to the code. That difference is what the startup assertion is about,
 * so it belongs on the screen rather than only in the boot log.
 */
function configurationLabel(state: any): string {
  const config = state?.configuration;
  if (!config) return 'Not reported';
  if (config.mode === 'error') return 'The configured export no longer loads';
  if (config.mode === 'stub') return 'Nothing configured or found — averages are answering';
  return config.configured
    ? 'Serving the export named by MODEL_BUNDLE_PATH'
    : 'Serving the export found at the default location';
}

/** The registry comparison, named rather than colour-coded only. */
function agreementLabel(agreement?: string | null): string {
  switch (agreement) {
    case 'in_sync':
      return 'Matches the production row';
    case 'version_mismatch':
      return 'Differs from the production row';
    case 'bundle_unregistered':
      return 'Nothing marked production';
    case 'stub':
      return 'Serving rolling-mean averages';
    default:
      return 'Not checked yet';
  }
}

/** Where a model's feature weights came from, in operator words. */
function importanceSourceLabel(method?: string | null): string {
  switch (method) {
    case 'gain':
      return 'Booster gain, measured from the serving export';
    case 'recorded_by_training_run':
      return 'Recorded by the training run that produced this version';
    case 'deterministic_fallback':
      return 'Placeholder weights: nothing has been measured for this version yet';
    default:
      return 'Feature weights for this version';
  }
}

/** A long artifact path in a cell: the last two segments, full value on hover. */
function shortPath(value?: string | null): string {
  if (!value) return '—';
  const parts = String(value).split('/');
  return parts.length > 2 ? `…/${parts.slice(-2).join('/')}` : String(value);
}

const groupTone: Record<string,string> = { 'Historical demand':'bg-brand-600', 'Calendar':'bg-brand-400', 'historical':'bg-brand-600', 'calendar':'bg-brand-400', 'location':'bg-sky-400', 'Restaurant':'bg-sky-400', 'Reservations':'bg-amber-400', 'reservation':'bg-amber-400' };

/** Coverage figures arrive as percentages (schema default 90.00), not fractions. */
function pct(value: unknown, digits = 0): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : '—';
}

/**
 * Section 3.16 sensitivity: the same online conformal loop re-run at other
 * coverage levels. Rendered only when the training run recorded it, so an
 * absent block means "not measured" rather than "measured as zero".
 */
function CoverageSensitivity({ sensitivity }: { sensitivity?: Record<string, { target_coverage?: number|null; empirical_coverage?: number|null; mean_interval_width?: number|null }> }) {
  const levels = Object.keys(sensitivity ?? {}).sort();
  if (levels.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-caption font-semibold text-slate-700">Other interval widths tried</p>
      <ul className="mt-1.5 flex flex-col gap-1">
        {levels.map((level) => {
          const block = sensitivity![level] ?? {};
          return (
            <li key={level} className="flex items-baseline justify-between gap-3 text-caption text-slate-600">
              <span>Asked for {pct(block.target_coverage ?? Number(level) * 100)}</span>
              <span className="tabular-nums">
                got {pct(block.empirical_coverage, 1)}
                {block.mean_interval_width != null ? ` · ${block.mean_interval_width} wide` : ''}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-1.5 text-fine text-slate-500">Re-checked without touching the test window. This shows the range behaviour comes from the method, not the 90% choice.</p>
    </div>
  );
}

/**
 * `key=value` for the scalar hyperparameters a run recorded, or `—`.
 *
 * The model row carries what the training run configured (`hyperparameters`) and
 * the artifact it wrote; the detail panel showed neither, so it described a model
 * without saying how it was set up or what it produced. Nested values (a
 * feature-importance map, for instance) are left out rather than printed as
 * `[object Object]`.
 */
function hyperparameterSummary(params: unknown): string {
  if (!params || typeof params !== 'object') return '—';
  const parts = Object.entries(params as Record<string, unknown>)
    .filter(([, value]) => ['number', 'string', 'boolean'].includes(typeof value))
    .map(([key, value]) => `${key}=${String(value)}`);
  return parts.length ? parts.join(' · ') : '—';
}

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message, hints:['Administrator role required']};
    return {variant:'error' as const,title:'Failed to load models',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check your connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

/**
 * Where a "Run training" button used to be.
 *
 * This API does not train: ``POST /models/train`` answers ``501
 * training_unavailable`` on purpose, because training happens offline in the
 * model/ package and the registry is published from the serving bundle that
 * package exports. A button that could only fail was worse than no button, so
 * the action is replaced by the explanation of how a version actually gets here.
 */
function OfflineTrainingNote({ className = '' }: { className?: string }) {
  return (
    <p className={`flex items-start gap-1.5 text-fine leading-relaxed text-slate-500 ${className}`}>
      <InfoIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} aria-hidden="true" />
      <span>
        Models are trained offline in the model pipeline. This registry publishes the versions a
        serving bundle describes, so export a bundle and its versions appear here on the next refresh.
      </span>
    </p>
  );
}

export function ModelRegistry(){
  const [models,setModels]=useState<any[]>([]);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [importance,setImportance]=useState<any[]>([]);
  const [importanceMethod,setImportanceMethod]=useState<string|null>(null);
  const [calibration,setCalibration]=useState<any>(null);
  // The live half of the console: what is being served, and the runs producing it.
  const [serving,setServing]=useState<any>(null);
  const [runs,setRuns]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [actionId,setActionId]=useState<string|null>(null);
  const [live,setLive]=useState(true);
  const [checkedAt,setCheckedAt]=useState<Date|null>(null);
  // The version dialog: what can change about a trained version is its place in
  // production (promote / roll back) — metrics are fixed at training time.
  const [editModel,setEditModel]=useState<any>(null);
  const { toast } = useToast();

  const fetchModels = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const res = await api.get<{data:any[]}>('/api/v1/models');
      const data = res.data ?? [];
      setModels(data);
      // Choosing a default row is a functional update, not a dependency: with
      // `selectedId` in this callback's deps, selecting the first row changed
      // its identity, which re-ran the mount effect and re-read every model
      // (and restarted the auto-refresh timer) the moment the first row
      // selected itself. Re-reads now leave the selection alone, and a
      // selection that vanished from the registry falls back to the first row.
      setSelectedId(prev => data.length===0 ? null : (prev && data.some((m:any)=>m.id===prev) ? prev : data[0].id));
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); setCheckedAt(new Date()); }
  },[]);

  /**
   * The two live reads beside the registry.
   *
   * Each is allowed to fail on its own: a seat that can read models but not the
   * pipeline still gets the serving state, and a serving-state failure must not
   * blank a registry that answered fine.
   */
  const fetchLive = useCallback(async()=>{
    const [servingRes, runsRes] = await Promise.all([
      api.get<any>('/api/v1/models/serving').catch(()=>null),
      api.get<{data:any[]}>('/api/v1/pipeline/runs', {limit:20}).catch(()=>null),
    ]);
    if(servingRes) setServing(servingRes);
    setRuns(runsRes?.data ?? []);
    setCheckedAt(new Date());
  },[]);

  const fetchDetail = useCallback(async(id:string)=>{
    try{
      // One read for the whole panel: the detail response carries the model row,
      // its conformal calibration and its feature weights together, so the
      // panels below cannot disagree or land out of step with each other.
      const res = await api.get<any>(`/api/v1/models/${id}`);
      setCalibration(res?.calibration ?? null);
      const impData = Array.isArray(res?.importance) ? res.importance : [];
      // backend importance returns list of {feature, importance}
      setImportance(impData.map((x:any)=>({feature:x.feature ?? x.feature_name ?? x.name ?? 'feature', group: x.group ?? 'Historical demand', importance: typeof x.importance==='number' ? Math.round(x.importance*100) : Number(x.importance)||0 })));
      setImportanceMethod(res?.importance_method ?? null);
    }catch{
      setCalibration(null); setImportance([]); setImportanceMethod(null);
    }
  },[]);

  useEffect(()=>{ void fetchModels(); void fetchLive(); },[fetchModels, fetchLive]);
  useEffect(()=>{ if(selectedId) void fetchDetail(selectedId); },[selectedId, fetchDetail]);
  // Auto-refresh: an export lands, a retrain is queued, a run finishes — none of
  // that touches this page, and all of it changes what it should say.
  useEffect(()=>{
    if(!live) return;
    const timer = setInterval(()=>{ void fetchModels(); void fetchLive(); }, LIVE_POLL_MS);
    return ()=>clearInterval(timer);
  },[live, fetchModels, fetchLive]);

  /**
   * Runs worth showing beside the registry: the ones that produce or recalibrate
   * a model, plus anything currently queued or running. A run's own `detail`
   * names the version it queued; nothing here infers a link the API does not
   * publish.
   */
  const trainingRuns = useMemo(()=>{
    const seen = new Set<string>();
    return runs
      .filter((r:any)=> TRAINING_JOBS.includes(String(r.job_type)) || r.status==='queued' || r.status==='running')
      .filter((r:any)=>{ const key = String(r.id); if(seen.has(key)) return false; seen.add(key); return true; })
      .sort((a:any,b:any)=> (a.status==='running'||a.status==='queued' ? 0 : 1) - (b.status==='running'||b.status==='queued' ? 0 : 1))
      .slice(0,6);
  },[runs]);
  const inFlightRuns = trainingRuns.filter((r:any)=> r.status==='running' || r.status==='queued');
  const queuedModels = models.filter((m:any)=> m.status==='training');

  const selected = models.find(r=>r.id===selectedId);
  const best = models.length ? models.reduce((a,b)=> (a.mae ?? 999) <= (b.mae ?? 999) ? a : b) : null;
  // Whether the version in the detail panel is the export actually being served.
  const selectedIsServed = Boolean(selected?.version) && selected?.version === serving?.bundle?.version;
  const servedVersion = serving?.bundle?.version ?? null;
  const servingRow = !serving
    ? 'Not checked yet'
    : selectedIsServed
      ? 'Yes, this is the loaded export'
      : servedVersion
        ? `No, the loaded export is ${servedVersion}`
        : 'No, no trained export is loaded';

  // Evaluation detail (MASE / Bias / best baseline + lift / windows) is served by
  // the API only when the loaded serving bundle is this exact model version; every
  // row falls back to '—' rather than showing an invented number.
  const ev = selected?.evaluation ?? null;
  const fmt = (v:any,d:number)=> (v!=null && Number.isFinite(Number(v)) ? Number(v).toFixed(d) : '—');
  // Signed MAE delta a challenger measured against the production model on the
  // same chronological window (bundle value from the training run, e.g. the
  // CatBoost fallback primary). Negative = better than the champion.
  const fmtDelta = (v:any)=>{
    if(v==null || !Number.isFinite(Number(v))) return '—';
    const n = Number(v);
    return `${n>0?'+':n<0?'−':''}${Math.abs(n).toFixed(2)}`;
  };
  const deltaTone = (v:any)=> (v==null ? 'text-slate-500' : Number(v)<=0 ? 'text-emerald-700' : 'text-rose-600');
  const evaluationRows: Array<[string,string]> = ev ? [
    ['MASE', fmt(ev.mase,3)],
    ['Bias (visitors)', fmt(ev.bias,2)],
    // Supplementary (§3.15): shown for completeness, never used to rank.
    ['R² (supplementary)', fmt(ev.r2,3)],
    ['Best baseline', ev.baseline_name ? `${ev.baseline_name} · MAE ${fmt(ev.baseline_mae,2)}` : '—'],
    ['Lift vs baseline', ev.mae_lift_vs_baseline!=null ? `${(Number(ev.mae_lift_vs_baseline)*100).toFixed(1)}% MAE` : '—'],
    ['Scored store-days', ev.test_rows!=null ? Number(ev.test_rows).toLocaleString() : '—'],
    ['Test window', selected?.test_start && selected?.test_end ? `${selected.test_start} → ${selected.test_end}` : '—'],
    // The evaluation's role is `champion`/`challenger` (app/services/forecasting/loader.py).
    ['Role', plainStatus(ev.role)],
    ['Δ MAE vs production', fmtDelta(ev.mae_delta_vs_production) + (ev.mae_delta_vs_production!=null ? (Number(ev.mae_delta_vs_production)<=0 ? ' (better)' : ' (worse)') : '')],
  ] : [];
  const detailRows: Array<[string,string]> = [
    ['MAE', fmt(selected?.mae,2)],
    ['RMSE', fmt(selected?.rmse,2)],
    ['RMSLE', fmt(selected?.rmsle,3)],
    ...evaluationRows,
    ['Hyperparameters', hyperparameterSummary(selected?.hyperparameters)],
    ['Trained on', selected?.train_start && selected?.train_end ? `${selected.train_start} → ${selected.train_end}` : '—'],
    ['Artifact', selected?.artifact_uri ?? '—'],
    ['Training runtime', selected?.runtime ?? '—'],
    ['Last trained', selected?.trained_at ? new Date(selected.trained_at).toLocaleString() : '—'],
    ['Promoted', selected?.promoted_at ? new Date(selected.promoted_at).toLocaleString() : '—'],
    // Provenance, beside the metrics it belongs to rather than on another screen.
    ['Served right now', servingRow],
  ];

  const promote = async (id:string)=>{
    setActionId(id);
    try{ await api.post(`/api/v1/models/${id}/promote`); toast('Promotion requested. Needs second approval.','info'); void fetchModels(); }
    catch(e){
      if(e instanceof ApiException){
        if(e.error.code==='two_person_rule' || e.status===422) toast(e.error.message,'error');
        else toast(e.error.message,'error');
      }
    } finally{ setActionId(null); }
  };
  /** The registry row's own name, looked up by id, for a message about it. */
  const labelForModelId = (id:string|null|undefined)=>{
    if(!id) return null;
    const row = models.find(r=>r.id===id);
    if(!row) return null;
    const name = row.version ?? row.name ?? row.algorithm;
    return name ? String(name) : null;
  };

  const rollback = async (id:string)=>{
    setActionId(id);
    try{
      // The endpoint names the version it put back in production (`model_id`)
      // and the one it replaced (`previous_production`). Both were dropped for a
      // bare "Rolled back", which does not say what changed.
      const res = await api.post<any>(`/api/v1/models/${id}/rollback`);
      const target = labelForModelId(res?.model_id ?? id) ?? 'The chosen version';
      const replaced = labelForModelId(res?.previous_production);
      toast(replaced ? `${target} is production. ${replaced} was rolled back` : `${target} is production`);
      void fetchModels();
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
    finally{ setActionId(null); }
  };

  /**
   * The live serving state, kept in one element so the empty-registry screen
   * shows it too. A workspace with no model rows is exactly when "the API is
   * answering with rolling means" is the most useful thing on the page.
   */
  const servingPanel = (
    <Panel
      title="Serving now"
      icon={ActivityIcon}
      description="What the API answers forecast requests with right now, and whether it matches the model this console calls production."
      action={<div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
        <span className="text-micro text-slate-500">{checkedAt ? `Checked ${checkedAt.toLocaleTimeString()}` : 'Checking…'}</span>
        <button type="button" onClick={()=>{ void fetchModels(); void fetchLive(); }} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><RefreshCwIcon className="h-3.5 w-3.5" strokeWidth={2} />Refresh</button>
        <span className="flex items-center gap-2">
          <span className="text-micro font-semibold text-slate-600">Auto-refresh</span>
          <Toggle checked={live} onChange={setLive} label="Auto-refresh" title="Re-read the serving state every 15 seconds" />
        </span>
      </div>}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <StatusPill tone={servingTone(serving)} dot>{servingLabel(serving)}</StatusPill>
        {serving?.bundle?.version && <span className="text-meta font-semibold text-slate-900">{serving.bundle.version} · {serving.bundle.algorithm}</span>}
        <span className="text-micro text-slate-500">feature set {serving?.bundle?.feature_set_version ?? '—'}</span>
      </div>
      {serving && <>
        <p className="mt-2.5 rounded-lg bg-slate-50 px-2.5 py-2 text-caption leading-relaxed text-slate-700">{serving.summary}</p>
        <p className="mt-2 text-caption leading-relaxed text-slate-600">{serving.note}</p>
        <p className="mt-1.5 text-micro leading-relaxed text-slate-600">
          <span className="font-semibold text-slate-800">Configuration:</span>{' '}
          {configurationLabel(serving)}
        </p>
        {serving.configuration?.mode === 'error' && (
          <p className="mt-1.5 rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-caption leading-relaxed text-red-700">{serving.configuration.reason}</p>
        )}
        <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-3">
          <div>
            <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">Export file</dt>
            <dd className="truncate text-micro text-slate-700" title={serving.bundle?.path ?? 'No export loaded'}>{shortPath(serving.bundle?.path)}</dd>
          </div>
          <div>
            <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">Export changed</dt>
            <dd className="text-caption text-slate-700">{serving.bundle?.changed_at ? new Date(serving.bundle.changed_at).toLocaleString() : '—'}</dd>
          </div>
          <div>
            <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">Exported</dt>
            <dd className="text-caption text-slate-700">{serving.bundle?.exported_at ?? '—'}</dd>
          </div>
        </dl>
        <p className="mt-2 text-micro leading-relaxed text-slate-600">
          <span className="font-semibold text-slate-800">Registry:</span> {agreementLabel(serving.agreement)}{serving.production
            ? ` · ${serving.production.version} (${serving.production.algorithm}), promoted ${serving.production.promoted_at ? new Date(serving.production.promoted_at).toLocaleDateString() : '—'}`
            : ''}
        </p>
      </>}
    </Panel>
  );

  // Only the first read blanks the screen. A refresh (the 15s auto-refresh, or
  // the button) keeps the rows up while it re-reads: unmounting the whole page
  // on every poll discarded the operator's selection and scroll position, and
  // flashed a loading state over data that was still perfectly good.
  if(loading && models.length===0) return <LoadingState label="Loading models" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} hints={error.hints} action={{label:'Retry',onClick:()=>void fetchModels()}} size="md" />;
  if(models.length===0) return (
    <div className="flex flex-col gap-3">
      {servingPanel}
      <EmptyState variant="empty" size="md" title="No models yet" description="The registry is published from the serving bundle. Models are trained offline in the model pipeline, so export a bundle and its versions appear here." hints={["Training uses chronological split","No test-period tuning"]} />
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {servingPanel}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <Panel title="Training runs in flight" icon={PlayIcon} description="Runs that produce or recalibrate a model, and any pipeline job queued or running right now." action={<span className="text-micro text-slate-500">{inFlightRuns.length} in flight</span>}>
          {trainingRuns.length===0 ? <EmptyState variant="empty" size="sm" title="Nothing in flight" description="No training or calibration run is queued or running right now." hints={["Retrains run Sundays 02:00","Calibration follows at 03:40"]} /> : (
            <ul className="flex flex-col gap-1.5">{trainingRuns.map((run:any)=> (
              <li key={run.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-slate-200 px-3 py-2.5">
                <div className="min-w-[150px] flex-1">
                  <p className="truncate text-meta font-semibold text-slate-900">{jobLabel(String(run.job_type))}</p>
                  <p className="truncate text-micro text-slate-500">{run.detail ?? 'No detail recorded'}</p>
                </div>
                <div className="hidden w-[124px] shrink-0 sm:block">
                  <p className="text-fine text-slate-500">Started</p>
                  <p className="text-caption text-slate-700">{run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</p>
                </div>
                <div className="hidden w-[68px] shrink-0 md:block">
                  <p className="text-fine text-slate-500">Rows</p>
                  <p className="text-caption tabular-nums text-slate-700">{run.rows_processed ?? '—'}</p>
                </div>
                <StatusPill tone={runStatusTone(run.status)} dot>{plainStatus(run.status)}</StatusPill>
              </li>
            ))}</ul>
          )}
          {/* Only shown when a version really is mid-training. Nothing in this console queues one
              (training is offline), so an unconditional "No model version is marked training"
              described a state no operator could reach — and read as if a run could still be started here. */}
          {queuedModels.length>0 && <p className="mt-2 text-fine text-slate-500">{`The registry has ${queuedModels.map((m:any)=> m.version ?? m.algorithm).join(', ')} still training.`}</p>}
        </Panel>

        <Panel title="Model registry" description="Chronological validation, no test-period tuning. Lower is better on all three metrics.">
          <OfflineTrainingNote className="mb-3" />
          <RecordList<any>
            stateKey="admin-model-registry"
            caption="Model registry"
            minWidth="min-w-[620px]"
            items={models}
            countNoun="versions"
            rowKey={(run:any)=>String(run.id)}
            searchFields={(run:any)=> [run.version, run.name, run.algorithm, run.status].filter(Boolean).join(' ')}
            searchPlaceholder="Search versions…"
            filters={[{ key:'status', label:'Status', match:(run:any,v:string)=> String(run.status ?? '')===v,
              options:[{value:'',label:'Any status'}, ...[...new Set(models.map((m:any)=>String(m.status ?? '')))].filter(Boolean).map((s)=>({value:s,label:plainStatus(s)}))] }]}
            sortValue={(run:any,k:string)=> ['mae','rmse','rmsle'].includes(k) ? Number(run[k] ?? Number.POSITIVE_INFINITY) : k==='trained' ? String(run.trained_at ?? '') : k==='model' ? String(run.version ?? run.name ?? '') : String(run[k] ?? '')}
            onRowClick={(run:any)=>setSelectedId(run.id)}
            rowAriaLabel={(run:any)=>`Select ${run.version ?? run.name ?? run.algorithm ?? 'model'}`}
            rowClassName={(run:any)=> run.id===selectedId ? 'bg-brand-50/60' : 'hover:bg-slate-50'}
            columns={[
              { key:'model', label:'Model', primary:true, sortable:true, render:(run:any)=>{
                  const isProduction = run.status==='production' || run.status==='live';
                  return <>
                    <span className="flex flex-wrap items-center gap-2 text-meta font-semibold text-slate-900">{run.version ?? run.name ?? run.algorithm} {isProduction && <StatusPill tone="success">Live</StatusPill>} {best && run.id===best.id && !isProduction && <StatusPill tone="info">Best MAE</StatusPill>}</span>
                    <span className="block text-fine text-slate-500">{run.algorithm ?? run.family} · {run.trained_at ? new Date(run.trained_at).toLocaleDateString() : run.runtime ?? ''}</span>
                  </>; }},
              // Was labelled "Role" while rendering the run's lifecycle status
              // (`candidate`/`production`/`archived`), which reads as a promise the
              // cell does not keep. The evaluation's champion/challenger role is
              // shown in the detail panel instead.
              { key:'status', label:'Status', sortable:true, render:(run:any)=> <span>{plainStatus(run.status)}</span> },
              { key:'mae', label:'MAE', sortable:true, render:(run:any)=> <span className="font-medium tabular-nums text-slate-900">{run.mae != null ? Number(run.mae).toFixed(2) : '—'}</span> },
              { key:'rmse', label:'RMSE', sortable:true, render:(run:any)=> <span className="tabular-nums">{run.rmse != null ? Number(run.rmse).toFixed(2) : '—'}</span> },
              { key:'rmsle', label:'RMSLE', sortable:true, render:(run:any)=> <span className="tabular-nums">{run.rmsle != null ? Number(run.rmsle).toFixed(3) : '—'}</span> },
              { key:'delta', label:'Δ MAE', render:(run:any)=> <span title="MAE difference vs the production model on the run's held-out window" className={`font-medium tabular-nums ${deltaTone(run.evaluation?.mae_delta_vs_production)}`}>{fmtDelta(run.evaluation?.mae_delta_vs_production)}</span> },
              { key:'trained', label:'Trained', sortable:true, render:(run:any)=> run.trained_at ? new Date(run.trained_at).toLocaleDateString() : run.trained ?? '' },
              { key:'action', label:'', cellClassName:'pr-0', render:(run:any)=>{
                  const isProduction = run.status==='production' || run.status==='live';
                  return <span className="inline-flex items-center justify-end gap-1">
                    {isProduction
                      ? <span className="inline-flex items-center gap-1 rounded text-micro font-semibold text-brand-700"><CheckIcon className="h-3.5 w-3.5" strokeWidth={2.5} />In production</span>
                      : <button type="button" disabled={actionId===run.id} onClick={(e)=>{e.stopPropagation(); void promote(run.id);}} className="tap-expand rounded-lg border border-slate-300 px-2.5 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60">Promote</button>}
                    <button type="button" aria-label={`Edit ${run.version ?? run.name ?? run.algorithm ?? 'version'}`} title="Version actions" onClick={(e)=>{e.stopPropagation(); setEditModel(run);}} className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><PencilIcon className="h-3.5 w-3.5" strokeWidth={1.9} /></button>
                  </span>; }},
            ]}
          />
          <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2">
            <p className="text-micro text-slate-600">Trained on imported demand from {selected?.train_start ?? '—'} to {selected?.train_end ?? '—'} · {selected?.version ? `model version ${selected.version}` : modelLabel(selected?.id)}</p>
            <button type="button" onClick={()=> selected && void rollback(selected.id)} className="tap-expand inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50"><RotateCcwIcon className="h-3.5 w-3.5" strokeWidth={2} />Roll back</button>
          </div>
        </Panel>

        <Panel title="Feature contribution" icon={BrainCircuitIcon} description={`${importanceSourceLabel(importanceMethod)}${selected?.version ? ` for ${selected.version}` : ''}. Predictive weight, not causal effect.`}>
          {importance.length===0 ? <EmptyState variant="empty" size="sm" title="No importance data" description="Feature importance not available for this model." /> : (
            <>
              <ul className="flex flex-col gap-2">{importance.map((item)=>
              <li key={item.feature}>
                  <div className="flex items-baseline justify-between gap-3"><span className="truncate text-meta text-slate-800">{item.feature}</span><span className="shrink-0 text-micro tabular-nums text-slate-500">{item.importance}%</span></div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200"><div className={`h-full rounded-full ${groupTone[item.group] ?? 'bg-brand-600'}`} style={{width:`${item.importance}%`}} /></div>
                </li>
              )}</ul>
              <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">{Object.entries(groupTone).slice(0,4).map(([label,tone])=> <li key={label} className="flex items-center gap-1.5 text-fine text-slate-500"><span className={`h-2 w-2 rounded-full ${tone}`} />{label}</li>)}</ul>
            </>
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-3">
        <Panel title={selected?.version ?? selected?.algorithm ?? 'Model detail'} description={selected?.algorithm ?? ''}>
          {selected ? (
            <dl className="flex flex-col gap-2">{detailRows.map(([label,value])=> <div key={label} className="flex items-baseline justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0"><dt className="text-caption text-slate-500">{label}</dt><dd className="text-body-xs font-semibold tabular-nums text-slate-900">{value}</dd></div>)}</dl>
          ) : <EmptyState variant="empty" size="sm" title="No selection" description="Select a model to see details." />}
        </Panel>

        {/* The version dialog: what can change about a trained version is its
            place in production; the metrics are fixed at training time, so the
            dialog offers promote / roll back rather than pretending to edit them. */}
        <Modal open={!!editModel} onClose={()=>setEditModel(null)} title={`Edit ${editModel?.version ?? editModel?.name ?? editModel?.algorithm ?? 'version'}`} description="Metrics and hyperparameters are fixed at training time. What can change is this version's place in production." footer={<>
          <ModalButton tone="ghost" onClick={()=>setEditModel(null)}>Close</ModalButton>
          {editModel && (editModel.status==='production' || editModel.status==='live')
            ? <ModalButton tone="danger" disabled={actionId===editModel.id} onClick={()=>{const id=editModel.id; setEditModel(null); void rollback(id);}}>Roll back production</ModalButton>
            : <ModalButton disabled={actionId===editModel?.id} onClick={()=>{const id=editModel.id; setEditModel(null); void promote(id);}}>Promote to production</ModalButton>}
        </>}>
          {editModel && (
            <dl className="flex flex-col gap-2">
              {([
                ['Version', editModel.version ?? editModel.name ?? '—'],
                ['Algorithm', editModel.algorithm ?? editModel.family ?? '—'],
                ['Status', plainStatus(editModel.status)],
                ['MAE', editModel.mae != null ? Number(editModel.mae).toFixed(2) : '—'],
                ['RMSE', editModel.rmse != null ? Number(editModel.rmse).toFixed(2) : '—'],
                ['RMSLE', editModel.rmsle != null ? Number(editModel.rmsle).toFixed(3) : '—'],
                ['Trained on', editModel.train_start && editModel.train_end ? `${editModel.train_start} → ${editModel.train_end}` : '—'],
                ['Artifact', editModel.artifact_uri ?? '—'],
              ] as Array<[string,string]>).map(([label,value])=> (
                <div key={label} className="flex items-baseline justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0"><dt className="text-caption text-slate-500">{label}</dt><dd className="text-body-xs font-semibold tabular-nums text-slate-900">{value}</dd></div>
              ))}
            </dl>
          )}
        </Modal>

        <Panel title="Conformal calibration" description="Adaptive intervals under drift.">
          {!calibration ? <EmptyState variant="empty" size="sm" title="No calibration" description="Calibration data not available." /> : (
            <>
              {/* Coverage figures are served as percentages (schema NUMERIC(5,2)
                  default 90.00), so they are rendered directly (an extra ×100
                  here printed 9000%. */}
              <div className="flex items-baseline justify-between gap-3"><span className="text-caption text-slate-500">Target coverage</span><span className="text-body-xs font-semibold text-slate-900">{calibration.target_coverage != null ? `${pct(calibration.target_coverage)}` : '90%'}</span></div>
              <div className="mt-1.5 flex items-baseline justify-between gap-3"><span className="text-caption text-slate-500">Empirical coverage</span><span className="text-body-xs font-semibold text-brand-700">{calibration.empirical_coverage != null ? `${pct(calibration.empirical_coverage, 1)}` : '—'}</span></div>
              <div className="mt-1.5 flex items-baseline justify-between gap-3"><span className="text-caption text-slate-500">Mean interval width</span><span className="text-body-xs font-semibold tabular-nums text-slate-900">{calibration.mean_interval_width ?? '—'}</span></div>
              <p className="mt-2.5 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-600">Coverage from held-out window; uncertainty-aware policy can be trusted at this quantile.</p>
              <CoverageSensitivity sensitivity={calibration.coverage_sensitivity} />
              <p className="mt-2 text-fine text-slate-500">{modelLabel(selected?.id)} · {calibration.evaluated_at ? new Date(calibration.evaluated_at).toLocaleDateString() : ''}</p>
            </>
          )}
        </Panel>
      </div>
      </div>
    </div>
  );
}
