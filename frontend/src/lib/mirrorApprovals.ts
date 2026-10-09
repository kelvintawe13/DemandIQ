/**
 * `/api/v1/device-mirror/**` → the rows the approval queue renders.
 *
 * Kept out of the page for the same reason `lib/deviceFleet.ts` is: the queue,
 * the live-session list and the consent panel all read one mirror session, and
 * a mapping written three times is three chances to disagree about whether a
 * session is live.
 *
 * The backend publishes three fields this module refuses to re-derive:
 *
 * * **`live`**: computed from `status` *and* `ends_at`, so an `active` row
 *   whose window has closed is not live. A console that trusted `status` alone
 *   would keep showing "watching" on a screen nobody can watch.
 * * **`expired`**: the window is closed, or was never set.
 * * **`approvable`**: whether *this viewer* may decide *this* request: they hold
 *   `device_mirror:approve` and it is not their own ask. The UI gates its
 *   buttons on it rather than working the rule out for itself, because the
 *   two-person rule is the consent gate and a button that always 403s is how a
 *   refusal that should never have been reachable becomes a support ticket.
 */

export type MirrorStatus = 'pending' | 'active' | 'ended' | 'denied';

export interface MirrorRequest {
  id: string;
  deviceId: string;
  /** Resolved from the fleet list, because a session row carries only an id. */
  deviceLabel: string;
  status: MirrorStatus;
  requestedBy: string;
  /** The requester's name when the seat may read the roster, else a safe stand-in. */
  requestedByLabel: string;
  decidedBy: string | null;
  decidedByLabel: string | null;
  /** The operator's own words, recorded on the session and read by the approver. */
  reason: string;
  /** The raw timestamp, for anything that needs to reason about the moment. */
  requestedAtIso: string | null;
  decidedAt: string | null;
  /** The hard expiry. The only thing that stops a view. */
  endsAt: string | null;
  endedAt: string | null;
  consentAt: string | null;
  live: boolean;
  expired: boolean;
  approvable: boolean;
  /** The viewer raised it, so no button is offered; the API says so too. */
  mine: boolean;
  /** "14 min ago": how long this has been sitting in the queue. */
  waiting: string;
}

export interface MirrorCondition {
  name: string;
  ok: boolean;
  detail: string;
}

export interface MirrorEligibility {
  deviceId: string;
  eligible: boolean;
  /** The server's condition code (`no_consent`, `already_active`, …) */
  reason: string;
  /** The server's operator-facing sentence for that code. Shown verbatim. */
  message: string;
  conditions: MirrorCondition[];
  consentAt: string | null;
  activeSessionId: string | null;
  canRequest: boolean;
  canApprove: boolean;
  maxSessionMinutes: number;
}

/** What recording or withdrawing consent did. */
export interface ConsentOutcome {
  deviceId: string;
  granted: boolean;
  consentAt: string | null;
  /** Live and queued sessions the withdrawal stopped, as the server counted. */
  sessionsEnded: number;
}

function asText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

function mirrorStatus(value: unknown): MirrorStatus {
  const token = asText(value)?.toLowerCase();
  if (token === 'active' || token === 'ended' || token === 'denied') return token;
  return 'pending';
}

