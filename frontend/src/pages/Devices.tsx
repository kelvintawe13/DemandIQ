import { useCallback, useEffect, useState } from 'react';
import {
  MonitorIcon,
  SmartphoneIcon,
  ShieldCheckIcon,
  KeyRoundIcon,
  FingerprintIcon } from
'lucide-react';
import { Panel } from '../components/ui/Panel';
import { StatusPill } from '../components/ui/StatusPill';
import { EmptyState } from '../components/ui/EmptyState';
import { LoadingState } from '../components/ui/LoadingState';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { FilterBar } from '../components/ui/FilterBar';
import { ViewSwitcher } from '../components/ui/ViewSwitcher';
import { Pagination } from '../components/ui/Pagination';
import { ResponsiveTable, type Column } from '../components/ui/Table';
import { useListState, type FilterDef } from '../hooks/useListState';
import { useToast } from '../contexts/ToastContext';
import { useSession } from '../contexts/SessionContext';
import { api, ApiException } from '../api/client';
import { mapSessions, type DeviceSession } from '../lib/sessions';

interface DevicesProps {
  onNavigate?: (id: string) => void;
}

type LoadError = { variant: 'error' | 'offline'; title: string; desc: string };

/** The same split every other page's `errMeta` makes: no network vs refused. */
function loadErrorFrom(e: unknown): LoadError {
  if (e instanceof ApiException) {
    const offline = !navigator.onLine || e.status === 0;
    return {
      variant: offline ? 'offline' : 'error',
      title: offline ? 'You are offline' : 'Could not load your devices',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  // A rejected fetch surfaces as a TypeError when the request never left.
  const offline = !navigator.onLine || e instanceof TypeError;
  return {
    variant: offline ? 'offline' : 'error',
    title: offline ? 'You are offline' : 'Could not load your devices',
    desc: offline ? 'Check your connection.' : e instanceof Error ? e.message : 'Failed to load',
  };
}

/**
 * Every place this account is signed in, and the way out of each one.
 *
 * The list lives on `/me/sessions`, the caller's own rows, so no capability
 * gate: a viewer reads and revokes exactly like an owner. The two flags the API
 * sends decide what a row may do. `current` (the token's `sid` claim) marks the
 * session this page was loaded with, and that row is labelled rather than
 * offered for sign-out: revoking it would end the session reading the screen.
 * `trusted_device` is the login's "remember this device" choice and is shown as
 * the badge it is; it was previously used as the reason to *withhold* the
 * revoke control, which left no way to sign a remembered device out.
 *
 * Rows are mapped by `lib/sessions.ts`, shared with the Profile panel and the
 * Settings Security tab, so the three agree on every field.
 */
export function Devices({ onNavigate }: DevicesProps) {
  const { user } = useSession();
  const { toast } = useToast();
  const [sessions, setSessions] = useState<DeviceSession[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  // Search and filters over the sessions. The named list below stays the
  // default rendering (its rows are what "This device" and "Trusted device"
  // refer to) while the view switcher offers the same rows as cards or a
  // table for scanning a long sign-in history.
  const sessionFilters: FilterDef<DeviceSession>[] = [
    {
      key: 'trust',
      label: 'Show',
      match: (session, value) =>
        value === 'this' ? session.current : value === 'trusted' ? session.trusted && !session.current : !session.current,
      options: [
        { value: '', label: 'Every session' },
        { value: 'this', label: 'This device' },
        { value: 'trusted', label: 'Remembered devices' },
        { value: 'other', label: 'Other devices' },
      ],
    },
  ];
  const list = useListState<DeviceSession>({
    key: 'data-devices',
    items: sessions ?? [],
    filters: sessionFilters,
    searchFields: (session) => [session.device, session.origin, session.time].filter(Boolean).join(' '),
    defaultView: 'list',
    pageSize: 12,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<unknown>('/api/v1/me/sessions');
      setSessions(mapSessions(data));
    } catch (e) {
      setSessions([]);
      setError(loadErrorFrom(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const confirmRevoke = async () => {
    const id = pendingId;
    if (!id) return;
    setRevoking(true);
    try {
      await api.delete(`/api/v1/me/sessions/${id}`);
      // Drop the row locally: a revoked session is gone from the list, and
      // re-reading it would only race the write.
      setSessions((current) => (current ?? []).filter((session) => session.id !== id));
      toast('That device is signed out and will need to sign in again.', 'info');
    } catch (e) {
      const message = e instanceof ApiException ? e.error.message : e instanceof Error ? e.message : 'Failed to revoke the session';
      toast(message, 'error');
    } finally {
      setRevoking(false);
    }
  };

  const others = (sessions ?? []).filter((session) => !session.current).length;

  const columns: Column<DeviceSession>[] = [
    {
      key: 'device',
      label: 'Device',
      primary: true,
      render: (session) => (
        <span>
          <span className="block truncate text-meta font-semibold text-slate-900">{session.device}</span>
          <span className="block text-micro text-slate-500">
            {session.origin ? `Signed in from ${session.origin} · ` : ''}
            {session.time || 'Last seen'}
          </span>
        </span>
      ),
    },
    {
      key: 'trust',
      label: 'Trust',
      render: (session) => (
        <span className="flex flex-wrap items-center gap-1.5">
          {session.trusted && <StatusPill tone="success">Trusted device</StatusPill>}
          {session.current ? (
            <StatusPill tone="success" dot>This device</StatusPill>
          ) : session.revocable ? null : (
            <StatusPill tone="neutral">Not revocable</StatusPill>
          )}
        </span>
      ),
    },
    {
      key: 'actions',
      label: '',
      cellClassName: 'pr-0',
      render: (session) =>
        !session.current && session.revocable ? (
          <button
            type="button"
            onClick={() => setPendingId(session.id)}
            aria-label={`Sign out ${session.device}`}
            className="shrink-0 rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
            Sign out
          </button>
        ) : null,
    },
  ];

  return (
    <>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel
          title="Where you're signed in"
          icon={MonitorIcon}
          description={`${sessions?.length ?? 0} active ${(sessions?.length ?? 0) === 1 ? 'session' : 'sessions'}${others > 0 ? ` · ${others} you can sign out` : ''}.`}>

          {loading ? (
            <LoadingState label="Loading your devices" size="sm" />
          ) : error ? (
            <EmptyState
              variant={error.variant}
              size="sm"
              title={error.title}
              description={error.desc}
              action={{ label: 'Retry', onClick: () => void load() }} />
          ) : !sessions || sessions.length === 0 ? (
            <EmptyState
              variant="empty"
              size="sm"
              title="No signed-in devices"
              description="This account has no active sessions. Sign in from another device and it will appear here." />
          ) : (
            <>
              <FilterBar<DeviceSession>
                search={list.search}
                onSearch={list.setSearch}
                searchPlaceholder="Search devices…"
                filters={sessionFilters}
                values={list.filterValues}
                onFilterChange={list.setFilter}
                onClear={list.clearFilters}
                activeCount={list.activeFilterCount}
                countLabel={list.filtered !== list.total ? `${list.filtered} of ${list.total} sessions` : undefined}>
                <ViewSwitcher value={list.view} onChange={list.setView} />
              </FilterBar>

              {list.view === 'table' ? (
                <ResponsiveTable<DeviceSession>
                  columns={columns}
                  rows={list.pageRows}
                  rowKey={(session) => session.id || session.device}
                  caption="Signed-in devices"
                  minWidth="min-w-[520px]"
                  className="mt-2"
                />
              ) : (
            <ul aria-label="Signed-in devices" className={list.view === 'cards' ? 'mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2' : 'mt-2 flex flex-col gap-2'}>
              {list.pageRows.map((session) => {
                const Icon = session.mobile ? SmartphoneIcon : MonitorIcon;
                return (
                  <li
                    key={session.id || session.device}
                    className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-slate-200 px-3.5 py-3">

                    <Icon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.8} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-xs font-semibold text-slate-900">
                        {session.device}
                      </span>
                      <span className="block text-micro text-slate-500">
                        {session.origin ? `Signed in from ${session.origin} · ` : ''}
                        {session.time || 'Last seen'}
                      </span>
                    </span>

                    {session.trusted && <StatusPill tone="success">Trusted device</StatusPill>}
                    {session.current ? (
                      <StatusPill tone="success" dot>This device</StatusPill>
                    ) : session.revocable ? (
                      <button
                        type="button"
                        onClick={() => setPendingId(session.id)}
                        aria-label={`Sign out ${session.device}`}
                        className="shrink-0 rounded-lg border border-slate-300 px-2.5 py-1.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                        Sign out
                      </button>
                    ) : (
                      <StatusPill tone="neutral">Not revocable</StatusPill>
                    )}
                  </li>);
              })}
            </ul>
              )}
              <Pagination
                page={list.page}
                pageCount={list.pageCount}
                onPage={list.setPage}
                total={list.filtered}
                from={list.from}
                to={list.to}
                noun="sessions"
              />
            </>
          )}

          <p className="mt-2.5 text-micro leading-relaxed text-slate-500">
            Signing a device out ends that session immediately; whoever is using it has to sign in again.
          </p>
        </Panel>

        <div className="flex flex-col gap-3">
          <Panel title="What these mean" icon={FingerprintIcon} description="Two different things, and only one is about access.">
            <ul className="flex flex-col gap-2.5">
              <li className="flex items-start gap-2.5">
                <ShieldCheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" strokeWidth={1.8} />
                <span className="min-w-0">
                  <span className="block text-meta font-semibold text-slate-900">This device</span>
                  <span className="block text-micro leading-relaxed text-slate-500">
                    The browser you are reading this in, identified by the session your sign-in token belongs to.
                    It is not offered for sign-out, because that would end the session showing you this page.
                  </span>
                </span>
              </li>
              <li className="flex items-start gap-2.5">
                <KeyRoundIcon className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" strokeWidth={1.8} />
                <span className="min-w-0">
                  <span className="block text-meta font-semibold text-slate-900">Trusted device</span>
                  <span className="block text-micro leading-relaxed text-slate-500">
                    The sign-in that remembered this device. It changes how often you are asked for a code, not what
                    the seat can reach, and a trusted device can still be signed out.
                  </span>
                </span>
              </li>
            </ul>
          </Panel>

          <Panel title="Sign-in protection" icon={ShieldCheckIcon} description="Password, two-factor and recovery codes.">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-3.5 py-3">
              <span className="min-w-0">
                <span className="block text-meta font-semibold text-slate-900">Two-factor authentication</span>
                <span className="block text-micro text-slate-500">
                  {user.mfaEnabled
                    ? 'On. A one-time code is required at sign-in.'
                    : 'Off. Your password is the only factor.'}
                </span>
              </span>
              <StatusPill tone={user.mfaEnabled ? 'success' : 'warning'} dot>
                {user.mfaEnabled ? 'Enabled' : 'Off'}
              </StatusPill>
            </div>
            <button
              type="button"
              onClick={() => onNavigate?.('settings')}
              className="mt-2.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              Security settings
            </button>
          </Panel>
        </div>
      </div>

      <ConfirmDialog
        open={pendingId !== null}
        onClose={() => setPendingId(null)}
        onConfirm={() => { void confirmRevoke(); }}
        title="Sign this device out?"
        body={revoking
          ? 'Signing out…'
          : 'That device will be signed out immediately and will need to sign in again.'}
        confirmLabel="Sign out"
        tone="danger" />
    </>
  );
}
