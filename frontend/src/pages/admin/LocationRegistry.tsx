import { useEffect, useMemo, useState } from 'react';
import {
  MapPinIcon,
  PlusIcon,
  UtensilsCrossedIcon,
  PencilIcon,
  TrashIcon,
  EyeIcon,
  PhoneIcon,
  UsersIcon,
  CalendarIcon,
  BuildingIcon,
  CheckCircle2Icon,
  AlertTriangleIcon,
  PackageIcon,
  ClockIcon,
} from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { LoadingState } from '../../components/ui/LoadingState';
import { StatusPill } from '../../components/ui/StatusPill';
import { StatCard, type StatTone } from '../../components/ui/StatCard';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { FormModal } from '../../components/ui/FormModal';
import { InlineEdit } from '../../components/ui/InlineEdit';
import { LocationFields } from '../../components/locations/LocationFields';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';
import { RecordList } from '../../components/ui/RecordList';
import { type FilterDef } from '../../hooks/useListState';
import {
  DAY_NAMES,
  NEEDED_HISTORY_DAYS,
  emptyLocationForm,
  forecastState,
  formFromRow,
  gapDaysOf,
  historyDaysOf,
  locationPayload,
  openDayNames,
  toggleOpenDay,
  type LocationForm,
} from '../../lib/locations';

/**
 * The history a site needs before its forecast means anything. `lag_7` and
 * `roll_mean_7` are the model's own features, so under seven days the model has
 * nothing to look back at — the number is a modelling fact, not a preference,
 * which is why it is stated on the page and drawn as a bar in the list. The
 * constant itself lives in `lib/locations` so the add route states the same bar.
 */

/**
 * Perishability, as a dot and as a chip.
 *
 * Two shapes because a bare 8px dot needs a legend to be read at all, and the
 * legend was three more rows on the panel. The row names it in words; the legend
 * stays for the colour-blind reader who wants the mapping stated once.
 */
const PERISHABILITY: Record<string, { dot: string; chip: string }> = {
  High: { dot: 'bg-red-500', chip: 'bg-red-50 text-red-700' },
  Medium: { dot: 'bg-amber-500', chip: 'bg-amber-50 text-amber-700' },
  Low: { dot: 'bg-brand-500', chip: 'bg-brand-50 text-brand-700' },
};

function perishabilityOf(group: any): 'High' | 'Medium' | 'Low' {
  const raw = String(group?.perishability ?? 'medium').toLowerCase();
  if (raw === 'high') return 'High';
  if (raw === 'low') return 'Low';
  return 'Medium';
}

