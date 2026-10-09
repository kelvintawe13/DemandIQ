import { useCallback, useEffect, useState } from 'react';
import { CheckIcon, CheckCheckIcon, TriangleAlertIcon, InfoIcon, CircleAlertIcon, PlusIcon, PencilIcon, TrashIcon, SettingsIcon, ZapIcon } from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { Toggle } from '../components/ui/Toggle';
import { PageSkeleton } from '../components/ui/Skeleton';
import { StatusPill } from '../components/ui/StatusPill';
import { EmptyState } from '../components/ui/EmptyState';
import { TabBar } from '../components/ui/TabBar';
import { Modal, ModalButton } from '../components/ui/Modal';
import { Field, inputClass } from '../components/ui/Field';
import { PermissionNote } from '../components/ui/PermissionNote';
import { FilterBar } from '../components/ui/FilterBar';
import { ViewSwitcher } from '../components/ui/ViewSwitcher';
import { SortMenu } from '../components/ui/RecordList';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { useListState, type FilterDef } from '../hooks/useListState';
import { api, ApiException } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { notifyNavBadgesChanged } from '../hooks/useNavBadges';

interface AlertsProps { onNavigate:(id:string)=>void; }
const tabs = ['All','Unread','Critical','Resolved'] as const;
const severityIcon:Record<string,any> = { critical: CircleAlertIcon, warning: TriangleAlertIcon, info: InfoIcon };

/**
 * Plain wording for each threshold kind. The API stores machine keys
 * (`mae_gt`); a rule is described to the reader as the question it answers, and
 * the dropdown offers that same phrase instead of the token.
 */
const THRESHOLDS:Record<string,{question:string}> = {
  mae_gt: { question:'the average forecast misses by more than' },
  coverage_lt: { question:'the forecast range holds less often than' },
  drift_p_gt: { question:'demand patterns shift by more than' },
  stale_hours_gt: { question:'a source has been silent for more than' },
};

/** A rule threshold as a sentence an operator can read. */
function describeThreshold(threshold?: Record<string, unknown> | null): string {
  const entries = threshold ? Object.entries(threshold) : [];
  if (!entries.length) return 'no threshold set';
  return entries
    .map(([key, value]) => THRESHOLDS[key] ? `${THRESHOLDS[key].question} ${value}` : `${key.replace(/_/g,' ')} ${value}`)
    .join('; ');
}

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const,title:'Sign in required',desc:e.error.message};
    if(e.status===403) return {variant:'error' as const,title:'Admin access required',desc:e.error.message};
    return {variant:'error' as const,title:'Failed to load alerts',desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check your connection'};
  return {variant:'offline' as const,title:'Network error',desc:String(e)};
}

