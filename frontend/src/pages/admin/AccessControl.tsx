import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangleIcon,
  CheckIcon,
  KeyRoundIcon,
  MailIcon,
  MinusIcon,
  MonitorIcon,
  PencilIcon,
  PlugZapIcon,
  ShieldCheckIcon,
  SmartphoneIcon,
  UserPlusIcon,
  UsersIcon,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Panel } from '../../components/ui/Panel';
import { StatCard, type StatTone } from '../../components/ui/StatCard';
import { LoadingState } from '../../components/ui/LoadingState';
import { TabBar } from '../../components/ui/TabBar';
import { StatusPill } from '../../components/ui/StatusPill';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Field, inputClass } from '../../components/ui/Field';
import { useToast } from '../../contexts/ToastContext';
import { EmptyState } from '../../components/ui/EmptyState';
import { RecordList } from '../../components/ui/RecordList';
import { FormModal } from '../../components/ui/FormModal';
import { CountChart } from '../../components/ui/CountChart';
import { MixDonut } from '../../components/ui/MixDonut';
import { api, ApiException } from '../../api/client';
import { useSession } from '../../contexts/SessionContext';
import { MAX_ASSIGNABLE_RANK, ROLE_RANK, roleLabel } from '../../data/navItems';
import { auditClientLabel } from '../../lib/plainLanguage';
import {
  AUDIT_SURFACES,
  allCapabilities,
  auditActionCounts,
  auditLines,
  auditSurfaceMix,
  capabilityCounts,
  capabilityGaps,
  groupedCapabilities,
  inviteMix,
  memberRoleCounts,
  rejectedCount,
  rolesInUse,
  sharePercent,
  type AuditEntry,
  type MemberRow,
  type RoleRow,
} from '../../lib/accessControl';

const tabs = ['Members', 'Roles', 'Activity'] as const;
const ACCESS_TABS = tabs.map((label) => ({ id: label, label }));

/**
 * The audit summary window.
 *
 * The timeline is paged at 20 and the reader can walk it; the four charts and the
 * page's KPIs need a window wide enough to be worth drawing, so one extra read
 * takes the last 100 events. Every panel that reads it says which window it is
 * describing — "the last 100 privileged actions" is a claim the page can support,
 * "all activity" is not.
 */
const AUDIT_WINDOW = 100;
const AUDIT_PAGE = 20;

// Keyed off the API's own values (`web`/`mobile`/`api`, see
// backend/app/middleware/audit.py), lower-cased so a capitalised `channel` from
// another shape matches too. Without the normalisation every row fell back to
// the desktop icon, because the keys were "Web"/"Mobile"/"API".
const channelIcon: Record<string, LucideIcon> = { web: MonitorIcon, mobile: SmartphoneIcon, api: PlugZapIcon };

function errMeta(e: unknown) {
  if (e instanceof ApiException) {
    if (e.status === 401) return { variant: 'error' as const, title: 'Sign in required', desc: e.error.message };
    if (e.status === 403) return { variant: 'error' as const, title: 'Admin access required', desc: e.error.message };
    return {
      variant: 'error' as const,
      title: 'Failed to load access',
      desc: e.error.message + (e.reference ? ` (ref: ${e.reference})` : ''),
    };
  }
  if (e instanceof TypeError) return { variant: 'offline' as const, title: 'You are offline', desc: 'Check connection' };
  return { variant: 'offline' as const, title: 'Network error', desc: String(e) };
}

/** Role rows used when GET /roles is unavailable — same shape as the API's. */
const FALLBACK_ROLES: RoleRow[] = [
  { role: 'owner', label: 'Owner', rank: 4, assignable: false },
  { role: 'administrator', label: 'Administrator', rank: 3, assignable: true },
  { role: 'analyst', label: 'Analyst', rank: 2, assignable: true },
  { role: 'operator', label: 'Data Clerk', rank: 1, assignable: true },
  { role: 'viewer', label: 'Viewer', rank: 0, assignable: true },
];

/** Rank of a role row (API or fallback), by published rank then by role value. */
function rankOfRole(row: any): number {
  if (typeof row?.rank === 'number') return row.rank;
  return ROLE_RANK[String(row?.role ?? '').toLowerCase()] ?? 0;
}

