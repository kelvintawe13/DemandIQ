import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { EmptyState } from './EmptyState';

export interface DonutSlice {
  /** Stable identity for the row, not shown. */
  key: string;
  /** What the slice is called on screen. */
  name: string;
  value: number;
  /** The pie fill. */
  fill: string;
  /** The legend swatch class, so the dot and the slice cannot drift apart. */
  dot: string;
}

interface MixDonutProps {
  slices: DonutSlice[];
  /** The word under the total in the middle — `devices`, `open`. */
  centreLabel: string;
  emptyTitle?: string;
  emptyDescription?: string;
}

/**
 * How a set divides into a few named parts.
 *
 * A three-way split is exactly what a donut is good at: the reader wants "what
 * is the shape of this", not a precise comparison. The legend carries the counts
 * and the shares, so every number is readable without hovering — a chart whose
 * values only exist in a tooltip is a chart a screenshot cannot use.
 *
 * Slices with a value of zero are dropped rather than drawn as a hairline, and
 * the total is the sum of what is left, so the centre never claims a part that
 * is not shown. Colours arrive from the caller: a severity palette and a device
 * status palette mean different things and must not be guessed from a count.
 */
export function MixDonut({
  slices,
  centreLabel,
  emptyTitle = 'Nothing to show yet',
  emptyDescription,
}: MixDonutProps) {
  const shown = slices.filter((slice) => slice.value > 0);
  const total = shown.reduce((sum, slice) => sum + slice.value, 0);

  if (total === 0) {
    return <EmptyState variant="empty" size="sm" title={emptyTitle} description={emptyDescription} />;
  }

  const data = shown.map((slice) => ({ name: slice.name, value: slice.value, fill: slice.fill }));

  return (
    <div className="flex flex-wrap items-center gap-4">
      <div className="relative h-[168px] w-[168px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={52}
              outerRadius={80}
              paddingAngle={1}
              startAngle={90}
              endAngle={-270}
              stroke="none"
              isAnimationActive={false}>
              {data.map((slice) => (
                <Cell key={slice.name} fill={slice.fill} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-display text-h2 font-bold leading-none tabular-nums text-slate-900">
            {total}
          </span>
          <span className="mt-1 text-fine font-semibold uppercase tracking-wide text-slate-500">
            {centreLabel}
          </span>
        </div>
      </div>

      <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* Every named part, including the empty ones: "Revoked 0" is the
            reassurance, and dropping it would make the legend disagree with the
            status filter in the toolbar. */}
        {slices.map((slice) => (
          <li key={slice.key} className="flex items-center gap-2.5">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${slice.dot}`} aria-hidden="true" />
            <span className="min-w-0 flex-1 text-meta text-slate-700">{slice.name}</span>
            <span className="tabular-nums text-meta font-semibold text-slate-900">{slice.value}</span>
            <span className="w-10 text-right tabular-nums text-fine text-slate-500">
              {Math.round((slice.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
