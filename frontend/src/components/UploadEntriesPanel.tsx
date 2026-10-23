import { useCallback, useEffect, useState } from 'react';
import { CheckIcon, PencilIcon, TrashIcon, XIcon } from 'lucide-react';
import { api, ApiException } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { ConfirmDialog } from './ui/ConfirmDialog';
import { EmptyState } from './ui/EmptyState';
import { Spinner } from './ui/LoadingState';
import { TableShell, TD, TRow } from './ui/Table';

/**
 * The demand entries one import wrote, editable in place.
 *
 * Recent uploads used to be a dead end for anything already committed: the row
 * named a file and a count, and the numbers it produced could only be reached
 * from the hand-entry tab, which shows the latest five for one location. This
 * panel answers the question an operator actually has when an import looks
 * wrong — "which days did this file write, and can I fix one?" — without
 * leaving the screen.
 *
 * Edits go through the same `PATCH /ingest/demand/{id}` the hand-entry tab uses,
 * so quantity, future-date and `(location, date)` uniqueness rules are enforced
 * once, in the API.
 */

export interface EntriesUpload {
  id: string;
  filename: string;
  status: string;
  row_count: number;
  has_source?: boolean;
}

interface DemandEntry {
  id: number;
  date: string;
  demand_qty: number;
  location_id: string;
  notes?: string | null;
}

interface UploadEntriesPanelProps {
  upload: EntriesUpload;
  onClose: () => void;
  /** Called after an entry changes, so the upload's row counts can be refreshed. */
  onChanged: () => void;
}

const PAGE = 50;

/** `YYYY-MM-DD` in local time, matching what the API expects for `date`. */
const todayISO = () => {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
};

