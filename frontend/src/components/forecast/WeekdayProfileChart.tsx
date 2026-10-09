import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis } from
'recharts';
import { EmptyState } from '../ui/EmptyState';
import { bottomAxisLabel, leftAxisLabel } from '../ui/axisLabel';
import { demandAxisScale, formatDemandTick } from '../../lib/chartScale';
import { weekdayProfile } from '../../lib/histogram';

export interface WeekdayProfileChartProps {
  rows: Array<{ date: string; demand_qty: number }>;
}

/**
 * Average demand per weekday, from the ingested history.
 *
 * This is the chart that answers "are we busier on Fridays", which the daily
 * forecast line hides inside its own noise. It needs history to mean anything,
 * so it shows an empty state rather than seven zeroes when nothing is ingested.
 */
export function WeekdayProfileChart({ rows }: WeekdayProfileChartProps) {
  const profile = useMemo(() => weekdayProfile(rows), [rows]);
  const covered = profile.reduce((count, day) => count + day.days, 0);

  if (covered === 0) {
    return (
      <EmptyState
        variant="empty"
        size="sm"
        title="No history yet"
        description="Import a few weeks of demand and the busiest weekdays appear here. One day per weekday gives a flat chart, so it reads better after a fortnight."
      />
    );
  }

  const { max: yMax, ticks: yTicks } = demandAxisScale(profile.map((day) => day.average));

  return (
    <div className="h-[200px] w-full overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={profile} margin={{ top: 12, right: 8, bottom: 22, left: 12 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            interval={0}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: '#cbd5e1' }}
            height={38}
            label={bottomAxisLabel('Weekday')} />
          
          <YAxis
            domain={[0, yMax]}
            ticks={yTicks}
            tickFormatter={formatDemandTick}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={48}
            label={leftAxisLabel('Average visitors')} />
          
          <Tooltip
            cursor={{ fill: 'rgba(148, 163, 184, 0.14)' }}
            contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }}
            formatter={(value: number, name: string) => [
            Math.round(value).toLocaleString('en-US'),
            name]
            } />
          
          <Bar
            dataKey="average"
            name="Average visitors"
            fill="#0f6d67"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false} />
          
        </BarChart>
      </ResponsiveContainer>
    </div>);

}