export function AccessControl() {
  const { user } = useSession();
  const myRank = ROLE_RANK[user.roleKey] ?? 0;
  const [tab, setTab] = useState<(typeof tabs)[number]>('Members');
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [roles, setRoles] = useState<RoleRow[]>([]);
  // Capability groups, as published by GET /roles (single source of truth with
  // the API's own enforcement — see backend/app/security/permissions.py).
  const [groups, setGroups] = useState<any[]>([]);
  // The timeline page, and the wider window the charts and KPIs describe.
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [summary, setSummary] = useState<AuditEntry[]>([]);
  const [summaryCapped, setSummaryCapped] = useState(false);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<ReturnType<typeof errMeta> | null>(null);
  const [auditMore, setAuditMore] = useState(false);
  const [auditCursor, setAuditCursor] = useState<string | null>(null);
  const [auditHistory, setAuditHistory] = useState<Array<string | null>>([]);
  // Server-side audit filters: `actor_id` and `client` are the two the endpoint
  // accepts that a person can answer. `action` is the raw request line, so it is
  // not offered as a control — a filter list of endpoints would print the very
  // tokens the rows go out of their way to avoid.
  const [actorFilter, setActorFilter] = useState('all');
  const [surfaceFilter, setSurfaceFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ReturnType<typeof errMeta> | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invite, setInvite] = useState({ name: '', email: '', role: 'operator' });
  // The edit dialog: name and role for an existing member. The row keeps its
  // quick role picker; the modal is for the change that needs a second look.
  const [editMember, setEditMember] = useState<any>(null);
  const [editDraft, setEditDraft] = useState({ name: '', role: 'viewer' });
  const [revoking, setRevoking] = useState<MemberRow | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { toast } = useToast();

  // Roles this account may grant: strictly below its own rank, and never
  // ownership. Mirrors the hierarchy the API enforces (permissions.py), so the
  // console cannot offer a change the server would refuse.
  const assignableRoles = (roles.length ? roles : FALLBACK_ROLES).filter(
    (r: any) => r.assignable !== false && rankOfRole(r) <= MAX_ASSIGNABLE_RANK && rankOfRole(r) < myRank,
  );

  /** Why a row's role cannot be changed (undefined when it can). */
  const lockedReason = (member: any): string | undefined => {
    if (!user.id || member.id === user.id) return 'You cannot change your own role';
    const targetRank = rankOfRole({ role: member.role });
    if (targetRank >= myRank) return `${roleLabel(String(member.role ?? 'viewer'))} is at or above your role`;
    return undefined;
  };

  /**
   * The two directory reads. No cursor on `/members`: the endpoint is not
   * paginated and returns the whole roster, so the console no longer offers it a
   * pager. `/roles` failing is not fatal — the page falls back to the published
   * ladder, which is why it is caught rather than thrown.
   */
  const fetchDirectory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mRes, rRes] = await Promise.all([
        api.get<{ data: MemberRow[] }>('/api/v1/members'),
        api.get<{ data: RoleRow[]; groups?: any[] }>('/api/v1/roles').catch(() => ({ data: [], groups: [] }) as any),
      ]);
      setMembers(mRes.data ?? []);
      setRoles(rRes.data ?? (Array.isArray(rRes) ? rRes : []));
      setGroups(rRes.groups ?? []);
    } catch (e) {
      setError(errMeta(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchDirectory();
  }, [fetchDirectory]);

  /**
   * One audit read: the timeline page, and the wider window behind the charts.
   *
   * Both take the same filters, so a chart can never describe a different set of
   * events from the list under it. `filters` is passed explicitly as well as
   * read from state because a `<select>` change sets state and calls this in the
   * same tick — reading the state alone would re-read the trail with the filter
   * the reader just moved away from.
   */
  const fetchAudit = useCallback(
    async (cursor?: string | null, filters?: { actor?: string; surface?: string }) => {
      const actor = filters?.actor ?? actorFilter;
      const surface = filters?.surface ?? surfaceFilter;
      const params = {
        actor_id: actor === 'all' ? undefined : actor,
        client: surface === 'all' ? undefined : surface,
      };
      setAuditLoading(true);
      setAuditError(null);
      try {
        const [page, wide] = await Promise.all([
          api.get<{ data: AuditEntry[]; has_more?: boolean; next_cursor?: string | null }>(
            '/api/v1/audit-log',
            { ...params, limit: AUDIT_PAGE, ...(cursor ? { cursor } : {}) },
          ),
          api.get<{ data: AuditEntry[]; has_more?: boolean }>('/api/v1/audit-log', {
            ...params,
            limit: AUDIT_WINDOW,
          }),
        ]);
        setAudit(page.data ?? []);
        setAuditMore(!!page.has_more);
        setAuditCursor(page.next_cursor ?? null);
        setSummary(wide.data ?? []);
        setSummaryCapped(!!wide.has_more);
      } catch (e) {
        setAudit([]);
        setSummary([]);
        setAuditError(errMeta(e));
      } finally {
        setAuditLoading(false);
      }
    },
    [actorFilter, surfaceFilter],
  );

  // Moving either filter re-reads the trail. The directory is not re-read: the
  // two filters are the audit endpoint's own and change nothing about who has
  // access.
  useEffect(() => {
    void fetchAudit(null);
    setAuditHistory([]);
  }, [fetchAudit]);

  /** Everything, after a write that changes the roster. */
  const reload = useCallback(async () => {
    await fetchDirectory();
    await fetchAudit(null);
    setAuditHistory([]);
  }, [fetchDirectory, fetchAudit]);

  /*
   * Everything the page counts, derived once from the rows it renders. A tile, a
   * chart and a table that each re-derive "how many administrators" will
   * eventually disagree, and on a permissions screen that is the number the
   * reader trusts least.
   */
  const roleList = roles.length ? roles : FALLBACK_ROLES;
  const overview = useMemo(() => {
    const invites = inviteMix(members);
    const inUse = rolesInUse(members);
    return {
      invites,
      inUse,
      byRole: memberRoleCounts(members, roleList),
      capabilities: capabilityCounts(roleList),
      capabilityTotal: allCapabilities(roleList).length,
      surfaces: auditSurfaceMix(summary),
      actions: auditActionCounts(summary),
      refused: rejectedCount(summary),
      // The role with the most capabilities, named only when it is *not* the
      // most senior one — which is the surprising case this page now shows.
      richest: [...capabilityCounts(roleList)].sort((a, b) => b.count - a.count)[0],
      senior: capabilityCounts(roleList)[0],
    };
  }, [members, roleList, summary]);

  const lines = useMemo(() => auditLines(audit), [audit]);

  /*
   * The Roles tab's two charts come from two API lists that do not cover the
   * same capabilities (see `capabilityGaps`). This is the sentence that keeps
   * them from silently disagreeing.
   */
  const capabilityNote = useMemo(() => {
    if (groups.length === 0) return null;
    const gaps = capabilityGaps(roleList, groups);
    const listed = groupedCapabilities(groups).length;
    const parts: string[] = [];
    if (gaps.heldButUngrouped.length > 0) {
      parts.push(
        `${gaps.heldButUngrouped.length} the roles hold ${gaps.heldButUngrouped.length === 1 ? 'is' : 'are'} listed under no area (${gaps.heldButUngrouped.join(', ')})`,
      );
    }
    if (gaps.groupedButUnheld.length > 0) {
      parts.push(
        `${gaps.groupedButUnheld.length} listed ${gaps.groupedButUnheld.length === 1 ? 'capability is' : 'capabilities are'} held by no role (${gaps.groupedButUnheld.join(', ')})`,
      );
    }
    if (parts.length === 0) return null;
    return `The two charts above draw from two API lists, and they do not cover the same capabilities: the roles hold ${overview.capabilityTotal}, the areas list ${listed}. ${parts.join('; ')}.`;
  }, [groups, roleList, overview.capabilityTotal]);

  const sendInvite = async () => {
    const name = invite.name.trim();
    const email = invite.email.trim();
    if (!name || !email.includes('@')) return;
    setSaving(true);
    try {
      await api.post('/api/v1/members/invite', { email, role: invite.role, name });
      toast(`Invitation sent to ${email}.`);
      setInvite({ name: '', email: '', role: 'operator' });
      setInviteOpen(false);
      void reload();
    } catch (e) {
      if (e instanceof ApiException) {
        if (e.status === 409) toast('Already invited', 'error');
        else toast(e.error.message, 'error');
      }
    } finally {
      setSaving(false);
    }
  };

  const updateMember = async (id: string, patch: any) => {
    setBusyId(id);
    try {
      await api.patch(`/api/v1/members/${id}`, patch);
      toast('Member updated');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };
  const openEdit = (member: any) => {
    setEditMember(member);
    setEditDraft({ name: member.name ?? '', role: String(member.role ?? 'viewer') });
  };
  const editLocked = editMember ? lockedReason(editMember) : undefined;
  const saveEdit = async () => {
    if (!editMember) return;
    await updateMember(editMember.id, { name: editDraft.name.trim() || undefined, role: editDraft.role });
    setEditMember(null);
  };

  /**
   * Revoking access is irreversible, so it is confirmed — and confirmed in the
   * app's own dialog rather than `window.confirm`, which is unstyled, cannot be
   * translated, and blocks the page while a test waits on the DOM.
   */
  const revokeAccess = async () => {
    if (!revoking?.id) return;
    try {
      await api.delete(`/api/v1/members/${revoking.id}`);
      toast('Access revoked', 'info');
      void reload();
    } catch (e) {
      if (e instanceof ApiException) toast(e.error.message, 'error');
    }
  };

  const changeFilters = (next: { actor?: string; surface?: string }) => {
    if (next.actor !== undefined) setActorFilter(next.actor);
    if (next.surface !== undefined) setSurfaceFilter(next.surface);
    setAuditHistory([]);
    void fetchAudit(null, next);
  };

  if (loading) return <LoadingState label="Loading access control" />;
  if (error)
    return (
      <EmptyState
        variant={error.variant}
        title={error.title}
        description={error.desc}
        action={{ label: 'Retry', onClick: () => void reload() }}
        size="md"
      />
    );

  const adminCount = members.filter((m) => ['owner', 'administrator'].includes(String(m.role ?? ''))).length;
  /*
   * What the summary window actually is, in words.
   *
   * The charts and three of the tiles describe the *loaded window*, not the
   * whole history: `/audit-log` is cursor-paginated, so "45 actions" is a claim
   * about the last 100 events, and the page has to say so or it is stating a
   * total it never read.
   */
  const windowLabel = summaryCapped
    ? `the last ${AUDIT_WINDOW} privileged actions`
    : `all ${summary.length} recorded action${summary.length === 1 ? '' : 's'}`;

  return (
    <div className="flex flex-col gap-3">
      {/*
        Who can do what, before the lists. Four tiles: how many people there are,
        how many have not accepted yet, how much privileged activity the audit
        trail holds, and how much of it was refused — the last being the one an
        owner opens this page looking for.
      */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {(
          [
            {
              label: 'Members',
              value: String(members.length),
              detail:
                members.length === 0
                  ? 'Nobody has access yet'
                  : `${adminCount} with admin rights · ${overview.inUse.length} of ${roleList.length} roles in use`,
              icon: UsersIcon,
              tone: 'default' as StatTone,
            },
            {
              label: 'Awaiting invite',
              value: String(overview.invites.invited),
              detail:
                overview.invites.invited === 0
                  ? 'Everyone has accepted'
                  : 'Invited, but has not signed in yet',
              icon: MailIcon,
              tone: (overview.invites.invited === 0 ? 'success' : 'warning') as StatTone,
            },
            {
              label: 'Privileged actions',
              value: summary.length ? String(summary.length) : '—',
              detail: summary.length ? `across ${windowLabel}` : 'Nothing recorded yet',
              icon: KeyRoundIcon,
              tone: 'default' as StatTone,
            },
            {
              label: 'Refused',
              value: String(overview.refused),
              detail:
                overview.refused === 0
                  ? `Nothing refused in ${windowLabel}`
                  : `Refused in ${windowLabel}`,
              icon: AlertTriangleIcon,
              tone: (overview.refused === 0 ? 'success' : 'critical') as StatTone,
            },
          ] as Array<{
            label: string;
            value: string;
            detail: string;
            icon: LucideIcon;
            tone: StatTone;
          }>
        ).map((kpi) => (
          <StatCard
            key={kpi.label}
            label={kpi.label}
            value={kpi.value}
            sub={kpi.detail}
            icon={kpi.icon}
            tone={kpi.tone}
          />
        ))}
      </div>

      <TabBar tabs={ACCESS_TABS} value={tab} onChange={setTab} ariaLabel="Access control" className="self-start" />

      {tab === 'Members' && (
        <>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel
              title="Members by role"
              description="Who holds what. The role decides which locations, models and settings a seat can reach."
            >
              {/* Horizontal: five role names ("Administrator", "Data Clerk") do
                  not fit across a phone's upright x-axis, where two of them were
                  measured overlapping at 320px. */}
              <CountChart
                data={overview.byRole}
                layout="bars"
                noun="members"
                categoryLabel="Role"
                emptyTitle="Nobody to group"
                emptyDescription="This workspace has no members yet, so there is no mix to show."
              />
            </Panel>

            <Panel title="Invitations" description="Whether each invitation has been accepted.">
              <MixDonut
                centreLabel="members"
                slices={[
                  {
                    key: 'verified',
                    name: 'Verified',
                    value: overview.invites.verified,
                    fill: '#0f6d67',
                    dot: 'bg-brand-600',
                  },
                  {
                    key: 'invited',
                    name: 'Awaiting invite',
                    value: overview.invites.invited,
                    fill: '#d97706',
                    dot: 'bg-amber-500',
                  },
                ]}
                emptyTitle="No invitations yet"
                emptyDescription="Invite a member and their state will show here."
              />
            </Panel>
          </div>

          <Panel
            title="Workspace members"
            description="Access is scoped by role."
            action={
              <button
                type="button"
                onClick={() => setInviteOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-2.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-1.5"
              >
                <UserPlusIcon className="h-3.5 w-3.5" strokeWidth={2} />
                Invite member
              </button>
            }
          >
            {members.length === 0 ? (
              <EmptyState
                variant="empty"
                size="md"
                title="No members yet"
                description="Invite your first team member."
                hints={['Members are workspace-scoped', 'Roles control what a seat can reach']}
                action={{ label: 'Invite member', onClick: () => setInviteOpen(true) }}
              />
            ) : (
              <RecordList<any>
                stateKey="admin-members"
                caption="Workspace members"
                minWidth="min-w-[440px]"
                items={members}
                rowKey={(member: any) => String(member.id)}
                countNoun="members"
                searchFields={(member: any) => [member.name, member.email].filter(Boolean).join(' ')}
                searchPlaceholder="Search members…"
                filters={[
                  {
                    key: 'role',
                    label: 'Role',
                    match: (m: any, v: string) => String(m.role ?? 'viewer') === v,
                    options: [
                      { value: '', label: 'Any role' },
                      ...(roles.length ? roles : FALLBACK_ROLES).map((r: any) => ({
                        value: String(r.role),
                        label: r.label ?? roleLabel(r.role),
                      })),
                    ],
                  },
                  {
                    key: 'invite',
                    label: 'Invitation',
                    match: (m: any, v: string) => (v === 'accepted' ? !!m.email_verified : !m.email_verified),
                    options: [
                      { value: '', label: 'Any invitation state' },
                      { value: 'accepted', label: 'Accepted' },
                      { value: 'pending', label: 'Awaiting invite' },
                    ],
                  },
                ]}
                columns={[
                  {
                    key: 'member',
                    label: 'Member',
                    primary: true,
                    sortable: true,
                    render: (member: any) => (
                      <div className="flex min-w-0 max-w-[164px] items-center gap-2.5 lg:max-w-none">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-700 text-fine font-semibold text-white">
                          {(member.name ?? member.email ?? '?')
                            .split(' ')
                            .map((p: string) => p[0])
                            .join('')
                            .slice(0, 2)
                            .toUpperCase()}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-meta font-semibold text-slate-900">
                            {member.name ?? member.email}
                          </span>
                          <span className="block truncate text-fine text-slate-500">{member.email}</span>
                        </span>
                      </div>
                    ),
                  },
                  { key: 'role', label: 'Role', sortable: true, render: (member: any) => roleLabel(String(member.role ?? 'viewer')) },
                  // No per-member location scoping exists in the API, so every
                  // member does have all locations — the column says so rather
                  // than leaving a blank. "Last active" and "Status" were removed:
                  // the roster carries neither, so they rendered "—" and a
                  // permanent "active" pill that stated a state the API does not
                  // track.
                  {
                    key: 'locations',
                    label: 'Locations',
                    headerClassName: 'hide-tablet',
                    cellClassName: 'hide-tablet',
                    render: (member: any) => member.locations ?? member.location_scope ?? 'All locations',
                  },
                  {
                    key: 'joined',
                    label: 'Joined',
                    sortable: true,
                    headerClassName: 'hide-tablet',
                    cellClassName: 'hide-tablet',
                    render: (member: any) =>
                      member.created_at ? new Date(member.created_at).toLocaleDateString() : '—',
                  },
                  {
                    key: 'access',
                    label: 'Invitation',
                    render: (member: any) => (
                      <StatusPill tone={member.email_verified ? 'success' : 'warning'} dot>
                        {member.email_verified ? 'verified' : 'invited'}
                      </StatusPill>
                    ),
                  },
                  {
                    key: 'actions',
                    label: '',
                    cellClassName: 'pr-0',
                    render: (member: any) => {
                      const locked = lockedReason(member);
                      const current = String(member.role ?? 'viewer');
                      // Keep the member's own role in the list so it always
                      // renders, even when that role is above the actor and
                      // cannot be granted.
                      const options = assignableRoles.some((r: any) => r.role === current)
                        ? assignableRoles
                        : [{ role: current, label: roleLabel(current) }, ...assignableRoles];
                      return (
                        <>
                          <div className="flex flex-wrap items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => openEdit(member)}
                              aria-label={`Edit ${member.name ?? member.email}`}
                              title="Edit member"
                              className="tap-expand rounded-lg border border-slate-300 p-1.5 text-slate-500 transition-colors duration-150 ease-out hover:bg-slate-50 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                            >
                              <PencilIcon className="h-3 w-3" strokeWidth={2} />
                            </button>
                            <select
                              aria-label={`Role for ${member.name ?? member.email}`}
                              value={current}
                              disabled={!!locked || busyId === member.id}
                              title={locked}
                              onChange={(e) => void updateMember(member.id, { role: e.target.value })}
                              className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-micro font-medium text-slate-700 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-50 disabled:text-slate-500"
                            >
                              {options.map((r: any) => (
                                <option key={r.role} value={r.role}>
                                  {r.label ?? roleLabel(r.role)}
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              onClick={() => setRevoking(member)}
                              disabled={!!locked}
                              title={locked ?? 'Revoke access'}
                              className="rounded-lg px-2.5 py-1.5 text-fine font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent"
                            >
                              Revoke
                            </button>
                          </div>
                          {locked && <p className="mt-1 text-right text-fine text-slate-500">{locked}</p>}
                        </>
                      );
                    },
                  },
                ]}
              />
            )}
          </Panel>
        </>
      )}

      {tab === 'Roles' && (
        <>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel
              title="Capabilities per role"
              description="How many of the platform's capabilities each role holds."
            >
              <CountChart
                data={overview.capabilities}
                layout="bars"
                noun="capabilities"
                categoryLabel="Role"
                emptyTitle="No roles to count"
                emptyDescription="The capability registry did not load, so there are no totals to show."
              />
              {/*
                The one thing this chart says that the matrix below cannot: rank
                order and capability count disagree on purpose. An owner is above
                an administrator in the hierarchy and holds fewer capabilities.
              */}
              {overview.richest && overview.senior && overview.richest.label !== overview.senior.label ? (
                <p className="mt-3 border-t border-slate-100 pt-3 text-micro leading-relaxed text-slate-500">
                  {overview.richest.label} holds the most capabilities ({overview.richest.count} of{' '}
                  {overview.capabilityTotal}), more than the {overview.senior.label} ({overview.senior.count}). Rank and
                  capability count are different ladders: ownership carries the decisions that are not capabilities.
                </p>
              ) : null}
            </Panel>

            <Panel title="Capabilities by area" description="What the platform can do, grouped the way the API publishes it.">
              <CountChart
                data={groups.map((group: any) => ({
                  label: group.group ?? 'Other',
                  count: (group.capabilities ?? []).length,
                }))}
                layout="bars"
                noun="capabilities"
                categoryLabel="Capability area"
                emptyTitle="No capability groups"
                emptyDescription="The capability registry did not load, so there is nothing to group."
              />
              {capabilityNote ? (
                <p className="mt-3 border-t border-slate-100 pt-3 text-micro leading-relaxed text-slate-500">
                  {capabilityNote}
                </p>
              ) : null}
            </Panel>
          </div>

          <Panel
            title="Role permissions"
            description="Rendered from the capability bundles the API enforces, showing what each role can do across web, mobile and API clients."
          >
            {roles.length === 0 ? (
              <EmptyState variant="empty" size="sm" title="No roles" description="No roles configured." />
            ) : (
              <div className="max-h-[520px] overflow-y-auto overscroll-contain">
                <table className="responsive-cards w-full min-w-[440px] text-left">
                  <thead>
                    <tr className="border-b border-slate-200">
                      <th
                        scope="col"
                        className="sticky top-0 z-10 bg-white py-2 pr-3 text-micro font-semibold text-slate-600"
                      >
                        Capability
                      </th>
                      {roles.map((r: any) => (
                        <th
                          key={r.role}
                          scope="col"
                          className="sticky top-0 z-10 w-[56px] max-w-[56px] break-words bg-white py-2 align-bottom text-micro font-semibold text-slate-600 lg:w-[76px] lg:max-w-[76px]"
                        >
                          {r.label ?? r.role}
                          <span className="block text-fine font-normal text-slate-500">
                            {sharePercent((r.permissions ?? []).length, overview.capabilityTotal)}%
                          </span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(groups.length ? groups : [{ group: '', capabilities: [] }]).flatMap((group: any) => {
                      /* A permission matrix has to keep its grid on a wide screen,
                         so it stays a raw table — but it carries the card-mode data
                         attributes, so on a phone each capability becomes a card
                         listing which roles allow it instead of a grid you have to
                         drag sideways. */
                      const rows = (group.capabilities ?? []).map((cap: string) => (
                        <tr key={cap} className="border-b border-slate-100">
                          <td data-primary="" className="py-2 pr-3 font-mono text-micro text-slate-700">
                            {cap}
                          </td>
                          {roles.map((r: any) => (
                            <td key={r.role} data-label={String(r.label ?? r.role)} className="py-2">
                              {(r.permissions ?? []).includes(cap) ? (
                                <CheckIcon className="h-4 w-4 text-brand-600" strokeWidth={2.5} aria-label="Allowed" />
                              ) : (
                                <MinusIcon className="h-4 w-4 text-slate-300" strokeWidth={2.5} aria-label="Not allowed" />
                              )}
                            </td>
                          ))}
                        </tr>
                      ));
                      if (!group.group || rows.length === 0) return rows;
                      return [
                        <tr key={`h-${group.group}`} className="bg-slate-50">
                          <td
                            colSpan={roles.length + 1}
                            className="py-1.5 pr-3 text-fine font-semibold uppercase tracking-wide text-slate-500"
                          >
                            {group.group} · {(group.capabilities ?? []).length}
                          </td>
                        </tr>,
                        ...rows,
                      ];
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}

      {tab === 'Activity' && (
        <>
          {/* The filters are the API's own (`actor_id`, `client`): each one
              re-reads the trail server-side, so what is on screen is what the
              server answered rather than a slice of one page narrowed here. */}
          <Panel
            title="Audit log"
            icon={ShieldCheckIcon}
            description="Every privileged action, with the client it came from."
          >
            <div className="mb-3 flex flex-wrap items-end gap-2 border-b border-slate-100 pb-3">
              <label className="flex flex-col gap-1">
                <span className="text-micro font-semibold text-slate-600">Who</span>
                <select
                  aria-label="Filter by who acted"
                  value={actorFilter}
                  onChange={(e) => changeFilters({ actor: e.target.value })}
                  className={inputClass}
                >
                  <option value="all">Anyone</option>
                  {members.map((member) => (
                    <option key={member.id} value={String(member.id)}>
                      {member.name ?? member.email}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-micro font-semibold text-slate-600">From</span>
                <select
                  aria-label="Filter by surface"
                  value={surfaceFilter}
                  onChange={(e) => changeFilters({ surface: e.target.value })}
                  className={inputClass}
                >
                  <option value="all">Anywhere</option>
                  {AUDIT_SURFACES.map((surface) => (
                    <option key={surface.value} value={surface.value}>
                      {surface.label}
                    </option>
                  ))}
                </select>
              </label>

              {(actorFilter !== 'all' || surfaceFilter !== 'all') && (
                <button
                  type="button"
                  onClick={() => changeFilters({ actor: 'all', surface: 'all' })}
                  className="rounded-lg border border-slate-300 px-3 py-2.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-2"
                >
                  Clear filters
                </button>
              )}

              <span className="ml-auto pb-1 text-micro text-slate-500">
                {audit.length} shown{auditMore ? ' · more available' : ''}
              </span>
            </div>

            {auditLoading ? (
              <LoadingState label="Loading the audit trail" size="sm" />
            ) : auditError ? (
              /* The trail is its own read: a filter the server refuses, or a
                 dropped connection, must not blank the roster above it. */
              <EmptyState
                variant={auditError.variant}
                size="sm"
                title={auditError.title}
                description={auditError.desc}
                action={{ label: 'Retry', onClick: () => void fetchAudit(null) }}
              />
            ) : audit.length === 0 ? (
              <EmptyState
                variant={actorFilter !== 'all' || surfaceFilter !== 'all' ? 'search' : 'empty'}
                size="md"
                title={
                  actorFilter !== 'all' || surfaceFilter !== 'all'
                    ? 'Nothing matches these filters'
                    : 'No audit events'
                }
                description={
                  actorFilter !== 'all' || surfaceFilter !== 'all'
                    ? 'No privileged action in this workspace matches. Clear a filter to widen the search.'
                    : 'Audit log will show privileged actions once they occur.'
                }
                hints={
                  actorFilter !== 'all' || surfaceFilter !== 'all'
                    ? undefined
                    : ['Actions are audited with actor and client', 'Filter by who acted or where from']
                }
                action={
                  actorFilter !== 'all' || surfaceFilter !== 'all'
                    ? { label: 'Clear filters', onClick: () => changeFilters({ actor: 'all', surface: 'all' }) }
                    : undefined
                }
              />
            ) : (
              <>
                <ol className="relative border-l border-slate-200 pl-4">
                  {lines.map((line) => {
                    // The raw `client` value, not its words: `channelIcon` is
                    // keyed by `web`/`mobile`/`api`, so looking up the label
                    // here would fall back to the desktop icon every time.
                    const Icon = channelIcon[line.clientKey ?? ''] ?? MonitorIcon;
                    return (
                      <li key={line.key} className="relative pb-3.5 last:pb-0">
                        <span className="absolute -left-[25px] flex h-4 w-4 items-center justify-center rounded-full bg-white">
                          <span
                            className={[
                              'h-2 w-2 rounded-full',
                              line.rejected ? 'bg-amber-500' : 'bg-brand-600',
                            ].join(' ')}
                          />
                        </span>
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <p className="text-meta font-semibold text-slate-900">{line.headline}</p>
                          {line.detail ? <span className="text-caption text-slate-600">{line.detail}</span> : null}
                        </div>
                        <p className="mt-0.5 flex items-center gap-1.5 text-fine text-slate-500">
                          <Icon className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
                          {[line.client ?? 'the web app', line.actor, line.at].filter(Boolean).join(' · ')}
                        </p>
                      </li>
                    );
                  })}
                </ol>

                {/* Real cursor paging, on the list that can take one. The trail
                    is the only paged read on this page; the roster is returned
                    whole and the console no longer offers it a pager. */}
                {(auditMore || auditHistory.length > 0) && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                    <button
                      type="button"
                      disabled={auditHistory.length === 0}
                      onClick={() => {
                        const previous = [...auditHistory];
                        const cursor = previous.pop() ?? null;
                        setAuditHistory(previous);
                        void fetchAudit(cursor);
                      }}
                      className="rounded-lg border border-slate-300 px-3 py-2.5 text-caption font-semibold text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:py-2"
                    >
                      Newer
                    </button>
                    <button
                      type="button"
                      disabled={!auditMore}
                      onClick={() => {
                        if (!auditCursor) return;
                        setAuditHistory((prev) => [...prev, auditCursor]);
                        void fetchAudit(auditCursor);
                      }}
                      className="rounded-lg bg-brand-700 px-3 py-2.5 text-caption font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 disabled:cursor-not-allowed disabled:bg-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:py-2"
                    >
                      Older
                    </button>
                    <span className="text-micro text-slate-500">
                      Newest first, {audit.length} per page.
                    </span>
                  </div>
                )}
              </>
            )}
          </Panel>

          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel
              title="Where actions came from"
              description={`The surface behind ${windowLabel}. A write from a till is filed as the API, not as the web app.`}
            >
              <MixDonut
                centreLabel="actions"
                slices={[
                  {
                    key: 'web',
                    name: auditClientLabel('web') ?? 'the web app',
                    value: overview.surfaces.web,
                    fill: '#0f6d67',
                    dot: 'bg-brand-600',
                  },
                  {
                    key: 'mobile',
                    name: auditClientLabel('mobile') ?? 'a phone',
                    value: overview.surfaces.mobile,
                    fill: '#0284c7',
                    dot: 'bg-sky-500',
                  },
                  {
                    key: 'api',
                    name: auditClientLabel('api') ?? 'the API',
                    value: overview.surfaces.api,
                    fill: '#d97706',
                    dot: 'bg-amber-500',
                  },
                ]}
                emptyTitle="Nothing recorded yet"
                emptyDescription="No privileged action has been audited in this workspace."
              />
            </Panel>

            <Panel
              title="What was done"
              description={`The actions taken most often in ${windowLabel}.`}
            >
              {/* Action labels are the longest on the console ("Assistant session
                  started", "Member role or name changed"). The default 110px band
                  clipped them, and a band set a hair wider than the rendered text
                  still clipped by a few px: recharts decides whether to wrap from
                  an *estimate*, so `Location details updated` (128px rendered)
                  stayed on one line in a 132px band and poked out of the svg.
                  150px leaves slack on both sides of that estimate. */}
              <CountChart
                data={overview.actions}
                layout="bars"
                labelWidth={150}
                noun="actions"
                categoryLabel="Action"
                emptyTitle="Nothing recorded yet"
                emptyDescription="No privileged action has been audited in this workspace."
              />
            </Panel>
          </div>
        </>
      )}

      {/*
        Inviting is a form, so it is a `FormModal`: a real `<form>` (Enter
        submits from any field), the shared Cancel/Save order and a busy state.
        The location-scope select that used to sit here is gone: `POST
        /members/invite` accepts email, role and name only, so the choice was
        silently discarded — a control that changes nothing but the reader's
        belief about what they granted.
      */}
      <FormModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Invite member"
        description="They receive an email invite and set their own password."
        onSubmit={() => void sendInvite()}
        saving={saving}
        submitLabel="Send invite"
      >
        <div className="flex flex-col gap-3">
          <Field id="inv-name" label="Full name">
            <input
              id="inv-name"
              type="text"
              value={invite.name}
              onChange={(e) => setInvite({ ...invite, name: e.target.value })}
              placeholder="Grace Uwase"
              className={inputClass}
            />
          </Field>
          <Field id="inv-email" label="Work email">
            <input
              id="inv-email"
              type="email"
              value={invite.email}
              onChange={(e) => setInvite({ ...invite, email: e.target.value })}
              placeholder="grace@restaurant.rw"
              className={inputClass}
            />
          </Field>
          <Field
            id="inv-role"
            label="Role"
            hint="Roles decide what they can see and change. You can grant any role below your own; ownership is not invitable."
          >
            <select
              id="inv-role"
              value={invite.role}
              onChange={(e) => setInvite({ ...invite, role: e.target.value })}
              className={inputClass}
            >
              {(assignableRoles.length ? assignableRoles : FALLBACK_ROLES.filter((r: any) => r.role !== 'owner')).map(
                (r: any) => (
                  <option key={r.role} value={r.role}>
                    {r.label ?? roleLabel(r.role)}
                  </option>
                ),
              )}
            </select>
          </Field>
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-fine leading-relaxed text-slate-600">
            Every member sees every location: this workspace has no per-location scoping, so the role is the whole of
            their access. Narrow it later by moving them to a lower role.
          </p>
        </div>
      </FormModal>

      {/* Edit member: name and role in one dialog, with the same hierarchy the
          row's quick picker enforces; a role at or above the actor's own is
          shown locked with its reason instead of failing on save. */}
      <FormModal
        open={!!editMember}
        onClose={() => setEditMember(null)}
        title={`Edit ${editMember?.name ?? editMember?.email ?? 'member'}`}
        description="Name and role apply the next time they load the app."
        onSubmit={() => void saveEdit()}
        saving={busyId === editMember?.id}
        submitLabel="Save changes"
      >
        <Field id="edit-name" label="Full name">
          <input
            id="edit-name"
            type="text"
            value={editDraft.name}
            onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
            placeholder="Grace Uwase"
            className={inputClass}
          />
        </Field>
        <Field id="edit-email" label="Work email" hint="Email is the sign-in identity and cannot be changed here.">
          <input id="edit-email" type="email" value={editMember?.email ?? ''} disabled className={inputClass} />
        </Field>
        <Field id="edit-role" label="Role" hint={editLocked ?? 'You can grant any role below your own; ownership is not assignable.'}>
          <select
            id="edit-role"
            value={editDraft.role}
            disabled={!!editLocked}
            onChange={(e) => setEditDraft({ ...editDraft, role: e.target.value })}
            className={inputClass}
          >
            {(assignableRoles.some((r: any) => r.role === editDraft.role)
              ? assignableRoles
              : [{ role: editDraft.role, label: roleLabel(editDraft.role) }, ...assignableRoles]
            ).map((r: any) => (
              <option key={r.role} value={r.role}>
                {r.label ?? roleLabel(r.role)}
              </option>
            ))}
          </select>
        </Field>
      </FormModal>

      <ConfirmDialog
        open={!!revoking}
        onClose={() => setRevoking(null)}
        onConfirm={() => void revokeAccess()}
        title="Revoke this person's access?"
        body={`${revoking?.name ?? revoking?.email ?? 'This member'} loses access to this workspace immediately. Their past actions stay in the audit log, and the email address can be invited again later.`}
        confirmLabel="Revoke access"
        tone="danger"
      />
    </div>
  );
}
