/**
 * `GET /api/v1/devices` and `/api/v1/device-grants/**` → the rows the fleet
 * console renders.
 *
 * Response mapping lives here rather than in the page, following
 * `lib/sessions.ts`: the fleet page and the mirroring page both read a device's
 * label, status and consent, and a mapping written twice is a mapping that
 * disagrees, which is how one screen says a till is "Active" and the other
 * says "Revoked".
 *
 * Two backend facts shape everything here:
 *
 * 1. **A device credential is published exactly once**, in the response that
 *    mints it. Only the bcrypt hash and a non-secret prefix are stored, so there
 *    is no second read that could return it. `mapSecret` therefore only ever
 *    runs on the response to a write; the page is not permitted to go looking
 *    for it, and `readableAgain: false` is the flag it renders.
 * 2. **The effective-scopes report is the authority on what a till may do**,
 *    and it is already filtered to the device: its `withheld` list is exactly
 *    the set of registered capabilities the device does *not* hold. The grant
 *    form offers that list rather than a second copy of the capability registry
 *    kept in the frontend, so a capability the backend adds is grantable the
 *    day it exists, and one it removes cannot be sent at all.
 */

/** `devices.status`: the only three values the router accepts. */
export type DeviceStatus = 'active' | 'quarantined' | 'revoked';

/** One enrolled device, as the fleet table and the mirroring page need it. */
export interface FleetDevice {
  id: string;
  label: string;
  /** `device_type`: `pos`, `kds`, whatever the operator named it. */
  type: string;
  platform: string | null;
  status: DeviceStatus;
  locationId: string | null;
  /** Relative "last seen", or "Never seen" for a device that has not checked in. */
  lastSeen: string;
  /**
   * The same reading, unrounded: epoch milliseconds, or `null` when the device
   * has never checked in.
   *
   * `lastSeen` is a sentence and cannot be bucketed: "1 day ago" and "3 days
   * ago" are both "not today", but "40 min ago" is not. Anything that counts
   * devices by when they last reported needs the timestamp, so it is parsed once
   * here — by the same rule `relativeTime` uses, so a device cannot read "Never
   * seen" and still land in a bucket.
   */
  lastSeenAt: number | null;
  appVersion: string;
  /** The non-secret half of the credential, so two rotations can be told apart. */
  credentialPrefix: string;
  baseScopes: string[];
  enrolledAt: string | null;
  enrolledBy: string | null;
  revokedAt: string | null;
  /** When the customer agreed to screen mirroring, or `null` for no consent. */
  consentAt: string | null;
  /** False for a revoked device: rotation is refused there, so no button. */
  rotatable: boolean;
}

export interface DeviceSecret {
  /** The one and only time this string is available. */
  secret: string;
  prefix: string;
  deviceLabel: string;
  /** The backend's own warning, shown next to our own. */
  warning: string;
}

/** One `device_grants` row with its liveness already resolved by the server. */
export interface DeviceGrant {
  id: string;
  capability: string;
  grantedBy: string;
  grantedAt: string | null;
  expiresAt: string | null;
  revokedAt: string | null;
  revokedBy: string | null;
  reason: string | null;
  /** `active` / `expired` / `revoked`, computed against the server's clock. */
  state: 'active' | 'expired' | 'revoked';
  effective: boolean;
  /** The server's one-sentence reason, quoted rather than reworded. */
  detail: string;
}

/** A capability the device holds because of a grant, with the grant's author. */
export interface GrantedCapability {
  capability: string;
  grantId: string;
  grantedBy: string;
  grantedAt: string | null;
  expiresAt: string | null;
  reason: string | null;
}

/** A capability the device does not hold, and the nearest reason it is missing. */
export interface WithheldCapability {
  capability: string;
  detail: string;
}

/** `GET /devices/{id}/effective-scopes`: "what may this till do, and why not X". */
export interface EffectiveScopes {
  baseScopes: string[];
  granted: GrantedCapability[];
  effective: string[];
  withheld: WithheldCapability[];
}

export interface DeviceEventRow {
  id: string;
  eventType: string;
  /** Short human summary of the telemetry blob, never the raw JSON. */
  summary: string;
  recordedAt: string;
}

const STATUSES: readonly DeviceStatus[] = ['active', 'quarantined', 'revoked'];

function asText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

function asTextArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function deviceStatus(value: unknown): DeviceStatus {
  const token = asText(value)?.toLowerCase() ?? 'active';
  return (STATUSES as readonly string[]).includes(token) ? (token as DeviceStatus) : 'active';
}

