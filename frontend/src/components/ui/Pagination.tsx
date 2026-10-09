import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react';

interface PaginationProps {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  total: number;
  from: number;
  to: number;
  /** Noun for the count line, e.g. "models". */
  noun?: string;
  pageSize?: number;
  pageSizeOptions?: number[];
  onPageSize?: (size: number) => void;
}

/**
 * "Showing 1–12 of 40 models" plus prev/next and an optional page-size picker.
 *
 * Hidden entirely for a single page of results — pagination is noise when
 * everything fits.
 */
export function Pagination({
  page,
  pageCount,
  onPage,
  total,
  from,
  to,
  noun = 'records',
  pageSize,
  pageSizeOptions = [12, 24, 48],
  onPageSize,
}: PaginationProps) {
  if (total === 0) return null;

  return (
    <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
      <p className="text-micro text-slate-500">
        Showing <span className="font-semibold text-slate-700">{from}</span>–
        <span className="font-semibold text-slate-700">{to}</span> of{' '}
        <span className="font-semibold text-slate-700">{total}</span> {noun}
      </p>

      <div className="flex items-center gap-2">
        {onPageSize && pageSize && (
          <label className="flex items-center gap-1.5 text-micro text-slate-500">
            Per page
            <select
              aria-label="Rows per page"
              value={pageSize}
              onChange={(event) => onPageSize(Number(event.target.value))}
              className="appearance-none rounded-lg border border-slate-300 bg-white py-1 pl-2 pr-6 text-micro font-medium text-slate-600 outline-none transition-colors duration-150 ease-out hover:border-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25">
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        )}

        {pageCount > 1 && (
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => onPage(page - 1)}
              disabled={page <= 1}
              aria-label="Previous page"
              className="rounded-lg border border-slate-300 p-1.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-40">
              <ChevronLeftIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
            <span className="text-micro text-slate-600">
              Page <span className="font-semibold">{page}</span> of {pageCount}
            </span>
            <button
              type="button"
              onClick={() => onPage(page + 1)}
              disabled={page >= pageCount}
              aria-label="Next page"
              className="rounded-lg border border-slate-300 p-1.5 text-slate-600 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-40">
              <ChevronRightIcon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
