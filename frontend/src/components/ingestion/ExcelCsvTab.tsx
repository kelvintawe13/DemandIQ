import { useEffect, useRef, useState } from 'react';
import {
  UploadCloudIcon,
  FolderIcon,
  LightbulbIcon,
  TableIcon,
  CircleCheckIcon,
  SheetIcon,
  InfoIcon,
  DownloadIcon,
  FileCheckIcon
} from 'lucide-react';
import { SpreadsheetFile } from '../illustrations/SpreadsheetFile';
import { Modal, ModalButton } from '../ui/Modal';
import { ResponsiveTable } from '../ui/Table';
import { EmptyState } from '../ui/EmptyState';
import { plainStatus } from '../../lib/plainLanguage';
import { useToast } from '../../contexts/ToastContext';
import { api, ApiException } from '../../api/client';

interface PreviewRow {
  [key: string]: string | number;
}

interface FileUploadResult {
  upload_id: string;
  detected_headers: string[];
  preview: PreviewRow[];
  row_count: number;
  status: string;
}

interface MappingResult {
  mapped_rows: number;
  error_count: number;
  errors: Array<{ row_index: number; field?: string | null; message: string }>;
  status: string;
}

interface LocationOption {
  id: string;
  name: string;
}

/** One stored demand row, as `GET /ingest/demand` returns it. */
interface DemandRow {
  id: number;
  location_id: string;
  date: string;
  demand_qty: number;
  upload_id?: string | null;
  upload_filename?: string | null;
}

/**
 * What a finished import left behind.
 *
 * Kept after `reset()` clears the working upload, because the question this
 * answers — "which rows did that file write?" — is asked *after* the commit, and
 * the screen has already emptied itself by then.
 */
interface ImportTrace {
  uploadId: string;
  filename: string;
  rows: DemandRow[];
  loading: boolean;
  error: string | null;
}

/** The API caps the demand list at 50 rows per page. */
const TRACE_LIMIT = 50;

