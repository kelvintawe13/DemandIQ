import { useCallback, useEffect, useState } from 'react';
import {
  SearchIcon,
  KeyRoundIcon,
  UserXIcon,
  UserCheckIcon,
  ShieldAlertIcon,
  MegaphoneIcon,
  WrenchIcon,
  UsersIcon,
  BuildingIcon,
  MapPinIcon,
  GaugeIcon,
  BrainCircuitIcon,
  ActivityIcon,
  RefreshCwIcon,
  PencilIcon,
  ChevronRightIcon,
  ShieldCheckIcon,
  DatabaseIcon,
} from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { StatCard } from '../../components/ui/StatCard';
import { StatusPill, StatusTone } from '../../components/ui/StatusPill';
import { TabBar } from '../../components/ui/TabBar';
import { LoadingState } from '../../components/ui/LoadingState';
import { TableShell, TRow, TD } from '../../components/ui/Table';
import { Modal, ModalButton } from '../../components/ui/Modal';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { useSiteMeta } from '../../contexts/SiteMetaContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { api, ApiException } from '../../api/client';
import { useSession } from '../../contexts/SessionContext';
import { ALL_ROLES, MAX_ASSIGNABLE_RANK, ROLE_KEY_BY_LABEL, ROLE_RANK, roleLabel } from '../../data/navItems';
import { auditClientLabel, auditLine, auditOutcomeLabel } from '../../lib/plainLanguage';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'users', label: 'Users' },
  { id: 'workspaces', label: 'Workspaces' },
  { id: 'settings', label: 'Settings' },
  { id: 'audit', label: 'Audit' },
] as const;
type Tab = (typeof TABS)[number]['id'];

const ROLE_TONE: Record<string, StatusTone> = {
  owner: 'success',
  administrator: 'info',
  analyst: 'warning',
  operator: 'neutral',
  viewer: 'neutral',
};

/** Roles the directory may hand out: everything but ownership. */
const GRANTABLE_ROLES = ALL_ROLES.filter((label) => label !== 'Owner');

