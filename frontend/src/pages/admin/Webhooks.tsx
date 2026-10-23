import { useCallback, useEffect, useState } from 'react';
import { WebhookIcon, PlusIcon, SendIcon, TrashIcon, PencilIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import type { StatusTone } from '../../components/ui/StatusPill';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';
import { RecordList } from '../../components/ui/RecordList';
import { FilterBar } from '../../components/ui/FilterBar';
import { FormModal } from '../../components/ui/FormModal';
import { useListState } from '../../hooks/useListState';
import { plainStatus } from '../../lib/plainLanguage';

const statusTone:Record<string,StatusTone>={ active:'success', paused:'neutral', failing:'critical' };

/** One test delivery, as `POST /webhooks/{id}/test` reports it. */
type TestDelivery = {
  endpointId: string;
  event: string;
  attempt?: number;
  response_code?: number;
  delivered_at?: string;
  /** Already-rendered: the endpoint sends an object, a string is tolerated. */
  payload?: string;
  signature_header?: string;
};

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load webhooks',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function Webhooks(){
  const [webhooks,setWebhooks]=useState<any[]>([]);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [deliveries,setDeliveries]=useState<any[]>([]);
  const [events,setEvents]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [addOpen,setAddOpen]=useState(false);
  const [draft,setDraft]=useState<{url:string; events:string[]}>({url:'', events:['forecast.generated']});
  // The edit dialog: URL and subscriptions for an existing endpoint.
  const [editOpen,setEditOpen]=useState(false);
  const [editDraft,setEditDraft]=useState<{url:string; events:string[]}>({url:'', events:[]});
  const [saving,setSaving]=useState(false);
  const [tested,setTested]=useState<string|null>(null);
  const [testRun,setTestRun]=useState<TestDelivery|null>(null);
  const { toast } = useToast();
  const selected = webhooks.find(w=>w.id===selectedId);

  // Hook — before any early return: the toolbar list state must exist on every
  // render or React sees the hook count change when loading finishes.
  const endpointList = useListState<any>({
    key:'admin-webhook-endpoints',
    items: webhooks,
    pageSize: 50,
    searchFields:(w:any)=> [w.url, ...(w.events ?? [])].join(' '),
    filters:[{ key:'status', label:'Status', match:(w:any,v:string)=> String(w.status ?? 'active')===v,
      options:[{value:'',label:'Any status'},{value:'active',label:'Active'},{value:'paused',label:'Paused'},{value:'failing',label:'Failing'}] }],
  });

  const fetchAll = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const [whRes, evRes] = await Promise.all([
        api.get<{data:any[]}>('/api/v1/webhooks'),
        api.get<{data:any[]}>('/api/v1/webhooks/events').catch(()=>({data:[]} as any)),
      ]);
      const data = whRes.data ?? [];
      setWebhooks(data);
      if(data.length && !selectedId) setSelectedId(data[0].id);
      setEvents(evRes.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); }
  },[selectedId]);

  const fetchDeliveries = useCallback(async(id:string)=>{
    try{
      const res = await api.get<{data:any[]; deliveries?:any[]}>(`/api/v1/webhooks/${id}/deliveries`);
      setDeliveries(res.data ?? (res as any).deliveries ?? []);
    }catch{ setDeliveries([]); }
  },[]);

  useEffect(()=>{ void fetchAll(); },[fetchAll]);
  useEffect(()=>{ if(selectedId) void fetchDeliveries(selectedId); },[selectedId, fetchDeliveries]);

  const toggleEvent = (label:string)=> setDraft(cur=>({...cur, events: cur.events.includes(label)? cur.events.filter(x=>x!==label): [...cur.events, label]}));

  const addWebhook = async()=>{
    if(!draft.url.startsWith('https://') || draft.events.length===0) return;
    try{
      const res = await api.post<{id:string}>('/api/v1/webhooks',{url:draft.url.trim(), events:draft.events, status:'active'});
      toast('Endpoint added. Send a test event to verify it.');
      setSelectedId(res.id);
      setDraft({url:'', events:['forecast.generated']});
      setAddOpen(false);
      void fetchAll();
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };
  const openEdit = (endpoint:any)=>{
    setEditDraft({url:String(endpoint.url ?? ''), events:[...(endpoint.events ?? [])]});
    setEditOpen(true);
  };
  const saveEdit = async()=>{
    if(!selected || !editDraft.url.startsWith('https://') || editDraft.events.length===0) return;
    setSaving(true);
    try{
      await api.patch(`/api/v1/webhooks/${selected.id}`,{url:editDraft.url.trim(), events:editDraft.events});
      toast('Endpoint updated');
      setEditOpen(false);
      void fetchAll();
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
    finally{ setSaving(false); }
  };
  const remove = async(id:string)=>{
    try{
      await api.delete(`/api/v1/webhooks/${id}`);
      toast('Endpoint removed','info');
      // A test delivery about an endpoint that no longer exists describes nothing.
      setTestRun(current => current?.endpointId === id ? null : current);
      setSelectedId(null);
      void fetchAll();
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };
  const test = async(id:string)=>{
    try{
      // The endpoint answers with the event it sent, the response code, the
      // attempt and the header a receiver must verify. All of it used to be
      // dropped for a toast that expired after two seconds, leaving an
      // integrator with nothing to check their signature code against.
      const res = await api.post<any>(`/api/v1/webhooks/${id}/test`);
      const raw = res?.payload;
      setTestRun({
        endpointId: id,
        event: res?.event ?? 'test',
        attempt: res?.attempt,
        response_code: res?.response_code,
        delivered_at: res?.delivered_at,
        // `payload` is the sample object the endpoint was handed, not a string:
        // rendering it only when it was already a string showed nothing.
        payload:
          typeof raw === 'string' ? raw : raw != null ? JSON.stringify(raw, null, 2) : undefined,
        signature_header: res?.signature_header,
      });
      setTested(id);
      toast(`Test ${res?.event ?? 'event'} sent. Response ${res?.response_code ?? '—'}`);
      void fetchDeliveries(id);
    }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  if(loading) return <LoadingState label="Loading webhooks" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchAll()}} size="md" />;

  return (
    <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <Panel title="Endpoints" icon={WebhookIcon} description="Push forecasts, policy changes and alerts into your own systems." action={<button type="button" onClick={()=>setAddOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlusIcon className="h-3.5 w-3.5" strokeWidth={2.2} />Add endpoint</button>}>
          {webhooks.length===0 ? (
            <EmptyState variant="empty" size="md" title="No webhooks yet" description="Add an HTTPS endpoint to receive signed events." hints={["Payloads are HMAC-SHA256 signed","Retried 5 times with backoff"]} action={{label:'Add endpoint',onClick:()=>setAddOpen(true)}} />
          ) : (
            <>
            <FilterBar<any>
              search={endpointList.search}
              onSearch={endpointList.setSearch}
              searchPlaceholder="Search endpoints…"
              filters={[{ key:'status', label:'Status', match:(w:any,v:string)=> String(w.status ?? 'active')===v,
                options:[{value:'',label:'Any status'},{value:'active',label:'Active'},{value:'paused',label:'Paused'},{value:'failing',label:'Failing'}] }]}
              values={endpointList.filterValues}
              onFilterChange={endpointList.setFilter}
              onClear={endpointList.clearFilters}
              activeCount={endpointList.activeFilterCount}
              countLabel={endpointList.filtered !== endpointList.total ? `${endpointList.filtered} of ${endpointList.total} endpoints` : undefined} />
            <ul className="mt-2 flex flex-col gap-1.5">{endpointList.rows.map((endpoint)=>{
              const isSelected = endpoint.id===selectedId;
              // The row is a container, not a giant button: the delete control
              // used to be a `<span onClick>` nested inside the row button,
              // which no keyboard could reach.
              return (
                <li key={endpoint.id} className={['flex items-start gap-2 rounded-lg border px-3 py-2.5 transition-colors duration-150 ease-out', isSelected?'border-brand-500 bg-brand-50/60':'border-slate-200 hover:bg-slate-50'].join(' ')}>
                  <button type="button" aria-pressed={isSelected} onClick={()=>{setSelectedId(endpoint.id); setTested(null); setTestRun(null);}} className="min-w-0 flex-1 rounded text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                      <code className="min-w-0 truncate font-mono text-caption font-semibold text-slate-900">{endpoint.url}</code>
                      <StatusPill tone={statusTone[endpoint.status] ?? 'neutral'} dot>{plainStatus(endpoint.status)}</StatusPill>
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-slate-500"><span>{(endpoint.events??[]).join(', ')}</span><span className="ml-auto tabular-nums">{endpoint.created_at ? new Date(endpoint.created_at).toLocaleDateString() : ''}</span></span>
                  </button>
                  <button type="button" aria-label={`Delete endpoint ${endpoint.url}`} onClick={()=>void remove(endpoint.id)} className="tap-expand shrink-0 rounded p-1 text-slate-500 transition-colors duration-150 ease-out hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                    <TrashIcon className="h-3.5 w-3.5" />
                  </button>
                </li>);
            })}</ul>
            </>
          )}
        </Panel>

        <Panel title="Recent deliveries" description="Last 24 hours, including retries.">
          {deliveries.length===0 ? <EmptyState variant="empty" size="sm" title="No deliveries yet" description="Deliveries appear once events are emitted." hints={["Send a test event","Check endpoint is reachable"]} /> : (
            <RecordList<any>
              stateKey="admin-webhook-deliveries"
              caption="Recent webhook deliveries"
              minWidth="min-w-[460px]"
              items={deliveries}
              rowKey={(d:any)=>String(d.id ?? d.delivery_id ?? d.created_at ?? Math.random())}
              countNoun="deliveries"
              searchFields={(d:any)=> [d.event, d.event_type, d.url, d.endpoint].filter(Boolean).join(' ')}
              searchPlaceholder="Search deliveries…"
              filters={[{ key:'result', label:'Result', match:(d:any,v:string)=> v==='delivered' ? (d.http_status ?? d.code ?? 0) < 300 : (d.http_status ?? d.code ?? 0) >= 300,
                options:[{value:'',label:'Any result'},{value:'delivered',label:'Delivered'},{value:'failed',label:'Failed'}] }]}
              sortValue={(d:any,k:string)=> k==='response' ? Number(d.http_status ?? d.code ?? 0) : k==='attempt' ? Number(d.attempt ?? d.attempt_count ?? 1) : k==='time' ? String(d.created_at ?? d.time ?? '') : String(d[k] ?? '')}
              columns={[
                { key:'event', label:'Event', primary:true, sortable:true, render:(d:any)=> <code className="font-mono text-micro text-slate-800">{d.event ?? d.event_type}</code> },
                { key:'endpoint', label:'Endpoint', render:(d:any)=> <span className="block max-w-[220px] truncate" title={d.url ?? d.endpoint ?? selected?.url ?? ''}>{d.url ?? d.endpoint ?? selected?.url ?? ''}</span> },
                { key:'response', label:'Response', sortable:true, render:(d:any)=> <span className={['rounded px-1.5 py-0.5 text-micro font-semibold tabular-nums', (d.http_status ?? d.code ?? 0) <300 ? 'bg-brand-100 text-brand-800':'bg-red-100 text-red-700'].join(' ')}>{d.http_status ?? d.code ?? '—'}</span> },
                { key:'attempt', label:'Attempt', sortable:true, render:(d:any)=> d.attempt ?? d.attempt_count ?? 1 },
                { key:'time', label:'Time', sortable:true, render:(d:any)=> d.created_at ? new Date(d.created_at).toLocaleString() : d.time ?? '' },
              ]}
            />
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-3">
        {selected ? (
          <Panel title="Endpoint settings" description={selected.url}>
            <dl className="flex flex-col gap-2">{[['Status', selected.status],['Last delivery', selected.last_delivery ?? '—'],['Retry policy','5 attempts, exponential backoff'],['Signature','HMAC-SHA256 header']].map(([label,value])=> <div key={label} className="flex items-baseline justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0"><dt className="text-caption text-slate-500">{label}</dt><dd className="text-meta font-semibold capitalize text-slate-900">{value as string}</dd></div>)}
            </dl>
            <button type="button" onClick={()=>void test(selected.id)} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><SendIcon className="h-3.5 w-3.5" strokeWidth={2} />{tested===selected.id ? 'Test event sent' : 'Send test event'}</button>
            <button type="button" onClick={()=>openEdit(selected)} className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><PencilIcon className="h-3.5 w-3.5" strokeWidth={2} />Edit endpoint</button>
          </Panel>
        ) : <Panel title="No endpoint selected"><EmptyState variant="empty" size="sm" title="Select an endpoint" description="Pick an endpoint to view settings." /></Panel>}

        {testRun && testRun.endpointId === selectedId && (
          <Panel
            title={`Test delivery: ${testRun.event}`}
            icon={SendIcon}
            description="What the endpoint was sent, and how to verify it arrived from us."
            action={<button type="button" onClick={()=>setTestRun(null)} className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Dismiss</button>}
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={(testRun.response_code ?? 0) < 300 ? 'success' : 'critical'} dot>
                {`HTTP ${testRun.response_code ?? '—'}`}
              </StatusPill>
              {testRun.attempt != null && <span className="text-micro text-slate-500">attempt {testRun.attempt}</span>}
              {testRun.delivered_at && (
                <span className="text-micro text-slate-500">Sent {new Date(testRun.delivered_at).toLocaleString()}</span>
              )}
            </div>
            {testRun.signature_header && (
              <div className="mt-3">
                <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Verify the signature</p>
                <p className="text-micro leading-relaxed text-slate-600">
                  Every delivery carries the{' '}
                  <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-fine text-slate-700">{testRun.signature_header}</code>{' '}
                  header: an HMAC-SHA256 of the raw request body, signed with this endpoint's secret. Recompute it over the bytes you received before trusting the payload.
                </p>
              </div>
            )}
            {testRun.payload && (
              <div className="mt-3">
                <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Sample payload</p>
                <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 font-mono text-fine text-slate-700">{testRun.payload}</pre>
              </div>
            )}
          </Panel>
        )}

        <Panel title="Event catalog" description="What you can subscribe to.">
          {events.length===0 ? <EmptyState variant="empty" size="sm" title="No catalog" description="Event catalog will load when available." /> : (
            <ul className="flex flex-col gap-2">{events.map((ev:any)=>
            <li key={ev.id ?? ev.event ?? ev.label}>
                <code className="font-mono text-caption font-semibold text-brand-800">{ev.label ?? ev.event ?? ev.id}</code>
                <p className="mt-0.5 text-micro leading-relaxed text-slate-500">{ev.detail ?? ev.description ?? ''}</p>
              </li>
            )}</ul>
          )}
        </Panel>
      </div>

      <Modal open={addOpen} onClose={()=>setAddOpen(false)} title="Add endpoint" description="Deliveries are signed with an HMAC header and retried on failure." footer={<><ModalButton tone="ghost" onClick={()=>setAddOpen(false)}>Cancel</ModalButton><ModalButton disabled={!draft.url.startsWith('https://') || draft.events.length===0} onClick={()=>void addWebhook()}>Add endpoint</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="hook-url" label="Endpoint URL" hint="HTTPS only."><input id="hook-url" type="url" value={draft.url} onChange={e=>setDraft({...draft,url:e.target.value})} placeholder="https://ops.example.rw/hooks/demandiq" className={inputClass} /></Field>
          <fieldset><legend className="text-caption font-semibold text-slate-700">Events</legend>
            <div className="mt-1.5 flex flex-col gap-1.5">{(events.length?events:[{label:'forecast.generated',detail:'Forecast generated'},{label:'forecast.updated',detail:'Forecast updated'},{label:'alert.raised',detail:'Alert raised'},{label:'ingest.completed',detail:'Ingest completed'},{label:'model.promoted',detail:'Model promoted'}]).map((ev:any)=>{
              const label = ev.label ?? ev.event ?? ev.id;
              return (
                <label key={label} className="flex items-start gap-2 rounded-lg border border-slate-200 px-2.5 py-2">
                  <input type="checkbox" checked={draft.events.includes(label)} onChange={()=>toggleEvent(label)} className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-brand-600" />
                  <span className="min-w-0"><code className="block font-mono text-micro font-semibold text-brand-800">{label}</code><span className="block text-fine leading-relaxed text-slate-500">{ev.detail ?? ev.description ?? ''}</span></span>
                </label>
              );
            })}</div>
          </fieldset>
        </div>
      </Modal>

      {/* Edit endpoint: the URL and the subscriptions in one dialog, so an
          endpoint can be repointed or narrowed without delete-and-recreate. */}
      <FormModal
        open={editOpen}
        onClose={()=>setEditOpen(false)}
        title="Edit endpoint"
        description="Deliveries are signed with an HMAC header and retried on failure."
        onSubmit={()=>void saveEdit()}
        saving={saving}
        submitLabel="Save changes"
        footerExtra={selected ? <ModalButton tone="danger" onClick={()=>{setEditOpen(false); void remove(selected.id);}}>Delete</ModalButton> : undefined}>
        <Field id="edit-hook-url" label="Endpoint URL" hint="HTTPS only."><input id="edit-hook-url" type="url" value={editDraft.url} onChange={e=>setEditDraft({...editDraft,url:e.target.value})} placeholder="https://ops.example.rw/hooks/demandiq" className={inputClass} /></Field>
        <fieldset><legend className="text-caption font-semibold text-slate-700">Events</legend>
          <div className="mt-1.5 flex flex-col gap-1.5">{(events.length?events:[{label:'forecast.generated',detail:'Forecast generated'},{label:'forecast.updated',detail:'Forecast updated'},{label:'alert.raised',detail:'Alert raised'},{label:'ingest.completed',detail:'Ingest completed'},{label:'model.promoted',detail:'Model promoted'}]).map((ev:any)=>{
            const label = ev.label ?? ev.event ?? ev.id;
            return (
              <label key={label} className="flex items-start gap-2 rounded-lg border border-slate-200 px-2.5 py-2">
                <input type="checkbox" checked={editDraft.events.includes(label)} onChange={()=>setEditDraft(cur=>({...cur, events: cur.events.includes(label) ? cur.events.filter(x=>x!==label) : [...cur.events, label]}))} className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-brand-600" />
                <span className="min-w-0"><code className="block font-mono text-micro font-semibold text-brand-800">{label}</code><span className="block text-fine leading-relaxed text-slate-500">{ev.detail ?? ev.description ?? ''}</span></span>
              </label>
            );
          })}</div>
        </fieldset>
      </FormModal>
    </div>
  );
}
