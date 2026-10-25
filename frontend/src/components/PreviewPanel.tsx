import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ChevronsUpDownIcon, FileTextIcon, CircleCheckBigIcon, ArrowRightIcon } from 'lucide-react';
import { Modal, ModalButton } from './ui/Modal';
import { EmptyState } from './ui/EmptyState';
import { useToast } from '../contexts/ToastContext';
import { api, ApiException } from '../api/client';
import { todayISO } from '../lib/workspaceTime';

/**
 * Where the engine read a value, as fractions of the uploaded photo.
 *
 * Fractions, not pixels: the review screen sizes the photo to its container, so
 * a pixel offset from the copy the engine read would not line up at any other
 * size.
 */
interface RowBoxes {
  date?: number[] | null;
  demand_qty?: number[] | null;
}

interface ExtractedRow {
  row_index?: number;
  date: string;
  demand_qty?: number;
  visitors?: number;
  location?: string | null;
  notes?: string | null;
  confidence?: number | null;
  boxes?: RowBoxes | null;
}

interface PreviewPanelProps {
  imageName?: string;
  jobId?: string | null;
  onClear?: () => void;
}

/** A row in the editable preview, plus whatever the engine said about it. */
interface EditingRow {
  date: string;
  demand_qty: number;
  confidence?: number | null;
  /** True for a row the operator typed themselves — it has no engine score. */
  handAdded?: boolean;
  /** Where the engine read this row on the photo, when it reported geometry. */
  boxes?: RowBoxes | null;
}

interface ImageJobResponse {
  job_id: string;
  status: string;
  filename: string;
  extracted_rows: ExtractedRow[];
  headers: string[];
  row_count: number;
  note?: string | null;
  rows?: ExtractedRow[];
  /** `remote` (a vision service) or `tesseract` (the local text parser). */
  engine?: string | null;
  /** Measured engine metadata; `mean_confidence` is 0-1, or null if unreported. */
  quality?: {
    mean_confidence?: number | null;
    page_segmentation_mode?: number | null;
    width?: number | null;
    height?: number | null;
    format?: string | null;
  } | null;
}

/**
 * Below this, the local parser is telling us the line was ambiguous rather than
 * that it was unlikely to be right.
 */
const AMBIGUOUS_LINE = 0.8;

/**
 * What a row's score means, worded for the engine that produced it.
 *
 * The two engines put different things in `confidence`, and showing one number
 * for both would be a quiet lie. A remote vision service returns a real model
 * score, so that is reported as a percentage. The local parser's value counts how
 * many numbers survived on the line after the date was stripped — 0.9 means the
 * demand count was the only candidate, 0.75 means several were and the nearest
 * to the date was taken for the operator. That is a readability signal, so it is
 * described in words and never dressed up as a probability.
 */
function scoreNote(engine: string | null | undefined, row: EditingRow): { label: string; flagged: boolean } {
  if (row.handAdded) return { label: 'Added by hand', flagged: false };
  if (row.confidence == null) return { label: 'No score reported', flagged: false };
  const flagged = row.confidence < AMBIGUOUS_LINE;
  if (engine === 'remote') {
    return {
      label: `${Math.round(row.confidence * 100)}% model score${flagged ? ' — check' : ''}`,
      flagged,
    };
  }
  return flagged
    ? { label: 'Several numbers on the line — check', flagged }
    : { label: 'One number on the line', flagged };
}

const ENGINE_LABEL: Record<string, string> = {
  remote: 'Read by the OCR service',
  tesseract: 'Read by the local OCR engine',
};