export function Alerts({ onNavigate }: AlertsProps) {
  const [items,setItems]=useState<any[]>([]);
  const [rules,setRules]=useState<any[]>([]);
  const [tab,setTab]=useState<(typeof tabs)[number]>('All');
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const [cursor,setCursor]=useState<string|null>(null);
  const [hasMore,setHasMore]=useState(false);
  const [detail,setDetail]=useState<any>(null);
  // Tab switches refetch; only the first load should blank the page.
  const [loaded,setLoaded]=useState(false);
  const [ruleModalOpen,setRuleModalOpen]=useState(false);
  const [editingRule,setEditingRule]=useState<any>(null);
  const [draftRule,setDraftRule]=useState<{name:string; thresholdKey:string; thresholdValue:string; enabled:boolean}>({name:'', thresholdKey:'mae_gt', thresholdValue:'20', enabled:true});
  const [ruleSaving,setRuleSaving]=useState(false);
  const { toast } = useToast();
  const { user } = useSession();
  // Rule configuration needs ``alerts:configure`` (owner/administrator). A
  // clerk can read the rules (useful context) but not change them.
  const canConfigure = (user.permissions ?? []).includes('alerts:configure');

  const fetchAll = useCallback(async (cur?:string|null)=>{
    setLoading(true); setError(null);
    try{
      const statusMap:Record<string,string> = { All:'', Unread:'unread', Critical:'critical', Resolved:'resolved' };
      const status = statusMap[tab] || undefined;
      const [aRes, rRes] = await Promise.all([
        api.get<{data:any[]; has_more?:boolean; next_cursor?:string|null}>('/api/v1/alerts', {...(status?{status}:{}), ...(cur?{cursor:cur}:{}), limit:20}),
        api.get<{data:any[]}>('/api/v1/alerts/rules'),
      ]);
      setItems(aRes.data ?? []);
      setHasMore(!!aRes.has_more);
      setCursor(aRes.next_cursor ?? null);
      setRules(rRes.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); setLoaded(true); }
  },[tab]);

  useEffect(()=>{ void fetchAll(null); },[fetchAll]);

  const unreadCount = items.filter(i=> !i.read_at && !i.resolved_at).length;

  // Inbox toolbar: search across what an alert says, a severity filter, and the
  // three ways to read the inbox (its cards, a compact list, a table). The
  // server pages the inbox by cursor; the Previous/Next below do that half.
  const alertFilters: FilterDef<any>[] = [
    { key:'severity', label:'Severity', match:(alert:any,v:string)=> String(alert.severity ?? 'info')===v,
      options:[{value:'',label:'Any severity'},{value:'critical',label:'Critical'},{value:'warning',label:'Warning'},{value:'info',label:'Info'}] },
  ];
  const severityRank:Record<string,number> = { critical:0, warning:1, info:2 };
  const list = useListState<any>({
    key:'data-alerts',
    items,
    filters: alertFilters,
    searchFields:(alert:any)=> [alert.title, alert.body, alert.detail, alert.rule_name].filter(Boolean).join(' '),
    sortValue:(alert:any,k:string)=> k==='created' ? String(alert.created_at ?? '') : k==='severity' ? (severityRank[String(alert.severity ?? 'info')] ?? 3) : String(alert[k] ?? ''),
    defaultSort:{key:'created',dir:'desc'},
    defaultView:'cards',
    pageSize: 50,
  });
  const visible = list.rows;

  const alertColumns: Column<any>[] = [
    { key:'title', label:'Alert', primary:true, sortable:true, render:(alert:any)=> (
      <span>
        <span className="block truncate text-meta font-semibold text-slate-900">{alert.title}</span>
        <span className="block truncate text-micro text-slate-500">{alert.body ?? alert.detail}</span>
      </span>
    ) },
    { key:'severity', label:'Severity', sortable:true, render:(alert:any)=> <StatusPill tone={alert.severity==='critical' ? 'critical' : alert.severity==='warning' ? 'warning' : 'info'} dot>{alert.severity}</StatusPill> },
    { key:'created', label:'When', sortable:true, render:(alert:any)=> alert.created_at ? new Date(alert.created_at).toLocaleString() : alert.time ?? '' },
    { key:'actions', label:'', cellClassName:'pr-0', render:(alert:any)=> (
      <span className="flex flex-wrap items-center justify-end gap-1.5">
        {!alert.resolved_at && <button type="button" onClick={()=>void resolve(alert.id)} className="rounded-lg bg-brand-700 px-2.5 py-1 text-micro font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Resolve</button>}
        <button type="button" onClick={()=>setDetail(alert)} className="rounded-lg border border-slate-300 px-2.5 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">View details</button>
      </span>
    ) },
  ];

  const markRead = async (id:string)=>{
    try{ await api.post(`/api/v1/alerts/${id}/read`); toast('Marked read'); notifyNavBadgesChanged(); void fetchAll(null); }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };
  const markAll = async ()=>{
    try{ await api.post('/api/v1/alerts/read-all'); toast('All marked read'); notifyNavBadgesChanged(); void fetchAll(null); }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };
  const resolve = async (id:string)=>{
    try{ await api.post(`/api/v1/alerts/${id}/resolve`); toast('Alert resolved'); notifyNavBadgesChanged(); void fetchAll(null); }
    catch(e){ if(e instanceof ApiException){ if(e.status===409) toast('Already resolved','error'); else toast(e.error.message,'error'); } }
  };
  const toggleRule = async (id:string, enabled:boolean)=>{
    try{ await api.patch(`/api/v1/alerts/rules/${id}`,{enabled:!enabled}); toast(`Rule ${!enabled?'enabled':'disabled'}`); void fetchAll(null); }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  const openCreateRule = () => {
    setEditingRule(null);
    setDraftRule({name:'', thresholdKey:'mae_gt', thresholdValue:'20', enabled:true});
    setRuleModalOpen(true);
  };
  const openEditRule = (rule:any) => {
    const thr = rule.threshold ?? {};
    const key = Object.keys(thr)[0] ?? 'mae_gt';
    const val = String(thr[key] ?? '');
    setEditingRule(rule);
    setDraftRule({name: rule.name ?? '', thresholdKey: key, thresholdValue: val, enabled: !!rule.enabled});
    setRuleModalOpen(true);
  };
  const handleSaveRule = async () => {
    if(!draftRule.name.trim()){ toast('Rule name is required','error'); return; }
    const num = parseFloat(draftRule.thresholdValue);
    if(Number.isNaN(num)){ toast('Threshold must be a number','error'); return; }
    setRuleSaving(true);
    try{
      const threshold = { [draftRule.thresholdKey]: num };
      if(editingRule){
        await api.patch(`/api/v1/alerts/rules/${editingRule.id}`, {name: draftRule.name.trim(), threshold, enabled: draftRule.enabled});
        toast('Rule updated');
      } else {
        await api.post('/api/v1/alerts/rules', {name: draftRule.name.trim(), threshold, enabled: draftRule.enabled});
        toast('Rule created. It will run automatically on the next check.');
      }
      setRuleModalOpen(false);
      setEditingRule(null);
      void fetchAll(null);
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); else toast('Failed to save rule','error'); }
    finally{ setRuleSaving(false); }
  };
  const handleDeleteRule = async (id:string) => {
    if(!confirm('Delete this alert rule? Alerts already created will remain.')) return;
    try{ await api.delete(`/api/v1/alerts/rules/${id}`); toast('Rule deleted'); void fetchAll(null); }
    catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  if(loading && !loaded) return <PageSkeleton label="Loading alerts" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} action={{label:'Retry',onClick:()=>void fetchAll(null)}} size="md" />;

  return (
    <>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <Panel title="Alert inbox" action={<button type="button" onClick={()=>void markAll()} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><CheckCheckIcon className="h-3.5 w-3.5" strokeWidth={2} />Mark all read</button>}>
          <TabBar
            tabs={tabs.map(option => ({
              id: option,
              label:
                option === 'Unread' && unreadCount > 0 ? (
                  <span className="inline-flex items-center gap-1.5">
                    {option}
                    <span className={['rounded-full px-1.5 text-fine font-semibold', tab === option ? 'bg-white/20' : 'bg-brand-100 text-brand-800'].join(' ')}>
                      {unreadCount}
                    </span>
                  </span>
                ) : (
                  option
                ),
            }))}
            value={tab}
            onChange={setTab}
            ariaLabel="Alert filters"
            className="mb-3"
          />

          <FilterBar<any>
            search={list.search}
            onSearch={list.setSearch}
            searchPlaceholder="Search alerts…"
            filters={alertFilters}
            values={list.filterValues}
            onFilterChange={list.setFilter}
            onClear={list.clearFilters}
            activeCount={list.activeFilterCount}
            countLabel={list.filtered !== list.total ? `${list.filtered} of ${list.total} alerts` : undefined}>
            {list.view !== 'table' && (
              <SortMenu<any>
                columns={[
                  { key:'created', label:'When', sortable:true },
                  { key:'severity', label:'Severity', sortable:true },
                  { key:'title', label:'Title', sortable:true },
                ]}
                sort={list.sort}
                onSort={list.setSortKey}
              />
            )}
            <ViewSwitcher value={list.view} onChange={list.setView} />
          </FilterBar>

          {visible.length===0 ? (
            <EmptyState variant="empty" size="md" title="All caught up" description={tab==='Resolved' ? 'No resolved alerts' : 'No alerts. Everything looks healthy.'} hints={["Alerts appear when thresholds are breached","Check back after next sync"]} />
          ) : (
            <>
              {list.view === 'table' ? (
                <ResponsiveTable<any>
                  columns={alertColumns}
                  rows={visible}
                  rowKey={(alert:any)=>String(alert.id)}
                  caption="Alert inbox"
                  minWidth="min-w-[560px]"
                />
              ) : list.view === 'list' ? (
                <ul className="flex flex-col divide-y divide-slate-100">
                  {visible.map((alert)=>{
                    const Icon = severityIcon[alert.severity] ?? InfoIcon;
                    const isResolved = !!alert.resolved_at;
                    return (
                      <li key={alert.id} className="flex items-center gap-2.5 py-2">
                        <Icon className={['h-4 w-4 shrink-0', alert.severity==='critical'?'text-red-500': alert.severity==='warning'?'text-amber-500':'text-sky-500'].join(' ')} strokeWidth={1.9} />
                        <span className="min-w-0 flex-1 truncate text-meta font-semibold text-slate-900">{alert.title}</span>
                        {isResolved && <StatusPill tone="success">Resolved</StatusPill>}
                        <span className="hidden shrink-0 text-fine text-slate-500 sm:block">{alert.created_at ? new Date(alert.created_at).toLocaleString() : alert.time ?? ''}</span>
                        <button type="button" onClick={()=>setDetail(alert)} className="shrink-0 rounded-lg border border-slate-300 px-2.5 py-1 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">View details</button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
              <ul className={list.view === 'cards' ? 'grid grid-cols-1 gap-2' : 'flex flex-col gap-2'}>
                {visible.map((alert)=>{
                  const Icon = severityIcon[alert.severity] ?? InfoIcon;
                  const isResolved = !!alert.resolved_at;
                  const isUnread = !alert.read_at && !isResolved;
                  return (
                    <li key={alert.id} className={['rounded-xl border px-3.5 py-3 transition-colors duration-150 ease-out', isUnread?'border-brand-200 bg-brand-50/40':'border-slate-200 bg-white'].join(' ')}>
                      <div className="flex items-start gap-2.5">
                        <Icon className={['mt-0.5 h-[18px] w-[18px] shrink-0', alert.severity==='critical'?'text-red-500': alert.severity==='warning'?'text-amber-500':'text-sky-500'].join(' ')} strokeWidth={1.9} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <p className="text-body-sm font-semibold text-slate-900">{alert.title}</p>
                            {isUnread && <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />}
                            {isResolved && <StatusPill tone="success">Resolved</StatusPill>}
                          </div>
                          <p className="mt-1 text-caption leading-relaxed text-slate-600">{alert.body ?? alert.detail}</p>
                          <p className="mt-1.5 text-fine text-slate-500">{alert.severity} · {alert.created_at ? new Date(alert.created_at).toLocaleString() : alert.time ?? ''}</p>
                        </div>
                      </div>
                      <div className="mt-2.5 flex flex-wrap gap-2 pl-7">
                          {!isResolved && <button type="button" onClick={()=>void resolve(alert.id)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><CheckIcon className="h-3.5 w-3.5" strokeWidth={2.25} />Resolve</button>}
                          {!isResolved && isUnread && <button type="button" onClick={()=>void markRead(alert.id)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Mark read</button>}
                          <button type="button" onClick={()=>setDetail(alert)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">View details</button>
                          <button type="button" onClick={()=>onNavigate('forecast')} className="rounded-lg px-2 py-1.5 text-caption font-semibold text-brand-700 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Investigate</button>
                        </div>
                    </li>);
                })}
              </ul>
              )}
              {(hasMore || cursor) && <div className="mt-3 flex gap-2"><button type="button" onClick={()=>void fetchAll(null)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 hover:bg-slate-50">Previous</button>{hasMore && <button type="button" onClick={()=>void fetchAll(cursor)} className="rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white hover:bg-brand-800">Next</button>}</div>}
            </>
          )}
        </Panel>

        <Panel title="Alert automation" description="Rules run automatically with no manual trigger. Checks run after each ingestion, forecast and nightly calibration." icon={ZapIcon}>
          <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-caption font-semibold text-sky-800"><SettingsIcon className="h-3.5 w-3.5" />How it works</p>
            <ul className="mt-1 list-disc pl-5 text-micro leading-relaxed text-sky-700">
              <li><span className="font-semibold">Big forecast miss</span>: the forecast is off by more than you allow</li>
              <li><span className="font-semibold">Range not holding</span>: actual demand lands outside the range too often</li>
              <li><span className="font-semibold">Demand pattern change</span>: what customers buy has drifted from the training data</li>
              <li><span className="font-semibold">Quiet source</span>: a connected channel has stopped sending data</li>
            </ul>
            <p className="mt-1 text-fine text-sky-600">Every enabled rule is checked automatically after each import, forecast and nightly run; you only set the limits.</p>
          </div>

          <div className="mt-3 flex items-center justify-between gap-3">
            <h3 className="text-body-xs font-semibold text-slate-900">Alert rules</h3>
            {canConfigure && <button type="button" onClick={openCreateRule} className="inline-flex items-center gap-1 rounded-md bg-brand-700 px-2.5 py-1.5 text-micro font-semibold text-white hover:bg-brand-800"><PlusIcon className="h-3 w-3" />New rule</button>}
          </div>

          {rules.length===0 ? (
            <div className="mt-2"><EmptyState variant="empty" size="sm" title="No rules" description="No alert rules configured. Defaults will be seeded on next load." /></div>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {rules.map((rule)=>{
                const thrText = describeThreshold(rule.threshold);
                return (
                <li key={rule.id} className="rounded-xl border border-slate-200 px-3.5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-body-xs font-semibold text-slate-900">{rule.name ?? rule.label} <span className="rounded bg-slate-100 px-1.5 py-0.5 text-fine font-medium text-slate-600">{thrText}</span></p>
                      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-fine text-slate-500">
                        <span>Fires when {thrText}</span>
                        <span>· {rule.enabled ? 'runs automatically' : 'paused'}</span>
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <Toggle checked={!!rule.enabled} onChange={()=>void toggleRule(rule.id, rule.enabled)} label={rule.name ?? rule.label} disabled={!canConfigure} title={canConfigure ? undefined : 'Your seat cannot change alert rules'} />
                      <StatusPill tone={rule.enabled ? 'success' : 'neutral'}>{rule.enabled ? 'On' : 'Off'}</StatusPill>
                    </div>
                  </div>
                  {canConfigure && (
                    <div className="mt-2 flex gap-1.5">
                      <button type="button" onClick={()=>openEditRule(rule)} className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-fine font-medium text-slate-700 hover:bg-slate-50"><PencilIcon className="h-3 w-3" />Edit threshold</button>
                      <button type="button" onClick={()=>void handleDeleteRule(rule.id)} className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-fine font-medium text-red-600 hover:bg-red-50"><TrashIcon className="h-3 w-3" />Delete</button>
                    </div>
                  )}
                </li>
                );
              })}
            </ul>
          )}
          {!canConfigure && <PermissionNote className="mt-3">
              Your seat can see which rules are active but not change them. Alert-rule configuration belongs to owners and administrators.
            </PermissionNote>}
          {canConfigure && <p className="mt-2 text-fine text-slate-500">Tip: thresholds are plain numbers. Forecast error is measured in customers, so <code className="rounded bg-slate-100 px-1">20</code> means "alert me when the forecast misses by more than 20 customers"; coverage is a share, so <code className="rounded bg-slate-100 px-1">0.85</code> means "alert me when the range holds less than 85% of the time".</p>}
        </Panel>
      </div>

      <Modal
        open={ruleModalOpen}
        onClose={()=>{setRuleModalOpen(false); setEditingRule(null)}}
        title={editingRule ? `Edit ${editingRule.name}` : 'New alert rule'}
        description={editingRule ? 'Update the threshold. It takes effect on the next automatic check.' : 'Create a rule that runs automatically after each relevant job.'}
        size="lg"
        footer={<><ModalButton tone="ghost" onClick={()=>{setRuleModalOpen(false); setEditingRule(null)}}>Cancel</ModalButton><ModalButton onClick={handleSaveRule} disabled={ruleSaving || !draftRule.name.trim()}>{ruleSaving ? 'Saving…' : editingRule ? 'Save changes' : 'Create rule'}</ModalButton></>}
      >
        <div className="flex flex-col gap-4">
          <Field id="rule-name" label="Rule name *"><input id="rule-name" value={draftRule.name} onChange={e=>setDraftRule({...draftRule, name:e.target.value})} placeholder="e.g. high_error" className={inputClass} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="rule-key" label="Alert me when…"><select id="rule-key" value={draftRule.thresholdKey} onChange={e=>setDraftRule({...draftRule, thresholdKey:e.target.value})} className={inputClass}><option value="mae_gt">Forecast error is too high</option><option value="coverage_lt">Forecast range holds too rarely</option><option value="drift_p_gt">Demand patterns have shifted</option><option value="stale_hours_gt">A source goes quiet</option><option value="custom">Other (advanced)</option></select></Field>
            <Field id="rule-value" label="Limit *" hint={THRESHOLDS[draftRule.thresholdKey] ? `Fires when ${THRESHOLDS[draftRule.thresholdKey].question} this number.` : 'The number the rule compares against.'}><input id="rule-value" type="number" step="0.01" value={draftRule.thresholdValue} onChange={e=>setDraftRule({...draftRule, thresholdValue:e.target.value})} placeholder="20" className={inputClass} /></Field>
          </div>
          <label className="flex items-center gap-2 text-body-xs text-slate-700"><input type="checkbox" checked={draftRule.enabled} onChange={e=>setDraftRule({...draftRule, enabled:e.target.checked})} className="h-4 w-4 rounded border-slate-300 accent-brand-600" />Enabled (runs automatically)</label>
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-micro leading-relaxed text-amber-800"><span className="font-semibold">This rule will alert you when</span> {THRESHOLDS[draftRule.thresholdKey]?.question ?? draftRule.thresholdKey.replace(/_/g,' ')} <span className="font-semibold">{draftRule.thresholdValue || '—'}</span>. Alerts are created automatically, with no manual check needed.</p>
        </div>
      </Modal>

      <Modal
        open={Boolean(detail)}
        onClose={()=>setDetail(null)}
        title={detail?.title ?? 'Alert'}
        description={detail ? `${detail.severity ?? 'info'} · ${detail.created_at ? new Date(detail.created_at).toLocaleString() : ''}` : undefined}>
        {detail && <div className="flex flex-col gap-4 text-meta text-slate-700">
            {detail.body || detail.detail ? <p className="leading-relaxed">{detail.body ?? detail.detail}</p> : null}
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {([
                ['Severity', detail.severity ?? 'info'],
                ['Status', detail.resolved_at ? 'Resolved' : detail.read_at ? 'Read' : 'Unread'],
                ['Detected', detail.created_at ? new Date(detail.created_at).toLocaleString() : '—'],
                ['Read at', detail.read_at ? new Date(detail.read_at).toLocaleString() : 'Not yet'],
                ['Resolved at', detail.resolved_at ? new Date(detail.resolved_at).toLocaleString() : 'Not yet'],
                ['Rule', detail.rule_name ?? detail.rule_id ?? '—'],
              ] as [string,string][]).map(([label, value])=> <div key={label}>
                    <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                    <dd className="text-slate-800">{value}</dd>
                  </div>)}
            </dl>
            {detail.location_id && <div>
                <p className="text-fine font-semibold uppercase tracking-wide text-slate-500">Location</p>
                <p className="font-mono text-micro text-slate-700">{detail.location_id}</p>
              </div>}
            {detail.payload && <div>
                <p className="mb-1 text-fine font-semibold uppercase tracking-wide text-slate-500">Evidence</p>
                <pre className="overflow-x-auto rounded-lg bg-slate-50 p-2.5 text-fine text-slate-700">{JSON.stringify(detail.payload, null, 2)}</pre>
              </div>}
            <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-3">
              {!detail.resolved_at && <button type="button" onClick={async()=>{ await resolve(detail.id); setDetail(null); }} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3.5 py-2 text-meta font-semibold text-white transition-colors hover:bg-brand-800"><CheckIcon className="h-3.5 w-3.5" strokeWidth={2.25} />Resolve</button>}
              <button type="button" onClick={()=>{ setDetail(null); onNavigate('forecast'); }} className="rounded-lg border border-slate-300 px-3.5 py-2 text-meta font-semibold text-slate-700 transition-colors hover:bg-slate-50">Investigate in forecast</button>
            </div>
          </div>}
      </Modal>
    </>
  );
}
