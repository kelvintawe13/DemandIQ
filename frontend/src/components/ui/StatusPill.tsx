import React from 'react';

export type StatusTone = 'success' | 'warning' | 'critical' | 'neutral' | 'info';

const toneClasses: Record<StatusTone, string> = {
  success: 'bg-brand-100 text-brand-800',
  warning: 'bg-amber-100 text-amber-800',
  critical: 'bg-red-100 text-red-700',
  neutral: 'bg-slate-100 text-slate-600',
  info: 'bg-sky-100 text-sky-800'
};

interface StatusPillProps {
  tone: StatusTone;
  children: React.ReactNode;
  dot?: boolean;
}

export function StatusPill({ tone, children, dot = false }: StatusPillProps) {
  const dotColor: Record<StatusTone, string> = {
    success: 'bg-brand-600',
    warning: 'bg-amber-500',
    critical: 'bg-red-500',
    neutral: 'bg-slate-400',
    info: 'bg-sky-500'
  };

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-micro font-semibold ${toneClasses[tone]}`}>
      
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${dotColor[tone]}`} />}
      {children}
    </span>);

}