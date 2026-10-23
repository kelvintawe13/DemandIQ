import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SmartphoneIcon, CreditCardIcon, FileSpreadsheetIcon, PenLineIcon, PlugZapIcon, KeyIcon, CopyIcon, ArrowRightIcon, TrashIcon, TestTubeIcon, CheckCircle2Icon, XCircleIcon, RefreshCwIcon, type LucideIcon } from "lucide-react";
import { Panel } from "../../components/ui/Panel";
import { LoadingState } from "../../components/ui/LoadingState";
import { StatusPill, StatusTone } from "../../components/ui/StatusPill";
import { Modal, ModalButton } from "../../components/ui/Modal";
import { Field, inputClass } from "../../components/ui/Field";
import { useToast } from "../../contexts/ToastContext";
import { EmptyState } from "../../components/ui/EmptyState";
import { api, ApiException } from "../../api/client";
import { RecordList } from "../../components/ui/RecordList";
import { FormModal } from "../../components/ui/FormModal";
import { plainStatus } from "../../lib/plainLanguage";

const channelIcon: Record<string, LucideIcon> = {
  'Mobile money': SmartphoneIcon, 'momo': SmartphoneIcon,
  POS: CreditCardIcon, 'pos': CreditCardIcon,
  File: FileSpreadsheetIcon, 'file': FileSpreadsheetIcon,
  Handwritten: PenLineIcon, 'ocr': PenLineIcon,
  API: PlugZapIcon, 'api': PlugZapIcon, 'sms': SmartphoneIcon
};
const typeToChannel:Record<string,string> = { momo:'Mobile money', pos:'POS', file:'File', ocr:'Handwritten', api:'API', sms:'API' };
const channelToType:Record<string,string> = { 'Mobile money':'momo', POS:'pos', File:'file', Handwritten:'ocr', API:'api' };
const statusTone: Record<string, StatusTone> = { connected:'success', active:'success', syncing:'info', error:'critical', available:'neutral', paused:'neutral', failing:'critical' };
const filters = ['All','Mobile money','POS','File','Handwritten','API'] as const;

/** What each precondition the test endpoint reports actually checks. */
const CHECK_LABELS: Record<string,string> = {
  type: 'Channel type',
  name: 'Channel name',
  credentials: 'Credentials',
  locations: 'Linked locations',
  mapping: 'Field mapping',
};

/**
 * Where a failing check is actually fixed. The endpoint runs each precondition
 * a sync depends on and reports all of them, but it can change none of them:
 * four live on the channel itself, and the mapping has its own panel on this
 * screen, so that one can be pointed at.
 */
const CHECK_FIX: Record<string,{hint:string; jump?:boolean}> = {
  type: { hint: 'The channel type is fixed when the channel is added.' },
  name: { hint: 'The channel name is fixed when the channel is added.' },
  credentials: { hint: 'Credentials belong to the channel and are not edited on this screen.' },
  locations: { hint: 'With no linked location there is nowhere for imported rows to land.' },
  mapping: { hint: 'Nothing maps this source onto the columns a sync needs. The Field mapping panel below shows what this connector has.', jump: true },
};

/** One connector test run, as `POST /connectors/{id}/test` reports it. */
type TestRun = {
  connectorId: string;
  name: string;
  status: string;
  latency_ms?: number;
  checks: any[];
  sample_payload: Record<string, unknown>;
  checked_at?: string;
};

/** One dry-run pull, as `POST /connectors/{id}/pull?dry_run=true` reports it. */
type PullPreview = {
  connectorId: string;
  name: string;
  status: string;
  pulled: number;
  committed: number;
  skipped: number;
  rows: { location_id: string; date: string; demand_qty: number; notes: string | null }[];
  errors: string[];
  detail: string;
};

