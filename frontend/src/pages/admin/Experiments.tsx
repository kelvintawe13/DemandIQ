import { useCallback, useEffect, useState } from 'react';
import { FlaskConicalIcon, PlayIcon, ArrowUpRightIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import type { StatusTone } from '../../components/ui/StatusPill';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';
import { plainStatus } from '../../lib/plainLanguage';

const statusTone:Record<string,StatusTone>={ completed:'success', complete:'success', running:'info', draft:'neutral', pending:'info' };

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load experiments',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check your connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function Experiments(){
  const [experiments,setExperiments]=useState<any[]>([]);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [folds,setFolds]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [newOpen,setNewOpen]=useState(false);
  const [submitted,setSubmitted]=useState<string[]>([]);
  const [draft,setDraft]=useState({name:'', hypothesis:'', algorithm:'LightGBM', folds:'6', feature_set:'v1'});
  const { toast } = useToast();

  const fetchList = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const res = await api.get<{data:any[]}>('/api/v1/experiments');
      const data = res.data ?? [];
      setExperiments(data);
      if(data.length && !selectedId) setSelectedId(data[0].id);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[selectedId]);

  const fetchDetail = useCallback(async(id:string)=>{
    try{
      const res = await api.get<any>(`/api/v1/experiments/${id}`);
      const f = res.folds ?? res.fold_results ?? [];
      setFolds(f);
    }catch{ setFolds([]); }
  },[]);

  useEffect(()=>{ void fetchList(); },[fetchList]);
  useEffect(()=>{ if(selectedId) void fetchDetail(selectedId); },[selectedId, fetchDetail]);

  const selected = experiments.find(item=>item.id===selectedId);
  const delta = selected ? ( (selected.baseline_mae ?? selected.baselineMae ?? 0) - (selected.mae ?? 0) ) : 0;
  const maxFoldMae = folds.length ? Math.max(...folds.map((f:any)=> Number(f.mae ?? 0)),1) : 1;

  const create = async()=>{
    if(!draft.name.trim()) return;
    try{
      const res = await api.post<{id:string}>('/api/v1/experiments',{name:draft.name.trim(), hypothesis:draft.hypothesis.trim()||undefined, algorithm:draft.algorithm, feature_set:draft.feature_set, folds:Number(draft.folds)});
      toast('Backtest queued. Results appear when all folds finish.');
      setNewOpen(false);
      setDraft({name:'',hypothesis:'',algorithm:'LightGBM',folds:'6',feature_set:'v1'});
      void fetchList();
      if(res.id) setSelectedId(res.id);
    }catch(e){
      if(e instanceof ApiException){
        if(e.status===422) toast(e.error.message,'error');
        else toast(e.error.message,'error');
      }
    }
  };

  const submit = async()=>{
    if(!selected) return;
    try{
      await api.post(`/api/v1/experiments/${selected.id}/submit`);
      setSubmitted(curr=>[...curr, selected.id]);
      toast('Sent to Approvals. A second admin must sign it off.');
    }catch(e){
      if(e instanceof ApiException){
        if(e.status===422 && e.error.code==='two_person_rule') toast(e.error.message,'error');
        else toast(e.error.message,'error');
      }
    }
  };

  if(loading) return <LoadingState label="Loading experiments" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchList()}} size="md" />;
  if(experiments.length===0) return (
    <>
      <EmptyState variant="empty" size="md" title="No experiments yet" description="Create a backtest to evaluate a new configuration." hints={["Rolling-origin evaluation","Chronological splits only"]} action={{label:'New experiment',onClick:()=>setNewOpen(true)}} />
      <Modal open={newOpen} onClose={()=>setNewOpen(false)} title="New experiment" description="Runs a rolling-origin backtest." footer={<><ModalButton tone="ghost" onClick={()=>setNewOpen(false)}>Cancel</ModalButton><ModalButton disabled={!draft.name.trim()} onClick={()=>void create()}>Run backtest</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="exp-name" label="Name"><input id="exp-name" type="text" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} placeholder="Add weather features" className={inputClass} /></Field>
          <Field id="exp-hypothesis" label="Hypothesis" hint="What you expect to change, and why."><textarea id="exp-hypothesis" rows={3} value={draft.hypothesis} onChange={e=>setDraft({...draft,hypothesis:e.target.value})} placeholder="Rainfall should lower walk-in customers" className={inputClass} /></Field>
          <Field id="exp-algorithm" label="Algorithm"><select id="exp-algorithm" value={draft.algorithm} onChange={e=>setDraft({...draft,algorithm:e.target.value})} className={inputClass}><option>LightGBM</option><option>CatBoost</option><option>XGBoost</option><option>Random Forest</option></select></Field>
          <Field id="exp-folds" label="Folds" hint="Expanding window, chronological."><select id="exp-folds" value={draft.folds} onChange={e=>setDraft({...draft,folds:e.target.value})} className={inputClass}><option>4</option><option>6</option><option>8</option></select></Field>
        </div>
      </Modal>
    </>
  );

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <Panel title="Backtests" icon={FlaskConicalIcon} description="Rolling-origin evaluations run before anything reaches the registry." action={<button type="button" onClick={()=>setNewOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlayIcon className="h-3.5 w-3.5" strokeWidth={2} />New experiment</button>}>
        <ul className="flex flex-col gap-2">
          {experiments.map((experiment)=>{
            const improvement = (experiment.baseline_mae ?? experiment.baselineMae ?? 0) - (experiment.mae ?? 0);
            const isSelected = experiment.id===selectedId;
            return (
              <li key={experiment.id}>
                <button type="button" aria-pressed={isSelected} onClick={()=>setSelectedId(experiment.id)} className={['w-full rounded-xl border px-3.5 py-3 text-left transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', isSelected?'border-brand-500 bg-brand-50/60':'border-slate-200 hover:bg-slate-50'].join(' ')}>
                  <span className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                    <span className="min-w-0"><span className="block truncate text-body-xs font-semibold text-slate-900">{experiment.name}</span><span className="block truncate text-micro text-slate-500">{experiment.hypothesis ?? experiment.config?.feature_set ?? ''}</span></span>
                    <StatusPill tone={statusTone[experiment.status] ?? 'neutral'} dot>{plainStatus(experiment.status)}</StatusPill>
                  </span>
                  <span className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-slate-500">
                    <span>{experiment.config ? `${experiment.config.algorithm ?? experiment.algorithm} · ${experiment.config.folds ?? ''} folds` : experiment.config ?? ''}</span>
                    {(experiment.status!=='draft' && experiment.mae!=null) && <span className={['font-semibold tabular-nums', improvement>0?'text-brand-700':'text-red-600'].join(' ')} title="Average forecast error (MAE): lower is better">{improvement>0?'−':'+'}{Math.abs(improvement).toFixed(2)} lower error</span>}
                    <span className="ml-auto">{experiment.finished_at ? new Date(experiment.finished_at).toLocaleDateString() : experiment.finished ?? ''}</span>
                  </span>
                </button>
              </li>);
          })}
        </ul>
      </Panel>

      <div className="flex flex-col gap-3">
        {selected ? (
          <Panel title={selected.name} description={`Run by ${selected.created_by ?? selected.owner ?? '—'}`}>
            <dl className="grid grid-cols-2 gap-2">
              {[['Candidate error (MAE)', selected.mae!=null?Number(selected.mae).toFixed(2):'—'],['Baseline error (MAE)', selected.baseline_mae!=null?Number(selected.baseline_mae).toFixed(2): selected.baselineMae!=null? Number(selected.baselineMae).toFixed(2):'—'],['How often the range held', selected.coverage!=null?`${Number(selected.coverage).toFixed(1)}%`:'—'],['Backtest windows', String(selected.config?.folds ?? selected.folds ?? '—')]].map(([label,value])=> <div key={label} className="rounded-lg border border-slate-200 px-3 py-2"><dt className="text-fine text-slate-500">{label}</dt><dd className="text-h5 font-bold tabular-nums text-slate-900">{value}</dd></div>)}
            </dl>
            <p className="mt-2 text-fine leading-relaxed text-slate-500">Error is the average forecast miss in customers; lower is better. "How often the range held" is the share of test days where actual demand landed inside the prediction range.</p>
            {selected.status==='completed' && <button type="button" disabled={delta<=0 || submitted.includes(selected.id)} onClick={()=>void submit()} className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300">
                {submitted.includes(selected.id) ? 'Awaiting approval' : delta>0 ? 'Submit for promotion' : 'No improvement to promote'}
                {delta>0 && !submitted.includes(selected.id) && <ArrowUpRightIcon className="h-4 w-4" strokeWidth={2} />}
              </button>}
            <p className="mt-2 text-fine text-slate-500">Compared on your demand data · candidate model versions</p>
          </Panel>
        ) : <Panel title="Select an experiment"><EmptyState variant="empty" size="sm" title="No selection" description="Pick an experiment." /></Panel>}

        <Panel title="Backtest windows" description="Each window trains on the past and tests forward, so the score is honest. Coverage is the share of that window's test days the forecast range held.">
          {folds.length===0 ? <EmptyState variant="empty" size="sm" title="No window data" description="Per-window results appear once the backtest completes." /> : (
            <ul className="flex flex-col gap-2">{folds.map((fold:any, idx:number)=>
            <li key={fold.id ?? idx}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5"><span className="text-caption font-medium text-slate-800">{`Window ${(fold.fold_index ?? idx) + 1}`}</span><span className="flex items-baseline gap-3"><span className="text-micro tabular-nums text-slate-500" title="Share of this window's test days where actual demand fell inside the forecast range">{fold.coverage!=null? `${Number(fold.coverage).toFixed(0)}% covered`:'—'}</span><span className="text-micro tabular-nums text-slate-500" title="Average forecast error for this window (lower is better)">{fold.mae!=null? Number(fold.mae).toFixed(2):'—'}</span></span></div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="w-14 shrink-0 text-fine text-slate-500" title="Average forecast error for this window (lower is better)">Error</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-brand-600" style={{width:`${fold.mae!=null? Math.min(100, Math.max(0, Number(fold.mae)/maxFoldMae*100)):0}%`}} /></div>
                </div>
                {fold.coverage!=null && (
                  <div className="mt-1 flex items-center gap-2">
                    <span className="w-14 shrink-0 text-fine text-slate-500" title="Share of this window's test days where actual demand fell inside the forecast range">Covered</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-emerald-500" style={{width:`${Math.min(100, Math.max(0, Number(fold.coverage)))}%`}} /></div>
                  </div>
                )}
                <p className="mt-0.5 text-fine text-slate-500">Train {fold.train_start ?? fold.train ?? ''} · test {fold.test_start ?? fold.test ?? ''}</p>
              </li>
            )}</ul>
          )}
        </Panel>
      </div>

      <Modal open={newOpen} onClose={()=>setNewOpen(false)} title="New experiment" description="Runs a rolling-origin backtest. Nothing reaches production from here." footer={<><ModalButton tone="ghost" onClick={()=>setNewOpen(false)}>Cancel</ModalButton><ModalButton disabled={!draft.name.trim()} onClick={()=>void create()}>Run backtest</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="exp-name" label="Name"><input id="exp-name" type="text" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} placeholder="Add weather features" className={inputClass} /></Field>
          <Field id="exp-hypothesis" label="Hypothesis" hint="What you expect to change, and why."><textarea id="exp-hypothesis" rows={3} value={draft.hypothesis} onChange={e=>setDraft({...draft,hypothesis:e.target.value})} placeholder="Rainfall should lower walk-in customers on open-air sites." className={inputClass} /></Field>
          <Field id="exp-algorithm" label="Algorithm"><select id="exp-algorithm" value={draft.algorithm} onChange={e=>setDraft({...draft,algorithm:e.target.value})} className={inputClass}><option>LightGBM</option><option>CatBoost</option><option>XGBoost</option><option>Random Forest</option></select></Field>
          <Field id="exp-folds" label="Folds" hint="Expanding window, chronological."><select id="exp-folds" value={draft.folds} onChange={e=>setDraft({...draft,folds:e.target.value})} className={inputClass}><option>4</option><option>6</option><option>8</option></select></Field>
          <Field id="exp-feature-set" label="Feature set"><input id="exp-feature-set" value={draft.feature_set} onChange={e=>setDraft({...draft,feature_set:e.target.value})} className={inputClass} /></Field>
        </div>
      </Modal>
    </div>
  );
}
