import type { ReactNode } from 'react';
import { ChevronDownIcon, type LucideIcon } from 'lucide-react';

export interface TabItem<T extends string = string> {
  id: T;
  /** A plain string, or a node when the tab carries a count badge. */
  label: ReactNode;
  /** Optional leading glyph. A phone's `<select>` shows the label alone. */
  icon?: LucideIcon;
}

function labelText(label: ReactNode, fallback: string): string {
  if (typeof label === 'string') return label;
  if (typeof label === 'number') return String(label);
  return fallback;
}

interface TabBarProps<T extends string = string> {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  /** Describes the tab set for screen readers, e.g. "Site administration". */
  ariaLabel: string;
  className?: string;
}

// A phone tab is a thumb target first and a label second: `min-h` keeps the
// row comfortably tappable below `sm`, while desktop keeps its compact density.
const tabClass =
  'inline-flex shrink-0 items-center gap-1.5 rounded-md px-3.5 py-1.5 text-meta min-h-[40px] sm:min-h-0 transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1';

/**
 * The one segmented control every screen shares. Before this, each page rolled
 * its own bar — different active colours, some with ARIA and some without, and
 * most unable to scroll on a phone. Tabs scroll horizontally, expose
 * `role="tab"` semantics, and keep a visible focus ring for keyboard users.
 */
export function TabBar<T extends string = string>({
  tabs,
  value,
  onChange,
  ariaLabel,
  className = '',
}: TabBarProps<T>) {
  return (
    <>
      {/* Mobile: native dropdown instead of a horizontal slider. */}
      <label className={`relative flex items-center sm:hidden ${className}`}>
        <span className="sr-only">{ariaLabel}</span>
        <select
          aria-label={ariaLabel}
          value={value}
          onChange={(e) => onChange(e.target.value as T)}
          className="w-full appearance-none rounded-lg border border-slate-300 bg-white py-2.5 pl-3 pr-9 text-body-xs font-medium text-slate-800 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
        >
          {tabs.map((tab) => (
            <option key={tab.id} value={tab.id}>
              {labelText(tab.label, tab.id)}
            </option>
          ))}
        </select>
        <ChevronDownIcon
          className="pointer-events-none absolute right-3 h-4 w-4 text-slate-500"
          strokeWidth={2}
          aria-hidden="true"
        />
      </label>

      {/* Tablet/desktop: segmented tab bar. */}
      <div
        role="tablist"
        aria-label={ariaLabel}
        className={`hidden gap-1 overflow-x-auto rounded-lg border border-slate-200 bg-white p-1 touch-scroll sm:flex ${className}`}
      >
        {tabs.map((tab) => {
          const active = tab.id === value;
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(tab.id)}
              className={[
                tabClass,
                active ? 'bg-brand-600 font-semibold text-white' : 'font-medium text-slate-600 hover:bg-slate-100',
              ].join(' ')}
            >
              {Icon && <Icon className="h-4 w-4 shrink-0" strokeWidth={1.9} />}
              {tab.label}
            </button>
          );
        })}
      </div>
    </>
  );
}