/**
 * A timestamp in epoch ms, or `null` when there is no usable one.
 *
 * The single parse rule: an absent, empty or unparseable value all mean "we do
 * not know when", which is what the label prints as "Never seen" and what the
 * buckets keep out of every bucket.
 */
export function epochOf(value: unknown): number | null {
  const text = asText(value);
  if (!text) return null;
  const at = new Date(text).getTime();
  return Number.isNaN(at) ? null : at;
}

/**
 * A relative time, at minute resolution.
 *
 * The device heartbeat is liveness, not an event log: "roughly when" is the
 * question an operator asks, and a seconds figure would imply a precision the
 * heartbeat does not have.
 */
export function relativeTime(iso: unknown, now: number = Date.now()): string {
  const at = epochOf(iso);
  if (at === null) return 'Never seen';
  const mins = Math.round((now - at) / 60_000);
  if (mins < 1) return 'Active now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} ${hrs === 1 ? 'hour' : 'hours'} ago`;
  const days = Math.round(hrs / 24);
  return `${days} ${days === 1 ? 'day' : 'days'} ago`;
}

/** An absolute date, for the detail panel where "how long ago" is not enough. */
export function absoluteTime(iso: unknown): string {
  const at = epochOf(iso);
  return at === null ? '—' : new Date(at).toLocaleString();
}

/** A device's `status` in words a restaurant owner would use. */
export function deviceStatusLabel(status: DeviceStatus): string {
  if (status === 'revoked') return 'Revoked';
  if (status === 'quarantined') return 'Quarantined';
  return 'In service';
}

/** A device type, defaulted the way the router defaults it. */
export function deviceTypeLabel(value: unknown): string {
  const text = asText(value)?.toLowerCase();
  if (!text) return 'Till';
  const known: Record<string, string> = {
    pos: 'Till',
    till: 'Till',
    kds: 'Kitchen display',
    tablet: 'Tablet',
    display: 'Customer display',
    handheld: 'Handheld',
  };
  return known[text] ?? text.charAt(0).toUpperCase() + text.slice(1);
}

/** A grant's `state` as a sentence, so the table never shows a bare token. */
export function grantStateLabel(state: DeviceGrant['state']): string {
  if (state === 'revoked') return 'Taken back';
  if (state === 'expired') return 'Ran out';
  return 'In effect';
}

function rowsFrom(payload: unknown): Record<string, unknown>[] {
  const body = payload as { data?: unknown } | null;
  const list = Array.isArray(payload) ? payload : body?.data;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
}

/** The devices the console lists, from whatever the client handed back. */
export function mapDevices(payload: unknown, now: number = Date.now()): FleetDevice[] {
  return rowsFrom(payload).map((row) => {
    const status = deviceStatus(row.status);
    return {
      id: asText(row.id) ?? '',
      label: asText(row.label) ?? 'Unlabelled device',
      type: deviceTypeLabel(row.device_type),
      platform: asText(row.platform),
      status,
      locationId: asText(row.location_id),
      lastSeen: relativeTime(row.last_seen_at, now),
      lastSeenAt: epochOf(row.last_seen_at),
      appVersion: asText(row.app_version) ?? 'Unknown version',
      credentialPrefix: asText(row.credential_prefix) ?? '—',
      baseScopes: asTextArray(row.scopes),
      enrolledAt: asText(row.enrolled_at),
      enrolledBy: asText(row.enrolled_by),
      revokedAt: asText(row.revoked_at),
      consentAt: asText(row.mirroring_consent_at),
      // A revoked device is re-enrolled, not revived: the router refuses
      // rotation with 409, so the console does not offer a button that will fail.
      rotatable: status !== 'revoked',
    };
  });
}

/** Device id → label, for screens that receive sessions rather than devices. */
export function deviceLabels(devices: FleetDevice[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const device of devices) {
    if (device.id) out[device.id] = device.label;
  }
  return out;
}

/**
 * The one-time credential, read off a write response.
 *
 * `readableAgain: false` is carried deliberately: the page renders it as the
 * warning it is, and nothing in the console offers to fetch it a second time
 * because there is no code path on the server that would answer.
 */
export function mapSecret(payload: unknown): DeviceSecret | null {
  const body = payload as { data?: Record<string, unknown> } | null;
  const row = body?.data;
  const secret = asText(row?.secret);
  if (!row || !secret) return null;
  return {
    secret,
    prefix: asText(row.credential_prefix) ?? '—',
    deviceLabel: asText(row.label) ?? 'This device',
    warning: asText(row.warning) ?? 'Store this secret securely. It will not be shown again.',
  };
}

