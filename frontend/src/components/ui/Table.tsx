import { Children } from 'react';
import type { ReactNode } from 'react';
import { ArrowDownIcon, ArrowUpIcon, ArrowUpDownIcon } from 'lucide-react';

interface TableShellProps {
  /** Column labels, or full cells when a header needs controls. */
  columns: ReactNode[];
  children: ReactNode;
  /** Minimum table width before it scrolls, e.g. `min-w-[720px]`. */
  minWidth?: string;
  /**
   * Rendered in place of the body when `children` holds no rows. It goes
   * *inside* the `<tbody>`, so a `<tr>` is the right markup here — the opposite
   * of `ResponsiveTable`, whose `empty` replaces the whole table.
   */
  empty?: ReactNode;
  /**
   * The table's own name, for assistive tech. A heading above the table names
   * the *panel*, not the table, so a caption is the only thing that can be a
   * table's accessible name. Without one, the table is announced unnamed.
   */
  caption?: string;
  /**
   * Draw the caption above the header row as well as exposing it.
   *
   * Off by default, and deliberately: every table in this app sits under a
   * visible panel heading, so a visible caption would print the same words
   * twice. Turn it on for a table that has no heading of its own.
   */
  captionVisible?: boolean;
  /**
   * Caps the list and scrolls it internally, with the header pinned to the top
   * of the box. Pass a Tailwind max-height class, e.g. `max-h-[520px]`.
   *
   * Use it for a directory whose length is the data's rather than the page's —
   * every account, every tenant — so the panel keeps its size and the column
   * names stay in view while the rows scroll under them. Below `md` the rows are
   * taller, so the same box shows fewer of them; intentional.
   */
  maxHeight?: string;
  className?: string;
}

/**
 * Tabular data, presented the same way everywhere: a scroll container so a
 * wide table never breaks the layout on a phone, one header style, and one
 * place to hang the empty state.
 */
export function TableShell({
  columns,
  children,
  minWidth = 'min-w-[560px]',
  empty,
  caption,
  captionVisible = false,
  maxHeight,
  className = '',
}: TableShellProps) {
  /*
   * A pinned header needs its own opaque fill — rows would otherwise read
   * through it — and its own bottom border, because the row's border scrolls
   * away with the row. Both are remapped by the `.dark` layer like every other
   * surface. Skipped when the list is not capped, where the `<tr>` does the job.
   * Pinning the cell rather than the `<tr>`: a sticky `tr` is unreliable across
   * browsers, which is why `ResponsiveTable` does the same.
   */
  const headClass = maxHeight ? 'sticky top-0 z-10 border-b border-slate-200 bg-white' : '';

  return (
    /* `relative` is a containing block, not decoration: an `sr-only` caption is
       `position: absolute`, and with a static box it would resolve against the
       initial containing block, escape the shell's `overflow: hidden`, and
       stretch the whole document (see components/ui/README.md). */
    <div
      className={`relative overflow-x-auto ${
        maxHeight ? `${maxHeight} touch-scroll overflow-y-auto overscroll-contain` : ''
      } ${className}`}
    >
      <table className={`w-full text-left ${minWidth}`}>
        {caption && (
          <caption className={captionVisible ? VISIBLE_CAPTION_CLASS : 'sr-only'}>{caption}</caption>
        )}
        <thead>
          <tr className="border-b border-slate-200">
            {columns.map((head, index) => (
              <th key={index} scope="col" className={`py-2 pr-3 text-micro font-semibold text-slate-600 ${headClass}`}>
                {head}
              </th>
            ))}
          </tr>
        </thead>
        {/* `children` is usually a `map`, so an empty list still counts as
            "provided"; pick the empty state by the row count instead. */}
        <tbody>{Children.count(children) > 0 ? children : empty}</tbody>
      </table>
    </div>
  );
}

/** A body row with the shared divider. */
export function TRow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <tr className={`border-b border-slate-100 last:border-0 ${className}`}>{children}</tr>;
}

/**
 * A body cell with the shared padding; the last cell should pass `pr-0`.
 *
 * `scope="row"` makes it the row's header instead — the cell that names the
 * row, the vertical axis of a table. Use it for the first column when that
 * column is the record's name (a user, a workspace), the same role
 * `ResponsiveTable` gives its `primary` column. The extras on the `th` branch
 * (`text-left font-normal`) only cancel the browser's centred, bold default for
 * `th`, so the cell looks exactly as it did as a `td`.
 */
