import {
  Bar,
  BarChart,
  CartesianGrid,
  ErrorBar,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis } from
'recharts';
import { EmptyState } from '../ui/EmptyState';
import { bottomAxisLabel, leftAxisLabel } from '../ui/axisLabel';
import { demandAxisScale, formatDemandTick } from '../../lib/chartScale';

export interface ForecastDayPoint {
  /** Short day label, e.g. `Sep 29`. */
  label: string;
  point: number;
  lower: number;
  upper: number;
}

/**
 * The forecast as one bar per day, so days can be compared against each other.
 *
 * The line chart next door answers "what is the shape of demand over time";
 * this answers "which day is the big one", which is the question an operator
 * placing an order actually asks. Each bar carries the 90% interval as a
 * whisker, so a tall bar with a huge range reads as uncertain rather than busy.
 */
export function ForecastByDayChart({ data }: { data: ForecastDayPoint[] }) {
  if (data.length === 0) {
    return (
      <EmptyState
        variant="empty"
        size="sm"
        title="No forecast days"
        description="Generate a forecast to compare days against each other."
      />
    );
  }

  // The interval ends are fed in as well as the point: a bar taller than the
  // ceiling would be clipped, and the whisker is the tallest thing on the chart.
  const { max: yMax, ticks: yTicks } = demandAxisScale(
    data.flatMap((day) => [day.point, day.lower, day.upper]),
  );

  // recharts reads an ErrorBar array as *offsets* from the value, not absolute
  // bounds (`scale(value - low)` / `scale(value + high)`), so the interval is
  // converted here rather than handed over as [lower, upper].
  const series = data.map((day) => ({
    ...day,
    spread: [day.point - day.lower, day.upper - day.point] as [number, number],
  }));

  return (
    <div className="h-[220px] w-full overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={series} margin={{ top: 12, right: 8, bottom: 22, left: 12 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            interval="preserveStartEnd"
            minTickGap={6}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: '#cbd5e1' }}
            height={38}
            label={bottomAxisLabel('Day')} />
          
          <YAxis
            domain={[0, yMax]}
            ticks={yTicks}
            tickFormatter={formatDemandTick}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={48}
            label={leftAxisLabel('Visitors')} />
          
          <Tooltip cursor={{ fill: 'rgba(148, 163, 184, 0.14)' }} content={<DayTooltip />} />
          <Bar
            dataKey="point"
            name="Expected visitors"
            fill="#0f6d67"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}>
            
            <ErrorBar dataKey="spread" width={5} strokeWidth={2} stroke="#17a294" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>);

}

/** Shows the day's estimate next to the interval the bar's whisker draws. */
function DayTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ForecastDayPoint }>;
}) {
  const day = active ? payload?.[0]?.payload : undefined;
  if (!day) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-caption shadow-sm">
      <p className="font-semibold text-slate-900">{day.label}</p>
      <p className="text-slate-600">
        Expected {day.point.toLocaleString('en-US')} visitors
      </p>
      <p className="text-slate-500">
        90% range {day.lower.toLocaleString('en-US')}–{day.upper.toLocaleString('en-US')}
      </p>
    </div>);

}
