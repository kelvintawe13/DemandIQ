import { useCallback, useMemo, useState } from 'react';

/** How a record list is rendered: dense table, card grid, or compact list. */
export type ViewMode = 'table' | 'cards' | 'list';

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

/**
 * One toolbar filter. `match` decides whether a row passes for the chosen
 * value; the empty value ('' = "All") always passes and is never consulted.
 */
export interface FilterDef<T> {
  key: string;
  label: string;
  /** Choices in the dropdown; callers include their own "All …" entry. */
  options: { value: string; label: string }[];
  match: (row: T, value: string) => boolean;
}

export interface UseListStateOptions<T> {
  /** Persistence key — the view choice is remembered per screen. */
  key: string;
  items: T[];
  filters?: FilterDef<T>[];
  /** Lowercased haystack for the search box. */
  searchFields?: (row: T) => string;
  /** Column accessor used by sorting; defaults to `(row as any)[sortKey]`. */
  sortValue?: (row: T, sortKey: string) => string | number;
  defaultSort?: SortState;
  defaultView?: ViewMode;
  pageSize?: number;
}

const VIEW_STORAGE_PREFIX = 'demandiq_list_view:';

function readStoredView(key: string): ViewMode | null {
  try {
    const raw = localStorage.getItem(VIEW_STORAGE_PREFIX + key);
    return raw === 'table' || raw === 'cards' || raw === 'list' ? raw : null;
  } catch {
    return null;
  }
}

function writeStoredView(key: string, view: ViewMode): void {
  try {
    localStorage.setItem(VIEW_STORAGE_PREFIX + key, view);
  } catch {
    /* private mode / storage full — the choice just won't persist */
  }
}

/**
 * All the state one record list needs, in one place.
 *
 * Search, named filters, sorting and paging are derived from the same `rows`
 * pipeline (filter → sort → slice), so the table, card and list views can never
 * disagree about what is on screen. The view mode persists per screen key, so a
 * clerk who prefers cards keeps getting cards.
 *
 * Lives in `hooks/` rather than a component file because it exports plain
 * functions and types alongside no component — see `lib/turnstile.ts` for why
 * the split matters (Fast Refresh).
 */
export function useListState<T>({
  key,
  items,
  filters = [],
  searchFields,
  sortValue,
  defaultSort,
  defaultView = 'table',
  pageSize = 12,
}: UseListStateOptions<T>) {
  const [view, setViewRaw] = useState<ViewMode>(() => readStoredView(key) ?? defaultView);
  const [search, setSearchRaw] = useState('');
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<SortState | undefined>(defaultSort);
  const [page, setPage] = useState(1);
  const [pageSizeState, setPageSizeState] = useState(pageSize);

  const setView = useCallback(
    (next: ViewMode) => {
      setViewRaw(next);
      writeStoredView(key, next);
    },
    [key],
  );

  // Any change to the query resets paging: page 4 of the old query is not a
  // meaningful place to land on the new one.
  const setSearch = useCallback((value: string) => {
    setSearchRaw(value);
    setPage(1);
  }, []);

  const setFilter = useCallback((filterKey: string, value: string) => {
    setFilterValues((current) => ({ ...current, [filterKey]: value }));
    setPage(1);
  }, []);

  const clearFilters = useCallback(() => {
    setFilterValues({});
    setSearchRaw('');
    setPage(1);
  }, []);

  const setSortKey = useCallback((sortKey: string) => {
    setSort((current) =>
      current?.key === sortKey
        ? { key: sortKey, dir: current.dir === 'asc' ? 'desc' : 'asc' }
        : { key: sortKey, dir: 'asc' },
    );
    setPage(1);
  }, []);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    let next = items.filter((row) => {
      if (needle && searchFields) {
        if (!searchFields(row).toLowerCase().includes(needle)) return false;
      }
      for (const filter of filters) {
        const value = filterValues[filter.key] ?? '';
        if (value && !filter.match(row, value)) return false;
      }
      return true;
    });
    if (sort) {
      const accessor = sortValue ?? ((row: T, sortKey: string) => (row as Record<string, unknown>)[sortKey]);
      const sign = sort.dir === 'asc' ? 1 : -1;
      next = [...next].sort((a, b) => {
        const av = accessor(a, sort.key);
        const bv = accessor(b, sort.key);
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sign;
        return String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true }) * sign;
      });
    }
    return next;
  }, [items, search, searchFields, filters, filterValues, sort, sortValue]);

  const total = items.length;
  const filtered = rows.length;
  const pageCount = Math.max(1, Math.ceil(filtered / pageSizeState));
  const safePage = Math.min(page, pageCount);
  const from = filtered === 0 ? 0 : (safePage - 1) * pageSizeState + 1;
  const to = Math.min(safePage * pageSizeState, filtered);
  const pageRows = useMemo(
    () => rows.slice((safePage - 1) * pageSizeState, safePage * pageSizeState),
    [rows, safePage, pageSizeState],
  );

  const activeFilterCount =
    (search.trim() ? 1 : 0) +
    filters.reduce((count, filter) => count + (filterValues[filter.key] ? 1 : 0), 0);

  return {
    view,
    setView,
    search,
    setSearch,
    filterValues,
    setFilter,
    clearFilters,
    activeFilterCount,
    sort,
    setSortKey,
    page: safePage,
    setPage,
    pageSize: pageSizeState,
    setPageSize: setPageSizeState,
    rows,
    pageRows,
    total,
    filtered,
    pageCount,
    from,
    to,
  };
}

export type ListState<T> = ReturnType<typeof useListState<T>>;
