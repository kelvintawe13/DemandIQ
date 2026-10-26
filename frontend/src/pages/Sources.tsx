import { useCallback, useEffect, useMemo, useState } from 'react';
import { PlusIcon, RefreshCwIcon, SettingsIcon, CreditCardIcon, SmartphoneIcon, FileSpreadsheetIcon, ImageIcon, KeyboardIcon } from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { PageSkeleton } from '../components/ui/Skeleton';
import { StatusPill } from '../components/ui/StatusPill';
import { ConnectSourcesArt } from '../components/illustrations/PageArt';
import { Modal, ModalButton } from '../components/ui/Modal';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { FilterBar } from '../components/ui/FilterBar';
import { ViewSwitcher } from '../components/ui/ViewSwitcher';
import { SortMenu } from '../components/ui/RecordList';
import { useListState, type FilterDef } from '../hooks/useListState';
import { PermissionNote } from '../components/ui/PermissionNote';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { EmptyState } from '../components/ui/EmptyState';
import { api, ApiException } from '../api/client';
import { fetchSchedule, jobLabel, nextRunLabel, runStatusTone } from '../lib/schedule';
import type { ScheduleRow } from '../lib/schedule';
import { plainStatus } from '../lib/plainLanguage';
import {
  SOURCE_STATUS_TONE,
  isSyncable,
  kindLabel,
  kindOf,
  stateOf,
  syncBlockedReason,
} from '../lib/sourceChannels';

interface SourcesProps { onNavigate: (id:string)=>void; }

/** The jobs that move source data: pull the channels, then build features. */
const INGEST_JOBS = ['connector_sync','feature_materialisation'];

/**
 * A channel's own last pull, in one line.
 *
 * Counts come from `GET /sources` (`last_pull`) rather than from the
 * workspace-wide run list: `pipeline_runs` records no connector id, so a
 * per-channel row there is impossible. `null` counts mean no pull has run yet;
 * a `detail` without counts is the reason none ran (a channel with no pull
 * endpoint configured).
 */
function lastPullLabel(source: any): string {
  const lp = source?.last_pull;
  if (!lp) return 'No pull yet';
  if (lp.committed != null) {
    const skipped = lp.skipped ? ` · ${lp.skipped} skipped` : '';
    return `${lp.committed} committed${skipped}`;
  }
  return lp.detail ? String(lp.detail) : 'No pull yet';
}

const kindIcon: Record<string, any> = {
  POS: CreditCardIcon,
  'Mobile Money': SmartphoneIcon,
  Spreadsheet: FileSpreadsheetIcon,
  'Image OCR': ImageIcon,
  Manual: KeyboardIcon,
  pos: CreditCardIcon,
  momo: SmartphoneIcon,
  file: FileSpreadsheetIcon,
  ocr: ImageIcon,
  api: KeyboardIcon,
  sms: SmartphoneIcon,
  manual: KeyboardIcon,
};

// Naming and colour live in `lib/sourceChannels` so this page and the ingestion
// page's POS / Mobile Money tab cannot drift into two vocabularies for one set
// of rows.
const statusTone = SOURCE_STATUS_TONE;

function errMeta(e:unknown){
  if(e instanceof ApiException){
    if(e.status===401) return {variant:'error' as const, title:'Sign in required', desc:e.error.message, hints:['Sign in to view sources']};
    if(e.status===403) return {variant:'error' as const, title:'Admin access required', desc:e.error.message};
    return {variant:'error' as const, title:'Failed to load sources', desc:e.error.message + (e.reference?` (ref: ${e.reference})`:'')};
  }
  if(e instanceof TypeError) return {variant:'offline' as const, title:'You are offline', desc:'Check your connection', hints:['Verify VITE_API_URL','Check network']};
  return {variant:'offline' as const, title:'Network error', desc:String(e)};
}

