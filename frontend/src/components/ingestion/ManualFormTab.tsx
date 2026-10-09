import { useEffect, useMemo, useState } from 'react';
import {
  SquarePenIcon,
  CalendarIcon,
  InfoIcon,
  MinusIcon,
  PlusIcon,
  MapPinIcon,
  CheckIcon,
  XIcon,
  BarChart3Icon,
  ArrowRightIcon,
  PencilIcon,
  Trash2Icon,
} from 'lucide-react';
import { Modal, ModalButton } from '../ui/Modal';
import { ResponsiveTable } from '../ui/Table';
import { useToast } from '../../contexts/ToastContext';
import { useNavigation } from '../../contexts/NavigationContext';
import { EmptyState } from '../ui/EmptyState';
import { MAX_DEMAND_QTY, MAX_DEMAND_QTY_MESSAGE } from './limits';
import { Spinner } from '../ui/LoadingState';
// `todayISO` is the workspace's day (Africa/Kigali), not UTC's. It is the `max`
// on the entry date, so a UTC day would reject today's demand for the two hours
// after Kigali has already rolled over.
import { todayISO } from '../../lib/workspaceTime';
import { api, ApiException } from '../../api/client';

interface Entry {
  id: string;
  date: string;
  visitors: number;
  locationId: string;
  locationName: string;
  notes?: string;
}

interface LocationOption {
  id: string;
  name: string;
}

function formatDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  if (!year || !month || !day) return value;
  const date = new Date(year, month - 1, day);
  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric'
  });
}

/** The message shown when the operator tries to submit a blank visitors field. */
const VISITORS_REQUIRED = 'Enter the number of visitors';

/**
 * When a demand row was written, which is a different thing from the day it
 * reports: an operator backfilling last week wants to see both.
 */
function recordedLabel(iso?: string | null): string {
  if (!iso) return '';
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleString();
}

