/**
 * The device rows two screens render, from the payload `GET /api/v1/me/sessions`
 * actually sends: `device`, `origin`, `trusted_device`, `last_seen_at` and
 * `current` (the token's `sid` claim). Both Profile and the Settings Security
 * tab mapped this by hand before, and disagreed — one fell back to a
 * `created_at` the API never sends, the other dropped the country, and one
 * invented a random id for a row that had none, so its Revoke button asked the
 * API to delete a session that did not exist.
 */
import { describe, it, expect } from 'vitest';
import { mapSessions, lastSeenLabel, isMobileDevice } from '../lib/sessions';

const ISO = '2026-09-20T08:00:00Z';

describe('sessions row mapping', () => {
  it('reads the fields the API sends, and labels the current one', () => {
    const now = new Date('2026-09-20T08:30:00Z').getTime();
    const rows = mapSessions(
      {
        data: [
          { id: 'sess-mine', device: 'MacBook Pro', origin: '127.0.0.1', trusted_device: false, last_seen_at: ISO, current: true },
          { id: 'sess-phone', device: 'iPhone', origin: '127.0.0.1', trusted_device: true, last_seen_at: ISO, current: false },
        ],
      },
      now,
    );

    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      id: 'sess-mine',
      device: 'MacBook Pro',
      origin: '127.0.0.1',
      trusted: false,
      current: true,
      time: 'Active now',
      revocable: false,
    });
    expect(rows[1]).toMatchObject({
      device: 'iPhone',
      trusted: true,
      current: false,
      time: '30 min ago',
      mobile: true,
      revocable: true,
    });
  });

  it('still reads the pre-rename `ip_country` key, but `origin` wins', () => {
    // Migration 013 renamed the column; a client talking to an un-migrated API
    // must keep rendering an origin instead of a blank.
    const rows = mapSessions({
      data: [
        { id: 'sess-old', device: 'MacBook Pro', ip_country: 'RW' },
        { id: 'sess-both', device: 'iPhone', origin: '127.0.0.1', ip_country: 'RW' },
      ],
    });
    expect(rows[0].origin).toBe('RW');
    expect(rows[1].origin).toBe('127.0.0.1');
  });

  it('offers no revoke for a row the API did not identify', () => {
    // The old mapping fell back to `Math.random()`, so the button existed and
    // fired DELETE /me/sessions/<gibberish>.
    const rows = mapSessions({ data: [{ device: 'Unknown browser', last_seen_at: ISO }] });
    expect(rows[0].id).toBe('');
    expect(rows[0].revocable).toBe(false);
  });

  it('says nothing about when a row was last seen rather than inventing a time', () => {
    // No `last_seen_at`, and no `created_at` on the payload to stand in for it.
    const rows = mapSessions({ data: [{ id: 'sess-1', device: 'iPhone', created_at: undefined }] });
    expect(rows[0].time).toBe('');
  });

  it('reads a bare list and an empty payload without throwing', () => {
    expect(mapSessions([{ id: 'a', device: 'iPad' }])).toHaveLength(1);
    expect(mapSessions(null)).toEqual([]);
    expect(mapSessions({ data: null })).toEqual([]);
  });
});

describe('time labels', () => {
  const now = new Date('2026-09-22T08:00:00Z').getTime();

  it('steps from minutes to hours to days', () => {
    expect(lastSeenLabel('2026-09-22T07:50:00Z', now)).toBe('10 min ago');
    expect(lastSeenLabel('2026-09-22T05:00:00Z', now)).toBe('3 hours ago');
    expect(lastSeenLabel('2026-09-20T08:00:00Z', now)).toBe('2 days ago');
  });

  it('reports a just-now session as active instead of "0 min ago"', () => {
    expect(lastSeenLabel('2026-09-22T07:59:40Z', now)).toBe('Active now');
  });

  it('returns nothing for a timestamp it cannot read', () => {
    expect(lastSeenLabel(undefined, now)).toBe('');
    expect(lastSeenLabel('not-a-date', now)).toBe('');
  });
});

describe('device kind', () => {
  it('recognises a phone user agent', () => {
    expect(isMobileDevice('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)')).toBe(true);
    expect(isMobileDevice('MacBook Pro')).toBe(false);
  });
});