function errMeta(e: unknown) {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error' as const, title: 'Sign in required', desc: e.error.message };
    if (e.status === 403)
      return { variant: 'error' as const, title: 'Site admin access required', desc: e.error.message };
    return {
      variant: 'error' as const,
      title: 'Request failed',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  if (e instanceof TypeError) return { variant: 'offline' as const, title: 'You are offline', desc: 'Check connection' };
  return { variant: 'offline' as const, title: 'Network error', desc: String(e) };
}

function when(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

function relative(iso?: string | null) {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

function uptime(seconds?: number) {
  if (seconds === undefined || seconds === null) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

/** One shared data-loading shape for every section. */
function useSection<T>(path: string | null, params?: Record<string, unknown>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta> | null>(null);
  const key = JSON.stringify(params ?? {});
  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      setData(params ? await api.get<T>(path, params) : await api.get<T>(path));
    } catch (e) {
      setError(errMeta(e));
    } finally {
      setLoading(false);
    }
    // `key` is the stable identity of `params`; callers pass plain objects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, loading, error, reload, setData };
}

function SectionError({ error, onRetry }: { error: ReturnType<typeof errMeta>; onRetry: () => void }) {
  return (
    <EmptyState
      variant={error.variant}
      title={error.title}
      description={error.desc}
      action={{ label: 'Retry', onClick: onRetry }}
      size="md"
    />
  );
}

/* ------------------------------------------------------------------ overview */

function OverviewSection() {
  const { data, loading, error, reload } = useSection<any>('/api/v1/admin/site/overview');
  const health = useSection<any>('/api/v1/admin/site/health');

  if (loading || health.loading) return <LoadingState label="Loading platform overview" />;
  if (error) return <SectionError error={error} onRetry={reload} />;
  if (!data) return null;

  const h = health.data;
  const roles = Object.entries(data.roles ?? {}) as [string, number][];
  const dbTone: StatusTone = h?.database?.status === 'ok' ? 'success' : 'critical';

  return (
    <div className="flex flex-col gap-3.5">
      <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-5">
        <StatCard
          label="Users"
          value={data.users?.total ?? '—'}
          sub={`${data.users?.active ?? 0} active`}
          icon={UsersIcon}
        />
        <StatCard label="Workspaces" value={data.workspaces ?? '—'} sub="tenants" icon={BuildingIcon} />
        <StatCard label="Locations" value={data.locations ?? '—'} sub="across all workspaces" icon={MapPinIcon} />
        <StatCard label="Forecasts" value={data.forecasts ?? '—'} sub="published runs" icon={GaugeIcon} />
        <StatCard
          label="Models"
          value={data.models ?? '—'}
          sub="registered versions"
          icon={BrainCircuitIcon}
        />
      </div>

      <div className="grid gap-3.5 lg:grid-cols-2">
        <Panel
          title="Platform health"
          description="Runtime facts, read live from the API."
          icon={ActivityIcon}
          action={
            health.data ? (
              <button
                type="button"
                onClick={() => void health.reload()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-micro font-semibold text-slate-600 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <RefreshCwIcon className="h-3.5 w-3.5" strokeWidth={2} />
                Re-check
              </button>
            ) : undefined
          }
        >
          {health.error ? (
            <SectionError error={health.error} onRetry={health.reload} />
          ) : !h ? (
            <EmptyState variant="empty" size="sm" title="No health data" description="The probe returned nothing." />
          ) : (
            <dl className="flex flex-col divide-y divide-slate-100 text-meta">
              {[
                ['API version', `${h.app?.name ?? 'API'} v${h.app?.version ?? '—'} · ${h.app?.environment ?? '—'}${h.app?.debug ? ' · debug' : ''}`],
                ['Database', `${h.database?.status ?? 'unknown'}${h.database?.latency_ms != null ? ` · ${h.database.latency_ms} ms` : ''}`],
                ['Uptime', uptime(h.uptime_seconds)],
                ['Migrations', h.migration_revision ?? 'unknown'],
                ['Live sessions', h.sessions?.active ?? 0],
                ['Last forecast', relative(h.pipeline?.latest_forecast_at)],
                ['Last training', relative(h.pipeline?.latest_training_at)],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex items-center justify-between gap-3 py-2">
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="text-right font-medium text-slate-800">
                    {label === 'Database' ? <StatusPill tone={dbTone}>{value}</StatusPill> : value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Panel>

        <Panel title="Seats by role" description="Every account on the platform, grouped by its bundle." icon={ShieldCheckIcon}>
          {roles.length === 0 ? (
            <EmptyState variant="empty" size="sm" title="No accounts" description="Nothing has been created yet." />
          ) : (
            <ul className="flex flex-col gap-1.5">
              {roles.map(([role, count]) => (
                <li key={role} className="flex items-center justify-between rounded-lg px-1 py-1.5 hover:bg-slate-50">
                  <span className="flex items-center gap-2">
                    <StatusPill tone={ROLE_TONE[role] ?? 'neutral'}>{roleLabel(role)}</StatusPill>
                    <code className="font-mono text-fine text-slate-500">{role}</code>
                  </span>
                  <b className="tabular-nums text-body-xs text-slate-800">{count}</b>
                </li>
              ))}
            </ul>
          )}
          {data.recent_users?.length > 0 && (
            <>
              <p className="mt-4 text-fine font-semibold uppercase tracking-wide text-slate-500">Newest sign-ups</p>
              <ul className="mt-1.5 flex flex-col gap-1 text-meta">
                {data.recent_users.map((u: any) => (
                  <li key={u.id} className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-slate-50">
                    <span className="min-w-0 truncate">
                      {u.name} <span className="text-slate-500">· {u.email}</span>
                    </span>
                    <StatusPill tone={ROLE_TONE[u.role] ?? 'neutral'}>{roleLabel(u.role)}</StatusPill>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- users */

const PAGE_SIZE = 25;

function UsersSection() {
  const { user } = useSession();
  const { toast } = useToast();
  const myRank = ROLE_RANK[user.roleKey] ?? 0;
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<any>(null);
  const [newPassword, setNewPassword] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const params = {
    ...(query ? { q: query } : {}),
    ...(roleFilter ? { role: roleFilter } : {}),
    ...(statusFilter ? { status: statusFilter } : {}),
    limit: PAGE_SIZE,
    offset,
  };
  const { data, loading, error, reload } = useSection<any>('/api/v1/admin/site/users', params);
  const detail = useSection<any>(detailId ? `/api/v1/admin/site/users/${detailId}` : null);

  const users: any[] = data?.data ?? [];
  const total: number = data?.total ?? 0;

  const grantable = GRANTABLE_ROLES.filter((label) => {
    const rank = ROLE_RANK[ROLE_KEY_BY_LABEL[label]] ?? 0;
    return rank < myRank && rank <= MAX_ASSIGNABLE_RANK;
  });

  /** Why this row's role cannot be changed (mirrors what the API enforces). */
  const lockedReason = (u: any): string | undefined => {
    if (u.is_site_admin) return 'Site administrator (managed outside the directory)';
    if (u.id === user.id) return 'You cannot change your own role';
    const rank = ROLE_RANK[String(u.role ?? 'viewer')] ?? 0;
    if (rank >= myRank) return `${roleLabel(String(u.role))} is at or above your role`;
    return undefined;
  };

  const patchUser = async (u: any, patch: any, message: string) => {
    setBusyId(u.id);
    try {
      await api.patch(`/api/v1/admin/site/users/${u.id}`, patch);
      toast(message);
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const doReset = async () => {
    if (!resetTarget) return;
    if (newPassword.length < 8) {
      toast('Password must be at least 8 characters', 'error');
      return;
    }
    try {
      // The endpoint answers `password_reset: true` only once the password is
      // stored and every live session for that account has been revoked, so the
      // confirmation is read from the API rather than assumed from a 200.
      const res = await api.post<{ id: string; password_reset?: boolean }>(
        `/api/v1/admin/site/users/${resetTarget.id}/reset-password`,
        { new_password: newPassword },
      );
      if (res?.password_reset !== true) {
        toast(
          `The password reset for ${resetTarget.email} was not confirmed by the API.`,
          'error',
        );
        return;
      }
      toast(`Password reset for ${resetTarget.email}. Their sessions were signed out.`);
      setResetTarget(null);
      setNewPassword('');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    }
  };

  if (loading && !data) return <LoadingState label="Loading user directory" />;
  if (error) return <SectionError error={error} onRetry={reload} />;

  return (
    <>
      <Panel
        title="Platform directory"
        description="Every account on the platform, across all workspaces."
        icon={UsersIcon}
        action={<span className="text-micro text-slate-500">{total} account{total === 1 ? '' : 's'}</span>}
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <SearchIcon
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
              strokeWidth={1.9}
            />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
              placeholder="Search name or email…"
              aria-label="Search users"
              className={`${inputClass} pl-8`}
            />
          </div>
          <select
            aria-label="Filter by role"
            value={roleFilter}
            onChange={(e) => {
              setRoleFilter(e.target.value);
              setOffset(0);
            }}
            className={`${inputClass} w-auto`}
          >
            <option value="">All roles</option>
            {ALL_ROLES.map((label) => (
              <option key={label} value={ROLE_KEY_BY_LABEL[label]}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setOffset(0);
            }}
            className={`${inputClass} w-auto`}
          >
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="unverified">Unverified</option>
            <option value="deactivated">Deactivated</option>
          </select>
        </div>

        <TableShell
          caption="Platform accounts"
          captionVisible
          maxHeight="max-h-[520px]"
          minWidth="min-w-[860px]"
          columns={[
            'User',
            'Workspace',
            'Role',
            'Status',
            'Joined',
            // The actions column: named for assistive tech, since a header cell
            // that is empty is not a header (axe `empty-table-header`).
            <span key="actions" className="sr-only">Actions</span>,
          ]}
          empty={
            // TableShell renders this inside the <tbody>, so a row is the right
            // wrapper — unlike a ResponsiveTable, whose slot replaces the table
            // and must not hold table markup.
            <tr>
              <td colSpan={6} className="py-6">
                <EmptyState
                  variant={query || roleFilter || statusFilter ? 'search' : 'empty'}
                  size="sm"
                  title={query || roleFilter || statusFilter ? 'No accounts match' : 'No accounts yet'}
                  description={
                    query || roleFilter || statusFilter
                      ? 'Try a different search or clear the filters.'
                      : 'Accounts appear here as people sign up or are invited.'
                  }
                />
              </td>
            </tr>
          }
        >
          {users.map((u) => {
            const locked = lockedReason(u);
            const current = String(u.role ?? 'viewer');
            const options = grantable.some((label) => ROLE_KEY_BY_LABEL[label] === current)
              ? grantable
              : ([...grantable, roleLabel(current)] as typeof grantable);
            return (
              <TRow key={u.id}>
                <TD scope="row">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-700 text-fine font-semibold text-white">
                      {(u.name ?? u.email ?? '?')
                        .split(' ')
                        .map((p: string) => p[0])
                        .join('')
                        .slice(0, 2)
                        .toUpperCase()}
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="block truncate text-meta font-semibold text-slate-900">{u.name}</span>
                        {u.is_site_admin && <StatusPill tone="info">site admin</StatusPill>}
                      </span>
                      <span className="block truncate text-fine text-slate-500">{u.email}</span>
                    </span>
                  </div>
                </TD>
                <TD className="text-slate-600">{u.workspace_name ?? '—'}</TD>
                <TD>
                  <select
                    aria-label={`Role for ${u.name}`}
                    value={current}
                    disabled={Boolean(locked) || busyId === u.id}
                    title={locked}
                    onChange={(e) => void patchUser(u, { role: e.target.value }, `Role → ${roleLabel(e.target.value)}`)}
                    className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-micro font-medium text-slate-700 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-50 disabled:text-slate-500"
                  >
                    {options.map((label) => (
                      <option key={label} value={ROLE_KEY_BY_LABEL[label]}>
                        {label}
                      </option>
                    ))}
                  </select>
                  {locked && <p className="mt-1 max-w-[190px] text-fine leading-snug text-slate-500">{locked}</p>}
                </TD>
                <TD>
                  <StatusPill tone={u.deactivated_at ? 'critical' : u.email_verified ? 'success' : 'warning'} dot>
                    {u.deactivated_at ? 'deactivated' : u.email_verified ? 'active' : 'unverified'}
                  </StatusPill>
                </TD>
                <TD className="whitespace-nowrap text-micro text-slate-500">{when(u.created_at)}</TD>
                <TD className="text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      type="button"
                      title="View account"
                      aria-label={`View account ${u.email}`}
                      onClick={() => setDetailId(u.id)}
                      className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <ChevronRightIcon className="h-4 w-4" strokeWidth={1.9} />
                    </button>
                    <button
                      type="button"
                      title="Set a new password"
                      aria-label={`Set a new password for ${u.email}`}
                      onClick={() => setResetTarget(u)}
                      className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <KeyRoundIcon className="h-4 w-4" strokeWidth={1.9} />
                    </button>
                    {u.deactivated_at ? (
                      <button
                        type="button"
                        title={locked ?? 'Reactivate'}
                        aria-label={`Reactivate ${u.email}`}
                        disabled={Boolean(locked) || busyId === u.id}
                        onClick={() => void patchUser(u, { deactivated: false }, 'Account reactivated')}
                        className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-emerald-600 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent"
                      >
                        <UserCheckIcon className="h-4 w-4" strokeWidth={1.9} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        title={locked ?? 'Deactivate'}
                        aria-label={`Deactivate ${u.email}`}
                        disabled={Boolean(locked) || busyId === u.id}
                        onClick={() => void patchUser(u, { deactivated: true }, 'Account deactivated')}
                        className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-rose-600 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent"
                      >
                        <UserXIcon className="h-4 w-4" strokeWidth={1.9} />
                      </button>
                    )}
                  </div>
                </TD>
              </TRow>
            );
          })}
        </TableShell>

        <div className="mt-3 flex items-center justify-between gap-2 text-micro text-slate-500">
          <span>
            {total === 0 ? 'Nothing to show' : `${offset + 1}–${Math.min(offset + users.length, total)} of ${total}`}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!data?.has_more}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 font-semibold text-white transition-colors hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </Panel>

      <Modal
        open={Boolean(detailId)}
        onClose={() => setDetailId(null)}
        title="Account detail"
        description={detail.data ? `${detail.data.name} · ${detail.data.email}` : 'Loading…'}
      >
        {detail.loading ? (
          <LoadingState label="Loading account" size="sm" />
        ) : detail.error ? (
          <SectionError error={detail.error} onRetry={detail.reload} />
        ) : detail.data ? (
          <div className="flex flex-col gap-4 text-meta">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {[
                ['Role', detail.data.role_label],
                ['Workspace', detail.data.workspace?.name ?? '—'],
                ['Job title', detail.data.job_title ?? '—'],
                ['Phone', detail.data.phone ?? '—'],
                ['MFA', detail.data.mfa_enabled ? 'Enabled' : 'Off'],
                ['Email verified', detail.data.email_verified ? 'Yes' : 'No'],
                ['Created', when(detail.data.created_at)],
                ['Last sign-in', relative(detail.data.last_seen_at)],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="text-slate-800">{value}</dd>
                </div>
              ))}
            </dl>

            <div>
              <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
                Live sessions ({detail.data.sessions?.length ?? 0})
              </p>
              {detail.data.sessions?.length ? (
                <ul className="flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {detail.data.sessions.map((s: any) => (
                    <li key={s.id} className="flex items-center justify-between gap-2 px-2.5 py-2">
                      <span className="min-w-0 truncate">
                        {s.device ?? 'Unknown device'}
                        {s.origin ? <span className="text-slate-500"> · {s.origin}</span> : null}
                      </span>
                      <span className="flex shrink-0 items-center gap-2 text-micro text-slate-500">
                        {s.trusted_device && <StatusPill tone="success">trusted</StatusPill>}
                        {relative(s.last_seen_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-caption text-slate-500">No active sessions. This account is signed out everywhere.</p>
              )}
            </div>

            {detail.data.recent_events?.length > 0 && (
              <div>
                <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
                  Recent privileged actions
                </p>
                <ul className="flex flex-col gap-1">
                  {detail.data.recent_events.map((e: any) => (
                    <li key={e.id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-fine text-slate-700">{auditLine(e.action, e.target)}</span>
                      <span className="shrink-0 text-fine text-slate-500">{[auditOutcomeLabel(e), when(e.created_at)].filter(Boolean).join(' · ')}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : null}
      </Modal>

      <Modal
        open={Boolean(resetTarget)}
        onClose={() => setResetTarget(null)}
        title={`Set new password: ${resetTarget?.email ?? ''}`}
      >
        <div className="flex flex-col gap-3">
          <Field id="new-password" label="New password (min 8 chars)">
            <input
              id="new-password"
              type="text"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className={inputClass}
            />
          </Field>
          <p className="text-micro text-slate-500">The user's live sessions are revoked. They must sign in again.</p>
          <div className="flex justify-end gap-2">
            <ModalButton tone="ghost" onClick={() => setResetTarget(null)}>
              Cancel
            </ModalButton>
            <ModalButton onClick={() => void doReset()}>Reset password</ModalButton>
          </div>
        </div>
      </Modal>
    </>
  );
}

/* ---------------------------------------------------------------- workspaces */

function WorkspacesSection() {
  const { toast } = useToast();
  const { data, loading, error, reload } = useSection<any>('/api/v1/admin/site/workspaces');
  const [editTarget, setEditTarget] = useState<any>(null);
  const [draft, setDraft] = useState({ name: '', business_type: '', country: '', timezone: '' });
  const [detailId, setDetailId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const detail = useSection<any>(detailId ? `/api/v1/admin/site/workspaces/${detailId}` : null);

  const workspaces: any[] = data?.data ?? [];

  const openEdit = (w: any) => {
    setEditTarget(w);
    setDraft({
      name: w.name ?? '',
      business_type: w.business_type ?? '',
      country: w.country ?? '',
      timezone: w.timezone ?? '',
    });
  };

  const save = async () => {
    if (!editTarget) return;
    setSaving(true);
    try {
      await api.patch(`/api/v1/admin/site/workspaces/${editTarget.id}`, {
        name: draft.name,
        business_type: draft.business_type || null,
        country: draft.country || null,
        timezone: draft.timezone || null,
      });
      toast(`${draft.name} updated`);
      setEditTarget(null);
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingState label="Loading workspaces" />;
  if (error) return <SectionError error={error} onRetry={reload} />;

  return (
    <>
      <Panel
        title="Workspaces"
        description="Every tenant on the platform, with what each one holds."
        icon={BuildingIcon}
        action={<span className="text-micro text-slate-500">{workspaces.length} tenant{workspaces.length === 1 ? '' : 's'}</span>}
      >
        <TableShell
          caption="Workspaces"
          captionVisible
          maxHeight="max-h-[520px]"
          minWidth="min-w-[880px]"
          columns={[
            'Workspace',
            'Type',
            'Country / TZ',
            'Users',
            'Locations',
            'Forecasts',
            'Created',
            <span key="actions" className="sr-only">Actions</span>,
          ]}
          empty={
            // Same as the accounts table above: TableShell, so the slot sits in
            // the <tbody> and takes a row.
            <tr>
              <td colSpan={8} className="py-6">
                <EmptyState
                  variant="empty"
                  size="sm"
                  title="No workspaces"
                  description="Tenants appear here once a workspace is created."
                />
              </td>
            </tr>
          }
        >
          {workspaces.map((w) => (
            <TRow key={w.id}>
              <TD scope="row" className="font-semibold text-slate-900">{w.name}</TD>
              <TD className="text-slate-600">{w.business_type ?? '—'}</TD>
              <TD className="text-slate-600">
                {w.country ?? '—'} · {w.timezone ?? '—'}
              </TD>
              <TD className="tabular-nums">{w.users}</TD>
              <TD className="tabular-nums">{w.locations}</TD>
              <TD className="tabular-nums">{w.forecasts}</TD>
              <TD className="whitespace-nowrap text-micro text-slate-500">{when(w.created_at)}</TD>
              <TD className="text-right">
                <div className="flex items-center justify-end gap-1.5">
                  <button
                    type="button"
                    title="View workspace"
                    aria-label={`View workspace ${w.name}`}
                    onClick={() => setDetailId(w.id)}
                    className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  >
                    <ChevronRightIcon className="h-4 w-4" strokeWidth={1.9} />
                  </button>
                  <button
                    type="button"
                    title="Edit workspace"
                    aria-label={`Edit workspace ${w.name}`}
                    onClick={() => openEdit(w)}
                    className="rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  >
                    <PencilIcon className="h-4 w-4" strokeWidth={1.9} />
                  </button>
                </div>
              </TD>
            </TRow>
          ))}
        </TableShell>
      </Panel>

      <Modal
        open={Boolean(editTarget)}
        onClose={() => setEditTarget(null)}
        title={`Edit ${editTarget?.name ?? 'workspace'}`}
        description="Renames and locale metadata are audited."
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setEditTarget(null)}>
              Cancel
            </ModalButton>
            <ModalButton onClick={() => void save()} disabled={saving || !draft.name.trim()}>
              {saving ? 'Saving…' : 'Save changes'}
            </ModalButton>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <Field id="ws-name" label="Name">
            <input
              id="ws-name"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              className={inputClass}
            />
          </Field>
          <Field id="ws-type" label="Business type" hint="e.g. cafe, restaurant, canteen">
            <input
              id="ws-type"
              value={draft.business_type}
              onChange={(e) => setDraft((d) => ({ ...d, business_type: e.target.value }))}
              className={inputClass}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="ws-country" label="Country" hint="ISO 3166-1 alpha-2">
              <input
                id="ws-country"
                maxLength={2}
                value={draft.country}
                onChange={(e) => setDraft((d) => ({ ...d, country: e.target.value.toUpperCase() }))}
                className={inputClass}
              />
            </Field>
            <Field id="ws-tz" label="Timezone" hint="IANA name">
              <input
                id="ws-tz"
                value={draft.timezone}
                onChange={(e) => setDraft((d) => ({ ...d, timezone: e.target.value }))}
                className={inputClass}
              />
            </Field>
          </div>
        </div>
      </Modal>

      <Modal
        open={Boolean(detailId)}
        onClose={() => setDetailId(null)}
        title="Workspace detail"
        description={detail.data ? detail.data.name : 'Loading…'}
      >
        {detail.loading ? (
          <LoadingState label="Loading workspace" size="sm" />
        ) : detail.error ? (
          <SectionError error={detail.error} onRetry={detail.reload} />
        ) : detail.data ? (
          <div className="flex flex-col gap-4 text-meta">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {(
                [
                  ['Users', detail.data.counts?.users],
                  ['Locations', detail.data.counts?.locations],
                  ['Forecasts', detail.data.counts?.forecasts],
                  ['Models', detail.data.counts?.models],
                ] as [string, number][]
              ).map(([label, value]) => (
                <div key={label} className="rounded-lg border border-slate-200 p-2.5">
                  <p className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</p>
                  <p className="text-h4 font-bold tabular-nums text-slate-900">{value ?? '—'}</p>
                </div>
              ))}
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {[
                ['Forecast window', detail.data.forecast_window?.first ? `${detail.data.forecast_window.first} → ${detail.data.forecast_window.last}` : 'No forecasts'],
                ['Created', when(detail.data.created_at)],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="text-slate-800">{value}</dd>
                </div>
              ))}
            </dl>
            <div>
              <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
                Members ({detail.data.members?.length ?? 0})
              </p>
              <ul className="flex flex-col divide-y divide-slate-100 rounded-lg border border-slate-200">
                {(detail.data.members ?? []).map((m: any) => (
                  <li key={m.id} className="flex items-center justify-between gap-2 px-2.5 py-2">
                    <span className="min-w-0 truncate">
                      {m.name} <span className="text-slate-500">· {m.email}</span>
                    </span>
                    <StatusPill tone={ROLE_TONE[m.role] ?? 'neutral'}>{roleLabel(m.role)}</StatusPill>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </Modal>
    </>
  );
}

/* ------------------------------------------------------------------ settings */

const KNOWN_SETTINGS = ['registration_open', 'default_role', 'announcement', 'support_email', 'maintenance_mode'];

function SettingsSection() {
  const { toast } = useToast();
  const { refresh: refreshBanner, applyLocal: applyBanner } = useSiteMeta();
  const { data, loading, error, reload } = useSection<any>('/api/v1/admin/site/settings');
  const [access, setAccess] = useState({ registration_open: true, default_role: 'viewer' });
  const [banner, setBanner] = useState({ announcement: '', support_email: '', maintenance_mode: false });
  const [advancedDraft, setAdvancedDraft] = useState<Record<string, string>>({});
  const [savingAccess, setSavingAccess] = useState(false);
  const [savingBanner, setSavingBanner] = useState(false);
  const [savingAdvanced, setSavingAdvanced] = useState(false);

  const settings = data?.data ?? {};
  const advancedKeys = Object.keys(settings).filter((k) => !KNOWN_SETTINGS.includes(k));

  useEffect(() => {
    if (!data?.data) return;
    setAccess({
      registration_open: data.data.registration_open?.value === true,
      default_role: String(data.data.default_role?.value ?? 'viewer'),
    });
    setBanner({
      announcement: String(data.data.announcement?.value ?? ''),
      support_email: String(data.data.support_email?.value ?? ''),
      maintenance_mode: data.data.maintenance_mode?.value === true,
    });
    setAdvancedDraft(
      Object.fromEntries(advancedKeys.map((k) => [k, JSON.stringify(settings[k]?.value ?? null)])),
    );
    // advancedKeys is derived from `data`; re-seeding on every fetch is intended.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const saveAccess = async () => {
    setSavingAccess(true);
    try {
      await api.patch('/api/v1/admin/site/settings', { settings: access });
      toast('Registration settings saved');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setSavingAccess(false);
    }
  };

  const saveBanner = async () => {
    if (
      banner.maintenance_mode &&
      !window.confirm(
        'Enable maintenance mode? All non-admin writes will be rejected with 503 until you turn it off.',
      )
    )
      return;
    if (banner.announcement.length > 280) {
      toast('Announcement must be 280 characters or fewer', 'error');
      return;
    }
    setSavingBanner(true);
    const patch = { ...banner };
    try {
      await api.patch('/api/v1/admin/site/settings', { settings: patch });
      applyBanner(patch); // optimistic — the live banner updates without a reload
      void refreshBanner();
      toast(banner.maintenance_mode ? 'Banner saved. Maintenance mode is ON.' : 'Banner saved');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setSavingBanner(false);
    }
  };

  const saveAdvanced = async () => {
    const parsed: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(advancedDraft)) {
      try {
        parsed[k] = JSON.parse(v);
      } catch {
        toast(`${k}: value must be valid JSON (e.g. true, "text", 3)`, 'error');
        return;
      }
    }
    setSavingAdvanced(true);
    try {
      await api.patch('/api/v1/admin/site/settings', { settings: parsed });
      toast('Advanced settings saved');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setSavingAdvanced(false);
    }
  };

  if (loading) return <LoadingState label="Loading platform settings" />;
  if (error) return <SectionError error={error} onRetry={reload} />;

  const roleOptions = ['viewer', 'operator', 'analyst', 'administrator'];
  const roleChoices = roleOptions.includes(access.default_role)
    ? roleOptions
    : [access.default_role, ...roleOptions];

  return (
    <div className="flex flex-col gap-3.5">
      <Panel
        title="Registration & seats"
        description="Who may create an account, and what they get when they do."
        icon={ShieldCheckIcon}
      >
        <div className="flex flex-col gap-3">
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-200 p-3 transition-colors hover:bg-slate-50">
            <input
              type="checkbox"
              checked={access.registration_open}
              onChange={(e) => setAccess((a) => ({ ...a, registration_open: e.target.checked }))}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-brand-600"
            />
            <span>
              <span className="text-meta font-semibold text-slate-800">Registration open</span>
              <span className="block text-micro text-slate-500">
                When off, the sign-up form closes and new accounts are refused with 403.
              </span>
            </span>
          </label>

          <Field
            id="default-role"
            label="Seat granted at sign-up"
            hint="A new sign-up can never claim more than this. Ownership and administrator are not offered here."
          >
            <select
              id="default-role"
              value={access.default_role}
              onChange={(e) => setAccess((a) => ({ ...a, default_role: e.target.value }))}
              className={inputClass}
            >
              {roleChoices.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
          </Field>

          <div>
            <button
              type="button"
              disabled={savingAccess}
              onClick={() => void saveAccess()}
              className="rounded-lg bg-brand-700 px-4 py-2 text-body-xs font-semibold text-white transition-colors hover:bg-brand-800 disabled:opacity-50"
            >
              {savingAccess ? 'Saving…' : 'Save registration'}
            </button>
          </div>
        </div>
      </Panel>

      <Panel
        title="Banner & status"
        description="Shown to every signed-in user through the top banner."
        icon={MegaphoneIcon}
      >
        <div className="flex flex-col gap-3">
          <Field
            id="banner-announcement"
            label="Announcement"
            hint="Plain text, e.g. 'New: Kigali demand data loaded'. Empty hides the banner (when maintenance is off)."
          >
            <input
              id="banner-announcement"
              value={banner.announcement}
              onChange={(e) => setBanner((b) => ({ ...b, announcement: e.target.value }))}
              placeholder="What should everyone see?"
              className={inputClass}
            />
          </Field>

          <Field id="banner-support" label="Support email" hint="Linked on the maintenance banner and error screens.">
            <input
              id="banner-support"
              type="email"
              value={banner.support_email}
              onChange={(e) => setBanner((b) => ({ ...b, support_email: e.target.value }))}
              className={inputClass}
            />
          </Field>

          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-200 p-3 transition-colors hover:bg-slate-50">
            <input
              type="checkbox"
              checked={banner.maintenance_mode}
              onChange={(e) => setBanner((b) => ({ ...b, maintenance_mode: e.target.checked }))}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-brand-600"
            />
            <span>
              <span className="text-meta font-semibold text-slate-800">Maintenance mode</span>
              <span className="block text-micro text-slate-500">
                Freezes all writes platform-wide (503) except for site admins. Reads stay open.
              </span>
            </span>
          </label>

          <div>
            <p className="mb-1.5 text-fine font-semibold uppercase tracking-wide text-slate-500">
              Preview: what users see
            </p>
            <div className="overflow-hidden rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-white px-3 py-1 text-fine font-semibold uppercase tracking-wide text-slate-500">
                App chrome
              </div>
              {banner.maintenance_mode ? (
                <div role="alert" className="flex items-center justify-center gap-2 bg-amber-100 px-4 py-1.5 text-meta font-medium text-amber-900">
                  <WrenchIcon className="h-3.5 w-3.5" aria-hidden="true" />
                  Scheduled maintenance. Saving is temporarily disabled.
                  {banner.support_email && (
                    <a className="underline underline-offset-2" href={`mailto:${banner.support_email}`}>
                      {banner.support_email}
                    </a>
                  )}
                </div>
              ) : banner.announcement ? (
                <div role="status" className="flex items-center justify-center gap-2 bg-brand-50 px-4 py-1.5 text-meta font-medium text-brand-800">
                  <MegaphoneIcon className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="truncate">{banner.announcement}</span>
                </div>
              ) : (
                <div className="bg-slate-50 px-4 py-3 text-center text-caption text-slate-500">
                  No banner. The announcement is empty and maintenance is off.
                </div>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={savingBanner}
              onClick={() => void saveBanner()}
              className="rounded-lg bg-brand-700 px-4 py-2 text-body-xs font-semibold text-white transition-colors hover:bg-brand-800 disabled:opacity-50"
            >
              {savingBanner ? 'Saving…' : banner.maintenance_mode ? 'Save & enable maintenance' : 'Save banner'}
            </button>
            <button
              type="button"
              disabled={savingBanner}
              onClick={() =>
                setBanner({
                  announcement: String(settings.announcement?.value ?? ''),
                  support_email: String(settings.support_email?.value ?? ''),
                  maintenance_mode: settings.maintenance_mode?.value === true,
                })
              }
              className="rounded-lg border border-slate-300 px-3 py-2 text-meta font-semibold text-slate-600 transition-colors hover:bg-slate-50 disabled:opacity-50"
            >
              Reset
            </button>
          </div>
        </div>
      </Panel>

      <Panel
        title="Advanced"
        description="Raw platform settings. Everything above is edited here too. This is the escape hatch for keys the console does not know about."
        icon={DatabaseIcon}
      >
        {advancedKeys.length === 0 ? (
          <p className="text-meta text-slate-500">
            No extra settings stored. Every key the platform knows is covered by the sections above.
          </p>
        ) : (
          <details>
            <summary className="cursor-pointer text-meta font-semibold text-slate-700">
              {advancedKeys.length} custom key{advancedKeys.length === 1 ? '' : 's'}
            </summary>
            <div className="mt-3 flex flex-col gap-3">
              {advancedKeys.map((key) => (
                <Field key={key} id={`set-${key}`} label={key} hint={settings[key]?.description ?? undefined}>
                  <input
                    id={`set-${key}`}
                    value={advancedDraft[key] ?? JSON.stringify(settings[key]?.value ?? null)}
                    onChange={(e) => setAdvancedDraft((d) => ({ ...d, [key]: e.target.value }))}
                    className={inputClass}
                  />
                </Field>
              ))}
              <div>
                <button
                  type="button"
                  disabled={savingAdvanced}
                  onClick={() => void saveAdvanced()}
                  className="rounded-lg bg-brand-700 px-4 py-2 text-body-xs font-semibold text-white transition-colors hover:bg-brand-800 disabled:opacity-50"
                >
                  {savingAdvanced ? 'Saving…' : 'Save advanced settings'}
                </button>
                <p className="mt-1.5 text-fine text-slate-500">Values are JSON: booleans bare, strings quoted.</p>
              </div>
            </div>
          </details>
        )}
      </Panel>
    </div>
  );
}

/* --------------------------------------------------------------------- audit */

const AUDIT_ACTIONS = [
  { value: '', label: 'All actions' },
  { value: 'site.user', label: 'User changes' },
  { value: 'site.workspace', label: 'Workspace changes' },
  { value: 'site.settings', label: 'Settings changes' },
];

function AuditSection() {
  const [query, setQuery] = useState('');
  const [action, setAction] = useState('');
  const [client, setClient] = useState('');
  const [offset, setOffset] = useState(0);
  const params = {
    ...(query ? { q: query } : {}),
    ...(action ? { action } : {}),
    ...(client ? { client } : {}),
    limit: PAGE_SIZE,
    offset,
  };
  const { data, loading, error, reload } = useSection<any>('/api/v1/admin/site/audit', params);
  const entries: any[] = data?.data ?? [];
  const total: number = data?.total ?? 0;

  if (loading && !data) return <LoadingState label="Loading audit log" />;
  if (error) return <SectionError error={error} onRetry={reload} />;

  return (
    <Panel
      title="Audit log"
      description="Every privileged platform action, with the actor and the client it came from."
      icon={ShieldAlertIcon}
      action={<span className="text-micro text-slate-500">{total} event{total === 1 ? '' : 's'}</span>}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <SearchIcon
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
            strokeWidth={1.9}
          />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOffset(0);
            }}
            placeholder="Search action or actor…"
            aria-label="Search audit log"
            className={`${inputClass} pl-8`}
          />
        </div>
        <select
          aria-label="Filter by action"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
          className={`${inputClass} w-auto`}
        >
          {AUDIT_ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by client"
          value={client}
          onChange={(e) => {
            setClient(e.target.value);
            setOffset(0);
          }}
          className={`${inputClass} w-auto`}
        >
          <option value="">Any client</option>
          <option value="web">Web</option>
          <option value="mobile">Mobile</option>
        </select>
      </div>

      {entries.length === 0 ? (
        <EmptyState
          variant={query || action || client ? 'search' : 'empty'}
          size="sm"
          title={query || action || client ? 'No events match' : 'No admin actions yet'}
          description={
            query || action || client
              ? 'Try a different search or clear the filters.'
              : 'Every privileged action taken in the console will be recorded here.'
          }
        />
      ) : (
        <ol className="flex flex-col divide-y divide-slate-100">
          {entries.map((a) => (
            <li key={a.id} className="flex flex-col gap-1 py-2.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <p className="min-w-0 truncate text-meta font-semibold text-slate-800">
                  <ShieldAlertIcon className="mr-1.5 inline h-3.5 w-3.5 text-slate-500" strokeWidth={1.9} />
                  {auditLine(a.action, a.target)}
                </p>
                <span className="shrink-0 text-micro text-slate-500">{[auditOutcomeLabel(a), when(a.created_at)].filter(Boolean).join(' · ')}</span>
              </div>
              <p className="text-micro text-slate-500">
                {a.actor_email ?? 'system'} · {auditClientLabel(a.client) ?? 'the web app'}
              </p>
              {a.meta && Object.keys(a.meta).length > 0 && (
                <details>
                  <summary className="cursor-pointer text-fine font-semibold text-slate-500">Details</summary>
                  <pre className="mt-1 overflow-x-auto rounded-lg bg-slate-50 p-2 text-fine text-slate-700">
                    {JSON.stringify(a.meta, null, 2)}
                  </pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}

      <div className="mt-3 flex items-center justify-between gap-2 text-micro text-slate-500">
        <span>
          {total === 0 ? 'Nothing to show' : `${offset + 1}–${Math.min(offset + entries.length, total)} of ${total}`}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={!data?.has_more}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className="rounded-lg bg-brand-700 px-3 py-2.5 sm:py-1.5 font-semibold text-white transition-colors hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------------------- page */

export function SiteAdmin() {
  const [tab, setTab] = useState<Tab>('overview');

  return (
    <div className="flex flex-col gap-3.5">
      <TabBar
        tabs={TABS}
        value={tab}
        onChange={setTab}
        ariaLabel="Site administration"
        className="self-start"
      />

      <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
        {tab === 'overview' && <OverviewSection />}
        {tab === 'users' && <UsersSection />}
        {tab === 'workspaces' && <WorkspacesSection />}
        {tab === 'settings' && <SettingsSection />}
        {tab === 'audit' && <AuditSection />}
      </div>
    </div>
  );
}
