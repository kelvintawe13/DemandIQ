import React, { useCallback, useEffect, useState } from 'react';
import { FileTextIcon, DownloadIcon, CalendarClockIcon, PlayIcon, MailIcon, ArrowRightIcon, PencilIcon } from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { Toggle } from '../components/ui/Toggle';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { FilterBar } from '../components/ui/FilterBar';
import { ViewSwitcher } from '../components/ui/ViewSwitcher';
import { SortMenu, RecordList } from '../components/ui/RecordList';
import { useListState, type FilterDef } from '../hooks/useListState';
import { LoadingState } from '../components/ui/LoadingState';
import { PageSkeleton } from '../components/ui/Skeleton';
import { StatusPill } from '../components/ui/StatusPill';
import { ReportsArt } from '../components/illustrations/PageArt';
import { Modal, ModalButton } from '../components/ui/Modal';
import { Field, inputClass } from '../components/ui/Field';
import { useToast } from '../contexts/ToastContext';
import { EmptyState } from '../components/ui/EmptyState';
import { PermissionNote } from '../components/ui/PermissionNote';
import { DateRangeModal } from '../components/ui/DateRangeModal';
import { useSession } from '../contexts/SessionContext';
// Report periods are the workspace's calendar days, so they line up with the
// demand the workspace recorded rather than with the browser's or UTC's clock.
import { todayISO, isoDaysAgo } from '../lib/workspaceTime';
import { api, ApiException } from '../api/client';
import { plainStatus } from '../lib/plainLanguage';

interface Template { id:string; name:string; description:string; cadence?:string; format?:string; title?:string; }
interface ReportRow { id:string; name?:string; title?:string; period?:string; status?:string; created_at?:string; created?:string; size?:string; download_url?:string; format?:string; }
interface Schedule { id:string; name?:string; template?:string; recipients:string[]|string; next_run_at?:string|null; next?:string; cadence:string; enabled:boolean; format?:string; }

/** The cadences the API accepts, with the label the UI shows for each. */
const CADENCES = [
  { value: 'daily', label: 'Daily · 07:00' },
  { value: 'weekly', label: 'Weekly · Monday 07:00' },
  { value: 'monthly', label: 'Monthly · 1st at 07:00' },
] as const;

/** `demand_summary` → `Demand summary`; the API keeps templates as keys. */
function humanise(value: unknown): string {
  const text = String(value ?? '').replace(/[_-]+/g, ' ').trim();
  if (!text) return '—';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A label/value row shared by the report detail view. */
function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-2 last:border-0 last:pb-0">
      <dt className="shrink-0 text-micro text-slate-500">{label}</dt>
      <dd className="min-w-0 text-right text-meta font-medium text-slate-900">{children}</dd>
    </div>);
}

function errMeta(err:unknown){ if(err instanceof ApiException){ if(err.status===401) return {variant:'error' as const,title:'Sign in required',desc:err.error.message, hints:['Sign in to view reports']}; if(err.status===403) return {variant:'error' as const,title:'Admin access required',desc:err.error.message}; return {variant:'error' as const,title:'Failed to load reports',desc:err.error.message + (err.reference?` (ref: ${err.reference})`:'' )};} if(err instanceof TypeError) return {variant:'offline' as const,title:'You are offline',desc:'Check your connection'}; return {variant:'offline' as const,title:'Network error',desc:String(err)}; }

