import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ImageIcon,
  ListIcon,
  LockIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  TrashIcon,
  XIcon,
} from 'lucide-react';
import { UploadEntriesPanel } from '../components/UploadEntriesPanel';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
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
  /** The original photo is stored server-side and can be shown again. */
  has_source?: boolean;
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
  /* The stored original opened from a row. The object URL is owned here so it
     is revoked on close and on unmount, not left alive for the tab's lifetime. */
  const [sourceView, setSourceView] = useState<{ id: string; filename: string; url: string } | null>(null);
  const [sourceLoadingId, setSourceLoadingId] = useState<string | null>(null);
  /* The upload whose committed entries are open in the editor below the list. */
  const [entriesUpload, setEntriesUpload] = useState<UploadRow | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  /** The pending destructive action, shown in the app's own dialog. */
  const [confirming, setConfirming] = useState<{
    title: string;
    body: string;
    confirmLabel: string;
    run: () => void;
  } | null>(null);
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

  /** Drop the open photo and release its object URL. */
  const closeSource = () => {
    setSourceView((prev) => {
      if (prev) URL.revokeObjectURL(prev.url);
      return null;
    });
  };

  /* A photo opened from a row would otherwise keep a reference to a blob for as
     long as the screen stays mounted, which is the whole session. */
  useEffect(() => () => closeSource(), []);

  /**
   * Show the photo an image upload was read from, or hide it if already open.
   *
   * Fetched with the session's token rather than opened by URL: the endpoint is
   * authenticated, so a plain link would arrive unauthenticated and 404.
   */
  const handleViewSource = async (row: UploadRow) => {
    if (sourceView?.id === row.id) {
      closeSource();
      return;
    }
    // One panel under the list at a time: the photo and the entries are both
    // about the same import, and stacking them pushed the table off screen.
    setEntriesUpload(null);
    setSourceLoadingId(row.id);
    try {
      const blob = await api.blob(`/api/v1/ingest/uploads/${row.id}/source`);
      const url = URL.createObjectURL(blob);
      setSourceView((prev) => {
        if (prev) URL.revokeObjectURL(prev.url);
        return { id: row.id, filename: row.filename, url };
      });
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Could not open the original photo', 'error');
    } finally {
      setSourceLoadingId(null);
    }
  };

  /**
   * Read the stored original again, or send the operator to the picker.
   *
   * Only photos keep their bytes, so a spreadsheet cannot be re-read; the
   * honest fallback is the tab that accepts it again rather than a button that
   * silently does nothing.
   */
  const handleRetry = async (row: UploadRow) => {
    if (!row.has_source) {
      setActiveTabId(row.kind === 'image' ? 'image' : 'excel');
      toast(`Nothing stored to re-read — choose ${row.filename} to upload it again.`, 'info');
      return;
    }
    setRetryingId(row.id);
    try {
      const res = await api.post<{ row_count: number }>(`/api/v1/ingest/uploads/${row.id}/retry`);
      const count = res?.row_count ?? 0;
      toast(`Re-read ${count} ${count === 1 ? 'row' : 'rows'} from the stored original`, 'success');
      fetchUploads(currentCursor, { force: true });
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Could not re-read the original', 'error');
    } finally {
      setRetryingId(null);
    }
  };

  const toggleEntries = (row: UploadRow) => {
    if (entriesUpload?.id === row.id) {
      setEntriesUpload(null);
      return;
    }
    closeSource();
    setEntriesUpload(row);
  };

  /** Return the image panels to their empty state. */
  const clearImageSelection = () => {
    setJobId(null);
    setImageName(undefined);
  };

  /**
   * Ask before deleting, in the app's own dialog.
   *
   * `window.confirm` blocked the whole page and could not say which file, how
   * many entries, or that history was involved — and the browser can suppress
   * it entirely, turning a destructive click into an immediate delete. The
   * dialog can carry all of that and is dismissible only on purpose.
   */
  const handleDelete = (id: string) => {
    const row = uploadsRef.current.find((r) => r.id === id);
    const committed = row?.status === 'committed';
    const count = row?.row_count ?? 0;
    const filename = row?.filename ?? 'this upload';
    setConfirming({
      title: committed ? `Delete ${filename}?` : `Discard ${filename}?`,
      body: committed
        ? `Deleting this import also deletes the ${count} ${count === 1 ? 'entry' : 'entries'} it wrote. They are removed from demand history permanently, and any forecast built on them is stale until it is re-run.`
        : 'The staged rows are discarded. Anything not yet imported is lost, and this cannot be undone.',
      confirmLabel: committed ? 'Delete import' : 'Discard upload',
      run: () => void performDelete(id),
    });
  };

  const performDelete = async (id: string) => {
    const deletingCurrentJob = id === jobId;
    try {
      const res = await api.delete<{ removed_rows?: number }>(`/api/v1/ingest/uploads/${id}`);
      if (deletingCurrentJob) clearImageSelection();
      // The photo is gone with the row; leaving it on screen would show an
      // image for an upload that no longer exists.
      if (sourceView?.id === id) closeSource();
      if (entriesUpload?.id === id) setEntriesUpload(null);
      const removed = res?.removed_rows ?? 0;
      toast(removed > 0 ? `Import deleted — ${removed} ${removed === 1 ? 'entry' : 'entries'} removed` : 'Upload discarded');
      // Forced: the row on screen is known stale, so this must not be swallowed
      // by the in-flight guard.
      fetchUploads(currentCursor, { force: true });
    } catch (e: unknown) {
      // A 404 means another tab or a retry already discarded it. The preview
      // must still stop pointing at a job that no longer exists.
      if (e instanceof ApiException && e.status === 404) {
        if (deletingCurrentJob) clearImageSelection();
        toast('Upload already discarded');
        fetchUploads(currentCursor, { force: true });
        return;
      }
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to delete upload', 'error');
    }
  };

  /**
   * Cancel the staged image job from the preview panel. Clear the panels first
   * so the UI never sits on a job that is being deleted; if the delete itself
   * fails, the refreshed Recent uploads row is still there to retry from.
   */
  const handleImageCancelled = () => {
    if (!jobId) return;
    const id = jobId;
    setConfirming({
      title: `Discard ${imageName ?? 'this upload'}?`,
      body: 'The staged photo and the rows read from it are discarded. Nothing has been imported yet, and this cannot be undone.',
      confirmLabel: 'Discard upload',
      run: () => void performImageCancel(id),
    });
  };

  const performImageCancel = async (id: string) => {
    clearImageSelection();
    try {
      await api.delete(`/api/v1/ingest/uploads/${id}`);
      toast('Image upload discarded');
    } catch (e: unknown) {
      if (!(e instanceof ApiException && e.status === 404)) {
        toast('Removed from this screen, but the staged upload could not be deleted.', 'error');
      }
    }
    fetchUploads(currentCursor, { force: true });
  };

  const handleImageUploaded = (newJobId: string, fileName: string) => {
    setJobId(newJobId);
    setImageName(fileName);
    // Refresh the list so the new job appears. Forced, because a job we just
    // created has to show up even if a background fetch is still open.
    fetchUploads(currentCursor, { force: true });
  };

  /* Rendered at the foot of the screen: the upload panels are what the
     operator came for, and the history belongs after them rather than pushing
     the tabs below the fold. */
  const recentUploads = (
    <section className="mt-3 rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5">
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
        <>
          <div className="relative mt-4">
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
                /* One actions cell rather than three empty-headed columns:
                   the list already has five labelled columns and each extra
                   sliver pushed the file name into a scroll. */
                { key:'actions', label:'', cellClassName:'pr-0', render:(row:any)=> (
                    <div className="flex items-center justify-end gap-0.5">
                      {row.has_source && (
                        <button
                          type="button"
                          aria-label={`View original photo of ${row.filename}`}
                          aria-pressed={sourceView?.id === row.id}
                          title="View original photo"
                          disabled={sourceLoadingId === row.id}
                          onClick={() => void handleViewSource(row)}
                          className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-brand-700 disabled:opacity-50"
                        >
                          {sourceLoadingId === row.id
                            ? <Spinner className="h-4 w-4" />
                            : <ImageIcon className="h-4 w-4" strokeWidth={1.9} />}
                        </button>
                      )}
                      {(row.status === 'committed' || row.row_count > 0) && (
                        <button
                          type="button"
                          aria-label={`Show entries from ${row.filename}`}
                          aria-pressed={entriesUpload?.id === row.id}
                          title="Show the entries this import wrote"
                          onClick={() => toggleEntries(row)}
                          className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-brand-700"
                        >
                          <ListIcon className="h-4 w-4" strokeWidth={1.9} />
                        </button>
                      )}
                      {row.status !== 'committed' && (
                        <button
                          type="button"
                          aria-label={`Re-upload ${row.filename}`}
                          title={row.has_source
                            ? 'Read the stored original again'
                            : 'Nothing stored — choose the file again'}
                          disabled={retryingId === row.id}
                          onClick={() => void handleRetry(row)}
                          className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-brand-700 disabled:opacity-50"
                        >
                          {retryingId === row.id
                            ? <Spinner className="h-4 w-4" />
                            : <RotateCcwIcon className="h-4 w-4" strokeWidth={1.9} />}
                        </button>
                      )}
                      <button
                        type="button"
                        aria-label={`Delete ${row.filename}`}
                        title={row.status === 'committed'
                          ? 'Delete this import and the entries it wrote'
                          : 'Discard this upload'}
                        onClick={() => handleDelete(row.id)}
                        className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-red-600"
                      >
                        <TrashIcon className="h-4 w-4" strokeWidth={1.9} />
                      </button>
                    </div>) },
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
          {entriesUpload && (
            <UploadEntriesPanel
              upload={entriesUpload}
              onClose={() => setEntriesUpload(null)}
              onChanged={() => fetchUploads(currentCursor, { force: true })}
            />
          )}
          {sourceView && (
            /* The photo is shown here rather than in a new tab so it sits
               next to the rows it produced: the point of keeping it is to
               check a doubtful value without leaving the screen. */
            <figure className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
              <figcaption className="flex items-center justify-between gap-2">
                <span className="truncate text-caption font-medium text-slate-600">
                  Original photo · {sourceView.filename}
                </span>
                <button
                  type="button"
                  aria-label="Close original photo"
                  onClick={closeSource}
                  className="tap-expand rounded-md p-1 text-slate-500 hover:bg-white hover:text-slate-700"
                >
                  <XIcon className="h-4 w-4" strokeWidth={1.9} />
                </button>
              </figcaption>
              <a
                href={sourceView.url}
                target="_blank"
                rel="noreferrer"
                title={`Open ${sourceView.filename} full size`}
                className="mt-2 block"
              >
                <img
                  src={sourceView.url}
                  alt={`Original photo: ${sourceView.filename}`}
                  className="mx-auto max-h-[360px] w-auto max-w-full rounded border border-slate-200 bg-white object-contain"
                />
              </a>
            </figure>
          )}
          </div>
        </>
      )}
    </section>
  );

  return (
    <>
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
            <PreviewPanel imageName={imageName} jobId={jobId} onClear={handleImageCancelled} />
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

      {recentUploads}

      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-slate-500 md:hidden">
        <LockIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.75} />
        <span className="font-medium text-slate-700">Secure upload</span>
        <span aria-hidden="true">•</span>
        <span>Your data is encrypted and protected.</span>
      </p>

      {/* One dialog for every destructive action on this screen, carrying the
          file name and the count so the confirmation is evidence, not a
          speed bump. */}
      <ConfirmDialog
        open={Boolean(confirming)}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming?.run()}
        title={confirming?.title ?? ''}
        body={confirming?.body ?? ''}
        confirmLabel={confirming?.confirmLabel ?? 'Delete'}
        tone="danger"
      />
    </>
  );
}
