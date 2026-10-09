import type { ReactNode } from 'react';
import { LockIcon } from 'lucide-react';

/**
 * Explains why a control is read-only, instead of silently disabling it or
 * hiding it. Screens a seat can see but not act on (a data clerk on the alert
 * rules, a clerk on the workspace profile) say so in the same words, so the
 * boundary is learnable rather than mysterious.
 */
export function PermissionNote({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <p
      role="note"
      className={`flex items-start gap-2 rounded-lg bg-slate-50 px-2.5 py-2 text-micro leading-relaxed text-slate-500 ${className}`}
    >
      <LockIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.9} aria-hidden="true" />
      <span>{children}</span>
    </p>
  );
}