export function mapGrants(payload: unknown): DeviceGrant[] {
  return rowsFrom(payload).map((row) => {
    const state = asText(row.state)?.toLowerCase();
    return {
      id: asText(row.id) ?? '',
      capability: asText(row.capability) ?? 'unknown capability',
      grantedBy: asText(row.granted_by) ?? '—',
      grantedAt: asText(row.granted_at),
      expiresAt: asText(row.expires_at),
      revokedAt: asText(row.revoked_at),
      // A taken-back grant stays on file, so the trail has to say who took it
      // back and not only when; otherwise the record cannot answer "who
      // removed this permission, and when".
      revokedBy: asText(row.revoked_by),
      reason: asText(row.reason),
      state: state === 'revoked' || state === 'expired' ? state : 'active',
      // The server resolves liveness; `effective` is its answer and is never
      // re-derived here, because a second implementation of the expiry boundary
      // is a second answer to "is this permission on right now".
      effective: row.effective === true,
      detail: asText(row.detail) ?? '',
    };
  });
}

export function mapEffectiveScopes(payload: unknown): EffectiveScopes {
  const body = payload as { data?: Record<string, unknown> } | null;
  const row = body?.data ?? {};
  return {
    baseScopes: asTextArray(row.base_scopes),
    granted: (Array.isArray(row.granted) ? row.granted : [])
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map((item) => ({
        capability: asText(item.capability) ?? 'unknown capability',
        grantId: asText(item.grant_id) ?? '',
        grantedBy: asText(item.granted_by) ?? '—',
        grantedAt: asText(item.granted_at),
        expiresAt: asText(item.expires_at),
        reason: asText(item.reason),
      })),
    effective: asTextArray(row.effective),
    withheld: (Array.isArray(row.withheld) ? row.withheld : [])
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map((item) => ({
        capability: asText(item.capability) ?? 'unknown capability',
        detail: asText(item.detail) ?? '',
      })),
  };
}

/**
 * The capabilities the grant form may offer this device.
 *
 * Derived from the server's own `withheld` list rather than from a copy of the
 * registry: it is the set the router would accept, so the form cannot offer a
 * capability that comes back 400, and it cannot hide one the backend can grant.
 */
export function grantableCapabilities(report: EffectiveScopes): WithheldCapability[] {
  return report.withheld;
}