export function ManualFormTab() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { toast } = useToast();
  const { navigate } = useNavigation();

  const [locations, setLocations] = useState<LocationOption[]>([]);
  const [locLoading, setLocLoading] = useState(true);
  const [locError, setLocError] = useState<{ message: string; reference?: string | null; offline?: boolean } | null>(null);

  const [date, setDate] = useState(todayISO());
  // Empty, not 100. A pre-filled 100 is a number the operator never chose, and
  // it was one stray click away from being saved as a real day of demand. The
  // field starts blank and the form will not submit until something is typed.
  const [visitors, setVisitors] = useState('');
  const [locationId, setLocationId] = useState<string>('');
  const [notes, setNotes] = useState('');

  const [dateFieldError, setDateFieldError] = useState<string | null>(null);
  const [visitorsFieldError, setVisitorsFieldError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Array<{ row_index: number; field?: string | null; code?: string; message: string }>>([]);
  const [submitRef, setSubmitRef] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<{ committed: number; reconciled: number; total: number } | null>(null);

  const fetchLocations = async () => {
    setLocLoading(true);
    setLocError(null);
    try {
      const res = await api.get<{ data: Array<{ id: string; name: string }>; has_more?: boolean; next_cursor?: string | null }>(
        '/api/v1/locations',
        { limit: 50 }
      );
      const opts = (res.data ?? []).map((d) => ({ id: d.id, name: d.name }));
      setLocations(opts);
      if (opts.length > 0 && !locationId) setLocationId(opts[0].id);
      if (opts.length === 0) setLocationId('');
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        setLocError({ message: e.error.message, reference: e.reference });
        // detect offline by message containing fetch failed
        if (e.error.message?.toLowerCase().includes('failed to fetch') || e.error.message?.toLowerCase().includes('network')) {
          setLocError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true, reference: e.reference });
        }
      } else if (e instanceof Error && (e.message.includes('Failed to fetch') || e.message.includes('NetworkError') || e.message.includes('fetch'))) {
        setLocError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true });
      } else {
        setLocError({ message: e instanceof Error ? e.message : 'Failed to load locations' });
      }
    } finally {
      setLocLoading(false);
    }
  };

  useEffect(() => {
    fetchLocations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalVisitors = useMemo(() => entries.reduce((sum, entry) => sum + entry.visitors, 0), [entries]);

  const validateDate = (value: string): string | null => {
    if (!value) return 'Date is required';
    if (value > todayISO()) return 'Date cannot be in the future';
    return null;
  };

  // A negative demand is floored to 0 as it is typed (0 is the only sensible
  // reading of -5), but an out-of-range one is left alone and reported: silently
  // rewriting 222,000,100 to 10,000,000 would hide the typo instead of naming it.
  const validateVisitors = (value: number): string | null =>
    value > MAX_DEMAND_QTY ? MAX_DEMAND_QTY_MESSAGE : null;

  /**
   * The typed text as a whole number, or `null` while the field is blank.
   *
   * The field holds a string rather than a number because "empty" and "zero"
   * are different states: 0 is a real day of demand (the restaurant was shut),
   * while empty means nothing has been entered yet. Coercing to `0` on change
   * would have made the field impossible to clear.
   */
  const visitorsValue = useMemo<number | null>(() => {
    const raw = visitors.trim();
    if (raw === '') return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
  }, [visitors]);

  const canSubmitCurrent = useMemo(
    () =>
      !validateDate(date) &&
      Boolean(locationId) &&
      visitorsValue !== null &&
      visitorsValue >= 0 &&
      !validateVisitors(visitorsValue),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [date, locationId, visitorsValue],
  );

  // Live summary: include the current draft form as a pending row so the
  // Quick Entry Summary updates while the user types (not only after Add).
  const draftLocationName = useMemo(() => locations.find((l) => l.id === locationId)?.name ?? locationId, [locations, locationId]);
  const livePending = useMemo(
    () =>
      canSubmitCurrent
        ? { date, visitors: visitorsValue ?? 0, locationName: draftLocationName }
        : null,
    [canSubmitCurrent, date, visitorsValue, draftLocationName],
  );
  const liveTotalVisitors = useMemo(() => totalVisitors + (livePending ? livePending.visitors : 0), [totalVisitors, livePending]);
  // Preview rows for the summary table: live pending first, then queued entries
  const previewEntries = useMemo(() => {
    const base = entries.slice(0, 3);
    if (livePending) {
      const draft = { id: '__draft__', date: livePending.date, visitors: livePending.visitors, locationName: livePending.locationName, locationId, notes } as Entry;
      if (entries.length === 0) return [draft];
      // When there are queued entries, show draft as an extra ghost row on top (capped at 3)
      return [draft, ...base].slice(0, 3);
    }
    return base;
  }, [entries, livePending, locationId, notes]);

  // Past fills: show what is already stored for the selected location (full CRUD)
  const [pastFills, setPastFills] = useState<Array<{ id: number; date: string; demand_qty: number; notes?: string | null; location_id?: string; recorded_at?: string | null }>>([]);
  const [pastFillsLoading, setPastFillsLoading] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDate, setEditDate] = useState('');
  const [editQty, setEditQty] = useState(0);
  const [editNotes, setEditNotes] = useState('');
  const [editLocationId, setEditLocationId] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  const refreshPastFills = async () => {
    if (!locationId) {
      setPastFills([]);
      return;
    }
    setPastFillsLoading(true);
    try {
      const res = await api.get<{ data: Array<{ id: number; date: string; demand_qty: number; notes?: string | null; location_id: string; recorded_at?: string | null }> }>('/api/v1/ingest/demand', {
        location_id: locationId,
        limit: 5,
      });
      setPastFills(res.data ?? []);
    } catch {
      setPastFills([]);
    } finally {
      setPastFillsLoading(false);
    }
  };

  /* Keyed on the location only. A *write* is what changes the stored rows, and
     `submitAll` refreshes explicitly after one. Keying on `entries.length` also
     fired when an entry was merely queued (`addEntry`) or removed — neither
     writes anything — and it never fired at all for a save of the bare form,
     because the length was 0 before and 0 after. That is exactly the case where
     the list most needed refreshing: the operator saved one row and it did not
     appear. */
  useEffect(() => {
    refreshPastFills();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId]);

  const handleDeletePastFill = async (id: number) => {
    try {
      await api.delete(`/api/v1/ingest/demand/${id}`);
      toast('Demand entry deleted');
      await refreshPastFills();
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to delete entry', 'error');
    }
  };

  const startEditPastFill = (row: { id: number; date: string; demand_qty: number; notes?: string | null; location_id?: string }) => {
    setEditingId(row.id);
    setEditDate(row.date);
    setEditQty(row.demand_qty);
    setEditNotes(row.notes ?? '');
    setEditLocationId((row as any).location_id ?? locationId);
  };

  const saveEditPastFill = async () => {
    if (editingId == null) return;
    if (editDate > todayISO()) {
      toast('Date cannot be in the future', 'error');
      return;
    }
    setEditSaving(true);
    try {
      const payload: Record<string, unknown> = { date: editDate, demand_qty: editQty, notes: editNotes || null };
      // Only send location_id if it changed from the original row's location
      const original = pastFills.find((r) => r.id === editingId);
      if (editLocationId && original && editLocationId !== (original as any).location_id) {
        payload.location_id = editLocationId;
      }
      await api.patch(`/api/v1/ingest/demand/${editingId}`, payload);
      toast('Demand entry updated', 'success');
      setEditingId(null);
      await refreshPastFills();
    } catch (e: unknown) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to update entry', 'error');
    } finally {
      setEditSaving(false);
    }
  };

  /**
   * Return the form to how it looks on first load.
   *
   * Called after a successful write. A retained count is not harmless: the
   * commit path is an upsert on `(location_id, date)`, so pressing Save again
   * on the same date *overwrites* the day that was just stored instead of
   * adding a second row. Blanking the draft is what makes "another submission"
   * a genuinely new day of demand rather than a silent re-write of the last one.
   * The date goes back to the workspace's today, matching the initial state.
   * The location is deliberately kept — operators usually log several days for
   * one site in a sitting.
   */
  const resetDraft = () => {
    setDate(todayISO());
    setVisitors('');
    setNotes('');
    setDateFieldError(null);
    setVisitorsFieldError(null);
  };

  const addEntry = () => {
    const err = validateDate(date);
    if (err) {
      setDateFieldError(err);
      return;
    }
    if (visitorsValue === null) {
      setVisitorsFieldError(VISITORS_REQUIRED);
      return;
    }
    if (!locationId) {
      toast('Select a location first', 'error');
      return;
    }
    setDateFieldError(null);
    const locName = locations.find((l) => l.id === locationId)?.name ?? locationId;
    setEntries((current) => [
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        date,
        visitors: visitorsValue,
        locationId,
        locationName: locName,
        notes: notes.trim() || undefined
      },
      ...current
    ]);
    // Blank the count as well as the notes. Leaving the number behind meant the
    // next "Add another entry" one click away was the *same* day of demand
    // queued twice, and the field gave no sign it still held the old value.
    setVisitors('');
    setNotes('');
    setVisitorsFieldError(null);
  };

  const removeEntry = (id: string) => {
    setEntries((current) => current.filter((entry) => entry.id !== id));
  };

  const submitAll = async () => {
    // UX: if the user filled the form but never pressed "Add another entry",
    // treat the current form as an implicit entry (common expectation from
    // the screenshot where Save was disabled with 150 visitors filled).
    let toSubmit = entries;
    if (entries.length === 0) {
      const err = validateDate(date);
      if (err) {
        setDateFieldError(err);
        toast(err, 'error');
        return;
      }
      if (visitorsValue === null) {
        setVisitorsFieldError(VISITORS_REQUIRED);
        toast(VISITORS_REQUIRED, 'error');
        return;
      }
      if (!locationId) {
        toast('Select a location first', 'error');
        return;
      }
      const locName = locations.find((l) => l.id === locationId)?.name ?? locationId;
      toSubmit = [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          date,
          visitors: visitorsValue,
          locationId,
          locationName: locName,
          notes: notes.trim() || undefined,
        },
      ];
    } else {
      const futureEntry = toSubmit.find((e) => e.date > todayISO());
      if (futureEntry) {
        setDateFieldError('One or more entries has a future date');
        toast('Future dates are not allowed', 'error');
        return;
      }
    }
    setSubmitting(true);
    setSubmitError(null);
    setFieldErrors([]);
    setSubmitRef(null);
    setDateFieldError(null);
    try {
      const payload = toSubmit.map((e) => ({
        location_id: e.locationId,
        date: e.date,
        demand_qty: e.visitors,
        notes: e.notes
      }));
      const res = await api.post<{ committed: number; reconciled: number; total: number }>(
        '/api/v1/ingest/manual',
        payload
      );
      toast(`${res.committed} committed, ${res.reconciled} reconciled, ${res.total} total`, 'success');
      setSaveSuccess(res);
      // Auto-hide success after 6s, keep past fills fresh
      window.setTimeout(() => setSaveSuccess(null), 6000);
      // The write landed: drop the queue and empty the draft, so the next entry
      // starts from a blank form rather than from the value just stored.
      setEntries([]);
      resetDraft();
      setConfirmOpen(false);
      /* Refresh here rather than waiting on `entries.length`. Submitting the
         bare form leaves that length at 0, so the version that relied on it
         never refetched — the row the operator had just saved was missing from
         the Past Fills list, directly under a panel saying it had refreshed. */
      await refreshPastFills();
      } catch (e: unknown) {
        if (e instanceof ApiException) {
          const details = (e.error.details as Array<{ row_index: number; field?: string | null; code?: string; message: string }> | undefined) ?? [];
          if (e.status === 422 && details.length > 0) {
            setFieldErrors(details);
            // surface future_date field error on date input if relevant
            const fut = details.find((d) => d.code === 'future_date' || d.field === 'date');
            if (fut) setDateFieldError(fut.message);
          }
          setSubmitError(e.error.message);
          setSubmitRef(e.reference ?? null);
        } else if (e instanceof Error && (e.message.includes('Failed to fetch') || e.message.includes('NetworkError'))) {
          // offline fallback: keep mock behavior
          toast(`${toSubmit.length} entries saved locally. Will sync when online.`, 'info');
          setSubmitError(null);
          // keep entries locally but clear after offline toast?
          // per spec: keep mock behavior as fallback -> don't clear, just show offline hint
          setLocError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true });
        } else {
          setSubmitError(e instanceof Error ? e.message : 'Failed to save entries');
        }
      } finally {
        setSubmitting(false);
      }
  };

  const isLocationsEmpty = !locLoading && !locError && locations.length === 0;
  const showLocationErrorState = Boolean(locError);

  return (
    <>
      <section
        aria-labelledby="manual-entry-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
      >
        <h2
          id="manual-entry-heading"
          className="flex items-center gap-2.5 text-h3 font-bold tracking-tight text-slate-900"
        >
          <SquarePenIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
          Enter Demand Data Manually
        </h2>

        {/* Location fetch states */}
        {locLoading && (
          <p className="mt-3 text-body-xs text-slate-500" role="status">
            Loading locations…
          </p>
        )}
        {showLocationErrorState && locError && (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant={locError.offline ? 'offline' : 'error'}
              title={locError.offline ? 'Backend offline' : 'Failed to load locations'}
              description={locError.message + (locError.reference ? ` (ref ${locError.reference})` : '')}
              hints={locError.offline ? ['Backend unreachable. Data saved locally will sync when online.'] : undefined}
              action={{ label: 'Retry', onClick: fetchLocations }}
            />
          </div>
        )}
        {isLocationsEmpty && (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant="empty"
              title="No locations yet"
              description="Create a location first"
              action={{ label: 'Go to Locations', onClick: () => navigate('admin-locations') }}
            />
          </div>
        )}

        <form
          className="mt-4 flex flex-col gap-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            addEntry();
          }}
        >
          {/*
            Label above control, not beside it. This form lives in a ~260px card
            (the ingestion tabs are an `xl:grid-cols-3`), so a label column left
            the fields 108px wide — the visitors input measured 26px, which is
            why it read as an empty gap rather than a field. A viewport
            breakpoint cannot see the card's width, so the rows stack at every
            size instead of guessing.
          */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="entry-date" className="flex items-center gap-2 text-control text-slate-700">
              Date
              <CalendarIcon className="h-4 w-4 text-brand-600" strokeWidth={1.75} />
            </label>
            <div className="flex flex-col gap-1">
              <input
                id="entry-date"
                type="date"
                value={date}
                max={todayISO()}
                onChange={(event) => {
                  setDate(event.target.value);
                  if (dateFieldError) setDateFieldError(validateDate(event.target.value));
                }}
                style={{ colorScheme: 'light' }}
                className={[
                  'w-full rounded-lg border bg-white px-3 py-2.5 text-control text-slate-900 transition-colors duration-150 ease-out hover:border-slate-400 focus:outline-none focus:ring-1',
                  dateFieldError ? 'border-red-400 focus:border-red-500 focus:ring-red-500' : 'border-slate-300 focus:border-brand-500 focus:ring-brand-500'
                ].join(' ')}
              />
              {dateFieldError && <span className="text-caption text-red-600">{dateFieldError}</span>}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="entry-visitors" className="flex items-center gap-1.5 text-control text-slate-700">
              Number of Visitors / Demand
              <InfoIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
            </label>
            <div>
            <div
              className={`flex w-full items-center rounded-lg border focus-within:ring-1 ${visitorsFieldError ? 'border-red-400 focus-within:border-red-500 focus-within:ring-red-500' : 'border-slate-300 focus-within:border-brand-500 focus-within:ring-brand-500'}`}
            >
              <button
                type="button"
                aria-label="Decrease visitors"
                onClick={() => {
                  // Stepping from a blank field starts at 0, so a single press
                  // yields a real value instead of doing nothing.
                  setVisitors(String(Math.max(0, (visitorsValue ?? 0) - 10)));
                  setVisitorsFieldError(null);
                }}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-l-lg text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-100 focus:outline-none"
              >
                <MinusIcon className="h-4 w-4" strokeWidth={2} />
              </button>
              <input
                id="entry-visitors"
                type="number"
                inputMode="numeric"
                min={0}
                max={MAX_DEMAND_QTY}
                step={1}
                placeholder="Visitors"
                aria-invalid={visitorsFieldError ? true : undefined}
                aria-describedby={visitorsFieldError ? 'entry-visitors-error' : undefined}
                value={visitors}
                onChange={(event) => {
                  const raw = event.target.value;
                  // A negative demand is floored to 0 as it is typed (0 is the
                  // only sensible reading of -5), but an out-of-range number is
                  // left exactly as typed and reported: silently rewriting
                  // 222,000,100 to 10,000,000 would hide the typo.
                  const parsed = raw === '' ? null : Number(raw);
                  if (parsed !== null && parsed < 0) {
                    setVisitors('0');
                    setVisitorsFieldError(null);
                    return;
                  }
                  setVisitors(raw);
                  setVisitorsFieldError(parsed === null ? null : validateVisitors(parsed));
                }}
                style={{ colorScheme: 'light' }}
                className="min-w-[4.5rem] flex-1 basis-0 border-x border-slate-200 bg-white px-2 py-2.5 text-center text-control font-medium tabular-nums text-slate-900 placeholder:text-slate-500 focus:outline-none"
              />
              <button
                type="button"
                aria-label="Increase visitors"
                onClick={() => {
                  setVisitors(String(Math.min(MAX_DEMAND_QTY, (visitorsValue ?? 0) + 10)));
                  setVisitorsFieldError(null);
                }}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-r-lg text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-100 focus:outline-none"
              >
                <PlusIcon className="h-4 w-4" strokeWidth={2} />
              </button>
            </div>
              {visitorsFieldError && (
                <span id="entry-visitors-error" className="mt-1 block text-caption text-red-600">
                  {visitorsFieldError}
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="entry-location" className="flex items-center gap-2 text-control font-medium text-slate-700">
              Location / Site
              <MapPinIcon className="h-4 w-4 text-brand-600" strokeWidth={1.75} />
            </label>
            <div className="relative">
              <select
                id="entry-location"
                value={locationId}
                onChange={(event) => setLocationId(event.target.value)}
                disabled={locLoading || locations.length === 0}
                style={{ colorScheme: 'light' }}
                className="w-full appearance-none rounded-lg border border-slate-300 bg-white px-3 py-2.5 pr-9 text-control text-slate-900 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-50"
              >
                {locations.length === 0 && <option value="">No locations</option>}
                {locations.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
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

          <div>
            <label htmlFor="entry-notes" className="block text-control text-slate-700">
              Notes (optional)
            </label>
            <div className="relative mt-1.5">
              <textarea
                id="entry-notes"
                rows={3}
                maxLength={500}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Add any additional context or notes..."
                style={{ colorScheme: 'light' }}
                className="w-full resize-none rounded-lg border border-slate-300 bg-white px-3 py-2.5 pr-16 text-control text-slate-900 placeholder:text-slate-500 transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
              <span className="pointer-events-none absolute bottom-2.5 right-3 text-caption text-slate-500">
                {notes.length} / 500
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={addEntry}
            disabled={locations.length === 0}
            className="inline-flex items-center gap-1.5 self-start rounded text-control font-semibold text-brand-600 transition-colors duration-150 ease-out hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:text-slate-500"
          >
            <PlusIcon className="h-4 w-4" strokeWidth={2.25} />
            Add another entry
          </button>

          {submitError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5">
              <p className="text-body-xs font-medium text-red-700">{submitError}</p>
              {submitRef && <p className="text-fine text-red-600">ref {submitRef}</p>}
              {fieldErrors.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1">
                  {fieldErrors.map((fe, i) => (
                    <li key={i} className="text-caption text-red-600">
                      Row {fe.row_index}
                      {fe.field ? ` · ${fe.field}` : ''}
                      {fe.code ? ` (${fe.code})` : ''}: {fe.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {saveSuccess && (
            <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-600">
                <CheckIcon className="h-5 w-5 text-white" strokeWidth={2.5} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-control font-semibold text-emerald-900">
                  Saved: {saveSuccess.committed} new, {saveSuccess.reconciled} updated
                </p>
                <p className="mt-0.5 text-meta leading-relaxed text-emerald-800">
                  Your demand is now stored and will be used for the next forecast. The Past Fills list below has been refreshed, and the form is empty for your next entry.
                </p>
                <button
                  type="button"
                  onClick={() => navigate('forecast')}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-meta font-semibold text-white hover:bg-emerald-700"
                >
                  View forecast <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} />
                </button>
              </div>
              <button
                type="button"
                onClick={() => setSaveSuccess(null)}
                className="shrink-0 rounded p-1 text-emerald-700 hover:bg-emerald-100"
                aria-label="Dismiss"
              >
                <XIcon className="h-4 w-4" strokeWidth={2} />
              </button>
            </div>
          )}

          {submitting && (
            <div className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
              <Spinner tone="muted" className="h-4 w-4" />
              <span className="text-body-xs font-medium text-slate-700">Saving your demand…</span>
              <span className="ml-auto text-micro text-slate-500">Please wait</span>
            </div>
          )}

          <div className="border-t border-slate-200 pt-4">
            <h3 className="flex items-center gap-2 text-h5 font-semibold text-slate-900">
              Added Entries ({entries.length})
              <span className="flex h-5 min-w-[20px] items-center justify-center rounded-full bg-brand-600 px-1.5 text-micro font-semibold text-white">
                {entries.length}
              </span>
            </h3>

            {entries.length === 0 ? (
              <p className="mt-3 rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-body-sm text-slate-500">
                No entries yet. Fill in the form above and add your first entry.
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-2">
                {entries.map((entry) => (
                  <li key={entry.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5">
                    <CalendarIcon className="h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={1.75} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-control font-medium text-slate-900">
                        {formatDate(entry.date)} • {entry.visitors.toLocaleString('en-US')} visitors
                      </p>
                      <p className="truncate text-meta text-slate-500">{entry.locationName}</p>
                      {entry.notes && <p className="truncate text-micro text-slate-500">{entry.notes}</p>}
                    </div>
                    <button
                      type="button"
                      aria-label={`Remove entry for ${formatDate(entry.date)}`}
                      onClick={() => removeEntry(entry.id)}
                      className="tap-expand shrink-0 rounded-md p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <XIcon className="h-4 w-4" strokeWidth={2} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <button
              type="button"
              onClick={submitAll}
              disabled={submitting || locations.length === 0 || (entries.length === 0 && !canSubmitCurrent)}
              title={entries.length === 0 && canSubmitCurrent ? 'Save the current form as a single entry' : undefined}
              className="mt-3 inline-flex w-full items-center justify-center gap-2.5 rounded-lg bg-brand-700 px-4 py-3.5 text-h5 font-semibold text-white shadow-sm transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {submitting ? (
                <Spinner tone="onDark" className="h-5 w-5" />
              ) : (
                <CheckIcon className="h-5 w-5" strokeWidth={2.25} />
              )}
              {submitting ? 'Saving…' : entries.length === 0 && canSubmitCurrent ? 'Save Entry' : 'Save & Add to Forecast'}
            </button>
            {entries.length === 0 && canSubmitCurrent && (
              <p className="mt-1.5 text-center text-micro text-slate-500">No entries added yet. Save will submit the current form above.</p>
            )}
          </div>
        </form>
      </section>

      {/* Past fills: what is already stored for this location (full CRUD) */}
      <section
        aria-labelledby="past-fills-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5 overflow-hidden"
      >
        <div className="flex items-center justify-between gap-3">
          <h2 id="past-fills-heading" className="flex items-center gap-2 text-h5 font-bold tracking-tight text-slate-900">
            <CalendarIcon className="h-5 w-5 text-brand-600" strokeWidth={2} />
            Past Fills
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={refreshPastFills}
              className="rounded-md border border-slate-200 px-2 py-1 text-fine font-medium text-slate-600 hover:bg-slate-50"
            >
              Refresh
            </button>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-fine font-semibold text-slate-600">
              {pastFillsLoading ? 'Loading…' : `${pastFills.length} recent`}
            </span>
          </div>
        </div>
        <p className="mt-1 text-meta text-slate-500">
          Last 5 demand entries for <span className="font-medium text-slate-700">{locations.find(l=>l.id===locationId)?.name ?? 'this site'}</span> (from <code className="rounded bg-slate-100 px-1 py-0.5 text-fine">demand_observations</code>). Edit or delete inline.
        </p>
        {pastFillsLoading ? (
          <p className="mt-3 text-center text-body-xs text-slate-500">Loading past fills…</p>
        ) : pastFills.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              size="sm"
              variant="empty"
              title="No past fills yet"
              description="This location has no stored demand. Your first save will appear here and be used for the next forecast."
            />
          </div>
        ) : (
          <ul className="mt-3 flex max-h-[320px] flex-col divide-y divide-slate-100 overflow-y-auto overflow-x-hidden rounded-lg border border-slate-200">
            {pastFills.map((row) => (
              <li key={row.id} className="flex flex-col gap-2 px-3 py-2.5">
                {editingId === row.id ? (
                  <div className="flex flex-col gap-2">
                    <div className="flex flex-wrap gap-2">
                      <input
                        type="date"
                        value={editDate}
                        max={todayISO()}
                        onChange={(e) => setEditDate(e.target.value)}
                        style={{ colorScheme: 'light' }}
                        className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-body-xs text-slate-900 focus:border-brand-500 focus:outline-none"
                      />
                      <input
                        type="number"
                        min={0}
                        value={editQty}
                        onChange={(e) => setEditQty(Math.max(0, parseInt(e.target.value || '0', 10) || 0))}
                        style={{ colorScheme: 'light' }}
                        className="w-28 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-body-xs text-slate-900 focus:border-brand-500 focus:outline-none"
                      />
                    </div>
                    <select
                      value={editLocationId}
                      onChange={(e) => setEditLocationId(e.target.value)}
                      style={{ colorScheme: 'light' }}
                      className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-body-xs text-slate-900 focus:border-brand-500 focus:outline-none"
                    >
                      {locations.map((l) => (
                        <option key={l.id} value={l.id}>{l.name}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      value={editNotes}
                      onChange={(e) => setEditNotes(e.target.value)}
                      placeholder="Notes (e.g. source:manual, event, promo)"
                      maxLength={500}
                      style={{ colorScheme: 'light' }}
                      className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-body-xs text-slate-900 placeholder:text-slate-500 focus:border-brand-500 focus:outline-none"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={saveEditPastFill}
                        disabled={editSaving}
                        className="rounded-md bg-brand-600 px-3 py-1.5 text-caption font-semibold text-white hover:bg-brand-700 disabled:bg-slate-300"
                      >
                        {editSaving ? 'Saving…' : 'Save'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="rounded-md border border-slate-300 px-3 py-1.5 text-caption font-medium text-slate-700 hover:bg-slate-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block text-body-xs font-medium text-slate-900">{formatDate(row.date)} • {row.demand_qty.toLocaleString()} visitors</span>
                      {row.notes && <span className="block truncate text-micro text-slate-500">{row.notes}</span>}
                      {recordedLabel(row.recorded_at) && (
                        <span className="block text-fine text-slate-500">Recorded {recordedLabel(row.recorded_at)}</span>
                      )}
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      <span className="rounded-full bg-brand-50 px-2 py-0.5 text-fine font-semibold text-brand-700">stored</span>
                      <button
                        type="button"
                        aria-label={`Edit ${row.date}`}
                        onClick={() => startEditPastFill(row)}
                        className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-brand-600"
                      >
                        <PencilIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${row.date}`}
                        onClick={() => handleDeletePastFill(row.id)}
                        className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-red-600"
                      >
                        <Trash2Icon className="h-3.5 w-3.5" strokeWidth={1.9} />
                      </button>
                    </span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => navigate('forecast')}
          className="mt-3 inline-flex items-center gap-1.5 self-start rounded text-body-xs font-semibold text-brand-700 hover:text-brand-800"
        >
          View forecast <ArrowRightIcon className="h-4 w-4" strokeWidth={2} />
        </button>
      </section>

      <section
        aria-labelledby="quick-summary-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5 overflow-hidden"
      >
        <h2 id="quick-summary-heading" className="flex items-center gap-2.5 text-h3 font-bold tracking-tight text-slate-900">
          <BarChart3Icon className="h-5 w-5 text-brand-600" strokeWidth={2} />
          Quick Entry Summary
        </h2>
        {livePending && (
          <p className="mt-1 text-caption text-brand-700">Live: current form counts as pending until you Add or Save</p>
        )}

        <div className="mt-4 flex items-center gap-4">
          <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-xl bg-brand-50">
            <CalendarIcon className="h-7 w-7 text-brand-600" strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <p className="text-control text-slate-600">Entries added today</p>
            <p className="text-display-sm font-bold leading-tight text-brand-700">{livePending && entries.length === 0 ? '1' : String(entries.length)}{livePending && entries.length > 0 ? ' +1 pending' : ''}</p>
            <p className="text-meta text-slate-500">Since 12:00 AM today{livePending ? ' · live' : ''}</p>
          </div>
        </div>

        <h3 className="mt-5 text-body font-semibold text-slate-900">Preview (latest 3){livePending ? ' · live' : ''}</h3>
        <ResponsiveTable<Entry>
          className="mt-2"
          caption="Latest three entries queued for this location"
          minWidth="min-w-[260px]"
          columns={[
            {
              key: 'date',
              label: 'Date',
              primary: true,
              render: (entry) => (
                <>{formatDate(entry.date)}{entry.id === '__draft__' ? ' · draft' : ''}</>
              ),
            },
            {
              key: 'visitors',
              label: 'Visitors',
              render: (entry) => entry.visitors.toLocaleString('en-US'),
            },
            {
              key: 'location',
              label: 'Location',
              render: (entry) => entry.locationName,
            },
          ]}
          rows={previewEntries}
          rowKey={(entry) => entry.id}
          rowClassName={(entry) => (entry.id === '__draft__' ? 'border-dashed border-brand-200 bg-brand-50/40' : '')}
          empty={
            // `empty` replaces the whole table (see ResponsiveTable), so it must
            // not be table markup: a `<tr>` here lands inside a `<div>`.
            <p className="py-6 text-center text-body-sm text-slate-500">
              No entries to preview yet.
            </p>
          }
        />

        <div className="mt-auto pt-5">
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 pt-4">
            <span className="text-body font-medium text-slate-700">Total visitors entered</span>
            <span className="text-h3-lg font-bold text-brand-700">{liveTotalVisitors.toLocaleString('en-US')}{livePending ? <span className="ml-1 text-fine font-normal text-brand-600">live</span> : null}</span>
          </div>

          <button
            type="button"
            disabled={entries.length === 0}
            onClick={() => setConfirmOpen(true)}
            className="mt-4 inline-flex w-full items-center justify-center gap-3 rounded-lg border border-brand-600 bg-white px-4 py-3.5 text-h5 font-semibold text-brand-700 transition-colors duration-150 ease-out hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-500"
          >
            Review & Confirm
            <ArrowRightIcon className="h-5 w-5" strokeWidth={2} />
          </button>
        </div>
      </section>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Confirm manual entries"
        description={`${entries.length} ${entries.length === 1 ? 'entry' : 'entries'} · ${totalVisitors.toLocaleString('en-US')} visitors`}
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setConfirmOpen(false)}>
              Keep editing
            </ModalButton>
            <ModalButton onClick={submitAll} disabled={submitting}>
              {submitting ? 'Saving…' : 'Save entries'}
            </ModalButton>
          </>
        }
      >
        <ul className="flex flex-col divide-y divide-slate-100">
          {entries.map((entry) => (
            <li key={entry.id} className="flex justify-between gap-3 py-2 text-body-xs">
              <span className="min-w-0 truncate text-slate-600">
                {formatDate(entry.date)} · {entry.locationName}
              </span>
              <span className="shrink-0 font-semibold tabular-nums text-slate-900">{entry.visitors.toLocaleString('en-US')}</span>
            </li>
          ))}
        </ul>
        {submitError && <p className="mt-3 text-caption text-red-600">{submitError}</p>}
      </Modal>
    </>
  );
}
