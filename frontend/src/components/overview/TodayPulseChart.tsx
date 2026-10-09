import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis } from
'recharts';
import { EmptyState } from '../ui/EmptyState';
import { bottomAxisLabel, leftAxisLabel } from '../ui/axisLabel';
import { formatDemandTick } from '../../lib/chartScale';

export interface PulsePoint {
  label: string;
  actual: number | null;
  predicted: number;
}

export interface TodayPulseChartProps {
  data: PulsePoint[];
}

/**
 * Live TodayPulse chart — renders `data` from /analytics/trend or /forecasts.
 * Falls back to EmptyState when empty (no mock).
 */
export function TodayPulseChart({ data }: TodayPulseChartProps) {
  if (!data || data.length === 0) {
    return (
      <EmptyState
        variant="empty"
        size="sm"
        title="No data yet"
        description="Ingest demand first"
      />
    );
  }

  return (
    <div className="h-[190px] w-full sm:h-[210px]">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 22, left: 12 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: '#cbd5e1' }}
            height={38}
            label={bottomAxisLabel('Day')} />

          <YAxis
            tickFormatter={formatDemandTick}
            tick={{ fill: '#64748b', fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={44}
            label={leftAxisLabel('Visitors')} />

          <Tooltip
            cursor={{ stroke: '#94a3b8', strokeDasharray: '4 4' }}
            contentStyle={{ borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }}
            formatter={(value: number, name: string) => [
            value != null ? value.toLocaleString('en-US') : '—',
            name === 'actual' ? 'Actual' : 'Predicted']
            } />

          <Area
            type="monotone"
            dataKey="predicted"
            stroke="#17a294"
            strokeWidth={2}
            strokeDasharray="6 5"
            fill="#17a294"
            fillOpacity={0.08}
            name="predicted"
            isAnimationActive={false} />

          <Line
            type="monotone"
            dataKey="actual"
            stroke="#0f6d67"
            strokeWidth={2.4}
            dot={{ r: 2.5, fill: '#0f6d67', strokeWidth: 0 }}
            connectNulls={false}
            name="actual"
            isAnimationActive={false} />

        </ComposedChart>
      </ResponsiveContainer>
    </div>);

}