import { SearchIcon, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type { FilterDef } from '../../hooks/useListState';

interface FilterBarProps<T> {
  search: string;
  onSearch: (value: string) => void;
  searchPlaceholder?: string;
  filters?: FilterDef<T>[];
  values: Record<string, string>;
  onFilterChange: (key: string, value: string) => void;
  onClear: () => void;
  /** How many constraints are active (search + filters) — shows "Clear all". */
  activeCount: number;
  /** Trailing controls: sort select, view switcher, page actions. */
  children?: ReactNode;
  /** Result line on the right, e.g. "12 of 40 records". */
  countLabel?: string;
}

/**
 * The toolbar every record list shares: search box, one dropdown per named
 * filter, removable chips for what is active, and a slot for view controls.
 *
 * A filter is a real `<select>` — keyboard and mobile behaviour come for free,
 * matching `SelectMenu`'s "renders a real select" convention.
 */
export function FilterBar<T>({
  search,
  onSearch,
  searchPlaceholder = 'Search…',
  filters = [],
  values,
  onFilterChange,
  onClear,
  activeCount,
  children,
  countLabel,
}: FilterBarProps<T>) {
  const chips: { label: string; onRemove: () => void }[] = [];
  if (search.trim()) chips.push({ label: `“${search.trim()}”`, onRemove: () => onSearch('') });
  for (const filter of filters) {
    const value = values[filter.key] ?? '';
    if (!value) continue;
    const option = filter.options.find((candidate) => candidate.value === value);
    chips.push({
      label: `${filter.label}: ${option?.label ?? value}`,
      onRemove: () => onFilterChange(filter.key, ''),
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-[260px]">
          <SearchIcon
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500"
            strokeWidth={2}
          />
          <input
            type="search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className="w-full rounded-lg border border-slate-300 bg-white py-1.5 pl-8 pr-2.5 text-meta text-slate-700 outline-none transition-colors duration-150 ease-out placeholder:text-slate-500 hover:border-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
          />
        </div>

        {filters.map((filter) => (
          <span key={filter.key} className="relative inline-flex items-center">
            <select
              aria-label={filter.label}
              value={values[filter.key] ?? ''}
              onChange={(event) => onFilterChange(filter.key, event.target.value)}
              className="appearance-none rounded-lg border border-slate-300 bg-white py-1.5 pl-2.5 pr-7 text-caption font-medium text-slate-600 outline-none transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25"
            >
              {filter.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-slate-500">
              <path
                d="M6 9l6 6 6-6"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        ))}

        {countLabel && (
          <span className="ml-auto text-micro text-slate-500">{countLabel}</span>
        )}
        {children}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip.label}
              className="inline-flex items-center gap-1 rounded-full border border-brand-200 bg-brand-50 px-2 py-0.5 text-fine font-medium text-brand-800">
              {chip.label}
              <button
                type="button"
                onClick={chip.onRemove}
                aria-label={`Remove filter ${chip.label}`}
                className="rounded-full p-0.5 text-brand-600 transition-colors duration-150 ease-out hover:bg-brand-100 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                <XIcon aria-hidden="true" className="h-3 w-3" strokeWidth={2.5} />
              </button>
            </span>
          ))}
          {activeCount > 1 && (
            <button
              type="button"
              onClick={onClear}
              className="rounded px-1.5 py-0.5 text-fine font-semibold text-slate-500 transition-colors duration-150 ease-out hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}