export function TD({
  children,
  className = '',
  scope,
}: {
  children?: ReactNode;
  className?: string;
  scope?: 'row';
}) {
  const cellClass = `py-2.5 pr-3 sm:py-2 align-middle text-meta text-slate-700 ${className}`;
  if (scope === 'row') {
    return (
      <th scope="row" className={`text-left font-normal ${cellClass}`}>
        {children}
      </th>
    );
  }
  return <td className={cellClass}>{children}</td>;
}

export interface Column<T> {
  key: string;
  /** Shown as the column head on desktop, and as the field label in a card. */
  label: ReactNode;
  render: (row: T, index: number) => ReactNode;
  /** The card's heading on small screens; exactly one column should set this. */
  primary?: boolean;
  /** Left out of the stacked card; use for columns a phone never needs. */
  cardHidden?: boolean;
  /** Header becomes a sort toggle when the table gets `onSort`. */
  sortable?: boolean;
  headerClassName?: string;
  cellClassName?: string;
}

/** A visible caption reads as a small label above the header row. */
const VISIBLE_CAPTION_CLASS = 'pb-2 text-left text-micro font-semibold text-slate-700 dark:text-slate-200';

interface ResponsiveTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  /** Minimum width before the desktop table scrolls, e.g. `min-w-[720px]`. */
  minWidth?: string;
  /**
   * Replaces the whole table when there are no rows, so it must be a block — an
   * `EmptyState`, a `<p>` — and never table markup: a `<tr>` here would render
   * inside a `<div>` (invalid nesting, and a warning in the console). Contrast
   * `TableShell.empty`, which sits inside the `<tbody>` and *does* take a row.
   */
  empty?: ReactNode;
  /** Distinguishes this table for assistive tech. */
  caption?: string;
  /** Draw the caption above the header row too. Off by default — the panel
   *  heading already names the table on screen. */
  captionVisible?: boolean;
  /** Makes a whole row a selection target (e.g. picking a model to inspect). */
  onRowClick?: (row: T, index: number) => void;
  /**
   * Accessible name for a clickable row. A row made clickable is also made
   * focusable and keyboard-activatable, so a keyboard user can select without
   * a pointer; provide this so they hear what they are selecting.
   */
  rowAriaLabel?: (row: T, index: number) => string;
  /** Per-row classes, for highlighting the selected row. */
  rowClassName?: (row: T, index: number) => string;
  /** Current sort, so a sortable header can show its direction. */
  sort?: { key: string; dir: 'asc' | 'desc' };
  /** Clicking a sortable header asks for this key (the owner toggles the dir). */
  onSort?: (key: string) => void;
  /**
   * Caps the list and scrolls it internally, with the header pinned to the top
   * of the box. Pass a Tailwind max-height class, e.g. `max-h-[420px]`.
   *
   * Use it for a list whose length is decided by the data rather than by the
   * page — an upload history, a daily breakdown — so the panel keeps its size
   * whether there are three rows or three hundred. `overscroll-contain` keeps
   * the inner scroll from chaining to the page when it hits the end.
   *
   * Body rows are 45–60px each, so the number to reach for is roughly
   * `rows × 60 + 40` for the header. Below `md` the rows are cards (~150px), so
   * the same box shows fewer of them — intentional, it stays a bounded panel.
   */
  maxHeight?: string;
  className?: string;
}

/**
 * A table that stops being a table on a phone.
 *
 * A wide table inside a horizontal scroller technically "fits", but on a phone
 * it reads as a cropped table: you see three of six columns and have to drag
 * sideways to learn anything. Below `md` the *same* table is restyled into one
 * card per row, labelled from each column's `label`, so the data fits a 320px
 * screen without scrolling.
 *
 * It is deliberately one markup tree rather than a cards copy plus a table
 * copy: duplicating the rows would double every form control in the DOM (two
 * selects, two revoke buttons per member) and every query in the app and the
 * tests would have to know which copy it meant. The switch is CSS only
 * (`.responsive-cards` in index.css), so there is exactly one of everything.
 */
