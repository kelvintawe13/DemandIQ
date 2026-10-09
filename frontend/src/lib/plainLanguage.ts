/**
 * Plain-language vocabulary.
 *
 * The API speaks in machine tokens (`awaiting_mapping`, `feature_materialisation`,
 * `completeness`, `PSI`), and an operator reading the UI for the first time does
 * not. These helpers are the one place that maps a raw token to the words a
 * person would actually use, shared by every screen so two pages cannot describe
 * the same status two different ways.
 */

/**
 * Turn a raw pipeline/upload/source status into a plain phrase.
 *
 * Unknown tokens are not hidden: they are title-cased with the underscores
 * turned into spaces, so a new backend status still reads as words rather than
 * a crash or an em dash.
 */
export function plainStatus(status?: string | null): string {
  if (!status) return '—';
  const token = String(status).toLowerCase().trim();
  const known: Record<string, string> = {
    // ingest uploads
    committed: 'Imported',
    ready: 'Ready to import',
    awaiting_mapping: 'Needs column mapping',
    awaiting: 'Waiting',
    // pipeline runs
    queued: 'Waiting to start',
    pending: 'Waiting to start',
    running: 'Running now',
    syncing: 'Syncing now',
    success: 'Succeeded',
    completed: 'Completed',
    failed: 'Failed',
    error: 'Failed',
    // connectors / sources
    connected: 'Connected',
    inactive: 'Not connected',
    healthy: 'Healthy',
    degraded: 'Needs attention',
    failing: 'Failing',
    available: 'Available',
    active: 'Active',
    paused: 'Paused',
    unknown: 'Unknown',
  };
  if (known[token]) return known[token];
  return token.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/**
 * Human label and one-line meaning for each data-quality dimension.
 *
 * `timeliness` and `freshness` are the same idea under two API names; the UI
 * calls both "Freshness" so the summary card and the monitor agree.
 */
const QUALITY_DIMENSIONS: Record<string, { label: string; hint: string }> = {
  completeness: {
    label: 'Completeness',
    hint: 'Share of expected days that have demand recorded',
  },
  timeliness: {
    label: 'Freshness',
    hint: 'How recently new data arrived',
  },
  freshness: {
    label: 'Freshness',
    hint: 'How recently new data arrived',
  },
  consistency: {
    label: 'Consistency',
    hint: 'Whether the same day is counted twice or two sources disagree',
  },
  accuracy: {
    label: 'Accuracy',
    hint: 'Suspicious values, gaps or impossible counts',
  },
};

/** Plain label for a quality dimension key, falling back to the raw key. */
export function qualityDimensionLabel(key: string): string {
  return QUALITY_DIMENSIONS[key]?.label ?? plainStatus(key);
}

/** Plain hint for a quality dimension key. */
export function qualityDimensionHint(key: string, fallback?: string): string {
  return QUALITY_DIMENSIONS[key]?.hint ?? fallback ?? `${qualityDimensionLabel(key)} (data quality check)`;
}

/**
 * A model version as an operator should read it. The value is a UUID; we show a
 * short, stable fragment prefixed with "version", never the bare hex.
 */
export function modelLabel(version?: string | null, length = 8): string {
  if (!version) return '—';
  return `version ${String(version).slice(0, length)}`;
}

/**
 * A readable label for an audit-log `action`.
 *
 * The audit middleware records the literal request line (`action=f"{method} {path}"`),
 * e.g. `POST /api/v1/agent/messages` (backend/app/middleware/audit.py). That is
 * correct for an audit trail and unreadable on a dashboard, so the Overview's
 * activity feed runs the value through here. Known routes get the operator's
 * wording; anything else has its area described in words ("Agent messages"),
 * the request line itself is never shown to an operator.
 */
/**
 * Audited routes that carry an id, so they cannot be matched by exact key.
 *
 * Only routes a person's own feed actually shows: their sessions, their
 * two-factor, the alerts they triaged and the reports they scheduled.
 */
const ID_ROUTE_LABELS: Array<[RegExp, string]> = [
  [/^DELETE \/api\/v1\/me\/sessions\/[^/]+$/, 'Another session signed out'],
  [/^DELETE \/api\/v1\/me\/identities\/[^/]+$/, 'Sign-in method disconnected'],
  [/^POST \/api\/v1\/alerts\/[^/]+\/read$/, 'Alert marked read'],
  [/^POST \/api\/v1\/alerts\/[^/]+\/resolve$/, 'Alert resolved'],
  [/^PATCH \/api\/v1\/alerts\/rules\/[^/]+$/, 'Alert rule updated'],
  [/^DELETE \/api\/v1\/alerts\/rules\/[^/]+$/, 'Alert rule deleted'],
  // A quality rule is switched on or off, never edited in place, so the label
  // says so: the fallback read this as "Quality rules", which is the area the
  // request touched rather than the change the person made.
  [/^PATCH \/api\/v1\/quality\/rules\/[^/]+$/, 'Quality rule switched'],
  [/^POST \/api\/v1\/quality\/issues\/[^/]+\/apply-fix$/, 'Quality fix applied'],
  [/^POST \/api\/v1\/quality\/issues\/[^/]+\/dismiss$/, 'Quality issue dismissed'],
  // Adding a site and editing one are different acts; without these both read
  // as "Locations", which is the one distinction a trail most needs to keep.
  [/^PATCH \/api\/v1\/locations\/[^/]+$/, 'Location details updated'],
  [/^DELETE \/api\/v1\/locations\/[^/]+$/, 'Location archived'],
  // The access console's own writes. Inviting, re-roling and revoking a seat
  // all fall back to the bare word "Members" otherwise.
  [/^PATCH \/api\/v1\/members\/[^/]+$/, 'Member role or name changed'],
  [/^DELETE \/api\/v1\/members\/[^/]+$/, 'Member access revoked'],
  [/^PATCH \/api\/v1\/reports\/schedules\/[^/]+$/, 'Report schedule updated'],
  [/^DELETE \/api\/v1\/reports\/schedules\/[^/]+$/, 'Report schedule removed'],
  [/^POST \/api\/v1\/sources\/[^/]+\/sync$/, 'Source sync started'],
];

export function auditActionLabel(action?: string | null): string {
  if (!action) return 'Activity';
  const raw = String(action).trim();

  const known: Record<string, string> = {
    // Self-service routes. These are what a personal feed is mostly made of, and
    // the path-derived fallback read them as "Me preferences" / "Me avatar",
    // the endpoint's own area, not what the person did.
    'PATCH /api/v1/me/preferences': 'Preferences updated',
    'PATCH /api/v1/me/notifications': 'Notification settings updated',
    'PATCH /api/v1/me/profile': 'Profile updated',
    'POST /api/v1/me/profile/complete': 'Profile completed',
    'POST /api/v1/me/avatar': 'Profile photo changed',
    'POST /api/v1/me/password': 'Password changed',
    'DELETE /api/v1/me/mfa': 'Two-factor turned off',
    'POST /api/v1/me/mfa/enroll': 'Two-factor setup started',
    'POST /api/v1/me/mfa/confirm': 'Two-factor turned on',
    'POST /api/v1/me/mfa/recovery-codes': 'Recovery codes regenerated',
    'POST /api/v1/auth/login': 'Signed in',
    'POST /api/v1/auth/logout': 'Signed out',
    'POST /api/v1/auth/password/forgot': 'Password reset requested',
    'POST /api/v1/auth/password/reset': 'Password reset',
    'POST /api/v1/reports/generate': 'Report generated',
    'POST /api/v1/reports/schedules': 'Report schedule created',
    'POST /api/v1/alerts/rules': 'Alert rule created',
    'POST /api/v1/alerts/read-all': 'All alerts marked read',
    'POST /api/v1/agent/sessions': 'Assistant session started',
    'DELETE /api/v1/agent/sessions': 'Assistant session ended',
    'POST /api/v1/agent/messages': 'Question asked in the assistant',
    'POST /api/v1/agent/messages/pin': 'Assistant answer saved',
    'POST /api/v1/policies': 'Preparation policy saved',
    'PATCH /api/v1/policies': 'Preparation policy updated',
    'POST /api/v1/policies/simulate': 'Policy comparison run',
    'POST /api/v1/forecasts': 'Forecast generated',
    'POST /api/v1/forecasts/batch': 'Forecasts generated',
    'POST /api/v1/ingest/manual': 'Demand entered by hand',
    'POST /api/v1/ingest/file': 'Demand file uploaded',
    'POST /api/v1/sources': 'Source added',
    'POST /api/v1/connectors': 'Connector added',
    'POST /api/v1/locations': 'Location added',
    'POST /api/v1/members/invite': 'Member invited',
    'PATCH /api/v1/workspace': 'Workspace settings updated',
  };
  if (known[raw]) return known[raw];

  // Routes that carry an identifier cannot be matched by exact key, and the
  // area fallback below would read them as "Me sessions" or "Issues apply fix".
  for (const [pattern, label] of ID_ROUTE_LABELS) {
    if (pattern.test(raw)) return label;
  }

  /*
   * The routers write their own audit rows beside the middleware's, and they
   * name the action as a token (`member.invite`, `workspace.update`) while the
   * middleware writes the request line for the very same request. Left to the
   * title-casing fallback those read as noun phrases — "Member invite" next to
   * the middleware's "Member invited: <id>" — i.e. one act named two ways on
   * one screen. The pairs below are the ones sharing a trail; the rest of the
   * token families (site admin, mirroring sessions) still fall through to
   * title case and read as their area.
   */
  const tokenPairs: Record<string, string> = {
    'member.invite': 'Member invited',
    'member.update': 'Member role or name changed',
    'member.revoke': 'Member access revoked',
    'workspace.update': 'Workspace settings updated',
  };
  if (tokenPairs[raw]) return tokenPairs[raw];

  // Any other bare token (a verb like "view", or a namespaced action like
  // "change_request.approved") is turned into words. The dots are namespace
  // separators, not part of a word, so they become spaces.
  if (!raw.includes('/')) return plainStatus(raw.replace(/\./g, '_'));

  // `METHOD /path` -> describe the area the request touched.
  const segments = raw
    .split(/\s+/)
    .pop()!
    .split('/')
    .filter(Boolean);
  const skip = new Set(['api', 'v1', 'v2', 'v3', 'admin']);
  const words = segments
    .filter((s) => !skip.has(s.toLowerCase()))
    // A UUID or long hex is an identifier, not a word.
    .filter((s) => !/^[0-9a-f-]{8,}$/i.test(s))
    .map((s) => s.replace(/[-_]/g, ' '));
  if (words.length === 0) return 'Activity';
  // Keep the most specific two segments: `/agent/messages` -> "agent messages".
  return plainStatus(words.slice(-2).join(' '));
}

/**
 * The audit row's `target`, when it is worth showing.
 *
 * The middleware sets `target=path` for privileged writes, so an unfiltered
 * slice of it renders as `/api/v1/`, a fragment of an endpoint, which tells an
 * operator nothing. Only a real identifier is returned; a request path is not.
 */
export function auditTargetLabel(target?: string | null): string | null {
  if (!target) return null;
  const value = String(target).trim();
  if (!value || value.startsWith('/')) return null;
  return value;
}

/**
 * One audit row: the action in words, plus the target only when it is a real
 * identifier rather than the request path the middleware also stores.
 *
 * Shared so the screens that list the audit log (Access Control, Site Admin and
 * the assistant's governance tab) render the same row the same way, and none of
 * them can print a raw request line on its own.
 */
/** Where an audited action came from, in operator words. */
export function auditClientLabel(client?: string | null): string | null {
  if (!client) return null;
  const key = String(client).trim().toLowerCase();
  const known: Record<string, string> = {
    web: 'the web app',
    mobile: 'a phone',
    api: 'the API',
    system: 'the scheduler',
  };
  return known[key] ?? `the ${key}`;
}

/** The status the audit middleware records alongside each privileged request. */
export interface AuditStatusMeta {
  status_code?: number | null;
}

/**
 * Whether an audited request was accepted, from the code the middleware stores:
 * ``accepted``, or ``rejected (403)``. ``null`` for a row written before the code
 * was recorded; an unknown outcome is left unsaid rather than guessed.
 *
 * Workspace rows carry it as ``meta_data`` (``GET /audit-log``); the site-admin
 * list flattens the same field to ``meta``.
 */
export function auditOutcomeLabel(
  row?: { meta_data?: AuditStatusMeta | null; meta?: AuditStatusMeta | null } | null,
): string | null {
  const status = row?.meta_data?.status_code ?? row?.meta?.status_code;
  if (typeof status !== 'number') return null;
  return status < 400 ? 'accepted' : `rejected (${status})`;
}

/**
 * The second line of an audit row: what changed, and whether it was accepted.
 *
 * The middleware stores the request path as ``target`` (which says nothing, see
 * ``auditTargetLabel``) and puts the status code and the surface in
 * ``meta_data``/``client``. So a row whose target is a real identifier keeps it,
 * and every other row says what happened instead of repeating the endpoint:
 * "accepted from the web app", or "rejected (403) from the API". A pipeline row
 * (the same feed's other source) has no ``meta_data`` and carries its own summary
 * in ``detail``; that summary is what is shown for it.
 */
export function auditDetailLabel(
  row?: {
    target?: string | null;
    detail?: string | null;
    description?: string | null;
    resource?: string | null;
    pipeline?: string | null;
    client?: string | null;
    meta_data?: AuditStatusMeta | null;
    meta?: AuditStatusMeta | null;
  } | null,
): string {
  const explicit =
    auditTargetLabel(row?.target ?? null) ??
    auditTargetLabel(row?.detail ?? row?.description ?? row?.resource ?? row?.pipeline ?? null);
  if (explicit) return explicit;
  const outcome = auditOutcomeLabel(row);
  const client = auditClientLabel(row?.client ?? null);
  return [outcome, client ? `from ${client}` : null].filter(Boolean).join(' ');
}

export function auditLine(action?: string | null, target?: string | null): string {
  const label = auditActionLabel(action);
  const shown = auditTargetLabel(target);
  return shown ? `${label}: ${shown}` : label;
}
