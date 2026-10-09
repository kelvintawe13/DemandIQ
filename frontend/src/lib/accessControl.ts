/**
 * `GET /members`, `/roles` and `/audit-log` → the counts the access console
 * renders.
 *
 * The shaping lives here rather than in the page for the same reason
 * `lib/deviceFleet.ts` exists: a KPI tile, a chart and a table that each derive
 * their own version of "how many administrators are there" will eventually
 * disagree, and on a permissions screen a disagreement is the thing a reader
 * trusts least.
 *
 * Three facts about the API shape everything below:
 *
 * 1. **`GET /members` is not paginated.** It takes no cursor and returns the
 *    whole workspace roster in one `data` array with no `has_more`. The console
 *    previously drove its Previous/Next buttons from the *audit log's* cursor, so
 *    the members list had a pager that paged nothing. Nothing here returns a
 *    cursor for a list that cannot take one.
 * 2. **`GET /members/invite` stores no location scope** — email, role and name
 *    only — so the console must not ask for one.
 * 3. **`GET /audit-log` is the opposite**: `actor_id`, `action` and `client` are
 *    real server-side filters and the cursor is real. Its rows carry the raw
 *    request line in `action`, which is why every label below goes through
 *    `plainLanguage` rather than printing the path.
 */

import {
  auditActionLabel,
  auditClientLabel,
  auditLine,
  auditOutcomeLabel,
} from './plainLanguage';

/** A member row, as `GET /members` sends it. */
export interface MemberRow {
  id?: string;
  name?: string | null;
  email?: string | null;
  role?: string | null;
  email_verified?: boolean | null;
  created_at?: string | null;
}

/** A role row, as `GET /roles` sends it. */
export interface RoleRow {
  role?: string;
  label?: string;
  rank?: number;
  assignable?: boolean;
  permissions?: string[] | null;
}

/** One audit row, as `GET /audit-log` sends it. */
export interface AuditEntry {
  id?: number | string;
  action?: string | null;
  target?: string | null;
  client?: string | null;
  actor_id?: string | null;
  actor_email?: string | null;
  meta_data?: { status_code?: number | null } | null;
  detail?: string | null;
  created_at?: string | null;
}

/** A plain `{ label, count }` pair, the shape both chart primitives take. */
export interface Tally {
  label: string;
  count: number;
}

/** The face of a role, using the label the product shows rather than the enum. */
function roleLabelOf(role: RoleRow): string {
  const label = typeof role.label === 'string' && role.label.trim() ? role.label : '';
  if (label) return label;
  const value = String(role.role ?? '').toLowerCase();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Viewer';
}

function rankOf(role: RoleRow): number {
  return typeof role.rank === 'number' ? role.rank : -1;
}

/**
 * How the roster divides by role, most senior first.
 *
 * Roles the workspace has nobody in are left out: an empty bar is a chart
 * artifact, and the role ladder itself is the Roles tab's job. The order is the
 * published rank, so the chart reads the same way the hierarchy does.
 */
export function memberRoleCounts(members: MemberRow[], roles: RoleRow[]): Tally[] {
  const known = new Map(roles.map((role) => [String(role.role ?? '').toLowerCase(), role]));
  const counts = new Map<string, { label: string; count: number; rank: number }>();
  for (const member of members) {
    const key = String(member.role ?? 'viewer').toLowerCase();
    const role = known.get(key);
    const label = role ? roleLabelOf(role) : key.charAt(0).toUpperCase() + key.slice(1);
    const existing = counts.get(key);
    if (existing) existing.count += 1;
    else counts.set(key, { label, count: 1, rank: rankOf(role ?? {}) });
  }
  return [...counts.values()]
    .sort((a, b) => b.rank - a.rank || a.label.localeCompare(b.label))
    .map(({ label, count }) => ({ label, count }));
}

/**
 * Whether a member has accepted their invitation.
 *
 * `email_verified` is the only state the roster carries; it is not a "status" —
 * there is no active/disabled flag on the endpoint, and the console used to
 * invent one.
 */
export function inviteMix(members: MemberRow[]): { verified: number; invited: number } {
  const verified = members.filter((member) => member.email_verified === true).length;
  return { verified, invited: members.length - verified };
}

/** The role keys somebody in this workspace actually holds. */
export function rolesInUse(members: MemberRow[]): string[] {
  return [...new Set(members.map((member) => String(member.role ?? 'viewer').toLowerCase()))].sort();
}

/**
 * How many capabilities each role holds.
 *
 * This is the interesting chart on this page, because the answer is not the
 * hierarchy: an administrator holds *more* capabilities than an owner (ownership
 * is deliberately kept out of the platform capability set), and an analyst holds
 * more than a data clerk. Drawing the counts beside the rank is what makes that
 * visible instead of surprising — the console shows the matrix of every
 * capability, but never said how the totals came out.
 */
export function capabilityCounts(roles: RoleRow[]): Tally[] {
  return roles
    .map((role) => ({
      label: roleLabelOf(role),
      count: Array.isArray(role.permissions) ? role.permissions.length : 0,
      rank: rankOf(role),
    }))
    .sort((a, b) => b.rank - a.rank || b.count - a.count)
    .map(({ label, count }) => ({ label, count }));
}

/** Every capability in the registry, deduplicated across roles. */
export function allCapabilities(roles: RoleRow[]): string[] {
  const out = new Set<string>();
  for (const role of roles) {
    for (const capability of role.permissions ?? []) out.add(capability);
  }
  return [...out];
}

