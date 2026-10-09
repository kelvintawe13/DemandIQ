import { useCallback, useEffect, useRef, useState } from 'react';
import { LockIcon, RefreshCwIcon, TrashIcon } from 'lucide-react';
import { SourceTabs } from '../components/SourceTabs';
import { UploadImagePanel } from '../components/UploadImagePanel';
import { PreviewPanel } from '../components/PreviewPanel';
import { ExcelCsvTab } from '../components/ingestion/ExcelCsvTab';
import { ManualFormTab } from '../components/ingestion/ManualFormTab';
import { PosMomoTab } from '../components/ingestion/PosMomoTab';
import { EmptyState } from '../components/ui/EmptyState';
import { StatusPill } from '../components/ui/StatusPill';
import type { StatusTone } from '../components/ui/StatusPill';
import { api, ApiException } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { ResponsiveTable } from '../components/ui/Table';
import { plainStatus } from '../lib/plainLanguage';
import { Spinner } from '../components/ui/LoadingState';

interface ImportDemandDataProps {
  onNavigate?: (id: string) => void;
}

interface UploadRow {
  id: string;
  kind: string;
  filename: string;
  status: string;
  row_count: number;
  error_count: number;
  created_at: string | null;
}

const statusTone: Record<string, StatusTone> = {
  committed: 'success',
  ready: 'info',
  awaiting_mapping: 'warning',
  failed: 'critical',
  awaiting: 'warning'
};

/** `image` reads as jargon in a table cell; "Photo or scan" does not. */
const kindLabel: Record<string, string> = {
  manual: 'Typed in by hand',
  image: 'Photo or scan',
  ocr: 'Photo or scan',
  csv: 'Spreadsheet',
  excel: 'Spreadsheet',
  file: 'Spreadsheet',
  pos: 'POS terminal',
  momo: 'Mobile money',
  api: 'Connected API'
};

const describeKind = (kind?: string) => kindLabel[String(kind ?? '').toLowerCase()] ?? plainStatus(kind);

