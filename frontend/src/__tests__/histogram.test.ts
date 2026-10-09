/**
 * Bucketing for the forecast distribution charts.
 *
 * These are the numbers the histograms draw, so the assertions are about the
 * properties that make a histogram readable: every input lands in exactly one
 * bin, the edges are distinct, and a flat series does not produce five empty
 * bins with the same rounded label.
 */
import { describe, it, expect } from 'vitest';
import { histogram, weekdayProfile } from '../lib/histogram';

describe('histogram', () => {
  it('returns nothing for nothing', () => {
    expect(histogram([])).toEqual([]);
    expect(histogram([Number.NaN])).toEqual([]);
  });

  it('gives a flat series a single bin', () => {
    // Five identical days would otherwise be five zero-width bins, rendered as
    // five identical axis labels.
    const bins = histogram([40, 40, 40]);
    expect(bins).toHaveLength(1);
    expect(bins[0].count).toBe(3);
    expect(bins[0].label).toBe('40');
  });

  it('counts every value exactly once', () => {
    const values = [12, 33, 34, 35, 46, 47, 48, 49, 50, 99];
    const bins = histogram(values);
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(values.length);
  });

  it('labels its bins with whole visitors and distinct edges', () => {
    const bins = histogram([33, 39.5, 46.04, 49.87, 33]);
    const labels = bins.map((bin) => bin.label);

    expect(new Set(labels).size).toBe(labels.length);
    for (const label of labels) {
      for (const edge of label.split('–')) {
        expect(edge).toMatch(/^\d+$/);
      }
    }
  });

  it('never runs the last value off the end', () => {
    // The maximum is the classic off-by-one: `floor((max - low) / width)` is
    // exactly `bins`, which is one past the array.
    const bins = histogram([10, 20, 30, 40, 50], 4);
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(5);
    expect(bins[bins.length - 1].count).toBeGreaterThan(0);
  });

  it('does not invent bins wider than the data can fill', () => {
    // Three distinct integers cannot make five distinct whole-number edges.
    expect(histogram([44, 45, 46], 5).length).toBeLessThanOrEqual(2);
  });

  it('speaks in thousands once the demand is that large', () => {
    const bins = histogram([2000, 4000, 6000]);
    expect(bins.some((bin) => bin.label.includes('K'))).toBe(true);
  });
});

describe('weekdayProfile', () => {
  it('always answers for all seven days, in Mon…Sun order', () => {
    const profile = weekdayProfile([]);
    expect(profile.map((day) => day.label)).toEqual([
      'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun',
    ]);
    expect(profile.every((day) => day.days === 0 && day.average === 0)).toBe(true);
  });

  it('averages each weekday across the weeks it has', () => {
    // 2026-09-28 is a Monday, so these are two Mondays and one Sunday.
    const profile = weekdayProfile([
      { date: '2026-09-28', demand_qty: 100 },
      { date: '2026-10-05', demand_qty: 200 },
      { date: '2026-10-04', demand_qty: 40 },
    ]);

    const monday = profile.find((day) => day.label === 'Mon');
    const sunday = profile.find((day) => day.label === 'Sun');
    expect(monday).toMatchObject({ average: 150, days: 2 });
    expect(sunday).toMatchObject({ average: 40, days: 1 });
  });

  it('tells an empty weekend apart from a weekend of zero demand', () => {
    // Both average 0; only `days` distinguishes "we have no evidence" from
    // "we know it was zero", and the chart's empty state keys off it.
    const profile = weekdayProfile([{ date: '2026-09-28', demand_qty: 0 }]);
    const monday = profile.find((day) => day.label === 'Mon');
    const saturday = profile.find((day) => day.label === 'Sat');
    expect(monday).toMatchObject({ average: 0, days: 1 });
    expect(saturday).toMatchObject({ average: 0, days: 0 });
  });

  it('skips rows it cannot date instead of counting them on Monday', () => {
    const profile = weekdayProfile([
      { date: 'not-a-date', demand_qty: 500 },
      { date: '2026-09-28', demand_qty: 10 },
    ]);
    const monday = profile.find((day) => day.label === 'Mon');
    expect(monday).toMatchObject({ average: 10, days: 1 });
    expect(profile.reduce((sum, day) => sum + day.days, 0)).toBe(1);
  });

  it('reads the day in UTC, so a date never shifts weekday', () => {
    // `new Date('2026-09-28T00:00:00')` in a negative-offset zone is Sunday the
    // 27th; the profile must not follow it there.
    const profile = weekdayProfile([{ date: '2026-09-28', demand_qty: 7 }]);
    expect(profile.find((day) => day.label === 'Mon')?.days).toBe(1);
  });
});