/** A published capability group, as `GET /roles` sends it alongside the roles. */
export interface CapabilityGroup {
  group?: string | null;
  capabilities?: string[] | null;
}

/** Every capability the published groups list. */
export function groupedCapabilities(groups: CapabilityGroup[]): string[] {
  const out = new Set<string>();
  for (const group of groups) {
    for (const capability of group.capabilities ?? []) out.add(capability);
  }
  return [...out];
}

/**
 * Where the Roles tab's two lists disagree.
 *
 * That tab draws two charts from two different API sources: the counts per role
 * come from each role's own `permissions`, and the area bars come from the
 * published capability *groups*. They are not the same population. Measured
 * against the live registry: the roles hold 42 distinct capabilities, the groups
 * list 40, three of them (`agent:configure`, `approvals:manage`,
 * `quality:configure`) appear under no area, and one grouped capability
 * (`device:sync`) is held by no role — it is a device-scoped grant, and the
 * device that holds it is not a role.
 *
 * So the two panels would state different totals for the same word with nothing
 * to explain the difference, which is the one thing this file exists to prevent.
 * The page names the gap rather than picking a total and hoping nobody adds the
 * bars up.
 */
export interface CapabilityGaps {
  /** Held by at least one role, listed under no area. */
  heldButUngrouped: string[];
  /** Listed under an area, held by no role. */
  groupedButUnheld: string[];
}

export function capabilityGaps(roles: RoleRow[], groups: CapabilityGroup[]): CapabilityGaps {
  const held = allCapabilities(roles);
  const grouped = groupedCapabilities(groups);
  const groupedSet = new Set(grouped);
  const heldSet = new Set(held);
  return {
    heldButUngrouped: held.filter((capability) => !groupedSet.has(capability)),
    groupedButUnheld: grouped.filter((capability) => !heldSet.has(capability)),
  };
}

/** Privileged requests the middleware recorded as refused (any 4xx/5xx). */
export function rejectedCount(entries: AuditEntry[]): number {
  return entries.filter((entry) => {
    const status = entry.meta_data?.status_code;
    return typeof status === 'number' && status >= 400;
  }).length;
}

/** Where the recorded actions came from: `web`, `mobile`, `api`, or nothing yet. */
export interface SurfaceMix {
  web: number;
  mobile: number;
  api: number;
}

export function auditSurfaceMix(entries: AuditEntry[]): SurfaceMix {
  const mix: SurfaceMix = { web: 0, mobile: 0, api: 0 };
  for (const entry of entries) {
    const key = String(entry.client ?? 'web').trim().toLowerCase();
    if (key === 'mobile' || key === 'api') mix[key] += 1;
    else mix.web += 1;
  }
  return mix;
}

/**
 * What was done, most frequent first, in words.
 *
 * `action` is the literal request line (`POST /api/v1/policies/simulate`), so the
 * grouping is by its human label and never by the path — the same rule the
 * timeline rows follow. The tail beyond `top` is summed into "Other" rather than
 * dropped, so the bars still add up to the window they describe.
 */
export function auditActionCounts(entries: AuditEntry[], top = 6): Tally[] {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    const label = auditActionLabel(entry.action);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const sorted = [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  if (sorted.length <= top) return sorted;
  const head = sorted.slice(0, top);
  const tail = sorted.slice(top).reduce((sum, item) => sum + item.count, 0);
  return [...head, { label: `Other (${sorted.length - top})`, count: tail }];
}

/** One audit row, ready for the timeline, with every token already in words. */
export interface AuditLineRow {
  key: string;
  /** What was done, in words — never the request line the API stores. */
  headline: string;
  /** The raw `client` value, for the icon; `client` is its words. */
  clientKey: string | null;
  /**
   * What happened to it — the stored outcome, plus the row's own summary when
   * the middleware wrote one. The surface is *not* folded in here: it has its
   * own line below, and naming it twice in one row reads as two sources.
   */
  detail: string | null;
  client: string | null;
  actor: string;
  at: string;
  rejected: boolean;
}

/**
 * The timeline's rows.
 *
 * Shaped here beside the charts for one reason: the bars count entries by
 * `auditActionLabel`, and if the list built its own labels the two could name the
 * same row differently on the same screen. Every string goes through
 * `plainLanguage`, so a raw `/api/v1/...` cannot reach the screen from this file
 * either.
 */
export function auditLines(entries: AuditEntry[]): AuditLineRow[] {
  return entries.map((entry, index) => {
    const status = entry.meta_data?.status_code;
    return {
      key: String(entry.id ?? `${entry.action ?? 'event'}-${index}`),
      headline: auditLine(entry.action, entry.target),
      detail: [auditOutcomeLabel(entry), entry.detail].filter(Boolean).join(' · ') || null,
      clientKey: entry.client ? String(entry.client).trim().toLowerCase() : null,
      client: auditClientLabel(entry.client),
      actor: entry.actor_email?.trim() || '',
      at: entry.created_at ? new Date(entry.created_at).toLocaleString() : '',
      rejected: typeof status === 'number' && status >= 400,
    };
  });
}

/** The surfaces the audit middleware can record, for the filter control. */
export const AUDIT_SURFACES: Array<{ value: string; label: string }> = [
  { value: 'web', label: auditClientLabel('web') ?? 'the web app' },
  { value: 'mobile', label: auditClientLabel('mobile') ?? 'a phone' },
  { value: 'api', label: auditClientLabel('api') ?? 'the API' },
];

/** The three columns of a coverage bar, as one percentage. */
export function sharePercent(part: number, total: number): number {
  if (!total) return 0;
  return Math.round((part / total) * 100);
}