export function Sources({ onNavigate }: SourcesProps) {
  const [syncingId,setSyncingId]=useState<string>();
  const [detail,setDetail]=useState<any>(null);
  // Keep the list on screen while a refetch is in flight (sync, filter).
  const [loaded,setLoaded]=useState(false);
  const [sources,setSources]=useState<any[]>([]);
  const [history,setHistory]=useState<any[]>([]);
  // null when the caller may not read pipeline info (e.g. a data clerk) — the
  // cadence panel is then hidden rather than filled with a guessed schedule.
  const [schedule,setSchedule]=useState<ScheduleRow[]|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ReturnType<typeof errMeta>|null>(null);
  const { toast } = useToast();
  const { user } = useSession();
  // `GET /pipeline/schedule` sits behind PIPELINE_READ (administrator+owner), so
  // a clerk's page must not ask for it at all — `fetchSchedule` would swallow
  // the 403 and the panel is hidden either way, but the console should not show
  // a rejected request the page knew it could not make.
  const canReadPipeline = (user.permissions ?? []).includes('pipeline:read');

  const fetchAll = useCallback(async()=>{
    setLoading(true); setError(null);
    try{
      const [sRes, hRes, sched] = await Promise.all([
        api.get<{data:any[]}>('/api/v1/sources'),
        api.get<{data:any[]}>('/api/v1/sources/sync-history', {limit:20}),
        canReadPipeline ? fetchSchedule() : Promise.resolve(null),
      ]);
      setSources(sRes.data ?? []);
      setHistory(hRes.data ?? []);
      setSchedule(sched);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); setLoaded(true); }
  },[canReadPipeline]);

  useEffect(()=>{ void fetchAll(); },[fetchAll]);

  // Toolbar: search across what a source is called, plus status and channel
  // filters — the same "Connected / Needs attention" split the segmented
  // control used to make, now as chips alongside a kind filter and the views.
  const sourceFilters: FilterDef<any>[] = [
    { key:'status', label:'Status',
      match:(s:any,v:string)=> v==='connected'
        ? ['connected','syncing','active','healthy'].includes(stateOf(s).toLowerCase())
        : ['error','inactive','failing','degraded','unknown'].includes(stateOf(s).toLowerCase()),
      options:[{value:'',label:'Any status'},{value:'connected',label:'Connected'},{value:'attention',label:'Needs attention'}] },
    { key:'kind', label:'Channel', match:(s:any,v:string)=> kindLabel(kindOf(s))===v,
      options:[{value:'',label:'All channels'}, ...[...new Set(sources.map((s:any)=>kindLabel(kindOf(s))))].sort().map((k)=>({value:k,label:k}))] },
  ];
  const list = useListState<any>({
    key:'data-sources',
    items: sources,
    filters: sourceFilters,
    searchFields:(s:any)=> [s.name, kindLabel(kindOf(s)), s.freshness, s.detail].filter(Boolean).join(' '),
    sortValue:(s:any,k:string)=> k==='source' ? String(s.name ?? '') : k==='sync' ? String(s.last_sync_at ?? '') : k==='volume' ? Number(s.volume ?? s.rows ?? -1) : k==='lastpull' ? Number(s.last_pull?.committed ?? -1) : String(s[k] ?? ''),
    defaultSort:{key:'source',dir:'asc'},
  });
  const visibleSources = list.rows;

  const sourceColumns: Column<any>[] = [
    // "Channel", not "Source": the sync-history table's first column is the
    // one that may not be called Source (it lists jobs), and one screen must not
    // carry two conflicting names for the same idea.
    { key:'source', label:'Channel', primary:true, sortable:true, render:(s:any)=>{
      const Icon = (kindIcon[kindLabel(kindOf(s))] ?? kindIcon[kindOf(s)] ?? SettingsIcon);
      return <span className="flex items-center gap-2.5"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50"><Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.8} /></span><span className="min-w-0"><span className="block truncate text-body-sm font-semibold text-slate-900">{s.name}</span><span className="block truncate text-micro text-slate-500">{kindLabel(kindOf(s))} · {s.freshness ?? s.detail ?? ''}</span></span></span>;
    } },
    { key:'sync', label:'Last sync', sortable:true, render:(s:any)=> <span className="text-meta font-medium text-slate-700">{s.last_sync_at ? new Date(s.last_sync_at).toLocaleString() : '—'}</span> },
    { key:'volume', label:'Volume', sortable:true, render:(s:any)=> <span className="text-meta font-medium text-slate-700">{s.volume ?? s.rows ?? '—'}</span> },
    { key:'lastpull', label:'Last pull', sortable:true, render:(s:any)=> <span className="text-meta font-medium text-slate-700" title={s.last_pull?.detail ?? undefined}>{lastPullLabel(s)}</span> },
    { key:'status', label:'Status', render:(s:any)=> <StatusPill tone={statusTone[stateOf(s)] ?? 'neutral'} dot>{stateOf(s)}</StatusPill> },
    { key:'actions', label:'Actions', cellClassName:'pr-0', render:(s:any)=> (
      <span className="flex shrink-0 items-center justify-end gap-1">
        {/* Only an active channel is syncable: the API answers 409 not_active
            for anything else, so the button is disabled with the reason rather
            than letting the request fail. */}
        <button type="button" aria-label={`Sync ${s.name}`} disabled={!isSyncable(s)} title={syncBlockedReason(s)} onClick={()=>void sync(s.id, s.name)} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:text-slate-500 disabled:hover:bg-transparent"><RefreshCwIcon className={['h-4 w-4', syncingId===s.id?'animate-spin text-brand-600':''].join(' ')} strokeWidth={1.9} /></button>
        <button type="button" aria-label={`View details for ${s.name}`} title="View details" onClick={()=>openDetail(s)} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><SettingsIcon className="h-4 w-4" strokeWidth={1.9} /></button>
      </span>
    ) },
  ];

  const sync = async (id:string,name:string)=>{
    setSyncingId(id);
    try{
      const res = await api.post<{job_id:string}>(`/api/v1/sources/${id}/sync`);
      toast(`${name} sync queued: ${res.job_id}`);
      void fetchAll();
    }catch(e){
      if(e instanceof ApiException) toast(e.error.message,'error');
      else if(e instanceof TypeError) toast('You are offline','error');
      else toast(String(e),'error');      } finally{ setTimeout(()=>setSyncingId(undefined),800); }
  };

  /**
   * Open the source's detail view.
   *
   * Runs are *not* filtered by source here: ``connector_sync`` sweeps every
   * active connector in the workspace in one run and ``pipeline_runs`` records
   * no connector id, so a per-source filter could only ever match on parsing
   * the free-text ``detail``. The modal lists the workspace's runs and shows
   * this source's own freshness from ``last_sync_at`` instead.
   */
  const openDetail = (source:any)=> setDetail(source);

  const ingestSchedule = useMemo(
    ()=> (schedule ?? []).filter(job=> INGEST_JOBS.includes(job.job_type)),
    [schedule],
  );

  if(loading && !loaded) return <PageSkeleton label="Loading sources" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} hints={error.hints} action={{label:'Retry',onClick:()=>void fetchAll()}} size="md" />;

  const hasSources = sources.length>0;

  return (
    <>
      <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="Connected sources" description="Status, freshness and volume per channel." action={<button type="button" onClick={()=>onNavigate('ingestion')} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 text-meta font-semibold text-white sm:py-2 transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlusIcon className="h-4 w-4" strokeWidth={2.25} />Add source</button>}>
            <FilterBar<any>
              search={list.search}
              onSearch={list.setSearch}
              searchPlaceholder="Search sources…"
              filters={sourceFilters}
              values={list.filterValues}
              onFilterChange={list.setFilter}
              onClear={list.clearFilters}
              activeCount={list.activeFilterCount}
              countLabel={list.filtered !== list.total ? `${list.filtered} of ${list.total} sources` : undefined}>
              {list.view !== 'table' && (
                <SortMenu<any>
                  columns={[
                    { key:'source', label:'Name', sortable:true },
                    { key:'sync', label:'Last sync', sortable:true },
                    { key:'volume', label:'Volume', sortable:true },
                  ]}
                  sort={list.sort}
                  onSort={list.setSortKey}
                />
              )}
              <ViewSwitcher value={list.view} onChange={list.setView} />
            </FilterBar>

            {!hasSources ? (
              <EmptyState variant="empty" size="md" title="No sources connected" description="Connect a channel to start ingesting demand." hints={["Connect POS","Use CSV upload","Try manual entry"]} action={{label:'Go to Connectors',onClick:()=>onNavigate('admin-connectors')}} secondaryAction={{label:'Ingest',onClick:()=>onNavigate('ingestion')}} />
            ) : visibleSources.length===0 ? (
              <EmptyState variant="search" size="sm" title="No matches" description="No sources match this search or filter." hints={["Try a different filter","Clear search"]} action={{label:'Clear search', onClick:()=>list.clearFilters()}} />
            ) : list.view === 'table' ? (
              <ResponsiveTable<any>
                columns={sourceColumns}
                rows={visibleSources}
                rowKey={(s:any)=>String(s.id)}
                caption="Connected sources"
                minWidth="min-w-[680px]"
              />
            ) : list.view === 'list' ? (
              <ul className="flex flex-col divide-y divide-slate-100">
                {visibleSources.map((source)=>{
                  const Icon = (kindIcon[kindLabel(kindOf(source))] ?? kindIcon[kindOf(source)] ?? SettingsIcon);
                  const st = stateOf(source);
                  return (
                    <li key={source.id} className="flex items-center gap-2.5 py-2 sm:py-1.5">
                      <Icon className="h-4 w-4 shrink-0 text-brand-600" strokeWidth={1.8} />
                      <span className="min-w-0 flex-1 truncate text-meta font-semibold text-slate-900">{source.name}</span>
                      <StatusPill tone={statusTone[st] ?? 'neutral'} dot>{st}</StatusPill>
                      <span className="hidden shrink-0 text-fine text-slate-500 sm:block">{source.last_sync_at ? new Date(source.last_sync_at).toLocaleString() : '—'}</span>
                      {/* An import channel has nothing to pull, so the control is disabled
                          here as it is in the table — the reason is on the button. */}
                      <button type="button" aria-label={`Sync ${source.name}`} disabled={!isSyncable(source)} title={syncBlockedReason(source)} onClick={()=>void sync(source.id, source.name)} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:text-slate-500 disabled:hover:bg-transparent"><RefreshCwIcon className={['h-4 w-4', syncingId===source.id?'animate-spin text-brand-600':''].join(' ')} strokeWidth={1.9} /></button>
                      <button type="button" aria-label={`View details for ${source.name}`} title="View details" onClick={()=>openDetail(source)} className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><SettingsIcon className="h-4 w-4" strokeWidth={1.9} /></button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <ul className="grid grid-cols-1 gap-2">
                {visibleSources.map((source)=>{
                  const rawKind = source.type ?? source.kind ?? 'API';
                  const key = rawKind === 'momo' ? 'Mobile Money' : rawKind === 'pos' ? 'POS' : rawKind === 'file' ? 'Spreadsheet' : rawKind === 'ocr' ? 'Image OCR' : rawKind;
                  const Icon = (kindIcon[key] ?? kindIcon[rawKind] ?? SettingsIcon);
                  const st = source.status ?? source.health ?? 'unknown';
                  return (
                    <li key={source.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-slate-200 px-2.5 py-2.5">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50"><Icon className="h-[18px] w-[18px] text-brand-600" strokeWidth={1.8} /></span>
                      <div className="min-w-[160px] flex-1">
                        <p className="truncate text-body-sm font-semibold text-slate-900">{source.name}</p>
                        {/* Show the humanised channel name (`momo` -> `Mobile Money`)
                            rather than the raw API token, which read as gibberish. */}
                        <p className="truncate text-micro text-slate-500">{key} · {source.freshness ?? source.detail ?? ''}</p>
                      </div>
                      <div className="hidden w-[110px] shrink-0 sm:block"><p className="text-fine text-slate-500">Last sync</p><p className="text-meta font-medium text-slate-700">{source.last_sync_at ? new Date(source.last_sync_at).toLocaleString() : '—'}</p></div>
                      <div className="hidden w-[96px] shrink-0 md:block"><p className="text-fine text-slate-500">Volume</p><p className="text-meta font-medium text-slate-700">{source.volume ?? source.rows ?? '—'}</p></div>
                      <div className="hidden w-[150px] shrink-0 md:block"><p className="text-fine text-slate-500">Last pull</p><p className="truncate text-meta font-medium text-slate-700" title={source.last_pull?.detail ?? undefined}>{lastPullLabel(source)}</p></div>
                      <StatusPill tone={statusTone[st] ?? 'neutral'} dot>{st}</StatusPill>
                      <div className="flex shrink-0 items-center gap-1">
                        <button type="button" aria-label={`Sync ${source.name}`} disabled={!isSyncable(source)} title={syncBlockedReason(source)} onClick={()=>void sync(source.id, source.name)} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:text-slate-500 disabled:hover:bg-transparent"><RefreshCwIcon className={['h-4 w-4', syncingId===source.id?'animate-spin text-brand-600':''].join(' ')} strokeWidth={1.9} /></button>
                        <button type="button" aria-label={`View details for ${source.name}`} title="View details" onClick={()=>openDetail(source)} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><SettingsIcon className="h-4 w-4" strokeWidth={1.9} /></button>
                      </div>
                    </li>);
                })}
              </ul>
            )}
            {hasSources && <p className="mt-2 text-fine text-slate-500">{sources.length} connected {sources.length === 1 ? 'source' : 'sources'} · each shows the last time it sent data</p>}
          </Panel>

          <Panel title="Sync history" description="Recent ingestion runs.">
            {history.length===0 ? (
              <EmptyState variant="empty" size="sm" title="No sync history" description="Connector syncs appear here. Uploads and manual entries are listed above as channels instead." hints={["Connect a POS or mobile money provider","Import a file to fill a channel"]} action={{label:'Import demand data', onClick:()=>onNavigate('ingestion')}} />
            ) : (
              <ResponsiveTable<any>
                caption="Sync history"
                minWidth="min-w-[560px]"
                rows={history}
                rowKey={(event:any)=>String(event.id)}
                columns={[
                  { key:'job', label:'Job', primary:true, render:(event:any)=> <span className="flex flex-col"><span className="font-medium text-slate-900">{jobLabel(String(event.job_type ?? 'run'))}</span>{event.detail && <span className="truncate text-micro text-slate-500">{event.detail}</span>}</span> },
                  { key:'result', label:'Result', render:(event:any)=> <StatusPill tone={runStatusTone(event.status ?? event.result)}>{plainStatus(event.status ?? event.result)}</StatusPill> },
                  { key:'rows', label:'Rows', render:(event:any)=> event.rows_processed ?? event.rows ?? '—' },
                  { key:'duration', label:'Duration', cardHidden:true, render:(event:any)=> event.duration_ms != null ? `${(event.duration_ms/1000).toFixed(1)}s` : '—' },
                  { key:'when', label:'When', render:(event:any)=> event.started_at ? new Date(event.started_at).toLocaleString() : event.time ?? '' },
                ]}
              />
            )}
          </Panel>

          {schedule !== null && ingestSchedule.length > 0 && (
            <Panel title="Ingestion cadence" description="When each ingestion job last ran and when it runs next.">
              <ul className="flex flex-col gap-2">{ingestSchedule.map((job)=>{
                const last = job.last_run;
                return (
                  <li key={job.job_type} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-slate-200 px-2.5 py-2.5">
                    <div className="min-w-[150px] flex-1">
                      <p className="truncate text-body-xs font-semibold text-slate-900">{jobLabel(job.job_type)}</p>
                      <p className="truncate text-micro text-slate-500">{job.cadence}</p>
                    </div>
                    <div className="w-[112px] shrink-0">
                      <p className="text-fine text-slate-500">Last run</p>
                      {last ? <StatusPill tone={runStatusTone(last.status)} dot>{plainStatus(last.status)}</StatusPill> : <p className="text-caption text-slate-500">Never</p>}
                    </div>
                    <div className="hidden w-[96px] shrink-0 sm:block">
                      <p className="text-fine text-slate-500">Next run</p>
                      <p className="text-caption text-slate-700">{nextRunLabel(job.next_run)}</p>
                    </div>
                  </li>
                );
              })}</ul>
              <p className="mt-2 text-fine text-slate-500">A sync sweeps every active connector in the workspace, so the run history above is workspace-wide.</p>
            </Panel>
          )}
        </div>

        <Panel title="Add a new source" description="Connect a channel in under a minute.">
          <div className="flex flex-col items-center rounded-xl border-2 border-dashed border-brand-200 bg-brand-50/50 px-4 py-6 text-center">
            <ConnectSourcesArt className="h-[110px] w-auto max-w-full text-brand-600" />
            <p className="mt-3 text-body-sm font-semibold text-slate-900">POS, mobile money, files or photos</p>
            <p className="mt-1 text-caption leading-relaxed text-slate-500">DemandIQ normalises every channel into one daily demand series.</p>
            <button type="button" onClick={()=>onNavigate('ingestion')} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"><PlusIcon className="h-4 w-4" strokeWidth={2.25} />Connect a source</button>
          </div>
          <ul className="mt-4 flex flex-col gap-2 text-caption text-slate-600"><li className="flex items-start gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />Historical backfill runs automatically on first connection.</li><li className="flex items-start gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />Duplicate days across sources are reconciled, never double counted.</li><li className="flex items-start gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />You can disconnect a source without losing ingested history.</li></ul>
        </Panel>
      </div>

      <Modal
        open={Boolean(detail)}
        onClose={()=>setDetail(null)}
        title={detail?.name ?? 'Source'}
        description="Everything this channel reports, straight from the API."
        footer={<>
          <ModalButton tone="ghost" onClick={()=>setDetail(null)}>Close</ModalButton>
          {detail && <ModalButton onClick={()=>void sync(detail.id, detail.name)} disabled={syncingId===detail.id || !isSyncable(detail)}>{syncingId===detail.id ? 'Syncing…' : 'Sync now'}</ModalButton>}
        </>}>
        {detail && <div className="flex flex-col gap-4 text-meta text-slate-700">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {/* Rendered from the object itself, so the view never drifts
                  from what the endpoint actually returns. ``null`` is excluded
                  from the object check on purpose; `typeof null` is 'object',
                  and dropping it would hide exactly the fields that say
                  "never synced". */}
              {Object.entries(detail).
                filter(([key, value])=> key !== 'id' && (value === null || typeof value !== 'object')).
                map(([key, value])=> <div key={key} className="min-w-0">
                    <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{key.replace(/_/g,' ').replace(/\b\w/g,(c:string)=>c.toUpperCase())}</dt>
                    <dd className="break-words text-slate-800">{value === null || value === '' ? '—' : String(value)}</dd>
                  </div>)}
            </dl>

            {/* `last_pull` is a nested object, so the flat field list above
                skips it — and it is the channel's own outcome rather than the
                workspace-wide run list below. */}
            <div>
              <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Last pull</p>
              <p className="text-caption font-medium text-slate-800">{lastPullLabel(detail)}</p>
              {detail.last_pull?.detail && (
                <p className="mt-0.5 break-words text-micro text-slate-500">{detail.last_pull.detail}</p>
              )}
            </div>

            <div>
              <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">Recent sync runs (workspace-wide)</p>
              {history.length === 0 ? (
                <p className="text-caption text-slate-500">No sync runs recorded yet. Trigger one with Sync now.</p>
              ) : (
                <ul className="flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {history.slice(0,5).map((run:any)=> <li key={run.id} className="flex items-center justify-between gap-2 px-2.5 py-2">
                      <span className="min-w-0 truncate">{jobLabel(String(run.job_type ?? 'run'))}</span>
                      <span className="flex shrink-0 items-center gap-2">
                        <StatusPill tone={runStatusTone(run.status)}>{plainStatus(run.status)}</StatusPill>
                        <span className="text-micro text-slate-500">{run.started_at ? new Date(run.started_at).toLocaleString() : ''}</span>
                      </span>
                    </li>)}
                </ul>
              )}
              <p className="mt-1.5 text-micro leading-relaxed text-slate-500">This source's own freshness comes from its last sync above. A run covers every active connector.</p>
            </div>

            <PermissionNote>
              Sync cadence, field mapping and backfill belong to the connector configuration, which owners and administrators manage on the Connectors page.
            </PermissionNote>
          </div>}
      </Modal>
    </>
  );
}
