/**
 * Y-axis scaling for the demand charts.
 *
 * Demand in this platform is a visitor count in the hundreds, and the forecast
 * chart was sized as if it were in the tens of thousands. Two separate
 * assumptions did the damage:
 *
 *  * the axis ceiling was pinned to steps of 20,000
 *    (`Math.ceil(maxVal / 20000) * 20000 || 80000`), so a day of 50 visitors got
 *    a 0–20,000 axis whatever the data said; and
 *  * every tick was divided by 1,000 and labelled `K`, so those ticks read
 *    `0`, `5K`, `10K`, `15K`, `20K` while the data underneath was in the tens.
 *
 * Together a forecast of 33 was drawn along the floor of a 0–20,000 axis, under
 * a label that said `0K`. It could be neither seen nor read. Both numbers follow
 * the data now, and both live here so the three charts cannot drift apart again.
 */

/**
 * A tick label that only speaks in thousands when the number is one.
 *
 * `1500` reads `1.5K`, `2000` reads `2K`, and `420` reads `420` — the last case
 * being the one that used to come out as `0K`.
 */
export function formatDemandTick(value: number): string {
  if (!Number.isFinite(value)) return '';
  if (value === 0) return '0';
  if (Math.abs(value) >= 1000) {
    const thousands = value / 1000;
    const rounded = Math.round(thousands * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}K`;
  }
  return value.toLocaleString('en-US');
}

/**
 * A tick label for an axis that counts days rather than visitors.
 *
 * The `K` suffix belongs on a demand axis; on a count of five it would be
 * nonsense. Kept beside `formatDemandTick` so the two axis flavours are
 * visibly different at the call site and `__tests__/chartScale.test.ts` can
 * still ask every `tickFormatter` to name one of them.
 */
export function formatCountTick(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('en-US') : '';
}

/**
 * The smallest "nice" axis ceiling at or above `max`, using 1/2/5 × 10ⁿ steps.
 *
 * `33` → `40`, `50` → `80`, `412` → `600`, `20000` → `20000`. A ceiling that
 * hugs the data is the whole point: the alternative was a fixed 20,000, which
 * left 0.25% of the plot height for a typical day.
 *
 * `steps` is the number of intervals, so the returned ceiling divides evenly
 * into that many ticks.
 */
export function niceAxisMax(max: number, steps = 4): number {
  // A ceiling of 0 would collapse the axis onto the baseline; fall back to a
  // small positive span so an all-zero series still draws something sane.
  const target = Math.max(max, 1) / steps;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const normalized = target / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  return step * steps;
}

/** Evenly spaced tick values from 0 to `max`, inclusive of both ends. */
export function axisTicks(max: number, steps = 4): number[] {
  return Array.from({ length: steps + 1 }, (_, index) => (max / steps) * index);
}

/**
 * The ceiling and ticks for a set of demand values.
 *
 * The floor of 10 keeps a nearly-empty series (a single small day, or one zero)
 * from producing a degenerate 0–1 axis, while staying far below a real day's
 * demand so the data is never squashed into the bottom of the plot.
 */
export function demandAxisScale(
  values: number[],
  steps = 4,
): { max: number; ticks: number[] } {
  const finite = values.filter((value) => Number.isFinite(value));
  const peak = finite.length ? Math.max(...finite) : 0;
  const max = niceAxisMax(Math.max(peak, 10), steps);
  return { max, ticks: axisTicks(max, steps) };
}
