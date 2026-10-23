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
import { formatCountTick } from '../../lib/chartScale';
import { histogram } from '../../lib/histogram';

export interface DemandDistributionChartProps {
  /** The demand values to bucket — forecast points, or ingested history. */
  values: number[];
  /** Which set is being shown; only used for the copy. */
  scope: 'forecast' | 'history';
}

/**
 * A histogram of demand values: how many days fall in each demand band, rather
 * than which day was which.
 *
 * The y-axis is a count of days, not demand, so it is formatted with
 * `formatCountTick` — the `K` suffix belongs on a demand axis and would be
 * misleading on a count of five.
 */
export function DemandDistributionChart({ values, scope }: DemandDistributionChartProps) {
  const bins = useMemo(() => histogram(values), [values]);

  if (bins.length === 0) {
    return (
      <EmptyState
        variant="empty"
        size="sm"
        title={scope === 'forecast' ? 'No forecast to summarise' : 'No history yet'}
        description={
        scope === 'forecast' ?
        'Generate a forecast to see how the days are spread across demand levels.' :
        'Import demand and the spread of your busiest and quietest days appears here.'
        } />);


  }

  const peak = Math.max(...bins.map((bin) => bin.count));

  return (
    <div className="h-[175px] w-full overflow-hidden">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={bins} margin={{ top: 12, right: 8, bottom: 22, left: 12 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            // Every bin label matters on a histogram: the gaps between them are
            // what makes it readable as a distribution.
            interval={0}
            tick={{ fill: '#64748b', fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: '#cbd5e1' }}
            height={38}
            label={bottomAxisLabel('Visitors')} />
          
          <YAxis
            domain={[0, peak]}
            allowDecimals={false}
            tickFormatter={formatCountTick}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={40}
            label={leftAxisLabel('Number of days')} />
          
          <Tooltip
            cursor={{ fill: 'rgba(148, 163, 184, 0.14)' }}
            contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }}
            formatter={(value: number, name: string) => [
            value === 1 ? '1 day' : `${value} days`,
            name]
            } />
          
          <Bar
            dataKey="count"
            name={
            scope === 'forecast' ? 'Days forecast at this level' : 'Days at this level'
            }
            fill="#0f6d67"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false} />
          
        </BarChart>
      </ResponsiveContainer>
    </div>);

}
