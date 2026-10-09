import { TableIcon, LayoutGridIcon, ListIcon } from 'lucide-react';
import type { ViewMode } from '../../hooks/useListState';

interface ViewSwitcherProps {
  value: ViewMode;
  onChange: (view: ViewMode) => void;
}

const OPTIONS: { mode: ViewMode; label: string; Icon: typeof TableIcon }[] = [
  { mode: 'table', label: 'Table view', Icon: TableIcon },
  { mode: 'cards', label: 'Card view', Icon: LayoutGridIcon },
  { mode: 'list', label: 'List view', Icon: ListIcon },
];

/**
 * Table / cards / list — the three ways every record list can be read.
 *
 * A segmented control of toggle buttons (`aria-pressed`), so screen readers
 * hear the current mode instead of guessing from colour. The choice persists
 * via `useListState`.
 */
export function ViewSwitcher({ value, onChange }: ViewSwitcherProps) {
  return (
    <div
      role="group"
      aria-label="View mode"
      className="inline-flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
      {OPTIONS.map(({ mode, label, Icon }) => (
        <button
          key={mode}
          type="button"
          aria-label={label}
          title={label}
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
          className={[
            'rounded-[7px] p-1.5 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            value === mode
              ? 'bg-brand-50 text-brand-700'
              : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700',
          ].join(' ')}>
          <Icon aria-hidden="true" className="h-4 w-4" strokeWidth={1.9} />
        </button>
      ))}
    </div>
  );
}
