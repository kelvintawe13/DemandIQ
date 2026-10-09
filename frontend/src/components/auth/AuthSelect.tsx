import { ChevronDownIcon, type LucideIcon } from "lucide-react";
interface Option {
  value: string;
  label: string;
}
interface AuthSelectProps {
  id: string;
  label: string;
  value: string;
  options: Option[];
  placeholder: string;
  icon: LucideIcon;
  error?: string;
  hint?: string;
  onChange: (value: string) => void;
}
/**
 * The `AuthField` chrome with a native select inside, so the register flow has
 * one control spec rather than two. See `AuthField` and DESIGN.md § 9.
 */
export function AuthSelect({
  id,
  label,
  value,
  options,
  placeholder,
  icon: Icon,
  error,
  hint,
  onChange
}: AuthSelectProps) {
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return <div className="min-w-0">
      <label htmlFor={id} className="block text-body-xs font-semibold text-slate-800">
        {label}
      </label>
      <div className={['relative mt-1.5 flex items-center rounded-xl border bg-white transition-[border-color,box-shadow,background-color] duration-150 ease-out', error ? 'border-red-400 focus-within:border-red-500 focus-within:ring-2 focus-within:ring-red-500/20' : 'border-slate-300 hover:border-slate-400 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20'].join(' ')}>
        <Icon className="ml-3.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} aria-hidden="true" />
        <select id={id} value={value} aria-invalid={Boolean(error)} aria-describedby={describedBy} onChange={(event) => onChange(event.target.value)} className={['w-full min-w-0 appearance-none bg-transparent px-3 py-2.5 pr-9 text-control focus:outline-none', value ? 'text-slate-900' : 'text-slate-500'].join(' ')}>
          <option value="">{placeholder}</option>
          {options.map((option) => <option key={option.value} value={option.value} className="text-slate-900">
              {option.label}
            </option>)}
        </select>
        <ChevronDownIcon aria-hidden="true" className="pointer-events-none absolute right-3 h-4 w-4 text-slate-500" strokeWidth={1.9} />
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
