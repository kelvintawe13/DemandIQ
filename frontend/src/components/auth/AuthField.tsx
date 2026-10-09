import React from "react";
import type { LucideIcon } from "lucide-react";
interface AuthFieldProps {
  id: string;
  label: string;
  type?: string;
  value: string;
  placeholder?: string;
  icon: LucideIcon;
  autoComplete?: string;
  error?: string;
  hint?: string;
  onChange: (value: string) => void;
  trailing?: React.ReactNode;
}
/**
 * Label + control + message, matching DESIGN.md § 9 (the canonical `ui/Field`
 * spec). The chrome mirrors `ui/Field.tsx`'s `inputClass`: `rounded-xl`, a
 * `ring-2` at 20% on focus, a 13px label and a 12px message that is
 * `role="alert"` when it is an error. `error` and `hint` are mutually
 * exclusive, as in `Field`. Auth screens are light-only, so no `dark:`
 * variants are needed here.
 */
export function AuthField({
  id,
  label,
  type = 'text',
  value,
  placeholder,
  icon: Icon,
  autoComplete,
  error,
  hint,
  onChange,
  trailing
}: AuthFieldProps) {
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return <div className="min-w-0">
      <label htmlFor={id} className="block text-body-xs font-semibold text-slate-800">
        {label}
      </label>
      <div className={['mt-1.5 flex items-center rounded-xl border bg-white transition-[border-color,box-shadow,background-color] duration-150 ease-out', error ? 'border-red-400 focus-within:border-red-500 focus-within:ring-2 focus-within:ring-red-500/20' : 'border-slate-300 hover:border-slate-400 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20'].join(' ')}>
        <Icon className="ml-3.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} aria-hidden="true" />
        <input id={id} type={type} value={value} placeholder={placeholder} autoComplete={autoComplete} aria-invalid={Boolean(error)} aria-describedby={describedBy} onChange={(event) => onChange(event.target.value)} className="w-full min-w-0 bg-transparent px-3 py-2.5 text-control text-slate-900 placeholder:text-slate-500 focus:outline-none" />
        {trailing}
      </div>
      {error ?
      <p id={`${id}-error`} role="alert" className="mt-1.5 text-caption text-red-600">
          {error}
        </p> :
      hint ?
      <p id={`${id}-hint`} className="mt-1.5 text-caption text-slate-500">
          {hint}
        </p> :
      null}
    </div>;
}
