import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { EmptyState } from './EmptyState';
import { bottomAxisLabel, leftAxisLabel } from './axisLabel';
import { formatCountTick } from '../../lib/chartScale';

export interface CountDatum {
  label: string;
  count: number;
}

export interface CountChartProps {
  data: CountDatum[];
  /** `columns` are upright bars, `bars` run horizontally for long labels. */
  layout?: 'columns' | 'bars';
  /** What one bar is — `devices`, `open issues` — used in the tooltip. */
  noun?: string;
  /** Names the category axis: what the rows/columns are, e.g. `Role`, `Check`. */
  categoryLabel?: string;
  /** Names the value axis; defaults to `Number of ${noun}`. */
  valueLabel?: string;
  /**
   * Width of the category band on a `layout="bars"` chart, in px.
   *
   * 110 is plenty for a device name or a check name on one line, but a label
   * wider than the band is drawn from the band's right edge leftwards — it pokes
   * out of the chart's own `<svg>`, which clips. Measured on the access
   * console's action chart: `Preferences updated` is 109px wide against a 5px
   * tick margin, so it rendered 7px outside the surface at every width. Widen
   * this when the labels are long; a two-line label is fine, a clipped one is not.
   */
  labelWidth?: number;
  emptyTitle?: string;
  emptyDescription?: string;
}

/**
 * A count of things grouped by something — which check raised the issues, which
 * restaurant the devices are in, how long since each last checked in.
 *
 * The y-axis counts objects, not demand, so it uses `formatCountTick`: a count
 * of five must never be labelled `5K`, and `__tests__/chartScale.test.ts` scans
 * every chart in `components/` to keep that true. `layout="bars"` exists because
 * check names, role names and restaurant names are long: an upright bar chart
 * cannot fit five of them across a phone ("Administrator" and "Analyst" were
 * measured overlapping at 320px), while a horizontal one gives each label a row.
 *
 * The ceiling comes from the data (`domain={[0, peak]}`), never a pinned
 * constant — the same guard the demand charts are held to.
 */
export function CountChart({
  data,
  layout = 'columns',
  noun = 'items',
  labelWidth = 110,
  categoryLabel,
  valueLabel,
  emptyTitle = 'Nothing to show yet',
  emptyDescription,
}: CountChartProps) {
  if (data.length === 0 || data.every((datum) => datum.count === 0)) {
    return <EmptyState variant="empty" size="sm" title={emptyTitle} description={emptyDescription} />;
  }

  const horizontal = layout === 'bars';
  const peak = Math.max(...data.map((datum) => datum.count));
  // Every chart states its unit: a bare `0 5 10` axis does not say whether it
  // counts devices, issues or days.
  const valueTitle = valueLabel ?? `Number of ${noun}`;

  /*
   * A horizontal chart is one row per label, so its height has to follow the row
   * count. Pinned at 210px, seven rows left a 30px band — and recharts wraps a
   * long category label onto a second line rather than dropping it, so a
   * two-line label is 25px tall and consecutive labels met at a measured **0px
   * gap** ("Quality rule switched" against "Policy comparison run"). 34px per row
   * gives every label room; a two-row chart is still exactly as tall as before.
   *
   * Capped at 14 rows (476px): past that the panel is a table, and callers with
   * an unbounded list are expected to bound it themselves — the access console
   * folds its tail into "Other (n)".
   */
  const height = horizontal ? Math.max(210, Math.min(data.length, 14) * 34) : 210;

  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout={horizontal ? 'vertical' : 'horizontal'}
          margin={
            horizontal
              ? { top: 4, right: 16, bottom: 22, left: categoryLabel ? 10 : 0 }
              : /* Not a negative left margin. `left: -20` is the usual recharts
                   trick for reclaiming the y-axis gutter, but it shifts the axis
                   band outside the chart's own `<svg>`, and that svg has
                   `overflow: hidden`: the tick text was measured starting at
                   **x = -5** in user units, so the digit was cut. 20px of plot
                   width is cheaper than a clipped axis.

                   `left: 12` and `bottom: 22` are the room the two axis titles
                   need: the rotated y title sits at the axis band's left edge, so
                   it needs a few px of margin or its ink falls outside the same
                   clipped svg, and the x title sits below the tick labels. */
                { top: 12, right: 8, bottom: 22, left: 12 }
          }>
          <CartesianGrid stroke="#e2e8f0" vertical={horizontal} horizontal={!horizontal} />

          {horizontal ? (
            <>
              <XAxis
                type="number"
                domain={[0, peak]}
                allowDecimals={false}
                tickFormatter={formatCountTick}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: '#cbd5e1' }}
                height={44}
                label={bottomAxisLabel(valueTitle)}
              />
              <YAxis
                type="category"
                dataKey="label"
                width={labelWidth}
                tick={{ fill: '#475569', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                label={categoryLabel ? leftAxisLabel(categoryLabel) : undefined}
              />
            </>
          ) : (
            <>
              <XAxis
                type="category"
                dataKey="label"
                interval={0}
                tick={{ fill: '#475569', fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: '#cbd5e1' }}
                height={categoryLabel ? 46 : 30}
                label={categoryLabel ? bottomAxisLabel(categoryLabel) : undefined}
              />
              <YAxis
                type="number"
                domain={[0, peak]}
                allowDecimals={false}
                tickFormatter={formatCountTick}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={34}
                label={leftAxisLabel(valueTitle)}
              />
            </>
          )}

          <Tooltip
            cursor={{ fill: 'rgba(148, 163, 184, 0.14)' }}
            contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }}
          />

          <Bar
            dataKey="count"
            name={noun}
            fill="#0f6d67"
            radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
