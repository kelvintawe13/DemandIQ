import React from "react";
import { motion, useReducedMotion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { EASE_OUT_EXPO } from "../../lib/motion";
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
  const reduced = useReducedMotion();
  return <motion.section
      initial={reduced ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: EASE_OUT_EXPO }}
      className={`flex min-w-0 flex-col rounded-xl border border-slate-200/90 bg-white p-3 shadow-[0_1px_0_rgba(15,23,42,0.03)] sm:p-3.5 ${className}`}>
      {(title || action) && <div className="mb-2.5 flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5 border-b border-slate-100 pb-2.5">
          <div className="min-w-0">
            {title && <h2 className="font-display flex items-center gap-2 text-h4 tracking-tight text-slate-900">
                {Icon && <Icon className="h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={2} />}
                {title}
              </h2>}
            {description && <p className="mt-1 text-meta leading-relaxed text-slate-500">{description}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>}
      {children}
    </motion.section>;
}