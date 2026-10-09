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
import type { ForecastPoint } from '../../data/forecast';
// mockSeries kept only for Storybook / type reference — not used as runtime fallback
import { EmptyState } from '../ui/EmptyState';
import { bottomAxisLabel, leftAxisLabel } from '../ui/axisLabel';
import { demandAxisScale, formatDemandTick } from '../../lib/chartScale';

export interface ForecastChartProps {
  data?: ForecastPoint[];
}

export function ForecastChart({ data }: ForecastChartProps) {
  if (!data || data.length === 0) {
    return <EmptyState variant="empty" size="sm" title="No data yet" description="Ingest demand first" />;
  }
  const series = data;
  // The ceiling comes from the data — history, forecast, and the top of the
  // interval — so a day of 50 visitors gets a 0–80 axis rather than 0–20,000.
  const { max: yMax, ticks: yTicks } = demandAxisScale(
    series.flatMap((p) => [p.historical ?? 0, p.forecast ?? 0, p.band ? p.band[1] : 0]),
  );
  return (
    <div className="h-[240px] w-full overflow-hidden sm:h-[260px]">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={series}
          margin={{ top: 8, right: 8, bottom: 22, left: 12 }}>
          
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="label"
            interval="preserveStartEnd"
            tick={{ fill: '#64748b', fontSize: 12 }}
            tickLine={false}
            axisLine={{ stroke: '#cbd5e1' }}
            height={38}
            label={bottomAxisLabel('Day')} />
          
          <YAxis
            domain={[0, yMax]}
            ticks={yTicks}
            tickFormatter={formatDemandTick}
            tick={{ fill: '#64748b', fontSize: 12 }}
            tickLine={false}
            axisLine={false}
            width={48}
            label={leftAxisLabel('Visitors')} />
          
          <Tooltip
            cursor={{ stroke: '#94a3b8', strokeDasharray: '4 4' }}
            contentStyle={{
              borderRadius: 8,
              border: '1px solid #e2e8f0',
              fontSize: 12
            }}
            formatter={(value: number | number[], name: string) => {
              if (Array.isArray(value)) {
                return [
                `${value[0].toLocaleString('en-US')} – ${value[1].toLocaleString('en-US')}`,
                '80% confidence'];

              }
              return [value != null ? (value as number).toLocaleString('en-US') : '', name];
            }} />
          
          <Area
            type="monotone"
            dataKey="band"
            stroke="none"
            fill="#0f6d67"
            fillOpacity={0.14}
            name="Confidence interval"
            isAnimationActive={false} />
          
          <Line
            type="monotone"
            dataKey="historical"
            stroke="#0f6d67"
            strokeWidth={2.5}
            dot={false}
            connectNulls
            name="Historical Demand"
            isAnimationActive={false} />
          
          <Line
            type="monotone"
            dataKey="forecast"
            stroke="#17a294"
            strokeWidth={2.5}
            strokeDasharray="7 6"
            dot={false}
            connectNulls
            name="Forecast (AI Prediction)"
            isAnimationActive={false} />
          
        </ComposedChart>
      </ResponsiveContainer>
    </div>);

}
