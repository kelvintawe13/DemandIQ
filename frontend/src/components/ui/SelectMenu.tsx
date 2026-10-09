import { ChevronDownIcon } from 'lucide-react';

interface SelectMenuProps {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  size?: 'sm' | 'md';
}

/**
 * Compact inline dropdown used for chart-level controls (granularity, metric).
 * Renders a real <select> so keyboard and mobile behaviour come for free.
 */
export function SelectMenu({
  label,
  value,
  options,
  onChange,
  size = 'sm'
}: SelectMenuProps) {
  return (
    <span className="relative inline-flex items-center">
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={[
        'appearance-none rounded-lg border border-slate-300 bg-white pr-7 font-medium text-slate-600 outline-none transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200',
        size === 'sm' ? 'py-1 pl-2.5 text-micro' : 'py-1.5 pl-3 text-meta'].
        join(' ')}>
        
        {options.map((option) =>
        <option key={option} value={option}>
            {option}
          </option>
        )}
      </select>
      <ChevronDownIcon
        className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-slate-500"
        strokeWidth={2} />
      
    </span>);

}