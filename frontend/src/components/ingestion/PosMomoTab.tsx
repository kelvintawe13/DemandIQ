import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  LinkIcon,
  ArrowRightIcon,
  UploadIcon,
  LockIcon,
  InfoIcon,
  CreditCardIcon,
  SmartphoneIcon,
  FileTextIcon,
  RefreshCwIcon,
} from 'lucide-react';
import { PosTerminal } from '../illustrations/PosTerminal';
import { EmptyState } from '../ui/EmptyState';
import { StatusPill } from '../ui/StatusPill';
import { PermissionNote } from '../ui/PermissionNote';
import { ResponsiveTable } from '../ui/Table';
import { Spinner } from '../ui/LoadingState';
import { useToast } from '../../contexts/ToastContext';
import { useNavigation } from '../../contexts/NavigationContext';
import { useSession } from '../../contexts/SessionContext';
import { api, ApiException } from '../../api/client';
import { jobLabel, runStatusTone } from '../../lib/schedule';
import {
  SOURCE_STATUS_TONE,
  isPosOrMomo,
  isSyncable,
  kindLabel,
  kindOf,
  stateOf,
} from '../../lib/sourceChannels';
import { plainStatus } from '../../lib/plainLanguage';

/**
 * POS / Mobile Money ingestion.
 *
 * This tab used to be a prototype that made no API call at all: "Authorise" set
 * a local flag and announced a 90-day backfill, "Import 4 days" announced an
 * import, and the preview listed four hardcoded dates. None of it touched the
 * server, so the screen could report demand as ingested when nothing had been
 * written.
 *
 * It is now driven entirely by what the API says. `GET /sources` is the truth
 * about which channels exist and how fresh they are; `POST /sources/{id}/sync`
 * queues the real pull; `GET /sources/sync-history` reports the runs. The one
 * thing this tab cannot do is *create* a channel — that needs
 * `connectors:manage`, which a data clerk and an owner here do not hold — so it
 * says who can, instead of pretending to have done it.
 */

interface SourcePull {
  pulled: number | null;
  committed: number | null;
  skipped: number | null;
  detail: string | null;
}

interface SourceRow {
  id: string;
  name: string;
  type: string;
  status: string;
  freshness?: string | null;
  last_sync_at?: string | null;
  volume?: number | null;
  health?: string | null;
  /** This channel's own last pull, from `GET /sources`. Null counts = never. */
  last_pull?: SourcePull | null;
  created_at?: string | null;
}

interface SyncRun {
  id: string;
  job_type: string;
  status: string;
  rows_processed?: number | null;
  duration_ms?: number | null;
  detail?: string | null;
  started_at?: string | null;
}

interface PosMomoTabProps {
  /**
   * Hand the operator to the spreadsheet channel. A POS export *is* a CSV/XLSX
   * file, and that channel already does preview → column mapping → commit, so
   * the export belongs there rather than in a second, thinner upload path.
   */
  onRouteToSpreadsheet?: () => void;
}

const CHANNELS = [
  {
    id: 'pos',
    // `short` names the channel inside a sentence ("No POS channel is…"); the
    // `title` is the row's own heading, where the fuller wording reads better.
    short: 'POS',
    title: 'POS System',
    description: 'Square, Toast, Clover, Lightspeed and more',
    icon: CreditCardIcon,
  },
  {
    id: 'momo',
    short: 'Mobile Money',
    title: 'MoMo / Mobile Money API',
    description: 'MTN MoMo, Airtel Money, M-Pesa and more',
    icon: SmartphoneIcon,
  },
  {
    id: 'export',
    short: 'export file',
    title: 'POS export file',
    description: 'A CSV, XLSX or TXT export from your till',
    icon: FileTextIcon,
  },
] as const;

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

/**
 * What this channel's own last pull did, in one line.
 *
 * Counts come from `GET /sources` (`last_pull`), not from the workspace-wide
 * run table — `pipeline_runs` records no connector id, so a per-channel row
 * there is impossible. `null` counts mean no pull has run; a `detail` without
 * counts is the reason none ran (a channel with no pull endpoint configured).
 */
const lastPullNote = (source: SourceRow): string => {
  const lp = source.last_pull;
  if (!lp) return 'No pull yet';
  if (lp.committed != null) {
    const skipped = lp.skipped ? `, ${lp.skipped} skipped` : '';
    return `Last pull: ${lp.committed} row${lp.committed === 1 ? '' : 's'} committed${skipped}`;
  }
  return lp.detail ? `Last pull: ${lp.detail}` : 'No pull yet';
};

