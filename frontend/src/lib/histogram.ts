/**
 * Bucketing for the distribution charts on the forecast page.
 *
 * A histogram answers a different question from the line chart beside it: not
 * "how much demand on each day" but "how many days look like this". The
 * forecast horizon and the ingested history are summarised the same way, so the
 * bucketing lives here instead of being written twice with two sets of edges.
 */
import { formatDemandTick } from './chartScale';

export interface Bin {
  /** Readable range, e.g. `40–60`, used as the axis label. */
  label: string;
  /** Inclusive lower edge. */
  from: number;
  /** Upper edge — exclusive, except on the final bin. */
  to: number;
  count: number;
}

/**
 * Split `values` into equal-width bins.
 *
 * Edges are whole visitors because demand is a count: a bin labelled
 * `12.45–53.55` is precise about a number nobody ever measured. The bin count is
 * also clamped to the number of whole integers spanned, so narrow data cannot
 * produce two bins with the same rounded label.
 */
export function histogram(values: number[], binCount = 5): Bin[] {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0 || binCount < 1) return [];

  const low = Math.floor(Math.min(...finite));
  const high = Math.ceil(Math.max(...finite));

  // Every value identical: one bin beats five zero-width ones.
  if (low === high) {
    return [{ label: formatDemandTick(low), from: low, to: high, count: finite.length }];
  }

  const bins = Math.max(1, Math.min(binCount, high - low));
  const width = (high - low) / bins;

  const result: Bin[] = Array.from({ length: bins }, (_, index) => {
    const from = low + width * index;
    const to = index === bins - 1 ? high : low + width * (index + 1);
    return {
      label: `${formatDemandTick(Math.round(from))}–${formatDemandTick(Math.round(to))}`,
      from,
      to,
      count: 0,
    };
  });

  for (const value of finite) {
    // `high` must land inside the final bin rather than one past the end.
    const index = Math.min(bins - 1, Math.floor((value - low) / width));
    result[index].count += 1;
  }

  return result;
}

export interface WeekdayAverage {
  label: string;
  /** Mean demand for that weekday; 0 when the history has no such day. */
  average: number;
  /** How many dated rows fed the average — a 0 here means "no evidence". */
  days: number;
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

/**
 * Mean demand per weekday, always seven entries in Mon…Sun order.
 *
 * Always seven, so the axis does not reshuffle itself when a day has no data,
 * and `days` is carried alongside so the caller can tell a genuine zero from an
 * empty bucket.
 */
export function weekdayProfile(
  rows: Array<{ date: string; demand_qty: number }>,
): WeekdayAverage[] {
  const totals = WEEKDAYS.map(() => ({ sum: 0, days: 0 }));

  for (const row of rows) {
    if (!Number.isFinite(row.demand_qty)) continue;
    const at = new Date(`${row.date}T00:00:00Z`);
    if (Number.isNaN(at.getTime())) continue;
    // getUTCDay is 0 = Sunday; shift it so the array starts on Monday.
    const index = (at.getUTCDay() + 6) % 7;
    totals[index].sum += row.demand_qty;
    totals[index].days += 1;
  }

  return WEEKDAYS.map((label, index) => ({
    label,
    average: totals[index].days === 0 ? 0 : totals[index].sum / totals[index].days,
    days: totals[index].days,
  }));
}