/** A heartbeat's telemetry, as one line. The raw blob never reaches a screen. */
function eventSummary(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null) return 'No details recorded';
  const entries = Object.entries(payload as Record<string, unknown>).filter(
    ([, value]) => value !== null && value !== '',
  );
  if (entries.length === 0) return 'No details recorded';
  return entries
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${String(value)}`)
    .join(' · ');
}

export function mapDeviceEvents(payload: unknown, now: number = Date.now()): DeviceEventRow[] {
  return rowsFrom(payload).map((row) => ({
    id: asText(row.id) ?? `${asText(row.event_type) ?? 'event'}-${asText(row.recorded_at) ?? ''}`,
    eventType: asText(row.event_type) ?? 'event',
    summary: eventSummary(row.payload),
    recordedAt: relativeTime(row.recorded_at, now),
  }));
}

/** The name of a telemetry key, turned into words for an event line. */
export function eventTypeLabel(eventType: string): string {
  if (eventType === 'heartbeat') return 'Checked in';
  const text = eventType.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// --------------------------------------------------------------------------- //
// Fleet overview: what the console counts, and which devices want a person
// --------------------------------------------------------------------------- //

/**
 * How long a device may stay quiet before somebody should look.
 *
 * The heartbeat is the device saying "I am still here", and it runs far more
 * often than daily; a full day of silence is not a network blip. The number is
 * stated on the page rather than implied, because "needs attention" is a claim
 * about somebody's hardware mid-service.
 */
export const STALE_AFTER_HOURS = 24;

/** Why a device is on the attention list. */
export type AttentionReason = 'never-seen' | 'quiet' | 'quarantined';

export interface AttentionItem {
  device: FleetDevice;
  reason: AttentionReason;
  /** One sentence a person can act on, in the operator's words. */
  detail: string;
}

/**
 * The three states that want a person, as sentences.
 *
 * `revoked` is deliberately **not** one of them. A revoked till is a decision
 * that has already been taken — the console says "a lost till is revoked and
 * stays on file" — so listing it as something to fix would be asking the
 * operator to re-open a closed question. It still counts in the status mix.
 *
 * A quarantined device is listed whatever its heartbeat says: it is paused on
 * purpose, and the reason to look at it is to decide whether to restore it.
 */
export function attentionFor(device: FleetDevice, now: number = Date.now()): AttentionItem | null {
  if (device.status === 'quarantined') {
    return {
      device,
      reason: 'quarantined',
      detail: 'Paused. It is not reporting until somebody restores it.',
    };
  }
  if (device.status === 'revoked') return null;
  if (device.lastSeenAt === null) {
    return {
      device,
      reason: 'never-seen',
      detail: 'Enrolled but has never checked in — the key may not have been typed in yet.',
    };
  }
  const quietHours = (now - device.lastSeenAt) / 3_600_000;
  if (quietHours >= STALE_AFTER_HOURS) {
    return {
      device,
      reason: 'quiet',
      detail: `Has not checked in for ${device.lastSeen}.`,
    };
  }
  return null;
}

/** Every device that wants a person, in the order the reasons matter. */
export function fleetAttention(devices: FleetDevice[], now: number = Date.now()): AttentionItem[] {
  const order: Record<AttentionReason, number> = { quarantined: 0, 'never-seen': 1, quiet: 2 };
  return devices
    .map((device) => attentionFor(device, now))
    .filter((item): item is AttentionItem => item !== null)
    .sort((a, b) => order[a.reason] - order[b.reason] || a.device.label.localeCompare(b.device.label));
}

/** How the fleet divides by status, in the order the donut draws. */
export interface StatusMix {
  active: number;
  quarantined: number;
  revoked: number;
}

export function statusMixOf(devices: FleetDevice[]): StatusMix {
  return {
    active: devices.filter((device) => device.status === 'active').length,
    quarantined: devices.filter((device) => device.status === 'quarantined').length,
    revoked: devices.filter((device) => device.status === 'revoked').length,
  };
}

/**
 * When the fleet last checked in, as buckets.
 *
 * "Never checked in" is its own bucket and never folded into "over a week":
 * a device that has not reported since enrolment is a different problem from
 * one that went quiet, and they are fixed differently (type the key in, versus
 * go and look at the till). Thresholds are the heartbeat's own
 * (`STALE_AFTER_HOURS` for the first bucket that matters), and the buckets are
 * exhaustive and disjoint, so the bars always total the fleet.
 */
export interface FleetBucket {
  /** Short enough for a chart axis; the panel's description says of what. */
  label: string;
  count: number;
  /** True when a device in this bucket is one the attention list is about. */
  attention?: boolean;
}

export function lastSeenBuckets(
  devices: FleetDevice[],
  now: number = Date.now(),
): FleetBucket[] {
  const hour = 3_600_000;
  const day = 24 * hour;
  const buckets: Array<FleetBucket & { within: (age: number) => boolean }> = [
    { label: '< 1 hour', count: 0, within: (age) => age < hour },
    { label: '1–24 hours', count: 0, within: (age) => age < day },
    { label: '1–7 days', count: 0, attention: true, within: (age) => age < 7 * day },
    { label: '> 7 days', count: 0, attention: true, within: () => true },
  ];
  const never: FleetBucket = { label: 'Never', count: 0, attention: true };

  for (const device of devices) {
    if (device.lastSeenAt === null) {
      never.count += 1;
      continue;
    }
    const age = now - device.lastSeenAt;
    const bucket = buckets.find((candidate) => candidate.within(age)) ?? buckets[buckets.length - 1];
    bucket.count += 1;
  }

  // "Never checked in" leads: it is the state with the least to go on. The rest
  // run freshest first, which is how the fleet is read.
  return [never, ...buckets.map(({ label, count, attention }) => ({ label, count, attention }))];
}

/**
 * App versions across the fleet, newest-looking first.
 *
 * A version the API did not send is its own bucket, not a blank: "unknown" is
 * a real row on a fleet console, because a device reporting no version is a
 * device whose build nobody can confirm.
 */
export function appVersionCounts(devices: FleetDevice[]): Array<{ label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const device of devices) {
    const label = device.appVersion || 'Unknown version';
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

/**
 * Devices per restaurant, including the unbound ones.
 *
 * "Not bound to a restaurant" is a bucket rather than a dropped row: a till
 * that reports into no site cannot feed any site's forecast, which is exactly
 * the kind of thing this panel exists to make visible.
 */
export function devicesByLocation(
  devices: FleetDevice[],
  nameFor: (locationId: string | null) => string,
): Array<{ label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const device of devices) {
    const label = nameFor(device.locationId);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}