/** Minute-resolution "how long ago", for the queue's "waiting" column. */
export function waitingLabel(iso: unknown, now: number = Date.now()): string {
  const text = asText(iso);
  if (!text) return 'Unknown';
  const at = new Date(text).getTime();
  if (Number.isNaN(at)) return 'Unknown';
  const mins = Math.max(0, Math.round((now - at) / 60_000));
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} ${hrs === 1 ? 'hour' : 'hours'} ago`;
  return `${Math.round(hrs / 24)} days ago`;
}

/**
 * The countdown against a session's hard expiry.
 *
 * Minute resolution while there is more than an hour left, seconds below it:
 * a session can be capped at thirty minutes, so the interesting part of the
 * countdown is the last few minutes, and "0 min left" for the final minute of a
 * live view of somebody's screen is exactly the wrong thing to show.
 */
export function countdownLabel(endsAt: unknown, now: number = Date.now()): string {
  const text = asText(endsAt);
  if (!text) return 'No end time set';
  const at = new Date(text).getTime();
  if (Number.isNaN(at)) return 'No end time set';
  const msLeft = at - now;
  if (msLeft <= 0) return 'Ended';
  const totalSeconds = Math.floor(msLeft / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m left`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, '0')}s left`;
  return `${seconds}s left`;
}

function rowsFrom(payload: unknown): Record<string, unknown>[] {
  const body = payload as { data?: unknown } | null;
  const list = Array.isArray(payload) ? payload : body?.data;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
}

export interface MapRequestsOptions {
  /** Device id → label, from the fleet list. */
  labels?: Record<string, string>;
  /**
   * User id → name, from `GET /api/v1/members`. That route needs
   * `members:manage`, which an administrator deliberately does not hold, so the
   * roster is best-effort: without it the queue says "another workspace member"
   * rather than printing a UUID, and never fails to load over it.
   */
  people?: Record<string, string>;
  /** The signed-in user's id, used only as a fallback for `cannot_approve`. */
  userId?: string | null;
  now?: number;
}

function personLabel(
  id: string | null,
  people: Record<string, string>,
  userId: string | null,
  fallback: string,
): string {
  if (!id) return fallback;
  // "You" beats your own name: the person reading the queue knows who they
  // are, and a row that says their own name in a third person is one more thing
  // to check against the two-person rule.
  if (userId !== null && id === userId) return 'You';
  const name = people[id];
  if (name) return name;
  return fallback;
}

/**
 * `GET /mirror-requests/pending` and `GET /devices/{id}/mirror-requests`.
 *
 * The queue's response also carries `cannot_approve` (the ids the server
 * recognised as the caller's own) and that list is preferred over comparing
 * ids here, so "this is yours" is the server's answer rather than a guess about
 * which id field meant the actor.
 */
export function mapMirrorRequests(
  payload: unknown,
  { labels = {}, people = {}, userId = null, now = Date.now() }: MapRequestsOptions = {},
): MirrorRequest[] {
  const body = payload as { cannot_approve?: unknown } | null;
  const own = new Set(
    Array.isArray(body?.cannot_approve)
      ? body.cannot_approve.filter((id): id is string => typeof id === 'string')
      : [],
  );

  return rowsFrom(payload).map((row) => {
    const id = asText(row.id) ?? '';
    const deviceId = asText(row.device_id) ?? '';
    const requestedBy = asText(row.requested_by) ?? '';
    const decidedBy = asText(row.decided_by);
    const endsAt = asText(row.ends_at);
    return {
      id,
      deviceId,
      deviceLabel: labels[deviceId] ?? 'A device',
      status: mirrorStatus(row.status),
      requestedBy,
      requestedByLabel: personLabel(
        requestedBy || null,
        people,
        userId,
        'Another workspace member',
      ),
      decidedBy,
      decidedByLabel: decidedBy
        ? personLabel(decidedBy, people, userId, 'Another workspace member')
        : null,
      reason: asText(row.reason) ?? 'No reason was given.',
      requestedAtIso: asText(row.requested_at),
      decidedAt: asText(row.decided_at),
      endsAt,
      endedAt: asText(row.ended_at),
      consentAt: asText(row.consent_at),
      live: row.live === true,
      expired: row.expired === true,
      // The server's computed answer, never a local re-derivation of the
      // two-person rule.
      approvable: row.approvable === true,
      mine: own.has(id) || (own.size === 0 && userId !== null && requestedBy === userId),
      waiting: waitingLabel(row.requested_at, now),
    };
  });
}

/** The live views, for the list that counts down to each hard expiry. */
export function liveSessions(requests: MirrorRequest[]): MirrorRequest[] {
  return requests.filter((request) => request.live);
}

export function mapEligibility(payload: unknown): MirrorEligibility {
  const body = payload as { data?: Record<string, unknown> } | null;
  const row = body?.data ?? {};
  const conditions = Array.isArray(row.conditions) ? row.conditions : [];
  return {
    deviceId: asText(row.device_id) ?? '',
    eligible: row.eligible === true,
    reason: asText(row.reason) ?? 'eligible',
    message: asText(row.message) ?? '',
    conditions: conditions
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map((item) => ({
        name: conditionLabel(asText(item.condition) ?? ''),
        ok: item.ok === true,
        detail: asText(item.detail) ?? '',
      })),
    consentAt: asText(row.consent_at),
    activeSessionId: asText(row.active_session_id),
    canRequest: row.can_request === true,
    canApprove: row.can_approve === true,
    maxSessionMinutes: typeof row.max_session_minutes === 'number' ? row.max_session_minutes : 30,
  };
}

/** A gate's name in words. The order is the product decision: consent first. */
export function conditionLabel(name: string): string {
  const known: Record<string, string> = {
    customer_consent: 'The customer agreed to this',
    device_in_service: 'The device is in service',
    no_live_view: 'Nobody else is watching that screen',
  };
  if (known[name]) return known[name];
  const words = name.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function mapConsentOutcome(payload: unknown): ConsentOutcome {
  const body = payload as { data?: Record<string, unknown> } | null;
  const row = body?.data ?? {};
  return {
    deviceId: asText(row.device_id) ?? '',
    granted: row.granted === true,
    consentAt: asText(row.consent_at),
    sessionsEnded: typeof row.sessions_ended === 'number' ? row.sessions_ended : 0,
  };
}

/**
 * The mirror session's status in the words the queue should use.
 *
 * `pending` is not "waiting" on the server but "a request for a second person
 * to agree", so that is what it says here.
 */
export function mirrorStatusLabel(status: MirrorStatus, live: boolean): string {
  if (status === 'active') return live ? 'Live now' : 'Window closed';
  if (status === 'denied') return 'Refused';
  if (status === 'ended') return 'Ended';
  return 'Needs a second person';
}

/**
 * A refusal code in one operator sentence.
 *
 * `MirrorError.code` reaches the client as the error code, so a 409 can be
 * explained rather than reported as "Request failed": "the customer withdrew
 * consent" and "a view is already live" are different conversations with a
 * different restaurant, and the queue is where the operator has to choose one.
 */
/** The sentence shown when the code is not one the console recognises. */
const UNKNOWN_REFUSAL = 'This request cannot be completed';

export function refusalTitle(code: string): string {
  const known: Record<string, string> = {
    two_person_rule: 'A second person has to agree. You cannot decide your own request.',
    not_permitted: 'Only the requester or a member holding device_mirror:approve can end this view',
    not_pending: 'This request has already been decided',
    request_expired: 'This request is too old to approve',
    no_consent: 'The customer has not agreed to screen mirroring',
    consent_withdrawn: 'The customer withdrew consent for this device',
    consent_changed: 'The customer’s consent changed after this request was raised',
    already_active: 'Someone is already watching that screen',
    device_revoked: 'This device has been revoked',
    device_quarantined: 'This device is quarantined and is not in service',
    device_inactive: 'This device is not in service',
    device_missing: 'This device no longer exists',
    reason_required: 'A reason is required before a screen can be watched',
    reason_too_long: 'That reason is too long',
    invalid_duration: 'That viewing window is not valid',
    wrong_workspace: 'That device is not in this workspace',
  };
  if (known[code]) return known[code];
  return UNKNOWN_REFUSAL;
}

/**
 * A refusal in one line: the console's own sentence for a gate it knows, and
 * the server's message for anything else, so a code this release has not seen
 * is reported rather than flattened into "Request failed".
 */
export function refusalMessage(code: string, serverMessage: string): string {
  const title = refusalTitle(code);
  return title === UNKNOWN_REFUSAL ? serverMessage : title;
}