export function Reports(_props: {onNavigate?:(id:string)=>void}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('');
  const [range, setRange] = useState('Last 30 days');
  const [format, setFormat] = useState('PDF');
  const [generating, setGenerating] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [recipients, setRecipients] = useState('ops@demandiq.app');
  const [cadence, setCadence] = useState('Weekly · Monday 07:00');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta>|null>(null);
  const [reportsCursor, setReportsCursor] = useState<string|null>(null);
  const [hasMore, setHasMore] = useState(false);
  // Only the very first load owns the content region. Later refetches keep the
  // current panels on screen instead of blanking the page to a spinner.
  const [loaded, setLoaded] = useState(false);
  const [rangeOpen, setRangeOpen] = useState(false);
  // Report detail: the list only carries an id, so opening a report fetches its
  // own record (status, window, format, signed URL) instead of dead-ending on a
  // "Download" button whose URL we never had.
  const [detail, setDetail] = useState<any | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // Schedule editor: cadence and recipients were write-once at creation. The API
  // has always supported PATCH and DELETE on a schedule; the UI never offered it.
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [editCadence, setEditCadence] = useState('weekly');
  const [editRecipients, setEditRecipients] = useState('');
  const [savingSchedule, setSavingSchedule] = useState(false);
  const { toast } = useToast();
  const { user } = useSession();
  // Generating and scheduling need ``reports:generate``, which a data clerk's
  // seat deliberately does not hold. The library and history stay readable;
  // the buttons say why they are off instead of failing with a 403.
  const canGenerate = (user.permissions ?? []).includes('reports:generate');

  const fetchAll = useCallback(async (cursor?:string|null)=>{
    setLoading(true); setError(null);
    try{
      const [tplRes, repRes, schRes] = await Promise.all([
        api.get<{data:Template[]}>('/api/v1/reports/templates'),
        api.get<{data:ReportRow[]; has_more?:boolean; next_cursor?:string|null}>('/api/v1/reports', cursor?{cursor,limit:20}:undefined),
        api.get<{data:Schedule[]}>('/api/v1/reports/schedules'),
      ]);
      const tpls = tplRes.data ?? [];
      setTemplates(tpls);
      // Default the selection without making the selection a fetch dependency:
      // picking a template used to re-request all three endpoints (and blank
      // the page) for data that had not changed.
      setSelectedTemplate(prev => prev || tpls[0]?.id || '');
      setReports(repRes.data ?? []);
      setHasMore(!!repRes.has_more);
      setReportsCursor(repRes.next_cursor ?? null);
      setSchedules(schRes.data ?? []);
    }catch(e){ setError(errMeta(e)); }
    finally{ setLoading(false); setLoaded(true); }
  },[]);

  useEffect(()=>{ void fetchAll(null); },[fetchAll]);

  const template = templates.find(item=>item.id===selectedTemplate) ?? templates[0];

  /** Resolve the chosen period label into the API's {start, end} shape.
   *  Handles the presets and the custom "YYYY-MM-DD → YYYY-MM-DD" label, so
   *  the old dead "Custom range…" option is now a real range. */
  const periodFor = (label:string):{start:string;end:string}|null=>{
    if(label.includes('→')){
      const [start, end] = label.split('→').map((part:string)=>part.trim());
      return /^\d{4}-\d{2}-\d{2}$/.test(start) && /^\d{4}-\d{2}-\d{2}$/.test(end) ? {start, end} : null;
    }
    const days:Record<string,number> = { 'Last 7 days':6, 'Last 14 days':13, 'Last 30 days':29, 'Last 90 days':89 };
    if(label === 'This month'){
      // Month start by string slice: `todayISO()` is already `YYYY-MM-DD`, so the
      // first of its month needs no Date arithmetic (and no timezone to drift in).
      return { start: `${todayISO().slice(0,7)}-01`, end: todayISO() };
    }
    return days[label] === undefined ? null : { start: isoDaysAgo(days[label]), end: isoDaysAgo(0) };
  };

  const generate = async ()=>{
    if(!template) return;
    const period = periodFor(range);
    if(!period){ toast('Choose a valid period first','error'); return; }
    setGenerating(true);
    try{
      const res = await api.post<{report_id:string; id:string}>('/api/v1/reports/generate',{ template_id:template.id, period, format: format.toLowerCase(), location_ids:[] });
      toast(`${template.name} (${format}) queued: ${res.report_id ?? res.id}`);
      void fetchAll(null);
    }catch(e){
      if(e instanceof ApiException){
        if(e.status===422) toast(e.error.message,'error');
        else if(e.status===401) toast('Sign in required','error');
        else toast(e.error.message,'error');
      } else if(e instanceof TypeError) toast('You are offline','error');
      else toast(String(e),'error');
    } finally{ setGenerating(false); }
  };

  const toggleSchedule = async (s:Schedule)=>{
    try{
      await api.patch(`/api/v1/reports/schedules/${s.id}`,{enabled:!s.enabled});
      toast(`${s.template ?? s.name} ${!s.enabled?'enabled':'disabled'}`);
      void fetchAll(null);
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  const createSchedule = async ()=>{
    if(!template) return;
    const cadenceMap:Record<string,string> = {'Daily · 07:00':'daily','Weekly · Monday 07:00':'weekly','Monthly · 1st at 07:00':'monthly'};
    try{
      await api.post('/api/v1/reports/schedules',{ template_id: template.id, cadence: cadenceMap[cadence] ?? 'weekly', recipients: recipients.split(',').map(s=>s.trim()).filter(Boolean), format: format.toLowerCase(), enabled:true });
      toast(`${template.name} scheduled: ${cadence.toLowerCase()}.`);
      setScheduleOpen(false);
      void fetchAll(null);
    }catch(e){ if(e instanceof ApiException) toast(e.error.message,'error'); }
  };

  // Open a generated report's own record. The list only carries an id, so the
  // detail is fetched here rather than guessed from the row.
  const openReport = async (report: {id:string} & Partial<ReportRow>) => {
    setDetail({ ...report });
    setDetailError(null);
    setDetailLoading(true);
    try {
      setDetail(await api.get<any>(`/api/v1/reports/${report.id}`));
    } catch (e) {
      setDetailError(e instanceof ApiException ? e.error.message + (e.reference ? ` (ref: ${e.reference})` : '') : 'Could not load this report');
    } finally {
      setDetailLoading(false);
    }
  };

  const downloadDetail = () => {
    const url = detail?.download_url ?? detail?.signed_url;
    if (url) { window.open(url, '_blank'); return; }
    // A generated report may still be queued. Say so instead of doing nothing.
    toast(
      detail?.status ? `This report is ${plainStatus(detail.status).toLowerCase()}. The file appears when it completes.` : 'No file for this report yet.',
      'info',
    );
  };

  const openScheduleEditor = (schedule: Schedule) => {
    setEditing(schedule);
    setEditCadence(String(schedule.cadence ?? 'weekly').toLowerCase());
    setEditRecipients(
      Array.isArray(schedule.recipients) ? schedule.recipients.join(', ') : String(schedule.recipients ?? ''),
    );
  };

  const saveScheduleEdits = async () => {
    if (!editing) return;
    setSavingSchedule(true);
    try {
      await api.patch(`/api/v1/reports/schedules/${editing.id}`, {
        cadence: editCadence,
        recipients: editRecipients.split(',').map((s)=>s.trim()).filter(Boolean),
      });
      toast('Schedule updated.');
      setEditing(null);
      void fetchAll(null);
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally { setSavingSchedule(false); }
  };

  const removeSchedule = async () => {
    if (!editing) return;
    setSavingSchedule(true);
    try {
      await api.delete(`/api/v1/reports/schedules/${editing.id}`);
      toast('Schedule removed', 'info');
      setEditing(null);
      void fetchAll(null);
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally { setSavingSchedule(false); }
  };

  // Recently-generated toolbar: search by name and period, filter by status and
  // format, and read the list as rows, cards or a table. The server pages the
  // history by cursor — the Previous/Next below keep doing that half.
  const reportFilters: FilterDef<any>[] = [
    { key:'status', label:'Status', match:(r:any,v:string)=> String(r.status ?? '')===v,
      options:[{value:'',label:'Any status'},{value:'completed',label:'Completed'},{value:'queued',label:'Queued'},{value:'failed',label:'Failed'}] },
    { key:'format', label:'Format', match:(r:any,v:string)=> String(r.format ?? '').toLowerCase()===v.toLowerCase(),
      options:[{value:'',label:'Any format'},{value:'PDF',label:'PDF'},{value:'CSV',label:'CSV'},{value:'XLSX',label:'XLSX'}] },
  ];
  const reportList = useListState<any>({
    key:'business-reports',
    items: reports,
    filters: reportFilters,
    searchFields:(r:any)=> [r.name, r.title, r.period, r.format].filter(Boolean).join(' '),
    sortValue:(r:any,k:string)=> k==='report' ? String(r.name ?? r.title ?? '') : k==='when' ? String(r.created_at ?? '') : String(r[k] ?? ''),
    defaultSort:{key:'when',dir:'desc'},
    defaultView:'list',
  });
  const reportColumns: Column<any>[] = [
    { key:'report', label:'Report', primary:true, sortable:true, render:(r:any)=> (
      <button type="button" onClick={()=>void openReport(r)} aria-label={`Open report ${(r.name ?? r.title) || String(r.id).slice(0,8)}`} className="flex min-w-0 items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
        <FileTextIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
        <span className="min-w-0 truncate text-meta font-medium text-slate-900">{(r.name ?? r.title) || `${String(r.id).slice(0, 8)}…`}</span>
      </button>
    ) },
    { key:'period', label:'Period', render:(r:any)=> <span className="text-caption text-slate-500">{(r as any).period ?? ''}</span> },
    { key:'when', label:'Generated', sortable:true, render:(r:any)=> <span className="text-micro text-slate-500">{r.created_at ? new Date(r.created_at).toLocaleDateString() : (r as any).created ?? ''} · {(r as any).size ?? r.format ?? '—'}</span> },
    { key:'actions', label:'', cellClassName:'pr-0', render:(r:any)=> (
      <button type="button" onClick={()=>void openReport(r)} aria-label={`Download ${(r.name ?? r.title) || String(r.id).slice(0,8)}`} className="tap-expand inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><DownloadIcon className="h-3.5 w-3.5" strokeWidth={2} />Download</button>
    ) },
  ];

  if(loading && !loaded) return <PageSkeleton label="Loading reports" />;
  if(error) return <EmptyState variant={error.variant} title={error.title} description={error.desc} hints={error.hints} action={{label:'Retry',onClick:()=>void fetchAll(null)}} size="md" />;

  const hasTemplates = templates.length>0;
  const hasReports = reports.length>0;
  const hasSchedules = schedules.length>0;

  return (
    <>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="Report library" description="Pick a template, then choose the period and format.">
            {!hasTemplates ? (
              <EmptyState variant="empty" size="sm" title="No templates" description="No report templates available." hints={["Contact admin to configure templates"]} />
            ) : (
              <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {templates.map((item)=>{
                  const isActive = item.id===selectedTemplate;
                  return (
                    <li key={item.id}>
                      <button type="button" aria-pressed={isActive} onClick={()=>setSelectedTemplate(item.id)} className={['flex h-full w-full flex-col rounded-xl border px-3.5 py-3 text-left transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', isActive ? 'border-brand-500 bg-brand-50/60' : 'border-slate-200 hover:bg-slate-50'].join(' ')}>
                        <span className="flex items-start justify-between gap-2">
                          <span className="text-body-sm font-semibold text-slate-900">{item.name ?? item.title}</span>
                          <StatusPill tone={isActive?'success':'neutral'}>{(item as any).cadence ?? 'On demand'}</StatusPill>
                        </span>
                        <span className="mt-1 text-micro leading-relaxed text-slate-500">{item.description}</span>
                        <span className="mt-auto pt-2 text-fine font-medium text-slate-500">{(item as any).format ?? 'PDF · CSV'}</span>
                      </button>
                    </li>);
                })}
              </ul>
            )}
          </Panel>

          <Panel title="Scheduled deliveries" icon={CalendarClockIcon}>
            {!hasSchedules ? (
              <EmptyState variant="empty" size="sm" title="No schedules" description="No scheduled deliveries yet." hints={["Schedule a report to automate delivery"]} action={canGenerate ? {label:'Schedule',onClick:()=>setScheduleOpen(true)} : undefined} />
            ) : (
              <RecordList<any>
                stateKey="business-report-schedules"
                caption="Scheduled deliveries"
                minWidth="min-w-[520px]"
                items={schedules}
                countNoun="schedules"
                rowKey={(schedule:any)=>String(schedule.id)}
                searchFields={(schedule:any)=> [schedule.template, schedule.name, schedule.cadence, ...(Array.isArray(schedule.recipients)? schedule.recipients : [schedule.recipients])].filter(Boolean).join(' ')}
                searchPlaceholder="Search schedules…"
                filters={[{ key:'cadence', label:'Cadence', match:(schedule:any,v:string)=> String(schedule.cadence ?? '').toLowerCase().includes(v),
                  options:[{value:'',label:'Any cadence'},{value:'daily',label:'Daily'},{value:'weekly',label:'Weekly'},{value:'monthly',label:'Monthly'}] }]}
                sortValue={(schedule:any,k:string)=> k==='report' ? String(schedule.template ?? schedule.name ?? '') : k==='nextRun' ? String(schedule.next_run_at ?? '') : String(schedule[k] ?? '')}
                columns={[
                  { key:'report', label:'Report', primary:true, sortable:true, render:(schedule:any)=> <><span className="block text-meta font-medium text-slate-900">{schedule.template ?? schedule.name ?? schedule.id}</span><span className="block text-fine text-slate-500">{schedule.cadence}</span></> },
                  { key:'recipients', label:'Recipients', render:(schedule:any)=> <span className="inline-flex items-center gap-1.5 text-caption text-slate-600"><MailIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.8} />{Array.isArray(schedule.recipients)? schedule.recipients.join(', ') : schedule.recipients}</span> },
                  { key:'nextRun', label:'Next run', sortable:true, render:(schedule:any)=> schedule.next_run_at ? new Date(schedule.next_run_at).toLocaleString() : (schedule as any).next ?? '—' },
                  { key:'active', label:'Active', render:(schedule:any)=> <Toggle checked={!!schedule.enabled} onChange={()=>void toggleSchedule(schedule)} label={`Toggle ${schedule.template ?? schedule.id}`} disabled={!canGenerate} title={canGenerate ? undefined : 'Your seat cannot change schedules'} /> },
                  { key:'edit', label:'', cellClassName:'pr-0', render:(schedule:any)=> (
                      <button type="button" onClick={()=>openScheduleEditor(schedule)} aria-label={`Edit schedule for ${schedule.template ?? schedule.name ?? schedule.id}`} className="tap-expand rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"><PencilIcon className="h-4 w-4" strokeWidth={1.9} /></button>) },
                ]}
              />
            )}
          </Panel>

          <Panel title="Recently generated">
            {!hasReports ? (
              <EmptyState variant="empty" size="md" title="No reports yet" description="Generate your first report from a template." hints={["Pick a template and period","Reports are delivered to your inbox when ready"]} action={canGenerate ? {label:'Generate',onClick:generate} : undefined} />
            ) : (
              <>
                <FilterBar<any>
                  search={reportList.search}
                  onSearch={reportList.setSearch}
                  searchPlaceholder="Search reports…"
                  filters={reportFilters}
                  values={reportList.filterValues}
                  onFilterChange={reportList.setFilter}
                  onClear={reportList.clearFilters}
                  activeCount={reportList.activeFilterCount}
                  countLabel={reportList.filtered !== reportList.total ? `${reportList.filtered} of ${reportList.total} reports` : undefined}>
                  {reportList.view !== 'table' && <SortMenu<any> columns={[{key:'report',label:'Name',sortable:true},{key:'when',label:'Generated',sortable:true}]} sort={reportList.sort} onSort={reportList.setSortKey} />}
                  <ViewSwitcher value={reportList.view} onChange={reportList.setView} />
                </FilterBar>

                {reportList.view === 'table' ? (
                  <ResponsiveTable<any>
                    columns={reportColumns}
                    rows={reportList.pageRows}
                    rowKey={(report:any)=>String(report.id)}
                    caption="Recently generated"
                    minWidth="min-w-[520px]"
                  />
                ) : (
                <ul className={reportList.view === 'cards' ? 'grid grid-cols-1 gap-2 sm:grid-cols-2' : 'divide-y divide-slate-100'}>
                  {reportList.pageRows.map((report)=>
                  <li key={report.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 first:pt-0 last:pb-0">
                      {/* The whole row opens the report's own record. It used to be
                          a dead end: the list carries only an id, so "Download"
                          had no URL and could only toast a status at the user. */}
                      <button
                        type="button"
                        onClick={()=>void openReport(report)}
                        aria-label={`Open report ${(report.name ?? report.title) || String(report.id).slice(0,8)}`}
                        className="tap-expand flex min-w-0 flex-1 items-center gap-x-3 gap-y-1 rounded-lg text-left transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        <FileTextIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
                        <span className="min-w-0 flex-1 truncate text-meta font-medium text-slate-900" title={String(report.name ?? report.title ?? report.id)}>{(report.name ?? report.title) || `${String(report.id).slice(0, 8)}…`}</span>
                        <span className="text-caption text-slate-500">{(report as any).period ?? ''}</span>
                        <span className="hidden text-micro text-slate-500 sm:inline">{report.created_at ? new Date(report.created_at).toLocaleDateString() : (report as any).created ?? ''} · {(report as any).size ?? report.format ?? '—'}</span>
                      </button>
                      <button type="button" onClick={()=>void openReport(report)} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-2.5 text-micro font-semibold text-slate-600 sm:py-1.5 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        <DownloadIcon className="h-3.5 w-3.5" strokeWidth={2} />Download
                      </button>
                    </li>
                  )}
                </ul>
                )}
                {(hasMore || reportsCursor) && <div className="mt-3 flex gap-2"><button type="button" onClick={()=>void fetchAll(null)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-semibold text-slate-600 hover:bg-slate-50">Previous</button>{hasMore && <button type="button" onClick={()=>void fetchAll(reportsCursor)} className="rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 text-caption font-semibold text-white hover:bg-brand-800">Next</button>}</div>}
                <p className="mt-2 text-fine text-slate-500">{reportList.filtered} {reportList.filtered === 1 ? 'report' : 'reports'} · each records the period it covers and which model produced it</p>
              </>
            )}
          </Panel>
        </div>

        <Panel title="Build a report" description="Runs against your live demand data.">
          {!template ? (
            <EmptyState variant="empty" size="sm" title="No template selected" description="No templates available to build." />
          ) : (
            <>
              <div className="flex flex-col items-center rounded-xl border-2 border-dashed border-brand-200 bg-brand-50/50 px-4 py-5 text-center">
                <ReportsArt className="h-[104px] w-auto max-w-full text-brand-600" />
                <p className="mt-3 text-body-sm font-semibold text-slate-900">{template.name ?? template.title}</p>
                <p className="mt-1 text-micro leading-relaxed text-slate-500">{template.description}</p>
              </div>
              <div className="mt-4 flex flex-col gap-3">
                <div><span className="block text-caption font-semibold text-slate-800">Period</span><button type="button" onClick={()=>setRangeOpen(true)} className="mt-1 flex w-full items-center justify-between gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-body-xs text-slate-800 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-500"><span>{periodFor(range) ? range : 'Choose a period'}</span><CalendarClockIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.9} /></button></div>
                <div><span className="block text-caption font-semibold text-slate-800">Format</span><div className="mt-1 flex gap-1 rounded-lg border border-slate-200 p-1">{['PDF','CSV','XLSX'].map((option)=><button key={option} type="button" aria-pressed={format===option} onClick={()=>setFormat(option)} className={['flex-1 rounded-md px-2 py-2.5 text-caption sm:py-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500', format===option ? 'bg-brand-600 font-semibold text-white' : 'font-medium text-slate-600 hover:bg-slate-100'].join(' ')}>{option}</button>)}</div></div>
                <button type="button" onClick={()=>void generate()} disabled={generating || !canGenerate} title={canGenerate ? undefined : 'Your seat cannot generate reports'} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2.5 text-body-sm font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"><PlayIcon className={['h-4 w-4',generating?'animate-pulse':''].join(' ')} strokeWidth={2} />{generating?'Generating…':`Generate ${format}`}</button>
                <button type="button" onClick={()=>setScheduleOpen(true)} disabled={!canGenerate} title={canGenerate ? undefined : 'Your seat cannot schedule reports'} className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-300 px-4 py-2.5 text-body-xs font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-50">Schedule instead<ArrowRightIcon className="h-4 w-4 text-slate-500" strokeWidth={2} /></button>
                {canGenerate ?
                <p className="text-center text-fine text-slate-500">{range} · {template.name ?? template.title} · delivered to your inbox when ready.</p> :
                <PermissionNote>
                    Your seat can read reports but not create them. Generate and schedule need the Analyst seat. Ask an owner or administrator to grant it.
                  </PermissionNote>
                }
              </div>
            </>
          )}
        </Panel>
      </div>

      <DateRangeModal open={rangeOpen} onClose={()=>setRangeOpen(false)} value={range} onApply={setRange} />

      {/* A generated report's own record: what it covers, whether it is ready,
          and the file itself. Previously the row could only offer a Download
          that had no URL to open. */}
      <Modal
        open={Boolean(detail)}
        onClose={()=>setDetail(null)}
        title={detail?.template || detail?.name ? humanise(detail?.template ?? detail?.name) : 'Report'}
        description="Status, window and file for this generated report."
        footer={<>
          <ModalButton tone="ghost" onClick={()=>setDetail(null)}>Close</ModalButton>
          <ModalButton onClick={downloadDetail} disabled={detailLoading || Boolean(detailError)}>
            {detail?.download_url ?? detail?.signed_url ? 'Download' : 'Check status'}
          </ModalButton>
        </>}>
        {detailLoading ?
        <LoadingState label="Loading report" size="sm" /> :
        detailError ?
        <EmptyState variant="error" size="sm" title="Could not load this report" description={detailError} action={{label:'Retry', onClick:()=> detail && void openReport({ id: String(detail.id) })}} /> :
        detail &&
        <dl className="flex flex-col gap-2.5">
            <DetailRow label="Report">
              <span className="break-all font-mono text-micro">{String(detail.id ?? '—')}</span>
            </DetailRow>
            <DetailRow label="Template">{detail.template ? humanise(detail.template) : '—'}</DetailRow>
            <DetailRow label="Window">
              {detail.period_start || detail.period_end ?
              `${detail.period_start ? new Date(detail.period_start).toLocaleDateString() : '—'} → ${detail.period_end ? new Date(detail.period_end).toLocaleDateString() : '—'}` :
              detail.period ?? '—'}
            </DetailRow>
            <DetailRow label="Format"><span className="uppercase">{detail.format ?? '—'}</span></DetailRow>
            <DetailRow label="Status">
              <StatusPill tone={detail.status === 'completed' || detail.status === 'ready' ? 'success' : detail.status === 'failed' ? 'critical' : 'info'} dot>
                {plainStatus(detail.status)}
              </StatusPill>
            </DetailRow>
            <DetailRow label="Size">{detail.size_bytes != null ? formatBytes(detail.size_bytes) : detail.size ?? '—'}</DetailRow>
            <DetailRow label="Generated">{detail.created_at ? new Date(detail.created_at).toLocaleString() : '—'}</DetailRow>
            <p className="mt-1 text-fine leading-relaxed text-slate-500">
              {(detail.download_url ?? detail.signed_url) ?
              'The download link is signed and short-lived. It was built from your latest demand data, and the model used is recorded on the report.' :
              'No file yet. The report is still being produced. The download appears here once it completes.'}
            </p>
        </dl>}
      </Modal>

      {/* Cadence and recipients were write-once when a schedule was created.
          The API has always supported PATCH and DELETE on a schedule. */}
      <Modal
        open={Boolean(editing)}
        onClose={()=>setEditing(null)}
        title="Edit schedule"
        description={editing ? `${editing.template ?? editing.name ?? editing.id} · ${editing.enabled ? 'active' : 'paused'}` : undefined}
        footer={<>
          <ModalButton tone="danger" onClick={()=>void removeSchedule()} disabled={savingSchedule}>Delete</ModalButton>
          <ModalButton tone="ghost" onClick={()=>setEditing(null)}>Cancel</ModalButton>
          <ModalButton onClick={()=>void saveScheduleEdits()} disabled={savingSchedule}>{savingSchedule ? 'Saving…' : 'Save changes'}</ModalButton>
        </>}>
        <div className="flex flex-col gap-3">
          <Field id="edit-cadence" label="Cadence">
            <select id="edit-cadence" value={editCadence} onChange={(e)=>setEditCadence(e.target.value)} className={inputClass}>
              {CADENCES.map((option)=> <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </Field>
          <Field id="edit-recipients" label="Recipients" hint="Comma separated. Each gets the file as an attachment.">
            <input id="edit-recipients" type="text" value={editRecipients} onChange={(e)=>setEditRecipients(e.target.value)} className={inputClass} />
          </Field>
          <PermissionNote>
            Deleting a schedule stops future deliveries. Reports already delivered are unaffected.
          </PermissionNote>
        </div>
      </Modal>

      <Modal open={scheduleOpen} onClose={()=>setScheduleOpen(false)} title="Schedule this report" description={`${template?.name ?? ''} · ${format}`} footer={<><ModalButton tone="ghost" onClick={()=>setScheduleOpen(false)}>Cancel</ModalButton><ModalButton onClick={()=>void createSchedule()}>Create schedule</ModalButton></>}>
        <div className="flex flex-col gap-3">
          <Field id="sched-cadence" label="Cadence"><select id="sched-cadence" value={cadence} onChange={(e)=>setCadence(e.target.value)} className={inputClass}><option>Daily · 07:00</option><option>Weekly · Monday 07:00</option><option>Monthly · 1st at 07:00</option></select></Field>
          <Field id="sched-recipients" label="Recipients" hint="Comma separated. Each gets the file as an attachment."><input id="sched-recipients" type="text" value={recipients} onChange={(e)=>setRecipients(e.target.value)} className={inputClass} /></Field>
        </div>
      </Modal>
    </>
  );
}
