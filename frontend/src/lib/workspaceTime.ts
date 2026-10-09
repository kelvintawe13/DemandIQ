/**
 * The workspace's calendar day.
 *
 * `workspaces.timezone` defaults to `Africa/Kigali` (`database/schema.sql:13`),
 * and every calendar feature the model sees is derived in that zone — see
 * `CalendarClock`, whose whole contract is "all calendar features must be
 * derived in the selected timeZone". A screen that instead asks
 * `new Date().toISOString()` for "today" is reading the **UTC** day, which is a
 * different calendar day for part of every day:
 *
 * * at 23:30 UTC, Kigali (UTC+2) is already on the next day, so the forecast
 *   target day `today + 1` is really today, and the demand-entry maximum is one
 *   day behind;
 * * for a browser west of UTC, in the evening UTC is already tomorrow, so a
 *   date the operator is allowed to enter is a day ahead of their own.
 *
 * Every screen that decides a date reads this one helper rather than re-deriving
 * the day, so none of them can disagree with the clock on the Overview.
 */

/**
 * IANA zone the workspace operates in, used until `GET /workspace` answers.
 * Mirrors `workspaces.timezone`'s default (`database/schema.sql:13`).
 */
export const WORKSPACE_TIMEZONE = 'Africa/Kigali';

/*
 * The active zone is module state rather than a constant so a workspace that
 * changed its timezone in Settings is honoured without threading it through
 * every screen. `useWorkspaceTimezone` resolves it once per page load and
 * `Settings.saveWorkspace` updates it on save; unset or blank falls back to the
 * documented default, so a failed read can never leave the app with no zone.
 */
const STORAGE_KEY = 'demandiq_workspace_timezone';

/**
 * The last zone this browser resolved, read *synchronously* at import.
 *
 * `GET /workspace` cannot resolve before first paint, so without a cached value
 * the very first render of a page would compute its day in the fallback zone and
 * only correct itself after the request landed — a visible jump for a workspace
 * that is not on the default. The cached value is what the last read answered;
 * the next read still overwrites it, so it can only be stale, never wrong for
 * long.
 */
function readStoredTimezone(): string | undefined {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored && stored.trim() ? stored.trim() : undefined;
  } catch {
    // No localStorage (private mode, SSR, a test without a DOM): default stands.
    return undefined;
  }
}

let activeTimezone: string = readStoredTimezone() ?? WORKSPACE_TIMEZONE;

/** The zone dates are currently read in. */
export function getWorkspaceTimezone(): string {
  return activeTimezone;
}

/** Adopt the workspace's configured zone. Blank values are ignored. */
export function setWorkspaceTimezone(timezone?: string | null): void {
  const next = typeof timezone === 'string' ? timezone.trim() : '';
  if (!next) return;
  activeTimezone = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Persisting is best-effort; the in-memory zone is what this session uses.
  }
}

/**
 * `YYYY-MM-DD` for an instant, as read in a timezone.
 *
 * `en-CA` is the locale whose short date is already ISO order, so this needs no
 * hand-rolled padding — and unlike `toISOString().slice(0, 10)` it answers in
 * the requested zone rather than always in UTC.
 */
export function isoDateInTZ(date: Date, timeZone: string = getWorkspaceTimezone()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

/** The workspace's current calendar day, as `YYYY-MM-DD`. */
export function todayISO(timeZone: string = getWorkspaceTimezone()): string {
  return isoDateInTZ(new Date(), timeZone);
}

/**
 * Shift a date-only `YYYY-MM-DD` by whole days (negative goes back).
 *
 * Date-only arithmetic on UTC midnight: there is no instant involved, so no
 * timezone can drift it, and `Date.UTC` handles month and year rollover.
 */
export function addDaysISO(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number);
  const next = new Date(Date.UTC(year, month - 1, day) + days * 86_400_000);
  return next.toISOString().slice(0, 10);
}

/** `days` before the workspace's today (`0` is today; negative is the future). */
export function isoDaysAgo(days: number, timeZone: string = getWorkspaceTimezone()): string {
  return addDaysISO(todayISO(timeZone), -days);
}