export function UploadEntriesPanel({ upload, onClose, onChanged }: UploadEntriesPanelProps) {
  const { toast } = useToast();
  const [entries, setEntries] = useState<DemandEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locations, setLocations] = useState<Record<string, string>>({});

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDate, setEditDate] = useState('');
  const [editQty, setEditQty] = useState('');
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  /** Entry awaiting confirmation; deletion of stored history is permanent. */
  const [confirmingDelete, setConfirmingDelete] = useState<DemandEntry | null>(null);
  /* The stored page, shown beside the entries. Correcting an OCR value means
     comparing it with what was written, and switching panels to do that loses
     the row being edited. */
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoLoading, setPhotoLoading] = useState(false);
  const [photoError, setPhotoError] = useState(false);

  const load = useCallback(
    async (offset: number) => {
      const res = await api.get<{ data: DemandEntry[] }>('/api/v1/ingest/demand', {
        upload_id: upload.id,
        limit: PAGE,
        offset,
      });
      return res.data ?? [];
    },
    [upload.id],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await load(0);
      setEntries(rows);
      setHasMore(rows.length === PAGE);
    } catch (e: unknown) {
      setError(e instanceof ApiException ? e.error.message : 'Could not load the entries for this import');
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Names, not ids: the entries table is meant to be readable next to the file
  // that produced it. A failure here just falls back to showing the id.
  useEffect(() => {
    setPhotoUrl(null);
    setPhotoError(false);
    if (!upload.has_source) {
      setPhotoLoading(false);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    const loadPhoto = async () => {
      setPhotoLoading(true);
      try {
        const blob = await api.blob(`/api/v1/ingest/uploads/${upload.id}/source`);
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setPhotoUrl(created);
      } catch {
        if (!cancelled) setPhotoError(true);
      } finally {
        if (!cancelled) setPhotoLoading(false);
      }
    };
    void loadPhoto();
    return () => {
      cancelled = true;
      // Released when the panel closes or another import is opened, so a 5 MB
      // photo does not stay pinned in memory for the rest of the session.
      if (created) URL.revokeObjectURL(created);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upload.id, upload.has_source]);

  useEffect(() => {
    let cancelled = false;
    const loadNames = async () => {
      try {
        const res = await api.get<{ data: Array<{ id: string; name: string }> }>(
          '/api/v1/locations',
          { limit: 50 },
        );
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const loc of res.data ?? []) map[loc.id] = loc.name;
        setLocations(map);
      } catch {
        // Names are a convenience: the table falls back to the location id.
      }
    };
    void loadNames();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const rows = await load(entries.length);
      setEntries((prev) => [...prev, ...rows]);
      setHasMore(rows.length === PAGE);
    } catch (e: unknown) {
      toast(e instanceof ApiException ? e.error.message : 'Could not load more entries', 'error');
    } finally {
      setLoadingMore(false);
    }
  };

  const startEdit = (entry: DemandEntry) => {
    setEditingId(entry.id);
    setEditDate(entry.date);
    setEditQty(String(entry.demand_qty));
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDate('');
    setEditQty('');
  };

  const saveEdit = async () => {
    if (editingId == null) return;
    const qty = Number(editQty);
    if (editQty.trim() === '' || !Number.isFinite(qty) || qty < 0) {
      toast('Enter a whole number of visitors', 'error');
      return;
    }
    if (!editDate) {
      toast('A date is required', 'error');
      return;
    }
    if (editDate > todayISO()) {
      toast('Date cannot be in the future', 'error');
      return;
    }
    setSaving(true);
    try {
      await api.patch(`/api/v1/ingest/demand/${editingId}`, {
        date: editDate,
        demand_qty: Math.trunc(qty),
      });
      toast('Entry updated', 'success');
      cancelEdit();
      await refresh();
      onChanged();
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Could not update the entry', 'error');
    } finally {
      setSaving(false);
    }
  };

  const deleteEntry = async (entry: DemandEntry) => {
    setDeletingId(entry.id);
    try {
      await api.delete(`/api/v1/ingest/demand/${entry.id}`);
      toast('Entry deleted', 'success');
      await refresh();
      onChanged();
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Could not delete the entry', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const locationName = (id: string) => locations[id] ?? id;

  return (
    <section
      aria-labelledby="import-entries-heading"
      className="mt-3 rounded-lg border border-slate-200 bg-white p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <h3 id="import-entries-heading" className="truncate text-body-sm font-semibold text-slate-900">
            Entries from {upload.filename}
          </h3>
          <p className="text-caption text-slate-500">
            {upload.status === 'committed'
              ? 'These rows are in demand history. Editing one rewrites the stored value.'
              : 'This import is not committed yet — entries appear here once it is.'}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close entries"
          onClick={onClose}
          className="tap-expand rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
        >
          <XIcon className="h-4 w-4" strokeWidth={1.9} />
        </button>
      </div>

      {/* Side by side on a wide screen, stacked on a phone. The photo is the
          reference the operator is checking the rows against, so it belongs
          next to them rather than in a panel that replaces them. */}
      <div
        className={
          photoLoading || photoUrl || photoError
            ? 'mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2'
            : 'mt-3'
        }
      >
      <div className="min-w-0">
        {loading ? (
          <div className="flex items-center justify-center gap-2.5 py-6">
            <Spinner className="h-4 w-4" />
            <span className="text-meta text-slate-600">Loading entries…</span>
          </div>
        ) : error ? (
          <div>
            <EmptyState size="sm" variant="error" title="Could not load entries" description={error} />
          </div>
        ) : entries.length === 0 ? (
          <div>
            <EmptyState
              size="sm"
              variant="empty"
              title="No entries yet"
              description={
                upload.status === 'committed'
                  ? 'This import wrote no rows — every row failed or was skipped.'
                  : 'Commit this import to write its rows into demand history.'
              }
            />
          </div>
        ) : (
          <>
            <div>
              <TableShell
                caption={`Demand entries from ${upload.filename}`}
                minWidth="min-w-[520px]"
                columns={['Date', 'Visitors', 'Location', '']}
              >
                {entries.map((entry) => {
                  const editing = editingId === entry.id;
                  return (
                    <TRow key={entry.id}>
                      <TD scope="row">
                        {editing ? (
                          <input
                            type="date"
                            aria-label={`Date for the entry of ${entry.date}`}
                            value={editDate}
                            max={todayISO()}
                            onChange={(e) => setEditDate(e.target.value)}
                            className="w-[135px] rounded-md border border-slate-300 px-2 py-1 text-meta"
                          />
                        ) : (
                          <span className="tabular-nums">{entry.date}</span>
                        )}
                      </TD>
                      <TD>
                        {editing ? (
                          <input
                            type="number"
                            min={0}
                            aria-label={`Visitors for the entry of ${entry.date}`}
                            value={editQty}
                            onChange={(e) => setEditQty(e.target.value)}
                            className="w-[90px] rounded-md border border-slate-300 px-2 py-1 text-meta tabular-nums"
                          />
                        ) : (
                          <span className="tabular-nums">{entry.demand_qty}</span>
                        )}
                      </TD>
                      <TD>
                        <span className="text-slate-600">{locationName(entry.location_id)}</span>
                      </TD>
                      <TD className="pr-0">
                        <div className="flex items-center justify-end gap-1">
                          {editing ? (
                            <>
                              <button
                                type="button"
                                aria-label={`Save the entry of ${entry.date}`}
                                onClick={() => void saveEdit()}
                                disabled={saving}
                                className="tap-expand rounded-md p-1.5 text-brand-700 hover:bg-brand-50 disabled:opacity-50"
                              >
                                <CheckIcon className="h-4 w-4" strokeWidth={2} />
                              </button>
                              <button
                                type="button"
                                aria-label={`Cancel editing the entry of ${entry.date}`}
                                onClick={cancelEdit}
                                className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100"
                              >
                                <XIcon className="h-4 w-4" strokeWidth={2} />
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                type="button"
                                aria-label={`Edit the entry of ${entry.date}`}
                                onClick={() => startEdit(entry)}
                                className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-brand-700"
                              >
                                <PencilIcon className="h-4 w-4" strokeWidth={1.9} />
                              </button>
                              <button
                                type="button"
                                aria-label={`Delete the entry of ${entry.date}`}
                                onClick={() => setConfirmingDelete(entry)}
                                disabled={deletingId === entry.id}
                                className="tap-expand rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-red-600 disabled:opacity-50"
                              >
                                <TrashIcon className="h-4 w-4" strokeWidth={1.9} />
                              </button>
                            </>
                          )}
                        </div>
                      </TD>
                    </TRow>
                  );
                })}
              </TableShell>
            </div>
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-caption text-slate-500">
                {entries.length} {entries.length === 1 ? 'entry' : 'entries'} shown
              </span>
              {hasMore && (
                <button
                  type="button"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                  className="rounded-lg border border-slate-300 px-3 py-1 text-meta font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {loadingMore ? 'Loading…' : 'Show more'}
                </button>
              )}
            </div>
          </>
        )}
      </div>

        {(photoLoading || photoUrl || photoError) && (
          <figure className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
            <figcaption className="truncate text-caption font-medium text-slate-600">
              Original photo · {upload.filename}
            </figcaption>
            {photoLoading ? (
              <div className="flex items-center justify-center gap-2 py-10">
                <Spinner className="h-4 w-4" />
                <span className="text-meta text-slate-600">Loading photo…</span>
              </div>
            ) : photoError ? (
              <p className="py-8 text-center text-caption text-slate-500">
                The stored photo could not be loaded. The entries are still editable.
              </p>
            ) : photoUrl ? (
              <a
                href={photoUrl}
                target="_blank"
                rel="noreferrer"
                title={`Open ${upload.filename} full size`}
                className="mt-2 block"
              >
                <img
                  src={photoUrl}
                  alt={`Original page: ${upload.filename}`}
                  className="mx-auto max-h-[420px] w-auto max-w-full rounded border border-slate-200 bg-white object-contain"
                />
              </a>
            ) : null}
          </figure>
        )}
      </div>

      <ConfirmDialog
        open={Boolean(confirmingDelete)}
        onClose={() => setConfirmingDelete(null)}
        onConfirm={() => {
          if (confirmingDelete) void deleteEntry(confirmingDelete);
        }}
        title={`Delete the entry for ${confirmingDelete?.date ?? 'this day'}?`}
        body={
          confirmingDelete
            ? `${confirmingDelete.demand_qty} visitors recorded on ${confirmingDelete.date} will be removed from demand history permanently. The import row stays.`
            : ''
        }
        confirmLabel="Delete entry"
        tone="danger"
      />
    </section>
  );
}
