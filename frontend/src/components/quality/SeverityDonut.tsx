import { MixDonut, type DonutSlice } from '../ui/MixDonut';

export interface SeverityMix {
  critical: number;
  warning: number;
  info: number;
}

/**
 * Severity order and colour, so the slices and the legend cannot drift apart.
 *
 * These are the issue palette, not a generic one: `critical` is only red here
 * because a critical issue is what red means on this platform. The donut itself
 * takes its colours from the caller for exactly this reason — a device status
 * mix reuses the same primitive without inheriting these meanings.
 */
const SLICES: Array<{ key: keyof SeverityMix; name: string; fill: string; dot: string }> = [
  { key: 'critical', name: 'Critical', fill: '#dc2626', dot: 'bg-red-500' },
  { key: 'warning', name: 'Warning', fill: '#d97706', dot: 'bg-amber-500' },
  { key: 'info', name: 'Info', fill: '#0284c7', dot: 'bg-sky-500' },
];

/**
 * How the open issues divide by severity.
 *
 * A three-way split is exactly what a donut is good at: the reader wants "is
 * anything on fire", not a precise comparison. The legend carries the counts and
 * shares so the numbers are still readable without hovering.
 */
export function SeverityDonut({ mix }: { mix: SeverityMix }) {
  const slices: DonutSlice[] = SLICES.map((slice) => ({
    key: slice.key,
    name: slice.name,
    value: mix[slice.key],
    fill: slice.fill,
    dot: slice.dot,
  }));

  return (
    <MixDonut
      slices={slices}
      centreLabel="open"
      emptyTitle="No open issues"
      emptyDescription="Nothing is failing right now, so there is nothing to break down."
    />
  );
}