export function ExcelCsvTab() {
  const [isDragging, setIsDragging] = useState(false);
  const [fileName, setFileName] = useState<string>();
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [detectedHeaders, setDetectedHeaders] = useState<string[]>([]);
  const [previewRows, setPreviewRows] = useState<PreviewRow[]>([]);
  const [rowCount, setRowCount] = useState(0);

  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<{ message: string; reference?: string | null; offline?: boolean } | null>(null);

  const [mapping, setMapping] = useState<Record<string, string>>({
    date: '',
    demand_qty: '',
    location: '',
    notes: ''
  });
  const [defaultLocationId, setDefaultLocationId] = useState<string>('');
  const [mappingLoading, setMappingLoading] = useState(false);
  const [mappingError, setMappingError] = useState<{ message: string; reference?: string | null } | null>(null);
  const [mappingResult, setMappingResult] = useState<MappingResult | null>(null);

  const [commitLoading, setCommitLoading] = useState(false);
  const [commitError, setCommitError] = useState<{ message: string; reference?: string | null } | null>(null);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [trace, setTrace] = useState<ImportTrace | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  const [locations, setLocations] = useState<LocationOption[]>([]);
  const [locLoading, setLocLoading] = useState(true);

  const fetchLocations = async () => {
    setLocLoading(true);
    try {
      const res = await api.get<{ data: Array<{ id: string; name: string }> }>('/api/v1/locations', { limit: 50 });
      setLocations((res.data ?? []).map((d) => ({ id: d.id, name: d.name })));
    } catch {
      // silently ignore; fallback to empty
      setLocations([]);
    } finally {
      setLocLoading(false);
    }
  };

  useEffect(() => {
    fetchLocations();
  }, []);

  const reset = () => {
    setUploadId(null);
    setDetectedHeaders([]);
    setPreviewRows([]);
    setRowCount(0);
    setFileName(undefined);
    setMapping({ date: '', demand_qty: '', location: '', notes: '' });
    setMappingResult(null);
    setMappingError(null);
    setCommitError(null);
    setFileError(null);
    if (fileInput.current) fileInput.current.value = '';
  };

  const handleFile = async (file?: File) => {
    if (!file) return;
    // client-side size check (50 MB per spec)
    if (file.size > 50 * 1024 * 1024) {
      setFileError({ message: 'File exceeds 50 MB limit' });
      return;
    }
    setFileName(file.name);
    setFileLoading(true);
    setFileError(null);
    setMappingResult(null);
    setMappingError(null);
    setCommitError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await api.postForm<FileUploadResult>('/api/v1/ingest/file', form);
      setUploadId(res.upload_id);
      setDetectedHeaders(res.detected_headers ?? []);
      setPreviewRows(res.preview ?? []);
      setRowCount(res.row_count ?? 0);
      // auto-populate mapping guesses if headers match common names
      const lowerHeaders = (res.detected_headers ?? []).map((h) => h.toLowerCase());
      const guess = (candidates: string[]) => {
        for (const c of candidates) {
          const idx = lowerHeaders.findIndex((h) => h.includes(c));
          if (idx !== -1) return res.detected_headers[idx];
        }
        return '';
      };
      setMapping({
        date: guess(['date']),
        demand_qty: guess(['demand', 'visitor', 'covers', 'qty']),
        location: guess(['location', 'site', 'store']),
        notes: guess(['note', 'comment'])
      });
      toast(`${file.name} parsed. Check the column mapping.`);
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        const offline = e.error.message.toLowerCase().includes('failed to fetch') || e.status === 0;
        setFileError({ message: e.error.message, reference: e.reference ?? undefined, offline });
      } else if (e instanceof Error && (e.message.includes('Failed to fetch') || e.message.includes('NetworkError'))) {
        setFileError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true });
      } else {
        setFileError({ message: e instanceof Error ? e.message : 'Failed to upload file' });
      }
    } finally {
      setFileLoading(false);
    }
  };

  const doMapping = async () => {
    if (!uploadId) return;
    if (!mapping.date || !mapping.demand_qty) {
      setMappingError({ message: 'Date and demand_qty mapping are required' });
      return;
    }
    setMappingLoading(true);
    setMappingError(null);
    try {
      const body: { column_map: Record<string, string>; default_location_id?: string } = {
        column_map: {
          date: mapping.date,
          demand_qty: mapping.demand_qty,
          ...(mapping.location ? { location: mapping.location } : {}),
          ...(mapping.notes ? { notes: mapping.notes } : {})
        },
        ...(defaultLocationId ? { default_location_id: defaultLocationId } : {})
      };
      const res = await api.post<MappingResult>(`/api/v1/ingest/file/${uploadId}/mapping`, body);
      setMappingResult(res);
      if (res.error_count > 0) {
        toast(`${res.error_count} row(s) have errors. Fix mapping before commit.`, 'error');
      } else {
        toast(`Mapping valid: ${res.mapped_rows} rows ready`);
      }
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        setMappingError({ message: e.error.message, reference: e.reference ?? undefined });
      } else {
        setMappingError({ message: e instanceof Error ? e.message : 'Mapping failed' });
      }
    } finally {
      setMappingLoading(false);
    }
  };

  /**
   * Show the rows this import actually wrote, by asking for them by `upload_id`.
   *
   * Matching on the row's own foreign key is the only honest trace: comparing
   * dates or hunting the filename inside free-text notes would also "find" a
   * manual entry that merely mentioned the file.
   */
  const traceImport = async (uploadId: string, filename: string) => {
    setTrace({ uploadId, filename, rows: [], loading: true, error: null });
    try {
      const res = await api.get<{ data: DemandRow[] }>('/api/v1/ingest/demand', {
        upload_id: uploadId,
        limit: TRACE_LIMIT,
      });
      setTrace({ uploadId, filename, rows: res.data ?? [], loading: false, error: null });
    } catch (e: unknown) {
      setTrace({
        uploadId,
        filename,
        rows: [],
        loading: false,
        error: e instanceof ApiException ? e.error.message : 'Failed to read the imported rows',
      });
    }
  };

  const doCommit = async () => {
    if (!uploadId) return;
    setCommitLoading(true);
    setCommitError(null);
    // Captured before `reset()` blanks them — the trace needs the id and the
    // name after the form has emptied itself.
    const committedId = uploadId;
    const committedName = fileName ?? 'the imported file';
    try {
      const res = await api.post<{ committed: number; reconciled: number; status: string }>(
        `/api/v1/ingest/file/${uploadId}/commit`
      );
      toast(`Import committed: ${res.committed} rows added (${res.reconciled} reconciled).`);
      reset();
      setConfirmOpen(false);
      await traceImport(committedId, committedName);
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        const details = (e.error.details as Array<{ message: string }> | undefined) ?? [];
        const msg = details.length > 0 ? `${e.error.message}: ${details.slice(0, 3).map((d) => d.message).join(', ')}` : e.error.message;
        setCommitError({ message: msg, reference: e.reference ?? undefined });
        toast(e.error.message, 'error');
      } else {
        setCommitError({ message: e instanceof Error ? e.message : 'Commit failed' });
      }
    } finally {
      setCommitLoading(false);
    }
  };

  const canMap = Boolean(uploadId && mapping.date && mapping.demand_qty);
  const canCommit = Boolean(mappingResult && mappingResult.error_count === 0 && uploadId);

  const downloadTemplate = () => {
    const headers = ['date', 'demand_qty', 'location', 'notes'];
    const rows = [
      ['2026-09-18', '112', 'Kigali Heights Bistro', 'Weekend rush'],
      ['2026-09-19', '87', 'Kigali Heights Bistro', ''],
      ['2026-09-20', '134', 'Kigali Heights Bistro', 'Holiday eve'],
    ];
    const esc = (v: string) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [headers.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'demand_template.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('Template downloaded. Fill it and re-upload as CSV or XLSX.');
  };

  return (
    <>
      <input
        ref={fileInput}
        type="file"
        accept=".csv,.xlsx,.xls"
        className="sr-only"
        aria-label="Spreadsheet file"
        onChange={(event) => handleFile(event.target.files?.[0])}
      />

      <section
        aria-labelledby="upload-spreadsheet-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
      >
        <h2 id="upload-spreadsheet-heading" className="flex items-center gap-2.5 text-h3 font-bold tracking-tight text-slate-900">
          <UploadCloudIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
          Upload Spreadsheet
        </h2>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setIsDragging(false);
            handleFile(event.dataTransfer.files?.[0]);
          }}
          className={[
            'mt-4 flex flex-col items-center rounded-xl border-2 border-dashed px-4 py-7 text-center transition-colors duration-150 ease-out sm:px-6',
            isDragging ? 'border-brand-400 bg-brand-50' : 'border-slate-300 bg-white'
          ].join(' ')}
        >
          <SpreadsheetFile className="h-[128px] w-auto max-w-full" />
          {fileName ? (
            <p className="mt-5 text-body-sm-lg font-semibold text-slate-800">{fileName}</p>
          ) : (
            <p className="mt-5 text-body-sm-lg text-slate-600">Drag &amp; drop your Excel or CSV file here</p>
          )}
          <p className="mt-1 text-body-sm-lg text-slate-600">
            or{' '}
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="font-semibold text-brand-600 underline-offset-2 transition-colors duration-150 ease-out hover:text-brand-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              Browse files
            </button>
          </p>
        </div>

        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={fileLoading}
          className="mx-auto mt-5 inline-flex items-center gap-2.5 rounded-lg bg-brand-700 px-5 py-3 text-body font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <FolderIcon className="h-[18px] w-[18px]" strokeWidth={2} />
          {fileLoading ? 'Uploading…' : fileName ? 'Choose a different file' : 'Choose File'}
        </button>

        <p className="mt-4 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-center text-body-xs text-slate-500">
          Accepted formats: .xlsx, .xls, .csv <span aria-hidden="true">•</span> Max size: 50 MB
        </p>

        {fileLoading && <p className="mt-3 text-center text-body-xs text-slate-500">Uploading and parsing…</p>}

        {fileError && (
          <div className="mt-4">
            <EmptyState
              size="sm"
              variant={fileError.offline ? 'offline' : 'error'}
              title={fileError.offline ? 'Backend offline' : 'Upload failed'}
              description={fileError.message + (fileError.reference ? ` (ref ${fileError.reference})` : '')}
              hints={fileError.offline ? ['Backend unreachable. Data saved locally will sync when online.'] : fileError.reference ? [`Reference ${fileError.reference}`] : undefined}
              action={{ label: 'Retry', onClick: () => fileName && fileInput.current?.click() }}
            />
          </div>
        )}

        {uploadId && !fileLoading && !fileError && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <p className="text-meta text-slate-600">
              Detected headers: <span className="font-mono text-slate-800">{detectedHeaders.join(', ')}</span>
            </p>
            <p className="text-caption text-slate-500">{rowCount} rows · {previewRows.length} preview shown</p>
          </div>
        )}

        <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-brand-100 bg-brand-50 px-3.5 py-3">
          <LightbulbIcon className="mt-0.5 h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={1.75} />
          <p className="text-body-sm leading-relaxed text-slate-700">
            <span className="font-semibold">Tip:</span> Make sure your file has columns for Date and Visitors (or Demand).
          </p>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={downloadTemplate}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-meta font-semibold text-slate-700 hover:bg-slate-50"
          >
            <DownloadIcon className="h-3.5 w-3.5" strokeWidth={2} />
            Download template
          </button>
          <span className="text-micro text-slate-500">CSV with headers <code className="rounded bg-slate-100 px-1 py-0.5">date,demand_qty,location,notes</code> + 3 example rows. Works for Excel too.</span>
        </div>
      </section>

      <section
        aria-labelledby="preview-mapping-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
      >
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <h2 id="preview-mapping-heading" className="flex items-center gap-2.5 text-h3 font-bold tracking-tight text-slate-900">
            <TableIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
            Preview &amp; Mapping
          </h2>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-brand-100 px-3 py-1 text-meta font-medium text-brand-800">
            <CircleCheckIcon className="h-3.5 w-3.5" strokeWidth={2} />
            {uploadId ? 'File staged' : 'Awaiting file'}
          </span>
        </div>

        <h3 className="mt-4 text-body font-semibold text-slate-900">File Preview (First 5 Rows)</h3>

        {!uploadId ? (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant="empty"
              title="No preview yet"
              description="Upload a file to preview and map columns"
              hints={['Ensure headers include Date and demand_qty']}
            />
          </div>
        ) : previewRows.length === 0 ? (
          <div className="mt-3">
            <EmptyState size="sm" variant="empty" title="No rows detected" description="The file appears empty or could not be parsed." />
          </div>
        ) : (
          <ResponsiveTable<PreviewRow>
            className="mt-2 rounded-lg border border-slate-200"
            caption="Preview of the rows detected in the uploaded file"
            minWidth="min-w-[420px]"
            columns={detectedHeaders.map((header, index) => ({
              key: header,
              label: header,
              primary: index === 0,
              /* Keeps the spreadsheet grid so the columns below line up with the
                 mapping selects. Card mode strips these borders itself. */
              headerClassName: 'border-r border-slate-200 bg-slate-50 last:border-r-0',
              cellClassName: 'border-r border-slate-200 last:border-r-0',
              render: (row) => String(row[header] ?? '—'),
            }))}
            rows={previewRows}
            rowKey={(_row, index) => String(index)}
          />
        )}

        <h3 className="mt-5 text-body font-semibold text-slate-900">Map Columns</h3>
        <p className="mt-0.5 text-body-sm text-slate-500">Match your file columns to DemandIQ fields.</p>

        <div className="mt-3 flex flex-col gap-2.5">
          {[
            { id: 'date', label: 'Date (Required)' },
            { id: 'demand_qty', label: 'Visitors / Demand (Required)' },
            { id: 'location', label: 'Location (Optional)' },
            { id: 'notes', label: 'Notes (Optional)' }
          ].map((field) => (
            <div key={field.id} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-2 sm:gap-3">
              <label htmlFor={`map-${field.id}`} className="flex items-center gap-1.5 text-control text-slate-700">
                {field.label}
                <InfoIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
              </label>
              <div className="relative">
                <SheetIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <select
                  id={`map-${field.id}`}
                  value={mapping[field.id] ?? ''}
                  onChange={(event) =>
                    setMapping((current) => ({
                      ...current,
                      [field.id]: event.target.value
                    }))
                  }
                  disabled={!uploadId || detectedHeaders.length === 0}
                  className="w-full appearance-none rounded-lg border border-slate-300 bg-white py-2.5 pl-9 pr-9 text-control text-slate-800 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-50"
                >
                  <option value="">Select a header</option>
                  {detectedHeaders.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
                <svg
                  viewBox="0 0 20 20"
                  className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="M6 8l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
            </div>
          ))}
          <div className="grid grid-cols-1 items-center gap-2 sm:grid-cols-2 sm:gap-3">
            <label htmlFor="map-default-location" className="flex items-center gap-1.5 text-control text-slate-700">
              Default location
              <InfoIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            </label>
            <div className="relative">
              <select
                id="map-default-location"
                value={defaultLocationId}
                onChange={(e) => setDefaultLocationId(e.target.value)}
                disabled={locLoading}
                className="w-full appearance-none rounded-lg border border-slate-300 bg-white px-3 py-2.5 pr-9 text-control text-slate-800 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-50"
              >
                <option value="">Use location column</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
              <svg
                viewBox="0 0 20 20"
                className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path d="M6 8l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>
        </div>

        <button
          type="button"
          onClick={doMapping}
          disabled={!canMap || mappingLoading}
          className="mt-4 inline-flex w-full items-center justify-center gap-2.5 rounded-lg bg-slate-900 px-4 py-2.5 text-control font-semibold text-white transition-colors duration-150 ease-out hover:bg-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <TableIcon className="h-4 w-4" strokeWidth={2} />
          {mappingLoading ? 'Mapping…' : 'Validate mapping'}
        </button>

        {mappingError && (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant="error"
              title="Mapping failed"
              description={mappingError.message + (mappingError.reference ? ` (ref ${mappingError.reference})` : '')}
              hints={mappingError.reference ? [`Reference ${mappingError.reference}`] : undefined}
            />
          </div>
        )}

        {mappingResult && (
          <div className="mt-3 rounded-lg border border-slate-200 px-3 py-3">
            <p className="text-body-xs font-medium text-slate-800">
              Mapped {mappingResult.mapped_rows} row(s), {mappingResult.error_count} error(s)
            </p>
            {mappingResult.errors.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1">
                {mappingResult.errors.map((e, i) => (
                  <li key={i} className="text-caption text-red-600">
                    Row {e.row_index}
                    {e.field ? ` · ${e.field}` : ''}: {e.message}
                  </li>
                ))}
              </ul>
            )}
            {/* Mapping statuses are machine tokens; `awaiting_mapping` is not a
                sentence. plainStatus is the shared vocabulary for them. */}
            <p className="mt-1 text-caption text-slate-500">Status: {plainStatus(mappingResult.status)}</p>
          </div>
        )}

        {commitError && (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant="error"
              title="Commit failed"
              description={commitError.message + (commitError.reference ? ` (ref ${commitError.reference})` : '')}
              hints={commitError.reference ? [`Reference ${commitError.reference}`] : undefined}
            />
          </div>
        )}

        {!uploadId && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-brand-100 bg-brand-50 px-3.5 py-3">
            <TableIcon className="mt-0.5 h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={1.75} />
            <p className="text-body-sm leading-relaxed text-slate-700">
              <span className="font-semibold">Upload a file to preview and map columns</span>
              <br />
              Once uploaded, you&apos;ll see a preview of your data and map columns to DemandIQ fields.
            </p>
          </div>
        )}

        <button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={!canCommit}
          className="mt-4 inline-flex w-full items-center justify-center gap-2.5 rounded-lg bg-brand-700 px-4 py-3.5 text-h5 font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <CircleCheckIcon className="h-5 w-5" strokeWidth={2} />
          Review &amp; Confirm
        </button>
      </section>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Confirm import"
        description={fileName ?? 'spreadsheet'}
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setConfirmOpen(false)}>
              Back to mapping
            </ModalButton>
            <ModalButton onClick={doCommit} disabled={commitLoading || !canCommit}>
              {commitLoading ? 'Importing…' : `Import ${mappingResult?.mapped_rows ?? 0} rows`}
            </ModalButton>
          </>
        }
      >
        <ul className="flex flex-col gap-2">
          {Object.entries(mapping)
            .filter(([, v]) => Boolean(v))
            .map(([field, column]) => (
              <li key={field} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800">
                <code className="font-mono text-caption text-slate-600 dark:text-slate-300">{String(column)}</code>
                <span className="text-fine text-slate-500">maps to</span>
                <code className="font-mono text-caption font-semibold text-brand-800">{field}</code>
              </li>
            ))}
        </ul>
        <p className="mt-3 text-caption leading-relaxed text-slate-500">
          {mappingResult ? `${mappingResult.mapped_rows} rows will be added. Days that already exist are reconciled, never double counted.` : 'Validate mapping first.'}
        </p>
        {canCommit ? null : <p className="mt-2 text-caption text-amber-600">Fix mapping errors before committing.</p>}
      </Modal>

      {/*
        The trace lives outside the upload card on purpose: that card resets
        itself the moment a commit lands, and "which rows did this file write?"
        is asked *after* the commit, when the upload id is gone from state.
      */}
      {trace && (
        <section
          aria-labelledby="import-trace-heading"
          className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
        >
          <h2
            id="import-trace-heading"
            className="flex items-center gap-2.5 text-h3 font-bold tracking-tight text-slate-900"
          >
            <FileCheckIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
            Import trace
          </h2>
          <p className="mt-1 text-control text-slate-500">
            The demand rows stored from{' '}
            <span className="font-medium text-slate-700">{trace.filename}</span>. Each row names the
            upload it came from, so this is the file's own footprint in{' '}
            <code className="rounded bg-slate-100 px-1 py-0.5 text-fine">demand_observations</code> —
            not a guess from the dates.
          </p>

          {trace.loading ? (
            <p className="mt-3 text-body-xs text-slate-500">Reading the imported rows…</p>
          ) : trace.error ? (
            <div className="mt-3">
              <EmptyState
                size="sm"
                variant="error"
                title="Could not read the imported rows"
                description={trace.error}
                action={{ label: 'Retry', onClick: () => void traceImport(trace.uploadId, trace.filename) }}
              />
            </div>
          ) : trace.rows.length === 0 ? (
            /* A commit that reconciled every day still wrote nothing new, and a
               truncated page can read as zero — say which happened. */
            <div className="mt-3">
              <EmptyState
                size="sm"
                variant="empty"
                title="No rows from this import"
                description="Every day in the file already had a stored value, so the import reconciled them instead of adding rows."
              />
            </div>
          ) : (
            <>
              <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-xs text-slate-600">
                <span className="font-semibold text-slate-900">
                  {trace.rows.length} row{trace.rows.length === 1 ? '' : 's'}
                </span>
                <span aria-hidden="true">·</span>
                <span className="tabular-nums">
                  {trace.rows[trace.rows.length - 1].date} → {trace.rows[0].date}
                </span>
                {trace.rows.length === TRACE_LIMIT && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span className="text-slate-500">showing the most recent {TRACE_LIMIT}</span>
                  </>
                )}
              </p>
              <ResponsiveTable<DemandRow>
                className="mt-2"
                caption={`Demand rows written by ${trace.filename}`}
                minWidth="min-w-[420px]"
                maxHeight="max-h-[320px]"
                rows={trace.rows}
                rowKey={(row) => String(row.id)}
                columns={[
                  {
                    key: 'date',
                    label: 'Date',
                    primary: true,
                    render: (row) => <span className="tabular-nums">{row.date}</span>,
                  },
                  {
                    key: 'visitors',
                    label: 'Visitors',
                    render: (row) => (
                      <span className="font-semibold tabular-nums">
                        {row.demand_qty.toLocaleString('en-US')}
                      </span>
                    ),
                  },
                  {
                    key: 'location',
                    label: 'Location',
                    render: (row) =>
                      locations.find((l) => l.id === row.location_id)?.name ?? row.location_id,
                  },
                  {
                    key: 'source',
                    label: 'From',
                    render: (row) => row.upload_filename ?? '—',
                  },
                ]}
              />
            </>
          )}

          <button
            type="button"
            onClick={() => setTrace(null)}
            className="mt-3 self-start rounded text-body-xs font-semibold text-slate-500 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            Dismiss trace
          </button>
        </section>
      )}
    </>
  );
}
