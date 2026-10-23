import type { ReactNode } from 'react';
import { ResponsiveTable, type Column } from './Table';
import { FilterBar } from './FilterBar';
import { ViewSwitcher } from './ViewSwitcher';
import { Pagination } from './Pagination';
import {
  useListState,
  type FilterDef,
  type ListState,
  type SortState,
  type ViewMode,
} from '../../hooks/useListState';

interface RecordListProps<T> {
  /** Persistence key for the view choice (one per screen). */
  stateKey: string;
  items: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string;
  filters?: FilterDef<T>[];
  searchFields?: (row: T) => string;
  searchPlaceholder?: string;
  sortValue?: (row: T, sortKey: string) => string | number;
  defaultSort?: SortState;
  defaultView?: ViewMode;
  pageSize?: number;
  minWidth?: string;
  empty?: ReactNode;
  caption?: string;
  onRowClick?: (row: T, index: number) => void;
  rowAriaLabel?: (row: T, index: number) => string;
  rowClassName?: (row: T, index: number) => string;
  /** Buttons at the end of the toolbar — "New connector", an export, etc. */
  toolbarEnd?: ReactNode;
  /** Rendered under pagination with the live list state — counts, load-more. */
  footer?: (state: ListState<T>) => ReactNode;
  countNoun?: string;
}

/** Sort control for the card and list views (the table sorts by its headers). */
export function SortMenu<T>({
  columns,
  sort,
  onSort,
}: {
  /** Just the sort surface of a column — pages without a table can pass stubs. */
  columns: Pick<Column<T>, 'key' | 'label' | 'sortable'>[];
  sort?: SortState;
  onSort: (key: string) => void;
}) {
  const sortable = columns.filter((column) => column.sortable !== false);
  if (sortable.length === 0) return null;
  return (
    <label className="flex items-center gap-1.5 text-micro text-slate-500">
      Sort
      <select
        aria-label="Sort by"
        value={sort ? `${sort.key}:${sort.dir}` : ''}
        onChange={(event) => {
          const [key, dir] = event.target.value.split(':');
          if (!key) return;
          onSort(key);
          // `onSort` toggles direction when the key repeats — correct the
          // direction here when the user picked one explicitly.
          if (sort?.key === key && sort.dir !== dir) onSort(key);
        }}
        className="appearance-none rounded-lg border border-slate-300 bg-white py-1 pl-2 pr-6 text-micro font-medium text-slate-600 outline-none transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25">
        {sortable.flatMap((column) => [
          <option key={`${column.key}:asc`} value={`${column.key}:asc`}>
            {typeof column.label === 'string' ? column.label : column.key} (A→Z)
          </option>,
          <option key={`${column.key}:desc`} value={`${column.key}:desc`}>
            {typeof column.label === 'string' ? column.label : column.key} (Z→A)
          </option>,
        ])}
      </select>
    </label>
  );
}

/** The card grid: one card per record, columns as labelled value rows. */
function CardGrid<T>({
  columns,
  rows,
  rowKey,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
}) {
  const primary = columns.find((column) => column.primary) ?? columns[0];
  const rest = columns.filter((column) => column !== primary && !column.cardHidden);
  return (
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map((row, index) => (
        <li
          key={rowKey(row, index)}
          className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-3.5">
          <div className="min-w-0 text-body-xs font-semibold text-slate-900">
            {primary.render(row, index)}
          </div>
          <dl className="flex flex-col gap-1.5">
            {rest.map((column) => (
              <div key={column.key} className="flex items-baseline justify-between gap-3">
                <dt className="shrink-0 text-fine font-semibold uppercase tracking-wide text-slate-500">
                  {column.label}
                </dt>
                <dd className="min-w-0 text-right text-meta text-slate-700">
                  {column.render(row, index)}
                </dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  );
}

/**
 * The compact list: one line per record — name, a few inline facts, and the
 * trailing (actions) cell pushed right. Fast to scan when the card grid is too
 * airy and the table too wide.
 */
function CompactList<T>({
  columns,
  rows,
  rowKey,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
}) {
  const primary = columns.find((column) => column.primary) ?? columns[0];
  const rest = columns.filter((column) => column !== primary && !column.cardHidden);
  const trailing = rest[rest.length - 1];
  const middle = trailing ? rest.slice(0, -1) : rest;
  return (
    <ul className="flex flex-col divide-y divide-slate-100">
      {rows.map((row, index) => (
        <li key={rowKey(row, index)} className="flex items-center gap-3 py-2 sm:py-1.5">
          <span className="min-w-0 flex-1 truncate text-meta font-semibold text-slate-900">
            {primary.render(row, index)}
          </span>
          <span className="hidden min-w-0 items-center gap-3 text-caption text-slate-500 md:flex">
            {middle.map((column) => (
              <span key={column.key} className="max-w-[200px] truncate">
                {column.render(row, index)}
              </span>
            ))}
          </span>
          {trailing && (
            <span className="ml-auto shrink-0 text-right text-meta text-slate-700">
              {trailing.render(row, index)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * One record list, three ways to read it.
 *
 * Owns the whole toolbar (search, filters, sort, view switch) and the paging
 * underneath, so a screen only supplies its columns and filter definitions.
 * The same `useListState` pipeline feeds all three views, so the table, cards
 * and list can never show different sets of records.
 */
export function RecordList<T>({
  stateKey,
  items,
  columns,
  rowKey,
  filters,
  searchFields,
  searchPlaceholder,
  sortValue,
  defaultSort,
  defaultView = 'table',
  pageSize = 12,
  minWidth = 'min-w-[560px]',
  empty,
  caption,
  onRowClick,
  rowAriaLabel,
  rowClassName,
  toolbarEnd,
  footer,
  countNoun = 'records',
}: RecordListProps<T>) {
  const state = useListState<T>({
    key: stateKey,
    items,
    filters,
    searchFields,
    sortValue,
    defaultSort,
    defaultView,
    pageSize,
  });

  return (
    <div className="flex flex-col gap-2.5">
      <FilterBar<T>
        search={state.search}
        onSearch={state.setSearch}
        searchPlaceholder={searchPlaceholder}
        filters={filters}
        values={state.filterValues}
        onFilterChange={state.setFilter}
        onClear={state.clearFilters}
        activeCount={state.activeFilterCount}
        countLabel={state.filtered !== state.total ? `${state.filtered} of ${state.total} ${countNoun}` : undefined}>
        {state.view !== 'table' && (
          <SortMenu columns={columns} sort={state.sort} onSort={state.setSortKey} />
        )}
        <ViewSwitcher value={state.view} onChange={state.setView} />
        {toolbarEnd}
      </FilterBar>

      {state.view === 'table' && (
        <ResponsiveTable<T>
          columns={columns}
          rows={state.pageRows}
          rowKey={rowKey}
          minWidth={minWidth}
          empty={empty}
          caption={caption}
          onRowClick={onRowClick}
          rowAriaLabel={rowAriaLabel}
          rowClassName={rowClassName}
          sort={state.sort}
          onSort={state.setSortKey}
        />
      )}
      {state.view === 'cards' && <CardGrid<T> columns={columns} rows={state.pageRows} rowKey={rowKey} />}
      {state.view === 'list' && <CompactList<T> columns={columns} rows={state.pageRows} rowKey={rowKey} />}

      <Pagination
        page={state.page}
        pageCount={state.pageCount}
        onPage={state.setPage}
        total={state.filtered}
        from={state.from}
        to={state.to}
        noun={countNoun}
        pageSize={state.pageSize}
        onPageSize={state.setPageSize}
      />
      {footer?.(state)}
    </div>
  );
}
