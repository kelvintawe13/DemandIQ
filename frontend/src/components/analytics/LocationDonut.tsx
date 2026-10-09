import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts';
import { EmptyState } from '../ui/EmptyState';
// fallbackBreakdown retained for Storybook reference only — runtime fallback removed

export interface DonutSlice { name: string; value: number; color: string; }

interface Props { data?: DonutSlice[]; }

export function LocationDonut({ data }: Props) {
  if (!data || data.length === 0) {
    return <EmptyState variant="empty" size="sm" title="No data yet" description="Ingest demand first" />;
  }
  const d = data;
  const centerNumber = (() => {
    const sum = d.reduce((a,b)=>a+b.value,0);
    return sum > 200 ? sum.toLocaleString() : `${sum.toFixed(1)}%`;
  })();
  return (
    <div className="relative h-[190px] w-[190px] shrink-0">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={d}
            dataKey="value"
            nameKey="name"
            innerRadius={58}
            outerRadius={92}
            paddingAngle={1}
            startAngle={90}
            endAngle={-270}
            stroke="none"
            isAnimationActive={false}>
            
            {d.map((slice) =>
            <Cell key={slice.name} fill={slice.color} />
            )}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-h3 font-bold leading-none text-slate-900">{centerNumber}</span>
        <span className="mt-1 text-meta text-slate-500">{data && data.length ? 'Share' : 'Total'}</span>
      </div>
    </div>);

}
