/**
 * `GET /api/v1/me/sessions` → the rows the device screens render.
 *
 * The endpoint sends `device`, `origin`, `trusted_device`, `last_seen_at`
 * and `current`; the last of these is true on the single session the request's
 * own token belongs to, read from the token's `sid` claim
 * (``backend/app/routers/me.py``). Three screens list those rows (Profile's
 * panel, the Settings Security tab and the Devices screen) and each rebuilt
 * this mapping by hand. They disagreed: one fell back to `created_at` (which
 * the payload never carried, so every row read as an empty placeholder), others dropped the origin
 * entirely, and one fabricated a random id for a row whose id was missing,
 * which made its Revoke button DELETE an id the API has never heard of.
 *
 * Note on the origin field: the login records the client's network origin as it
 * stood at sign-in, and in this deployment that is an address (`127.0.0.1`), not
 * an ISO code. The API used to call the column `ip_country`, which made every
 * screen label an address a country; it was renamed to `origin` in migration
 * 013, so the screens say "Signed in from <value>", true of an address and of a
 * country code alike. `ip_country` is still read as a fallback so a client
 * talking to an un-migrated API keeps rendering something instead of blank.
 */

/** One active session, as the two device screens need it. */
export interface DeviceSession {
  /** The session row's id, or `''` when the API did not name one. */
  id: string;
  device: string;
  /**
   * Where the sign-in came from: the address (or country code, where the
   * deployment resolves one) the login recorded, or `null` when it recorded
   * nothing.
   */
  origin: string | null;
  /** The login's "remember this device" choice. */
  trusted: boolean;
  /** True for the session this page was loaded with. */
  current: boolean;
  /** "Active now" for this device, else a relative "last seen". */
  time: string;
  /** Whether the user agent looks like a phone, for the row's icon. */
  mobile: boolean;
  /**
   * Whether the row can be revoked. Never the current session; signing
   * yourself out from a session-management screen is not what the button is
   * for, and never a row the API did not identify.
   */
  revocable: boolean;
}

interface SessionRow {
  id?: unknown;
  device?: unknown;
  user_agent?: unknown;
  origin?: unknown;
  /** Pre-013 name for `origin`; read only as a fallback. */
  ip_country?: unknown;
  trusted_device?: unknown;
  last_seen_at?: unknown;
  created_at?: unknown;
  last_active?: unknown;
  current?: unknown;
}

function asText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/**
 * A relative "last seen", from a timestamp the API reported.
 *
 * Minute resolution: this answers "roughly when", and a seconds figure would
 * imply a precision the session sweep does not have.
 */
export function lastSeenLabel(iso: unknown, now: number = Date.now()): string {
  const text = asText(iso);
  if (!text) return '';
  const at = new Date(text).getTime();
  if (Number.isNaN(at)) return '';
  const mins = Math.round((now - at) / 60_000);
  if (mins < 1) return 'Active now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hours ago`;
  return `${Math.round(hrs / 24)} days ago`;
}

/** Phone-ish user agents get a phone icon; everything else is a desktop. */
export function isMobileDevice(device: string): boolean {
  const ua = device.toLowerCase();
  return ua.includes('mobile') || ua.includes('android') || ua.includes('iphone');
}

/** The rows out of whatever shape the client handed back. */
function rowsFrom(payload: unknown): SessionRow[] {
  const body = payload as { data?: unknown; sessions?: unknown } | null;
  const list = Array.isArray(payload) ? payload : body?.data ?? body?.sessions;
  return Array.isArray(list) ? (list as SessionRow[]) : [];
}

export function mapSessions(payload: unknown, now: number = Date.now()): DeviceSession[] {
  return rowsFrom(payload).map((row) => {
    const id = asText(row.id) ?? '';
    const device =
      asText(row.device) ?? asText(row.user_agent) ?? 'Unknown device';
    const current = row.current === true;
    const lastSeen = row.last_seen_at ?? row.created_at ?? row.last_active;
    return {
      id,
      device,
      origin: asText(row.origin) ?? asText(row.ip_country),
      trusted: row.trusted_device === true,
      current,
      time: current ? 'Active now' : lastSeenLabel(lastSeen, now),
      mobile: isMobileDevice(device),
      revocable: id !== '' && !current,
    };
  });
}