/** Position of a highlight box over the photo, from an OCR box in fractions. */
function boxStyle(box?: number[] | null): CSSProperties | undefined {
  if (!box || box.length !== 4 || box.some((value) => !Number.isFinite(value))) return undefined;
  const [left, top, width, height] = box;
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${width * 100}%`,
    height: `${height * 100}%`,
  };
}

export function PreviewPanel({ imageName, jobId, onClear }: PreviewPanelProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [imported, setImported] = useState(false);
  const { toast } = useToast();

  const [fetching, setFetching] = useState(false);
  /** Which job the current rows belong to, so a new job never flashes the empty state. */
  const [loadedJobId, setLoadedJobId] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<{ message: string; reference?: string | null } | null>(null);
  const [extractedRows, setExtractedRows] = useState<ExtractedRow[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [engine, setEngine] = useState<string | null>(null);
  const [quality, setQuality] = useState<ImageJobResponse['quality']>(null);

  // locations for commit
  const [locations, setLocations] = useState<Array<{ id: string; name: string }>>([]);
  const [defaultLocationId, setDefaultLocationId] = useState<string>('');
  const [editingRows, setEditingRows] = useState<EditingRow[]>([]);
  const [commitLoading, setCommitLoading] = useState(false);
  const [commitError, setCommitError] = useState<{ message: string; reference?: string | null } | null>(null);
  /** The stored photo, so rows can be checked against what they were read from. */
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  /** The row the operator is on, so its highlights are the pointed-at ones. */
  const [activeRow, setActiveRow] = useState<number | null>(null);

  /** The job the panel is showing now; a late response for an older one is ignored. */
  const jobIdRef = useRef<string | null>(jobId ?? null);
  jobIdRef.current = jobId ?? null;

  const hasJob = Boolean(jobId);
  // On the render where `jobId` first becomes non-null, the effect below has
  // not run yet. Without this, the panel renders "No rows detected" for a frame
  // before the fetch starts — the flash operators see after every upload.
  const loadingJob = hasJob && (fetching || loadedJobId !== jobId);
  const statusLabel = imported
    ? 'Imported'
    : !hasJob
      ? 'Ready to extract'
      : loadingJob
        ? 'Extracting…'
        : extractedRows.length === 0 && editingRows.length === 0
          ? 'No rows detected'
          : 'Extraction complete';

  // fetch locations for location picker
  useEffect(() => {
    api
      .get<{ data: Array<{ id: string; name: string }> }>('/api/v1/locations', { limit: 50 })
      .then((res) => {
        const opts = (res.data ?? []).map((d) => ({ id: d.id, name: d.name }));
        setLocations(opts);
        if (opts.length > 0 && !defaultLocationId) setDefaultLocationId(opts[0].id);
      })
      .catch(() => setLocations([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchJob = async (id: string) => {
    setFetching(true);
    setFetchError(null);
    try {
      const res = await api.get<ImageJobResponse>(`/api/v1/ingest/image/${id}`);
      // Cancelled or replaced while the request was in flight: this response
      // belongs to a job the panel no longer shows.
      if (jobIdRef.current !== id) return;
      const rows: ExtractedRow[] = (res.extracted_rows ?? (res as unknown as { rows?: ExtractedRow[] }).rows ?? []) as ExtractedRow[];
      setExtractedRows(rows);
      setNote(res.note ?? null);
      setEngine(res.engine ?? null);
      setQuality(res.quality ?? null);
      // Initialise the editable copy, carrying each row's score so the review
      // step can point at the ones worth a second look.
      setEditingRows(
        rows.map((r) => ({
          date: String(r.date ?? ''),
          demand_qty: Number(r.demand_qty ?? r.visitors ?? 0),
          confidence: r.confidence ?? null,
          boxes: r.boxes ?? null
        }))
      );
      // if rows empty but status not committed, keep polling maybe
      if (rows.length === 0 && res.status !== 'committed') {
        // OCR returned no readable rows; nothing to poll for.
      }
    } catch (e: unknown) {
      if (jobIdRef.current !== id) return;
      if (e instanceof ApiException) {
        setFetchError({ message: e.error.message, reference: e.reference ?? undefined });
      } else {
        setFetchError({ message: e instanceof Error ? e.message : 'Failed to fetch image job' });
      }
    } finally {
      if (jobIdRef.current === id) {
        setFetching(false);
        setLoadedJobId(id);
      }
    }
  };

  // OCR runs synchronously inside POST /ingest/image, so the job is already
  // ready when this panel receives its id. Polling it every 3s only repeated
  // the same 200 response forever and overwrote rows the operator had started
  // editing (the manual-entry rows are reset from the response on each fetch).
  useEffect(() => {
    if (!jobId) {
      setExtractedRows([]);
      setEditingRows([]);
      setFetchError(null);
      setEngine(null);
      setQuality(null);
      setImported(false);
      setLoadedJobId(null);
      setFetching(false);
      setActiveRow(null);
      return;
    }
    void fetchJob(jobId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // The stored photo, fetched separately from the rows: it is the thing the
  // operator is asked to check them against, and a review without it is just
  // another table. Fetching happens on the job id and nowhere else, so editing a
  // row never re-downloads the image.
  useEffect(() => {
    if (!jobId) {
      setPhotoUrl(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;
    setPhotoUrl(null);
    void (async () => {
      try {
        const blob = await api.blob(`/api/v1/ingest/uploads/${jobId}/source`);
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPhotoUrl(objectUrl);
      } catch {
        // The photo is a review aid, not a requirement: an upload from before
        // originals were kept, or a browser without blob URLs, still leaves the
        // rows editable — so the panel simply shows no highlights.
        if (!cancelled) setPhotoUrl(null);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId]);

  const handleCommit = async () => {
    if (!jobId) return;
    if (!defaultLocationId) {
      setCommitError({ message: 'Select a location for the extracted rows' });
      return;
    }
    // validate rows
    for (let i = 0; i < editingRows.length; i++) {
      const r = editingRows[i];
      if (!r.date) {
        setCommitError({ message: `Row ${i}: date is required` });
        return;
      }
      if (Number.isNaN(Number(r.demand_qty)) || Number(r.demand_qty) < 0) {
        setCommitError({ message: `Row ${i}: demand_qty must be >= 0` });
        return;
      }
    }
    // if no rows to commit (stub empty), use placeholder? allow committing edited rows even if original empty -> require at least one row
    if (editingRows.length === 0) {
      setCommitError({ message: 'No rows to commit. Add at least one row.' });
      return;
    }
    setCommitLoading(true);
    setCommitError(null);
    try {
      const payload = {
        rows: editingRows.map((r) => ({
          location_id: defaultLocationId,
          date: r.date,
          demand_qty: Number(r.demand_qty)
        }))
      };
      const res = await api.post<{ committed: number; reconciled: number; status: string }>(
        `/api/v1/ingest/image/${jobId}/commit`,
        payload
      );
      toast(`${res.committed} rows imported from the photo (${res.reconciled} reconciled).`);
      setImported(true);
      setConfirmOpen(false);
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        const details = (e.error.details as Array<{ message: string }> | undefined) ?? [];
        const msg = details.length > 0 ? `${e.error.message}: ${details.map((d) => d.message).join(', ')}` : e.error.message;
        setCommitError({ message: msg, reference: e.reference ?? undefined });
      } else {
        setCommitError({ message: e instanceof Error ? e.message : 'Commit failed' });
      }
    } finally {
      setCommitLoading(false);
    }
  };

  const updateRow = (idx: number, field: 'date' | 'demand_qty', value: string) => {
    setEditingRows((cur) => {
      const copy = [...cur];
      copy[idx] = { ...copy[idx], [field]: field === 'demand_qty' ? Number(value) : value } as typeof copy[number];
      return copy;
    });
  };

  /** Rows the engine flagged, or that the parser found ambiguous. */
  const flaggedRows = editingRows.filter((row) => scoreNote(engine, row).flagged);

  /**
   * Dates entered more than once. The commit upserts on (location, date), so only
   * one row per day can survive — the later one silently replaces the earlier.
   * Derived from the rows being edited rather than from the server's read, so
   * correcting a date here updates the warning immediately.
   */
  const duplicateDates = (() => {
    const byDate = new Map<string, number[]>();
    editingRows.forEach((row, index) => {
      if (!row.date) return;
      byDate.set(row.date, [...(byDate.get(row.date) ?? []), index]);
    });
    return [...byDate.entries()]
      .filter(([, indexes]) => indexes.length > 1)
      .map(([date, indexes]) => {
        const quantities = indexes.map((index) => Number(editingRows[index].demand_qty));
        return {
          date,
          indexes,
          conflicting: new Set(quantities).size > 1,
          quantities
        };
      });
  })();
  const duplicateRowIndexes = new Set(duplicateDates.flatMap((duplicate) => duplicate.indexes));

  // If no job, show empty state per spec
  const showEmptyNoImage = !hasJob;

  return (
    <section
      aria-labelledby="preview-heading"
      className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <h2 id="preview-heading" className="text-h3 font-bold tracking-tight text-brand-700">
          Preview &amp; Extracted Data
        </h2>
        <span
          className={[
            'shrink-0 rounded-full px-3 py-1 text-meta font-medium sm:mt-1',
            imported ? 'bg-brand-700 text-white' : hasJob ? 'bg-brand-700 text-white' : 'bg-brand-100 text-brand-800'
          ].join(' ')}
        >
          {statusLabel}
        </span>
      </div>
      <p className="mt-1 text-control text-slate-500">
        {hasJob ? 'Check each row against the photo before importing.' : 'AI will extract dates and visitor counts from your image.'}
      </p>

      <div className="mt-4 overflow-hidden rounded-lg border border-slate-200">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 bg-brand-700 px-3 py-2.5 sm:px-4">
          <span className="text-body-sm-lg font-semibold text-white">Extracted Data (Preview)</span>
          <span className="text-meta text-brand-100">
            {loadingJob
              ? 'Loading…'
              : hasJob
                ? `${extractedRows.length} rows detected${engine && ENGINE_LABEL[engine] ? ` · ${ENGINE_LABEL[engine]}` : ''}${
                    engine === 'tesseract' && quality?.mean_confidence != null
                      ? ` · OCR quality: ${quality.mean_confidence >= 0.8 ? 'high' : quality.mean_confidence >= 0.6 ? 'medium' : 'low'}`
                      : ''
                  }`
                : '2 columns detected'}
          </span>
        </div>

        <div className="bg-white px-3 pb-4 pt-3 sm:px-4">
          {/* The photo the rows were read from, with each date and count boxed
              where the engine found it. Checking a row against a disconnected
              table is guesswork; this is the comparison the panel is asking for. */}
          {photoUrl && editingRows.length > 0 && (
            <figure className="mb-3">
              <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
                <img
                  src={photoUrl}
                  alt={imageName ? `Photo of ${imageName}` : 'Photo of the uploaded log'}
                  className="block w-full"
                />
                {editingRows.map((row, idx) => {
                  const dateStyle = boxStyle(row.boxes?.date);
                  const countStyle = boxStyle(row.boxes?.demand_qty);
                  if (!dateStyle && !countStyle) return null;
                  return (
                    <span key={idx} aria-hidden="true">
                      {dateStyle && (
                        <span
                          data-ocr-box="date"
                          style={dateStyle}
                          className={[
                            'absolute rounded-sm border-2',
                            activeRow === idx
                              ? 'border-brand-600 bg-brand-400/25'
                              : 'border-brand-400/70 bg-brand-400/10'
                          ].join(' ')}
                        />
                      )}
                      {countStyle && (
                        <span
                          data-ocr-box="demand_qty"
                          style={countStyle}
                          className={[
                            'absolute rounded-sm border-2',
                            activeRow === idx
                              ? 'border-amber-600 bg-amber-400/30'
                              : 'border-amber-400/70 bg-amber-400/15'
                          ].join(' ')}
                        />
                      )}
                    </span>
                  );
                })}
              </div>
              <figcaption className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-fine text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-3 rounded-sm border border-brand-400 bg-brand-400/20" />
                  date
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-3 rounded-sm border border-amber-400 bg-amber-400/20" />
                  visitors
                </span>
                <span>Highlights are approximate once the photo is straightened.</span>
              </figcaption>
            </figure>
          )}

          <div className="flex items-center gap-8 px-1 pb-3 sm:gap-10">
            <span className="flex items-center gap-1.5 text-control font-semibold text-slate-800">
              Date
              <ChevronsUpDownIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={2} />
            </span>
            <span className="flex items-center gap-1.5 text-control font-semibold text-slate-800">
              Visitors
              <ChevronsUpDownIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={2} />
            </span>
          </div>

          {showEmptyNoImage ? (
            <EmptyState size="sm" variant="empty" title="No data extracted yet" description="Upload an image to see extracted dates and visitor counts." />
          ) : loadingJob ? (
            <p role="status" className="py-6 text-center text-body-xs text-slate-500">Fetching extracted rows…</p>
          ) : fetchError ? (
            <EmptyState
              size="sm"
              variant="error"
              title="Extraction failed"
              description={fetchError.message + (fetchError.reference ? ` (ref ${fetchError.reference})` : '')}
              hints={fetchError.reference ? [`Reference ${fetchError.reference}`] : undefined}
              action={{ label: 'Retry', onClick: () => jobId && fetchJob(jobId) }}
              secondaryAction={onClear ? { label: 'Start over', onClick: onClear } : undefined}
            />
          ) : extractedRows.length === 0 && editingRows.length === 0 ? (
            <div className="flex flex-col gap-3">
              <EmptyState
                size="sm"
                variant="empty"
                title="No rows detected"
                description={note ?? 'OCR found no rows. Add them manually and commit.'}
              />
              {/* allow manual row entry when OCR returns nothing */}
              <div className="rounded-lg border border-dashed border-slate-300 px-3 py-3">
                <p className="text-body-xs font-medium text-slate-700">Add rows manually</p>
                <div className="mt-2 flex flex-col gap-2">
                  {editingRows.map((row, idx) => (
                    <div key={idx} className="flex gap-2">
                      <input
                        type="date"
                        value={row.date}
                        onChange={(e) => updateRow(idx, 'date', e.target.value)}
                        className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-body-xs focus:border-brand-500 focus:outline-none"
                      />
                      <input
                        type="number"
                        min={0}
                        value={row.demand_qty}
                        onChange={(e) => updateRow(idx, 'demand_qty', e.target.value)}
                        className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-body-xs focus:border-brand-500 focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={() => setEditingRows((c) => c.filter((_, i) => i !== idx))}
                        className="rounded-md px-2 py-1 text-caption text-red-600 hover:bg-red-50"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() =>
                      setEditingRows((c) => [...c, { date: todayISO(), demand_qty: 0, handAdded: true }])
                    }
                    className="self-start rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-medium text-slate-700 hover:bg-slate-50"
                  >
                    + Add row
                  </button>
                </div>
                <div className="mt-3">
                  <label htmlFor="img-location" className="block text-caption font-semibold text-slate-700">
                    Location for these rows
                  </label>
                  <select
                    id="img-location"
                    value={defaultLocationId}
                    onChange={(e) => setDefaultLocationId(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-body-xs focus:border-brand-500 focus:outline-none"
                  >
                    <option value="">Select location</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </div>
                {commitError && <p className="mt-2 text-caption text-red-600">{commitError.message}{commitError.reference ? ` (ref ${commitError.reference})` : ''}</p>}
              </div>
            </div>
          ) : (              <div className="flex flex-col gap-3">
              {duplicateDates.length > 0 && (
                <p
                  role="note"
                  className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-caption leading-relaxed text-red-800"
                >
                  <span className="font-semibold">
                    {duplicateDates.length} date{duplicateDates.length === 1 ? '' : 's'} repeated
                  </span>{' '}
                  — only one row per day can be imported, so the later one would replace the
                  earlier. {duplicateDates
                    .map((duplicate) =>
                      duplicate.conflicting
                        ? `${duplicate.date} (${duplicate.quantities.join(' / ')})`
                        : duplicate.date
                    )
                    .join(', ')}
                </p>
              )}
              {flaggedRows.length > 0 && (
                <p
                  role="note"
                  className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-caption leading-relaxed text-amber-800"
                >
                  <span className="font-semibold">
                    {flaggedRows.length} of {editingRows.length} row{editingRows.length === 1 ? '' : 's'} to check
                  </span>{' '}
                  — their demand figure was not the only number on the line, so the last one was
                  taken. Compare them with the photo before importing.
                </p>
              )}
              {/* The rows scroll inside the panel so a 100-row photo does not
                  push the confirm controls and the whole page down with it. */}
              <ul
                aria-label="Extracted rows"
                tabIndex={0}
                className="touch-scroll flex max-h-[420px] flex-col divide-y divide-slate-100 overflow-y-auto overscroll-contain rounded-lg border border-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                {editingRows.map((row, idx) => {
                  const note = scoreNote(engine, row);
                  return (
                    <li
                      key={idx}
                      // Pointing at a row points at its place on the photo.
                      onMouseEnter={() => setActiveRow(idx)}
                      onMouseLeave={() => setActiveRow(null)}
                      onFocusCapture={() => setActiveRow(idx)}
                      onBlurCapture={() => setActiveRow(null)}
                      className="flex flex-wrap items-center gap-2 px-3 py-2"
                    >
                      <input
                        type="date"
                        aria-label={`Date for row ${idx + 1}`}
                        value={row.date}
                        onChange={(e) => updateRow(idx, 'date', e.target.value)}
                        className="w-full min-w-[9rem] flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-body-xs tabular-nums text-slate-700 focus:border-brand-500 focus:outline-none"
                      />
                      <input
                        type="number"
                        min={0}
                        aria-label={`Visitors for row ${idx + 1}`}
                        value={row.demand_qty}
                        onChange={(e) => updateRow(idx, 'demand_qty', e.target.value)}
                        className="w-28 rounded-md border border-slate-300 px-2 py-1.5 text-body-xs font-semibold tabular-nums text-slate-900 focus:border-brand-500 focus:outline-none"
                      />
                      {/* The engine's own reading of the line, so a doubtful row is
                          visible before it is imported rather than after. */}
                      <span
                        className={[
                          'shrink-0 rounded-full px-2 py-0.5 text-fine font-medium',
                          note.flagged ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'
                        ].join(' ')}
                      >
                        {note.label}
                      </span>
                      {duplicateRowIndexes.has(idx) && (
                        <span className="shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-fine font-medium text-red-700">
                          Same date twice
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              <div>
                <button
                  type="button"
                  onClick={() =>
                    setEditingRows((c) => [...c, { date: todayISO(), demand_qty: 0, handAdded: true }])
                  }
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-caption font-medium text-slate-700 hover:bg-slate-50"
                >
                  + Add row
                </button>
                <div className="mt-3">
                  <label htmlFor="img-location-2" className="block text-caption font-semibold text-slate-700">
                    Location for these rows
                  </label>
                  <select
                    id="img-location-2"
                    value={defaultLocationId}
                    onChange={(e) => setDefaultLocationId(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-body-xs focus:border-brand-500 focus:outline-none"
                  >
                    <option value="">Select location</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </div>
                {commitError && <p className="mt-2 text-caption text-red-600">{commitError.message}{commitError.reference ? ` (ref ${commitError.reference})` : ''}</p>}
                {note && <p className="mt-2 text-fine text-slate-500">{note}</p>}
              </div>
            </div>
          )}
        </div>
      </div>

      {!hasJob && (
        <p className="mt-4 flex flex-wrap items-center justify-center gap-x-2.5 gap-y-1 rounded-lg border border-dashed border-slate-300 px-4 py-3.5 text-center text-control text-slate-500">
          <FileTextIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} />
          Table will appear here after extraction.
        </p>
      )}

      <button
        type="button"
        disabled={!hasJob || imported || commitLoading}
        onClick={() => setConfirmOpen(true)}
        className="mt-4 inline-flex w-full items-center justify-center gap-2.5 rounded-lg bg-brand-700 px-4 py-3.5 text-h5 font-semibold text-white sm:gap-3 sm:px-5 sm:text-lead transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        <CircleCheckBigIcon className="h-5 w-5" strokeWidth={2} />
        {imported ? 'Imported' : commitLoading ? 'Committing…' : 'Review & Confirm'}
        <ArrowRightIcon className="h-5 w-5" strokeWidth={2} />
      </button>
      {hasJob && !imported && onClear && (
        <button
          type="button"
          onClick={onClear}
          disabled={commitLoading}
          className="mt-2.5 inline-flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-body-xs font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Cancel upload
        </button>
      )}
      <p className="mt-2.5 text-center text-body-xs text-slate-500">
        {hasJob ? 'Review extracted data and confirm import.' : 'Upload an image to enable importing.'}
      </p>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Confirm extracted rows"
        description={imageName ?? jobId ?? ''}
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setConfirmOpen(false)}>
              Keep editing
            </ModalButton>
            <ModalButton onClick={handleCommit} disabled={commitLoading || !defaultLocationId}>
              {commitLoading ? 'Committing…' : `Import ${editingRows.length} rows`}
            </ModalButton>
          </>
        }
      >
        <ul className="flex flex-col divide-y divide-slate-100">
          {editingRows.map((row, idx) => (
            <li key={idx} className="flex justify-between gap-3 py-2 text-body-xs">
              <span className="tabular-nums text-slate-600">{row.date}</span>
              <span className="font-semibold tabular-nums text-slate-900">{row.demand_qty} covers</span>
            </li>
          ))}
          {editingRows.length === 0 && <li className="py-2 text-body-xs text-slate-500">No rows to import.</li>}
        </ul>
        <p className="mt-3 text-caption leading-relaxed text-slate-500">
          Anything already recorded for these dates is reconciled rather than added twice.
        </p>
        {commitError && <p className="mt-2 text-caption text-red-600">{commitError.message}</p>}
        {!defaultLocationId && <p className="mt-2 text-caption text-amber-600">Select a location to commit.</p>}
      </Modal>
    </section>
  );
}