export function ResponsiveTable<T>({
  columns,
  rows,
  rowKey,
  minWidth = 'min-w-[560px]',
  empty,
  caption,
  captionVisible = false,
  onRowClick,
  rowAriaLabel,
  rowClassName,
  sort,
  onSort,
  maxHeight,
  className = '',
}: ResponsiveTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className={className}>
        {empty ?? <p className="py-3 text-center text-caption text-slate-500">Nothing to show.</p>}
      </div>
    );
  }

  const primaryKey = columns.find((column) => column.primary)?.key;

  /**
   * A sticky header needs its own opaque fill — rows would otherwise read
   * through it — and its own bottom border, because the row's border scrolls
   * away with the row. Both are remapped by the `.dark` layer like every other
   * surface. Skipped when the list is not capped, where the `<tr>` does the job.
   */
  const headClass = maxHeight
    ? 'sticky top-0 z-10 border-b border-slate-200 bg-white'
    : '';

  return (
    <div
      className={`relative overflow-x-auto touch-scroll scroll-fade-x ${
        maxHeight ? `${maxHeight} overflow-y-auto overscroll-contain` : ''
      } ${className}`}
    >
      <table className={`responsive-cards w-full text-left ${minWidth}`}>
        {caption && (
          <caption className={captionVisible ? VISIBLE_CAPTION_CLASS : 'sr-only'}>{caption}</caption>
        )}
        <thead>
          <tr className="border-b border-slate-200">
            {columns.map((column) => {
              const sortable = Boolean(column.sortable && onSort);
              const active = sortable && sort?.key === column.key;
              const head = column.label || <span className="sr-only">{column.key}</span>;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={
                    active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined
                  }
                  className={`py-2 pr-3 text-micro font-semibold text-slate-600 ${headClass} ${column.headerClassName ?? ''}`}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => onSort!(column.key)}
                      className="inline-flex items-center gap-1 transition-colors duration-150 ease-out hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      {head}
                      {active ? (
                        sort!.dir === 'asc' ? (
                          <ArrowUpIcon aria-hidden="true" className="h-3 w-3" strokeWidth={2} />
                        ) : (
                          <ArrowDownIcon aria-hidden="true" className="h-3 w-3" strokeWidth={2} />
                        )
                      ) : (
                        <ArrowUpDownIcon aria-hidden="true" className="h-3 w-3 opacity-50" strokeWidth={2} />
                      )}
                    </button>
                  ) : (
                    /* An unlabelled column is normal here (an actions column holds
                       only buttons), but an empty `th` leaves the column nameless
                       for a screen reader (axe's `empty-table-header`). The column
                       key names it for assistive tech without drawing a header. */
                    head
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={rowKey(row, index)}
              onClick={onRowClick ? () => onRowClick(row, index) : undefined}
              /* A clickable row is also keyboard-operable: focusable, and Enter
                 or Space selects. Without this, mouse users could pick a model
                 but keyboard users could not. */
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={onRowClick ? (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onRowClick(row, index);
                }
              } : undefined}
              aria-label={onRowClick && rowAriaLabel ? rowAriaLabel(row, index) : undefined}
              className={[
                'border-b border-slate-100 last:border-0',
                onRowClick ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500' : '',
                rowClassName?.(row, index) ?? '',
              ].join(' ')}
            >
              {columns.map((column) => {
                const isRowHeader = column.key === primaryKey;
                /* `data-label` is the card's field name below `md`; the primary
                   cell skips it and becomes the card's heading instead. */
                const dataLabel = column.cardHidden ? undefined : (typeof column.label === 'string' ? column.label : '');
                // `text-left font-normal` matter only for the row-header cell: the
                // browser centres and bolds a `th` by default, which would make the
                // primary column look different from before.
                const cellClass = `py-2.5 pr-3 sm:py-2 text-left align-middle text-meta font-normal text-slate-700 ${column.cellClassName ?? ''}`;
                /* The primary column names its row, so it is a `th[scope="row"]`.
                   A table with only column headers gives a screen reader a row of
                   cells with nothing to attach them to, and the first column —
                   the row's axis, read down the page — carried no more weight
                   than a value. */
                return isRowHeader ? (
                  <th
                    key={column.key}
                    scope="row"
                    data-label={dataLabel}
                    data-primary=""
                    data-card-hidden={column.cardHidden ? '' : undefined}
                    className={cellClass}
                  >
                    {column.render(row, index)}
                  </th>
                ) : (
                  <td
                    key={column.key}
                    data-label={dataLabel}
                    data-card-hidden={column.cardHidden ? '' : undefined}
                    className={cellClass}
                  >
                    {column.render(row, index)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
