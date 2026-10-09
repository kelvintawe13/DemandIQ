import {
  EmptyTrayArt,
  NoResultsArt,
  BrokenPlugArt,
  OfflineArt } from
'../illustrations/StateArt';

export type EmptyStateVariant = 'empty' | 'search' | 'error' | 'offline';

interface EmptyStateAction {
  label: string;
  onClick: () => void;
}

interface EmptyStateProps {
  variant?: EmptyStateVariant;
  title: string;
  description?: string;
  /** Primary call to action. Omit when there is genuinely nothing to do. */
  action?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
  /** Extra guidance, rendered as a short checklist under the copy. */
  hints?: string[];
  /** `sm` fits inside a panel or table; `md` owns a whole content region. */
  size?: 'sm' | 'md';
  className?: string;
}

const art: Record<EmptyStateVariant, (props: {className?: string;}) => JSX.Element> =
{
  empty: EmptyTrayArt,
  search: NoResultsArt,
  error: BrokenPlugArt,
  offline: OfflineArt
};

const tone: Record<EmptyStateVariant, string> = {
  empty: 'text-brand-300',
  search: 'text-slate-300',
  error: 'text-red-300',
  offline: 'text-amber-300'
};

/**
 * The single empty / no-results / failure state every surface shares, so a
 * screen with no data from the API still reads as designed rather than blank.
 */
export function EmptyState({
  variant = 'empty',
  title,
  description,
  action,
  secondaryAction,
  hints,
  size = 'md',
  className = ''
}: EmptyStateProps) {
  const Art = art[variant];
  const isCompact = size === 'sm';

  return (
    <div
      role="status"
      className={[
      'flex w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 text-center dark:border-slate-700',
      isCompact ? 'px-4 py-6' : 'px-5 py-10 sm:px-8 sm:py-14',
      className].
      join(' ')}>
      
      <Art
        className={[
        'w-auto max-w-full',
        isCompact ? 'h-[68px]' : 'h-[92px] sm:h-[112px]',
        tone[variant]].
        join(' ')} />
      

      <h3
        className={[
        'mt-4 font-semibold text-slate-900 dark:text-slate-100',
        isCompact ? 'text-control' : 'text-h5 sm:text-h4'].
        join(' ')}>
        
        {title}
      </h3>

      {description &&
      <p
        className={[
        'mt-1.5 max-w-[420px] leading-relaxed text-slate-500 dark:text-slate-400',
        isCompact ? 'text-caption' : 'text-body-xs sm:text-body-sm'].
        join(' ')}>
        
          {description}
        </p>
      }

      {hints && hints.length > 0 &&
      <ul className="mt-3 flex max-w-[420px] flex-col gap-1.5 text-left">
          {hints.map((hint) =>
        <li
          key={hint}
          className="flex items-start gap-2 text-caption leading-relaxed text-slate-500 dark:text-slate-400">
          
              <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
              {hint}
            </li>
        )}
        </ul>
      }

      {(action || secondaryAction) &&
      <div className="mt-5 flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:justify-center">
          {action &&
        <button
          type="button"
          onClick={action.onClick}
          className="inline-flex items-center justify-center rounded-lg bg-brand-700 px-4 py-2.5 text-body-xs font-semibold text-white transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
          
              {action.label}
            </button>
        }
          {secondaryAction &&
        <button
          type="button"
          onClick={secondaryAction.onClick}
          className="inline-flex items-center justify-center rounded-lg border border-slate-300 px-4 py-2.5 text-body-xs font-semibold text-slate-700 transition-colors duration-150 ease-out hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">
          
              {secondaryAction.label}
            </button>
        }
        </div>
      }
    </div>);

}