function errMeta(e: unknown) {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error' as const, title: 'Sign in required', desc: e.error.message };
    if (e.status === 403) return { variant: 'error' as const, title: 'Admin access required', desc: e.error.message };
    return {
      variant: 'error' as const,
      title: 'Failed to load locations',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  if (e instanceof TypeError) return { variant: 'offline' as const, title: 'You are offline', desc: 'Check your connection' };
  return { variant: 'offline' as const, title: 'Network error', desc: String(e) };
}

/**
 * History against the 7-day bar, in one cell.
 *
 * `12d` on its own asks the reader to hold "is that enough?" in their head for
 * every row. The bar answers it in the same glance, and the two text nodes the
 * tests and the operator both read (`12d`, `2 gaps`, `4d to go`) are unchanged.
 */
function HistoryCell({ row }: { row: any }) {
  const days = historyDaysOf(row);
  const gaps = gapDaysOf(row);
  const short = days != null && days < NEEDED_HISTORY_DAYS;
  const filled = days == null ? 0 : Math.min(1, days / NEEDED_HISTORY_DAYS);

  return (
    <span className="flex min-w-[104px] flex-col gap-1">
      <span className="flex items-center gap-1.5">
        <span className="tabular-nums font-medium">{days != null ? `${days}d` : '—'}</span>
        {gaps ? (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-fine font-semibold text-amber-700">
            {gaps} gaps
          </span>
        ) : null}
        {short ? (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-fine font-semibold text-amber-700">
            {NEEDED_HISTORY_DAYS - (days as number)}d to go
          </span>
        ) : null}
      </span>
      {/*
        No bar when the count is missing. An empty bar is not "unknown", it is
        "zero days", which is a different and much worse claim — and this list
        has already shipped one literal fallback that kept stating a number.
      */}
      {days != null && (
        <span
          className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100"
          role="img"
          aria-label={`${days} of ${NEEDED_HISTORY_DAYS} days of history`}>
          <span
            className={`block h-full rounded-full ${short ? 'bg-amber-500' : 'bg-brand-600'}`}
            style={{ width: `${Math.round(filled * 100)}%` }}
          />
        </span>
      )}
    </span>
  );
}

/**
 * `onNavigate` is how "Add location" leaves for its own route. Adding is a
 * destination (`/admin-locations-new`), not a dialog: it is the first thing a
 * new workspace does, so the address survives a reload and Back leaves the
 * form. The optional prop keeps the registry renderable on its own.
 */
export function LocationRegistry({ onNavigate }: { onNavigate?: (id: string) => void } = {}) {
  const [showBlockedOnly, setShowBlockedOnly] = useState(false);
  const [locations, setLocations] = useState<any[]>([]);
  const [menuGroups, setMenuGroups] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta> | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [selected, setSelected] = useState<any>(null);
  const [draft, setDraft] = useState<LocationForm>({ ...emptyLocationForm });
  const [saving, setSaving] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const { toast } = useToast();

  const fetchPaged = async (cursor?: string | null) => {
    setLoading(true);
    try {
      const res = await api.get<{ data: any[]; has_more?: boolean; next_cursor?: string | null }>(
        '/api/v1/locations',
        cursor ? { cursor, limit: 20 } : { limit: 20 },
      );
      const data = res.data ?? [];
      setLocations((prev) => (cursor ? [...prev, ...data] : data));
      setHasMore(!!res.has_more);
      setNextCursor(res.next_cursor ?? null);
      if (!cursor) {
        const m = await api.get<any>('/api/v1/menu-groups').catch(() => ({ data: [] }) as any);
        setMenuGroups(m.data ?? m.groups ?? []);
      }
    } catch (e) {
      setError(errMeta(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchPaged(null);
  }, []);

  const visible = showBlockedOnly ? locations.filter((r) => forecastState(r) === 'no') : locations;
  const blocked = locations.filter((r) => forecastState(r) === 'no').length;
  const unreported = locations.filter((r) => forecastState(r) === 'unknown').length;
  const forecastable = locations.filter((r) => forecastState(r) === 'yes').length;

  const seats = locations.reduce((sum, r) => sum + (Number(r.seating_capacity) || 0), 0);
  const sitesWithCapacity = locations.filter((r) => Number(r.seating_capacity) > 0).length;
  const menuItems = menuGroups.reduce(
    (sum, g) => sum + (Number(g.items ?? g.item_count) || 0),
    0,
  );

  // Toolbar: search across the fields a person recognises a site by, plus
  // district and service-type filters built from the rows themselves. The
  // "cannot be forecast" checkbox stays as the quick pre-filter it was, and
  // everything downstream (sort, view, paging) runs on `visible`.
  const districtOptions = [...new Set(locations.map((r: any) => String(r.district ?? r.area ?? '')).filter(Boolean))].sort();
  const serviceOptions = [...new Set(locations.map((r: any) => String(r.service_type ?? r.cuisine ?? '')).filter(Boolean))].sort();
  const locationFilters: FilterDef<any>[] = [
    {
      key: 'district',
      label: 'District',
      match: (r: any, v: string) => String(r.district ?? r.area ?? '') === v,
      options: [{ value: '', label: 'All districts' }, ...districtOptions.map((d) => ({ value: d, label: d }))],
    },
    {
      key: 'service',
      label: 'Service type',
      match: (r: any, v: string) => String(r.service_type ?? r.cuisine ?? '') === v,
      options: [{ value: '', label: 'All service types' }, ...serviceOptions.map((s) => ({ value: s, label: s }))],
    },
  ];
  const locationSort = (r: any, k: string) => {
    if (k === 'location') return String(r.name ?? '');
    if (k === 'capacity') return Number(r.seating_capacity ?? -1);
    if (k === 'history') return Number(r.history_days ?? r.historyDays ?? -1);
    if (k === 'status') return { yes: 2, unknown: 1, no: 0 }[forecastState(r)];
    return String(r[k] ?? '');
  };

  const openAdd = () => onNavigate?.('admin-locations-new');
  const openView = (row: any) => {
    setSelected(row);
    setViewOpen(true);
  };
  const openEdit = (row: any) => {
    setSelected(row);
    setDraft(formFromRow(row));
    setEditOpen(true);
  };
  const openDelete = (row: any) => {
    setSelected(row);
    setDeleteOpen(true);
  };

  /**
   * Fix one field in place — the manager or the phone on a row.
   *
   * The row is rewritten from the **server's own response**, not from the value
   * that was typed. Those differ: `PATCH /locations` applies a field only when it
   * is not `None`, so a write the API chooses to ignore would otherwise show up
   * in the list as done. A refused write throws, which is what puts the old value
   * back inside the control.
   *
   * Clearing sends `''`, never `null` — for the same reason: `null` means "leave
   * this field alone" to that endpoint, so the old number would stay stored while
   * the row showed it gone.
   */
  const patchField = async (row: any, field: string, next: string) => {
    try {
      const fresh = await api.patch<any>(`/api/v1/locations/${row.id}`, { [field]: next });
      /*
       * `PATCH /locations` answers with the full read model, so the row is
       * rewritten from that. When the response carries no read model at all
       * (an empty 200 body), there is nothing to check the write against and the
       * typed value stands in — the alternative is a row that cannot render.
       */
      const stored = fresh?.id ? fresh : { [field]: next };
      setLocations((prev) => prev.map((r) => (r.id === row.id ? { ...r, ...stored } : r)));
      setSelected((prev: any) => (prev?.id === row.id ? { ...prev, ...stored } : prev));
      toast(`${field === 'phone' ? 'Phone' : 'Manager'} updated`, 'success');
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to update', 'error');
      throw e;
    }
  };

  const update = async () => {
    if (!selected || !draft.name.trim()) return;
    setSaving(true);
    try {
      await api.patch(`/api/v1/locations/${selected.id}`, locationPayload(draft));
      toast('Location updated', 'success');
      setEditOpen(false);
      setSelected(null);
      void fetchPaged(null);
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
      else toast('Failed to update', 'error');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await api.delete(`/api/v1/locations/${selected.id}`);
      toast('Location archived', 'success');
      setDeleteOpen(false);
      setSelected(null);
      void fetchPaged(null);
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (idx: number) => {
    setDraft({ ...draft, open_days_mask: toggleOpenDay(draft.open_days_mask, idx) });
  };

  const modalProps = {
    draft,
    setDraft,
    saving,
    toggleDay,
  };

  if (loading && locations.length === 0) return <LoadingState label="Loading locations" />;
  if (error)
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.desc}
        action={{ label: 'Retry', onClick: () => void fetchPaged(null) }}
        size="md"
      />
    );

  if (locations.length === 0)
    return (
      <EmptyState
        variant="empty"
        size="md"
        title="No locations yet"
        description="Add your first location to start ingesting demand."
        hints={[`Forecasts from ${NEEDED_HISTORY_DAYS} days of history`, 'Ingest can start immediately']}
        action={{ label: 'Add location', onClick: openAdd }}
      />
    );

  return (
    <>
      {/* Site health at a glance, in the same StatCard tiles the console and the
          forecast screens use. The panel below says the same thing in a
          sentence; a figure you can see without reading is what a KPI strip is
          for, and the two are computed from one list so they cannot disagree. */}
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          {
            label: 'Locations',
            value: String(locations.length),
            detail: blocked
              ? `${blocked} waiting on history`
              : unreported
                ? `${unreported} not scored yet`
                : 'All forecastable',
            icon: MapPinIcon,
            tone: (blocked ? 'warning' : unreported ? 'default' : 'success') as StatTone,
          },
          {
            label: 'Forecasting',
            value: String(forecastable),
            detail: `Sites at ${NEEDED_HISTORY_DAYS}+ days`,
            icon: CheckCircle2Icon,
            tone: 'success' as StatTone,
          },
          {
            label: 'Seats',
            value: seats ? seats.toLocaleString() : '—',
            detail: sitesWithCapacity
              ? `across ${sitesWithCapacity} site${sitesWithCapacity === 1 ? '' : 's'}`
              : 'No capacity recorded',
            icon: UsersIcon,
            tone: 'default' as StatTone,
          },
          {
            label: 'Menu groups',
            value: String(menuGroups.length),
            detail: menuItems ? `${menuItems} items` : 'No items mapped',
            icon: PackageIcon,
            tone: 'default' as StatTone,
          },
        ].map((kpi) => (
          <StatCard key={kpi.label} label={kpi.label} value={kpi.value} sub={kpi.detail} icon={kpi.icon} tone={kpi.tone} />
        ))}
      </div>

      <div className="grid grid-cols-1 items-start gap-2.5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <Panel
          title="Locations"
          icon={MapPinIcon}
          description={[
            `${forecastable} of ${locations.length} locations forecastable (${NEEDED_HISTORY_DAYS}+ days).`,
            blocked ? `${blocked} need more history.` : '',
            // A site nobody has scored is not a site that is "all set".
            unreported ? `${unreported} not scored yet.` : '',
            !blocked && !unreported ? 'All set.' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          action={
            <button
              type="button"
              onClick={openAdd}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 text-caption font-semibold text-white transition-colors hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-1.5">
              <PlusIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
              Add location
            </button>
          }>
          <RecordList<any>
            stateKey="admin-locations"
            items={visible}
            filters={locationFilters}
            searchFields={(r: any) =>
              [r.name, r.district, r.area, r.cuisine, r.service_type, r.manager_name, r.phone]
                .filter(Boolean)
                .join(' ')}
            searchPlaceholder="Search locations…"
            sortValue={locationSort}
            defaultSort={{ key: 'location', dir: 'asc' }}
            countNoun="locations"
            caption="Locations"
            minWidth="min-w-[440px]"
            rowKey={(r: any) => String(r.id)}
            toolbarEnd={
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-caption font-medium text-slate-700 transition-colors hover:bg-slate-50 sm:py-1.5 dark:text-slate-200">
                <input
                  type="checkbox"
                  checked={showBlockedOnly}
                  onChange={(e) => setShowBlockedOnly(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-slate-300 accent-brand-600"
                />
                Show only locations that cannot be forecast ({blocked})
              </label>
            }
            columns={[
              {
                key: 'location',
                label: 'Location',
                primary: true,
                sortable: true,
                render: (r: any) => (
                  <button type="button" onClick={() => openView(r)} className="text-left">
                    <span className="block text-meta font-semibold text-slate-900">{r.name}</span>
                    <span className="block text-fine text-slate-500">
                      {[r.district ?? r.area, r.service_type ?? r.cuisine].filter(Boolean).join(' · ')}
                    </span>
                    {r.blocker && <span className="mt-0.5 block text-fine text-red-600">{r.blocker}</span>}
                  </button>
                ),
              },
              {
                key: 'capacity',
                label: 'Capacity',
                sortable: true,
                render: (r: any) => (
                  <span className="inline-flex items-center gap-1 tabular-nums">
                    <UsersIcon className="h-3 w-3 shrink-0 text-slate-500" strokeWidth={1.9} aria-hidden="true" />
                    {r.seating_capacity ?? '—'}
                  </span>
                ),
              },
              { key: 'history', label: 'History', sortable: true, render: (r: any) => <HistoryCell row={r} /> },
              {
                key: 'phone',
                label: 'Contact',
                /*
                  Editable in place. A manager or a phone number changes for
                  reasons that have nothing to do with the site's details, and
                  sending the operator through an eleven-field dialog for a
                  two-second correction loses their place in the list. The rest
                  of the record — what the site *is* — stays behind the edit
                  dialog, where a change of that size belongs.
                */
                render: (r: any) => (
                  <span className="flex min-w-[170px] flex-col gap-0.5">
                    <InlineEdit
                      value={r.manager_name}
                      label={`manager for ${r.name}`}
                      placeholder="Add manager"
                      onSave={(next) => patchField(r, 'manager_name', next)}
                      textClassName="font-medium text-slate-800"
                    />
                    <InlineEdit
                      value={r.phone}
                      label={`phone for ${r.name}`}
                      placeholder="Add phone"
                      type="tel"
                      onSave={(next) => patchField(r, 'phone', next)}
                      textClassName="text-slate-600"
                    />
                  </span>
                ),
              },
              {
                key: 'status',
                label: 'Status',
                sortable: true,
                render: (r: any) => {
                  const state = forecastState(r);
                  return (
                    <StatusPill tone={state === 'yes' ? 'success' : state === 'no' ? 'warning' : 'neutral'} dot>
                      {state === 'yes' ? 'forecasting' : state === 'no' ? 'needs history' : 'not scored'}
                    </StatusPill>
                  );
                },
              },
              // Icon-only controls need a name and a focus ring: three bare icons
              // per row announced as "button, button, button" and gave a keyboard
              // user no idea which location they were about to archive.
              //
              // Kept as a wrapping row on purpose: `whitespace-nowrap` measured
              // no shorter (the Location cell's own wrap sets the row's height at
              // every width) and pushed the buttons out of the panel at 768px,
              // where a stacked column still fits.
              {
                key: 'actions',
                label: 'Actions',
                cellClassName: 'pr-0',
                render: (r: any) => (
                  <span className="flex flex-wrap items-center gap-1">
                    <button
                      type="button"
                      onClick={() => openView(r)}
                      aria-label={`View ${r.name}`}
                      title="View"
                      className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                      <EyeIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                    </button>
                    <button
                      type="button"
                      onClick={() => openEdit(r)}
                      aria-label={`Edit ${r.name}`}
                      title="Edit"
                      className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                      <PencilIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                    </button>
                    <button
                      type="button"
                      onClick={() => openDelete(r)}
                      aria-label={`Archive ${r.name}`}
                      title="Archive"
                      className="tap-expand rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                      <TrashIcon className="h-3.5 w-3.5" strokeWidth={1.9} />
                    </button>
                  </span>
                ),
              },
            ]}
            footer={(list) => (
              <>
                {/* These pages append rather than paginate, so "Previous" reset to the
                    first page instead of going back one; it now says so. */}
                {(hasMore || nextCursor) && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void fetchPaged(null)}
                      className="tap-expand rounded-lg border border-slate-300 bg-white px-3 py-2 text-caption font-semibold text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-1.5">
                      Back to the first page
                    </button>
                    {hasMore && (
                      <button
                        type="button"
                        onClick={() => void fetchPaged(nextCursor)}
                        className="rounded-lg bg-brand-700 px-3 py-2 text-caption font-semibold text-white transition-colors hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-1.5">
                        Load 20 more
                      </button>
                    )}
                  </div>
                )}
                <p className="mt-2 text-fine text-slate-500">
                  Tap a location to view details · {list.filtered} shown
                </p>
              </>
            )}
          />
        </Panel>

        <div className="flex flex-col gap-3">
          <Panel
            title="Menu groups"
            icon={UtensilsCrossedIcon}
            description="Share of covers and perishability. Gaps shown as low coverage.">
            {menuGroups.length === 0 ? (
              <EmptyState
                variant="empty"
                size="sm"
                title="No menu groups"
                description="No menu groups configured. Add them to model perishability."
              />
            ) : (
              <>
                <ul className="flex flex-col gap-3">
                  {menuGroups.map((g: any) => {
                    const level = perishabilityOf(g);
                    const share = Number(g.share ?? g.share_pct ?? 0);
                    const coverage = g.coverage_pct != null ? Number(g.coverage_pct) : null;
                    const tone = PERISHABILITY[level];
                    return (
                      <li key={g.id} className="rounded-xl border border-slate-200 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <span className="min-w-0">
                            <span className="block truncate text-meta font-semibold text-slate-900" title={g.name}>
                              {g.name}
                            </span>
                            <span className="mt-0.5 block text-fine text-slate-500">
                              {g.items ?? g.item_count ?? 0} items
                              {coverage != null ? ` · ${coverage}% coverage` : ''}
                            </span>
                          </span>
                          <span className="flex shrink-0 flex-col items-end gap-1">
                            <span className="text-stat-sm font-bold tabular-nums leading-none text-slate-900">
                              {share}%
                            </span>
                            <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-fine font-semibold ${tone.chip}`}>
                              <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden="true" />
                              {level}
                            </span>
                          </span>
                        </div>
                        <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                          <div
                            className={`h-full rounded-full ${tone.dot}`}
                            style={{ width: `${Math.max(0, Math.min(100, share))}%` }}
                          />
                        </div>
                        {coverage != null && coverage < 90 && (
                          <p className="mt-1.5 text-fine text-amber-700">
                            {100 - coverage}% of {g.name} items have no demand history
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-slate-100 pt-3">
                  {(['High', 'Medium', 'Low'] as const).map((l) => (
                    <li key={l} className="flex items-center gap-1.5 text-fine text-slate-500">
                      <span className={`h-2 w-2 rounded-full ${PERISHABILITY[l].dot}`} aria-hidden="true" />
                      {l} perishability
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Panel>

          <Panel title="Onboarding rule" icon={ClockIcon} description="When a new location starts forecasting.">
            <p className="text-caption leading-relaxed text-slate-600">
              A location forecasts from <span className="font-semibold text-slate-900">{NEEDED_HISTORY_DAYS} days</span> of
              history, enough for <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-fine text-slate-700">lag_7</code>
              {' / '}
              <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-fine text-slate-700">roll_mean_7</code> to be
              meaningful. Below that it still shows a model forecast with a{' '}
              <span className="rounded bg-amber-50 px-1.5 py-0.5 text-fine font-semibold text-amber-700">limited history</span>{' '}
              flag.
            </p>
            <ol className="mt-3 flex flex-col gap-2">
              {[
                { day: 'Day 0', text: 'The site is on the register and ingest can start.' },
                { day: `Day ${NEEDED_HISTORY_DAYS}`, text: 'Forecasting switches on for its own series.' },
                { day: 'Any time', text: 'Fill gaps from the Manual Form or a CSV for tighter intervals.' },
              ].map((step) => (
                <li key={step.day} className="flex items-baseline gap-2.5 text-caption">
                  <span className="w-14 shrink-0 text-fine font-semibold uppercase tracking-wide text-slate-500">
                    {step.day}
                  </span>
                  <span className="min-w-0 text-slate-600">{step.text}</span>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>

      <LocationFormModal
        open={editOpen}
        onClose={() => {
          setEditOpen(false);
          setSelected(null);
        }}
        onSave={update}
        title={`Edit ${selected?.name ?? ''}`}
        description="Update site details. Changes affect the next forecast's features."
        submitLabel="Save changes"
        {...modalProps}
      />
      <LocationDetailModal
        open={viewOpen}
        onClose={() => {
          setViewOpen(false);
          setSelected(null);
        }}
        row={selected}
        onEdit={() => {
          setViewOpen(false);
          openEdit(selected);
        }}
        onArchive={() => {
          setViewOpen(false);
          openDelete(selected);
        }}
      />
      <Modal
        open={deleteOpen}
        onClose={() => {
          setDeleteOpen(false);
          setSelected(null);
        }}
        title="Archive location?"
        description={selected?.name ?? undefined}
        size="md"
        footer={
          <>
            <ModalButton
              tone="ghost"
              onClick={() => {
                setDeleteOpen(false);
                setSelected(null);
              }}>
              Cancel
            </ModalButton>
            <ModalButton tone="danger" onClick={remove} disabled={saving}>
              {saving ? 'Archiving…' : 'Archive'}
            </ModalButton>
          </>
        }>
        <div className="flex flex-col gap-3">
          <p className="text-body-xs leading-relaxed text-slate-600">
            {selected?.name ?? 'This location'} stops receiving new forecasts. This is not a hard delete.
          </p>
          <ul className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3">
            {[
              ['Kept', 'Demand history and every forecast already published for it'],
              ['Stopped', 'New forecasts, and any connector pointed at the site'],
              ['Reversible', 'Support can restore an archived location'],
            ].map(([label, text]) => (
              <li key={label} className="flex items-baseline gap-2.5 text-caption">
                <span className="w-20 shrink-0 text-fine font-semibold uppercase tracking-wide text-slate-500">
                  {label}
                </span>
                <span className="min-w-0 text-slate-600">{text}</span>
              </li>
            ))}
          </ul>
        </div>
      </Modal>
    </>
  );
}/**
 * Edit a site, in sections.
 *
 * Built on `FormModal` rather than `Modal` + a hand-rolled footer: that is what
 * gives the dialog a real `<form>` (Enter submits), the shared Cancel/Save
 * ordering, and the `Saving…` state. The eleven fields come from
 * `LocationFields` — the same component the add route renders — so the dialog
 * and the page can never drift into asking for different things.
 */
function LocationFormModal({
  open,
  onClose,
  draft,
  setDraft,
  onSave,
  saving,
  title,
  description,
  submitLabel,
  toggleDay,
}: {
  open: boolean;
  onClose: () => void;
  draft: LocationForm;
  setDraft: (v: LocationForm) => void;
  onSave: () => void;
  saving: boolean;
  title: string;
  description: string;
  submitLabel: string;
  toggleDay: (idx: number) => void;
}) {
  const openDays = openDayNames(draft.open_days_mask);

  return (
    <FormModal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      onSubmit={onSave}
      submitLabel={submitLabel}
      saving={saving}
      size="xl"
      footerExtra={
        <span className="mr-auto hidden items-center gap-1.5 text-fine text-slate-500 sm:inline-flex">
          <ClockIcon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
          {openDays.length === 7
            ? 'Trading every day'
            : `Closed ${DAY_NAMES.filter((d) => !openDays.includes(d)).join(', ')}`}
        </span>
      }>
      <LocationFields draft={draft} setDraft={setDraft} toggleDay={toggleDay} autoFocusName />
    </FormModal>
  );
}

/**
 * The detail dialog.
 *
 * It used to be four flat tiles and an address block, which answered "what is
 * this site?" but not the question the registry exists to answer: is it
 * forecasting yet, and if not, how far off. The state banner and the history bar
 * lead now; the reference fields follow.
 */
function LocationDetailModal({
  open,
  onClose,
  row,
  onEdit,
  onArchive,
}: {
  open: boolean;
  onClose: () => void;
  row: any;
  onEdit: () => void;
  onArchive: () => void;
}) {
  const days = historyDaysOf(row);
  const gaps = gapDaysOf(row);
  const state3 = row ? forecastState(row) : 'unknown';
  const short = days != null && days < NEEDED_HISTORY_DAYS;
  const filled = days == null ? 0 : Math.min(1, days / NEEDED_HISTORY_DAYS);
  /*
    The state, then the number — never a number we were not given. The list
    endpoint reports `history_days` and the detail may not carry it forward, so
    "7 days of history" here would be the page asserting a figure it invented.
  */
  const notEnoughBody = `A model forecast still appears, flagged as limited history, until ${NEEDED_HISTORY_DAYS} days land.`;
  const state = state3 === 'unknown'
    ? {
        title: 'Forecast readiness not reported',
        body: `The API did not send this site's history or its forecastable flag, so nothing here can say whether the ${NEEDED_HISTORY_DAYS}-day bar is cleared.`,
      }
    : state3 === 'yes'
      ? {
          title: days != null ? `Forecasting from ${days} days of history` : 'Forecasting is on',
          body:
            days != null
              ? 'Its own series is long enough for lag_7 and roll_mean_7 to be meaningful.'
              : `Its series clears the ${NEEDED_HISTORY_DAYS}-day bar. The API did not report how many days it holds.`,
        }
      : {
          title: days != null ? `${days} of ${NEEDED_HISTORY_DAYS} days of history` : 'Not enough history yet',
          body: notEnoughBody,
        };
  /*
    A missing mask is not "open every day". The row's `open_days_mask` comes back
    null on a site that predates the field, and defaulting it to seven ones made
    the dialog state a trading pattern nobody had entered.
  */
  const maskRecorded = row?.open_days_mask != null;
  const openDays = openDayNames(row?.open_days_mask);
  const closedDays = DAY_NAMES.filter((d) => !openDays.includes(d));

  const facts = useMemo(
    () => [
      { label: 'Capacity', value: row?.seating_capacity ? `${row.seating_capacity} seats` : '—', icon: UsersIcon },
      { label: 'District', value: row?.district ?? row?.area ?? '—', icon: BuildingIcon },
      { label: 'Service type', value: row?.service_type ?? row?.cuisine ?? '—', icon: UtensilsCrossedIcon },
      { label: 'Opened', value: row?.opened_on ?? '—', icon: CalendarIcon },
    ],
    [row],
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={row?.name ?? 'Location'}
      description={[row?.district ?? row?.area, row?.service_type ?? row?.cuisine].filter(Boolean).join(' · ')}
      size="lg"
      footer={
        <>
          <ModalButton tone="ghost" onClick={onClose}>
            Close
          </ModalButton>
          <ModalButton onClick={onEdit}>Edit</ModalButton>
        </>
      }>
      {row && (
        <div className="flex flex-col gap-4">
          {/* The one thing an operator opens this for. */}
          <div
            className={[
              'flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border p-3.5',
              state3 === 'yes'
                ? 'border-brand-200 bg-brand-50/50'
                : state3 === 'no'
                  ? 'border-amber-200 bg-amber-50/60'
                  : 'border-slate-200 bg-slate-50/70',
            ].join(' ')}>
            <div className="flex min-w-0 items-start gap-2.5">
              {state3 === 'yes' ? (
                <CheckCircle2Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand-700" strokeWidth={2} aria-hidden="true" />
              ) : state3 === 'no' ? (
                <AlertTriangleIcon className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" strokeWidth={2} aria-hidden="true" />
              ) : (
                <ClockIcon className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={2} aria-hidden="true" />
              )}
              <div className="min-w-0">
                <p className="text-control font-semibold text-slate-900">{state.title}</p>
                <p className="mt-0.5 text-caption leading-relaxed text-slate-600">{state.body}</p>
              </div>
            </div>
            <StatusPill tone={state3 === 'yes' ? 'success' : state3 === 'no' ? 'warning' : 'neutral'} dot>
              {state3 === 'yes' ? 'forecasting' : state3 === 'no' ? 'needs history' : 'not scored'}
            </StatusPill>
          </div>

          {days != null && (
            <div>
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-fine font-semibold uppercase tracking-wide text-slate-500">History</p>
                <p className="text-caption tabular-nums text-slate-600">
                  {days}d recorded
                  {gaps ? ` · ${gaps} gap${gaps === 1 ? '' : 's'}` : ''}
                </p>
              </div>
              <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full ${short ? 'bg-amber-500' : 'bg-brand-700'}`}
                  style={{ width: `${Math.round(filled * 100)}%` }}
                />
              </div>
              <p className="mt-1.5 text-fine text-slate-500">
                The bar is {NEEDED_HISTORY_DAYS} days — the point at which forecasting switches on.
              </p>
            </div>
          )}

          <dl className="grid grid-cols-2 gap-3">
            {facts.map((fact) => (
              <div key={fact.label} className="rounded-xl border border-slate-200 p-3">
                <dt className="flex items-center gap-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
                  <fact.icon className="h-3.5 w-3.5" strokeWidth={1.9} aria-hidden="true" />
                  {fact.label}
                </dt>
                <dd className="mt-1 text-control font-semibold text-slate-900">{fact.value}</dd>
              </div>
            ))}
          </dl>

          <div className="rounded-xl border border-slate-200 p-3.5">
            <p className="text-fine font-semibold uppercase tracking-wide text-slate-500">Address &amp; contact</p>
            <p className="mt-1.5 text-body-xs text-slate-800">{row.address ?? 'No address recorded'}</p>
            <p className="mt-1 flex items-center gap-1.5 text-body-xs text-slate-600">
              <PhoneIcon className="h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.9} aria-hidden="true" />
              {[row.phone ?? 'No phone', row.manager_name].filter(Boolean).join(' · ')}
            </p>
            <p className="mt-1.5 text-caption text-slate-600">
              {!maskRecorded
                ? 'Open days not recorded'
                : openDays.length === 7
                  ? 'Open every day'
                  : `Open ${openDays.join(', ') || 'no days'} · closed ${closedDays.join(', ')}`}
            </p>
            {row.notes && (
              <p className="mt-2 rounded-lg bg-slate-50 p-2 text-caption leading-relaxed text-slate-600">{row.notes}</p>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
            <p className="text-fine text-slate-500">
              Archiving keeps the history and stops new forecasts.
            </p>
            <button
              type="button"
              onClick={onArchive}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-700 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              <TrashIcon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
              Archive this location
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