function checkedAtLabel(iso?: string | null){
  if(!iso) return '';
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleString();
}

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load connectors',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function Connectors(){
  const [filter,setFilter]=useState<(typeof filters)[number]>('All');
  const [connectors,setConnectors]=useState<any[]>([]);
  const [keys,setKeys]=useState<any[]>([]);
  const [mappings,setMappings]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [syncingId,setSyncingId]=useState<string>();
  const [addOpen,setAddOpen]=useState(false);
  const [keyOpen,setKeyOpen]=useState(false);
  // The edit dialog: name and description for an existing channel. The channel
  // type is fixed at creation (the test-run panel says so), so it is not offered.
  const [editConnector,setEditConnector]=useState<any>(null);
  const [editDraft,setEditDraft]=useState({name:'', detail:''});
  const [saving,setSaving]=useState(false);
  const [draft,setDraft]=useState({name:'', channel:'Mobile money', detail:''});
  const [keyDraft,setKeyDraft]=useState({label:'', scope:'read:forecasts'});
  const [keySecret,setKeySecret]=useState<string|null>(null);
  const [testRun,setTestRun]=useState<TestRun|null>(null);
  const [pullPreview,setPullPreview]=useState<PullPreview|null>(null);
  const mappingRef=useRef<HTMLDivElement>(null);
  const { toast } = useToast();

  // A failed mapping check has a fix on this screen; the other four do not, so
  // the panel says where they live instead of offering a button that would not
  // change anything.
  const showMapping = ()=>{
    const el = mappingRef.current;
    if(!el) return;
    el.scrollIntoView?.({ behavior:'smooth', block:'start' });
    el.focus?.();
  };

  const fetchAll = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const [cRes, kRes] = await Promise.all([
        api.get<{data:any[]}>('/api/v1/connectors'),
        api.get<{data:any[]}>('/api/v1/api-keys').catch(()=>({data:[]} as any)),
      ]);
      setConnectors(cRes.data ?? []);
      setKeys(kRes.data ?? []);
      // try to fetch mapping for first connector if exists
      if(cRes.data && cRes.data.length){
        try{
          // `GET /connectors/{id}/mapping` returns
          // `{mappings:[{id, source_field, canonical_field, transform}]}`. When the
          // read failed or had nothing configured, this used to substitute a
          // hardcoded momo/pos mapping as if it were the workspace's own.
          const m = await api.get<any>(`/api/v1/connectors/${cRes.data[0].id}/mapping`).catch(()=>null);
          setMappings(m ? m.data ?? m.mappings ?? [] : []);
        }catch{ setMappings([]); }
      }
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[]);

  useEffect(()=>{ void fetchAll(); },[fetchAll]);

  const visible = useMemo(()=> {
    if(filter==='All') return connectors;
    return connectors.filter(c=> {
      const ch = typeToChannel[c.type] ?? c.type;
      return ch===filter;
    });
  },[filter,connectors]);

  const testConnector = async (id:string, name:string)=>{
    setSyncingId(id);
    try{
      const res = await api.post<any>(`/api/v1/connectors/${id}/test`);
      // The run is kept on screen rather than flashed in a toast: the endpoint
      // reports every precondition it checked, the canonical schema the
      // connector must produce (`sample_payload`) and when it ran
      // (`checked_at`) — none of which survive a toast, and all of which the
      // operator needs to fix a blocked connector.
      setTestRun({
        connectorId: id,
        name,
        status: res.status ?? 'ok',
        latency_ms: res.latency_ms,
        checks: Array.isArray(res.checks) ? res.checks : [],
        sample_payload: res.sample_payload ?? {},
        checked_at: res.checked_at,
      });
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
    finally{ setTimeout(()=>setSyncingId(undefined),800); }
  };

  /**
   * Fetch and map the provider's rows without writing any of them.
   *
   * This is the on-demand pull's dry run: it shows exactly what a real pull
   * would commit, so a mapping or a credential can be checked before the
   * channel is activated. It is allowed on any status because it writes
   * nothing; a real pull is active-only.
   */
  const previewPull = async(id:string, name:string)=>{
    setSyncingId(id);
    try{
      const res = await api.post<any>(`/api/v1/connectors/${id}/pull?dry_run=true`);
      setPullPreview({
        connectorId: id,
        name,
        status: res.status ?? 'preview',
        pulled: Number(res.pulled ?? 0),
        committed: Number(res.committed ?? 0),
        skipped: Number(res.skipped ?? 0),
        rows: Array.isArray(res.rows) ? res.rows : [],
        errors: Array.isArray(res.errors) ? res.errors : [],
        detail: String(res.detail ?? ''),
      });
    }
    catch(e){
      // A refusal (no endpoint configured) carries the reason; the panel is for
      // a pull that ran, a toast is for one that could not start.
      if(e instanceof ApiException) toast(e.error.message,'error');
    }
    finally{ setTimeout(()=>setSyncingId(undefined),800); }
  };

  const addChannel = async()=>{
    const name=draft.name.trim(); if(!name) return;
    try{
      const type = channelToType[draft.channel] ?? 'api';
      await api.post('/api/v1/connectors',{type, name, location_ids:[], credentials:{detail:draft.detail}});
      toast(`${name} added.`);
      setDraft({name:'', channel:'Mobile money', detail:''});
      setAddOpen(false);
      void fetchAll();
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  const openEdit = (connector:any)=>{
    setEditConnector(connector);
    setEditDraft({name:String(connector.name ?? ''), detail:String(connector.credentials?.detail ?? connector.detail ?? '')});
  };
  const saveEdit = async()=>{
    if(!editConnector || !editDraft.name.trim()) return;
    setSaving(true);
    try{
      await api.patch(`/api/v1/connectors/${editConnector.id}`,{name:editDraft.name.trim(), credentials:{detail:editDraft.detail.trim()}});
      toast('Channel updated');
      setEditConnector(null);
      void fetchAll();
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
    finally{ setSaving(false); }
  };
  const delConnector = async(id:string)=>{
    // Removing a channel cannot be undone, so confirm before it happens.
    if(!window.confirm('Remove this channel? Demand already imported from it is kept.')) return;
    try{
      await api.delete(`/api/v1/connectors/${id}`);
      toast('Connector removed','info');
      // A run about a connector that no longer exists would describe nothing.
      setTestRun(current => current?.connectorId === id ? null : current);
      void fetchAll();
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  const generateKey = async()=>{
    const label=keyDraft.label.trim(); if(!label) return;
    try{
      const res = await api.post<any>('/api/v1/api-keys',{label, scope:keyDraft.scope});
      const secret = res.secret ?? res.api_key ?? res.key ?? `dq_live_${Math.random().toString(36).slice(2,10)}`;
      setKeySecret(secret);
      navigator.clipboard?.writeText(secret);
      toast('Key created and copied. It is shown only once.');
      setKeyOpen(false);
      void fetchAll();
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  if(loading) return <LoadingState label="Loading connectors" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchAll()}} size="md" />;

  return <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <Panel title="Channels" description="Every way demand reaches DemandIQ, all turned into one daily demand figure." action={<button type="button" onClick={()=>setAddOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlugZapIcon className="h-3.5 w-3.5" strokeWidth={2} />Add channel</button>}>
          <div className="mb-3 flex gap-1 overflow-x-auto rounded-lg border border-slate-200 p-1">
            {filters.map(option=> <button key={option} type="button" aria-pressed={filter===option} onClick={()=>setFilter(option)} className={['shrink-0 rounded-md px-2.5 py-2.5 text-caption sm:py-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', filter===option?'bg-brand-600 font-semibold text-white':'font-medium text-slate-600 hover:bg-slate-100'].join(' ')}>{option}</button>)}
          </div>

          {connectors.length===0 ? (
            <EmptyState variant="empty" size="md" title="No connectors yet" description="Create your first connector to ingest demand." hints={["POS, mobile money, file, OCR","Connectors are workspace-scoped"]} action={{label:'Add channel',onClick:()=>setAddOpen(true)}} />
          ) : (
          <RecordList<any>
            stateKey="admin-connectors"
            items={visible}
            countNoun="channels"
            caption="Inbound channels"
            minWidth="min-w-[560px]"
            rowKey={(connector:any)=>String(connector.id)}
            searchFields={(connector:any)=> [connector.name, typeToChannel[connector.type] ?? connector.type, connector.status].filter(Boolean).join(' ')}
            searchPlaceholder="Search channels…"
            empty={<EmptyState variant="search" size="sm" title="No matches" description="No connectors match this filter." />}
            sortValue={(connector:any,k:string)=> k==='sync' ? String(connector.last_sync_at ?? '') : k==='channel' ? String(connector.name ?? '') : String(connector[k] ?? '')}
            columns={[
              { key:'channel', label:'Channel', primary:true, sortable:true, render:(connector:any)=>{
                const ch = typeToChannel[connector.type] ?? connector.type;
                const Icon = channelIcon[ch] ?? channelIcon[connector.type] ?? PlugZapIcon;
                return <span className="flex items-center gap-2.5"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50"><Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.8} /></span><span className="min-w-0"><span className="block truncate text-body-xs font-semibold text-slate-900">{connector.name}</span><span className="block truncate text-micro text-slate-500">{ch}</span></span></span>;
              } },
              { key:'health', label:'Health', render:(connector:any)=> <span className="text-caption font-medium text-slate-700">{plainStatus(connector.health ?? connector.status)}</span> },
              { key:'sync', label:'Last sync', sortable:true, render:(connector:any)=> <span className="text-caption font-medium text-slate-700">{connector.last_sync_at ? new Date(connector.last_sync_at).toLocaleDateString() : '—'}</span> },
              { key:'status', label:'Status', render:(connector:any)=> <StatusPill tone={statusTone[connector.status] ?? 'neutral'} dot>{plainStatus(connector.status)}</StatusPill> },
              { key:'actions', label:'Actions', cellClassName:'pr-0', render:(connector:any)=> (
                <span className="flex items-center justify-end gap-1">
                  <button type="button" aria-label={`Preview pull ${connector.name}`} title="Preview the pull (dry run)" onClick={()=>void previewPull(connector.id, connector.name)} disabled={syncingId===connector.id} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:text-brand-600"><RefreshCwIcon className={['h-4 w-4', syncingId===connector.id?'animate-spin text-brand-600':''].join(' ')} strokeWidth={1.9} /></button>
                  <button type="button" aria-label={`Test ${connector.name}`} onClick={()=>void testConnector(connector.id, connector.name)} disabled={syncingId===connector.id} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:text-brand-600"><TestTubeIcon className={['h-4 w-4', syncingId===connector.id?'animate-pulse text-brand-600':''].join(' ')} strokeWidth={1.9} /></button>
                  <button type="button" aria-label={`Edit ${connector.name}`} title="Edit channel" onClick={()=>openEdit(connector)} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><PenLineIcon className="h-4 w-4" strokeWidth={1.9} /></button>
                  <button type="button" aria-label={`Delete ${connector.name}`} onClick={()=>void delConnector(connector.id)} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><TrashIcon className="h-4 w-4" strokeWidth={1.9} /></button>
                </span>
              ) },
            ]}
          />
          )}
        </Panel>

        {testRun && (
          <Panel
            title={`Test run: ${testRun.name}`}
            description="What the connector check verified, and the shape it must produce."
            icon={TestTubeIcon}
            action={<button type="button" onClick={()=>setTestRun(null)} className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Dismiss</button>}
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={testRun.status==='ok'?'success':'critical'} dot>{plainStatus(testRun.status)}</StatusPill>
              {testRun.latency_ms != null && <span className="text-micro text-slate-500">answered in {testRun.latency_ms} ms</span>}
              {checkedAtLabel(testRun.checked_at) && <span className="text-micro text-slate-500">Checked {checkedAtLabel(testRun.checked_at)}</span>}
            </div>

            <div className="mt-3">
              <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
                What was checked ({testRun.checks.filter(c=>c?.ok).length} of {testRun.checks.length} passed)
              </p>
              <ul aria-label="Connector test checks" className="flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                {testRun.checks.map((check:any, index:number)=> (
                  <li key={check?.name ?? index} className="flex items-start gap-2 px-2.5 py-2">
                    {check?.ok
                      ? <CheckCircle2Icon className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" strokeWidth={1.9} aria-hidden="true" />
                      : <XCircleIcon className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" strokeWidth={1.9} aria-hidden="true" />}
                    <span className="min-w-0">
                      <span className="block text-caption font-medium text-slate-800">
                        {CHECK_LABELS[check?.name] ?? check?.name ?? 'Check'}
                        <span className="ml-1.5 font-normal text-slate-500">{check?.ok ? 'passed' : 'failed'}</span>
                      </span>
                      {check?.detail && <span className="block text-micro text-slate-500">{check.detail}</span>}
                      {!check?.ok && CHECK_FIX[check?.name] && (
                        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="text-micro text-rose-700">{CHECK_FIX[check.name].hint}</span>
                          {CHECK_FIX[check.name].jump && (
                            <button
                              type="button"
                              onClick={showMapping}
                              className="rounded border border-rose-300 px-1.5 py-0.5 text-fine font-semibold text-rose-800 transition-colors duration-150 ease-out hover:bg-rose-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
                            >Show field mapping</button>
                          )}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            {Object.keys(testRun.sample_payload ?? {}).length > 0 && (
              <div className="mt-3">
                <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Sample payload</p>
                <p className="mb-1.5 text-micro text-slate-500">
                  The shape this connector must produce. Each placeholder names the source field
                  that maps into it and the type it must carry; it is the schema, not a real demand row.
                </p>
                <dl className="flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {Object.entries(testRun.sample_payload).map(([field, value])=> (
                    <div key={field} className="flex items-center justify-between gap-3 px-2.5 py-1.5">
                      <dt className="font-mono text-micro font-medium text-slate-700">{field}</dt>
                      <dd className="truncate font-mono text-micro text-slate-500">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </Panel>
        )}

        {pullPreview && (
          <Panel
            title={`Pull preview: ${pullPreview.name}`}
            description="What a real pull would read from the provider — a dry run, so nothing is written."
            icon={RefreshCwIcon}
            action={<button type="button" onClick={()=>setPullPreview(null)} className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Dismiss</button>}
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={pullPreview.skipped>0?'warning':'success'} dot>
                {pullPreview.committed} of {pullPreview.pulled} ready to commit
              </StatusPill>
              <span className="text-micro text-slate-500">{pullPreview.detail}</span>
            </div>

            {pullPreview.rows.length>0 ? (
              <ul aria-label="Rows this pull would commit" className="mt-3 flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                {pullPreview.rows.slice(0,10).map((row, index)=> (
                  <li key={`${row.location_id}-${row.date}-${index}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-2.5 py-1.5">
                    <span className="font-mono text-micro text-slate-700">{row.date}</span>
                    <span className="text-caption font-semibold text-slate-800">{row.demand_qty}</span>
                    {row.notes && <span className="min-w-0 truncate text-micro text-slate-500">{row.notes}</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-caption text-slate-500">The provider returned no rows.</p>
            )}

            {pullPreview.errors.length>0 && (
              <div className="mt-3">
                <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Skipped rows</p>
                <ul className="flex flex-col gap-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2">
                  {pullPreview.errors.map((err, index)=> <li key={index} className="text-micro text-amber-800">{err}</li>)}
                </ul>
              </div>
            )}
          </Panel>
        )}

        <div ref={mappingRef} tabIndex={-1} className="scroll-mt-4 focus:outline-none">
        <Panel title="Field mapping" description="Which field in each source becomes which column in DemandIQ (left → right).">
          {mappings.length===0 ? <EmptyState variant="empty" size="sm" title="No mappings" description="No field mappings configured." /> : (
            <ul className="flex flex-col gap-1.5">{mappings.map((mapping:any, index:number)=>{
              // The API names the pair `source_field` → `canonical_field`. Reading
              // `source`/`target` left both chips empty on every row.
              const source = mapping.source_field ?? mapping.source ?? '';
              const canonical = mapping.canonical_field ?? mapping.target ?? '';
              return <li key={mapping.id ?? `${source}-${index}`} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg border border-slate-200 px-3 py-2">
                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-micro text-slate-700">{source}</code>
                <ArrowRightIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={2} />
                <code className="rounded bg-brand-50 px-1.5 py-0.5 font-mono text-micro font-semibold text-brand-800">{canonical}</code>
                <span className="ml-auto text-micro text-slate-500">{mapping.transform ?? ''}</span>
              </li>;
            })}</ul>
          )}
        </Panel>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <Panel title="API keys" icon={KeyIcon} description="Scoped credentials for partner systems.">
          {keys.length===0 ? <EmptyState variant="empty" size="sm" title="No keys" description="Generate an API key for partner systems." action={{label:'Generate new key',onClick:()=>setKeyOpen(true)}} /> : (
            <ul className="flex flex-col gap-2">{keys.map((key:any)=>
              <li key={key.id} className="rounded-xl border border-slate-200 px-3 py-2.5">
                <div className="flex items-start justify-between gap-2"><p className="text-meta font-semibold text-slate-900">{key.label ?? key.name}</p><StatusPill tone="neutral">{key.scope ?? key.scopes ?? ''}</StatusPill></div>
                <div className="mt-1.5 flex items-center justify-between gap-2"><code className="truncate font-mono text-micro text-slate-500">{key.prefix ?? key.key_prefix ?? '••••'}</code>
                  <button type="button" aria-label={`Copy ${key.label} key`} onClick={()=>{ navigator.clipboard?.writeText(key.prefix ?? ''); toast(`${key.label} prefix copied.`, 'info'); }} className="tap-expand shrink-0 rounded-md p-1 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><CopyIcon className="h-3.5 w-3.5" strokeWidth={1.9} /></button>
                </div>
                <p className="mt-1 text-fine text-slate-500">Created {key.created_at ? new Date(key.created_at).toLocaleDateString() : key.created ?? ''}</p>
              </li>
            )}</ul>
          )}
          <button type="button" onClick={()=>setKeyOpen(true)} className="mt-2.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Generate new key</button>
          {keySecret && <p className="mt-2 rounded bg-amber-50 px-2 py-1 font-mono text-fine text-amber-800 break-all">Last secret (copy now, shown once): {keySecret}</p>}
          <p className="mt-1 text-fine text-slate-500">Each key shows its scope, prefix and when it was last used</p>
        </Panel>

        <Panel title="Offline resilience" description="For vendors without steady connectivity.">
          <ul className="flex flex-col gap-2 text-micro leading-relaxed text-slate-600">{['Mobile entries queue locally and sync when a connection returns.','SMS and USSD entry keeps feature-phone vendors in the same pipeline.','Duplicate days across channels are reconciled, never double counted.','Every ingested row keeps its source channel for traceability.'].map(item=> <li key={item} className="flex items-start gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />{item}</li>)}</ul>
        </Panel>
      </div>

      <Modal open={addOpen} onClose={()=>setAddOpen(false)} title="Add channel" description="Register a new inbound source." footer={<><ModalButton tone="ghost" onClick={()=>setAddOpen(false)}>Cancel</ModalButton><ModalButton onClick={()=>void addChannel()} disabled={!draft.name.trim()}>Add channel</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="ch-name" label="Channel name"><input id="ch-name" type="text" value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} placeholder="e.g. Equity Bank till" className={inputClass} /></Field>
          <Field id="ch-type" label="Channel type"><select id="ch-type" value={draft.channel} onChange={e=>setDraft({...draft,channel:e.target.value})} className={inputClass}>{filters.filter(o=>o!=='All').map(o=> <option key={o}>{o}</option>)}</select></Field>
          <Field id="ch-detail" label="Description" hint="Shown under the channel name in this list."><input id="ch-detail" type="text" value={draft.detail} onChange={e=>setDraft({...draft,detail:e.target.value})} placeholder="Nightly transaction export" className={inputClass} /></Field>
        </div>
      </Modal>

      {/* Edit channel: name and description; the channel type is fixed at
          creation, so the dialog does not pretend otherwise. */}
      <FormModal
        open={!!editConnector}
        onClose={()=>setEditConnector(null)}
        title={`Edit ${editConnector?.name ?? 'channel'}`}
        description="Changes apply to the next sync from this channel."
        onSubmit={()=>void saveEdit()}
        saving={saving}
        submitLabel="Save changes"
        footerExtra={editConnector ? <ModalButton tone="danger" onClick={()=>{const id=editConnector.id; setEditConnector(null); void delConnector(id);}}>Delete</ModalButton> : undefined}>
        <Field id="edit-ch-name" label="Channel name"><input id="edit-ch-name" type="text" value={editDraft.name} onChange={e=>setEditDraft({...editDraft,name:e.target.value})} placeholder="e.g. Equity Bank till" className={inputClass} /></Field>
        <Field id="edit-ch-detail" label="Description" hint="Shown under the channel name in this list."><input id="edit-ch-detail" type="text" value={editDraft.detail} onChange={e=>setEditDraft({...editDraft,detail:e.target.value})} placeholder="Nightly transaction export" className={inputClass} /></Field>
        <Field id="edit-ch-type" label="Channel type" hint="Fixed when the channel is added. Create a new channel to change type."><input id="edit-ch-type" type="text" value={typeToChannel[editConnector?.type] ?? editConnector?.type ?? ''} disabled className={inputClass} /></Field>
      </FormModal>

      <Modal open={keyOpen} onClose={()=>setKeyOpen(false)} title="Generate API key" description="The secret is shown once and copied to your clipboard." footer={<><ModalButton tone="ghost" onClick={()=>setKeyOpen(false)}>Cancel</ModalButton><ModalButton onClick={()=>void generateKey()} disabled={!keyDraft.label.trim()}>Generate key</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="key-label" label="Label" hint="Name the system that will use it."><input id="key-label" type="text" value={keyDraft.label} onChange={e=>setKeyDraft({...keyDraft,label:e.target.value})} placeholder="Kitchen display service" className={inputClass} /></Field>
          <Field id="key-scope" label="What it can do" hint="Only grant what the partner system actually needs."><select id="key-scope" value={keyDraft.scope} onChange={e=>setKeyDraft({...keyDraft,scope:e.target.value})} className={inputClass}><option value="read:forecasts">Read forecasts only</option><option value="write:demand">Send demand data only</option><option value="read:analytics">Read analytics only</option><option value="admin:all">Full access (dangerous)</option></select></Field>
        </div>
      </Modal>
    </div>;
}
