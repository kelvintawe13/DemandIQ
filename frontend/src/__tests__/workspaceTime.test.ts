/**
 * The workspace's calendar day is Africa/Kigali, not UTC.
 *
 * A screen that asked `new Date().toISOString().slice(0, 10)` for "today" read
 * the UTC day instead. At 23:30 UTC that is a different day in Kigali (UTC+2),
 * so the Demand Forecast page's `today + 1` target — and the demand-entry
 * `max` — were a day out for two hours every night. These tests pin the day
 * against a fixed instant so the two can never silently re-merge.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  addDaysISO,
  getWorkspaceTimezone,
  isoDateInTZ,
  isoDaysAgo,
  setWorkspaceTimezone,
  todayISO,
  WORKSPACE_TIMEZONE,
} from '../lib/workspaceTime';

/** 23:30 UTC is already the next calendar day in Kigali (UTC+2). */
const LATE_UTC = new Date('2026-09-30T23:30:00Z');

afterEach(() => {
  vi.useRealTimers();
});

describe('isoDateInTZ', () => {
  it('reads the same instant as a different day per zone', () => {
    expect(isoDateInTZ(LATE_UTC, 'UTC')).toBe('2026-09-30');
    expect(isoDateInTZ(LATE_UTC, 'Africa/Kigali')).toBe('2026-10-01');
    expect(isoDateInTZ(LATE_UTC, 'America/New_York')).toBe('2026-09-30');
  });

  it('defaults to the workspace zone', () => {
    expect(WORKSPACE_TIMEZONE).toBe('Africa/Kigali');
    expect(isoDateInTZ(LATE_UTC)).toBe('2026-10-01');
  });
});

describe('todayISO', () => {
  it('is the Kigali day, which is ahead of the UTC day late in the evening', () => {
    vi.useFakeTimers();
    vi.setSystemTime(LATE_UTC);

    expect(todayISO()).toBe('2026-10-01');
    // The old derivation, kept here so the regression is explicit.
    expect(new Date().toISOString().slice(0, 10)).toBe('2026-09-30');
  });

  it('agrees with the UTC day when the two zones share it', () => {
    // 01:00 UTC: UTC has rolled to 2026-10-01 and Kigali is two hours past it,
    // so both read 2026-10-01. Pins the other direction of the off-by-one.
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-01T01:00:00Z'));
    expect(todayISO()).toBe('2026-10-01');
  });
});

describe('addDaysISO', () => {
  it('shifts by whole days and rolls over month and year', () => {
    expect(addDaysISO('2026-09-30', 1)).toBe('2026-10-01');
    expect(addDaysISO('2026-10-01', -1)).toBe('2026-09-30');
    expect(addDaysISO('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDaysISO('2026-03-01', -1)).toBe('2026-02-28');
  });
});

describe('isoDaysAgo', () => {
  it('counts back from the workspace today', () => {
    vi.useFakeTimers();
    vi.setSystemTime(LATE_UTC);

    expect(isoDaysAgo(0)).toBe('2026-10-01');
    expect(isoDaysAgo(29)).toBe('2026-09-02');
  });
});

// Deliberately last: it changes the module's active zone, and restores it so the
// assertions above keep reading the fallback.
describe('persisted zone', () => {
  it('is remembered for the next first paint', () => {
    setWorkspaceTimezone('Africa/Nairobi');
    expect(getWorkspaceTimezone()).toBe('Africa/Nairobi');
    // The value read synchronously at import, before `/workspace` can answer.
    expect(localStorage.getItem('demandiq_workspace_timezone')).toBe('Africa/Nairobi');

    setWorkspaceTimezone(WORKSPACE_TIMEZONE);
    expect(getWorkspaceTimezone()).toBe(WORKSPACE_TIMEZONE);
  });

  it('ignores a blank value', () => {
    setWorkspaceTimezone('   ');
    expect(getWorkspaceTimezone()).toBe(WORKSPACE_TIMEZONE);
  });
});
