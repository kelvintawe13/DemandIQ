import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import {
  axisTicks,
  demandAxisScale,
  formatCountTick,
  formatDemandTick,
  niceAxisMax,
} from '../lib/chartScale';
import { bottomAxisLabel, leftAxisLabel } from '../components/ui/axisLabel';

// A forecast of 33 was once drawn on a 0–20,000 axis, ticked `0, 5K, 10K, 15K,
// 20K`: the line sat on the floor and its label read `0K`. Two hardcoded
// assumptions, one chart. These cover the pure scale, and the last block is a
// source scan so the next chart cannot hardcode its own.

describe('formatDemandTick', () => {
  it('leaves a number in the hundreds alone', () => {
    // The reported case: a day of demand below 1,000 used to render as `0K`.
    expect(formatDemandTick(420)).toBe('420');
    expect(formatDemandTick(33)).toBe('33');
    expect(formatDemandTick(999)).toBe('999');
    expect(formatDemandTick(0)).toBe('0');
  });

  it('speaks in thousands only once the value is in thousands', () => {
    expect(formatDemandTick(1000)).toBe('1K');
    expect(formatDemandTick(2000)).toBe('2K');
    expect(formatDemandTick(20000)).toBe('20K');
  });

  it('keeps one decimal when the thousands are not whole', () => {
    expect(formatDemandTick(1500)).toBe('1.5K');
    expect(formatDemandTick(4321)).toBe('4.3K');
    expect(formatDemandTick(9950)).toBe('10K');
  });

  it('does not print NaN onto an axis', () => {
    expect(formatDemandTick(Number.NaN)).toBe('');
    expect(formatDemandTick(Number.POSITIVE_INFINITY)).toBe('');
  });
});

describe('formatCountTick', () => {
  it('stays a plain count, with no `K` suffix', () => {
    // The histogram's y-axis counts days. `5` is five days, not five thousand
    // visitors, and the two formatters must not be interchangeable.
    expect(formatCountTick(0)).toBe('0');
    expect(formatCountTick(5)).toBe('5');
    expect(formatCountTick(1200)).toBe('1,200');
  });

  it('does not print NaN onto an axis', () => {
    expect(formatCountTick(Number.NaN)).toBe('');
  });
});

describe('niceAxisMax', () => {
  it('hugs the data instead of pinning a ceiling', () => {
    expect(niceAxisMax(33)).toBe(40);
    expect(niceAxisMax(50)).toBe(80);
    expect(niceAxisMax(100)).toBe(200);
    expect(niceAxisMax(412)).toBe(800);
  });

  it('is never below the data', () => {
    for (const value of [1, 3, 7, 33, 50, 99, 100, 412, 999, 1000, 4321, 20000]) {
      expect(niceAxisMax(value)).toBeGreaterThanOrEqual(value);
    }
  });

  it('still steps in whole thousands when the data is that large', () => {
    expect(niceAxisMax(20000)).toBe(20000);
    expect(niceAxisMax(25000)).toBe(40000);
  });

  it('gives a degenerate series a usable span', () => {
    expect(niceAxisMax(0)).toBeGreaterThan(0);
    expect(axisTicks(niceAxisMax(0))).toEqual([0, 0.5, 1, 1.5, 2]);
  });
});

describe('axis titles', () => {
  it('rotates the vertical title and anchors the horizontal one', () => {
    const left = leftAxisLabel('Visitors');
    expect(left.value).toBe('Visitors');
    expect(left.angle).toBe(-90);
    expect(left.position).toBe('insideLeft');

    const bottom = bottomAxisLabel('Day');
    expect(bottom.value).toBe('Day');
    expect(bottom.position).toBe('insideBottom');
  });
});

describe('demandAxisScale', () => {
  it('sizes a typical day so the forecast is readable', () => {
    const { max, ticks } = demandAxisScale([42, 39, 45]);
    expect(max).toBe(80);
    expect(ticks).toEqual([0, 20, 40, 60, 80]);

    // The regression, stated as a proportion: the old 0–20,000 axis put a
    // busy day at 0.2% of the plot height. Anything above half is readable.
    expect(Math.max(42, 39, 45) / max).toBeGreaterThan(0.5);
  });

  it('includes the top of the confidence interval when it is the largest', () => {
    // A band top above the history must set the ceiling, or the interval is
    // clipped off the top of the chart.
    const { max } = demandAxisScale([20, 25, 130]);
    expect(max).toBeGreaterThanOrEqual(130);
  });

  it('survives an empty or all-zero series', () => {
    expect(demandAxisScale([]).max).toBe(20);
    expect(demandAxisScale([0, 0]).max).toBe(20);
    expect(demandAxisScale([Number.NaN]).max).toBe(20);
  });
});

// --------------------------------------------------------------------------- //
// The rule, enforced across every chart
// --------------------------------------------------------------------------- //

const SRC = join(process.cwd(), 'src');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      return entry === '__tests__' ? [] : walk(full);
    }
    return full.endsWith('.tsx') ? [full] : [];
  });
}

/** Files that draw a recharts axis. */
function chartFiles(): { path: string; source: string }[] {
  return walk(join(SRC, 'components'))
    .map((path) => ({ path, source: readFileSync(path, 'utf8') }))
    .filter(({ source }) => /from\s+['"]recharts['"]/.test(source) && /<YAxis/.test(source));
}

describe('chart axes', () => {
  it('finds the charts it is meant to be guarding', () => {
    // A guard that silently matches nothing is worse than no guard.
    expect(chartFiles().length).toBeGreaterThanOrEqual(3);
  });

  it('takes every tick label from a shared formatter', () => {
    // Two flavours are legitimate: demand (which speaks in `K` once the value is
    // in thousands) and a count of days (which must never). What is not
    // legitimate is a hand-rolled formatter, which is how the `0K` regression
    // got in.
    const offenders = chartFiles()
      .filter(({ source }) => {
        const formatters = source.match(/tickFormatter=\{[^}]*\}?/g) ?? [];
        return formatters.some(
          (formatter) =>
            !formatter.includes('formatDemandTick') &&
            !formatter.includes('formatCountTick'),
        );
      })
      .map(({ path }) => path.slice(SRC.length + 1));
    expect(offenders).toEqual([]);
  });

  it('titles both axes through the shared helpers', () => {
    // Charts drew tick numbers with no unit — a column of `0 35 70 105` did not
    // say whether it counted visitors, days or issues. Every cartesian chart must
    // name its axes with `axisLabel`, not a one-off inline label.
    const offenders = chartFiles()
      .filter(({ source }) => !/leftAxisLabel|bottomAxisLabel/.test(source))
      .map(({ path }) => path.slice(SRC.length + 1));
    expect(offenders).toEqual([]);
  });

  it('never pins the y-axis to a hardcoded ceiling', () => {
    // `domain={[0, 20000]}` is the shape that hid the forecast; an axis limit
    // has to come from the data (`demandAxisScale`) or from recharts ('auto').
    const offenders = chartFiles()
      .filter(({ source }) => /domain=\{\[\s*0\s*,\s*\d/.test(source))
      .map(({ path }) => path.slice(SRC.length + 1));
    expect(offenders).toEqual([]);
  });
});
