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

export interface TrendPointLive { label: string; current: number; previous?: number; }

interface Props {
  data?: TrendPointLive[];
  ticks?: string[];
  showPrevious?: boolean;
  height?: number;
  className?: string;
}

export function DemandTrendChart({ data, ticks, showPrevious = true, height, className }: Props) {
  if (!data || data.length === 0) {
    return <EmptyState variant="empty" size="sm" title="No trend data" description="Ingest demand first. Trend appears after the nightly materialisation." />;
  }
  const chartData = data;
  const ticksToUse = ticks;
  const isDense = chartData.length > 14;
  const isVeryDense = chartData.length > 24;
  // No-overlap strategy: preserve ends and add angled ticks on dense data.
  const xAngle = isDense ? -22 : 0;
  const xHeight = isDense ? 36 : 18;
  // The bottom axis band has to hold the angled tick labels *and* the `Date`
  // title under them.
  const xAxisHeight = xHeight + 18;
  
  // Get brand colors from CSS variables
  const brand500 = getComputedStyle(document.documentElement).getPropertyValue('--brand-500').trim() || '#64748b';
  const brand400 = getComputedStyle(document.documentElement).getPropertyValue('--brand-400').trim() || '#94a3b8';
  const brand200 = getComputedStyle(document.documentElement).getPropertyValue('--brand-200').trim() || '#e2e8f0';
  const brand300 = getComputedStyle(document.documentElement).getPropertyValue('--brand-300').trim() || '#cbd5e1';
  const brand700 = getComputedStyle(document.documentElement).getPropertyValue('--brand-700').trim() || '#334155';
  
  return (
    <div className={className ?? (height ? 'w-full' : 'h-[220px] w-full sm:h-[240px]')} style={height ? { height } : undefined}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={chartData}
          /* `left` is not 0: the rotated `Visitors` title is drawn from the axis
             band's left edge leftwards, and with a zero margin its ink box sat
             at user x ∈ [−2, 12] — 2px of glyph outside the chart's own
             `<svg>`, which has `overflow: hidden`, so the word was clipped at
             every width. 6px keeps the title fully inside. */
          margin={{ top: 8, right: 12, bottom: xAxisHeight, left: 6 }}>
          
          <CartesianGrid stroke={brand200} vertical={false} />
          <XAxis
            dataKey="label"
            ticks={ticksToUse}
            interval={isVeryDense ? 'preserveStartEnd' : isDense ? 'equidistantPreserveStart' as any : 0}
            minTickGap={isDense ? 14 : 8}
            angle={xAngle}
            textAnchor={isDense ? 'end' : 'middle'}
            height={xAxisHeight}
            tick={{ fill: brand500, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: brand300 }}
            label={bottomAxisLabel('Date')} />
          
          <YAxis
            domain={[0, 'auto']}
            tickFormatter={formatDemandTick}
            tick={{ fill: brand500, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={44}
            label={leftAxisLabel('Visitors')} />
          
          <Tooltip
            cursor={{ stroke: brand400, strokeDasharray: '4 4' }}
            contentStyle={{
              borderRadius: 10,
              border: '1px solid #e2e8f0',
              fontSize: 12,
              padding: '8px 10px',
              boxShadow: '0 8px 24px rgba(15,23,42,0.08)'
            }}
            wrapperStyle={{ outline: 'none' }}
            formatter={(value: number, name: string) => [
            value.toLocaleString('en-US'),
            name]
            } />
          
          <Area
            type="monotone"
            dataKey="current"
            stroke={brand700}
            strokeWidth={2.2}
            fill={brand700}
            fillOpacity={0.1}
            name="Visitors (Current Period)"
            isAnimationActive={false} />
          
          {showPrevious && (
            <Line
              type="monotone"
              dataKey="previous"
              stroke={brand400}
              strokeWidth={2}
              strokeDasharray="6 5"
              dot={false}
              name="Visitors (Previous Period)"
              isAnimationActive={false} />
          )}
          
        </ComposedChart>
      </ResponsiveContainer>
    </div>);

}
