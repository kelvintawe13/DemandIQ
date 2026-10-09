import React from "react";
import type { LucideIcon } from "lucide-react";
interface PanelProps {
  title?: string;
  description?: string;
  icon?: LucideIcon;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}
export function Panel({
  title,
  description,
  icon: Icon,
  action,
  children,
  className = ''
}: PanelProps) {
  return <section className={`flex min-w-0 flex-col rounded-2xl border border-slate-200/90 bg-white p-4 shadow-[0_1px_0_rgba(15,23,42,0.03)] sm:p-5 ${className}`}>
      {(title || action) && <div className="mb-4 flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5 border-b border-slate-100 pb-3.5">
          <div className="min-w-0">
            {title && <h2 className="font-display flex items-center gap-2 text-h3 tracking-tight text-slate-900 sm:text-h3-lg">
                {Icon && <Icon className="h-5 w-5 shrink-0 text-brand-600" strokeWidth={2} />}
                {title}
              </h2>}
            {description && <p className="mt-1 text-meta leading-relaxed text-slate-500">{description}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>}
      {children}
    </section>;
}