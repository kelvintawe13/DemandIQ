import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

export type StatTone = 'default' | 'success' | 'warning' | 'critical' | 'info';

const toneRing: Record<StatTone, string> = {
  default: 'text-brand-600',
  success: 'text-brand-600',
  warning: 'text-amber-600',
  critical: 'text-red-600',
  info: 'text-sky-600',
};

interface StatCardProps {
  label: string;
  /** Already-formatted value; `—` when the number is genuinely unknown. */
  value: ReactNode;
  sub?: string;
  icon?: LucideIcon;
  tone?: StatTone;
}

/**
 * A single KPI tile. The console, Overview and admin pages all show the same
 * "label / big number / one line of context" shape, so it lives here rather
 * than being re-typed per screen with slightly different sizes each time.
 */
export function StatCard({ label, value, sub, icon: Icon, tone = 'default' }: StatCardProps) {
  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-slate-200 bg-white p-2 sm:p-2.5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-fine font-semibold uppercase tracking-wide text-slate-500">{label}</p>
        {Icon && <Icon className={`h-4 w-4 shrink-0 ${toneRing[tone]}`} strokeWidth={1.9} aria-hidden="true" />}
      </div>
      <p className="mt-0.5 text-display-xs font-bold tabular-nums tracking-tight text-slate-900">{value}</p>
      {sub && <p className="text-micro text-slate-500">{sub}</p>}
    </div>
  );
}