export function PosMomoTab({ onRouteToSpreadsheet }: PosMomoTabProps) {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [history, setHistory] = useState<SyncRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncingKind, setSyncingKind] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>('pos');

  const { toast } = useToast();
  const { navigate } = useNavigation();
  const { user } = useSession();

  /**
   * Creating a channel is gated on `connectors:manage` server-side (verified: it
   * answers 403 without it), so the offer to connect one is only made to a seat
   * that can. Everyone else gets the explanation, not a button that would fail.
   */
  const canManageConnectors = (user.permissions ?? []).includes('connectors:manage');

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sourcesRes, historyRes] = await Promise.all([
        api.get<{ data: SourceRow[] }>('/api/v1/sources'),
        api.get<{ data: SyncRun[] }>('/api/v1/sources/sync-history', { limit: 20 }),
      ]);
      setSources(sourcesRes.data ?? []);
      setHistory(historyRes.data ?? []);
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        setError(`${e.error.message}${e.reference ? ` (ref ${e.reference})` : ''}`);
      } else if (e instanceof Error && /failed to fetch|network/i.test(e.message)) {
        setError('Backend unreachable. Nothing has been queued or imported.');
      } else {
        setError(e instanceof Error ? e.message : 'Failed to load sources');
      }
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  /** Only POS / mobile-money channels: this tab does not answer for OCR or file. */
  const channels = useMemo(() => sources.filter(isPosOrMomo), [sources]);

  const selectedChannels = useMemo(
    () => channels.filter((s) => kindOf(s) === selected),
    [channels, selected],
  );

  /** Of the selected kind, the ones a sync would actually pull. */
  const selectedSyncable = useMemo(
    () => selectedChannels.filter(isSyncable),
    [selectedChannels],
  );

  /** Runs that pull a channel. Feature builds are a different job and a different story. */
  const syncRuns = useMemo(
    () => history.filter((run) => run.job_type === 'connector_sync'),
    [history],
  );

  const syncNow = async (kind: string) => {
    // Only channels the job will act on. Queueing a run for an inactive channel
    // would settle as `success` having pulled nothing, which reads as a working
    // sync that silently does nothing.
    const targets = channels.filter((s) => kindOf(s) === kind && isSyncable(s));
    if (targets.length === 0) return;
    setSyncingKind(kind);
    setSyncError(null);
    try {
      const queued = await Promise.all(
        targets.map((s) =>
          api.post<{ job_id: string; status: string }>(`/api/v1/sources/${s.id}/sync`),
        ),
      );
      /* The API queues the run; it does not perform it in this request. Saying
         "imported" here is exactly the mistake this tab used to make — the run
         is still `queued` until a worker or the scheduler drains it. */
      toast(
        queued.length === 1
          ? `${targets[0].name}: sync queued (job ${queued[0].job_id.slice(0, 8)}).`
          : `${queued.length} syncs queued.`,
      );
      await fetchAll();
    } catch (e: unknown) {
      const message =
        e instanceof ApiException
          ? e.error.message
          : e instanceof Error
            ? e.message
            : 'Failed to queue the sync';
      setSyncError(message);
      toast(message, 'error');
    } finally {
      setSyncingKind(null);
    }
  };

  const activeCount = channels.filter(isSyncable).length;

  const selectedLabel = CHANNELS.find((c) => c.id === selected)?.short ?? selected;

  if (loading && !loaded) {
    return (
      <section className="flex min-w-0 items-center justify-center gap-2.5 rounded-xl border border-slate-200 bg-white p-6">
        <Spinner className="h-5 w-5" />
        <span className="text-body-xs font-medium text-slate-600">Loading channels…</span>
      </section>
    );
  }

  if (error) {
    return (
      <section className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <EmptyState
          size="sm"
          variant="error"
          title="Failed to load channels"
          description={error}
          action={{ label: 'Retry', onClick: () => void fetchAll() }}
        />
      </section>
    );
  }

  return (
    <>
      <section
        aria-labelledby="connect-source-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
      >
        <h2
          id="connect-source-heading"
          className="text-h3 font-bold tracking-tight text-slate-900"
        >
          Connect POS or Mobile Money
        </h2>
        <p className="mt-1 text-control text-slate-500">
          Choose a channel to see its connection and pull the latest takings.
        </p>

        <div className="mt-3 flex justify-center">
          <PosTerminal className="h-[150px] w-auto max-w-full" />
        </div>

        <fieldset className="mt-4 flex flex-col gap-2.5">
          <legend className="sr-only">Data source</legend>
          {CHANNELS.map((option) => {
            const Icon = option.icon;
            const isSelected = option.id === selected;
            const matching = channels.filter((s) => kindOf(s) === option.id);
            return (
              <label
                key={option.id}
                className={[
                  'flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-3 transition-colors duration-150 ease-out',
                  isSelected
                    ? 'border-brand-500 bg-brand-50/60'
                    : 'border-slate-200 bg-white hover:bg-slate-50',
                ].join(' ')}
              >
                <Icon
                  className={[
                    'h-5 w-5 shrink-0',
                    isSelected ? 'text-brand-600' : 'text-slate-500',
                  ].join(' ')}
                  strokeWidth={1.75}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-body-sm-lg font-semibold text-slate-900">
                    {option.title}
                  </span>
                  <span className="block text-meta text-slate-500">{option.description}</span>
                </span>
                {option.id !== 'export' && (
                  <span
                    className={[
                      'shrink-0 rounded-full px-2.5 py-1 text-fine font-semibold',
                      matching.length > 0
                        ? 'bg-brand-100 text-brand-800'
                        : 'bg-slate-100 text-slate-500',
                    ].join(' ')}
                  >
                    {matching.length > 0 ? `${matching.length} connected` : 'None'}
                  </span>
                )}
                <input
                  type="radio"
                  name="pos-source"
                  value={option.id}
                  checked={isSelected}
                  onChange={() => setSelected(option.id)}
                  className="h-4 w-4 shrink-0 accent-brand-600"
                />
              </label>
            );
          })}
        </fieldset>

        <div className="mt-4 flex flex-col gap-2.5 sm:flex-row">
          <button
            type="button"
            disabled={
              selected === 'export' || selectedSyncable.length === 0 || syncingKind !== null
            }
            onClick={() => void syncNow(selected)}
            className="inline-flex flex-1 items-center justify-center gap-2.5 rounded-lg bg-brand-700 px-4 py-3 text-body font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            <RefreshCwIcon
              className={['h-[18px] w-[18px]', syncingKind === selected ? 'animate-spin' : ''].join(
                ' ',
              )}
              strokeWidth={2}
            />
            {syncingKind === selected ? 'Queueing…' : `Sync now (${selectedLabel})`}
          </button>
          <button
            type="button"
            onClick={() => {
              if (onRouteToSpreadsheet) {
                onRouteToSpreadsheet();
                toast('Opened the spreadsheet channel for your export.');
              } else {
                navigate('ingestion');
              }
            }}
            className="inline-flex flex-1 items-center justify-center gap-2.5 rounded-lg border border-slate-300 bg-white px-4 py-3 text-body font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <UploadIcon className="h-[18px] w-[18px]" strokeWidth={2} />
            Upload export file
          </button>
        </div>

        <p className="mt-2.5 text-caption text-slate-500">
          {selected === 'export'
            ? 'A POS export is a spreadsheet — the file channel previews it and maps the columns.'
            : selectedChannels.length === 0
              ? `No ${selectedLabel} channel is connected in this workspace yet.`
              : selectedSyncable.length === 0
                ? `This ${selectedLabel} channel is ${stateOf(selectedChannels[0]).toLowerCase()}, not active. A sync only pulls active channels, so it would do nothing — an administrator activates it on Connectors.`
                : `Queues a pull for ${selectedSyncable.length} active ${selectedLabel} channel${selectedSyncable.length === 1 ? '' : 's'}. The run is queued, not finished: its status appears below.`}
        </p>

        {syncError && (
          <p className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-caption text-red-700">
            {syncError}
          </p>
        )}

        <p className="mt-3 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-center text-meta text-slate-500">
          <LockIcon className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.75} />
          DemandIQ only ever reads transaction totals — never card or customer data.
        </p>

        {canManageConnectors ? (
          <button
            type="button"
            onClick={() => navigate('admin-connectors')}
            className="mt-3 inline-flex items-center gap-1.5 self-start rounded text-body-xs font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            Add or configure a channel <ArrowRightIcon className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        ) : (
          <PermissionNote className="mt-3">
            Adding a channel — the provider, its credentials and its backfill window — is done on
            the Connectors page, which owners and administrators manage. You can queue a sync for
            a channel that already exists, and read its history below.
          </PermissionNote>
        )}
      </section>

      <section
        aria-labelledby="sync-status-heading"
        className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
      >
        <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
          <div>
            <h2
              id="sync-status-heading"
              className="text-h3 font-bold tracking-tight text-slate-900"
            >
              Channels &amp; Sync History
            </h2>
            <p className="mt-1 text-control text-slate-500">
              Every POS and mobile-money channel in this workspace, and the runs that pulled from
              them.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void fetchAll()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-meta font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <RefreshCwIcon
              className={['h-3.5 w-3.5', loading ? 'animate-spin' : ''].join(' ')}
              strokeWidth={1.9}
            />
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        <div className="mt-4 rounded-xl border border-slate-200 p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100">
                <LinkIcon className="h-5 w-5 text-slate-500" strokeWidth={1.75} />
              </span>
              <div>
                <p className="text-body-sm-lg font-semibold text-slate-900">Connection status</p>
                <p className="flex items-center gap-1.5 text-body-xs text-slate-500">
                  <span
                    className={[
                      'h-2 w-2 rounded-full',
                      activeCount > 0 ? 'bg-brand-500' : 'bg-slate-400',
                    ].join(' ')}
                  />
                  {activeCount > 0
                    ? `${activeCount} of ${channels.length} channel${channels.length === 1 ? '' : 's'} active`
                    : channels.length > 0
                      ? 'No channel is active, so a sync would pull nothing'
                      : 'No channel ready to pull'}
                </p>
              </div>
            </div>
          </div>
        </div>

        {channels.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              size="sm"
              variant="empty"
              title="No POS or mobile-money channel"
              description="This workspace has no POS or Mobile Money source connected, so there is nothing to sync yet."
              hints={[
                'An owner or administrator adds the provider on the Connectors page',
                'A CSV or XLSX export can be ingested today through the spreadsheet channel',
              ]}
              action={{ label: 'Use the spreadsheet channel', onClick: () => onRouteToSpreadsheet?.() }}
            />
          </div>
        ) : (
          <ul className="mt-4 flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200">
            {channels.map((source) => {
              const state = stateOf(source);
              return (
                <li key={source.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3">
                  <span className="flex min-w-0 flex-1 items-center gap-2.5">
                    {kindOf(source) === 'momo' ? (
                      <SmartphoneIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} />
                    ) : (
                      <CreditCardIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} />
                    )}
                    <span className="min-w-0">
                      <span className="block truncate text-body-sm font-semibold text-slate-900">
                        {source.name}
                      </span>
                      <span className="block truncate text-micro text-slate-500">
                        {kindLabel(kindOf(source))} · {source.freshness ?? 'freshness unknown'} ·{' '}
                        {source.volume ?? 0} row{(source.volume ?? 0) === 1 ? '' : 's'} ingested
                      </span>
                      <span
                        className="block truncate text-micro text-slate-500"
                        title={source.last_pull?.detail ?? undefined}
                      >
                        {lastPullNote(source)}
                      </span>
                    </span>
                  </span>
                  <span className="text-meta text-slate-600">{when(source.last_sync_at)}</span>
                  <StatusPill tone={SOURCE_STATUS_TONE[state] ?? 'neutral'} dot>
                    {plainStatus(state)}
                  </StatusPill>
                  <button
                    type="button"
                    /* Disabled for a channel the job skips, so pressing Sync
                       cannot produce a "successful" run that pulled nothing. */
                    disabled={syncingKind !== null || !isSyncable(source)}
                    title={
                      isSyncable(source)
                        ? undefined
                        : 'Only an active channel can be pulled — an administrator activates it on Connectors'
                    }
                    onClick={() => void syncNow(kindOf(source))}
                    className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-fine font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-500"
                  >
                    {syncingKind === kindOf(source) ? 'Queueing…' : 'Sync'}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <h3 className="mt-5 flex flex-wrap items-center gap-1.5 text-body font-semibold text-slate-900">
          Recent sync runs
          <InfoIcon className="h-4 w-4 shrink-0 text-slate-500" />
        </h3>
        <p className="mt-0.5 text-body-sm text-slate-500">
          A queued run waits for the scheduler or worker to drain it. <code className="rounded bg-slate-100 px-1 py-0.5 text-fine">Last sync</code> is stamped when
          a pull finishes, so it is the completion time and not the queue time.
        </p>

        <ResponsiveTable<SyncRun>
          className="mt-3"
          caption="Recent connector sync runs"
          minWidth="min-w-[480px]"
          maxHeight="max-h-[320px]"
          rows={syncRuns}
          rowKey={(run) => run.id}
          columns={[
            {
              key: 'job',
              label: 'Run',
              primary: true,
              render: (run) => (
                <span className="text-body-xs font-medium text-slate-800">
                  {jobLabel(run.job_type)}
                </span>
              ),
            },
            {
              key: 'started',
              label: 'Started',
              render: (run) => when(run.started_at),
            },
            {
              key: 'rows',
              label: 'Rows',
              render: (run) =>
                run.rows_processed == null ? '—' : run.rows_processed.toLocaleString(),
            },
            {
              key: 'duration',
              label: 'Duration',
              render: (run) =>
                run.duration_ms == null ? '—' : `${(run.duration_ms / 1000).toFixed(1)}s`,
            },
            {
              key: 'status',
              label: 'Status',
              render: (run) => (
                <StatusPill tone={runStatusTone(run.status)}>{plainStatus(run.status)}</StatusPill>
              ),
            },
          ]}
          empty={
            <p className="py-6 text-center text-body-sm text-slate-500">
              No sync has run yet. Queue one above and its status will appear here.
            </p>
          }
        />
      </section>
    </>
  );
}
