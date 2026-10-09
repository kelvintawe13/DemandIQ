import { Panel } from './Panel';

/**
 * Loading placeholders.
 *
 * A single centred spinner tells the operator "something is happening" but not
 * what will appear, so a slow read feels like a stalled screen. A skeleton that
 * mirrors the real layout keeps the page's shape on screen and the arrival of
 * data reads as a fill-in rather than a jump.
 *
 * The whole tree carries one `role="status"` with the page's accessible name,
 * so a screen reader announces the same thing the spinner did.
 */

function SkeletonLine({ className = '' }: { className?: string }) {
  return (
    <span
      className={['block h-3 animate-pulse rounded bg-slate-200 dark:bg-slate-700', className].join(' ')}
      aria-hidden="true"
    />
  );
}

function SkeletonBlock({ className = '' }: { className?: string }) {
  return (
    <span
      className={['block animate-pulse rounded-lg bg-slate-200 dark:bg-slate-700', className].join(' ')}
      aria-hidden="true"
    />
  );
}

/** A panel-shaped placeholder: a title line, then a few content lines. */
function PanelSkeleton({ rows = 3, className = '' }: { rows?: number; className?: string }) {
  return (
    <Panel className={className}>
      <div className="flex flex-col gap-3" aria-hidden="true">
        <SkeletonLine className="h-4 w-1/3" />
        {Array.from({ length: rows }).map((_, index) => (
          <SkeletonLine key={index} className={index === rows - 1 ? 'w-2/3' : 'w-full'} />
        ))}
      </div>
    </Panel>
  );
}

interface PageSkeletonProps {
  /** Accessible name — the same string the old spinner announced. */
  label: string;
  /** Layout to mirror. Operators mostly see the wide-left / narrow-right grid. */
  variant?: 'wide-left' | 'equal';
}

/**
 * The default first-load state for an operator screen: one wide panel beside a
 * stack of narrower ones, so the grid matches what replaces it.
 */
export function PageSkeleton({ label, variant = 'wide-left' }: PageSkeletonProps) {
  return (
    <div role="status" aria-label={label} aria-busy="true" className="flex w-full flex-col gap-3">
      <div
        className={[
          'grid grid-cols-1 gap-3',
          variant === 'wide-left'
            ? 'xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]'
            : 'xl:grid-cols-2',
        ].join(' ')}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <Panel>
            <div className="flex flex-col gap-4" aria-hidden="true">
              <div className="flex items-center justify-between gap-3">
                <SkeletonLine className="h-4 w-1/4" />
                <SkeletonBlock className="h-8 w-20" />
              </div>
              <SkeletonBlock className="h-40 w-full" />
            </div>
          </Panel>
          <PanelSkeleton rows={4} />
        </div>
        <div className="flex flex-col gap-3">
          <PanelSkeleton rows={3} />
          <PanelSkeleton rows={3} />
        </div>
      </div>
    </div>
  );
}
