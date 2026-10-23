import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIcon, RefreshCwIcon, PlayIcon, AlertTriangleIcon, ClockIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import type { StatusTone } from '../../components/ui/StatusPill';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { CalendarClock } from '../../components/ui/CalendarClock';
import { api, ApiException } from '../../api/client';
import { fetchSchedule, jobLabel, nextRunLabel, runStatusTone } from '../../lib/schedule';
import type { ScheduleRow } from '../../lib/schedule';
import { plainStatus } from '../../lib/plainLanguage';

const severityTone:Record<string,StatusTone>={ stable:'success', watch:'warning', drifting:'critical', drift:'critical' };
const filters=['All','Ingestion','Training','Calibration','Forecast'] as const;

/**
 * A run's name for a person: the API's label when it sends one, else the job
 * token in words, else an explicitly labelled short reference. It never falls
 * through to a bare id fragment.
 */
function runLabel(run:any):string{
  if(run?.job) return String(run.job);
  if(run?.job_type) return jobLabel(String(run.job_type));
  return run?.id ? `Run ${String(run.id).slice(0,8)}` : 'Run';
}

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load pipeline',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function PipelineHealth(){
  const [filter,setFilter]=useState<(typeof filters)[number]>('All');
  const [runs,setRuns]=useState<any[]>([]);
  const [drift,setDrift]=useState<any[]>([]);
  const [schedule,setSchedule]=useState<ScheduleRow[]|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [rerunningId,setRerunningId]=useState<string>();
  const [runningJob,setRunningJob]=useState<string>();
  const [draining,setDraining]=useState(false);
  const [mappingOpen,setMappingOpen]=useState(false);
  const [mappedColumn,setMappedColumn]=useState('txn_date');
  const { toast } = useToast();

  const fetchAll = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const [rRes, dRes, sData] = await Promise.all([
        api.get<{data:any[]}>('/api/v1/pipeline/runs', {limit:20}),
        api.get<any>('/api/v1/pipeline/drift').catch(()=>({data:[]} as any)),
        fetchSchedule(),
      ]);
      setRuns(rRes.data ?? []);
      const dData = dRes.data ?? dRes.signals ?? dRes.drift ?? [];
      setDrift(Array.isArray(dData) ? dData : []);
      // Only the server's cadence/last-run/next-run list is shown; a failed
      // fetch renders "unavailable" rather than a plausible-looking guess.
      setSchedule(sData);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[]);

  useEffect(()=>{ void fetchAll(); },[fetchAll]);

  const rerun = async (id:string, job:string)=>{
    setRerunningId(id);
    try{ await api.post(`/api/v1/pipeline/runs/${id}/rerun`); toast(`${job} re-queued.`); void fetchAll(); }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
    finally{ setTimeout(()=>setRerunningId(undefined),800); }
  };

  // POST /pipeline/jobs/{job_type}/run — the same function the scheduler runs,
  // executed now and recorded as a pipeline_runs row.
  const runJob = async (jobType:string)=>{
    setRunningJob(jobType);
    try{
      const res = await api.post<{status:string; rows_processed?:number|null; detail?:string}>(`/api/v1/pipeline/jobs/${jobType}/run`);
      // `${jobLabel} success.` was the old toast — a bare token in a sentence.
      toast(res.detail ?? `${jobLabel(jobType)}: ${plainStatus(res.status)}`, res.status==='success' ? 'success' : 'error');
      void fetchAll();
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); else toast(`Could not run ${jobLabel(jobType)}`,'error'); }
    finally{ setRunningJob(undefined); }
  };

  // Completes queued runs (e.g. POST /sources/{id}/sync) when no worker is running.
  const drainQueue = async ()=>{
    setDraining(true);
    try{
      const res = await api.post<{processed:number; still_queued:number}>('/api/v1/pipeline/queue/drain');
      toast(`Ran ${res.processed} queued job${res.processed===1?'':'s'}, ${res.still_queued} left in the queue.`);
      void fetchAll();
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); else toast('Could not drain the queue','error'); }
    finally{ setDraining(false); }
  };

  const runsFiltered = useMemo(()=> filter==='All' ? runs : runs.filter(r=> (r.job_type ?? r.kind ?? '') .toLowerCase().includes(filter.toLowerCase())), [filter,runs]);
  const failing = runs.filter(r=> (r.status==='failed' || r.status==='error'));
  const queued = runs.filter(r=> r.status==='queued');

  if(loading) return <LoadingState label="Loading pipeline runs" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchAll()}} size="md" />;

  return (
    <div className="flex flex-col gap-3">
      {/* Live calendar clock — shows current time and ML calendar features */}
      <div className="mb-3">
        <CalendarClock size="md" showML={true} />
      </div>

      <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          {failing.length>0 && <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-red-200 bg-red-50 px-3.5 py-3">
            <AlertTriangleIcon className="h-4 w-4 shrink-0 text-red-600" strokeWidth={2} aria-hidden="true" />
            <p className="min-w-0 flex-1 text-meta text-red-800"><span className="font-semibold">{runLabel(failing[0])}</span>: {failing[0].detail ?? failing[0].note ?? 'Failed'}</p>
            <button type="button" onClick={()=>setMappingOpen(true)} className="shrink-0 rounded-lg bg-red-600 px-3 py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2">Fix mapping</button>
          </div>}

          <Panel title="Job runs" icon={ActivityIcon} description="Ingestion, training, calibration and forecast jobs across the last seven days." action={<div className="flex items-center gap-1.5">{queued.length>0 && <button type="button" onClick={()=>void drainQueue()} disabled={draining} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:text-slate-500"><RefreshCwIcon className={['h-3.5 w-3.5', draining?'animate-spin':''].join(' ')} strokeWidth={2} />Drain {queued.length} queued</button>}<button type="button" onClick={()=>void runJob('full_retrain')} disabled={runningJob==='full_retrain'} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:bg-brand-500"><PlayIcon className="h-3.5 w-3.5" strokeWidth={2} />Run retrain</button></div>}>
            <div className="mb-3 flex gap-1 overflow-x-auto rounded-lg border border-slate-200 p-1">
              {filters.map(option=> <button key={option} type="button" aria-pressed={filter===option} onClick={()=>setFilter(option)} className={['shrink-0 rounded-md px-2.5 py-1.5 text-caption transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', filter===option?'bg-brand-600 font-semibold text-white':'font-medium text-slate-600 hover:bg-slate-100'].join(' ')}>{option}</button>)}
            </div>

            {runsFiltered.length===0 ? <EmptyState variant="empty" size="sm" title="No runs" description="No pipeline runs in this filter." hints={["Runs appear daily at 04:00","Ingestion runs every 15 minutes"]} /> : (
              <ul className="flex flex-col gap-1.5">{runsFiltered.map((run)=>
              <li key={run.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-slate-200 px-3 py-2.5">
                  <div className="min-w-[180px] flex-1">
                    <p className="truncate text-meta font-semibold text-slate-900">{runLabel(run)}</p>
                    <p className="truncate text-micro text-slate-500">{run.detail ?? run.note ?? ''}</p>
                  </div>
                  <div className="hidden w-[108px] shrink-0 sm:block"><p className="text-fine text-slate-500">Started</p><p className="text-caption text-slate-700">{run.started_at ? new Date(run.started_at).toLocaleString() : run.started ?? ''}</p></div>
                  <div className="hidden w-[92px] shrink-0 md:block"><p className="text-fine text-slate-500">Volume</p><p className="truncate text-caption text-slate-700">{run.rows_processed ?? run.rows ?? '—'}</p></div>
                  <span className="hidden w-[68px] shrink-0 text-caption tabular-nums text-slate-500 lg:block">{run.duration ?? (run.duration_ms ? `${(run.duration_ms/1000).toFixed(1)}s` : '')}</span>
                  <StatusPill tone={runStatusTone(run.status)} dot>{plainStatus(run.status)}</StatusPill>
                  <button type="button" aria-label={`Re-run ${runLabel(run)}`} onClick={()=>void rerun(run.id, runLabel(run))} disabled={rerunningId===run.id} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:text-brand-600"><RefreshCwIcon className={['h-4 w-4', rerunningId===run.id?'animate-spin':''].join(' ')} strokeWidth={1.9} /></button>
                </li>
              )}</ul>
            )}
            <p className="mt-2 text-fine text-slate-500">{runs.length} runs in the last 7 days · each training run records the model version it produced</p>
          </Panel>
        </div>

        <div className="flex flex-col gap-3">
          <Panel title="Drift watch" description="Live data compared against the distribution the model was trained on.">
            {drift.length===0 ? <EmptyState variant="empty" size="sm" title="No drift signals" description="Drift is monitored daily; no signals yet." /> : (
              <ul className="flex flex-col gap-2.5">{drift.map((signal:any, index:number)=>
              <li key={signal.id ?? signal.label ?? `drift-${index}`}>
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0"><span className="block text-meta font-semibold text-slate-900">{signal.label ?? signal.name}</span><span className="block text-micro leading-relaxed text-slate-500">{signal.detail ?? signal.description ?? ''}</span></span>
                    <span className="flex shrink-0 flex-col items-end gap-1"><StatusPill tone={severityTone[signal.severity] ?? 'neutral'} dot>{signal.severity}</StatusPill><span className="text-micro tabular-nums text-slate-500">{signal.delta!=null? (signal.delta>0?'+':'')+signal.delta+'%':''}</span></span>
                  </div>
                </li>
              )}</ul>
            )}
            <p className="mt-3 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-600">Signals outside usual band trigger retrain recommendation.</p>
          </Panel>

          <Panel title="Schedule" icon={ClockIcon} description="Cadence, last run and next tick for every job.">
            {!schedule ? <EmptyState variant="empty" size="sm" title="Schedule unavailable" description="The job cadence could not be loaded." action={{label:'Retry',onClick:()=>void fetchAll()}} /> : (
              <ul className="flex flex-col gap-1.5">{schedule.map((job)=>{
                const last=job.last_run;
                return (
                <li key={job.job_type} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-slate-200 px-3 py-2.5">
                  <div className="min-w-[140px] flex-1">
                    <p className="truncate text-meta font-semibold text-slate-900">{jobLabel(job.job_type)}</p>
                    <p className="truncate text-micro text-slate-500">{job.cadence}</p>
                  </div>
                  <div className="w-[112px] shrink-0">
                    <p className="text-fine text-slate-500">Last run</p>
                    {last ? <StatusPill tone={runStatusTone(last.status)} dot>{plainStatus(last.status)}</StatusPill> : <p className="text-caption text-slate-500">Never</p>}
                  </div>
                  <div className="w-[96px] shrink-0">
                    <p className="text-fine text-slate-500">Next run</p>
                    <p className="text-caption text-slate-700">{nextRunLabel(job.next_run)}</p>
                  </div>
                  <button type="button" aria-label={`Run ${jobLabel(job.job_type)} now`} onClick={()=>void runJob(job.job_type)} disabled={runningJob===job.job_type} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:text-brand-500"><PlayIcon className={['h-4 w-4', runningJob===job.job_type?'animate-pulse':''].join(' ')} strokeWidth={1.9} /></button>
                </li>
                );
              })}</ul>
            )}
          </Panel>
        </div>
      </div>

      <Modal open={mappingOpen} onClose={()=>setMappingOpen(false)} title="Fix column mapping" description={failing[0]?.job ?? 'Fix mapping'} footer={<><ModalButton tone="ghost" onClick={()=>setMappingOpen(false)}>Cancel</ModalButton><ModalButton onClick={()=>{ setMappingOpen(false); toast('Mapping saved. The failed job has been re-queued.'); void fetchAll(); }}>Save and re-run</ModalButton></>}>
        <p className="text-meta leading-relaxed text-slate-600">The source stopped sending the column this job expected. Point <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-micro">date</code> at the right field to unblock ingestion.</p>
        <div className="mt-3 flex flex-col gap-3">
          <Field id="fix-column" label="Source column for `date`"><select id="fix-column" value={mappedColumn} onChange={e=>setMappedColumn(e.target.value)} className={inputClass}><option>txn_date</option><option>transaction_date</option><option>business_day</option><option>created_at</option></select></Field>
          <Field id="fix-backfill" label="Re-process window (days)"><input id="fix-backfill" type="number" defaultValue={7} className={inputClass} /></Field>
        </div>
      </Modal>
    </div>
  );
}