export function ImportDemandData(_props: ImportDemandDataProps) {
  const [activeTabId, setActiveTabId] = useState('manual');
  const [imageName, setImageName] = useState<string>();
  const [jobId, setJobId] = useState<string | null>(null);

  // uploads list — global loading: keep stale data visible while refreshing
  const [uploads, setUploads] = useState<UploadRow[]>([]);
  const [uploadsLoading, setUploadsLoading] = useState(true);
  const [uploadsRefreshing, setUploadsRefreshing] = useState(false);
  /* Read by the render to tell an initial load from a background refresh, so it
     is state. `fetchUploads` reads the ref twin instead — see below. */
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [uploadsError, setUploadsError] = useState<{ message: string; reference?: string | null; offline?: boolean } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([]);
  const [currentCursor, setCurrentCursor] = useState<string | undefined>(undefined);
  /* These three must not be dependencies of `fetchUploads`. That callback drives
     the list effect, so any state it reads has to come from a ref instead:
     depending on `hasLoadedOnce`/`uploads` made its identity change as soon as
     the first response landed, which re-ran the effect and refetched the list a
     second time on every mount. The stack is read for the same reason — stepping
     back a page after a delete needs its value without re-triggering a fetch. */
  const hasLoadedOnceRef = useRef(false);
  const uploadsRef = useRef<UploadRow[]>([]);
  const cursorStackRef = useRef<(string | null)[]>([]);
  /* Collapse repeat fetches of the page already on screen. The Refresh button is
     disabled while a request is in flight, but that is React state and state does
     not update until the next render — three clicks in one tick all read the old
     `false` and all fire. A ref updates synchronously, so it actually holds. */
  const inFlightCursorRef = useRef<string | null | undefined>(undefined);
  /* Monotonic request id. Responses can arrive out of order (slow page 1 after a
     fast page 2), and the later arrival would otherwise overwrite the newer page
     with stale rows. Only the newest id is allowed to write state. */
  const requestIdRef = useRef(0);
  const { toast } = useToast();

  const fetchUploads = useCallback(
    async (cursor?: string, options?: { force?: boolean }) => {
      /* Already loading this exact page: a duplicate call, so drop it. Compared
         with the cursor rather than a bare boolean so navigating to a different
         page while a request is open is still allowed. `force` bypasses this for
         refreshes that follow a write, where the data on screen is known stale. */
      if (!options?.force && inFlightCursorRef.current === (cursor ?? null)) return;
      inFlightCursorRef.current = cursor ?? null;
      const requestId = ++requestIdRef.current;

      const isInitial = !hasLoadedOnceRef.current && uploadsRef.current.length === 0;
      if (isInitial) setUploadsLoading(true);
      else setUploadsRefreshing(true);
      setUploadsError(null);
      try {
        const res = await api.get<{ data: UploadRow[]; has_more: boolean; next_cursor: string | null }>(
          '/api/v1/ingest/uploads',
          { limit: 10, ...(cursor ? { cursor } : {}) }
        );
        /* A newer fetch has since started, so this response is stale. Dropping it
           here keeps a slow earlier page from clobbering the current one. */
        if (requestId !== requestIdRef.current) return;
        const rows = res.data ?? [];
        /* Deleting the last row on a later page leaves that page empty even
           though earlier pages still have rows. Rendering the "no imports yet"
           empty state there would be a lie, and it drops the pager, so the
           operator is stranded on a blank list with no way back. Step back to
           the previous page instead; only page 1 may show the empty state. */
        if (rows.length === 0 && cursor) {
          const stack = cursorStackRef.current;
          /* One entry per page already visited, so the last entry is the cursor
             for the page before this one. Page 1 is stored as `null`, which
             normalises to "no cursor" — the first page. */
          const back = stack.length ? stack[stack.length - 1] ?? undefined : undefined;
          cursorStackRef.current = stack.slice(0, -1);
          setCursorStack(cursorStackRef.current);
          setCurrentCursor(back);
          if (back) {
            // Recurse for the previous page rather than leaving the list blank.
            // The slot is released first, otherwise the guard above would treat
            // this as a duplicate fetch of the page already in flight.
            inFlightCursorRef.current = undefined;
            void fetchUploads(back);
            return;
          }
        }
        uploadsRef.current = rows;
        setUploads(rows);
        setHasMore(Boolean(res.has_more));
        setNextCursor(res.next_cursor ?? null);
      } catch (e: unknown) {
        // A stale failure must not replace the error of the newer, current fetch.
        if (requestId !== requestIdRef.current) return;
        if (e instanceof ApiException) {
          const offline = e.error.message.toLowerCase().includes('failed to fetch');
          setUploadsError({ message: e.error.message, reference: e.reference ?? undefined, offline });
        } else if (e instanceof Error && (e.message.includes('Failed to fetch') || e.message.includes('NetworkError'))) {
          setUploadsError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true });
        } else {
          setUploadsError({ message: e instanceof Error ? e.message : 'Failed to load uploads' });
        }
      } finally {
        /* Only the newest request clears the in-flight slot; a stale one must not
           release the guard held by the request that replaced it. */
        if (requestId === requestIdRef.current) {
          inFlightCursorRef.current = undefined;
          setUploadsLoading(false);
          setUploadsRefreshing(false);
          hasLoadedOnceRef.current = true;
          setHasLoadedOnce(true);
        }
      }
    },
    []
  );

  useEffect(() => {
    fetchUploads(currentCursor);
  }, [fetchUploads, currentCursor]);

  const handleNext = () => {
    if (nextCursor) {
      const stack = [...cursorStackRef.current, currentCursor ?? null];
      cursorStackRef.current = stack;
      setCursorStack(stack);
      setCurrentCursor(nextCursor);
    }
  };

  const handlePrev = () => {
    const copy = [...cursorStackRef.current];
    copy.pop();
    cursorStackRef.current = copy;
    setCursorStack(copy);
    setCurrentCursor(copy.length ? copy[copy.length - 1] ?? undefined : undefined);
  };

  const handleDelete = async (id: string) => {
    // Discarding a staged upload cannot be undone, so confirm before it happens.
    if (!window.confirm('Discard this upload? Anything not yet imported is lost.')) return;
    try {
      await api.delete(`/api/v1/ingest/uploads/${id}`);
      toast('Upload discarded');
      // Forced: the row on screen is known stale, so this must not be swallowed
      // by the in-flight guard.
      fetchUploads(currentCursor, { force: true });
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to delete upload', 'error');
    }
  };

  const handleImageUploaded = (newJobId: string, fileName: string) => {
    setJobId(newJobId);
    setImageName(fileName);
    // Refresh the list so the new job appears. Forced, because a job we just
    // created has to show up even if a background fetch is still open.
    fetchUploads(currentCursor, { force: true });
  };

  return (
    <>
      {/* Recent uploads */}
      <section className="mb-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-h5 font-bold tracking-tight text-slate-900">Recent uploads</h2>
            <p className="text-meta text-slate-500">Staged files and image jobs, most recent first.</p>
          </div>
          <button
            type="button"
            onClick={() => fetchUploads(currentCursor)}
            disabled={uploadsLoading || uploadsRefreshing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-meta font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <RefreshCwIcon className={`h-3.5 w-3.5 ${uploadsRefreshing ? 'animate-spin' : ''}`} strokeWidth={1.9} />
            {uploadsRefreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {uploadsLoading && !hasLoadedOnce ? (
          <div className="mt-4 flex items-center justify-center gap-2.5 py-8">
            <Spinner className="h-5 w-5" />
            <span className="text-body-xs font-medium text-slate-600">Loading uploads…</span>
          </div>
        ) : uploadsError ? (
          <div className="mt-4">
            <EmptyState
              size="sm"
              variant={uploadsError.offline ? 'offline' : 'error'}
              title={uploadsError.offline ? 'Backend offline' : 'Failed to load uploads'}
              description={uploadsError.message + (uploadsError.reference ? ` (ref ${uploadsError.reference})` : '')}
              hints={uploadsError.offline ? ['Backend unreachable. Data saved locally will sync when online.'] : uploadsError.reference ? [`Reference ${uploadsError.reference}`] : undefined}
              action={{ label: 'Retry', onClick: () => fetchUploads(currentCursor) }}
            />
          </div>
        ) : uploads.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              size="md"
              variant="empty"
              title="No imports yet"
              description="Your staged files and image jobs will appear here"
              hints={['Upload a CSV or image to get started', 'Manual entries appear after you save them']}
              action={{ label: 'Upload a spreadsheet', onClick: () => setActiveTabId('excel') }}
              secondaryAction={{ label: 'Add a row by hand', onClick: () => setActiveTabId('manual') }}
            />
          </div>
        ) : (
          <>            <div className="relative mt-4">
              {uploadsRefreshing && (
                <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-white/70 backdrop-blur-[1px]">
                  <span className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-meta font-medium text-slate-700 shadow-sm ring-1 ring-slate-200">
                    <Spinner className="h-4 w-4" />
                    Refreshing…
                  </span>
                </div>
              )}
              <div className={uploadsRefreshing ? 'opacity-60' : ''}>
                <ResponsiveTable<any>
                caption="Your uploads"
                minWidth="min-w-[520px]"
                /* Ten rows a page is still taller than the panels around it,
                   so the box is capped and its header pinned. */
                maxHeight="max-h-[420px]"
                rows={uploads}
                rowKey={(row:any)=>String(row.id)}
                columns={[
                  { key:'file', label:'File', primary:true, render:(row:any)=> <span className="text-body-xs font-medium text-slate-800">{row.filename}</span> },
                  { key:'kind', label:'How it came in', render:(row:any)=> describeKind(row.kind) },
                  { key:'status', label:'Status', render:(row:any)=> <StatusPill tone={statusTone[row.status] ?? 'neutral'} dot>{plainStatus(row.status)}</StatusPill> },
                  { key:'rows', label:'Rows', render:(row:any)=> <><span className="tabular-nums">{row.row_count}</span>{row.error_count > 0 && <span className="ml-1 text-red-600">({row.error_count} need fixing)</span>}</> },
                  { key:'created', label:'Created', render:(row:any)=> row.created_at ? new Date(row.created_at).toLocaleString() : '—' },
                  { key:'delete', label:'', cellClassName:'pr-0', render:(row:any)=> row.status !== 'committed' && (
                      <button
                        type="button"
                        aria-label={`Delete ${row.filename}`}
                        onClick={() => handleDelete(row.id)}
                        className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-red-600"
                      >
                        <TrashIcon className="h-4 w-4" strokeWidth={1.9} />
                      </button>) },
                ]}
              />
            </div>
            {(hasMore || cursorStack.length > 0) && (
              <div className="mt-3 flex items-center justify-between gap-2">
                <button
                  type="button"
                  disabled={cursorStack.length === 0}
                  onClick={handlePrev}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-meta font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-500"
                >
                  Previous
                </button>
                <span className="text-caption text-slate-500">{hasMore ? 'More results available' : 'End of list'}</span>
                <button
                  type="button"
                  disabled={!hasMore}
                  onClick={handleNext}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-meta font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-500"
                >
                  Next
                </button>
              </div>
            )}
              </div>
          </>
        )}
      </section>

      <SourceTabs activeTabId={activeTabId} onSelect={setActiveTabId} />

      <div
        role="tabpanel"
        className={
          activeTabId === 'manual'
            ? 'mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3'
            : activeTabId === 'pos' || activeTabId === 'excel'
              ? 'mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2'
              : 'mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2'
        }
      >
        {activeTabId === 'image' && (
          <>
            <UploadImagePanel imageName={imageName} jobId={jobId} onUploaded={handleImageUploaded} />
            <PreviewPanel imageName={imageName} jobId={jobId} />
          </>
        )}
        {activeTabId === 'excel' && <ExcelCsvTab />}
        {activeTabId === 'manual' && <ManualFormTab />}
        {activeTabId === 'pos' && (
          /* A POS export is a spreadsheet, and the file channel already does
             preview → column mapping → commit. Handing the export there is the
             real ingest path; the tab used to accept the file itself and only
             show a toast. */
          <PosMomoTab onRouteToSpreadsheet={() => setActiveTabId('excel')} />
        )}
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-slate-500 md:hidden">
        <LockIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.75} />
        <span className="font-medium text-slate-700">Secure upload</span>
        <span aria-hidden="true">•</span>
        <span>Your data is encrypted and protected.</span>
      </p>
    </>
  );
}
